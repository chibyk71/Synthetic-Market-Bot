"""Minimal asynchronous Deriv public WebSocket client.

This module deliberately hides connection details from the rest of the
application. Future market-data layers should depend only on the public
request/response interface, not on raw WebSocket mechanics.
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
#          https://developers.deriv.com/docs/options/websocket/
DEFAULT_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
DEFAULT_TIMEOUT = 15.0


class DerivAPIError(Exception):
    """Raised when the Deriv API returns an error payload."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DerivClient:
    """Thin async client for the Deriv public WebSocket API.

    Usage::

        async with DerivClient() as client:
            response = await client.request({"active_symbols": "full"})
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

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.state.name == "OPEN"

    async def connect(self) -> None:
        """Open the WebSocket connection."""
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
        logger.info("Connected to Deriv WebSocket")

    async def close(self) -> None:
        """Close the WebSocket connection cleanly."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Error while closing WebSocket: %s", exc)
            finally:
                self._ws = None
                logger.info("Deriv WebSocket closed")

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON request and return the parsed response.

        Raises:
            ConnectionError: if not connected or the socket fails.
            TimeoutError: if no response arrives within the timeout.
            DerivAPIError: if the response contains an API error.
            ValueError: if the response is not valid JSON or is empty.
        """
        if not self.connected or self._ws is None:
            raise ConnectionError("Not connected. Call connect() first.")

        self._req_id += 1
        body = dict(payload)
        if "req_id" not in body:
            body["req_id"] = self._req_id

        message = json.dumps(body)
        logger.debug("Sending request req_id=%s: %s", body.get("req_id"), body)

        try:
            await self._ws.send(message)
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                f"No response from Deriv within {self._timeout}s for req_id={body.get('req_id')}"
            ) from exc
        except websockets.exceptions.ConnectionClosed as exc:
            self._ws = None
            raise ConnectionError(f"WebSocket closed while waiting for response: {exc}") from exc

        if not raw:
            raise ValueError("Empty response received from Deriv WebSocket")

        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON response from Deriv: {raw[:200]!r}") from exc

        if not isinstance(response, dict):
            raise ValueError(f"Expected JSON object response, got {type(response).__name__}")

        if "error" in response:
            err = response["error"]
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise DerivAPIError(msg, code=code, details=response)

        logger.debug("Received response msg_type=%s", response.get("msg_type"))
        return response

    async def __aenter__(self) -> DerivClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
