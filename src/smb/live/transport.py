"""Transport abstraction for live Deriv tick subscriptions.

``subscribe(symbol)`` waits for the server acknowledgement and stores the
**server-provided** ``subscription.id``. ``unsubscribe`` / ``forget`` uses
that ID — never the client ``req_id``.
"""

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
    def subscription_id(self, symbol: str) -> str | int | None: ...


def _extract_subscription_id(msg: dict[str, Any]) -> str | int | None:
    sub = msg.get("subscription")
    if isinstance(sub, dict) and "id" in sub:
        return sub["id"]
    return None


class DerivTickTransport:
    def __init__(self, *, url: str = DEFAULT_WS_URL, timeout: float = 15.0) -> None:
        self._url = url
        self._timeout = timeout
        self._ws: ClientConnection | None = None
        self._req_id = 0
        self._subscriptions: dict[str, str | int] = {}
        self._pending_subs: dict[int, tuple[str, asyncio.Future[str | int]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._inbound: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def subscription_id(self, symbol: str) -> str | int | None:
        return self._subscriptions.get(symbol)

    async def connect(self) -> None:
        if self._ws is not None:
            return
        self._ws = await websockets.connect(self._url, open_timeout=self._timeout)
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="deriv-tick-reader"
        )
        logger.info("Live tick transport connected to %s", self._url)

    async def close(self) -> None:
        for sym in list(self._subscriptions):
            try:
                await self.unsubscribe(sym)
            except Exception:
                logger.debug("unsubscribe during close failed for %s", sym, exc_info=True)
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("ws close error", exp_info=True)
            finally:
                self._ws = None
        self._subscriptions.clear()
        for _sym, fut in list(self._pending_subs.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("transport closed"))
        self._pending_subs.clear()
        await self._inbound.put(None)

    async def subscribe(self, symbol: str) -> None:
        if not self.connected or self._ws is None:
            raise ConnectionError("not connected")
        if symbol in self._subscriptions:
            return
        async with self._lock:
            if symbol in self._subscriptions:
                return
            self._req_id += 1
            req_id = self._req_id
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[str | int] = loop.create_future()
            self._pending_subs[req_id] = (symbol, fut)
            payload = {"ticks": symbol, "subscribe": 1, "req_id": req_id}
            await self._ws.send(json.dumps(payload))
        try:
            sub_id = await asyncio.wait_for(fut, timeout=self._timeout)
        except TimeoutError as exc:
            self._pending_subs.pop(req_id, None)
            raise TimeoutError(
                f"No subscription acknowledgement for {symbol!r} within {self._timeout}s"
            ) from exc
        except Exception:
            self._pending_subs.pop(req_id, None)
            raise
        self._subscriptions[symbol] = sub_id
        logger.info("Subscribed to ticks for %s (subscription.id=%s)", symbol, sub_id)

    async def unsubscribe(self, symbol: str) -> None:
        if symbol not in self._subscriptions:
            return
        sub_id = self._subscriptions.pop(symbol)
        if self._ws is not None:
            self._req_id += 1
            payload = {"forget": sub_id, "req_id": self._req_id}
            try:
                await self._ws.send(json.dumps(payload))
                logger.info("Forgot subscription %s for %s", sub_id, symbol)
            except Exception:
                logger.debug("forget send failed", exc_info=True)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._inbound.get()
            if item is None:
                return
            yield item

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
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
                self._dispatch_subscription_ack(msg)
                if msg.get("msg_type") == "tick" or "tick" in msg:
                    await self._inbound.put(msg)
                elif "error" in msg:
                    logger.warning("Deriv stream error: %s", msg.get("error"))
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed (reader exit)")
        except Exception:
            logger.exception("Unexpected error in tick reader")
        finally:
            for _sym, fut in list(self._pending_subs.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket reader stopped"))
            self._pending_subs.clear()
            await self._inbound.put(None)

    def _dispatch_subscription_ack(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("req_id")
        if req_id is None:
            return
        try:
            rid = int(req_id)
        except (TypeError, ValueError):
            return
        pending = self._pending_subs.get(rid)
        if pending is None:
            return
        symbol, fut = pending
        if fut.done():
            self._pending_subs.pop(rid, None)
            return
        if "error" in msg:
            err = msg["error"]
            text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            fut.set_exception(ConnectionError(f"subscribe failed: {text}"))
            self._pending_subs.pop(rid, None)
            return
        sub_id = _extract_subscription_id(msg)
        if sub_id is None:
            return
        fut.set_result(sub_id)
        self._pending_subs.pop(rid, None)


class FakeTickTransport:
    def __init__(self) -> None:
        self._connected = False
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscriptions: dict[str, str] = {}
        self._sub_counter = 0
        self.subscribe_calls: list[str] = []
        self.unsubscribe_calls: list[tuple[str, str]] = []
        self.forget_ids: list[str] = []
        self.connect_calls = 0
        self.close_calls = 0
        self._fail_connect = False

    def set_fail_connect(self, value: bool = True) -> None:
        self._fail_connect = value

    @property
    def connected(self) -> bool:
        return self._connected

    def subscription_id(self, symbol: str) -> str | int | None:
        return self._subscriptions.get(symbol)

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._fail_connect:
            raise ConnectionError("simulated connect failure")
        self._connected = True

    async def close(self) -> None:
        self.close_calls += 1
        for sym in list(self._subscriptions):
            await self.unsubscribe(sym)
        self._connected = False
        await self._queue.put(None)

    async def subscribe(self, symbol: str) -> None:
        if not self._connected:
            raise ConnectionError("not connected")
        if symbol in self._subscriptions:
            return
        self._sub_counter += 1
        server_id = f"srv-sub-{self._sub_counter}"
        self._subscriptions[symbol] = server_id
        self.subscribe_calls.append(symbol)

    async def unsubscribe(self, symbol: str) -> None:
        if symbol not in self._subscriptions:
            return
        server_id = self._subscriptions.pop(symbol)
        self.unsubscribe_calls.append((symbol, server_id))
        self.forget_ids.append(server_id)

    async def push(self, message: dict[str, Any]) -> None:
        await self._queue.put(message)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item
