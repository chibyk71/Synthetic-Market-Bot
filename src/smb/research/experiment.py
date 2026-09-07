"""Historical research experiment harness.

Composes existing 1C–3A components into a deterministic offline experiment:

    stored ticks → stream → M1/M15 candles → StrategyEngine
         → TradeConstructor → SimulationEngine semantics (via LiveSimulationSession)
         → ResearchMetrics → StrategyValidationCalculator

Tick data is streamed from the repository. Simulation uses a **single**
chronological pass over post-signal ticks with bounded per-session buffers
(at most the simulation horizon). The full dataset is never materialized as a
Python list, and there is no per-signal full-history Parquet rescan.

When ``ExperimentConfig.end_epoch`` is set, simulation never consumes ticks with
``epoch >= end_epoch`` (exclusive range). SimulationEngine TIMEOUT semantics for
truncated streams are unchanged.

No new strategy, risk, simulation, or ML logic. No live/demo execution.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick
from smb.live.sim_session import LiveSimulationSession
from smb.market.candles import (
    TIMEFRAME_M1,
    TIMEFRAME_M15,
    Candle,
    CandleBuilder,
)
from smb.research.metrics import ResearchMetricsCalculator
from smb.research.models import TradeResearchMetrics
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


def _simulation_tick_end(
    signal_epoch: int,
    max_duration_seconds: int,
    experiment_end_epoch: int | None,
) -> int:
    """Exclusive end bound for ticks fed into one simulation.

    Horizon is inclusive through ``signal_epoch + max_duration`` in the engine.
    The stream uses exclusive ``end_epoch`` semantics, so we pass
    ``horizon + 1``. When the experiment sets an exclusive research
    ``end_epoch``, ticks at or after that boundary are never supplied.
    """
    horizon_inclusive = signal_epoch + max_duration_seconds
    stream_end_exclusive = horizon_inclusive + 1
    if experiment_end_epoch is not None:
        stream_end_exclusive = min(stream_end_exclusive, experiment_end_epoch)
    return stream_end_exclusive


@dataclass
class _OpenSim:
    candidate: TradeCandidate
    signal: StrategySignal
    session: LiveSimulationSession
    metrics_ticks: list[Tick]


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
        """Execute the experiment deterministically with streaming ticks."""
        self._validate_preconditions()

        ticks_processed = 0
        m1_count = 0
        m15_count = 0
        signals: list[StrategySignal] = []

        engine = StrategyEngine(self.config.instrument, self.config.strategy)
        m1_builder = CandleBuilder(TIMEFRAME_M1)
        m15_builder = CandleBuilder(TIMEFRAME_M15)

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

        if ticks_processed == 0:
            raise ExperimentError(
                f"no ticks for instrument={self.config.instrument!r} "
                f"in range [{self.config.start_epoch}, {self.config.end_epoch})"
            )

        constructor = TradeConstructor(self.config.trade)
        risk = RiskContext(equity=self.config.risk_equity)
        metrics_calc = ResearchMetricsCalculator()

        rejected_rows: list[TradeExperimentRow] = []
        accepted: list[tuple[StrategySignal, TradeCandidate]] = []

        for signal in signals:
            construction = constructor.construct(signal, risk)
            if not construction.accepted or construction.trade is None:
                rejected_rows.append(
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
            accepted.append((signal, construction.trade))

        sim_by_key, metrics_by_key = self._simulate_all_streaming(
            accepted, metrics_calc
        )

        rows: list[TradeExperimentRow] = list(rejected_rows)
        simulations: list[TradeSimulationResult] = []
        metrics_list: list[TradeResearchMetrics] = []
        outcome_counts: dict[str, int] = {
            SimulationOutcome.TP.value: 0,
            SimulationOutcome.SL.value: 0,
            SimulationOutcome.TIMEOUT.value: 0,
            SimulationOutcome.NO_FILL.value: 0,
        }

        for signal, candidate in accepted:
            key = (candidate.signal_epoch, str(candidate.direction))
            sim = sim_by_key[key]
            m = metrics_by_key[key]
            simulations.append(sim)
            metrics_list.append(m)
            outcome_counts[sim.outcome.value] = (
                outcome_counts.get(sim.outcome.value, 0) + 1
            )
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
                    realized_r=_realized_r(sim),
                    mfe=m.mfe,
                    mae=m.mae,
                    signal=signal,
                    candidate=candidate,
                    simulation=sim,
                    metrics=m,
                )
            )

        rows.sort(key=lambda r: (r.signal_epoch, 0 if not r.accepted else 1, r.direction))

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
            candidates_accepted=len(accepted),
            candidates_rejected=len(rejected_rows),
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

    def _simulate_all_streaming(
        self,
        accepted: Sequence[tuple[StrategySignal, TradeCandidate]],
        metrics_calc: ResearchMetricsCalculator,
    ) -> tuple[
        dict[tuple[int, str], TradeSimulationResult],
        dict[tuple[int, str], TradeResearchMetrics],
    ]:
        """One chronological stream; bounded per-session tick buffers (≤ horizon)."""
        sim_by_key: dict[tuple[int, str], TradeSimulationResult] = {}
        metrics_by_key: dict[tuple[int, str], TradeResearchMetrics] = {}
        if not accepted:
            return sim_by_key, metrics_by_key

        pending = sorted(
            accepted,
            key=lambda pair: (pair[1].signal_epoch, str(pair[1].direction)),
        )
        max_dur = self.config.simulation.max_duration_seconds
        exp_end = self.config.end_epoch

        stream_start = min(c.signal_epoch for _, c in pending) + 1
        stream_end = max(
            _simulation_tick_end(c.signal_epoch, max_dur, exp_end) for _, c in pending
        )

        open_sims: list[_OpenSim] = []
        pending_i = 0

        def _activate_due(epoch: int) -> None:
            nonlocal pending_i
            while pending_i < len(pending):
                signal, candidate = pending[pending_i]
                if epoch <= candidate.signal_epoch:
                    break
                open_sims.append(
                    _OpenSim(
                        candidate=candidate,
                        signal=signal,
                        session=LiveSimulationSession(
                            candidate, self.config.simulation
                        ),
                        metrics_ticks=[],
                    )
                )
                pending_i += 1

        def _close(os: _OpenSim, result: TradeSimulationResult) -> None:
            key = (os.candidate.signal_epoch, str(os.candidate.direction))
            m = metrics_calc.calculate(result, os.metrics_ticks)
            sim_by_key[key] = result
            metrics_by_key[key] = m

        for tick in self.repository.as_tick_stream(
            self.config.instrument,
            start_epoch=stream_start,
            end_epoch=stream_end,
        ):
            if exp_end is not None and tick.epoch >= exp_end:
                break
            _activate_due(tick.epoch)

            still_open: list[_OpenSim] = []
            for os in open_sims:
                cand_end = _simulation_tick_end(
                    os.candidate.signal_epoch, max_dur, exp_end
                )
                if tick.epoch >= cand_end:
                    if os.session.is_open:
                        _close(os, os.session.force_close_at_horizon())
                    continue
                if tick.epoch <= os.candidate.signal_epoch:
                    still_open.append(os)
                    continue

                os.metrics_ticks.append(tick)
                result = os.session.on_tick(tick)
                if result is not None:
                    _close(os, result)
                else:
                    still_open.append(os)
            open_sims = still_open

        for os in open_sims:
            if os.session.is_open:
                _close(os, os.session.force_close_at_horizon())

        while pending_i < len(pending):
            signal, candidate = pending[pending_i]
            pending_i += 1
            sess = LiveSimulationSession(candidate, self.config.simulation)
            result = sess.force_close_at_horizon()
            key = (candidate.signal_epoch, str(candidate.direction))
            m = metrics_calc.calculate(result, ())
            sim_by_key[key] = result
            metrics_by_key[key] = m

        return sim_by_key, metrics_by_key

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
