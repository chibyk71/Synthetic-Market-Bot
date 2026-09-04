"""Offline tests for Parquet store, DuckDB repository, validation, ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
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


def test_duplicate_within_incoming_batch(store: ParquetTickStore):
    ticks = [
        _st("x", 100, 1.0),
        _st("x", 100, 1.0),
        _st("x", 101, 2.0),
        _st("x", 101, 2.0),
    ]
    assert store.write_page(ticks, dedupe=True) == 2
    got = list(store.read_ticks("x"))
    assert len(got) == 2
    assert [t.epoch for t in got] == [100, 101]


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


def test_dataset_stats_via_sql(store: ParquetTickStore):
    store.write_ticks([_st("vol", 100, 50.0), _st("vol", 200, 60.0)])
    stats = compute_dataset_stats(TickRepository(store))
    item = stats.for_instrument("vol")
    assert item is not None
    assert item.tick_count == 2
    assert item.min_price == 50.0
    assert item.max_price == 60.0
    assert item.duplicate_count == 0


def test_coverage_detects_duplicates_without_list(store: ParquetTickStore):
    store.write_ticks(
        [_st("x", 1, 1.0), _st("x", 1, 1.0), _st("x", 2, 2.0)],
        dedupe=False,
    )
    cov = TickRepository(store).coverage("x")
    assert cov["tick_count"] == 3
    assert cov["duplicate_count"] == 1


def test_repository_raises_on_corrupt_parquet(store: ParquetTickStore, tmp_path: Path):
    instrument_dir = store.ticks_dir / "instrument=bad"
    part = instrument_dir / "year=2020" / "month=01"
    part.mkdir(parents=True)
    corrupt = part / "part-000.parquet"
    corrupt.write_text("this is not parquet")
    repo = TickRepository(store)
    with pytest.raises(StorageError, match="Failed to read"):
        list(repo.iter_ticks("bad"))


@pytest.mark.asyncio
async def test_ingest_page_by_page(store: ParquetTickStore):
    page1 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(2000, 10.0), _tick(2001, 11.0)),
        pip_size=0.01,
    )
    page2 = HistoryPage(
        symbol="1HZ75V",
        ticks=(_tick(1000, 9.0), _tick(1001, 9.5)),
        pip_size=0.01,
    )
    pages = [page1, page2]
    write_log: list[int] = []
    original_write_page = store.write_page

    def tracking_write_page(ticks, *, dedupe=True):
        write_log.append(len(ticks))
        return original_write_page(ticks, dedupe=dedupe)

    store.write_page = tracking_write_page  # type: ignore[method-assign]

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        fake_info = MagicMock()
        fake_info.symbol = "1HZ75V"

        async def fake_load(client, detail="full"):
            return [fake_info]

        def fake_resolve(name, symbols):
            return fake_info

        call_count = {"n": 0}

        async def fake_fetch(client, symbol, *, count, end, start=1):
            idx = call_count["n"]
            call_count["n"] += 1
            if idx == 1:
                assert len(write_log) == 1
            return pages[idx]

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)

        result = await ingest_instrument(
            client,
            store,
            instrument="volatility_75_1s",
            display_name="Volatility 75 (1s) Index",
            pages=2,
        )

    assert result.pages_fetched == 2
    assert result.ticks_written == 4
    assert write_log == [2, 2]
    got = list(store.read_ticks("volatility_75_1s"))
    assert len(got) == 4


@pytest.mark.asyncio
async def test_iter_history_pages_stops_on_empty():
    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        empty = HistoryPage(symbol="X", ticks=(), pip_size=None)

        async def fake_fetch(client, symbol, *, count, end, start=1):
            return empty

        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)
        collected = []
        async for page in iter_history_pages(client, "X", pages=5):
            collected.append(page)
        assert len(collected) == 1
        assert collected[0].count == 0


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
