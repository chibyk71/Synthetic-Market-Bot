"""Confirmed swing high / swing low detection with right-side confirmation.

A swing at index ``i`` is only available after candle ``i + X`` has closed.
Strict inequalities are required on both sides.
"""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.strategy.models import SwingPoint


def is_swing_high(candles: Sequence[Candle], i: int, x: int) -> bool:
    """Return True if candle ``i`` is a strict swing high with window ``x``.

    Requires indices ``[i - x, i + x]`` to exist. Does **not** check
    confirmation timing — callers must ensure right-side candles have closed.
    """
    if x < 1:
        raise ValueError("x must be >= 1")
    n = len(candles)
    if i - x < 0 or i + x >= n:
        return False
    hi = candles[i].high
    for j in range(i - x, i):
        if not (hi > candles[j].high):
            return False
    for j in range(i + 1, i + x + 1):
        if not (hi > candles[j].high):
            return False
    return True


def is_swing_low(candles: Sequence[Candle], i: int, x: int) -> bool:
    """Return True if candle ``i`` is a strict swing low with window ``x``."""
    if x < 1:
        raise ValueError("x must be >= 1")
    n = len(candles)
    if i - x < 0 or i + x >= n:
        return False
    lo = candles[i].low
    for j in range(i - x, i):
        if not (lo < candles[j].low):
            return False
    for j in range(i + 1, i + x + 1):
        if not (lo < candles[j].low):
            return False
    return True


def newly_confirmed_swings(
    candles: Sequence[Candle],
    x: int,
    *,
    prev_len: int,
) -> list[SwingPoint]:
    """Detect swings that become confirmed when the latest candle closes.

    When the candle list grows from ``prev_len`` to ``len(candles)``, any
    swing whose right-side confirmation ends exactly at the new last index
    becomes available.

    For a swing at index ``i``, confirmation occurs when index ``i + x``
    closes, i.e. when ``len(candles) - 1 == i + x``.
    """
    n = len(candles)
    if n <= prev_len or x < 1:
        return []

    confirmed: list[SwingPoint] = []
    # Only check the single candidate that just became confirmable:
    # i = (n - 1) - x
    i = (n - 1) - x
    if i < x:
        # Not enough left-side context either
        return []

    # Safety: i must have existed before this confirmation candle
    if i >= n - 1:
        return []

    if is_swing_high(candles, i, x):
        c = candles[i]
        confirmed.append(
            SwingPoint(
                kind="high",
                price=c.high,
                candle_start_epoch=c.start_epoch,
                candle_end_epoch=c.end_epoch,
                index=i,
                confirmed_at_epoch=candles[n - 1].end_epoch,
            )
        )
    if is_swing_low(candles, i, x):
        c = candles[i]
        confirmed.append(
            SwingPoint(
                kind="low",
                price=c.low,
                candle_start_epoch=c.start_epoch,
                candle_end_epoch=c.end_epoch,
                index=i,
                confirmed_at_epoch=candles[n - 1].end_epoch,
            )
        )
    return confirmed
