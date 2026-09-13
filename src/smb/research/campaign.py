"""Historical research campaign runner (Milestone 5A).

Orchestrates the existing historical pipeline into a reproducible campaign:

    stored ticks → HistoricalResearchExperiment
         → strategy → trade construction → simulation → metrics → validation
         → campaign artifacts (manifest, summary, trades, diagnostics, report)

Observer / orchestration only. Does not change strategy, risk, simulation, or
execution semantics. Does not implement filters, optimization, or live trading.

Simulation requires tick data; the campaign therefore uses the existing tick
repository (same contract as :class:`~smb.research.experiment.HistoricalResearchExperiment`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.research.experiment import (
    ExperimentConfig,
    ExperimentError,
    ExperimentResult,
    HistoricalResearchExperiment,
    TradeExperimentRow,
)

try:
    from smb.research.diagnostic import BaselineDiagnosticCalculator as _DiagCalc
except ImportError:  # pragma: no cover — optional when 3C not merged
    _DiagCalc = None  # type: ignore[misc, assignment]
from smb.simulation.models import SimulationConfig, SimulationOutcome
from smb.strategy.models import StrategyConfig
from smb.trade.models import TradeConfig

logger = logging.getLogger(__name__)


class CampaignError(ValueError):
    """Raised for invalid campaign configuration or genuine runtime failures."""


@dataclass(frozen=True, slots=True)
class CampaignConfig:
    """Immutable parameters for one historical research campaign."""

    instrument: str
    output_dir: str | Path
    start_epoch: int | None = None
    end_epoch: int | None = None
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    risk_equity: float = 10_000.0
    campaign_id: str | None = None
    data_root: str | Path | None = None

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("instrument must be non-empty")
        if not str(self.output_dir).strip():
            raise ValueError("output_dir must be non-empty")
        if self.start_epoch is not None and self.end_epoch is not None:
            if self.start_epoch >= self.end_epoch:
                raise ValueError("start_epoch must be < end_epoch")
        if self.risk_equity <= 0.0:
            raise ValueError("risk_equity must be > 0")


@dataclass(frozen=True, slots=True)
class CampaignSummary:
    """Aggregate campaign statistics (machine-readable)."""

    campaign_id: str
    instrument: str
    start_epoch: int | None
    end_epoch: int | None
    ticks_processed: int
    m1_candles: int
    m15_candles: int
    signals: int
    candidates_accepted: int
    candidates_rejected: int
    completed_simulations: int
    outcomes: dict[str, int]
    win_count: int
    loss_count: int
    timeout_count: int
    no_fill_count: int
    win_rate: float | None
    average_r: float | None
    cumulative_r: float | None
    profit_factor: float | None
    average_mae: float | None
    average_mfe: float | None
    average_duration_seconds: float | None
    maximum_drawdown_r: float | None
    empty: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "instrument": self.instrument,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "ticks_processed": self.ticks_processed,
            "m1_candles": self.m1_candles,
            "m15_candles": self.m15_candles,
            "signals": self.signals,
            "candidates_accepted": self.candidates_accepted,
            "candidates_rejected": self.candidates_rejected,
            "completed_simulations": self.completed_simulations,
            "outcomes": dict(self.outcomes),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "timeout_count": self.timeout_count,
            "no_fill_count": self.no_fill_count,
            "win_rate": self.win_rate,
            "average_r": self.average_r,
            "cumulative_r": self.cumulative_r,
            "profit_factor": self.profit_factor,
            "average_mae": self.average_mae,
            "average_mfe": self.average_mfe,
            "average_duration_seconds": self.average_duration_seconds,
            "maximum_drawdown_r": self.maximum_drawdown_r,
            "empty": self.empty,
        }


@dataclass(frozen=True, slots=True)
class CampaignResults:
    """Full output of one campaign run (in-memory + paths to artifacts)."""

    config: CampaignConfig
    campaign_id: str
    summary: CampaignSummary
    experiment: ExperimentResult | None
    output_dir: Path
    manifest_path: Path
    summary_path: Path
    trades_path: Path | None
    diagnostics_path: Path | None
    report_path: Path
    diagnostics_status: str  # "complete" | "unavailable" | "skipped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "output_dir": str(self.output_dir),
            "summary": self.summary.to_dict(),
            "manifest_path": str(self.manifest_path),
            "summary_path": str(self.summary_path),
            "trades_path": str(self.trades_path) if self.trades_path else None,
            "diagnostics_path": (
                str(self.diagnostics_path) if self.diagnostics_path else None
            ),
            "report_path": str(self.report_path),
            "diagnostics_status": self.diagnostics_status,
        }


def _git_commit() -> str | None:
    """Best-effort current HEAD commit; None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _maximum_drawdown_r(realized_rs: list[float]) -> float | None:
    """Peak-to-trough drawdown of cumulative R (chronological order)."""
    if not realized_rs:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in realized_rs:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _profit_factor(realized_rs: list[float]) -> float | None:
    """Gross profit / gross loss; None if no losses or empty."""
    if not realized_rs:
        return None
    gains = sum(r for r in realized_rs if r > 0.0)
    losses = sum(-r for r in realized_rs if r < 0.0)
    if losses == 0.0:
        return None if gains == 0.0 else float("inf")
    return gains / losses


def _config_to_dict(cfg: CampaignConfig) -> dict[str, Any]:
    return {
        "instrument": cfg.instrument,
        "start_epoch": cfg.start_epoch,
        "end_epoch": cfg.end_epoch,
        "risk_equity": cfg.risk_equity,
        "strategy": asdict(cfg.strategy),
        "trade": asdict(cfg.trade),
        "simulation": asdict(cfg.simulation),
        "data_root": str(cfg.data_root) if cfg.data_root is not None else None,
        "output_dir": str(cfg.output_dir),
        "campaign_id": cfg.campaign_id,
    }


def _row_to_flat(row: TradeExperimentRow) -> dict[str, Any]:
    """Flatten a trade experiment row for Parquet persistence."""
    return {
        "instrument": row.instrument,
        "signal_epoch": row.signal_epoch,
        "direction": row.direction,
        "accepted": row.accepted,
        "rejection_reason": (
            row.rejection_reason.value if row.rejection_reason is not None else None
        ),
        "entry_price": row.entry_price,
        "stop_loss": row.stop_loss,
        "take_profit": row.take_profit,
        "risk_reward": row.risk_reward,
        "risk_amount": row.risk_amount,
        "outcome": row.outcome.value if row.outcome is not None else None,
        "entry_time": row.entry_time,
        "exit_time": row.exit_time,
        "duration_seconds": row.duration_seconds,
        "realized_r": row.realized_r,
        "mfe": row.mfe,
        "mae": row.mae,
        "timeframe_context": row.signal.timeframe_context,
    }


_TRADES_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("signal_epoch", pa.int64()),
        ("direction", pa.string()),
        ("accepted", pa.bool_()),
        ("rejection_reason", pa.string()),
        ("entry_price", pa.float64()),
        ("stop_loss", pa.float64()),
        ("take_profit", pa.float64()),
        ("risk_reward", pa.float64()),
        ("risk_amount", pa.float64()),
        ("outcome", pa.string()),
        ("entry_time", pa.int64()),
        ("exit_time", pa.int64()),
        ("duration_seconds", pa.int64()),
        ("realized_r", pa.float64()),
        ("mfe", pa.float64()),
        ("mae", pa.float64()),
        ("timeframe_context", pa.string()),
    ]
)


def _write_trades_parquet(path: Path, rows: tuple[TradeExperimentRow, ...]) -> None:
    records = [_row_to_flat(r) for r in rows]
    if not records:
        table = pa.Table.from_pylist([], schema=_TRADES_SCHEMA)
    else:
        table = pa.Table.from_pylist(records, schema=_TRADES_SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _empty_summary(
    campaign_id: str,
    cfg: CampaignConfig,
    *,
    ticks_processed: int = 0,
    m1: int = 0,
    m15: int = 0,
) -> CampaignSummary:
    outcomes = {
        SimulationOutcome.TP.value: 0,
        SimulationOutcome.SL.value: 0,
        SimulationOutcome.TIMEOUT.value: 0,
        SimulationOutcome.NO_FILL.value: 0,
    }
    return CampaignSummary(
        campaign_id=campaign_id,
        instrument=cfg.instrument,
        start_epoch=cfg.start_epoch,
        end_epoch=cfg.end_epoch,
        ticks_processed=ticks_processed,
        m1_candles=m1,
        m15_candles=m15,
        signals=0,
        candidates_accepted=0,
        candidates_rejected=0,
        completed_simulations=0,
        outcomes=outcomes,
        win_count=0,
        loss_count=0,
        timeout_count=0,
        no_fill_count=0,
        win_rate=None,
        average_r=None,
        cumulative_r=None,
        profit_factor=None,
        average_mae=None,
        average_mfe=None,
        average_duration_seconds=None,
        maximum_drawdown_r=None,
        empty=True,
    )


def _summary_from_experiment(
    campaign_id: str, cfg: CampaignConfig, result: ExperimentResult
) -> CampaignSummary:
    s = result.summary
    outcomes = dict(s.outcomes)
    win = outcomes.get(SimulationOutcome.TP.value, 0)
    loss = outcomes.get(SimulationOutcome.SL.value, 0)
    timeout = outcomes.get(SimulationOutcome.TIMEOUT.value, 0)
    no_fill = outcomes.get(SimulationOutcome.NO_FILL.value, 0)

    # Chronological realized R for drawdown / PF (accepted filled with R only)
    ordered = sorted(
        (r for r in result.rows if r.realized_r is not None),
        key=lambda r: (r.signal_epoch, r.direction),
    )
    rs = [float(r.realized_r) for r in ordered if r.realized_r is not None]
    # Filter non-finite
    rs = [r for r in rs if math.isfinite(r)]

    pf = _profit_factor(rs)
    # JSON cannot encode inf; store as None and note in report
    if pf is not None and not math.isfinite(pf):
        pf = None

    return CampaignSummary(
        campaign_id=campaign_id,
        instrument=s.instrument,
        start_epoch=s.start_epoch,
        end_epoch=s.end_epoch,
        ticks_processed=s.ticks_processed,
        m1_candles=s.m1_candles,
        m15_candles=s.m15_candles,
        signals=s.signals,
        candidates_accepted=s.candidates_accepted,
        candidates_rejected=s.candidates_rejected,
        completed_simulations=len(result.simulations),
        outcomes=outcomes,
        win_count=win,
        loss_count=loss,
        timeout_count=timeout,
        no_fill_count=no_fill,
        win_rate=s.win_rate,
        average_r=s.average_r,
        cumulative_r=s.total_r,
        profit_factor=pf,
        average_mae=s.average_mae,
        average_mfe=s.average_mfe,
        average_duration_seconds=s.average_duration_seconds,
        maximum_drawdown_r=_maximum_drawdown_r(rs),
        empty=s.ticks_processed == 0 or (s.signals == 0 and s.candidates_accepted == 0),
    )


def format_campaign_report(results: CampaignResults) -> str:
    """Concise human-readable Markdown summary."""
    s = results.summary
    lines = [
        f"# Campaign report: {s.campaign_id}",
        "",
        "## What was run",
        f"- **instrument:** {s.instrument}",
        f"- **start_epoch:** {s.start_epoch}",
        f"- **end_epoch:** {s.end_epoch}",
        f"- **output:** `{results.output_dir}`",
        "",
        "## Data",
        f"- ticks processed: {s.ticks_processed}",
        f"- M1 candles: {s.m1_candles}",
        f"- M15 candles: {s.m15_candles}",
        "",
        "## Signals & trades",
        f"- signals generated: {s.signals}",
        f"- candidates accepted: {s.candidates_accepted}",
        f"- candidates rejected: {s.candidates_rejected}",
        f"- completed simulations: {s.completed_simulations}",
        "",
        "## Outcomes",
        f"- wins (TP): {s.win_count}",
        f"- losses (SL): {s.loss_count}",
        f"- timeouts: {s.timeout_count}",
        f"- no-fill: {s.no_fill_count}",
        f"- win rate: {s.win_rate}",
        "",
        "## Performance",
        f"- average R: {s.average_r}",
        f"- cumulative R: {s.cumulative_r}",
        f"- profit factor: {s.profit_factor}",
        f"- maximum drawdown (R): {s.maximum_drawdown_r}",
        f"- average MAE: {s.average_mae}",
        f"- average MFE: {s.average_mfe}",
        f"- average duration (s): {s.average_duration_seconds}",
        "",
        f"- empty campaign: {s.empty}",
        f"- diagnostics: {results.diagnostics_status}",
    ]
    return "\n".join(lines) + "\n"


class CampaignRunner:
    """Reusable orchestration layer for one historical research campaign.

    Prefer::

        runner = CampaignRunner(repository, config)
        results = runner.run()

    over putting logic only in the CLI.
    """

    def __init__(
        self,
        repository: TickRepository,
        config: CampaignConfig,
    ) -> None:
        self.repository = repository
        self.config = config

    def run(self) -> CampaignResults:
        """Execute the campaign and write artifacts under ``config.output_dir``.

        Genuine data/configuration failures (missing instrument, corrupt ticks,
        invalid range, etc.) raise :class:`CampaignError`. A successful run
        with zero signals is a legitimate empty campaign.
        """
        cfg = self.config
        campaign_id = cfg.campaign_id or _default_campaign_id(cfg)
        out = Path(cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        exp_cfg = ExperimentConfig(
            instrument=cfg.instrument,
            start_epoch=cfg.start_epoch,
            end_epoch=cfg.end_epoch,
            strategy=cfg.strategy,
            trade=cfg.trade,
            simulation=cfg.simulation,
            risk_equity=cfg.risk_equity,
        )

        try:
            experiment = HistoricalResearchExperiment(
                self.repository, config=exp_cfg
            ).run()
        except ExperimentError as exc:
            # Do not convert configuration / data-integrity errors into empty
            # campaigns — that would silently poison research conclusions.
            raise CampaignError(str(exc)) from exc

        summary = _summary_from_experiment(campaign_id, cfg, experiment)

        # Diagnostics: unavailable vs complete vs hard failure
        diagnostics_path: Path | None = None
        if _DiagCalc is None:
            diagnostics_status = "unavailable"
        else:
            try:
                diag = _DiagCalc().diagnose(experiment)
                diagnostics_path = out / "diagnostics.json"
                diagnostics_path.write_text(
                    json.dumps(diag.to_dict(), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                diagnostics_status = "complete"
            except Exception as exc:  # noqa: BLE001
                # 3C is installed but failed — treat as campaign failure so
                # silent diagnostic loss cannot masquerade as success.
                raise CampaignError(
                    f"diagnostics failed (Milestone 3C): {exc}"
                ) from exc

        # Artifacts
        manifest = {
            "campaign_id": campaign_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "instrument": cfg.instrument,
            "start_epoch": cfg.start_epoch,
            "end_epoch": cfg.end_epoch,
            "configuration": _config_to_dict(cfg),
            "git_commit": _git_commit(),
            "diagnostics_status": diagnostics_status,
            "pipeline": [
                "ticks",
                "candles",
                "strategy",
                "trade_construction",
                "simulation",
                "research_metrics",
                "strategy_validation",
                "diagnostics",
            ],
        }
        manifest_path = out / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        summary_path = out / "summary.json"
        summary_path.write_text(
            json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        trades_path = out / "trades.parquet"
        _write_trades_parquet(trades_path, experiment.rows if experiment.rows else ())

        results = CampaignResults(
            config=cfg,
            campaign_id=campaign_id,
            summary=summary,
            experiment=experiment,
            output_dir=out,
            manifest_path=manifest_path,
            summary_path=summary_path,
            trades_path=trades_path,
            diagnostics_path=diagnostics_path,
            report_path=out / "report.md",
            diagnostics_status=diagnostics_status,
        )
        results.report_path.write_text(format_campaign_report(results), encoding="utf-8")
        return results


def _default_campaign_id(cfg: CampaignConfig) -> str:
    """Deterministic identity from research configuration (not run metadata).

    Same instrument + range + strategy/trade/simulation/risk config → same ID.
    ``created_at_utc`` remains run metadata and is *not* part of identity.
    """
    payload = {
        "instrument": cfg.instrument,
        "start_epoch": cfg.start_epoch,
        "end_epoch": cfg.end_epoch,
        "risk_equity": cfg.risk_equity,
        "strategy": asdict(cfg.strategy),
        "trade": asdict(cfg.trade),
        "simulation": asdict(cfg.simulation),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{cfg.instrument}_{digest}"


def run_campaign(
    data_root: str | Path,
    *,
    instrument: str,
    output_dir: str | Path,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
    strategy: StrategyConfig | None = None,
    trade: TradeConfig | None = None,
    simulation: SimulationConfig | None = None,
    risk_equity: float = 10_000.0,
    campaign_id: str | None = None,
) -> CampaignResults:
    """Convenience entry: open store/repository and run one campaign."""
    store = ParquetTickStore(data_root)
    repo = TickRepository(store)
    cfg = CampaignConfig(
        instrument=instrument,
        output_dir=output_dir,
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        strategy=strategy if strategy is not None else StrategyConfig(),
        trade=trade if trade is not None else TradeConfig(),
        simulation=simulation if simulation is not None else SimulationConfig(),
        risk_equity=risk_equity,
        campaign_id=campaign_id,
        data_root=data_root,
    )
    return CampaignRunner(repo, cfg).run()
