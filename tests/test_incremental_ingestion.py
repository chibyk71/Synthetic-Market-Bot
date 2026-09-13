"""Focused tests for incremental historical ingestion end-cursor resolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from smb.data.ingest import ingest_instrument, resolve_ingest_end
from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import HistoryPage, Tick


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "dataset")


def _patch_symbol_and_fetch(mp, pages_or_callable):
    """Shared monkeypatch for symbol resolution + fetch_ticks."""
    from smb.data import ingest as ingest_mod

    fake_info = MagicMock()
    fake_info.symbol = "1HZ75V"

    async def fake_load(client, detail="full"):
        return [fake_info]

    def fake_resolve(name, symbols):
        return fake_info

    if callable(pages_or_callable):
        fake_fetch = pages_or_callable
    else:
        queue = list(pages_or_callable)

        async def fake_fetch(client, symbol, *, count, end, start=1):
            if not queue:
                return HistoryPage(symbol="1HZ75V", ticks=(), pip_size=0.01)
            return queue.pop(0)

    mp.setattr(ingest_mod, "load_active_symbols", fake_load)
    mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
    mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)
    return fake_info


# ---------------------------------------------------------------------------
# resolve_ingest_end unit tests
# ---------------------------------------------------------------------------


def test_resolve_end_empty_dataset_uses_latest(store: ParquetTickStore):
    """Test A — empty dataset → initial cursor = latest."""
    assert resolve_ingest_end(store, "volatility_75_1s", None) == "latest"


def test_resolve_end_existing_dataset_uses_oldest_minus_one(store: ParquetTickStore):
    """Test B — existing dataset → oldest_epoch - 1."""
    store.write_ticks(
        [
            _st("volatility_75_1s", 1000, 10.0),
            _st("volatility_75_1s", 1001, 10.1),
            _st("volatility_75_1s", 2000, 11.0),
        ]
    )
    assert resolve_ingest_end(store, "volatility_75_1s", None) == 999


def test_resolve_end_explicit_overrides_automatic(store: ParquetTickStore):
    """Test C — explicit --end overrides automatic oldest-tick detection."""
    store.write_ticks([_st("volatility_75_1s", 1000, 10.0)])
    assert resolve_ingest_end(store, "volatility_75_1s", "latest") == "latest"
    assert resolve_ingest_end(store, "volatility_75_1s", 1757000000) == 1757000000
    assert resolve_ingest_end(store, "volatility_75_1s", "1757000000") == "1757000000"


# ---------------------------------------------------------------------------
# ingest_instrument integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_dataset_first_request_uses_latest(store: ParquetTickStore):
    """Test A (integration) — empty store requests end=latest."""
    ends: list[str | int] = []
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(2000, 10.0), _tick(2001, 10.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="Volatility 75 (1s) Index",
            pages=1,
            end=None,
        )

    assert ends == ["latest"]
    assert result.ticks_written == 2


@pytest.mark.asyncio
async def test_existing_dataset_first_request_uses_oldest_minus_one(
    store: ParquetTickStore,
):
    """Test B (integration) — existing ticks → first request end=X-1."""
    store.write_ticks(
        [
            _st("volatility_75_1s", 1500, 10.0),
            _st("volatility_75_1s", 1600, 10.5),
        ]
    )
    ends: list[str | int] = []
    older = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(1400, 9.0), _tick(1401, 9.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        return older

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="Volatility 75 (1s) Index",
            pages=1,
            end=None,
        )

    assert ends == [1499]
    assert result.ticks_written == 2
    cov = TickRepository(store).coverage("volatility_75_1s")
    assert cov["tick_count"] == 4
    assert cov["earliest_epoch"] == 1400
    assert cov["latest_epoch"] == 1600


@pytest.mark.asyncio
async def test_explicit_end_overrides_automatic(store: ParquetTickStore):
    """Test C (integration) — explicit end is used even when data exists."""
    store.write_ticks([_st("volatility_75_1s", 1500, 10.0)])
    ends: list[str | int] = []
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(100, 1.0),),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="X",
            pages=1,
            end="latest",
        )
        await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="X",
            pages=1,
            end=12345,
        )

    assert ends == ["latest", 12345]


@pytest.mark.asyncio
async def test_repeated_ingestion_extends_backward(store: ParquetTickStore):
    """Test D — second run continues from oldest-1, no duplicates, valid order."""
    newer = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(200, 2.0), _tick(201, 2.1), _tick(202, 2.2)),
        pip_size=0.01,
    )
    older = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(100, 1.0), _tick(101, 1.1), _tick(102, 1.2)),
        pip_size=0.01,
    )
    ends: list[str | int] = []

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        # First call (empty → latest) returns newer; second (oldest-1) returns older.
        if end == "latest":
            return newer
        return older

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        r1 = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end=None,
        )
        r2 = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end=None,
        )

    assert ends == ["latest", 199]
    assert r1.ticks_written == 3
    assert r2.ticks_written == 3

    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 6
    assert cov["duplicate_count"] == 0
    assert cov["non_monotonic_count"] == 0
    assert cov["earliest_epoch"] == 100
    assert cov["latest_epoch"] == 202

    import duckdb

    pattern = str(store.ticks_dir / "instrument=vol" / "**" / "*.parquet")
    con = duckdb.connect()
    epochs = [
        r[0]
        for r in con.execute(
            """
            SELECT epoch FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
            ORDER BY source_order ASC
            """,
            [pattern],
        ).fetchall()
    ]
    con.close()
    assert epochs == [100, 101, 102, 200, 201, 202]


@pytest.mark.asyncio
async def test_boundary_overlap_is_deduplicated(store: ParquetTickStore):
    """Test E — overlapping boundary ticks are deduplicated."""
    # Existing: epochs 100, 101, 102
    store.write_ticks(
        [
            _st("vol", 100, 1.0),
            _st("vol", 101, 1.1),
            _st("vol", 102, 1.2),
        ]
    )
    store.reindex_source_order("vol")

    # Overlapping page includes 100–102 plus older 98–99
    overlap = HistoryPage(
        symbol="1HZ75V",
        ticks=(
            _tick(98, 0.8),
            _tick(99, 0.9),
            _tick(100, 1.0),
            _tick(101, 1.1),
            _tick(102, 1.2),
        ),
        pip_size=0.01,
    )
    ends: list[str | int] = []

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        return overlap

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end=None,
        )

    assert ends == [99]  # oldest was 100 → 100-1
    # Only genuinely new ticks written
    assert result.ticks_written == 2

    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 5
    assert cov["duplicate_count"] == 0
    assert cov["non_monotonic_count"] == 0
    assert cov["earliest_epoch"] == 98


@pytest.mark.asyncio
async def test_pagination_continues_with_earliest_minus_one(store: ParquetTickStore):
    """Test F — subsequent pages use earliest_epoch - 1."""
    page1 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(300, 3.0), _tick(301, 3.1)),
        pip_size=0.01,
    )
    page2 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(200, 2.0), _tick(201, 2.1)),
        pip_size=0.01,
    )
    ends: list[str | int] = []
    pages = [page1, page2]

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        return pages[len(ends) - 1]

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=2,
            end=None,
        )

    assert ends[0] == "latest"
    assert ends[1] == 299  # page1.earliest.epoch - 1


@pytest.mark.asyncio
async def test_source_order_chronological_after_incremental(store: ParquetTickStore):
    """Test G — final source_order is chronological after incremental ingest."""
    newer = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(500, 5.0), _tick(501, 5.1)),
        pip_size=0.01,
    )
    older = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(400, 4.0), _tick(401, 4.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        if end == "latest":
            return newer
        return older

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        await ingest_instrument(
            client, store, instrument="vol", display_name="X", pages=1, end=None
        )
        await ingest_instrument(
            client, store, instrument="vol", display_name="X", pages=1, end=None
        )

    import duckdb

    pattern = str(store.ticks_dir / "instrument=vol" / "**" / "*.parquet")
    con = duckdb.connect()
    rows = con.execute(
        """
        SELECT epoch, source_order
        FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
        ORDER BY source_order ASC
        """,
        [pattern],
    ).fetchall()
    con.close()

    epochs = [r[0] for r in rows]
    orders = [r[1] for r in rows]
    assert epochs == [400, 401, 500, 501]
    assert orders == list(range(len(orders)))  # dense 0..n-1 or 1..n

    cov = TickRepository(store).coverage("vol")
    assert cov["non_monotonic_count"] == 0
    assert cov["duplicate_count"] == 0


# ---------------------------------------------------------------------------
# Milestone 5C — start bound, idempotency, manifest, retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_epoch_stops_backward_walk(store: ParquetTickStore):
    """Pagination stops once page earliest <= start_epoch."""
    page1 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(500, 5.0), _tick(501, 5.1)),
        pip_size=0.01,
    )
    page2 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(400, 4.0), _tick(401, 4.1)),
        pip_size=0.01,
    )
    page3 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(300, 3.0), _tick(301, 3.1)),
        pip_size=0.01,
    )
    ends: list[str | int] = []
    queue = [page1, page2, page3]

    async def fake_fetch(client, symbol, *, count, end, start=1):
        ends.append(end)
        if not queue:
            return HistoryPage(symbol="1HZ75V", ticks=(), pip_size=0.01)
        return queue.pop(0)

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=10,
            end="latest",
            start_epoch=400,
            write_manifest=False,
        )

    # page1 (500s) then page2 (400s) hits bound → stop; page3 not requested
    assert len(ends) == 2
    assert result.pages_fetched == 2
    cov = TickRepository(store).coverage("vol")
    assert cov["earliest_epoch"] == 400
    assert cov["latest_epoch"] == 501
    assert cov["tick_count"] == 4


@pytest.mark.asyncio
async def test_start_epoch_filters_boundary_crossing_page(store: ParquetTickStore):
    """Boundary-crossing page: only ticks with epoch >= start_epoch are stored."""
    # Single page mixes ticks below and at/above the inclusive lower bound.
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(999, 9.9), _tick(1000, 10.0), _tick(1001, 10.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            start_epoch=1000,
            write_manifest=False,
        )

    assert result.pages_fetched == 1
    assert result.ticks_received == 3
    assert result.ticks_written == 2
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 2
    assert cov["earliest_epoch"] == 1000
    assert cov["latest_epoch"] == 1001
    assert cov["duplicate_count"] == 0

    epochs = [t.epoch for t in TickRepository(store).get_ticks("vol")]
    assert epochs == [1000, 1001]


@pytest.mark.asyncio
async def test_idempotent_double_ingest(store: ParquetTickStore):
    """Running the same ingestion twice does not duplicate ticks."""
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(1000, 10.0), _tick(1001, 10.1), _tick(1002, 10.2)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        r1 = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            write_manifest=False,
        )
        r2 = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            write_manifest=False,
        )

    assert r1.ticks_written == 3
    assert r2.ticks_written == 0  # all duplicates
    assert r2.duplicates_skipped == 3
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 3
    assert cov["duplicate_count"] == 0


@pytest.mark.asyncio
async def test_manifest_written_with_provenance(store: ParquetTickStore, tmp_path: Path):
    """Manifest records requested range, coverage before/after, counts."""
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(2000, 20.0), _tick(2001, 20.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            write_manifest=True,
            manifest_dir=tmp_path / "manifests",
        )

    assert result.manifest_path is not None
    path = Path(result.manifest_path)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["instrument"] == "vol"
    assert payload["symbol"] == "1HZ75V"
    assert payload["pages_fetched"] == 1
    assert payload["ticks_received"] == 2
    assert payload["ticks_persisted"] == 2
    assert payload["coverage_after"]["tick_count"] == 2
    assert payload["actual_first_epoch"] == 2000
    assert payload["actual_last_epoch"] == 2001
    assert payload["source"] == "deriv_ticks_history"


@pytest.mark.asyncio
async def test_retry_on_transient_then_success(store: ParquetTickStore):
    """Transient failure is retried; successful retry persists ticks."""
    from smb.deriv.client import DerivAPIError

    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(50, 0.5), _tick(51, 0.6)),
        pip_size=0.01,
    )
    calls = {"n": 0}

    async def flaky_fetch(client, symbol, *, count, end, start=1):
        calls["n"] += 1
        if calls["n"] == 1:
            raise DerivAPIError("rate limited", code="RateLimit")
        return page

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, flaky_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            max_retries=2,
            retry_base_delay=0.01,
            write_manifest=False,
        )

    assert calls["n"] == 2
    assert result.retries == 1
    assert result.ticks_written == 2
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 2


@pytest.mark.asyncio
async def test_partial_failure_preserves_prior_pages(store: ParquetTickStore):
    """Page 1 succeeds; page 2 fails permanently — page 1 data remains."""
    from smb.deriv.client import DerivAPIError

    page1 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(800, 8.0), _tick(801, 8.1)),
        pip_size=0.01,
    )
    calls = {"n": 0}

    async def fail_second(client, symbol, *, count, end, start=1):
        calls["n"] += 1
        if calls["n"] == 1:
            return page1
        raise DerivAPIError("server error", code="InternalError")

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fail_second)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=3,
            end="latest",
            max_retries=0,
            write_manifest=False,
        )

    assert result.pages_fetched == 1
    assert result.failed_chunks == 1
    assert result.errors
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 2
    assert cov["earliest_epoch"] == 800
    assert cov["duplicate_count"] == 0


@pytest.mark.asyncio
async def test_forward_extension_via_explicit_end_latest(store: ParquetTickStore):
    """Existing older data + end=latest adds newer ticks without duplicates."""
    store.write_ticks([_st("vol", 100, 1.0), _st("vol", 101, 1.1)])
    store.reindex_source_order("vol")

    newer = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(100, 1.0), _tick(101, 1.1), _tick(200, 2.0), _tick(201, 2.1)),
        pip_size=0.01,
    )

    async def fake_fetch(client, symbol, *, count, end, start=1):
        return newer

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        _patch_symbol_and_fetch(mp, fake_fetch)
        result = await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="X",
            pages=1,
            end="latest",
            write_manifest=False,
        )

    assert result.ticks_written == 2
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 4
    assert cov["earliest_epoch"] == 100
    assert cov["latest_epoch"] == 201
    assert cov["duplicate_count"] == 0


def test_ingest_result_and_manifest_types():
    from smb.data.ingest import IngestManifest, IngestResult

    r = IngestResult(instrument="x", symbol="Y", pages_fetched=0, ticks_written=0)
    assert r.duplicates_skipped == 0
    m = IngestManifest(
        instrument="x",
        symbol="Y",
        requested_end=None,
        resolved_end="latest",
        start_epoch_bound=None,
        pages_requested=1,
        pages_fetched=0,
        pages_failed=0,
        count_per_page=1000,
        ticks_received=0,
        ticks_persisted=0,
        duplicates_skipped=0,
        retries=0,
        coverage_before={},
        coverage_after={},
        actual_first_epoch=None,
        actual_last_epoch=None,
        started_at_utc="t0",
        completed_at_utc="t1",
        errors=(),
    )
    d = m.to_dict()
    assert d["source"] == "deriv_ticks_history"
