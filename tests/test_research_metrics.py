"""Unit tests for Milestone 2D ResearchMetricsCalculator (MAE / MFE)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
from smb.research import ResearchMetricsCalculator, TradeResearchMetrics
from smb.simulation import (
    ExitReason,
    SimulationConfig,
    SimulationEngine,
    SimulationOutcome,
    TradeSimulationResult,
)
from smb.strategy.models import (
    Direction,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategySignal,
    SwingPoint,
)
from smb.trade.models import TradeCandidate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


def _minimal_signal(
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "vol75",
    signal_epoch: int = 1000,
) -> StrategySignal:
    swing = SwingPoint(
        kind="low" if direction == Direction.LONG else "high",
        price=100.0,
        candle_start_epoch=100,
        candle_end_epoch=160,
        index=2,
        confirmed_at_epoch=280,
    )
    structure = SwingPoint(
        kind="high" if direction == Direction.LONG else "low",
        price=110.0 if direction == Direction.LONG else 90.0,
        candle_start_epoch=0,
        candle_end_epoch=60,
        index=0,
        confirmed_at_epoch=180,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=100.0,
        sweep_candle_start_epoch=300,
        sweep_candle_end_epoch=360,
        sweep_candle_low=98.0,
        sweep_candle_high=103.0,
        sweep_candle_close=102.0,
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure.price,
        msb_candle_start_epoch=420,
        msb_candle_end_epoch=480,
        msb_candle_close=112.0 if direction == Direction.LONG else 88.0,
        bars_after_sweep=1,
        structure_swing=structure,
    )
    disp = Displacement(
        direction=direction,
        candle_start_epoch=480,
        candle_end_epoch=540,
        open=112.0,
        high=120.0,
        low=111.0,
        close=119.0,
        body=7.0,
        range_=9.0,
        body_range_ratio=7 / 9,
        body_atr_ratio=1.0,
        atr=5.0,
    )
    if direction == Direction.LONG:
        fvg = FairValueGap(
            direction=direction,
            gap_low=115.0,
            gap_high=117.0,
            size=2.0,
            size_atr_ratio=0.4,
            candle1_start_epoch=480,
            candle2_start_epoch=540,
            candle3_start_epoch=600,
            candle3_end_epoch=660,
        )
    else:
        fvg = FairValueGap(
            direction=direction,
            gap_low=83.0,
            gap_high=85.0,
            size=2.0,
            size_atr_ratio=0.4,
            candle1_start_epoch=480,
            candle2_start_epoch=540,
            candle3_start_epoch=600,
            candle3_end_epoch=660,
        )
    m15 = M15Context(
        last_m15_start_epoch=None,
        last_m15_end_epoch=None,
        last_m15_close=None,
        recent_high=None,
        recent_low=None,
        directional_bias=None,
    )
    return StrategySignal(
        instrument=instrument,
        direction=direction,
        signal_epoch=signal_epoch,
        timeframe_context="M15+M1",
        sweep=sweep,
        msb=msb,
        displacement=disp,
        fvg=fvg,
        m15_context=m15,
    )


def _candidate(
    *,
    direction: Direction = Direction.LONG,
    signal_epoch: int = 1000,
    entry_price: float = 100.0,
    stop_loss: float = 95.0,
    take_profit: float = 110.0,
    instrument: str = "vol75",
) -> TradeCandidate:
    if direction == Direction.SHORT:
        if stop_loss == 95.0 and take_profit == 110.0 and entry_price == 100.0:
            stop_loss = 105.0
            take_profit = 90.0
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    rr = reward / risk if risk > 0 else 0.0
    signal = _minimal_signal(
        direction=direction, instrument=instrument, signal_epoch=signal_epoch
    )
    return TradeCandidate(
        instrument=instrument,
        direction=direction,
        signal_epoch=signal_epoch,
        entry_price=entry_price,
        entry_zone_low=min(entry_price - 0.5, entry_price + 0.5),
        entry_zone_high=max(entry_price - 0.5, entry_price + 0.5),
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk,
        reward_distance=reward,
        risk_reward=rr,
        risk_percent=0.01,
        risk_amount=10.0,
        position_size=risk and 10.0 / risk or 0.0,
        source_signal=signal,
    )


def _sim_engine(max_duration_seconds: int = 900) -> SimulationEngine:
    return SimulationEngine(SimulationConfig(max_duration_seconds=max_duration_seconds))


def _calc(max_duration_seconds: int = 900) -> ResearchMetricsCalculator:
    return ResearchMetricsCalculator(
        SimulationConfig(max_duration_seconds=max_duration_seconds)
    )


def _run(
    candidate: TradeCandidate,
    ticks: list[Tick],
    *,
    max_duration_seconds: int = 900,
) -> tuple[TradeSimulationResult, TradeResearchMetrics]:
    sim = _sim_engine(max_duration_seconds).simulate(candidate, ticks)
    metrics = _calc(max_duration_seconds).calculate(sim, ticks)
    return sim, metrics


# ---------------------------------------------------------------------------
# LONG / SHORT path metrics
# ---------------------------------------------------------------------------


def test_long_positive_mfe_and_mae():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),  # fill
        _tick(1002, 103.0),  # fav +3
        _tick(1003, 98.0),  # adv +2
        _tick(1004, 105.0),  # fav +5
        _tick(1005, 96.0),  # adv +4
        _tick(1006, 110.0),  # TP, fav +10
    ]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.TP
    assert m.filled is True
    assert m.mfe == pytest.approx(10.0)
    assert m.mae == pytest.approx(4.0)
    assert m.mfe_time == 1006
    assert m.mae_time == 1005
    assert m.observation_start == 1001
    assert m.observation_end == 1006


def test_short_positive_mfe_and_mae():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [
        _tick(1001, 100.0),  # fill
        _tick(1002, 97.0),  # fav +3
        _tick(1003, 102.0),  # adv +2
        _tick(1004, 94.0),  # fav +6
        _tick(1005, 104.0),  # adv +4
        _tick(1006, 90.0),  # TP, fav +10
    ]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.TP
    assert m.mfe == pytest.approx(10.0)
    assert m.mae == pytest.approx(4.0)
    assert m.mfe_time == 1006
    assert m.mae_time == 1005


def test_long_only_favorable():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 102.0),
        _tick(1003, 108.0),
        _tick(1004, 110.0),
    ]
    _, m = _run(c, ticks)
    assert m.mfe == pytest.approx(10.0)
    assert m.mae == pytest.approx(0.0)
    assert m.mae_time == 1001  # zero adverse first seen at fill


def test_long_only_adverse():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 98.0),
        _tick(1003, 96.0),
        _tick(1004, 95.0),  # SL
    ]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.SL
    assert m.mae == pytest.approx(5.0)
    assert m.mfe == pytest.approx(0.0)
    assert m.mae_time == 1004


# ---------------------------------------------------------------------------
# Timestamp correctness
# ---------------------------------------------------------------------------


def test_mfe_timestamp_first_occurrence():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=120.0)
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 105.0),  # mfe=5
        _tick(1003, 105.0),  # equal — keep first
        _tick(1004, 108.0),  # mfe=8
        _tick(1005, 120.0),
    ]
    _, m = _run(c, ticks)
    assert m.mfe == pytest.approx(20.0)
    assert m.mfe_time == 1005


def test_mae_timestamp_first_occurrence():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=130.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 97.0),  # mae=3
        _tick(1003, 97.0),  # equal — keep first
        _tick(1004, 94.0),  # mae=6
        _tick(1005, 130.0),  # TP
    ]
    _, m = _run(c, ticks)
    assert m.mae == pytest.approx(6.0)
    assert m.mae_time == 1004


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_tp_result_metrics():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [_tick(1001, 100.0), _tick(1002, 110.0)]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.TP
    assert m.outcome == SimulationOutcome.TP
    assert m.exit_time == 1002
    assert m.mfe == pytest.approx(10.0)


def test_sl_result_metrics():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [_tick(1001, 100.0), _tick(1002, 95.0)]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.SL
    assert m.mae == pytest.approx(5.0)


def test_timeout_result_metrics():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1005, 103.0),
        _tick(1010, 102.0),
    ]
    sim, m = _run(c, ticks, max_duration_seconds=15)
    assert sim.outcome == SimulationOutcome.TIMEOUT
    assert m.outcome == SimulationOutcome.TIMEOUT
    assert m.mfe == pytest.approx(3.0)
    assert m.observation_end == 1015  # horizon


def test_no_fill_metrics_are_none():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [_tick(1001, 101.0), _tick(1002, 102.0)]
    sim, m = _run(c, ticks, max_duration_seconds=10)
    assert sim.outcome == SimulationOutcome.NO_FILL
    assert m.filled is False
    assert m.mfe is None
    assert m.mae is None
    assert m.mfe_time is None
    assert m.mae_time is None
    assert m.observation_start is None
    assert m.observation_end is None
    assert m.entry_time is None
    assert m.entry_price is None


# ---------------------------------------------------------------------------
# Fill price / pre-fill isolation
# ---------------------------------------------------------------------------


def test_uses_actual_fill_price_not_tick_price():
    """Fill uses planned entry; excursions measured from that price."""
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 98.0),  # crosses entry; fill_price still 100
        _tick(1002, 105.0),
        _tick(1003, 110.0),
    ]
    sim, m = _run(c, ticks)
    assert sim.entry_price == 100.0
    assert m.entry_price == 100.0
    # At fill tick price 98 → adverse 2 from entry 100
    assert m.mae == pytest.approx(2.0)
    assert m.mae_time == 1001
    assert m.mfe == pytest.approx(10.0)


def test_pre_fill_ticks_do_not_affect_metrics():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
    )
    # Pre-fill prices stay strictly above entry so they never fill
    ticks = [
        _tick(1001, 101.0),  # no fill
        _tick(1002, 150.0),  # huge favorable-looking move pre-fill
        _tick(1003, 130.0),  # still above entry
        _tick(1004, 100.0),  # fill
        _tick(1005, 102.0),
        _tick(1006, 120.0),
    ]
    sim, m = _run(c, ticks)
    assert sim.entry_time == 1004
    assert m.mfe == pytest.approx(20.0)
    assert m.mae == pytest.approx(0.0)
    assert m.observation_start == 1004
    # Pre-fill 150 must not appear as MFE relative to entry
    assert m.mfe_time == 1006


def test_ticks_after_horizon_excluded():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=200.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1005, 105.0),
        _tick(1020, 180.0),  # after horizon 15 → must not set MFE
    ]
    sim, m = _run(c, ticks, max_duration_seconds=15)
    assert sim.outcome == SimulationOutcome.TIMEOUT
    assert m.mfe == pytest.approx(5.0)
    assert m.mfe_time == 1005


def test_ticks_after_exit_excluded():
    """After TP/SL exit, later ticks in the stream must not update MFE/MAE."""
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 110.0),  # TP
        _tick(1003, 50.0),  # would be huge MAE if included
        _tick(1004, 200.0),  # would be huge MFE if included
    ]
    sim, m = _run(c, ticks)
    assert sim.outcome == SimulationOutcome.TP
    assert m.observation_end == 1002
    assert m.mfe == pytest.approx(10.0)
    assert m.mae == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_empty_post_fill_observation():
    """Filled but no ticks at/after fill in the provided stream → zero excursions."""
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    sim = _sim_engine(max_duration_seconds=15).simulate(
        c, [_tick(1001, 100.0), _tick(1005, 101.0)]
    )
    assert sim.filled is True
    m = _calc(15).calculate(sim, [_tick(999, 50.0), _tick(1000, 50.0)])
    assert m.mfe == pytest.approx(0.0)
    assert m.mae == pytest.approx(0.0)
    assert m.mfe_time == sim.entry_time
    assert m.mae_time == sim.entry_time


def test_nan_tick_price_raises():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [_tick(1001, 100.0), _tick(1002, float("nan"))]
    sim = _sim_engine().simulate(c, [_tick(1001, 100.0)])
    with pytest.raises(ValueError, match="finite"):
        _calc().calculate(sim, ticks)


def test_inf_tick_price_raises():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    sim = _sim_engine().simulate(c, [_tick(1001, 100.0)])
    with pytest.raises(ValueError, match="finite"):
        _calc().calculate(sim, [_tick(1001, 100.0), _tick(1002, float("inf"))])


def test_deterministic_repeated_calculation():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 103.0),
        _tick(1003, 98.0),
        _tick(1004, 110.0),
    ]
    sim = _sim_engine().simulate(c, ticks)
    calc = _calc()
    m1 = calc.calculate(sim, ticks)
    m2 = calc.calculate(sim, ticks)
    assert m1 == m2


def test_metrics_immutable():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    _, m = _run(c, [_tick(1001, 100.0)], max_duration_seconds=10)
    assert isinstance(m, TradeResearchMetrics)
    with pytest.raises(AttributeError):
        m.mfe = 99.0  # type: ignore[misc]


def test_signal_tick_excluded_from_metrics():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1000, 50.0),  # signal epoch — must not affect
        _tick(1001, 100.0),
        _tick(1002, 110.0),
    ]
    _, m = _run(c, ticks)
    assert m.mae == pytest.approx(0.0)
    assert m.mfe == pytest.approx(10.0)
