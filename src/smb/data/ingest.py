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

Optional ``start_epoch`` stops backward pagination once the page's earliest
tick is at or below that bound (inclusive stop). Overlapping boundary ticks
are deduplicated by the store. Ticks strictly older than ``start_epoch`` on a
boundary-crossing page are not persisted.

Each run can write an :class:`IngestManifest` JSON under the data root for
audit/resume provenance. Transient API failures are retried with exponential
backoff (bounded).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.deriv.client import DerivAPIError, DerivClient
from smb.deriv.history import MAX_TICKS_PER_REQUEST, HistoryPage, fetch_ticks
from smb.deriv.symbols import load_active_symbols, resolve_symbol

logger = logging.getLogger(__name__)

# Transient failures worth retrying (network / rate / timeout).
_RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
    DerivAPIError,
)


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingest_instrument invocation."""

    instrument: str
    symbol: str
    pages_fetched: int
    ticks_written: int
    ticks_received: int = 0
    duplicates_skipped: int = 0
    retries: int = 0
    failed_chunks: int = 0
    resolved_end: str | int | None = None
    start_epoch_bound: int | None = None
    coverage_before: dict[str, Any] = field(default_factory=dict)
    coverage_after: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    started_at_utc: str | None = None
    completed_at_utc: str | None = None
    manifest_path: str | None = None


@dataclass(frozen=True)
class IngestManifest:
    """Provenance record for one ingestion campaign (one instrument)."""

    instrument: str
    symbol: str
    requested_end: str | int | None
    resolved_end: str | int | None
    start_epoch_bound: int | None
    pages_requested: int
    pages_fetched: int
    pages_failed: int
    count_per_page: int
    ticks_received: int
    ticks_persisted: int
    duplicates_skipped: int
    retries: int
    coverage_before: dict[str, Any]
    coverage_after: dict[str, Any]
    actual_first_epoch: int | None
    actual_last_epoch: int | None
    started_at_utc: str
    completed_at_utc: str
    errors: tuple[str, ...]
    source: str = "deriv_ticks_history"
    api_note: str = (
        "Public ticks_history via wss://api.derivws.com; "
        "pages walk newest\u2192oldest with end=earliest-1."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


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


def _coverage_snapshot(store: ParquetTickStore, instrument: str) -> dict[str, Any]:
    from smb.data.repository import TickRepository

    return dict(TickRepository(store).coverage(instrument))


async def _fetch_ticks_with_retry(
    client: DerivClient,
    symbol: str,
    *,
    count: int,
    end: str | int,
    max_retries: int,
    base_delay: float,
) -> tuple[HistoryPage, int]:
    """Fetch one page with bounded exponential backoff.

    Returns ``(page, retry_count)``. Non-retryable errors are re-raised after
    exhausting retries only for retryable exceptions.
    """
    retries = 0
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            page = await fetch_ticks(
                client,
                symbol,
                count=count,
                end=end,
                start=1,
            )
            return page, retries
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = base_delay * (2**attempt)
            logger.warning(
                "Transient history fetch failure (attempt %s/%s) end=%s: %s; "
                "retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                end,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            retries += 1
    assert last_exc is not None
    raise last_exc


async def iter_history_pages(
    client: DerivClient,
    symbol: str,
    *,
    pages: int = 1,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int = "latest",
    start_epoch: int | None = None,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
) -> AsyncIterator[tuple[HistoryPage, int]]:
    """Yield ``(page, retries_used)`` walking backward in time.

    Stops early when:
    * empty page / no earliest tick
    * ``start_epoch`` bound is reached (page earliest <= start_epoch)
    * ``pages`` limit exhausted
    """
    if pages < 1:
        raise ValueError("pages must be >= 1")
    count_per_page = min(max(1, count_per_page), MAX_TICKS_PER_REQUEST)
    cursor: str | int = end

    for i in range(pages):
        page, retries = await _fetch_ticks_with_retry(
            client,
            symbol,
            count=count_per_page,
            end=cursor,
            max_retries=max_retries,
            base_delay=retry_base_delay,
        )
        yield page, retries
        if page.count == 0 or page.earliest is None:
            logger.info("Empty history page at index %s; stopping", i)
            break
        if start_epoch is not None and page.earliest.epoch <= start_epoch:
            logger.info(
                "Reached start_epoch bound %s at page index %s (earliest=%s); stopping",
                start_epoch,
                i,
                page.earliest.epoch,
            )
            break
        cursor = page.earliest.epoch - 1


def _write_manifest(path: Path, manifest: IngestManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


async def ingest_instrument(
    client: DerivClient,
    store: ParquetTickStore,
    *,
    instrument: str,
    display_name: str,
    pages: int = 3,
    count_per_page: int = MAX_TICKS_PER_REQUEST,
    end: str | int | None = None,
    start_epoch: int | None = None,
    dedupe: bool = True,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
    write_manifest: bool = True,
    manifest_dir: str | Path | None = None,
) -> IngestResult:
    """Fetch historical pages for one instrument and persist each page.

    ``instrument`` is the semantic config key (e.g. ``volatility_75_1s``).
    ``display_name`` is resolved via ``active_symbols``.

    When ``end`` is ``None``, the initial cursor is chosen automatically:
    oldest stored epoch minus one if data exists, otherwise ``\"latest\"``.
    An explicit ``end`` (including ``\"latest\"``) always overrides.

    ``start_epoch`` optionally stops backward walk once coverage reaches that
    inclusive lower bound. Ticks with ``epoch < start_epoch`` are not persisted.
    """
    started = _utc_now()
    t0 = time.monotonic()

    symbols = await load_active_symbols(client, detail="full")
    info = resolve_symbol(display_name, symbols)

    coverage_before = _coverage_snapshot(store, instrument)
    resolved_end = resolve_ingest_end(store, instrument, end)

    pages_fetched = 0
    ticks_written = 0
    ticks_received = 0
    retries_total = 0
    failed_chunks = 0
    errors: list[str] = []

    try:
        async for page, retries in iter_history_pages(
            client,
            info.symbol,
            pages=pages,
            count_per_page=count_per_page,
            end=resolved_end,
            start_epoch=start_epoch,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        ):
            pages_fetched += 1
            retries_total += retries
            ticks_received += page.count
            # Inclusive lower bound: drop ticks older than start_epoch even when
            # they arrive on the final (boundary-crossing) page.
            page_ticks = (
                StoredTick.from_tick(instrument, t)
                for t in page.ticks
                if start_epoch is None or t.epoch >= start_epoch
            )
            # Page ticks are chronological from Deriv; sort defensively so a
            # single page never contributes provisional non-monotonic order.
            stored = sorted(
                page_ticks,
                key=lambda t: (t.epoch, t.price),
            )
            written = store.write_page(stored, dedupe=dedupe)
            ticks_written += written
    except _RETRYABLE_EXCEPTIONS as exc:
        failed_chunks += 1
        errors.append(str(exc))
        logger.error("Ingest aborted for %s after partial progress: %s", instrument, exc)

    # Pages arrived newest->oldest; provisional source_order follows that
    # write sequence. Reindex so ORDER BY source_order is chronological.
    if pages_fetched > 0:
        store.reindex_source_order(instrument)

    coverage_after = _coverage_snapshot(store, instrument)
    duplicates_skipped = max(0, ticks_received - ticks_written)
    completed = _utc_now()

    manifest_path: str | None = None
    if write_manifest:
        mdir = Path(manifest_dir) if manifest_dir else store.root / "ingest_manifests"
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        mpath = mdir / f"{instrument}_{stamp}.json"
        manifest = IngestManifest(
            instrument=instrument,
            symbol=info.symbol,
            requested_end=end,
            resolved_end=resolved_end,
            start_epoch_bound=start_epoch,
            pages_requested=pages,
            pages_fetched=pages_fetched,
            pages_failed=failed_chunks,
            count_per_page=min(max(1, count_per_page), MAX_TICKS_PER_REQUEST),
            ticks_received=ticks_received,
            ticks_persisted=ticks_written,
            duplicates_skipped=duplicates_skipped,
            retries=retries_total,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
            actual_first_epoch=coverage_after.get("earliest_epoch"),
            actual_last_epoch=coverage_after.get("latest_epoch"),
            started_at_utc=started,
            completed_at_utc=completed,
            errors=tuple(errors),
        )
        _write_manifest(mpath, manifest)
        manifest_path = str(mpath)

    elapsed = time.monotonic() - t0
    logger.info(
        "Ingested %s (%s): pages=%s written=%s received=%s retries=%s elapsed=%.1fs",
        instrument,
        info.symbol,
        pages_fetched,
        ticks_written,
        ticks_received,
        retries_total,
        elapsed,
    )
    return IngestResult(
        instrument=instrument,
        symbol=info.symbol,
        pages_fetched=pages_fetched,
        ticks_written=ticks_written,
        ticks_received=ticks_received,
        duplicates_skipped=duplicates_skipped,
        retries=retries_total,
        failed_chunks=failed_chunks,
        resolved_end=resolved_end,
        start_epoch_bound=start_epoch,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        errors=tuple(errors),
        started_at_utc=started,
        completed_at_utc=completed,
        manifest_path=manifest_path,
    )
