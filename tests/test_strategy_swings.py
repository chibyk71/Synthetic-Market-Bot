"""Swing detection and right-side confirmation tests."""

from __future__ import annotations

from smb.market.candles import Candle
from smb.strategy.swings import is_swing_high, is_swing_low, newly_confirmed_swings


def _c(start: int, high: float, low: float, close: float | None = None) -> Candle:
    o = (high + low) / 2 if close is None else close
    cl = close if close is not None else o
    return Candle(
        timeframe="M1",
        start_epoch=start,
        end_epoch=start + 60,
        open=o,
        high=high,
        low=low,
        close=cl,
        tick_count=60,
        finalized=True,
    )


def test_swing_high_strict():
    candles = [
        _c(0, 10, 9),
        _c(60, 11, 9),
        _c(120, 15, 10),
        _c(180, 12, 10),
        _c(240, 13, 11),
    ]
    assert is_swing_high(candles, 2, 2) is True
    assert is_swing_low(candles, 2, 2) is False


def test_swing_low_strict():
    candles = [
        _c(0, 12, 10),
        _c(60, 11, 9),
        _c(120, 10, 5),
        _c(180, 11, 8),
        _c(240, 12, 9),
    ]
    assert is_swing_low(candles, 2, 2) is True
    assert is_swing_high(candles, 2, 2) is False


def test_swing_requires_strict_inequality():
    candles = [
        _c(0, 10, 9),
        _c(60, 11, 9),
        _c(120, 15, 10),
        _c(180, 15, 10),
        _c(240, 13, 11),
    ]
    assert is_swing_high(candles, 2, 2) is False


def test_insufficient_left_context():
    candles = [
        _c(0, 15, 10),
        _c(60, 12, 10),
        _c(120, 13, 11),
    ]
    assert is_swing_high(candles, 0, 2) is False


def test_insufficient_right_confirmation():
    candles = [
        _c(0, 10, 9),
        _c(60, 11, 9),
        _c(120, 15, 10),
        _c(180, 12, 10),
    ]
    assert is_swing_high(candles, 2, 2) is False


def test_confirmation_only_after_x_candles():
    x = 2
    base = [
        _c(0, 10, 9),
        _c(60, 11, 9),
        _c(120, 15, 10),
        _c(180, 12, 10),
    ]
    assert newly_confirmed_swings(base, x, prev_len=3) == []
    full = base + [_c(240, 13, 11)]
    confirmed = newly_confirmed_swings(full, x, prev_len=4)
    assert len(confirmed) == 1
    assert confirmed[0].kind == "high"
    assert confirmed[0].price == 15.0
    assert confirmed[0].index == 2
    assert confirmed[0].confirmed_at_epoch == 300


def test_no_confirmation_on_partial_growth():
    candles = [_c(i * 60, 10 + i, 9) for i in range(3)]
    assert newly_confirmed_swings(candles, 2, prev_len=2) == []


def test_swing_x_configurable():
    candles = [
        _c(0, 10, 9),
        _c(60, 15, 10),
        _c(120, 12, 10),
    ]
    assert is_swing_high(candles, 1, 1) is True
    assert is_swing_high(candles, 1, 2) is False
