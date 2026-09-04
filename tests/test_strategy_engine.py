"""Strategy engine: sweeps, MSB, displacement, FVG, full setups, lookahead."""

from __future__ import annotations

import pytest

from smb.market.candles import Candle
from smb.strategy import (
    Direction,
    OutOfOrderCandleError,
    StrategyConfig,
    StrategyEngine,
    StrategyState,
)


def m1(
    start: int,
    o: float,
    h: float,
    low: float,
    c: float,
) -> Candle:
    return Candle(
        timeframe="M1",
        start_epoch=start,
        end_epoch=start + 60,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_count=60,
        finalized=True,
    )


def m15(
    start: int,
    o: float,
    h: float,
    low: float,
    c: float,
) -> Candle:
    return Candle(
        timeframe="M15",
        start_epoch=start,
        end_epoch=start + 900,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_count=900,
        finalized=True,
    )


def _build_confirmed_swing_low_sequence(
    base_epoch: int = 0,
    swing_price: float = 100.0,
) -> list[Candle]:
    return [
        m1(base_epoch + 0, 106, 108, 105, 106),
        m1(base_epoch + 60, 106, 107, 104, 105),
        m1(base_epoch + 120, 105, 106, swing_price, 103),
        m1(base_epoch + 180, 103, 106, 103, 105),
        m1(base_epoch + 240, 105, 107, 104, 106),
    ]


def test_out_of_order_m1_raises():
    eng = StrategyEngine("test")
    eng.on_m1(m1(100, 1, 2, 0, 1))
    with pytest.raises(OutOfOrderCandleError):
        eng.on_m1(m1(0, 1, 2, 0, 1))


def test_out_of_order_m15_raises():
    eng = StrategyEngine("test")
    eng.on_m15(m15(900, 1, 2, 0, 1))
    with pytest.raises(OutOfOrderCandleError):
        eng.on_m15(m15(0, 1, 2, 0, 1))


def test_duplicate_end_epoch_ignored():
    eng = StrategyEngine("test")
    c = m1(0, 1, 2, 0, 1)
    assert eng.on_m1(c) == []
    assert eng.on_m1(c) == []


def test_bullish_sweep_detected():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    assert eng.state == StrategyState.SWEEP_DETECTED
    assert eng._active_sweep is not None
    assert eng._active_sweep.direction == Direction.LONG
    assert eng._active_sweep.swept_level == 100.0
    assert eng._active_structure_swing is not None
    assert eng._active_structure_swing.price == 112.0


def test_bearish_sweep_detected():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    candles = [
        m1(0, 110, 112, 109, 111),
        m1(60, 111, 114, 110, 113),
        m1(120, 113, 120, 112, 118),
        m1(180, 118, 119, 115, 116),
        m1(240, 116, 117, 100, 102),
        m1(300, 102, 105, 101, 103),
        m1(360, 103, 106, 102, 104),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 110, 123, 108, 115))
    assert eng.state == StrategyState.SWEEP_DETECTED
    assert eng._active_sweep is not None
    assert eng._active_sweep.direction == Direction.SHORT
    assert eng._active_structure_swing is not None
    assert eng._active_structure_swing.price == 100.0


def test_breach_without_close_back_is_not_sweep():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 102, 103, 98, 99))
    assert eng.state == StrategyState.IDLE


def test_close_back_without_breach_is_not_sweep():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 101, 103, 100.5, 102))
    assert eng.state == StrategyState.IDLE


def test_sweep_cannot_use_unconfirmed_swing():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    partial = [
        m1(0, 106, 108, 105, 106),
        m1(60, 106, 107, 104, 105),
        m1(120, 105, 106, 100, 103),
    ]
    for c in partial:
        eng.on_m1(c)
    eng.on_m1(m1(180, 102, 104, 98, 101))
    assert eng.state == StrategyState.IDLE


def test_sweep_without_structure_does_not_activate():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    for c in _build_confirmed_swing_low_sequence(0, 100.0):
        eng.on_m1(c)
    eng.on_m1(m1(300, 102, 104, 98, 101))
    assert eng.state == StrategyState.IDLE


def test_bullish_msb_within_window():
    cfg = StrategyConfig(swing_x=2, msb_window_bars=3, atr_period=3)
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    assert eng.state == StrategyState.SWEEP_DETECTED
    eng.on_m1(m1(480, 103, 115, 102, 114))
    assert eng.state == StrategyState.MSB_DETECTED
    assert eng._active_msb is not None
    assert eng._active_msb.direction == Direction.LONG
    assert eng._active_msb.broken_level == 112.0
    assert eng._active_displacement is None


def test_msb_expires_after_window():
    cfg = StrategyConfig(swing_x=2, msb_window_bars=2, atr_period=3)
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    eng.on_m1(m1(480, 103, 105, 102, 104))
    eng.on_m1(m1(540, 104, 106, 103, 105))
    eng.on_m1(m1(600, 105, 107, 104, 106))
    assert eng.state == StrategyState.IDLE


def test_msb_must_occur_after_sweep():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 106, 120, 105, 119))
    assert eng.state == StrategyState.IDLE


def test_post_sweep_confirmed_swing_cannot_be_msb_structure():
    """Mandatory: swing confirmed AFTER the sweep must not be MSB structure."""
    cfg = StrategyConfig(swing_x=2, msb_window_bars=8, atr_period=3)
    eng = StrategyEngine("test", cfg)
    sequence = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 112, 105, 110),
        m1(180, 110, 111, 100, 102),
        m1(240, 102, 108, 101, 107),
        m1(300, 107, 109, 106, 108),
    ]
    for c in sequence:
        eng.on_m1(c)
    eng.on_m1(m1(360, 105, 107, 98, 103))
    assert eng.state == StrategyState.SWEEP_DETECTED
    assert eng._active_structure_swing is not None
    assert eng._active_structure_swing.price == 112.0
    frozen = eng._active_structure_swing.price
    eng.on_m1(m1(420, 103, 140, 102, 138))
    assert eng.state == StrategyState.MSB_DETECTED
    assert eng._active_msb is not None
    assert eng._active_msb.broken_level == frozen
    assert eng._active_msb.structure_swing.price == frozen
    assert eng._active_msb.broken_level != 140.0
    assert eng._active_msb.structure_swing.confirmed_at_epoch < (
        eng._active_sweep.sweep_candle_end_epoch
    )


def test_post_sweep_only_structure_not_yet_confirmed_blocks_setup():
    cfg = StrategyConfig(swing_x=2, msb_window_bars=5, atr_period=3)
    eng = StrategyEngine("test", cfg)
    for c in _build_confirmed_swing_low_sequence(0, 100.0):
        eng.on_m1(c)
    eng.on_m1(m1(300, 102, 104, 98, 101))
    assert eng.state == StrategyState.IDLE


def test_same_candle_msb_and_displacement_does_not_signal():
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.50,
        displacement_body_atr_ratio=0.50,
        atr_period=3,
    )
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    eng.on_m1(m1(480, 103, 118, 102, 117))
    assert eng.state == StrategyState.MSB_DETECTED
    assert eng._active_displacement is None
    assert len(eng.signals) == 0


def test_zero_range_candle_not_displacement():
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.5,
        displacement_body_atr_ratio=0.1,
        atr_period=2,
    )
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    eng.on_m1(m1(480, 103, 115, 102, 114))
    assert eng.state == StrategyState.MSB_DETECTED
    eng.on_m1(m1(540, 114, 114, 114, 114))
    assert eng.state == StrategyState.MSB_DETECTED
    assert eng._active_displacement is None


def test_displacement_body_range_threshold():
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.90,
        displacement_body_atr_ratio=0.01,
        atr_period=2,
    )
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    eng.on_m1(m1(480, 103, 115, 102, 114))
    eng.on_m1(m1(540, 114, 116, 113, 115))
    assert eng.state in (StrategyState.MSB_DETECTED, StrategyState.IDLE)


def test_bullish_fvg_geometry():
    c1 = m1(0, 100, 105, 99, 104)
    c3 = m1(120, 109, 115, 108, 114)
    assert c3.low > c1.high
    assert c3.low - c1.high == 3.0


def test_bearish_fvg_geometry():
    c1 = m1(0, 110, 112, 108, 109)
    c3 = m1(120, 101, 102, 95, 96)
    assert c3.high < c1.low


def test_no_gap():
    c1 = m1(0, 100, 105, 99, 104)
    c3 = m1(120, 103, 106, 102, 105)
    assert not (c3.low > c1.high)


def _full_bullish_fixture() -> tuple[list[Candle], StrategyConfig]:
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.50,
        displacement_body_atr_ratio=0.50,
        atr_period=3,
    )
    candles: list[Candle] = []
    t = 0

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal t
        candles.append(m1(t, o, h, low, c))
        t += 60

    add(108, 110, 107, 109)
    add(109, 111, 106, 108)
    add(108, 109, 100, 102)
    add(102, 108, 101, 107)
    add(107, 115, 106, 114)
    add(114, 114.5, 112, 113)
    add(113, 114, 111, 112)
    add(112, 113, 111, 112)
    add(105, 108, 97, 104)
    add(104, 118, 103, 117)
    add(117, 125, 116, 124)
    add(124, 126, 123, 125)
    add(125, 130, 127, 129)
    return candles, cfg


def _full_bearish_fixture() -> tuple[list[Candle], StrategyConfig]:
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.50,
        displacement_body_atr_ratio=0.50,
        atr_period=3,
    )
    candles: list[Candle] = []
    t = 0

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal t
        candles.append(m1(t, o, h, low, c))
        t += 60

    add(110, 112, 109, 111)
    add(111, 114, 110, 113)
    add(113, 120, 112, 118)
    add(118, 119, 115, 116)
    add(116, 117, 100, 102)
    add(102, 105, 101, 103)
    add(103, 106, 102, 104)
    add(110, 123, 108, 115)
    add(115, 116, 95, 96)
    add(96, 97, 70, 71)
    add(71, 72, 69, 70)
    add(69, 69.5, 60, 61)
    return candles, cfg


def test_full_bullish_setup_emits_long_signal():
    candles, cfg = _full_bullish_fixture()
    eng = StrategyEngine("vol75", cfg)
    eng.on_m15(m15(0, 100, 120, 95, 110))
    signals = eng.process(candles)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.direction == Direction.LONG
    assert sig.instrument == "vol75"
    assert sig.sweep.direction == Direction.LONG
    assert sig.msb.direction == Direction.LONG
    assert sig.displacement.direction == Direction.LONG
    assert sig.fvg.direction == Direction.LONG
    assert sig.fvg.size > 0
    assert sig.displacement.candle_start_epoch > sig.msb.msb_candle_start_epoch
    assert "swept_level" in sig.reference_levels
    assert not hasattr(sig, "stop_loss")
    assert not hasattr(sig, "take_profit")
    assert not hasattr(sig, "position_size")


def test_full_bearish_setup_emits_short_signal():
    candles, cfg = _full_bearish_fixture()
    eng = StrategyEngine("step", cfg)
    eng.on_m15(m15(0, 120, 130, 90, 100))
    signals = eng.process(candles)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.direction == Direction.SHORT
    assert sig.fvg.direction == Direction.SHORT
    assert sig.displacement.candle_start_epoch > sig.msb.msb_candle_start_epoch


def test_swing_unavailable_until_confirmation():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    partial = [
        m1(0, 106, 108, 105, 106),
        m1(60, 106, 107, 104, 105),
        m1(120, 105, 106, 100, 103),
    ]
    for c in partial:
        eng.on_m1(c)
    eng.on_m1(m1(180, 102, 104, 98, 101))
    assert eng.state == StrategyState.IDLE


def test_m15_not_usable_before_close():
    eng = StrategyEngine("test", StrategyConfig(swing_x=2, atr_period=2))
    for i in range(5):
        eng.on_m1(m1(i * 60, 100, 101, 99, 100))
    ctx = eng._m15_context_at(decision_epoch=300)
    assert ctx.last_m15_end_epoch is None
    eng.on_m15(m15(0, 100, 110, 90, 105))
    ctx2 = eng._m15_context_at(decision_epoch=900)
    assert ctx2.last_m15_end_epoch == 900
    assert ctx2.last_m15_close == 105.0
    ctx3 = eng._m15_context_at(decision_epoch=600)
    assert ctx3.last_m15_end_epoch is None


def test_appending_future_candles_does_not_change_past_signals():
    candles, cfg = _full_bullish_fixture()
    eng = StrategyEngine("vol75", cfg)
    eng.on_m15(m15(0, 100, 120, 95, 110))
    signals1 = eng.process(candles)
    assert len(signals1) >= 1
    first_epoch = signals1[0].signal_epoch
    first_dir = signals1[0].direction
    last_start = candles[-1].start_epoch
    extra = [m1(last_start + 60 * (i + 1), 120, 121, 119, 120) for i in range(20)]
    eng.process(extra)
    assert eng.signals[0].signal_epoch == first_epoch
    assert eng.signals[0].direction == first_dir
    past = [s for s in eng.signals if s.signal_epoch <= first_epoch]
    assert len(past) == len(signals1)


def test_batch_equals_streaming():
    candles, cfg = _full_bullish_fixture()
    eng_batch = StrategyEngine("vol75", cfg)
    eng_batch.on_m15(m15(0, 100, 120, 95, 110))
    batch_signals = eng_batch.process(list(candles))
    eng_stream = StrategyEngine("vol75", cfg)
    eng_stream.on_m15(m15(0, 100, 120, 95, 110))
    stream_signals: list = []
    for c in candles:
        stream_signals.extend(eng_stream.on_m1(c))
    assert len(batch_signals) == len(stream_signals)
    for a, b in zip(batch_signals, stream_signals, strict=True):
        assert a.signal_epoch == b.signal_epoch
        assert a.direction == b.direction
        assert a.sweep.swept_level == b.sweep.swept_level
        assert a.fvg.size == b.fvg.size


def test_determinism_identical_runs():
    candles, cfg = _full_bullish_fixture()

    def run():
        eng = StrategyEngine("vol75", cfg)
        eng.on_m15(m15(0, 100, 120, 95, 110))
        return eng.process(candles)

    s1 = run()
    s2 = run()
    assert len(s1) == len(s2)
    for a, b in zip(s1, s2, strict=True):
        assert a == b


def test_reset_clears_state():
    candles, cfg = _full_bullish_fixture()
    eng = StrategyEngine("vol75", cfg)
    eng.on_m15(m15(0, 100, 120, 95, 110))
    eng.process(candles)
    eng.reset()
    assert eng.state == StrategyState.IDLE
    assert len(eng.signals) == 0
    assert eng._m1 == []
    assert eng._active_structure_swing is None


def test_sweep_without_msb_expires():
    cfg = StrategyConfig(swing_x=2, msb_window_bars=1, atr_period=2)
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 108, 110, 107, 109),
        m1(60, 109, 111, 106, 108),
        m1(120, 108, 109, 100, 102),
        m1(180, 102, 108, 101, 107),
        m1(240, 107, 112, 106, 110),
        m1(300, 110, 111, 108, 109),
        m1(360, 109, 110, 107, 108),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(420, 105, 107, 98, 103))
    assert eng.state == StrategyState.SWEEP_DETECTED
    eng.on_m1(m1(480, 101, 103, 100, 102))
    eng.on_m1(m1(540, 102, 104, 101, 103))
    assert eng.state == StrategyState.IDLE


def test_sweep_cannot_use_swing_confirmed_by_sweep_candle():
    """Swing right-side confirmation on the sweep candle must not qualify."""
    cfg = StrategyConfig(swing_x=2, atr_period=2)
    eng = StrategyEngine("test", cfg)
    candles = [
        m1(0, 106, 108, 105, 106),
        m1(60, 106, 107, 104, 105),
        m1(120, 105, 106, 100, 103),
        m1(180, 103, 106, 103, 105),
    ]
    for c in candles:
        eng.on_m1(c)
    eng.on_m1(m1(240, 102, 104, 98, 101))
    assert eng.state == StrategyState.IDLE
    assert eng._active_sweep is None


def test_strategy_signal_mappings_are_immutable():
    """reference_levels and metadata must reject in-place mutation."""
    candles, cfg = _full_bullish_fixture()
    eng = StrategyEngine("vol75", cfg)
    eng.on_m15(m15(0, 100, 120, 95, 110))
    signals = eng.process(candles)
    assert len(signals) >= 1
    sig = signals[0]
    with pytest.raises(TypeError):
        sig.reference_levels["swept_level"] = 0.0  # type: ignore[index]
    with pytest.raises(TypeError):
        sig.metadata["extra"] = True  # type: ignore[index]
    with pytest.raises(AttributeError):
        sig.metadata = {"hacked": True}  # type: ignore[misc]


def test_non_finalized_candle_rejected():
    eng = StrategyEngine("test")
    bad = Candle("M1", 0, 60, 1, 2, 0, 1, 10, finalized=False)
    with pytest.raises(ValueError, match="finalized"):
        eng.on_m1(bad)
