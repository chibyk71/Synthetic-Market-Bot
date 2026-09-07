"""Historical research experiment harness.

Composes existing 1C–3A components into a deterministic offline experiment:

    stored ticks → replay/stream → M1/M15 candles → StrategyEngine
         → TradeConstructor → SimulationEngine → ResearchMetrics
         → StrategyValidationCalculator

No new strategy, risk, simulation, or ML logic. No live/demo execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.market.candles import (
    TIMEFRAME_M1,
    TIMEFRAME_M15,
    Candle,
    CandleBuilder,
)
from smb.research.metrics import ResearchMetricsCalculator
from smb.research.models import TradeResearchMetrics
from smb.simulation.engine import SimulationEngine
from smb.simulation.models import (
    SimulationConfig,
    SimulationOutcome,
    TradeSimulationResult,
)
from smb.strategy.engine import StrategyEngine
from smb.strategy.models import Direction, StrategyConfig, StrategySignal
from smb.trade.constructor import TradeConstructor
from smb.trade.models import (
    RejectionReason,
    RiskContext,
    TradeCandidate,
    TradeConfig,
)
from smb.validation.calculator import StrategyValidationCalculator
from smb.validation.models import StrategyValidationReport

logger = logging.getLogger(__name__)


class ExperimentError(ValueError):
    """Raised when an experiment cannot run (bad range, empty data, invalid store)."""


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Immutable parameters for one historical experiment run."""

    instrument: str
    start_epoch: int | None = None
    end_epoch: int | None = None
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    risk_equity: float = 10_000.0

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("instrument must be non-empty")
        if self.start_epoch is not None and self.end_epoch is not None:
            if self.start_epoch >= self.end_epoch:
                raise ValueError("start_epoch must be < end_epoch")
        if self.risk_equity <= 0.0:
            raise ValueError("risk_equity must be > 0")


@dataclass(frozen=True, slots=True)
class TradeExperimentRow:
    """One simulated (or risk-rejected) research row for inspection."""

    instrument: str
    signal_epoch: int
    direction: str
    accepted: bool
    rejection_reason: RejectionReason | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float | None
    risk_amount: float | None
    outcome: SimulationOutcome | None
    entry_time: int | None
    exit_time: int | None
    duration_seconds: int | None
    realized_r: float | None
    mfe: float | None
    mae: float | None
    signal: StrategySignal
    candidate: TradeCandidate | None
    simulation: TradeSimulationResult | None
    metrics: TradeResearchMetrics | None


@dataclass(frozen=True, slots=True)
class ExperimentSummary:
    """Aggregate counters for a completed experiment."""

    instrument: str
    start_epoch: int | None
    end_epoch: int | None
    ticks_processed: int
    m1_candles: int
    m15_candles: int
    signals: int
    candidates_accepted: int
    candidates_rejected: int
    outcomes: dict[str, int]
    win_rate: float | None
    average_r: float | None
    total_r: float | None
    average_duration_seconds: float | None
    average_mae: float | None
    average_mfe: float | None


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Full deterministic output of one historical experiment."""

    config: ExperimentConfig
    summary: ExperimentSummary
    rows: tuple[TradeExperimentRow, ...]
    simulations: tuple[TradeSimulationResult, ...]
    metrics: tuple[TradeResearchMetrics, ...]
    validation: StrategyValidationReport | None

    def to_dict(self) -> dict[str, Any]:
        s = self.summary
        return {
            "instrument": s.instrument,
            "start_epoch": s.start_epoch,
            "end_epoch": s.end_epoch,
            "ticks_processed": s.ticks_processed,
            "m1_candles": s.m1_candles,
            "m15_candles": s.m15_candles,
            "signals": s.signals,
            "candidates_accepted": s.candidates_accepted,
            "candidates_rejected": s.candidates_rejected,
            "outcomes": dict(s.outcomes),
            "win_rate": s.win_rate,
            "average_r": s.average_r,
            "total_r": s.total_r,
            "average_duration_seconds": s.average_duration_seconds,
            "average_mae": s.average_mae,
            "average_mfe": s.average_mfe,
            "row_count": len(self.rows),
        }


def _realized_r(sim: TradeSimulationResult) -> float | None:
    """Direction-aware realized R from a filled simulation (research helper)."""
    if not sim.filled or sim.entry_price is None or sim.exit_price is None:
        return None
    risk = sim.candidate.risk_distance
    if risk <= 0.0:
        return None
    if sim.candidate.direction == Direction.LONG:
        return (sim.exit_price - sim.entry_price) / risk
    return (sim.entry_price - sim.exit_price) / risk


def _order_finalized_pair(
    m1: Candle | None, m15: Candle | None
) -> list[tuple[str, Candle]]:
    """Emit M15 before M1 when both finalize at the same end_epoch (4B parity)."""
    items: list[tuple[str, Candle]] = []
    if m15 is not None:
        items.append(("M15", m15))
    if m1 is not None:
        items.append(("M1", m1))
    if len(items) == 2:
        items.sort(key=lambda x: (x[1].end_epoch, 0 if x[0] == "M15" else 1))
    return items


class HistoricalResearchExperiment:
    """Run one offline historical experiment against a tick repository."""

    def __init__(
        self,
        repository: TickRepository,
        *,
        config: ExperimentConfig,
    ) -> None:
        self.repository = repository
        self.config = config

    def run(self) -> ExperimentResult:
        """Execute the experiment deterministically.

        Raises
        ------
        ExperimentError
            Invalid range, missing instrument, empty ticks, or integrity failure.
        """
        self._validate_preconditions()

        ticks_processed = 0
        m1_count = 0
        m15_count = 0
        signals: list[StrategySignal] = []

        engine = StrategyEngine(self.config.instrument, self.config.strategy)
        m1_builder = CandleBuilder(TIMEFRAME_M1)
        m15_builder = CandleBuilder(TIMEFRAME_M15)

        # Pass 1: stream ticks → finalized candles → strategy (no lookahead).
        for tick in self.repository.as_tick_stream(
            self.config.instrument,
            start_epoch=self.config.start_epoch,
            end_epoch=self.config.end_epoch,
        ):
            ticks_processed += 1
            c1 = m1_builder.on_tick(tick)
            c15 = m15_builder.on_tick(tick)
            for tf, candle in _order_finalized_pair(c1, c15):
                if tf == "M15":
                    m15_count += 1
                    engine.on_m15(candle)
                else:
                    m1_count += 1
                    signals.extend(engine.on_m1(candle))

        # Do not flush partial forming candles into strategy (no premature finalize).
        if ticks_processed == 0:
            raise ExperimentError(
                f"no ticks for instrument={self.config.instrument!r} "
                f"in range [{self.config.start_epoch}, {self.config.end_epoch})"
            )

        constructor = TradeConstructor(self.config.trade)
        risk = RiskContext(equity=self.config.risk_equity)
        sim_engine = SimulationEngine(self.config.simulation)
        metrics_calc = ResearchMetricsCalculator()

        rows: list[TradeExperimentRow] = []
        simulations: list[TradeSimulationResult] = []
        metrics_list: list[TradeResearchMetrics] = []
        accepted = 0
        rejected = 0
        outcome_counts: dict[str, int] = {
            SimulationOutcome.TP.value: 0,
            SimulationOutcome.SL.value: 0,
            SimulationOutcome.TIMEOUT.value: 0,
            SimulationOutcome.NO_FILL.value: 0,
        }

        for signal in signals:
            construction = constructor.construct(signal, risk)
            if not construction.accepted or construction.trade is None:
                rejected += 1
                rows.append(
                    TradeExperimentRow(
                        instrument=signal.instrument,
                        signal_epoch=signal.signal_epoch,
                        direction=str(signal.direction),
                        accepted=False,
                        rejection_reason=construction.rejection_reason,
                        entry_price=None,
                        stop_loss=None,
                        take_profit=None,
                        risk_reward=None,
                        risk_amount=None,
                        outcome=None,
                        entry_time=None,
                        exit_time=None,
                        duration_seconds=None,
                        realized_r=None,
                        mfe=None,
                        mae=None,
                        signal=signal,
                        candidate=None,
                        simulation=None,
                        metrics=None,
                    )
                )
                continue

            accepted += 1
            candidate = construction.trade
            horizon_end = (
                candidate.signal_epoch + self.config.simulation.max_duration_seconds
            )
            # Stream only post-signal ticks up through the horizon (inclusive).
            tick_window = list(
                self.repository.as_tick_stream(
                    self.config.instrument,
                    start_epoch=candidate.signal_epoch + 1,
                    end_epoch=horizon_end + 1,
                )
            )
            sim = sim_engine.simulate(candidate, tick_window)
            simulations.append(sim)
            outcome_counts[sim.outcome.value] = (
                outcome_counts.get(sim.outcome.value, 0) + 1
            )
            m = metrics_calc.calculate(sim, tick_window)
            metrics_list.append(m)
            r_val = _realized_r(sim)
            rows.append(
                TradeExperimentRow(
                    instrument=candidate.instrument,
                    signal_epoch=candidate.signal_epoch,
                    direction=str(candidate.direction),
                    accepted=True,
                    rejection_reason=None,
                    entry_price=candidate.entry_price,
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                    risk_reward=candidate.risk_reward,
                    risk_amount=candidate.risk_amount,
                    outcome=sim.outcome,
                    entry_time=sim.entry_time,
                    exit_time=sim.exit_time,
                    duration_seconds=sim.duration_seconds,
                    realized_r=r_val,
                    mfe=m.mfe,
                    mae=m.mae,
                    signal=signal,
                    candidate=candidate,
                    simulation=sim,
                    metrics=m,
                )
            )

        validation: StrategyValidationReport | None = None
        if simulations:
            validation = StrategyValidationCalculator().validate(
                simulations, metrics_list
            )

        r_values = [r.realized_r for r in rows if r.realized_r is not None]
        total_r = sum(r_values) if r_values else None
        average_r = (total_r / len(r_values)) if r_values else None
        filled_wins = outcome_counts.get(SimulationOutcome.TP.value, 0)
        filled = (
            filled_wins
            + outcome_counts.get(SimulationOutcome.SL.value, 0)
            + outcome_counts.get(SimulationOutcome.TIMEOUT.value, 0)
        )
        win_rate = (filled_wins / filled) if filled else None
        durations = [r.duration_seconds for r in rows if r.duration_seconds is not None]
        avg_dur = (sum(durations) / len(durations)) if durations else None
        maes = [r.mae for r in rows if r.mae is not None]
        mfes = [r.mfe for r in rows if r.mfe is not None]

        summary = ExperimentSummary(
            instrument=self.config.instrument,
            start_epoch=self.config.start_epoch,
            end_epoch=self.config.end_epoch,
            ticks_processed=ticks_processed,
            m1_candles=m1_count,
            m15_candles=m15_count,
            signals=len(signals),
            candidates_accepted=accepted,
            candidates_rejected=rejected,
            outcomes=outcome_counts,
            win_rate=win_rate,
            average_r=average_r,
            total_r=total_r,
            average_duration_seconds=avg_dur,
            average_mae=(sum(maes) / len(maes)) if maes else None,
            average_mfe=(sum(mfes) / len(mfes)) if mfes else None,
        )
        return ExperimentResult(
            config=self.config,
            summary=summary,
            rows=tuple(rows),
            simulations=tuple(simulations),
            metrics=tuple(metrics_list),
            validation=validation,
        )

    def _validate_preconditions(self) -> None:
        cfg = self.config
        if cfg.start_epoch is not None and cfg.end_epoch is not None:
            if cfg.start_epoch >= cfg.end_epoch:
                raise ExperimentError("start_epoch must be < end_epoch")

        instruments = self.repository.list_instruments()
        if cfg.instrument not in instruments:
            raise ExperimentError(
                f"instrument {cfg.instrument!r} not found in dataset "
                f"(available: {instruments})"
            )

        cov = self.repository.coverage(cfg.instrument)
        if cov["tick_count"] == 0:
            raise ExperimentError(f"instrument {cfg.instrument!r} has no ticks")
        if cov.get("duplicate_count", 0) > 0:
            raise ExperimentError(
                f"instrument {cfg.instrument!r} has "
                f"{cov['duplicate_count']} duplicate tick(s); refuse to run"
            )
        if cov.get("non_monotonic_count", 0) > 0:
            raise ExperimentError(
                f"instrument {cfg.instrument!r} has "
                f"{cov['non_monotonic_count']} non-monotonic pair(s); refuse to run"
            )


def format_summary(result: ExperimentResult) -> str:
    """Human-readable experiment summary for CLI / logs."""
    s = result.summary
    lines = [
        "Historical research experiment",
        f"  instrument:           {s.instrument}",
        f"  start_epoch:          {s.start_epoch}",
        f"  end_epoch:            {s.end_epoch}",
        f"  ticks_processed:      {s.ticks_processed}",
        f"  m1_candles:           {s.m1_candles}",
        f"  m15_candles:          {s.m15_candles}",
        f"  signals:              {s.signals}",
        f"  candidates_accepted:  {s.candidates_accepted}",
        f"  candidates_rejected:  {s.candidates_rejected}",
        f"  outcomes:             {s.outcomes}",
        f"  win_rate:             {s.win_rate}",
        f"  average_r:            {s.average_r}",
        f"  total_r:              {s.total_r}",
        f"  avg_duration_seconds: {s.average_duration_seconds}",
        f"  average_mae:          {s.average_mae}",
        f"  average_mfe:          {s.average_mfe}",
        f"  row_count:            {len(result.rows)}",
    ]
    if result.validation is not None:
        lines.append("  validation:           present")
    return "\n".join(lines)


def run_experiment(
    data_root: str | Path,
    *,
    instrument: str,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
    strategy: StrategyConfig | None = None,
    trade: TradeConfig | None = None,
    simulation: SimulationConfig | None = None,
    risk_equity: float = 10_000.0,
) -> ExperimentResult:
    """Convenience entry: open store/repository and run one experiment."""
    store = ParquetTickStore(data_root)
    repo = TickRepository(store)
    cfg = ExperimentConfig(
        instrument=instrument,
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        strategy=strategy if strategy is not None else StrategyConfig(),
        trade=trade if trade is not None else TradeConfig(),
        simulation=simulation if simulation is not None else SimulationConfig(),
        risk_equity=risk_equity,
    )
    return HistoricalResearchExperiment(repo, config=cfg).run()
