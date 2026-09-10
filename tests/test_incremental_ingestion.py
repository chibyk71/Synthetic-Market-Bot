"""Focused tests for incremental historical ingestion end-cursor resolution."""

from __future__ import annotations

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
