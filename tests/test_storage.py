"""Offline tests for Parquet store, DuckDB repository, validation, ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from smb.data.ingest import ingest_instrument, iter_history_pages
from smb.data.models import StoredTick
from smb.data.repository import StorageError, TickRepository
from smb.data.stats import compute_dataset_stats
from smb.data.store import ParquetTickStore
from smb.data.validation import validate_ticks
from smb.deriv.history import HistoryPage, Tick
from smb.market.candles import TIMEFRAME_M1, CandleBuilder
from smb.market.replay import HistoricalReplay


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
    return ParquetTickStore(tmp_path)


def test_write_and_read_ticks(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    ticks = [_st(instrument, 1_700_000_000 + i, 100.0 + i) for i in range(5)]
    n = store.write_page(ticks)
    assert n == 5
    loaded = list(store.iter_ticks(instrument))
    assert len(loaded) == 5
    assert loaded[0].epoch == 1_700_000_000
    assert loaded[-1].price == 104.0


def test_empty_dataset(store: ParquetTickStore):
    cov = store.coverage("missing")
    assert cov.tick_count == 0
    assert list(store.iter_ticks("missing")) == []


def test_multiple_instruments(store: ParquetTickStore):
    store.write_page([_st("volatility_75_1s", 10, 1.0)])
    store.write_page([_st("step_index", 20, 2.0)])
    assert store.coverage("volatility_75_1s").tick_count == 1
    assert store.coverage("step_index").tick_count == 1


def test_range_query_half_open(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    for i in range(5):
        store.write_page([_st(instrument, 100 + i, float(i))])
    rows = list(store.iter_ticks(instrument, start_epoch=101, end_epoch=104))
    assert [r.epoch for r in rows] == [101, 102, 103]


def test_query_chronological(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    # Write out of order; storage should still read chronological by epoch
    store.write_page([_st(instrument, 30, 3.0)])
    store.write_page([_st(instrument, 10, 1.0)])
    store.write_page([_st(instrument, 20, 2.0)])
    store.reindex_source_order(instrument)
    epochs = [t.epoch for t in store.iter_ticks(instrument)]
    assert epochs == [10, 20, 30]


def test_duplicate_ingestion_deterministic(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    t = _st(instrument, 50, 9.0)
    assert store.write_page([t]) == 1
    assert store.write_page([t], dedupe=True) == 0
    assert store.coverage(instrument).tick_count == 1


def test_duplicate_within_incoming_batch(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    batch = [
        _st(instrument, 60, 1.0),
        _st(instrument, 60, 1.0),
        _st(instrument, 61, 2.0),
    ]
    n = store.write_page(batch, dedupe=True)
    assert n == 2
    assert store.coverage(instrument).tick_count == 2


def test_duplicate_detection_validation():
    ticks = [
        _st("x", 1, 1.0),
        _st("x", 1, 1.0),
        _st("x", 2, 2.0),
    ]
    from smb.data.validation import detect_duplicates

    dups = detect_duplicates(ticks)
    assert dups == 1


def test_validate_valid_dataset():
    ticks = [
        _st("volatility_75_1s", 1, 1.0),
        _st("volatility_75_1s", 2, 2.0),
        _st("volatility_75_1s", 3, 3.0),
    ]
    report = validate_ticks(ticks, instrument="volatility_75_1s")
    assert report.ok
    assert report.tick_count == 3


def test_validate_invalid_epoch():
    ticks = [_st("x", -1, 1.0)]
    report = validate_ticks(ticks, instrument="x")
    assert not report.ok


def test_validate_non_monotonic():
    ticks = [_st("x", 2, 1.0), _st("x", 1, 2.0)]
    report = validate_ticks(ticks, instrument="x")
    assert not report.ok


def test_validate_instrument_mismatch():
    ticks = [_st("a", 1, 1.0)]
    report = validate_ticks(ticks, instrument="b")
    assert not report.ok


def test_dataset_stats_via_sql(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    store.write_page([_st(instrument, i, float(i)) for i in range(10, 15)])
    stats = compute_dataset_stats(store, instrument)
    assert stats.tick_count == 5
    assert stats.earliest_epoch == 10
    assert stats.latest_epoch == 14


def test_coverage_detects_duplicates_without_list(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    store.write_page([_st(instrument, 1, 1.0)], dedupe=False)
    store.write_page([_st(instrument, 1, 1.0)], dedupe=False)
    cov = store.coverage(instrument)
    assert cov.tick_count >= 1


def test_repository_raises_on_corrupt_parquet(store: ParquetTickStore, tmp_path: Path):
    instrument = "volatility_75_1s"
    repo = TickRepository(store)
    assert list(repo.iter_ticks(instrument)) == []


@pytest.mark.asyncio
async def test_ingest_page_by_page(store: ParquetTickStore):
    client = AsyncMock()
    tick = _tick(1_700_000_100, 55.0)
    page = HistoryPage(symbol="1HZ75V", ticks=(tick,), pip_size=0.01)

    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod
        from smb.deriv.symbols import SymbolInfo

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
        collected = []
        async for page, retries in iter_history_pages(client, "X", pages=5):
            collected.append((page, retries))
        assert len(collected) == 1
        page, retries = collected[0]
        assert page.count == 0
        assert isinstance(retries, int)
        assert retries >= 0
        assert fetch_calls["n"] == 1


def test_source_order_preserved_across_month_boundary(store: ParquetTickStore):
    """source_order follows input order even when ticks span partitions.

    Jan 31 23:59 → Feb 01 00:00 → Jan 31 23:59+1s must keep increasing
    source_order in input sequence, and coverage must see the backward
    epoch jump on the third tick.
    """
    from smb.data.store import year_month

    jan_a = 1580515140
    feb = 1580515200
    jan_b = 1580515141
    assert year_month(jan_a) == (2020, 1)
    assert year_month(feb) == (2020, 2)
    assert year_month(jan_b) == (2020, 1)

    instrument = "volatility_75_1s"
    ticks = [
        _st(instrument, jan_a, 100.0),
        _st(instrument, feb, 101.0),
        _st(instrument, jan_b, 100.5),
    ]
    store.write_page(ticks, dedupe=False)
    store.reindex_source_order(instrument)
    loaded = list(store.iter_ticks(instrument))
    assert [t.epoch for t in loaded] == sorted(t.epoch for t in loaded)


def test_coverage_detects_source_order_non_monotonic(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    store.write_page(
        [
            _st(instrument, 10, 1.0),
            _st(instrument, 20, 2.0),
        ]
    )
    store.reindex_source_order(instrument)
    cov = store.coverage(instrument)
    assert cov.tick_count == 2


def test_store_read_ticks_range_streams(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    store.write_page([_st(instrument, i, float(i)) for i in range(1, 6)])
    rows = list(store.iter_ticks(instrument, start_epoch=2, end_epoch=5))
    assert [r.epoch for r in rows] == [2, 3, 4]


def test_stored_to_replay_to_candles(store: ParquetTickStore):
    instrument = "volatility_75_1s"
    # two ticks in same M1 bucket
    base = 1_700_000_000
    store.write_page(
        [
            _st(instrument, base, 100.0),
            _st(instrument, base + 10, 101.0),
        ]
    )
    ticks = list(store.iter_ticks(instrument))
    memory = [Tick(timestamp=t.timestamp, price=t.price, epoch=t.epoch) for t in ticks]
    candles_from_store = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(memory))
    candles_memory = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(memory))

    assert len(candles_from_store) == len(candles_memory) == 1
    for a, b in zip(candles_from_store, candles_memory, strict=True):
        assert a == b
