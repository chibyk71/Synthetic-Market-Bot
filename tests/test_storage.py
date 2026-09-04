"""Offline tests for Parquet store, DuckDB repository, validation, ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from smb.data.ingest import ingest_instrument
from smb.data.models import StoredTick
from smb.data.repository import TickRepository
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
        timestamp=datetime.fromtimestamp(epoch, tz=timezone.utc),
        price=price,
        epoch=epoch,
    )


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "dataset")


def test_write_and_read_ticks(store: ParquetTickStore):
    ticks = [
        _st("volatility_75_1s", 1000, 100.0),
        _st("volatility_75_1s", 1001, 101.0),
        _st("volatility_75_1s", 1002, 99.5),
    ]
    n = store.write_ticks(ticks)
    assert n == 3
    got = list(store.read_ticks("volatility_75_1s"))
    assert [t.epoch for t in got] == [1000, 1001, 1002]
    assert [t.price for t in got] == [100.0, 101.0, 99.5]


def test_empty_dataset(store: ParquetTickStore):
    assert list(store.read_ticks("missing")) == []
    assert store.list_instruments() == []


def test_multiple_instruments(store: ParquetTickStore):
    store.write_ticks([_st("a", 10, 1.0), _st("b", 20, 2.0)])
    assert set(store.list_instruments()) == {"a", "b"}
    assert list(store.read_ticks("a"))[0].price == 1.0
    assert list(store.read_ticks("b"))[0].price == 2.0


def test_range_query_half_open(store: ParquetTickStore):
    store.write_ticks(
        [
            _st("x", 100, 1.0),
            _st("x", 200, 2.0),
            _st("x", 300, 3.0),
        ]
    )
    repo = TickRepository(store)
    got = repo.get_ticks("x", start_epoch=100, end_epoch=300)
    assert [t.epoch for t in got] == [100, 200]


def test_query_chronological(store: ParquetTickStore):
    store.write_ticks(
        [
            _st("x", 300, 3.0),
            _st("x", 100, 1.0),
            _st("x", 200, 2.0),
        ],
        dedupe=False,
    )
    repo = TickRepository(store)
    epochs = [t.epoch for t in repo.get_ticks("x")]
    assert epochs == [100, 200, 300]


def test_duplicate_ingestion_deterministic(store: ParquetTickStore):
    ticks = [_st("x", 100, 1.0), _st("x", 101, 2.0)]
    assert store.write_ticks(ticks) == 2
    assert store.write_ticks(ticks, dedupe=True) == 0
    assert len(list(store.read_ticks("x"))) == 2


def test_duplicate_detection_validation():
    ticks = [
        _st("x", 1, 1.0),
        _st("x", 1, 1.0),
        _st("x", 2, 2.0),
    ]
    report = validate_ticks(ticks)
    assert report.duplicate_count == 1
    assert not report.valid


def test_validate_valid_dataset():
    ticks = [_st("x", 1, 1.0), _st("x", 2, 2.0), _st("x", 3, 1.5)]
    report = validate_ticks(ticks, expected_instrument="x")
    assert report.valid
    assert report.tick_count == 3
    assert report.earliest_epoch == 1
    assert report.latest_epoch == 3
    assert report.min_price == 1.0
    assert report.max_price == 2.0
    assert report.duplicate_count == 0
    assert report.non_monotonic_count == 0


def test_validate_invalid_epoch():
    report = validate_ticks([_st("x", -1, 1.0)])
    assert not report.valid
    assert any("epoch" in e for e in report.errors)


def test_validate_non_monotonic():
    ticks = [_st("x", 10, 1.0), _st("x", 9, 2.0)]
    report = validate_ticks(ticks)
    assert report.non_monotonic_count == 1
    assert not report.valid


def test_validate_instrument_mismatch():
    report = validate_ticks([_st("a", 1, 1.0)], expected_instrument="b")
    assert not report.valid


def test_dataset_stats(store: ParquetTickStore):
    store.write_ticks([_st("vol", 100, 50.0), _st("vol", 200, 60.0)])
    stats = compute_dataset_stats(TickRepository(store))
    item = stats.for_instrument("vol")
    assert item is not None
    assert item.tick_count == 2
    assert item.min_price == 50.0
    assert item.max_price == 60.0


@pytest.mark.asyncio
async def test_ingest_incremental(store: ParquetTickStore):
    page = HistoryPage(
        symbol="1HZ75V",
        ticks=(
            _tick(1000, 10.0),
            _tick(1001, 11.0),
        ),
        pip_size=0.01,
    )
    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.deriv import history as hist_mod
        from smb.deriv import symbols as sym_mod

        fake_info = MagicMock()
        fake_info.symbol = "1HZ75V"

        async def fake_load(client, detail="full"):
            return [fake_info]

        def fake_resolve(name, symbols):
            return fake_info

        async def fake_pages(client, symbol, pages=1, count_per_page=1000, end="latest"):
            return [page]

        from smb.data import ingest as ingest_mod

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
        mp.setattr(ingest_mod, "fetch_ticks_paginated", fake_pages)

        result = await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="Volatility 75 (1s) Index",
            pages=1,
        )
    assert result.ticks_written == 2
    assert result.symbol == "1HZ75V"
    got = list(store.read_ticks("volatility_75_1s"))
    assert len(got) == 2


def test_stored_to_replay_to_candles(store: ParquetTickStore):
    store.write_ticks(
        [
            _st("x", 0, 100.0),
            _st("x", 10, 102.0),
            _st("x", 30, 99.0),
            _st("x", 60, 101.0),
        ]
    )
    repo = TickRepository(store)
    ticks = list(repo.as_tick_stream("x"))
    candles_from_store = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(ticks))

    memory = [_tick(0, 100.0), _tick(10, 102.0), _tick(30, 99.0), _tick(60, 101.0)]
    candles_memory = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(memory))

    assert len(candles_from_store) == len(candles_memory) == 2
    for a, b in zip(candles_from_store, candles_memory, strict=True):
        assert a == b
