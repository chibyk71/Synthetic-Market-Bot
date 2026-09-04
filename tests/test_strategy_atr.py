"""Deterministic ATR helper tests."""

from __future__ import annotations

from smb.market.candles import Candle
from smb.strategy.atr import atr, true_range


def _c(start: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle("M1", start, start + 60, o, h, low, c, 60, finalized=True)


def test_true_range_first_candle():
    c = _c(0, 10, 15, 8, 12)
    assert true_range(c, None) == 7.0


def test_true_range_with_gap():
    c = _c(60, 20, 22, 18, 21)
    assert true_range(c, 12.0) == 10.0


def test_atr_insufficient_history():
    candles = [_c(0, 10, 12, 9, 11), _c(60, 11, 13, 10, 12)]
    assert atr(candles, period=5) is None


def test_atr_exact_period():
    candles = [
        _c(0, 10, 12, 9, 11),
        _c(60, 11, 14, 10, 13),
        _c(120, 13, 15, 12, 14),
    ]
    val = atr(candles, period=3)
    assert val is not None
    assert abs(val - 10 / 3) < 1e-9


def test_atr_end_index_respects_history():
    candles = [
        _c(0, 10, 12, 9, 11),
        _c(60, 11, 14, 10, 13),
        _c(120, 13, 15, 12, 14),
        _c(180, 14, 20, 13, 19),
    ]
    early = atr(candles, period=2, end_index=1)
    late = atr(candles, period=2, end_index=3)
    assert early is not None and late is not None
    assert late != early
