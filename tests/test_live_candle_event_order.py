"""Regression: M15 FINALIZED before M1 at shared boundary (4B correctness)."""

from __future__ import annotations

from smb.live import (
    CandleEvent,
    CandleEventKind,
    LiveRunnerConfig,
    LiveStrategyRunner,
    LiveTick,
    MultiTimeframeLiveCandles,
    FakeTickTransport,
    LiveMarketDataService,
    make_fake_symbol,
    order_candle_events,
)
from smb.market.candles import Candle
from smb.strategy import StrategyConfig


INSTRUMENT = "vol75"


def m1(start: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        timeframe="M1",
        start_epoch=start,
        end_epoch=start + 60,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_count=60,
        finalized=True,
    )


def m15(start: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        timeframe="M15",
        start_epoch=start,
        end_epoch=start + 900,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_count=900,
        finalized=True,
    )


def test_order_candle_events_m15_before_m1_same_end():
    T = 900
    m15_c = m15(0, 100, 110, 90, 105)
    m1_c = m1(T - 60, 105, 106, 104, 105.5)
    # Intentionally reverse tracker order (M1 first).
    events = order_candle_events(
        [
            CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1_c),
            CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m15_c),
        ]
    )
    assert [e.candle.timeframe for e in events] == ["M15", "M1"]


def test_multi_timeframe_feed_m15_before_m1_at_boundary():
    mt = MultiTimeframeLiveCandles("I")
    start = 1_700_000_000 // 900 * 900
    for i in range(0, 900, 60):
        mt.on_tick(LiveTick("I", "S", 100.0, start + i + 1))
    boundary = start + 900
    events = mt.on_tick(LiveTick("I", "S", 101.0, boundary))
    fins = [
        e
        for e in events
        if e.kind is CandleEventKind.FINALIZED and e.candle.end_epoch == boundary
    ]
    assert [e.candle.timeframe for e in fins] == ["M15", "M1"]


def test_runner_m1_decision_at_boundary_sees_m15_context():
    transport = FakeTickTransport()
    market = LiveMarketDataService(
        INSTRUMENT,
        transport=transport,
        symbol_resolver=lambda n: make_fake_symbol(n, "1HZ75V"),
    )
    runner = LiveStrategyRunner(
        market, config=LiveRunnerConfig(strategy=StrategyConfig(swing_x=2, atr_period=2))
    )
    T = 900
    m15_c = m15(0, 100, 110, 90, 105)
    m1_c = m1(T - 60, 105, 106, 104, 105.5)
    events = order_candle_events(
        [
            CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1_c),
            CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m15_c),
        ]
    )
    assert events[0].candle.timeframe == "M15"
    for ev in events:
        runner.handle_event_for_tests(ev)
    assert runner.strategy is not None
    ctx = runner.strategy._m15_context_at(T)
    assert ctx.last_m15_end_epoch == T
    assert ctx.last_m15_close == 105.0
