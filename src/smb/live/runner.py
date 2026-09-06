"""Live strategy + simulation runner (Milestone 4B).

Orchestrates 4A live market data -> existing 2A strategy -> 2B risk -> 2C
simulation. Does **not** place real or demo trades.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from smb.live.models import CandleEvent, CandleEventKind, LiveTick
from smb.live.records import (
    LiveEventKind,
    LiveResearchRecord,
    LiveSignalRecord,
    LiveTradeClosedRecord,
    LiveTradeOpenedRecord,
    signal_identity,
)
from smb.live.sim_session import LiveSimulationSession
from smb.live.stream import LiveMarketDataService
from smb.market.candles import Candle
from smb.simulation.models import SimulationConfig
from smb.strategy.engine import StrategyEngine
from smb.strategy.models import StrategyConfig, StrategySignal
from smb.trade.constructor import TradeConstructor
from smb.trade.models import RiskContext, TradeConfig

logger = logging.getLogger(__name__)

RecordSink = Callable[[LiveResearchRecord], None]


@dataclass
class LiveRunnerConfig:
    """Orchestration policy for the live research runner."""

    max_open_simulations: int = 1
    max_processed_signal_ids: int = 4096
    max_records_in_memory: int = 10_000
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    risk_equity: float = 10_000.0
    finalize_open_on_stop: bool = True

    def __post_init__(self) -> None:
        if self.max_open_simulations < 0:
            raise ValueError("max_open_simulations must be >= 0")
        if self.max_processed_signal_ids < 1:
            raise ValueError("max_processed_signal_ids must be >= 1")
        if self.max_records_in_memory < 1:
            raise ValueError("max_records_in_memory must be >= 1")
        if self.risk_equity <= 0:
            raise ValueError("risk_equity must be > 0")


class LiveStrategyRunner:
    """Continuously consume 4A events and drive strategy -> risk -> simulation.

    Strategy evaluation runs only on finalized M1 candles. Forming-candle
    UPDATE events never trigger strategy decisions.
    """

    def __init__(
        self,
        market: LiveMarketDataService,
        *,
        config: LiveRunnerConfig | None = None,
        record_sink: RecordSink | None = None,
    ) -> None:
        self._market = market
        self.config = config if config is not None else LiveRunnerConfig()
        self._record_sink = record_sink
        self._strategy: StrategyEngine | None = None
        self._constructor = TradeConstructor(self.config.trade)
        self._risk = RiskContext(equity=self.config.risk_equity)
        self._open_sessions: dict[tuple[str, int, str], LiveSimulationSession] = {}
        self._processed_ids: deque[tuple[str, int, str]] = deque(
            maxlen=self.config.max_processed_signal_ids
        )
        self._processed_set: set[tuple[str, int, str]] = set()
        self._records: deque[LiveResearchRecord] = deque(
            maxlen=self.config.max_records_in_memory
        )
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._stop_requested = False
        self._accepting_signals = True
        self._last_tick_epoch: int | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def open_session_count(self) -> int:
        return len(self._open_sessions)

    @property
    def records(self) -> Sequence[LiveResearchRecord]:
        return tuple(self._records)

    @property
    def processed_signal_count(self) -> int:
        return len(self._processed_set)

    @property
    def strategy(self) -> StrategyEngine | None:
        return self._strategy

    async def start(self) -> None:
        if self._running:
            return
        self._stop_requested = False
        self._accepting_signals = True
        await self._market.start()
        state = self._market.state
        if state is None:
            raise RuntimeError("market service has no state after start")
        self._strategy = StrategyEngine(
            instrument=state.instrument,
            config=self.config.strategy,
        )
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="live-strategy-runner")

    async def stop(self) -> None:
        self._stop_requested = True
        self._accepting_signals = False
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.config.finalize_open_on_stop and self._open_sessions:
            for key, session in list(self._open_sessions.items()):
                result = session.force_close_at_horizon()
                self._emit(
                    LiveTradeClosedRecord(
                        kind=LiveEventKind.TRADE_CLOSED,
                        instrument=session.candidate.instrument,
                        signal_epoch=session.candidate.signal_epoch,
                        direction=session.candidate.direction,
                        candidate=session.candidate,
                        result=result,
                        closed_at_epoch=self._last_tick_epoch or session.horizon_end,
                    )
                )
                del self._open_sessions[key]
        await self._market.stop()

    async def _run_loop(self) -> None:
        try:
            async for event in self._market.events():
                if self._stop_requested:
                    break
                try:
                    self._handle_event(event)
                except Exception:
                    logger.exception("Error handling live event; continuing")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Live runner loop failed")

    def _handle_event(self, event: LiveTick | CandleEvent) -> None:
        if isinstance(event, LiveTick):
            self._on_tick(event)
        elif isinstance(event, CandleEvent):
            self._on_candle_event(event)

    def _on_tick(self, tick: LiveTick) -> None:
        self._last_tick_epoch = tick.epoch
        hist = tick.as_history_tick()
        closed_keys: list[tuple[str, int, str]] = []
        for key, session in self._open_sessions.items():
            if tick.instrument != session.candidate.instrument:
                continue
            result = session.on_tick(hist)
            if result is not None:
                self._emit(
                    LiveTradeClosedRecord(
                        kind=LiveEventKind.TRADE_CLOSED,
                        instrument=session.candidate.instrument,
                        signal_epoch=session.candidate.signal_epoch,
                        direction=session.candidate.direction,
                        candidate=session.candidate,
                        result=result,
                        closed_at_epoch=tick.epoch,
                    )
                )
                closed_keys.append(key)
            elif tick.epoch > session.horizon_end:
                result = session.force_close_at_horizon()
                self._emit(
                    LiveTradeClosedRecord(
                        kind=LiveEventKind.TRADE_CLOSED,
                        instrument=session.candidate.instrument,
                        signal_epoch=session.candidate.signal_epoch,
                        direction=session.candidate.direction,
                        candidate=session.candidate,
                        result=result,
                        closed_at_epoch=tick.epoch,
                    )
                )
                closed_keys.append(key)
        for key in closed_keys:
            self._open_sessions.pop(key, None)

    def _on_candle_event(self, event: CandleEvent) -> None:
        if event.kind is CandleEventKind.UPDATE:
            return
        if event.kind is not CandleEventKind.FINALIZED:
            return
        if self._strategy is None:
            return
        candle = event.candle
        if candle.timeframe == "M15":
            self._strategy.on_m15(candle)
            return
        if candle.timeframe == "M1":
            if not self._accepting_signals:
                self._strategy.on_m1(candle)
                return
            signals = self._strategy.on_m1(candle)
            for signal in signals:
                self._process_signal(signal, candle)

    def _process_signal(self, signal: StrategySignal, candle: Candle) -> None:
        sid = signal_identity(signal)
        if sid in self._processed_set:
            self._emit(
                LiveSignalRecord(
                    kind=LiveEventKind.SIGNAL_DUPLICATE,
                    instrument=signal.instrument,
                    signal_epoch=signal.signal_epoch,
                    direction=signal.direction,
                    signal=signal,
                    metadata={"reason": "duplicate_identity"},
                )
            )
            return

        self._remember_signal_id(sid)
        self._emit(
            LiveSignalRecord(
                kind=LiveEventKind.SIGNAL_GENERATED,
                instrument=signal.instrument,
                signal_epoch=signal.signal_epoch,
                direction=signal.direction,
                signal=signal,
            )
        )

        construction = self._constructor.construct(signal, self._risk)
        if not construction.accepted or construction.trade is None:
            self._emit(
                LiveSignalRecord(
                    kind=LiveEventKind.SIGNAL_REJECTED,
                    instrument=signal.instrument,
                    signal_epoch=signal.signal_epoch,
                    direction=signal.direction,
                    signal=signal,
                    rejection_reason=construction.rejection_reason,
                )
            )
            return

        if len(self._open_sessions) >= self.config.max_open_simulations:
            self._emit(
                LiveSignalRecord(
                    kind=LiveEventKind.SIGNAL_REJECTED,
                    instrument=signal.instrument,
                    signal_epoch=signal.signal_epoch,
                    direction=signal.direction,
                    signal=signal,
                    rejection_reason=None,
                    metadata={
                        "reason": "max_open_simulations",
                        "limit": self.config.max_open_simulations,
                    },
                )
            )
            return

        candidate = construction.trade
        session = LiveSimulationSession(candidate, self.config.simulation)
        self._open_sessions[sid] = session
        opened_at = candle.end_epoch
        self._emit(
            LiveTradeOpenedRecord(
                kind=LiveEventKind.TRADE_OPENED,
                instrument=candidate.instrument,
                signal_epoch=candidate.signal_epoch,
                direction=candidate.direction,
                candidate=candidate,
                opened_at_epoch=opened_at,
            )
        )

    def _remember_signal_id(self, sid: tuple[str, int, str]) -> None:
        if len(self._processed_ids) == self._processed_ids.maxlen:
            old = self._processed_ids[0]
            self._processed_set.discard(old)
        self._processed_ids.append(sid)
        self._processed_set.add(sid)

    def _emit(self, record: LiveResearchRecord) -> None:
        self._records.append(record)
        if self._record_sink is not None:
            try:
                self._record_sink(record)
            except Exception:
                logger.exception("record_sink failed")

    def handle_event_for_tests(self, event: LiveTick | CandleEvent) -> None:
        """Process one event without the async loop (unit tests)."""
        if self._strategy is None:
            instrument = (
                event.instrument
                if isinstance(event, LiveTick)
                else event.instrument
            )
            self._strategy = StrategyEngine(
                instrument=instrument,
                config=self.config.strategy,
            )
            self._accepting_signals = True
        self._handle_event(event)

    def inject_finalized_m15(self, instrument: str, candle: Candle) -> None:
        self.handle_event_for_tests(
            CandleEvent(CandleEventKind.FINALIZED, instrument, candle)
        )

    def inject_finalized_m1(
        self, instrument: str, candle: Candle
    ) -> list[StrategySignal]:
        before = len(self._records)
        self.handle_event_for_tests(
            CandleEvent(CandleEventKind.FINALIZED, instrument, candle)
        )
        out: list[StrategySignal] = []
        for rec in list(self._records)[before:]:
            if (
                isinstance(rec, LiveSignalRecord)
                and rec.kind is LiveEventKind.SIGNAL_GENERATED
            ):
                out.append(rec.signal)
        return out
