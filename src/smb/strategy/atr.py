"""Deterministic Average True Range helper (no external TA libraries).

ATR is computed only from completed candles available at or before the
evaluation point. No future values are used.
"""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle


def true_range(candle: Candle, prev_close: float | None) -> float:
    """True range of ``candle``.

    If ``prev_close`` is None (first candle), TR = high - low.
    """
    high_low = candle.high - candle.low
    if prev_close is None:
        return high_low
    return max(
        high_low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(
    candles: Sequence[Candle],
    period: int,
    *,
    end_index: int | None = None,
) -> float | None:
    """Simple moving average of true range over ``period`` bars.

    Parameters
    ----------
    candles:
        Chronological sequence of completed candles.
    period:
        ATR lookback length.
    end_index:
        Inclusive index of the last candle to use. Defaults to the last
        candle in ``candles``. Only candles at indices
        ``[end_index - period + 1, end_index]`` (and the previous close for
        the first TR) are used.

    Returns
    -------
    float | None
        ATR value, or None if fewer than ``period`` candles are available
        up to ``end_index``.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if not candles:
        return None

    if end_index is None:
        end_index = len(candles) - 1
    if end_index < 0 or end_index >= len(candles):
        return None

    # Need `period` TRs ending at end_index.
    start = end_index - period + 1
    if start < 0:
        return None

    total = 0.0
    for i in range(start, end_index + 1):
        prev_close = candles[i - 1].close if i > 0 else None
        total += true_range(candles[i], prev_close)
    return total / period
