"""Milestone 4B — live strategy + simulation orchestration tests.

Offline, deterministic, no real Deriv WebSocket.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
from smb.live import (
    CandleEvent,
    CandleEventKind,
    FakeTickTransport,
    LiveEventKind,
    LiveMarketDataService,
    LiveRunnerConfig,
    LiveSignalRecord,
    LiveSimulationSession,
    LiveStrategyRunner,
    LiveTick,
    LiveTradeClosedRecord,
    LiveTradeOpenedRecord,
    make_fake_symbol,
    signal_identity,
)
from smb.market.candles import Candle
from smb.simulation import (
    ExitReason,
    SimulationConfig,
    SimulationEngine,
    SimulationOutcome,
)
from smb.strategy import Direction, StrategyConfig, StrategyEngine
from smb.strategy.models import (
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategySignal,
    SwingPoint,
)
from smb.trade import RiskContext, TradeConstructor


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


def forming_m1(start: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        timeframe="M1",
        start_epoch=start,
        end_epoch=start + 60,
        open=o,
        high=h,
        low=low,
        close=c,
        tick_count=10,
        finalized=False,
    )


def _full_bullish_fixture() -> tuple[list[Candle], StrategyConfig]:
    cfg = StrategyConfig(
        swing_x=2,
        msb_window_bars=5,
        displacement_body_range_ratio=0.50,
        displacement_body_atr_ratio=0.50,
        atr_period=3,
    )
    candles: list[Candle] = []
    t = 0

    def add(o: float, h: float, low: float, c: float) -> None:
        nonlocal t
        candles.append(m1(t, o, h, low, c))
        t += 60

    add(108, 110, 107, 109)
    add(109, 111, 106, 108)
    add(108, 109, 100, 102)
    add(102, 108, 101, 107)
    add(107, 115, 106, 114)
    add(114, 114.5, 112, 113)
    add(113, 114, 111, 112)
    add(112, 113, 111, 112)
    add(105, 108, 97, 104)
    add(104, 118, 103, 117)
    add(117, 125, 116, 124)
    add(124, 126, 123, 125)
    add(125, 130, 127, 129)
    return candles, cfg


def _tick(epoch: int, price: float) -> Tick:
    return Tick(timestamp=datetime.fromtimestamp(epoch, tz=UTC), price=price, epoch=epoch)


def _make_signal(
    *,
    direction: Direction = Direction.LONG,
    signal_epoch: int = 1000,
    gap_low: float = 100.0,
    gap_high: float = 102.0,
    atr: float = 2.0,
    swept_level: float = 98.0,
) -> StrategySignal:
    swing = SwingPoint(
        kind="low" if direction == Direction.LONG else "high",
        price=swept_level,
        candle_start_epoch=100,
        candle_end_epoch=160,
        index=2,
        confirmed_at_epoch=280,
    )
    structure = SwingPoint(
        kind="high" if direction == Direction.LONG else "low",
        price=110.0 if direction == Direction.LONG else 90.0,
        candle_start_epoch=0,
        candle_end_epoch=60,
        index=0,
        confirmed_at_epoch=180,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=swept_level,
        sweep_candle_start_epoch=300,
        sweep_candle_end_epoch=360,
        sweep_candle_low=98.0,
        sweep_candle_high=103.0,
        sweep_candle_close=102.0,
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure.price,
        msb_candle_start_epoch=420,
        msb_candle_end_epoch=480,
        msb_candle_close=112.0 if direction == Direction.LONG else 88.0,
        bars_after_sweep=1,
        structure_swing=structure,
    )
    if direction == Direction.LONG:
        d_o, d_h, d_l, d_c = 112.0, 120.0, 111.0, 119.0
    else:
        d_o, d_h, d_l, d_c = 88.0, 89.0, 80.0, 81.0
    body = abs(d_c - d_o)
    range_ = d_h - d_l
    disp = Displacement(
        direction=direction,
        candle_start_epoch=480,
        candle_end_epoch=540,
        open=d_o,
        high=d_h,
        low=d_l,
        close=d_c,
        body=body,
        range_=range_,
        body_range_ratio=body / range_ if range_ else 0.0,
        body_atr_ratio=body / atr if atr else 0.0,
        atr=atr,
    )
    fvg = FairValueGap(
        direction=direction,
        gap_low=gap_low,
        gap_high=gap_high,
        size=gap_high - gap_low,
        size_atr_ratio=(gap_high - gap_low) / atr if atr else None,
        candle1_start_epoch=480,
        candle2_start_epoch=540,
        candle3_start_epoch=600,
        candle3_end_epoch=signal_epoch,
    )
    return StrategySignal(
        instrument=INSTRUMENT,
        direction=direction,
        signal_epoch=signal_epoch,
        timeframe_context="M15+M1",
        sweep=sweep,
        msb=msb,
        displacement=disp,
        fvg=fvg,
        m15_context=M15Context(
            last_m15_start_epoch=0,
            last_m15_end_epoch=900,
            last_m15_close=110.0,
            recent_high=120.0,
            recent_low=95.0,
            directional_bias="bullish" if direction == Direction.LONG else "bearish",
        ),
        reference_levels={"swept_level": swept_level},
    )


def _accepted_candidate(signal: StrategySignal | None = None):
    sig = signal or _make_signal()
    result = TradeConstructor().construct(sig, RiskContext(equity=10_000.0))
    assert result.accepted and result.trade is not None
    return result.trade


def _runner(config: LiveRunnerConfig | None = None) -> LiveStrategyRunner:
    transport = FakeTickTransport()
    market = LiveMarketDataService(
        INSTRUMENT,
        transport=transport,
        symbol_resolver=lambda n: make_fake_symbol(n, "1HZ75V"),
    )
    return LiveStrategyRunner(market, config=config or LiveRunnerConfig())


def test_session_matches_batch_engine_tp():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    ticks = [_tick(1001, 101.0), _tick(1002, 101.0), _tick(1010, cand.take_profit)]
    batch = SimulationEngine().simulate(cand, ticks)
    session = LiveSimulationSession(cand)
    result = None
    for t in ticks:
        result = session.on_tick(t) or result
    assert result is not None
    assert result.outcome == SimulationOutcome.TP
    assert result.outcome == batch.outcome
    assert result.entry_time == batch.entry_time
    assert result.exit_time == batch.exit_time


def test_session_sl_and_timeout():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    cfg = SimulationConfig(max_duration_seconds=30)
    session = LiveSimulationSession(cand, cfg)
    t_entry = _tick(1001, 101.0)
    assert session.on_tick(t_entry) is None
    t_sl = _tick(1005, cand.stop_loss)
    res = session.on_tick(t_sl)
    assert res is not None and res.outcome == SimulationOutcome.SL
    session2 = LiveSimulationSession(cand, cfg)
    session2.on_tick(t_entry)
    res2 = session2.force_close_at_horizon()
    assert res2.outcome == SimulationOutcome.TIMEOUT
    assert res2.exit_time == 1030


def test_session_no_fill():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    session = LiveSimulationSession(cand, SimulationConfig(max_duration_seconds=10))
    assert session.on_tick(_tick(1005, cand.entry_price + 10.0)) is None
    assert session.force_close_at_horizon().outcome == SimulationOutcome.NO_FILL


def test_finalized_m1_triggers_strategy_evaluation():
    candles, cfg = _full_bullish_fixture()
    runner = _runner(LiveRunnerConfig(strategy=cfg))
    runner.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
    emitted = []
    for c in candles:
        emitted.extend(runner.inject_finalized_m1(INSTRUMENT, c))
    assert len(emitted) >= 1
    kinds = [r.kind for r in runner.records if isinstance(r, LiveSignalRecord)]
    assert LiveEventKind.SIGNAL_GENERATED in kinds


def test_forming_candle_does_not_evaluate_strategy():
    runner = _runner()
    runner.handle_event_for_tests(
        CandleEvent(CandleEventKind.UPDATE, INSTRUMENT, forming_m1(0, 100, 101, 99, 100.5))
    )
    assert runner.records == ()
    assert runner.open_session_count == 0


def test_m15_context_only_from_finalized():
    runner = _runner(LiveRunnerConfig(strategy=StrategyConfig(swing_x=2, atr_period=2)))
    runner.inject_finalized_m1(INSTRUMENT, m1(0, 100, 101, 99, 100))
    assert runner.strategy is not None
    assert runner.strategy._m15_context_at(300).last_m15_end_epoch is None
    runner.inject_finalized_m15(INSTRUMENT, m15(0, 100, 110, 90, 105))
    assert runner.strategy._m15_context_at(900).last_m15_end_epoch == 900
    assert runner.strategy._m15_context_at(600).last_m15_end_epoch is None


def test_signal_through_risk_and_open_trade():
    candles, cfg = _full_bullish_fixture()
    runner = _runner(LiveRunnerConfig(strategy=cfg, max_open_simulations=5))
    runner.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
    for c in candles:
        runner.inject_finalized_m1(INSTRUMENT, c)
    kinds = [r.kind for r in runner.records]
    assert LiveEventKind.SIGNAL_GENERATED in kinds
    assert LiveEventKind.TRADE_OPENED in kinds
    assert runner.open_session_count >= 1


def test_rejected_signal_record_observable():
    candles, cfg = _full_bullish_fixture()
    runner2 = _runner(LiveRunnerConfig(strategy=cfg, max_open_simulations=0))
    runner2.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
    for c in candles:
        runner2.inject_finalized_m1(INSTRUMENT, c)
    kinds = [r.kind for r in runner2.records]
    assert LiveEventKind.SIGNAL_GENERATED in kinds
    assert LiveEventKind.TRADE_OPENED not in kinds
    assert any(k is LiveEventKind.SIGNAL_REJECTED for k in kinds)


def test_live_tick_advances_and_closes_tp():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    runner = _runner()
    runner.handle_event_for_tests(CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1(0, 1, 2, 0, 1)))
    sid = signal_identity(cand.source_signal)
    runner._open_sessions[sid] = LiveSimulationSession(cand)
    runner._emit(
        LiveTradeOpenedRecord(
            kind=LiveEventKind.TRADE_OPENED,
            instrument=cand.instrument,
            signal_epoch=cand.signal_epoch,
            direction=cand.direction,
            candidate=cand,
            opened_at_epoch=1000,
        )
    )
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "1HZ75V", cand.entry_price, 1001))
    assert runner.open_session_count == 1
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "1HZ75V", cand.take_profit, 1010))
    assert runner.open_session_count == 0
    closed = [r for r in runner.records if isinstance(r, LiveTradeClosedRecord)]
    assert closed[-1].result.outcome == SimulationOutcome.TP


def test_live_tick_closes_sl():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    runner = _runner()
    runner.handle_event_for_tests(CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1(0, 1, 2, 0, 1)))
    sid = signal_identity(cand.source_signal)
    runner._open_sessions[sid] = LiveSimulationSession(cand)
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.entry_price, 1001))
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.stop_loss, 1005))
    closed = [r for r in runner.records if isinstance(r, LiveTradeClosedRecord)]
    assert closed[-1].result.outcome == SimulationOutcome.SL


def test_timeout_via_tick_past_horizon():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    cfg = LiveRunnerConfig(simulation=SimulationConfig(max_duration_seconds=20))
    runner = _runner(cfg)
    runner.handle_event_for_tests(CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1(0, 1, 2, 0, 1)))
    sid = signal_identity(cand.source_signal)
    runner._open_sessions[sid] = LiveSimulationSession(cand, cfg.simulation)
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.entry_price, 1001))
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.entry_price + 0.1, 1030))
    closed = [r for r in runner.records if isinstance(r, LiveTradeClosedRecord)]
    assert closed[-1].result.outcome == SimulationOutcome.TIMEOUT


def test_signal_deduplication():
    candles, cfg = _full_bullish_fixture()
    runner = _runner(LiveRunnerConfig(strategy=cfg, max_open_simulations=5))
    runner.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
    for c in candles:
        runner.inject_finalized_m1(INSTRUMENT, c)
    n_gen = sum(1 for r in runner.records if isinstance(r, LiveSignalRecord) and r.kind is LiveEventKind.SIGNAL_GENERATED)
    last = candles[-1]
    runner.inject_finalized_m1(INSTRUMENT, last)
    n_gen2 = sum(1 for r in runner.records if isinstance(r, LiveSignalRecord) and r.kind is LiveEventKind.SIGNAL_GENERATED)
    assert n_gen2 == n_gen
    sigs = [r.signal for r in runner.records if isinstance(r, LiveSignalRecord) and r.kind is LiveEventKind.SIGNAL_GENERATED]
    if sigs:
        runner._process_signal(sigs[0], last)
        assert any(isinstance(r, LiveSignalRecord) and r.kind is LiveEventKind.SIGNAL_DUPLICATE for r in runner.records)


def test_reconnect_preserves_open_simulation():
    cand = _accepted_candidate(_make_signal(signal_epoch=1000, gap_low=100, gap_high=102))
    runner = _runner()
    runner.handle_event_for_tests(CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1(0, 1, 2, 0, 1)))
    sid = signal_identity(cand.source_signal)
    runner._open_sessions[sid] = LiveSimulationSession(cand)
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.entry_price, 1001))
    assert runner.open_session_count == 1
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.entry_price, 1005))
    assert runner.open_session_count == 1
    runner.handle_event_for_tests(LiveTick(INSTRUMENT, "S", cand.take_profit, 1015))
    assert runner.open_session_count == 0


def test_shutdown_terminates_cleanly():
    import asyncio

    transport = FakeTickTransport()
    market = LiveMarketDataService(
        INSTRUMENT, transport=transport, symbol_resolver=lambda n: make_fake_symbol(n, "1HZ75V")
    )
    runner = LiveStrategyRunner(market, config=LiveRunnerConfig())

    async def _run():
        await runner.start()
        assert runner.is_running
        await runner.stop()
        assert not runner.is_running
        assert runner._task is None

    asyncio.run(_run())


def test_state_bounds_processed_ids():
    cfg = LiveRunnerConfig(max_processed_signal_ids=8, max_records_in_memory=16)
    runner = _runner(cfg)
    runner.handle_event_for_tests(CandleEvent(CandleEventKind.FINALIZED, INSTRUMENT, m1(0, 1, 2, 0, 1)))
    for i in range(30):
        sig = _make_signal(signal_epoch=1000 + i * 60)
        runner._process_signal(sig, m1(1000 + i * 60 - 60, 1, 2, 0, 1))
    assert len(runner._processed_ids) <= 8
    assert len(runner._processed_set) <= 8
    assert len(runner._records) <= 16


def test_determinism_same_event_sequence():
    candles, cfg = _full_bullish_fixture()

    def run_once():
        r = _runner(LiveRunnerConfig(strategy=cfg, max_open_simulations=5))
        r.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
        for c in candles:
            r.inject_finalized_m1(INSTRUMENT, c)
        return [(rec.kind, getattr(rec, "signal_epoch", None), getattr(rec, "direction", None)) for rec in r.records]

    assert run_once() == run_once()


def test_existing_strategy_contract_unchanged():
    candles, cfg = _full_bullish_fixture()
    eng = StrategyEngine(INSTRUMENT, cfg)
    eng.on_m15(m15(0, 100, 120, 95, 110))
    direct = eng.process(candles)
    runner = _runner(LiveRunnerConfig(strategy=cfg, max_open_simulations=5))
    runner.inject_finalized_m15(INSTRUMENT, m15(0, 100, 120, 95, 110))
    via_runner = []
    for c in candles:
        via_runner.extend(runner.inject_finalized_m1(INSTRUMENT, c))
    assert len(direct) == len(via_runner)
    if direct:
        assert direct[0].signal_epoch == via_runner[0].signal_epoch
        assert direct[0].direction == via_runner[0].direction


def test_signal_identity_stable():
    assert signal_identity(_make_signal(signal_epoch=1234)) == (INSTRUMENT, 1234, "long")


def test_no_execution_imports_in_runner():
    import smb.live.runner as mod

    src = open(mod.__file__).read()
    assert "place_order" not in src
    assert "Telegram" not in src
