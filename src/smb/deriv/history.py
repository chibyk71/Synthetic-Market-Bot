"""Historical tick retrieval for Deriv public market data.

Uses the official ``ticks_history`` endpoint over the existing
:class:`~smb.deriv.client.DerivClient`. Symbol IDs must be resolved at
runtime via ``active_symbols`` — they are never hard-coded here.

Official docs (verified September 2026):
https://developers.deriv.com/llms/ticks-history.md
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from smb.deriv.client import DerivClient

logger = logging.getLogger(__name__)

# Empirically observed upper bound for a single ticks_history request
# (count values above this are silently truncated by the API).
MAX_TICKS_PER_REQUEST = 1000


@dataclass(frozen=True)
class Tick:
    """Normalized historical tick.

    ``timestamp`` is timezone-aware UTC. ``epoch`` is the raw Unix second
    returned by Deriv. ``raw`` preserves the original price/time pair when
    useful for debugging.
    """

    timestamp: datetime
    price: float
    epoch: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class HistoryPage:
    """One page of historical ticks returned by a single API call."""

    symbol: str
    ticks: tuple[Tick, ...]
    pip_size: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def count(self) -> int:
        return len(self.ticks)

    @property
    def earliest(self) -> Tick | None:
        return self.ticks[0] if self.ticks else None

    @property
    def latest(self) -> Tick | None:
        return self.ticks[-1] if self.ticks else None


def _epoch_to_utc(epoch: int | float) -> datetime:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def parse_history_response(
    response: dict[str, Any],
    *,
    symbol: str,
) -> HistoryPage:
    """Convert a raw ``ticks_history`` response into a :class:`HistoryPage`.

    Raises:
        ValueError: if the response shape is unexpected or arrays mismatch.
    """
    msg_type = response.get("msg_type")
    if msg_type not in ("history", "candles"):
        if "history" not in response:
            raise ValueError(
                f"Unexpected ticks_history response msg_type={msg_type!r}"
            )

    history = response.get("history")
    if history is None:
        return HistoryPage(
            symbol=symbol,
            ticks=(),
            pip_size=_as_int(response.get("pip_size")),
            raw=dict(response),
        )

    if not isinstance(history, dict):
        raise ValueError("history field is not an object")

    prices = history.get("prices")
    times = history.get("times")
    if prices is None or times is None:
        raise ValueError("history missing required prices/times arrays")
    if not isinstance(prices, list) or not isinstance(times, list):
        raise ValueError("history prices/times must be lists")
    if len(prices) != len(times):
        raise ValueError(
            f"history prices/times length mismatch: {len(prices)} vs {len(times)}"
        )

    ticks: list[Tick] = []
    for epoch, price in zip(times, prices, strict=True):
        try:
            ep = int(epoch)
            pr = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid tick pair epoch={epoch!r} price={price!r}"
            ) from exc
        ticks.append(
            Tick(
                timestamp=_epoch_to_utc(ep),
                price=pr,
                epoch=ep,
                raw={"epoch": ep, "price": pr},
            )
        )

    ticks.sort(key=lambda t: t.epoch)

    pip_size = _as_int(response.get("pip_size"))
    return HistoryPage(
        symbol=symbol,
        ticks=tuple(ticks),
        pip_size=pip_size,
        raw=dict(response),
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def fetch_ticks(
    client: DerivClient,
    symbol: str,
    *,
    count: int = 1000,
    end: str | int = "latest",
    start: int | None = 1,
    style: Literal["ticks"] = "ticks",
    adjust_start_time: int | None = None,
) -> HistoryPage:
    """Request a single page of historical ticks.

    Parameters mirror the official ``ticks_history`` request. ``count`` is
    capped at :data:`MAX_TICKS_PER_REQUEST` because the API silently truncates
    larger values (verified empirically).

    ``end`` may be ``\"latest\"`` or an epoch second (int or digit string).
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    effective_count = min(count, MAX_TICKS_PER_REQUEST)
    if effective_count < count:
        logger.debug(
            "Capping requested count %s to API max %s", count, MAX_TICKS_PER_REQUEST
        )

    payload: dict[str, Any] = {
        "ticks_history": symbol,
        "style": style,
        "count": effective_count,
        "end": str(end) if not isinstance(end, str) else end,
    }
    if start is not None:
        payload["start"] = int(start)
    if adjust_start_time is not None:
        payload["adjust_start_time"] = int(adjust_start_time)

    response = await client.request(payload)
    return parse_history_response(response, symbol=symbol)


async def fetch_ticks_paginated(
    client: DerivClient,
    symbol: str,
    *,
    pages: int = 1,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int = "latest",
) -> list[HistoryPage]:
    """Fetch multiple pages of historical ticks, walking backward in time.

    Pagination strategy (verified empirically against the public API):

    * Each request returns at most 1000 ticks in chronological order.
    * To obtain the previous page, set ``end`` to ``earliest_epoch - 1``.
    * Adjacent pages do not overlap; the gap is exactly 1 second for
      1-second synthetic indices.

    Returns pages in reverse-time order (newest page first).
    """
    if pages < 1:
        raise ValueError("pages must be >= 1")
    count_per_page = min(max(1, count_per_page), MAX_TICKS_PER_REQUEST)

    results: list[HistoryPage] = []
    cursor: str | int = end

    for i in range(pages):
        page = await fetch_ticks(
            client,
            symbol,
            count=count_per_page,
            end=cursor,
            start=1,
        )
        results.append(page)
        if page.count == 0 or page.earliest is None:
            logger.info("Empty history page at index %s; stopping pagination", i)
            break
        cursor = page.earliest.epoch - 1

    return results


def flatten_pages(pages: Sequence[HistoryPage]) -> list[Tick]:
    """Merge pages into a single chronological tick list (oldest first).

    Duplicate epochs (should not occur with correct pagination) are dropped,
    keeping the first occurrence.
    """
    seen: set[int] = set()
    merged: list[Tick] = []
    for page in reversed(pages):
        for tick in page.ticks:
            if tick.epoch in seen:
                continue
            seen.add(tick.epoch)
            merged.append(tick)
    merged.sort(key=lambda t: t.epoch)
    return merged


@dataclass(frozen=True)
class TickStats:
    """Summary statistics for a sequence of ticks."""

    count: int
    earliest: datetime | None
    latest: datetime | None
    duration_seconds: float | None
    min_interval: float | None
    max_interval: float | None
    median_interval: float | None
    ticks_per_second: float | None
    min_price: float | None
    max_price: float | None
    price_precision: int | None
    duplicate_epochs: int
    non_monotonic_pairs: int


def compute_tick_stats(ticks: Sequence[Tick]) -> TickStats:
    """Compute descriptive statistics used by the history probe."""
    if not ticks:
        return TickStats(
            count=0,
            earliest=None,
            latest=None,
            duration_seconds=None,
            min_interval=None,
            max_interval=None,
            median_interval=None,
            ticks_per_second=None,
            min_price=None,
            max_price=None,
            price_precision=None,
            duplicate_epochs=0,
            non_monotonic_pairs=0,
        )

    epochs = [t.epoch for t in ticks]
    prices = [t.price for t in ticks]

    unique = set(epochs)
    duplicate_epochs = len(epochs) - len(unique)

    non_monotonic = 0
    intervals: list[float] = []
    for i in range(1, len(epochs)):
        delta = epochs[i] - epochs[i - 1]
        if delta <= 0:
            non_monotonic += 1
        else:
            intervals.append(float(delta))

    median_interval: float | None = None
    if intervals:
        sorted_iv = sorted(intervals)
        mid = len(sorted_iv) // 2
        if len(sorted_iv) % 2 == 0:
            median_interval = (sorted_iv[mid - 1] + sorted_iv[mid]) / 2.0
        else:
            median_interval = sorted_iv[mid]

    duration = float(epochs[-1] - epochs[0]) if len(epochs) > 1 else 0.0
    tps = (len(epochs) - 1) / duration if duration > 0 else None

    precision = 0
    for p in prices:
        s = f"{p:.10f}".rstrip("0")
        if "." in s:
            precision = max(precision, len(s.split(".")[1]))

    return TickStats(
        count=len(ticks),
        earliest=ticks[0].timestamp,
        latest=ticks[-1].timestamp,
        duration_seconds=duration if len(epochs) > 1 else None,
        min_interval=min(intervals) if intervals else None,
        max_interval=max(intervals) if intervals else None,
        median_interval=median_interval,
        ticks_per_second=tps,
        min_price=min(prices),
        max_price=max(prices),
        price_precision=precision,
        duplicate_epochs=duplicate_epochs,
        non_monotonic_pairs=non_monotonic,
    )
