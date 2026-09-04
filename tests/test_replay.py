"""Offline tests for HistoricalReplay and TickStream contract."""

from __future__ import annotations

from datetime import datetime, timezone

from smb.deriv.history import Tick
from smb.market.candles import TIMEFRAME_M1, CandleBuilder
from smb.market.replay import HistoricalReplay, TickStream


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=timezone.utc),
        price=price,
        epoch=epoch,
    )


def test_replay_preserves_exact_source_order():
    ticks = [_tick(3, 30.0), _tick(1, 10.0), _tick(2, 20.0)]
    replay = HistoricalReplay(ticks)
    emitted = list(replay)
    assert [t.epoch for t in emitted] == [3, 1, 2]
    assert [t.price for t in emitted] == [30.0, 10.0, 20.0]


def test_replay_empty():
    replay = HistoricalReplay([])
    assert list(replay) == []
    assert len(replay) == 0
    assert replay.run() == []


def test_replay_run_callback():
    ticks = [_tick(10, 1.0), _tick(11, 2.0)]
    seen: list[int] = []
    result = HistoricalReplay(ticks).run(on_tick=lambda t: seen.append(t.epoch))
    assert seen == [10, 11]
    assert [t.epoch for t in result] == [10, 11]


def test_replay_determinism():
    ticks = [_tick(i, float(i)) for i in range(100, 110)]
    r1 = list(HistoricalReplay(ticks))
    r2 = list(HistoricalReplay(ticks))
    assert [t.epoch for t in r1] == [t.epoch for t in r2]
    assert [t.price for t in r1] == [t.price for t in r2]


def test_candle_determinism_via_replay():
    ticks = [
        _tick(0, 100.0),
        _tick(10, 102.0),
        _tick(30, 99.0),
        _tick(60, 101.0),
    ]
    c1 = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(ticks))
    c2 = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(ticks))
    assert len(c1) == len(c2) == 2
    for a, b in zip(c1, c2, strict=True):
        assert a == b


def test_replay_is_tick_stream():
    replay = HistoricalReplay([_tick(1, 1.0)])
    assert isinstance(replay, TickStream)


def test_candle_builder_accepts_replay_as_source():
    ticks = [_tick(0, 5.0), _tick(60, 6.0)]
    candles = CandleBuilder(TIMEFRAME_M1).process(HistoricalReplay(ticks))
    assert len(candles) == 2
    assert candles[0].open == 5.0
    assert candles[1].open == 6.0
