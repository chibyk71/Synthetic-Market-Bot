"""Milestone 4A — live market data tests (no live Deriv network)."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from smb.live import (
    CandleEventKind,
    ConnectionState,
    FakeTickTransport,
    LiveCandleTracker,
    LiveMarketDataService,
    LiveMarketState,
    LiveTick,
    MalformedTickError,
    MultiTimeframeLiveCandles,
    TickOrderingGate,
    make_fake_symbol,
    normalize_tick_message,
)
from smb.market.candles import TIMEFRAME_M1, TIMEFRAME_M15


def _tick_msg(symbol: str, price: float, epoch: int) -> dict:
    return {
        "msg_type": "tick",
        "tick": {"symbol": symbol, "quote": price, "epoch": epoch},
    }


def test_normalize_valid_tick():
    t = normalize_tick_message(
        _tick_msg("1HZ75V", 100.5, 1_700_000_000),
        instrument="Volatility 75 (1s) Index",
        expected_symbol="1HZ75V",
    )
    assert t.symbol == "1HZ75V"
    assert t.price == 100.5
    assert t.epoch == 1_700_000_000


@pytest.mark.parametrize(
    "msg",
    [
        {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "epoch": 1}},
        {"msg_type": "tick", "tick": {"quote": 1.0, "epoch": 1}},
        {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "quote": 1.0}},
        {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "quote": "x", "epoch": 1}},
        {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "quote": 1.0, "epoch": -1}},
    ],
)
def test_normalize_malformed(msg):
    with pytest.raises(MalformedTickError):
        normalize_tick_message(msg, instrument="X", expected_symbol="1HZ75V")


def test_ordering_chronological_and_duplicate_and_stale():
    gate = TickOrderingGate()
    t1 = LiveTick(instrument="I", symbol="S", price=1.0, epoch=100)
    assert gate.accept(t1).accepted
    d = gate.accept(t1)
    assert not d.accepted and d.reason == "duplicate"
    t1b = LiveTick(instrument="I", symbol="S", price=1.1, epoch=100)
    assert gate.accept(t1b).accepted
    t2 = LiveTick(instrument="I", symbol="S", price=2.0, epoch=101)
    assert gate.accept(t2).accepted
    t0 = LiveTick(instrument="I", symbol="S", price=0.5, epoch=99)
    d = gate.accept(t0)
    assert not d.accepted and d.reason == "stale"


def test_m1_ohlc_and_finalization():
    tr = LiveCandleTracker(TIMEFRAME_M1, instrument="I")
    base = 1_700_000_000
    start = (base // 60) * 60
    tr.on_tick(LiveTick("I", "S", 10.0, start + 5))
    tr.on_tick(LiveTick("I", "S", 12.0, start + 10))
    tr.on_tick(LiveTick("I", "S", 9.0, start + 20))
    tr.on_tick(LiveTick("I", "S", 11.0, start + 30))
    assert tr.current is not None
    assert tr.current.open == 10.0
    assert tr.current.high == 12.0
    assert tr.current.low == 9.0
    assert tr.current.close == 11.0
    more = tr.on_tick(LiveTick("I", "S", 13.0, start + 60))
    fin = next(e for e in more if e.kind is CandleEventKind.FINALIZED)
    assert fin.candle.finalized is True
    assert fin.candle.open == 10.0


def test_m15_boundary():
    tr = LiveCandleTracker(TIMEFRAME_M15, instrument="I")
    epoch = int(datetime(2024, 1, 1, 12, 17, 42, tzinfo=UTC).timestamp())
    expected_start = (epoch // 900) * 900
    tr.on_tick(LiveTick("I", "S", 1.0, epoch))
    assert tr.current is not None
    assert tr.current.start_epoch == expected_start


def test_tick_exactly_on_boundary_opens_new_candle():
    tr = LiveCandleTracker(TIMEFRAME_M1, instrument="I")
    start = (1_700_000_060 // 60) * 60
    tr.on_tick(LiveTick("I", "S", 1.0, start))
    ev = tr.on_tick(LiveTick("I", "S", 2.0, start + 60))
    assert any(e.kind is CandleEventKind.FINALIZED for e in ev)
    assert tr.current is not None
    assert tr.current.start_epoch == start + 60


def test_finalized_emitted_once():
    tr = LiveCandleTracker(TIMEFRAME_M1, instrument="I")
    start = 1_700_000_000 // 60 * 60
    tr.on_tick(LiveTick("I", "S", 1.0, start))
    e1 = tr.on_tick(LiveTick("I", "S", 2.0, start + 60))
    e2 = tr.on_tick(LiveTick("I", "S", 3.0, start + 61))
    assert sum(1 for e in e1 if e.kind is CandleEventKind.FINALIZED) == 1
    assert sum(1 for e in e2 if e.kind is CandleEventKind.FINALIZED) == 0


def test_multi_timeframe_same_stream():
    mt = MultiTimeframeLiveCandles("I")
    start = 1_700_000_000 // 900 * 900
    mt.on_tick(LiveTick("I", "S", 1.0, start + 10))
    assert mt.current("M1") is not None
    assert mt.current("M15") is not None


def test_candle_determinism():
    def run():
        tr = LiveCandleTracker(TIMEFRAME_M1, instrument="I")
        start = 1000 * 60
        out = []
        for i, p in enumerate([1.0, 2.0, 0.5, 1.5]):
            out.append(tr.on_tick(LiveTick("I", "S", p, start + i * 10)))
        out.append(tr.on_tick(LiveTick("I", "S", 3.0, start + 60)))
        return [
            (e.kind, e.candle.open, e.candle.high, e.candle.low,
             e.candle.close, e.candle.start_epoch)
            for batch in out
            for e in batch
        ]

    assert run() == run()


def test_state_multi_instrument_isolation():
    s1 = LiveMarketState(instrument="A", symbol="S1")
    s2 = LiveMarketState(instrument="B", symbol="S2")
    s1.record_tick(LiveTick("A", "S1", 1.0, 10))
    assert s1.last_tick is not None
    assert s2.last_tick is None


@pytest.mark.asyncio
async def test_service_stream_ticks_and_candles():
    transport = FakeTickTransport()
    info = make_fake_symbol("Volatility 75 (1s) Index", "1HZ75V")
    svc = LiveMarketDataService(
        "Volatility 75 (1s) Index",
        transport=transport,
        symbol_resolver=lambda _n: info,
        max_reconnect_attempts=2,
    )
    await svc.start()
    assert svc.status().connection is ConnectionState.CONNECTED
    assert transport.subscribe_calls == ["1HZ75V"]
    start = 1_700_000_000 // 60 * 60
    await transport.push(_tick_msg("1HZ75V", 100.0, start + 1))
    await transport.push(_tick_msg("1HZ75V", 101.0, start + 2))
    await transport.push(_tick_msg("1HZ75V", 99.0, start + 60))
    collected: list = []

    async def collect():
        async for item in svc.events():
            collected.append(item)
            if len(collected) >= 5:
                break

    try:
        await asyncio.wait_for(collect(), timeout=2.0)
    finally:
        await svc.stop()
    ticks = [x for x in collected if isinstance(x, LiveTick)]
    assert len(ticks) >= 2
    assert svc.status().connection is ConnectionState.CLOSED


@pytest.mark.asyncio
async def test_malformed_does_not_stop_stream():
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    await svc.start()
    await transport.push({"msg_type": "tick", "tick": {"symbol": "1HZ75V"}})
    await transport.push(_tick_msg("1HZ75V", 1.0, 100))
    got = []

    async def one():
        async for item in svc.events():
            if isinstance(item, LiveTick):
                got.append(item)
                break

    try:
        await asyncio.wait_for(one(), timeout=2.0)
    finally:
        await svc.stop()
    assert len(got) == 1


@pytest.mark.asyncio
async def test_duplicate_subscription_not_created():
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "stpRNG")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    await svc.start()
    await transport.subscribe("stpRNG")
    assert transport.subscribe_calls.count("stpRNG") == 1
    await svc.stop()


@pytest.mark.asyncio
async def test_connect_failure():
    transport = FakeTickTransport()
    transport.set_fail_connect(True)
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    with pytest.raises(ConnectionError):
        await svc.start()


@pytest.mark.asyncio
async def test_graceful_shutdown_cancels_cleanly():
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    await svc.start()
    await svc.stop()
    assert transport.close_calls >= 1
    assert svc.status().connection is ConnectionState.CLOSED


@pytest.mark.asyncio
async def test_stale_tick_dropped_no_candle_event():
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    await svc.start()
    await transport.push(_tick_msg("1HZ75V", 1.0, 200))
    await transport.push(_tick_msg("1HZ75V", 1.0, 150))
    ticks = []

    async def collect_two_seconds():
        async for item in svc.events():
            if isinstance(item, LiveTick):
                ticks.append(item)
            if len(ticks) >= 1:
                await asyncio.sleep(0.05)
                break

    try:
        await asyncio.wait_for(collect_two_seconds(), timeout=2.0)
    finally:
        await svc.stop()
    assert len(ticks) == 1
    assert ticks[0].epoch == 200


def test_livetick_is_genuinely_immutable():
    tick = LiveTick(instrument="I", symbol="S", price=1.5, epoch=100)
    with pytest.raises(FrozenInstanceError):
        tick.price = 2.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        tick.epoch = 200  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        tick.symbol = "OTHER"  # type: ignore[misc]
    assert set(tick.__dataclass_fields__) == {"instrument", "symbol", "price", "epoch"}


@pytest.mark.parametrize(
    "epoch",
    [True, False, 1.9, -1.0, "1.9", "invalid", 1.5, "1.0"],
)
def test_epoch_rejects_non_integer(epoch):
    msg = {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "quote": 1.0, "epoch": epoch}}
    with pytest.raises(MalformedTickError):
        normalize_tick_message(msg, instrument="X", expected_symbol="1HZ75V")


@pytest.mark.parametrize("epoch", [0, 1, 1_700_000_000, "42", 100.0])
def test_epoch_accepts_integers(epoch):
    msg = {"msg_type": "tick", "tick": {"symbol": "1HZ75V", "quote": 1.0, "epoch": epoch}}
    t = normalize_tick_message(msg, instrument="X", expected_symbol="1HZ75V")
    assert isinstance(t.epoch, int)
    assert not isinstance(t.epoch, bool)


@pytest.mark.asyncio
async def test_subscription_id_is_server_provided_not_req_id():
    transport = FakeTickTransport()
    await transport.connect()
    await transport.subscribe("1HZ75V")
    sid = transport.subscription_id("1HZ75V")
    assert sid is not None
    assert sid == "srv-sub-1"
    await transport.unsubscribe("1HZ75V")
    assert transport.forget_ids == ["srv-sub-1"]
    assert transport.unsubscribe_calls == [("1HZ75V", "srv-sub-1")]
    assert transport.subscription_id("1HZ75V") is None


@pytest.mark.asyncio
async def test_unsubscribe_before_first_tick():
    transport = FakeTickTransport()
    await transport.connect()
    await transport.subscribe("stpRNG")
    assert transport.subscription_id("stpRNG") == "srv-sub-1"
    await transport.unsubscribe("stpRNG")
    assert transport.forget_ids == ["srv-sub-1"]
    assert transport.subscription_id("stpRNG") is None


@pytest.mark.asyncio
async def test_reconnect_gets_fresh_subscription_id():
    transport = FakeTickTransport()
    await transport.connect()
    await transport.subscribe("1HZ75V")
    first = transport.subscription_id("1HZ75V")
    await transport.unsubscribe("1HZ75V")
    await transport.subscribe("1HZ75V")
    second = transport.subscription_id("1HZ75V")
    assert first == "srv-sub-1"
    assert second == "srv-sub-2"
    assert first != second


@pytest.mark.asyncio
async def test_repeated_subscribe_no_duplicate():
    transport = FakeTickTransport()
    await transport.connect()
    await transport.subscribe("1HZ75V")
    await transport.subscribe("1HZ75V")
    await transport.subscribe("1HZ75V")
    assert transport.subscribe_calls == ["1HZ75V"]
    assert transport.subscription_id("1HZ75V") == "srv-sub-1"


@pytest.mark.asyncio
async def test_shutdown_uses_server_subscription_id():
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X", transport=transport, symbol_resolver=lambda _n: info
    )
    await svc.start()
    sid_before = transport.subscription_id("1HZ75V")
    assert sid_before == "srv-sub-1"
    await svc.stop()
    assert "srv-sub-1" in transport.forget_ids


def test_candle_tracker_state_remains_bounded():
    tr = LiveCandleTracker(TIMEFRAME_M1, instrument="I")
    start = 1_700_000_000 // 60 * 60
    n_minutes = 5_000
    finalized_count = 0
    for m in range(n_minutes):
        events = tr.on_tick(LiveTick("I", "S", float(m), start + m * 60))
        finalized_count += sum(1 for e in events if e.kind is CandleEventKind.FINALIZED)
    assert finalized_count == n_minutes - 1
    assert not hasattr(tr, "_finalized_starts")
    assert tr._last_finalized_start is not None
    assert isinstance(tr._last_finalized_start, int)
    more = tr.on_tick(LiveTick("I", "S", 99.0, start + (n_minutes - 1) * 60 + 30))
    assert sum(1 for e in more if e.kind is CandleEventKind.FINALIZED) == 0


@pytest.mark.asyncio
async def test_reconnect_resumes_live_stream():
    """close() sentinel must not poison the post-reconnect message stream."""
    transport = FakeTickTransport()
    info = make_fake_symbol("X", "1HZ75V")
    svc = LiveMarketDataService(
        "X",
        transport=transport,
        symbol_resolver=lambda _n: info,
        max_reconnect_attempts=3,
    )
    await svc.start()
    assert transport.subscription_id("1HZ75V") == "srv-sub-1"

    await transport.push(_tick_msg("1HZ75V", 10.0, 1000))
    first: list[LiveTick] = []

    async def take_one(bucket: list) -> None:
        async for item in svc.events():
            if isinstance(item, LiveTick):
                bucket.append(item)
                break

    await asyncio.wait_for(take_one(first), timeout=2.0)
    assert first[0].price == 10.0

    # Simulate underlying stream death (reader exit / disconnect).
    await transport.push(None)

    deadline = asyncio.get_event_loop().time() + 5.0
    while asyncio.get_event_loop().time() < deadline:
        if transport.subscription_id("1HZ75V") == "srv-sub-2":
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("did not obtain fresh subscription after reconnect")

    assert transport.connect_calls >= 2
    assert "srv-sub-1" in transport.forget_ids

    await transport.push(_tick_msg("1HZ75V", 20.0, 2000))
    second: list[LiveTick] = []
    await asyncio.wait_for(take_one(second), timeout=3.0)
    assert second[0].price == 20.0
    assert second[0].epoch == 2000

    await svc.stop()
