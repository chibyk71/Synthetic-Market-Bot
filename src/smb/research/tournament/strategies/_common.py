"""Shared helpers for tournament strategies (no lookahead)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.strategy.atr import atr as compute_atr


def completed_atr(candles: Sequence[Candle], end_index: int, period: int = 14) -> float | None:
    """ATR using only candles[0..end_index] inclusive."""
    if end_index < 0 or end_index >= len(candles):
        return None
    return compute_atr(candles, period, end_index=end_index)


def bar_return(candles: Sequence[Candle], end_index: int, lookback: int) -> float | None:
    """Close-to-close return over ``lookback`` bars ending at end_index."""
    if lookback < 1 or end_index < lookback or end_index >= len(candles):
        return None
    start_close = candles[end_index - lookback].close
    end_close = candles[end_index].close
    if start_close == 0.0:
        return None
    return (end_close - start_close) / start_close


def rolling_mean_close(candles: Sequence[Candle], end_index: int, lookback: int) -> float | None:
    if lookback < 1 or end_index < lookback - 1 or end_index >= len(candles):
        return None
    start = end_index - lookback + 1
    total = sum(candles[i].close for i in range(start, end_index + 1))
    return total / lookback


def range_high_low(
    candles: Sequence[Candle], end_index: int, lookback: int
) -> tuple[float, float] | None:
    """High/low of candles in [end_index - lookback + 1, end_index] inclusive."""
    if lookback < 1 or end_index < lookback - 1 or end_index >= len(candles):
        return None
    start = end_index - lookback + 1
    hi = max(candles[i].high for i in range(start, end_index + 1))
    lo = min(candles[i].low for i in range(start, end_index + 1))
    return hi, lo
