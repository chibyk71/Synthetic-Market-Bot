"""Live market-data service: connect, subscribe, normalize, candles, reconnect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from smb.deriv.symbols import SymbolInfo, load_active_symbols, resolve_symbol
from smb.live.candle_feed import MultiTimeframeLiveCandles
from smb.live.models import (
    CandleEvent,
    CandleEventKind,
    ConnectionState,
    LiveTick,
    StreamStatus,
)
from smb.live.normalize import MalformedTickError, normalize_tick_message
from smb.live.ordering import TickOrderingGate
from smb.live.state import LiveMarketState
from smb.live.transport import DerivTickTransport, TickTransport

logger = logging.getLogger(__name__)

_BACKOFF_SCHEDULE = (0.5, 1.0, 2.0, 4.0, 8.0)
_MAX_BACKOFF = 8.0


def _backoff_delay(attempt: int) -> float:
    if attempt < 0:
        attempt = 0
    if attempt < len(_BACKOFF_SCHEDULE):
        return _BACKOFF_SCHEDULE[attempt]
    return _MAX_BACKOFF


class LiveMarketDataService:
    def __init__(
        self,
        instrument_name: str,
        *,
        transport: TickTransport | None = None,
        symbol_resolver: Callable[[str], Any] | None = None,
        max_reconnect_attempts: int = 5,
    ) -> None:
        self._instrument_name = instrument_name
        self._transport: TickTransport = transport or DerivTickTransport()
        self._symbol_resolver = symbol_resolver
        self._max_reconnect = max_reconnect_attempts
        self._symbol_info: SymbolInfo | None = None
        self._state: LiveMarketState | None = None
        self._gate = TickOrderingGate()
        self._candles: MultiTimeframeLiveCandles | None = None
        self._task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[LiveTick | CandleEvent | None] = asyncio.Queue()
        self._closed = False
        self._running = False

    @property
    def state(self) -> LiveMarketState | None:
        return self._state

    def status(self) -> StreamStatus:
        if self._state is None:
            return StreamStatus(
                connection=ConnectionState.DISCONNECTED,
                instrument=None,
                symbol=None,
                last_tick_epoch=None,
                last_tick_price=None,
                subscribed=False,
            )
        return self._state.status()

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("service is closed")
        if self._running:
            return
        self._running = True
        await self._connect_and_subscribe()
        self._task = asyncio.create_task(self._run_loop(), name="live-market-data")

    async def stop(self) -> None:
        self._closed = True
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            if self._symbol_info is not None:
                await self._transport.unsubscribe(self._symbol_info.symbol)
        except Exception:
            logger.debug("unsubscribe on stop failed", exc_info=True)
        await self._transport.close()
        if self._state is not None:
            self._state.connection = ConnectionState.CLOSED
            self._state.subscribed = False
        await self._event_queue.put(None)

    async def events(self) -> AsyncIterator[LiveTick | CandleEvent]:
        while True:
            item = await self._event_queue.get()
            if item is None:
                return
            yield item

    async def _connect_and_subscribe(self) -> None:
        if self._state is not None:
            self._state.connection = ConnectionState.CONNECTING
        await self._transport.connect()
        info = await self._resolve_symbol()
        self._symbol_info = info
        if self._state is None:
            self._state = LiveMarketState(instrument=info.name, symbol=info.symbol)
            self._candles = MultiTimeframeLiveCandles(info.name)
        else:
            self._state.symbol = info.symbol
            self._state.instrument = info.name
        self._state.connection = ConnectionState.CONNECTED
        await self._transport.subscribe(info.symbol)
        self._state.subscribed = True

    async def _resolve_symbol(self) -> SymbolInfo:
        if self._symbol_resolver is not None:
            result = self._symbol_resolver(self._instrument_name)
            if asyncio.iscoroutine(result):
                result = await result
            if not isinstance(result, SymbolInfo):
                raise TypeError("symbol_resolver must return SymbolInfo")
            return result
        from smb.deriv.client import DerivClient

        async with DerivClient() as client:
            symbols = await load_active_symbols(client, detail="full")
            return resolve_symbol(self._instrument_name, symbols)

    async def _run_loop(self) -> None:
        assert self._state is not None and self._candles is not None
        reconnect_attempt = 0
        while self._running and not self._closed:
            try:
                async for msg in self._transport.messages():
                    if self._closed:
                        return
                    await self._handle_message(msg)
                    reconnect_attempt = 0
                # Message stream ended — reconnect unless shutting down.
                if self._closed or not self._running:
                    return
                logger.warning("Live message stream ended; attempting reconnect")
                self._state.connection = ConnectionState.RECONNECTING
                self._state.subscribed = False
                reconnected = await self._reconnect_with_backoff(reconnect_attempt)
                if not reconnected:
                    self._state.connection = ConnectionState.DISCONNECTED
                    await self._event_queue.put(None)
                    return
                reconnect_attempt += 1
                self._state.reconnect_count += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed or not self._running:
                    return
                logger.warning("Live stream error: %s", exc)
                self._state.connection = ConnectionState.RECONNECTING
                self._state.subscribed = False
                reconnected = await self._reconnect_with_backoff(reconnect_attempt)
                if not reconnected:
                    self._state.connection = ConnectionState.DISCONNECTED
                    await self._event_queue.put(None)
                    return
                reconnect_attempt += 1
                self._state.reconnect_count += 1

    async def _reconnect_with_backoff(self, attempt: int) -> bool:
        if attempt >= self._max_reconnect:
            logger.error("Max reconnect attempts reached (%s)", self._max_reconnect)
            return False
        delay = _backoff_delay(attempt)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        if self._closed:
            return False
        try:
            await self._transport.close()
        except Exception:
            logger.debug("close before reconnect failed", exp_info=True)
        try:
            await self._connect_and_subscribe()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Reconnect failed: %s", exc)
            return await self._reconnect_with_backoff(attempt + 1)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        assert self._state is not None and self._candles is not None
        assert self._symbol_info is not None
        if msg.get("msg_type") not in (None, "tick") and "tick" not in msg:
            return
        if "tick" not in msg and msg.get("msg_type") != "tick":
            if "quote" not in msg and "price" not in msg:
                return
        try:
            tick = normalize_tick_message(
                msg,
                instrument=self._state.instrument,
                expected_symbol=self._symbol_info.symbol,
            )
        except MalformedTickError as exc:
            logger.debug("Dropping malformed tick: %s", exp_info=True)
            return
        decision = self._gate.accept(tick)
        if not decision.accepted:
            logger.debug("Dropping tick (%s): epoch=%s", decision.reason, tick.epoch)
            return
        self._state.record_tick(tick)
        await self._event_queue.put(tick)
        for event in self._candles.on_tick(tick):
            if event.kind is CandleEventKind.UPDATE:
                self._state.set_current(event.candle.timeframe, event.candle)
            elif event.kind is CandleEventKind.FINALIZED:
                self._state.append_finalized(event.candle.timeframe, event.candle)
            await self._event_queue.put(event)


def make_fake_symbol(name: str, symbol: str) -> SymbolInfo:
    return SymbolInfo(symbol=symbol, name=name)
