"""Offline tests for historical candle dataset (Milestone 1E)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from smb.data.candle_store import (
    ParquetCandleStore,
    build_candles_from_ticks,
)
from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick
from smb.market.candles import TIMEFRAME_M1, TIMEFRAME_M5, CandleBuilder
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
def roots(tmp_path: Path):
    tick_store = ParquetTickStore(tmp_path / "dataset")
    candle_store = ParquetCandleStore(tmp_path / "dataset")
    return tick_store, candle_store, TickRepository(tick_store)


def test_write_and_read_candles(roots):
    _, candle_store, _ = roots
    from smb.market.candles import Candle

    candles = [
        Candle("M1", 0, 60, 100.0, 102.0, 99.0, 101.0, 4),
        Candle("M1", 60, 120, 101.0, 103.0, 100.0, 102.0, 3),
    ]
    assert candle_store.write_candles("vol", candles) == 2
    got = candle_store.get_candles("vol", "M1")
    assert len(got) == 2
    assert got[0].open == 100.0
    assert got[1].start_epoch == 60


def test_candle_range_half_open(roots):
    _, candle_store, _ = roots
    from smb.market.candles import Candle

    candles = [
        Candle("M1", 0, 60, 1.0, 1.0, 1.0, 1.0, 1),
        Candle("M1", 60, 120, 2.0, 2.0, 2.0, 2.0, 1),
        Candle("M1", 120, 180, 3.0, 3.0, 3.0, 3.0, 1),
    ]
    candle_store.write_candles("x", candles)
    got = candle_store.get_candles("x", "M1", start_epoch=60, end_epoch=120)
    assert [c.start_epoch for c in got] == [60]


def test_empty_candle_dataset(roots):
    _, candle_store, _ = roots
    assert candle_store.get_candles("missing", "M1") == []
    assert candle_store.list_instruments() == []


def test_build_from_ticks_matches_memory(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(
        [
            _st("x", 0, 100.0),
            _st("x", 10, 102.0),
            _st("x", 30, 99.0),
            _st("x", 60, 101.0),
            _st("x", 90, 100.5),
        ]
    )
    counts = build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    assert counts["M1"] == 2

    stored = candle_store.get_candles("x", "M1")
    memory = CandleBuilder(TIMEFRAME_M1).process(
        HistoricalReplay(
            [
                _tick(0, 100.0),
                _tick(10, 102.0),
                _tick(30, 99.0),
                _tick(60, 101.0),
                _tick(90, 100.5),
            ]
        )
    )
    assert len(stored) == len(memory) == 2
    for a, b in zip(stored, memory, strict=True):
        assert a.start_epoch == b.start_epoch
        assert a.open == b.open
        assert a.high == b.high
        assert a.low == b.low
        assert a.close == b.close
        assert a.tick_count == b.tick_count


def test_build_multi_timeframe(roots):
    tick_store, candle_store, tick_repo = roots
    ticks = [_st("x", i, 100.0 + (i % 5)) for i in range(0, 900, 1)]
    tick_store.write_ticks(ticks)
    counts = build_candles_from_ticks(tick_repo, candle_store, instrument="x")
    assert counts["M1"] >= 1
    assert counts["M5"] >= 1
    assert counts["M15"] >= 1
    assert "M1" in candle_store.list_timeframes("x")


def test_rebuild_replaces_same_range(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(
        [_st("x", 0, 1.0), _st("x", 30, 2.0), _st("x", 60, 3.0)]
    )
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    first = candle_store.get_candles("x", "M1")
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    second = candle_store.get_candles("x", "M1")
    assert len(first) == len(second)
    assert [c.start_epoch for c in first] == [c.start_epoch for c in second]


def test_candle_coverage(roots):
    _, candle_store, _ = roots
    from smb.market.candles import Candle

    candle_store.write_candles(
        "x",
        [
            Candle("M5", 0, 300, 10.0, 15.0, 9.0, 12.0, 50),
            Candle("M5", 300, 600, 12.0, 20.0, 11.0, 18.0, 40),
        ],
    )
    cov = candle_store.coverage("x", "M5")
    assert cov["candle_count"] == 2
    assert cov["earliest_start"] == 0
    assert cov["latest_start"] == 300
    assert cov["min_low"] == 9.0
    assert cov["max_high"] == 20.0


def test_m5_from_ticks(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(
        [
            _st("x", 0, 100.0),
            _st("x", 60, 105.0),
            _st("x", 120, 95.0),
            _st("x", 240, 102.0),
        ]
    )
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M5]
    )
    got = candle_store.get_candles("x", "M5")
    assert len(got) == 1
    assert got[0].open == 100.0
    assert got[0].high == 105.0
    assert got[0].low == 95.0
    assert got[0].close == 102.0
    assert got[0].tick_count == 4
