"""Incremental historical ingestion: Deriv history API -> Parquet store.

Page-by-page: each history page is persisted before the next is fetched,
so a multi-month download never holds the full tick set in memory.

Deriv ``ticks_history`` pages are requested newest->oldest (cursor =
``earliest.epoch - 1``). Each page is chronological ascending. After all
pages for an instrument are written, :meth:`ParquetTickStore.reindex_source_order`
assigns dense ``source_order`` in canonical ``(epoch, price)`` order so
dataset validation does not see false non-monotonic breaks at page boundaries.

When ``end`` is omitted (``None``), the initial cursor is chosen from the
store: if ticks already exist, ingestion continues from ``oldest_epoch - 1``;
otherwise it starts from ``\"latest\"``. An explicit ``end`` always wins.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.deriv.client import DerivClient
from smb.deriv.history import MAX_TICKS_PER_REQUEST, HistoryPage, fetch_ticks
from smb.deriv.symbols import load_active_symbols, resolve_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    instrument: str
    symbol: str
    pages_fetched: int
    ticks_written: int


def resolve_ingest_end(
    store: ParquetTickStore,
    instrument: str,
    end: str | int | None,
) -> str | int:
    """Resolve the initial Deriv history cursor for ingestion.

    * Explicit ``end`` (including ``\"latest\"``) is returned unchanged.
    * When ``end`` is ``None`` (CLI omitted ``--end``):
      - existing ticks -> ``oldest_epoch - 1`` so the next run extends backward
      - empty dataset -> ``\"latest\"`` (initial-ingestion behavior)

    The ``- 1`` boundary avoids re-requesting the exact oldest stored tick;
    storage still deduplicates any residual overlap.
    """
    if end is not None:
        return end

    from smb.data.repository import TickRepository

    cov = TickRepository(store).coverage(instrument)
    earliest = cov.get("earliest_epoch")
    if earliest is not None and cov.get("tick_count", 0) > 0:
        cursor = int(earliest) - 1
        logger.info(
            "Incremental ingest for %s: starting from oldest_epoch-1=%s (existing ticks=%s)",
            instrument,
            cursor,
            cov["tick_count"],
        )
        return cursor

    logger.info(
        "Empty dataset for %s: starting history cursor from latest",
        instrument,
    )
    return "latest"


async def iter_history_pages(
    client: DerivClient,
    symbol: str,
    *,
    pages: int = 1,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int = "latest",
) -> AsyncIterator[HistoryPage]:
    """Yield historical pages one at a time, walking backward in time.

    Same cursor strategy as :func:`fetch_ticks_paginated`, but does not
    accumulate pages in a list.
    """
    if pages < 1:
        raise ValueError("pages must be >= 1")
    count_per_page = min(max(1, count_per_page), MAX_TICKS_PER_REQUEST)
    cursor: str | int = end

    for i in range(pages):
        page = await fetch_ticks(
            client,
            symbol,
            count=count_per_page,
            end=cursor,
            start=1,
        )
        yield page
        if page.count == 0 or page.earliest is None:
            logger.info("Empty history page at index %s; stopping", i)
            break
        cursor = page.earliest.epoch - 1


async def ingest_instrument(
    client: DerivClient,
    store: ParquetTickStore,
    *,
    instrument: str,
    display_name: str,
    pages: int = 3,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int | None = None,
    dedupe: bool = True,
) -> IngestResult:
    """Fetch historical pages for one instrument and persist each page.

    ``instrument`` is the semantic config key (e.g. ``volatility_75_1s``).
    ``display_name`` is resolved via ``active_symbols``.

    When ``end`` is ``None``, the initial cursor is chosen automatically:
    oldest stored epoch minus one if data exists, otherwise ``\"latest\"``.
    An explicit ``end`` (including ``\"latest\"``) always overrides.
    """
    symbols = await load_active_symbols(client, detail="full")
    info = resolve_symbol(display_name, symbols)

    resolved_end = resolve_ingest_end(store, instrument, end)

    pages_fetched = 0
    ticks_written = 0

    async for page in iter_history_pages(
        client,
        info.symbol,
        pages=pages,
        count_per_page=count_per_page,
        end=resolved_end,
    ):
        pages_fetched += 1
        # Page ticks are chronological from Deriv; sort defensively so a
        # single page never contributes provisional non-monotonic order.
        stored = sorted(
            (StoredTick.from_tick(instrument, t) for t in page.ticks),
            key=lambda t: (t.epoch, t.price),
        )
        ticks_written += store.write_page(stored, dedupe=dedupe)

    # Pages arrived newest->oldest; provisional source_order follows that
    # write sequence. Reindex so ORDER BY source_order is chronological.
    if pages_fetched > 0:
        store.reindex_source_order(instrument)

    logger.info(
        "Ingested %s (%s): pages=%s written=%s",
        instrument,
        info.symbol,
        pages_fetched,
        ticks_written,
    )
    return IngestResult(
        instrument=instrument,
        symbol=info.symbol,
        pages_fetched=pages_fetched,
        ticks_written=ticks_written,
    )
