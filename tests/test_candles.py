"""Offline tests for CandleBuilder, MultiTimeframeCandleBuilder, boundaries."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from smb.deriv.history import Tick
from smb.market.candles import (
    TIMEFRAME_M1,
    TIMEFRAME_M5,
    TIMEFRAME_M15,
    CandleBuilder,
    MultiTimeframeCandleBuilder,
    OutOfOrderTickError,
    Timeframe,
)


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=timezone.utc),
        price=price,
        epoch=epoch,
    )


def test_basic_ohlc_one_minute():
    base = 960
    ticks = [
        _tick(base + 0, 100.0),
        _tick(base + 10, 102.0),
        _tick(base + 20, 99.0),
        _tick(base + 30, 101.0),
    ]
    candles = CandleBuilder(TIMEFRAME_M1).process(ticks)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 100.0
    assert c.high == 102.0
    assert c.low == 99.0
    assert c.close == 101.0
    assert c.tick_count == 4
    assert c.start_epoch == base
    assert c.end_epoch == base + 60
    assert c.timeframe == "M1"
    assert c.finalized is True


def test_single_tick_candle():
    base = 960
    candles = CandleBuilder(TIMEFRAME_M1).process([_tick(base + 5, 42.5)])
    assert len(candles) == 1
    c = candles[0]
    assert c.open == c.high == c.low == c.close == 42.5
    assert c.tick_count == 1


def test_m1_exact_boundaries():
    t0 = _tick(0, 10.0)
    t59 = _tick(59, 11.0)
    t60 = _tick(60, 12.0)
    candles = CandleBuilder(TIMEFRAME_M1).process([t0, t59, t60])
    assert len(candles) == 2
    assert candles[0].start_epoch == 0
    assert candles[0].end_epoch == 60
    assert candles[0].open == 10.0
    assert candles[0].close == 11.0
    assert candles[0].tick_count == 2
    assert candles[1].start_epoch == 60
    assert candles[1].end_epoch == 120
    assert candles[1].open == 12.0
    assert candles[1].tick_count == 1


def test_tick_at_boundary_starts_next_candle():
    builder = CandleBuilder(TIMEFRAME_M1)
    assert builder.on_tick(_tick(0, 1.0)) is None
    completed = builder.on_tick(_tick(60, 2.0))
    assert completed is not None
    assert completed.start_epoch == 0
    assert completed.close == 1.0
    final = builder.flush()
    assert final is not None
    assert final.start_epoch == 60
    assert final.open == 2.0


def test_gaps_do_not_fabricate_candles():
    ticks = [_tick(59, 10.0), _tick(190, 11.0)]
    candles = CandleBuilder(TIMEFRAME_M1).process(ticks)
    assert len(candles) == 2
    assert candles[0].start_epoch == 0
    assert candles[1].start_epoch == 180
    starts = {c.start_epoch for c in candles}
    assert 60 not in starts
    assert 120 not in starts


def test_m5_aggregates_across_m1_boundaries():
    ticks = [
        _tick(0, 100.0),
        _tick(60, 105.0),
        _tick(120, 95.0),
        _tick(240, 102.0),
    ]
    candles = CandleBuilder(TIMEFRAME_M5).process(ticks)
    assert len(candles) == 1
    c = candles[0]
    assert c.start_epoch == 0
    assert c.end_epoch == 300
    assert c.open == 100.0
    assert c.high == 105.0
    assert c.low == 95.0
    assert c.close == 102.0
    assert c.tick_count == 4
    assert c.timeframe == "M5"


def test_m15_bucket():
    ticks = [
        _tick(0, 50.0),
        _tick(400, 55.0),
        _tick(899, 48.0),
        _tick(900, 60.0),
    ]
    candles = CandleBuilder(TIMEFRAME_M15).process(ticks)
    assert len(candles) == 2
    assert candles[0].start_epoch == 0
    assert candles[0].end_epoch == 900
    assert candles[0].open == 50.0
    assert candles[0].high == 55.0
    assert candles[0].low == 48.0
    assert candles[0].close == 48.0
    assert candles[0].tick_count == 3
    assert candles[1].start_epoch == 900
    assert candles[1].open == 60.0


def test_out_of_order_raises():
    builder = CandleBuilder(TIMEFRAME_M1)
    builder.on_tick(_tick(100, 1.0))
    with pytest.raises(OutOfOrderTickError) as exc_info:
        builder.on_tick(_tick(99, 2.0))
    assert exc_info.value.previous_epoch == 100
    assert exc_info.value.tick_epoch == 99


def test_equal_epoch_allowed():
    builder = CandleBuilder(TIMEFRAME_M1)
    builder.on_tick(_tick(100, 1.0))
    assert builder.on_tick(_tick(100, 2.0)) is None
    c = builder.flush()
    assert c is not None
    assert c.tick_count == 2
    assert c.open == 1.0
    assert c.close == 2.0


def test_flush_finalizes_open_candle():
    builder = CandleBuilder(TIMEFRAME_M1)
    assert builder.flush() is None
    builder.on_tick(_tick(0, 5.0))
    builder.on_tick(_tick(10, 6.0))
    c = builder.flush()
    assert c is not None
    assert c.open == 5.0
    assert c.close == 6.0
    assert c.tick_count == 2
    assert builder.flush() is None


def test_empty_stream():
    candles = CandleBuilder(TIMEFRAME_M1).process([])
    assert candles == []


def test_multi_timeframe_coordinator():
    ticks = [
        _tick(0, 100.0),
        _tick(30, 101.0),
        _tick(60, 102.0),
        _tick(300, 103.0),
    ]
    mt = MultiTimeframeCandleBuilder()
    results = mt.process(ticks)
    assert "M1" in results and "M5" in results and "M15" in results
    assert len(results["M1"]) == 3
    assert results["M1"][0].start_epoch == 0
    assert results["M1"][1].start_epoch == 60
    assert results["M1"][2].start_epoch == 300
    assert len(results["M5"]) == 2
    assert results["M5"][0].start_epoch == 0
    assert results["M5"][0].tick_count == 3
    assert results["M5"][1].start_epoch == 300
    assert len(results["M15"]) == 1
    assert results["M15"][0].start_epoch == 0
    assert results["M15"][0].tick_count == 4


def test_timeframe_invalid_seconds():
    with pytest.raises(ValueError):
        Timeframe("X", 0)
