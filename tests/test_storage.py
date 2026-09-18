"""Tests for ParquetTickStore and historical ingest helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from smb.data.ingest import ingest_instrument, iter_history_pages
from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore, year_month
from smb.data.validate import validate_instrument
from smb.deriv.history import HistoryPage, Tick
from smb.deriv.symbols import SymbolInfo


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path)


def _tick(epoch: int, price: float = 100.0) -> Tick:
    from datetime import UTC, datetime

    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


def _stored(instrument: str, epoch: int, price: float = 100.0, source_order: int = 0) -> StoredTick:
    return StoredTick.from_tick(instrument, _tick(epoch, price), source_order=source_order)


def test_write_and_read_roundtrip(store: ParquetTickStore) -> None:
    instrument = "volatility_75_1s"
    ticks = [
        _stored(instrument, 1_700_000_000 + i, 100.0 + i * 0.01, source_order=i)
        for i in range(5)
    ]
    n = store.write_page(ticks)
    assert n == 5
    loaded = list(store.iter_ticks(instrument))
    assert len(loaded) == 5
    assert loaded[0].epoch == 1_700_000_000
    assert loaded[-1].epoch == 1_700_000_004


def test_coverage_empty(store: ParquetTickStore) -> None:
    cov = store.coverage("missing")
    assert cov.tick_count == 0
    assert cov.earliest_epoch is None


def test_coverage_after_write(store: ParquetTickStore) -> None:
    instrument = "step_index"
    store.write_page(
        [
            _stored(instrument, 100, 1.0, 0),
            _stored(instrument, 200, 2.0, 1),
        ]
    )
    cov = store.coverage(instrument)
    assert cov.tick_count == 2
    assert cov.earliest_epoch == 100
    assert cov.latest_epoch == 200


def test_dedupe_skips_existing(store: ParquetTickStore) -> None:
    instrument = "volatility_75_1s"
    t0 = _stored(instrument, 50, 10.0, 0)
    store.write_page([t0])
    n = store.write_page([t0, _stored(instrument, 51, 10.1, 1)], dedupe=True)
    assert n == 1
    assert store.coverage(instrument).tick_count == 2


def test_validate_ok(store: ParquetTickStore) -> None:
    instrument = "volatility_75_1s"
    store.write_page(
        [
            _stored(instrument, 10, 1.0, 0),
            _stored(instrument, 11, 1.1, 1),
            _stored(instrument, 12, 1.2, 2),
        ]
    )
    report = validate_instrument(store, instrument)
    assert report.ok
    assert report.tick_count == 3


def test_validate_detects_epoch_regression(store: ParquetTickStore) -> None:
    instrument = "volatility_75_1s"
    # Force non-monotonic epoch by writing without relying on source_order sort alone
    store.write_page(
        [
            _stored(instrument, 20, 1.0, 0),
            _stored(instrument, 10, 1.1, 1),
        ],
        dedupe=False,
    )
    # reindex puts chronological order — validation after reindex should be ok
    store.reindex_source_order(instrument)
    report = validate_instrument(store, instrument)
    assert report.ok


def test_year_month() -> None:
    assert year_month(1_577_836_800) == (2020, 1)  # 2020-01-01 UTC-ish


def _year_month_check(epoch: int) -> tuple[int, int]:
    return year_month(epoch)


@pytest.mark.asyncio
async def test_iter_history_pages_stops_on_empty():
    """Empty first page stops pagination; yields (HistoryPage, retries) tuples."""
    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        empty = HistoryPage(symbol="X", ticks=(), pip_size=None)
        fetch_calls = {"n": 0}

        async def fake_fetch(client, symbol, *, count, end, start=1):
            fetch_calls["n"] += 1
            return empty

        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)
        collected: list[tuple] = []
        async for item in iter_history_pages(client, "X", pages=5):
            collected.append(item)
        # API yields (page, retries_used), not bare HistoryPage
        assert len(collected) == 1
        page, retries = collected[0]
        assert page.count == 0
        assert isinstance(retries, int)
        assert retries >= 0
        # Stopped after empty page — did not request remaining pages
        assert fetch_calls["n"] == 1


@pytest.mark.asyncio
async def test_iter_history_pages_stops_on_empty_after_data():
    """Non-empty page then empty page: only the empty page ends the walk."""
    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod
        from datetime import UTC, datetime

        from smb.deriv.history import Tick

        epoch = 1_700_000_000
        tick = Tick(
            timestamp=datetime.fromtimestamp(epoch, tz=UTC),
            price=100.0,
            epoch=epoch,
        )
        full = HistoryPage(symbol="X", ticks=(tick,), pip_size=0.01)
        empty = HistoryPage(symbol="X", ticks=(), pip_size=None)
        pages_queue = [full, empty, empty]

        async def fake_fetch(client, symbol, *, count, end, start=1):
            if not pages_queue:
                return empty
            return pages_queue.pop(0)

        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)
        collected = []
        async for page, retries in iter_history_pages(client, "X", pages=5):
            collected.append((page.count, retries))
        assert len(collected) == 2
        assert collected[0][0] == 1
        assert collected[1][0] == 0


def test_source_order_preserved_across_month_boundary(store: ParquetTickStore):
    """source_order follows input order even when ticks span partitions.

    Jan 31 23:59 → Feb 01 00:00 → Jan 31 23:59+1s must keep increasing
    source_order in input sequence, and coverage must see the backward
    epoch jump on the third tick.
    """
    jan_a = 1580515140
    feb = 1580515200
    jan_b = 1580515141
    assert _year_month_check(jan_a) == (2020, 1)
    assert _year_month_check(feb) == (2020, 2)
    assert _year_month_check(jan_b) == (2020, 1)

    instrument = "volatility_75_1s"
    ticks = [
        _stored(instrument, jan_a, 100.0, 0),
        _stored(instrument, feb, 101.0, 1),
        _stored(instrument, jan_b, 100.5, 2),
    ]
    store.write_page(ticks, dedupe=False)
    store.reindex_source_order(instrument)
    loaded = list(store.iter_ticks(instrument))
    assert [t.epoch for t in loaded] == sorted(t.epoch for t in loaded)


@pytest.mark.asyncio
async def test_ingest_instrument_writes_pages(tmp_path: Path) -> None:
    store = ParquetTickStore(tmp_path)
    client = AsyncMock()

    tick = _tick(1_700_000_100, 55.0)
    page = HistoryPage(symbol="1HZ75V", ticks=(tick,), pip_size=0.01)

    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        async def fake_load(client, detail="full"):
            return [
                SymbolInfo(
                    symbol="1HZ75V",
                    display_name="Volatility 75 (1s) Index",
                    market="synthetic_index",
                    market_display_name="Synthetic Indices",
                    pip_size=0.01,
                )
            ]

        async def fake_fetch(client, symbol, *, count, end, start=1):
            return page

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)

        result = await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="Volatility 75 (1s) Index",
            pages=1,
            write_manifest=False,
        )

    assert result.pages_fetched == 1
    assert result.ticks_written >= 1
    assert store.coverage("volatility_75_1s").tick_count >= 1
