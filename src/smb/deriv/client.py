"""Minimal asynchronous Deriv public WebSocket client.

This module deliberately hides connection details from the rest of the
application. Future market-data layers should depend only on the public
request/response interface, not on raw WebSocket mechanics.

Responses are matched to requests via ``req_id`` so concurrent (or
overlapping) calls are safe. Streaming subscriptions are intentionally
out of scope for this milestone.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)

# Official public market-data endpoint (no auth).
# Source: https://developers.deriv.com/llms/active-symbols.md
#          https://developers.deriv.com/llms/ticks-history.md
DEFAULT_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
DEFAULT_TIMEOUT = 15.0


class DerivAPIError(Exception):
    """Raised when the Deriv API returns an error payload."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DerivClient:
    """Thin async client for the Deriv public WebSocket API.

    Usage::

        async with DerivClient() as client:
            response = await client.request({"active_symbols": "full"})

    Concurrent ``request()`` calls are supported: each call is assigned a
    unique ``req_id`` and the matching response is routed back via an
    internal future map.
    """

    def __init__(
        self,
        url: str = DEFAULT_WS_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._url = url
        self._timeout = timeout
        self._ws: ClientConnection | None = None
        self._req_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.state.name == "OPEN"

    async def connect(self) -> None:
        """Open the WebSocket connection and start the response reader."""
        if self.connected:
            return
        logger.info("Connecting to Deriv WebSocket: %s", self._url)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._url, open_timeout=self._timeout),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise ConnectionError(
                f"Timed out connecting to Deriv WebSocket after {self._timeout}s"
            ) from exc
        except Exception as exc:
            raise ConnectionError(f"Failed to connect to Deriv WebSocket: {exc}") from exc

        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="deriv-ws-reader"
        )
        logger.info("Connected to Deriv WebSocket")

    async def close(self) -> None:
        """Close the WebSocket connection and cancel pending requests."""
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("WebSocket closed"))
        self._pending.clear()

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
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error while closing WebSocket: %s", exc)
            finally:
                self._ws = None
                logger.info("Deriv WebSocket closed")

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON request and return the parsed response matched by req_id.

        Raises:
            ConnectionError: if not connected or the socket fails.
            TimeoutError: if no matching response arrives within the timeout.
            DerivAPIError: if the response contains an API error.
            ValueError: if the response is not valid JSON or is empty.
        """
        if not self.connected or self._ws is None:
            raise ConnectionError("Not connected. Call connect() first.")

        async with self._lock:
            self._req_id += 1
            req_id = self._req_id

        body = dict(payload)
        body["req_id"] = req_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        message = json.dumps(body)
        logger.debug("Sending request req_id=%s: %s", req_id, body)

        try:
            await self._ws.send(message)
            response = await asyncio.wait_for(fut, timeout=self._timeout)
        except TimeoutError as exc:
            self._pending.pop(req_id, None)
            if not fut.done():
                fut.cancel()
            raise TimeoutError(
                f"No response from Deriv within {self._timeout}s for req_id={req_id}"
            ) from exc
        except websockets.exceptions.ConnectionClosed as exc:
            self._ws = None
            self._pending.pop(req_id, None)
            raise ConnectionError(
                f"WebSocket closed while waiting for response: {exc}"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

        if "error" in response:
            err = response["error"]
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise DerivAPIError(msg, code=code, details=response)

        logger.debug(
            "Received response req_id=%s msg_type=%s",
            response.get("req_id"),
            response.get("msg_type"),
        )
        return response

    async def _reader_loop(self) -> None:
        """Background task: dispatch inbound messages to pending futures by req_id."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if not raw:
                    continue
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Ignoring invalid JSON from Deriv: %r", raw[:200])
                    continue
                if not isinstance(response, dict):
                    logger.warning(
                        "Ignoring non-object response: %s", type(response).__name__
                    )
                    continue

                req_id = response.get("req_id")
                if req_id is None:
                    logger.debug(
                        "Ignoring message without req_id (msg_type=%s)",
                        response.get("msg_type"),
                    )
                    continue

                fut = self._pending.get(int(req_id))
                if fut is None or fut.done():
                    logger.debug("No pending waiter for req_id=%s", req_id)
                    continue
                fut.set_result(response)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed (reader exit)")
        except Exception:
            logger.exception("Unexpected error in WebSocket reader")
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket reader stopped"))
            self._pending.clear()

    async def __aenter__(self) -> DerivClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
