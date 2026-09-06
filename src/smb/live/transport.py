"""Transport abstraction for live Deriv tick subscriptions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

import websockets
from websockets.asyncio.client import ClientConnection

from smb.deriv.client import DEFAULT_WS_URL

logger = logging.getLogger(__name__)


@runtime_checkable
class TickTransport(Protocol):
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...
    async def subscribe(self, symbol: str) -> None: ...
    async def unsubscribe(self, symbol: str) -> None: ...
    def messages(self) -> AsyncIterator[dict[str, Any]]: ...


class DerivTickTransport:
    def __init__(self, *, url: str = DEFAULT_WS_URL, timeout: float = 15.0) -> None:
        self._url = url
        self._timeout = timeout
        self._ws: ClientConnection | None = None
        self._req_id = 0
        self._subscriptions: dict[str, int | str] = {}

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._ws = await websockets.connect(self._url, open_timeout=self._timeout)
        logger.info("Live tick transport connected to %s", self._url)

    async def close(self) -> None:
        for sym in list(self._subscriptions):
            try:
                await self.unsubscribe(sym)
            except Exception:
                logger.debug("unsubscribe during close failed for %s", sym, exc_info=True)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("ws close error", exc_info=True)
            finally:
                self._ws = None
        self._subscriptions.clear()

    async def subscribe(self, symbol: str) -> None:
        if not self.connected or self._ws is None:
            raise ConnectionError("not connected")
        if symbol in self._subscriptions:
            return
        self._req_id += 1
        payload = {"ticks": symbol, "subscribe": 1, "req_id": self._req_id}
        await self._ws.send(json.dumps(payload))
        self._subscriptions[symbol] = self._req_id
        logger.info("Subscribed to ticks for %s", symbol)

    async def unsubscribe(self, symbol: str) -> None:
        if symbol not in self._subscriptions:
            return
        sub_id = self._subscriptions.pop(symbol)
        if self._ws is not None:
            self._req_id += 1
            try:
                await self._ws.send(json.dumps({"forget": sub_id, "req_id": self._req_id}))
            except Exception:
                logger.debug("forget send failed", exc_info=True)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise ConnectionError("not connected")
        async for raw in self._ws:
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Ignoring invalid JSON on live stream")
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("msg_type") == "tick" and "subscription" in msg:
                sub = msg["subscription"]
                if isinstance(sub, dict) and "id" in sub:
                    tick = msg.get("tick") or {}
                    sym = tick.get("symbol")
                    if sym and sym in self._subscriptions:
                        self._subscriptions[sym] = sub["id"]
            yield msg


class FakeTickTransport:
    def __init__(self) -> None:
        self._connected = False
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribed: set[str] = set()
        self.subscribe_calls: list[str] = []
        self.unsubscribe_calls: list[str] = []
        self.connect_calls = 0
        self.close_calls = 0
        self._fail_connect = False

    def set_fail_connect(self, value: bool = True) -> None:
        self._fail_connect = value

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._fail_connect:
            raise ConnectionError("simulated connect failure")
        self._connected = True

    async def close(self) -> None:
        self.close_calls += 1
        self._connected = False
        self._subscribed.clear()
        await self._queue.put(None)

    async def subscribe(self, symbol: str) -> None:
        if not self._connected:
            raise ConnectionError("not connected")
        if symbol in self._subscribed:
            return
        self._subscribed.add(symbol)
        self.subscribe_calls.append(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        self.unsubscribe_calls.append(symbol)
        self._subscribed.discard(symbol)

    async def push(self, message: dict[str, Any]) -> None:
        await self._queue.put(message)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item
