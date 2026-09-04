"""Incremental historical ingestion: Deriv history API → Parquet store.

Does not hold the full multi-month dataset in memory. Pages are fetched,
converted, and written batch-by-batch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.deriv.client import DerivClient
from smb.deriv.history import MAX_TICKS_PER_REQUEST, fetch_ticks_paginated, flatten_pages
from smb.deriv.symbols import load_active_symbols, resolve_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    instrument: str
    symbol: str
    pages_fetched: int
    ticks_written: int


async def ingest_instrument(
    client: DerivClient,
    store: ParquetTickStore,
    *,
    instrument: str,
    display_name: str,
    pages: int = 3,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int = "latest",
    dedupe: bool = True,
) -> IngestResult:
    """Fetch historical pages for one instrument and persist them.

    ``instrument`` is the semantic config key (e.g. ``volatility_75_1s``).
    ``display_name`` is resolved via ``active_symbols``.
    """
    symbols = await load_active_symbols(client, detail="full")
    info = resolve_symbol(display_name, symbols)

    pages_list = await fetch_ticks_paginated(
        client,
        info.symbol,
        pages=pages,
        count_per_page=count_per_page,
        end=end,
    )
    ticks = flatten_pages(pages_list)
    stored = (StoredTick.from_tick(instrument, t) for t in ticks)
    written = store.write_ticks(stored, dedupe=dedupe)

    logger.info(
        "Ingested %s (%s): pages=%s written=%s",
        instrument,
        info.symbol,
        len(pages_list),
        written,
    )
    return IngestResult(
        instrument=instrument,
        symbol=info.symbol,
        pages_fetched=len(pages_list),
        ticks_written=written,
    )
