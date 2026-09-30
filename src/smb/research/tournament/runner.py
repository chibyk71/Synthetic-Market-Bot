"""Strategy tournament runner — identical cohort, common simulation path."""

from __future__ import annotations

import bisect
import logging
import math
import statistics
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick
from smb.market.candles import (
    TIMEFRAME_M1,
    TIMEFRAME_M15,
    Candle,
    CandleBuilder,
)
from smb.research.metrics import ResearchMetricsCalculator
from smb.research.tournament.constructor import construct_from_tournament_signal
from smb.research.tournament.models import (
    STUDY_VERSION,
    TournamentConfig,
    TournamentResult,
    TournamentRow,
    TournamentSignal,
    TournamentStrategySummary,
)
from smb.research.tournament.strategies import build_strategies
from smb.research.tournament.strategies.ict_adapter import ICTControlStrategy
from smb.simulation.engine import SimulationEngine
from smb.simulation.models import SimulationConfig, SimulationOutcome
from smb.strategy.models import StrategyConfig
from smb.trade.models import RiskContext

logger = logging.getLogger(__name__)


class TournamentError(ValueError):
    """Raised when the tournament cannot run."""


def _realized_r(
    direction: str,
    entry: float,
    exit_price: float,
    risk_distance: float,
) -> float | None:
    if risk_distance <= 0.0 or not math.isfinite(risk_distance):
        return None
    if direction == "long":
        return (exit_price - entry) / risk_distance
    return (entry - exit_price) / risk_distance


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.mean(values))


def _profit_factor(rs: list[float]) -> float | None:
    gains = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    if losses <= 0.0:
        return None if gains <= 0.0 else float("inf")
    return gains / losses


def post_entry_tick_window(
    tick_list: Sequence[Tick],
    signal_epoch: int,
    horizon_seconds: int,
    *,
    epochs: Sequence[int] | None = None,
) -> list[Tick]:
    """Return post-entry ticks via binary search (O(log N) index + O(W) slice).

    Logical definition (unchanged from prior full-scan filter)::

        tick.epoch > signal_epoch
        and tick.epoch <= signal_epoch + horizon_seconds

    ``tick_list`` must be sorted ascending by ``epoch``. When ``epochs`` is
    provided it must be the parallel list ``[t.epoch for t in tick_list]``
    (built once per cohort) so callers avoid re-extracting keys.
    """
    if not tick_list:
        return []
    if epochs is None:
        epochs = [t.epoch for t in tick_list]
    lo = bisect.bisect_right(epochs, signal_epoch)
    hi = bisect.bisect_right(epochs, signal_epoch + horizon_seconds)
    return list(tick_list[lo:hi])


def build_m1_m15_from_ticks(ticks: Sequence[Tick]) -> tuple[list[Candle], list[Candle]]:
    """Build finalized M1 and M15 candles from a chronological tick stream."""
    m1_b = CandleBuilder(TIMEFRAME_M1)
    m15_b = CandleBuilder(TIMEFRAME_M15)
    m1: list[Candle] = []
    m15: list[Candle] = []
    for t in ticks:
        c1 = m1_b.on_tick(t)
        if c1 is not None:
            m1.append(c1)
        c15 = m15_b.on_tick(t)
        if c15 is not None:
            m15.append(c15)
    flushed_m1 = m1_b.flush()
    if flushed_m1 is not None:
        m1.append(flushed_m1)
    flushed_m15 = m15_b.flush()
    if flushed_m15 is not None:
        m15.append(flushed_m15)
    return m1, m15


class StrategyTournamentRunner:
    """Run all strategies on one historical cohort with shared simulation."""

    def __init__(
        self,
        store: ParquetTickStore | Path,
        config: TournamentConfig | None = None,
        *,
        strategy_config: StrategyConfig | None = None,
    ) -> None:
        if isinstance(store, Path):
            store = ParquetTickStore(store)
        self.store = store
        self.config = config if config is not None else TournamentConfig()
        self.strategy_config = strategy_config if strategy_config is not None else StrategyConfig()
        self.repo = TickRepository(self.store)

    def run(self) -> TournamentResult:
        cfg = self.config
        instrument = cfg.instrument

        ticks = self.repo.get_ticks(
            instrument,
            start_epoch=cfg.start_epoch,
            end_epoch=cfg.end_epoch,
        )
        if not ticks:
            raise TournamentError(f"No ticks for {instrument} in requested range")

        tick_list: list[Tick] = []
        for t in ticks:
            if isinstance(t, Tick):
                tick_list.append(t)
            else:
                tick_list.append(t.to_tick())

        # Parallel epoch index for O(log N) window lookups (built once).
        epoch_index: list[int] = [t.epoch for t in tick_list]

        m1, m15 = build_m1_m15_from_ticks(tick_list)
        if not m1:
            raise TournamentError("No M1 candles built from tick stream")

        first_epoch = tick_list[0].epoch
        last_epoch = tick_list[-1].epoch
        dataset = {
            "instrument": instrument,
            "tick_count": len(tick_list),
            "m1_candles": len(m1),
            "m15_candles": len(m15),
            "first_epoch": first_epoch,
            "last_epoch": last_epoch,
            "start_epoch_requested": cfg.start_epoch,
            "end_epoch_requested": cfg.end_epoch,
        }

        strategies = build_strategies(cfg.strategy_ids)
        definitions: dict[str, Any] = {}
        all_signals: dict[str, list[TournamentSignal]] = {}

        for strat in strategies:
            definitions[strat.strategy_id] = strat.definition
            if isinstance(strat, ICTControlStrategy):
                sigs = strat.generate_signals(
                    instrument,
                    m1,
                    strategy_config=self.strategy_config,
                    m15_candles=m15,
                )
            else:
                sigs = strat.generate_signals(
                    instrument, m1, strategy_config=self.strategy_config
                )
            all_signals[strat.strategy_id] = sigs
            logger.info(
                "strategy %s emitted %d signals", strat.strategy_id, len(sigs)
            )

        risk_ctx = RiskContext(equity=cfg.equity)
        sim_engine = SimulationEngine(
            SimulationConfig(max_duration_seconds=cfg.max_duration_seconds)
        )
        metrics_calc = ResearchMetricsCalculator()
        cost_r = float(cfg.spread_cost_r)

        rows: list[TournamentRow] = []

        # Outer loop is signals; post-entry window retrieved once per signal
        # and reused across all target_rr cells (0.30 / 0.40 / 0.50).
        for sid, signals in all_signals.items():
            for sig in signals:
                post = post_entry_tick_window(
                    tick_list,
                    sig.signal_epoch,
                    cfg.max_duration_seconds,
                    epochs=epoch_index,
                )
                for target_rr in cfg.target_r_multiples:
                    construction = construct_from_tournament_signal(
                        sig,
                        target_rr=target_rr,
                        risk_context=risk_ctx,
                        risk_per_trade=cfg.risk_per_trade,
                        minimum_rr=cfg.minimum_rr,
                    )
                    if not construction.accepted or construction.trade is None:
                        rows.append(
                            TournamentRow(
                                strategy_id=sid,
                                target_rr=target_rr,
                                instrument=instrument,
                                signal_epoch=sig.signal_epoch,
                                direction=sig.direction.value,
                                accepted=False,
                                filled=False,
                                outcome=None,
                                entry_price=None,
                                stop_loss=None,
                                take_profit=None,
                                entry_time=None,
                                exit_time=None,
                                duration_seconds=None,
                                realized_r=None,
                                mfe=None,
                                mae=None,
                                signal_metadata=dict(sig.metadata),
                                candidate=None,
                                simulation=None,
                                metrics=None,
                                gross_r=None,
                                cost_r=None,
                                net_r=None,
                            )
                        )
                        continue

                    candidate = construction.trade
                    sim = sim_engine.simulate(candidate, post)
                    metrics = metrics_calc.calculate(sim, post)

                    gross: float | None = None
                    net: float | None = None
                    applied_cost: float | None = None
                    if (
                        sim.filled
                        and sim.entry_price is not None
                        and sim.exit_price is not None
                    ):
                        gross = _realized_r(
                            candidate.direction.value,
                            sim.entry_price,
                            sim.exit_price,
                            candidate.risk_distance,
                        )
                        if gross is not None:
                            applied_cost = cost_r
                            net = gross - cost_r

                    rows.append(
                        TournamentRow(
                            strategy_id=sid,
                            target_rr=target_rr,
                            instrument=instrument,
                            signal_epoch=sig.signal_epoch,
                            direction=sig.direction.value,
                            accepted=True,
                            filled=sim.filled,
                            outcome=sim.outcome,
                            entry_price=candidate.entry_price,
                            stop_loss=candidate.stop_loss,
                            take_profit=candidate.take_profit,
                            entry_time=sim.entry_time,
                            exit_time=sim.exit_time,
                            duration_seconds=sim.duration_seconds,
                            realized_r=gross,  # gross (pre-cost) for backward compat
                            mfe=metrics.mfe,
                            mae=metrics.mae,
                            signal_metadata=dict(sig.metadata),
                            candidate=candidate,
                            simulation=sim,
                            metrics=metrics,
                            gross_r=gross,
                            cost_r=applied_cost,
                            net_r=net,
                        )
                    )

        summaries = self._aggregate(
            rows,
            list(all_signals.keys()),
            list(cfg.target_r_multiples),
            spread_cost_r=cost_r,
        )
        return TournamentResult(
            study_version=STUDY_VERSION,
            config=cfg,
            dataset=dataset,
            strategy_definitions=definitions,
            summaries=tuple(summaries),
            rows=tuple(rows),
        )

    def _aggregate(
        self,
        rows: Sequence[TournamentRow],
        strategy_ids: list[str],
        targets: list[float],
        *,
        spread_cost_r: float = 0.0,
    ) -> list[TournamentStrategySummary]:
        out: list[TournamentStrategySummary] = []
        for sid in strategy_ids:
            for tr in targets:
                cell = [r for r in rows if r.strategy_id == sid and r.target_rr == tr]
                signals = len(cell)
                accepted = sum(1 for r in cell if r.accepted)
                fills = sum(1 for r in cell if r.filled)
                no_fill = sum(
                    1
                    for r in cell
                    if r.outcome == SimulationOutcome.NO_FILL
                    or (r.accepted and not r.filled)
                )
                tp = sum(1 for r in cell if r.outcome == SimulationOutcome.TP)
                sl = sum(1 for r in cell if r.outcome == SimulationOutcome.SL)
                timeout = sum(1 for r in cell if r.outcome == SimulationOutcome.TIMEOUT)

                gross_rs = [
                    r.gross_r if r.gross_r is not None else r.realized_r
                    for r in cell
                    if (r.gross_r is not None or r.realized_r is not None)
                ]
                # Prefer explicit gross_r; fall back to realized_r
                gross_rs = [float(x) for x in gross_rs if x is not None]
                net_rs = [float(r.net_r) for r in cell if r.net_r is not None]
                durs = [
                    float(r.duration_seconds)
                    for r in cell
                    if r.duration_seconds is not None
                ]
                maes = [float(r.mae) for r in cell if r.mae is not None]
                mfes = [float(r.mfe) for r in cell if r.mfe is not None]

                win_rate = (tp / fills) if fills > 0 else None
                target_hit = (tp / fills) if fills > 0 else None
                stop_hit = (sl / fills) if fills > 0 else None
                timeout_rate = (timeout / fills) if fills > 0 else None

                out.append(
                    TournamentStrategySummary(
                        strategy_id=sid,
                        target_rr=tr,
                        signals=signals,
                        accepted=accepted,
                        fills=fills,
                        no_fill=no_fill,
                        tp=tp,
                        sl=sl,
                        timeout=timeout,
                        win_rate=win_rate,
                        total_r=sum(gross_rs) if gross_rs else None,
                        average_r=_mean(gross_rs),
                        median_r=_median(gross_rs),
                        profit_factor=_profit_factor(gross_rs) if gross_rs else None,
                        mean_duration_seconds=_mean(durs),
                        median_duration_seconds=_median(durs),
                        mean_mae=_mean(maes),
                        mean_mfe=_mean(mfes),
                        median_mae=_median(maes),
                        median_mfe=_median(mfes),
                        target_hit_rate=target_hit,
                        stop_hit_rate=stop_hit,
                        timeout_rate=timeout_rate,
                        total_gross_r=sum(gross_rs) if gross_rs else None,
                        total_net_r=sum(net_rs) if net_rs else None,
                        average_gross_r=_mean(gross_rs),
                        average_net_r=_mean(net_rs),
                        median_net_r=_median(net_rs),
                        profit_factor_net=_profit_factor(net_rs) if net_rs else None,
                        spread_cost_r=spread_cost_r,
                    )
                )
        return out
