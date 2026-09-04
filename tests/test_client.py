"""Unit tests for DerivClient (mocked WebSocket, no network)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smb.deriv.client import DerivAPIError, DerivClient


class FakeWS:
    """Minimal async WebSocket stand-in with a message queue."""

    def __init__(self):
        self.state = MagicMock()
        self.state.name = "OPEN"
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        body = json.loads(msg)
        self.sent.append(body)
        if not hasattr(self, "_auto_reply") or self._auto_reply:
            reply = {
                "req_id": body.get("req_id"),
                "msg_type": "ping",
                "echo_req": body,
            }
            if hasattr(self, "_reply_factory"):
                reply = self._reply_factory(body)
            await self._queue.put(json.dumps(reply))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self.closed and self._queue.empty():
            raise StopAsyncIteration
        return await self._queue.get()

    async def close(self) -> None:
        self.closed = True
        self.state.name = "CLOSED"


@pytest.fixture
def fake_ws():
    return FakeWS()


@pytest.mark.asyncio
async def test_successful_request(fake_ws):
    def factory(body):
        return {
            "req_id": body["req_id"],
            "msg_type": "active_symbols",
            "active_symbols": [{"underlying_symbol": "1HZ75V"}],
        }

    fake_ws._reply_factory = factory
    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=fake_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        result = await client.request({"active_symbols": "full"})
        await client.close()

    assert result["msg_type"] == "active_symbols"
    assert result["active_symbols"][0]["underlying_symbol"] == "1HZ75V"
    assert fake_ws.sent[0]["req_id"] == 1


@pytest.mark.asyncio
async def test_api_error_response(fake_ws):
    def factory(body):
        return {
            "req_id": body["req_id"],
            "msg_type": "error",
            "error": {"code": "InputValidationFailed", "message": "Invalid request"},
            "echo_req": body,
        }

    fake_ws._reply_factory = factory
    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=fake_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        with pytest.raises(DerivAPIError) as exc_info:
            await client.request({"active_symbols": "full"})
        await client.close()

    assert "Invalid request" in str(exc_info.value)
    assert exc_info.value.code == "InputValidationFailed"


@pytest.mark.asyncio
async def test_request_without_connect():
    client = DerivClient(url="wss://example.test/ws")
    with pytest.raises(ConnectionError, match="Not connected"):
        await client.request({"ping": 1})


@pytest.mark.asyncio
async def test_context_manager_cleanup(fake_ws):
    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=fake_ws)):
        async with DerivClient(url="wss://example.test/ws") as client:
            assert client.connected
            await client.request({"ping": 1})
        assert fake_ws.closed


@pytest.mark.asyncio
async def test_req_id_routing(fake_ws):
    """Responses are matched by req_id even if delivered out of order."""

    def factory(body):
        rid = body["req_id"]
        return {
            "req_id": rid,
            "msg_type": "history",
            "history": {"prices": [float(rid)], "times": [rid]},
        }

    fake_ws._reply_factory = factory
    original_send = fake_ws.send

    async def delayed_send(msg):
        body = json.loads(msg)
        if body["req_id"] == 1:
            await asyncio.sleep(0.05)
        await original_send(msg)

    fake_ws.send = delayed_send

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=fake_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        t1 = asyncio.create_task(
            client.request(
                {"ticks_history": "A", "end": "latest", "count": 1, "style": "ticks"}
            )
        )
        t2 = asyncio.create_task(
            client.request(
                {"ticks_history": "B", "end": "latest", "count": 1, "style": "ticks"}
            )
        )
        r1, r2 = await asyncio.gather(t1, t2)
        await client.close()

    assert r1["req_id"] == 1
    assert r2["req_id"] == 2
    assert r1["history"]["prices"] == [1.0]
    assert r2["history"]["prices"] == [2.0]
