"""Offline tests for historical candle dataset (Milestone 1E)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from smb.data.candle_store import (
    ParquetCandleStore,
    align_build_range,
    build_candles_from_ticks,
    is_complete_candle,
)
from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick
from smb.market.candles import TIMEFRAME_M1, TIMEFRAME_M5, Candle, CandleBuilder
from smb.market.replay import HistoricalReplay


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=timezone.utc),
        price=price,
        epoch=epoch,
    )


def _fill_seconds(instrument: str, start: int, end: int, price_fn) -> list[StoredTick]:
    """One tick per second in [start, end)."""
    return [_st(instrument, ep, float(price_fn(ep))) for ep in range(start, end)]


@pytest.fixture
def roots(tmp_path: Path):
    tick_store = ParquetTickStore(tmp_path / "dataset")
    candle_store = ParquetCandleStore(tmp_path / "dataset")
    return tick_store, candle_store, TickRepository(tick_store)


def test_write_and_read_candles(roots):
    _, candle_store, _ = roots
    candles = [
        Candle("M1", 0, 60, 100.0, 102.0, 99.0, 101.0, 60),
        Candle("M1", 60, 120, 101.0, 103.0, 100.0, 102.0, 60),
    ]
    assert candle_store.write_candles("vol", candles) == 2
    got = candle_store.get_candles("vol", "M1")
    assert len(got) == 2
    assert got[0].open == 100.0
    assert got[1].start_epoch == 60


def test_candle_range_half_open(roots):
    _, candle_store, _ = roots
    candles = [
        Candle("M1", 0, 60, 1.0, 1.0, 1.0, 1.0, 60),
        Candle("M1", 60, 120, 2.0, 2.0, 2.0, 2.0, 60),
        Candle("M1", 120, 180, 3.0, 3.0, 3.0, 3.0, 60),
    ]
    candle_store.write_candles("x", candles)
    got = candle_store.get_candles("x", "M1", start_epoch=60, end_epoch=120)
    assert [c.start_epoch for c in got] == [60]


def test_empty_candle_dataset(roots):
    _, candle_store, _ = roots
    assert candle_store.get_candles("missing", "M1") == []
    assert candle_store.list_instruments() == []


def test_align_build_range():
    assert align_build_range(TIMEFRAME_M1, 10, 130) == (60, 120)
    assert align_build_range(TIMEFRAME_M1, 0, 120) == (0, 120)
    assert align_build_range(TIMEFRAME_M5, 100, 700) == (300, 600)
    with pytest.raises(ValueError, match="No complete"):
        align_build_range(TIMEFRAME_M1, 10, 50)


def test_is_complete_candle():
    full = Candle("M1", 0, 60, 1.0, 2.0, 0.5, 1.5, 60)
    assert is_complete_candle(full, TIMEFRAME_M1)
    partial = Candle("M1", 0, 60, 1.0, 2.0, 0.5, 1.5, 30)
    assert not is_complete_candle(partial, TIMEFRAME_M1)
    misaligned = Candle("M1", 10, 70, 1.0, 2.0, 0.5, 1.5, 60)
    assert not is_complete_candle(misaligned, TIMEFRAME_M1)


def test_build_from_ticks_matches_memory(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(_fill_seconds("x", 0, 120, lambda ep: 100.0 + (ep % 7)))
    counts = build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    assert counts["M1"] == 2

    stored = candle_store.get_candles("x", "M1")
    memory = CandleBuilder(TIMEFRAME_M1).process(
        HistoricalReplay([_tick(ep, 100.0 + (ep % 7)) for ep in range(0, 120)])
    )
    memory = [c for c in memory if is_complete_candle(c, TIMEFRAME_M1)]
    assert len(stored) == len(memory) == 2
    for a, b in zip(stored, memory, strict=True):
        assert a.start_epoch == b.start_epoch
        assert a.open == b.open
        assert a.high == b.high
        assert a.low == b.low
        assert a.close == b.close
        assert a.tick_count == b.tick_count


def test_partial_edge_candles_not_persisted(roots):
    """Ticks mid-bucket must not produce canonical partial candles."""
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(
        _fill_seconds("x", 20, 50, lambda ep: 1.0)
        + _fill_seconds("x", 60, 80, lambda ep: 2.0)
    )
    counts = build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    assert counts["M1"] == 0
    assert candle_store.get_candles("x", "M1") == []


def test_aligned_range_skips_partial_bounds(roots):
    """--start/--end cut mid-bucket: only complete interior buckets kept."""
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(_fill_seconds("x", 0, 180, lambda ep: 10.0 + ep * 0.01))
    counts = build_candles_from_ticks(
        tick_repo,
        candle_store,
        instrument="x",
        timeframes=[TIMEFRAME_M1],
        start_epoch=10,
        end_epoch=130,
    )
    assert counts["M1"] == 1
    got = candle_store.get_candles("x", "M1")
    assert [c.start_epoch for c in got] == [60]
    assert got[0].tick_count == 60


def test_rebuild_clears_stale_candles(roots):
    """If a candle disappears on rebuild, the old row must not remain."""
    _, candle_store, _ = roots
    c0 = Candle("M1", 0, 60, 1.0, 1.0, 1.0, 1.0, 60)
    c60 = Candle("M1", 60, 120, 2.0, 2.0, 2.0, 2.0, 60)
    candle_store.write_candles(
        "x", [c0, c60], clear_start=0, clear_end=120
    )
    assert [c.start_epoch for c in candle_store.get_candles("x", "M1")] == [0, 60]

    candle_store.write_candles(
        "x", [c0], clear_start=0, clear_end=120
    )
    got = candle_store.get_candles("x", "M1")
    assert [c.start_epoch for c in got] == [0]


def test_rebuild_from_ticks_clears_vanished_bucket(roots):
    """Full-range rebuild after coverage shrinks drops missing candles."""
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(_fill_seconds("x", 0, 120, lambda ep: 1.0))
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1],
        start_epoch=0, end_epoch=120,
    )
    assert len(candle_store.get_candles("x", "M1")) == 2

    c0 = candle_store.get_candles("x", "M1")[0]
    candle_store.write_candles("x", [c0], clear_start=0, clear_end=120)
    assert [c.start_epoch for c in candle_store.get_candles("x", "M1")] == [0]


def test_rebuild_replaces_same_range(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(_fill_seconds("x", 0, 120, lambda ep: 1.0 + (ep % 3)))
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    first = candle_store.get_candles("x", "M1")
    build_candles_from_ticks(
        tick_repo, candle_store, instrument="x", timeframes=[TIMEFRAME_M1]
    )
    second = candle_store.get_candles("x", "M1")
    assert len(first) == len(second) == 2
    assert [c.start_epoch for c in first] == [c.start_epoch for c in second]


def test_build_multi_timeframe(roots):
    tick_store, candle_store, tick_repo = roots
    tick_store.write_ticks(_fill_seconds("x", 0, 900, lambda ep: 100.0 + (ep % 5)))
    counts = build_candles_from_ticks(tick_repo, candle_store, instrument="x")
    assert counts["M1"] == 15
    assert counts["M5"] == 3
    assert counts["M15"] == 1
    assert "M1" in candle_store.list_timeframes("x")


def test_candle_coverage(roots):
    _, candle_store, _ = roots
    candle_store.write_candles(
        "x",
        [
            Candle("M5", 0, 300, 10.0, 15.0, 9.0, 12.0, 300),
            Candle("M5", 300, 600, 12.0, 20.0, 11.0, 18.0, 300),
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
    root = tick_store.root
    from smb.data.store import ParquetTickStore as PTS

    ts = PTS(root.parent / "m5set")
    cs = ParquetCandleStore(root.parent / "m5set")
    repo = TickRepository(ts)
    prices = [100.0] * 300
    prices[0] = 100.0
    prices[60] = 105.0
    prices[120] = 95.0
    prices[240] = 102.0
    prices[299] = 102.0
    ts.write_ticks([_st("x", i, prices[i]) for i in range(300)])
    build_candles_from_ticks(repo, cs, instrument="x", timeframes=[TIMEFRAME_M5])
    got = cs.get_candles("x", "M5")
    assert len(got) == 1
    assert got[0].open == 100.0
    assert got[0].high == 105.0
    assert got[0].low == 95.0
    assert got[0].close == 102.0
    assert got[0].tick_count == 300
