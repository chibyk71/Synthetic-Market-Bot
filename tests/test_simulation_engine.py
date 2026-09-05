"""Unit tests for Milestone 2C SimulationEngine — causality, fills, exits."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
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
    """Build a minimal valid TradeCandidate (geometry not re-validated)."""
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


def _engine(max_duration_seconds: int = 900) -> SimulationEngine:
    return SimulationEngine(SimulationConfig(max_duration_seconds=max_duration_seconds))


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_simulation_config_defaults():
    cfg = SimulationConfig()
    assert cfg.max_duration_seconds == 900


def test_simulation_config_rejects_non_positive():
    with pytest.raises(ValueError, match="max_duration_seconds"):
        SimulationConfig(max_duration_seconds=0)
    with pytest.raises(ValueError, match="max_duration_seconds"):
        SimulationConfig(max_duration_seconds=-1)


def test_simulation_config_rejects_non_int():
    with pytest.raises(ValueError, match="max_duration_seconds"):
        SimulationConfig(max_duration_seconds=900.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LONG outcomes
# ---------------------------------------------------------------------------


def test_long_tp():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 101.0),
        _tick(1002, 100.0),
        _tick(1003, 105.0),
        _tick(1004, 110.0),
        _tick(1005, 112.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TP
    assert result.filled is True
    assert result.entry_time == 1002
    assert result.entry_price == 100.0
    assert result.exit_time == 1004
    assert result.exit_price == 110.0
    assert result.exit_reason == ExitReason.TP
    assert result.duration_seconds == 2
    assert result.instrument == "vol75"
    assert result.direction == Direction.LONG
    assert result.signal_epoch == 1000
    assert result.candidate is c


def test_long_sl():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 99.5),
        _tick(1002, 97.0),
        _tick(1003, 95.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.filled is True
    assert result.entry_time == 1001
    assert result.entry_price == 100.0
    assert result.exit_time == 1003
    assert result.exit_price == 95.0
    assert result.exit_reason == ExitReason.SL
    assert result.duration_seconds == 2


def test_long_no_fill():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [_tick(e, 101.0 + (e % 3) * 0.1) for e in range(1001, 1100)]
    result = _engine(max_duration_seconds=100).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.NO_FILL
    assert result.filled is False
    assert result.entry_time is None
    assert result.entry_price is None
    assert result.exit_time is None
    assert result.exit_price is None
    assert result.exit_reason == ExitReason.NONE
    assert result.duration_seconds is None


def test_long_timeout():
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),  # fill
        _tick(1002, 101.0),
        _tick(1005, 102.0),
        _tick(1010, 103.0),  # still inside, no TP/SL
    ]
    result = _engine(max_duration_seconds=15).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.filled is True
    assert result.entry_time == 1001
    assert result.entry_price == 100.0
    assert result.exit_reason == ExitReason.TIMEOUT
    assert result.exit_time == 1015  # horizon_end, not last tick
    assert result.exit_price is None
    assert result.duration_seconds == 14


# ---------------------------------------------------------------------------
# SHORT outcomes
# ---------------------------------------------------------------------------


def test_short_tp():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [
        _tick(1001, 99.0),
        _tick(1002, 100.0),
        _tick(1003, 95.0),
        _tick(1004, 90.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TP
    assert result.filled is True
    assert result.entry_time == 1002
    assert result.entry_price == 100.0
    assert result.exit_time == 1004
    assert result.exit_price == 90.0
    assert result.exit_reason == ExitReason.TP


def test_short_sl():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [
        _tick(1001, 100.5),
        _tick(1002, 103.0),
        _tick(1003, 105.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.entry_time == 1001
    assert result.exit_time == 1003
    assert result.exit_price == 105.0
    assert result.exit_reason == ExitReason.SL


def test_short_no_fill():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [_tick(e, 99.0) for e in range(1001, 1050)]
    result = _engine(max_duration_seconds=50).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.NO_FILL
    assert result.filled is False
    assert result.exit_reason == ExitReason.NONE


def test_short_timeout():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1005, 99.0),
        _tick(1010, 98.0),
    ]
    result = _engine(max_duration_seconds=15).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.filled is True
    assert result.exit_reason == ExitReason.TIMEOUT
    assert result.exit_time == 1015  # horizon_end, not last tick


# ---------------------------------------------------------------------------
# Entry touch / cross
# ---------------------------------------------------------------------------


def test_long_entry_exactly_touched():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [_tick(1001, 100.0)]
    result = _engine().simulate(c, ticks)
    assert result.filled is True
    assert result.entry_time == 1001
    assert result.entry_price == 100.0


def test_long_entry_crossed():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [_tick(1001, 99.5)]
    result = _engine().simulate(c, ticks)
    assert result.filled is True
    assert result.entry_price == 100.0


def test_short_entry_exactly_touched():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [_tick(1001, 100.0)]
    result = _engine().simulate(c, ticks)
    assert result.filled is True
    assert result.entry_price == 100.0


def test_short_entry_crossed():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=105.0,
        take_profit=90.0,
    )
    ticks = [_tick(1001, 100.5)]
    result = _engine().simulate(c, ticks)
    assert result.filled is True


# ---------------------------------------------------------------------------
# Exit exact / cross
# ---------------------------------------------------------------------------


def test_long_tp_exactly_touched():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [_tick(1001, 100.0), _tick(1002, 110.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TP
    assert result.exit_price == 110.0


def test_long_sl_exactly_touched():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, stop_loss=95.0)
    ticks = [_tick(1001, 100.0), _tick(1002, 95.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.exit_price == 95.0


def test_long_tp_crossed():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [_tick(1001, 100.0), _tick(1002, 112.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TP
    assert result.exit_price == 110.0


def test_long_sl_crossed():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, stop_loss=95.0)
    ticks = [_tick(1001, 100.0), _tick(1002, 94.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.exit_price == 95.0


# ---------------------------------------------------------------------------
# Causality / data leakage (CRITICAL)
# ---------------------------------------------------------------------------


def test_signal_tick_excluded_would_have_filled():
    """A tick at exactly signal_epoch that touches entry must NOT fill."""
    c = _candidate(direction=Direction.LONG, signal_epoch=1000, entry_price=100.0)
    ticks = [
        _tick(1000, 100.0),
        _tick(1001, 101.0),
    ]
    result = _engine(max_duration_seconds=10).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.NO_FILL
    assert result.filled is False


def test_pre_signal_tick_excluded():
    """Profitable / filling tick before signal must have zero influence."""
    c = _candidate(
        direction=Direction.LONG,
        signal_epoch=1000,
        entry_price=100.0,
        take_profit=110.0,
        stop_loss=95.0,
    )
    ticks = [
        _tick(999, 100.0),
        _tick(999, 110.0),
        _tick(1000, 100.0),
        _tick(1001, 101.0),
    ]
    result = _engine(max_duration_seconds=10).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.NO_FILL


def test_post_horizon_tick_excluded():
    """TP/SL after horizon must not determine the result."""
    c = _candidate(
        direction=Direction.LONG,
        signal_epoch=1000,
        entry_price=100.0,
        take_profit=110.0,
        stop_loss=95.0,
    )
    ticks = [
        _tick(1001, 100.0),  # fill
        _tick(1005, 102.0),
        _tick(1016, 110.0),  # after horizon (15s) — must not count as TP
    ]
    result = _engine(max_duration_seconds=15).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.exit_reason == ExitReason.TIMEOUT
    assert result.exit_time == 1015  # horizon_end, not last in-horizon tick


def test_tick_exactly_at_horizon_is_eligible():
    """Tick at signal_epoch + max_duration is still inside the horizon."""
    c = _candidate(
        direction=Direction.LONG,
        signal_epoch=1000,
        entry_price=100.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1015, 110.0),
    ]
    result = _engine(max_duration_seconds=15).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TP
    assert result.exit_time == 1015


def test_first_eligible_tick_is_strictly_after_signal():
    c = _candidate(direction=Direction.LONG, signal_epoch=1000, entry_price=100.0)
    ticks = [
        _tick(999, 99.0),
        _tick(1000, 99.0),
        _tick(1001, 99.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.filled is True
    assert result.entry_time == 1001


# ---------------------------------------------------------------------------
# Same-tick TP/SL ambiguity — conservative SL
# ---------------------------------------------------------------------------


def test_same_tick_entry_and_both_exits_classifies_sl_long():
    """Single tick fills and satisfies both TP and SL → SL (conservative)."""
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=120.0,
        take_profit=80.0,
    )
    ticks = [_tick(1001, 90.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.exit_reason == ExitReason.SL
    assert result.entry_time == 1001
    assert result.exit_time == 1001


def test_same_tick_both_exits_after_entry_classifies_sl_short():
    c = _candidate(
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=110.0,
    )
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 100.0),
    ]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.SL
    assert result.exit_reason == ExitReason.SL


# ---------------------------------------------------------------------------
# Edge: empty / single tick / short horizon
# ---------------------------------------------------------------------------


def test_empty_tick_stream():
    c = _candidate()
    result = _engine().simulate(c, [])
    assert result.outcome == SimulationOutcome.NO_FILL


def test_single_eligible_tick_fill_only():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    result = _engine().simulate(c, [_tick(1001, 100.0)])
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.filled is True
    assert result.entry_time == 1001
    assert result.exit_time == 1900  # signal 1000 + default 900
    assert result.duration_seconds == 899


def test_no_ticks_after_signal():
    c = _candidate(signal_epoch=1000)
    ticks = [_tick(900, 100.0), _tick(1000, 100.0)]
    result = _engine().simulate(c, ticks)
    assert result.outcome == SimulationOutcome.NO_FILL


def test_extremely_short_horizon():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 110.0),
    ]
    result = _engine(max_duration_seconds=1).simulate(c, ticks)
    # 1002 is beyond horizon (1000+1=1001); TIMEOUT at horizon_end
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.exit_time == 1001
    assert result.duration_seconds == 0


# ---------------------------------------------------------------------------
# Determinism & immutability
# ---------------------------------------------------------------------------


def test_deterministic_repeated_simulation():
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 101.0),
        _tick(1002, 100.0),
        _tick(1003, 105.0),
        _tick(1004, 110.0),
    ]
    engine = _engine()
    r1 = engine.simulate(c, ticks)
    r2 = engine.simulate(c, ticks)
    assert r1 == r2
    assert r1.outcome == SimulationOutcome.TP


def test_result_is_immutable():
    c = _candidate()
    result = _engine().simulate(c, [_tick(1001, 100.0)])
    assert isinstance(result, TradeSimulationResult)
    with pytest.raises(AttributeError):
        result.outcome = SimulationOutcome.TP  # type: ignore[misc]


def test_config_is_immutable():
    cfg = SimulationConfig(max_duration_seconds=60)
    with pytest.raises(AttributeError):
        cfg.max_duration_seconds = 30  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Numerical validity
# ---------------------------------------------------------------------------


def test_nan_tick_price_raises():
    c = _candidate()
    ticks = [_tick(1001, float("nan"))]
    with pytest.raises(ValueError, match="finite"):
        _engine().simulate(c, ticks)


def test_inf_tick_price_raises():
    c = _candidate()
    ticks = [_tick(1001, float("inf"))]
    with pytest.raises(ValueError, match="finite"):
        _engine().simulate(c, ticks)


# ---------------------------------------------------------------------------
# Fill price is candidate entry (no slippage)
# ---------------------------------------------------------------------------


def test_fill_price_is_candidate_entry_not_tick():
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [_tick(1001, 98.5)]
    result = _engine().simulate(c, ticks)
    assert result.entry_price == 100.0


# ---------------------------------------------------------------------------
# Chronological processing — does not reorder
# ---------------------------------------------------------------------------


def test_processes_in_source_order_not_sorted():
    """Engine must not silently sort; if source order is weird, follow it.
    Causality still filters by epoch, but among eligible ticks order is source.
    """
    c = _candidate(
        direction=Direction.LONG,
        entry_price=100.0,
        take_profit=110.0,
        stop_loss=95.0,
    )
    # Out-of-order source: later epoch first, then earlier eligible
    ticks = [
        _tick(1005, 110.0),  # would be TP if already filled
        _tick(1002, 100.0),  # fill
        _tick(1003, 101.0),
    ]
    # Following source order: see 1005 first → fill? 110 <= 100? No.
    # Then 1002 → fill. Then 1003 → no exit. TIMEOUT (no TP after fill in order)
    result = _engine(max_duration_seconds=20).simulate(c, ticks)
    assert result.filled is True
    assert result.entry_time == 1002
    # TP tick was before fill in source order, so never applied
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.exit_time == 1020


def test_timeout_uses_horizon_not_last_tick():
    """TIMEOUT reports horizon_end even when the supplied stream ends early."""
    c = _candidate(direction=Direction.LONG, entry_price=100.0)
    ticks = [
        _tick(1001, 100.0),  # fill
        _tick(1005, 101.0),  # stream ends early
    ]
    result = _engine(max_duration_seconds=15).simulate(c, ticks)
    assert result.outcome == SimulationOutcome.TIMEOUT
    assert result.exit_time == 1015
    assert result.duration_seconds == 14
    assert result.exit_price is None
    assert result.exit_reason == ExitReason.TIMEOUT
