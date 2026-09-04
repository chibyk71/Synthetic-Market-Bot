"""Unit tests for DerivClient (mocked WebSocket, no network)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smb.deriv.client import DerivAPIError, DerivClient


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.state = MagicMock()
    ws.state.name = "OPEN"
    return ws


@pytest.mark.asyncio
async def test_successful_request(mock_ws):
    response = {
        "msg_type": "active_symbols",
        "active_symbols": [{"underlying_symbol": "1HZ75V"}],
        "req_id": 1,
    }
    mock_ws.recv = AsyncMock(return_value=json.dumps(response))
    mock_ws.send = AsyncMock()

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=mock_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        result = await client.request({"active_symbols": "full"})
        await client.close()

    assert result["msg_type"] == "active_symbols"
    assert result["active_symbols"][0]["underlying_symbol"] == "1HZ75V"
    mock_ws.send.assert_awaited()
    mock_ws.close.assert_awaited()


@pytest.mark.asyncio
async def test_api_error_response(mock_ws):
    error_payload = {
        "echo_req": {"active_symbols": "full"},
        "error": {"code": "InputValidationFailed", "message": "Invalid request"},
        "msg_type": "error",
    }
    mock_ws.recv = AsyncMock(return_value=json.dumps(error_payload))
    mock_ws.send = AsyncMock()

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=mock_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        with pytest.raises(DerivAPIError) as exc_info:
            await client.request({"active_symbols": "full"})
        await client.close()

    assert "Invalid request" in str(exc_info.value)
    assert exc_info.value.code == "InputValidationFailed"


@pytest.mark.asyncio
async def test_malformed_json(mock_ws):
    mock_ws.recv = AsyncMock(return_value="not-json{{{")
    mock_ws.send = AsyncMock()

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=mock_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        with pytest.raises(ValueError, match="Invalid JSON"):
            await client.request({"ping": 1})
        await client.close()


@pytest.mark.asyncio
async def test_empty_response(mock_ws):
    mock_ws.recv = AsyncMock(return_value="")
    mock_ws.send = AsyncMock()

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=mock_ws)):
        client = DerivClient(url="wss://example.test/ws", timeout=5.0)
        await client.connect()
        with pytest.raises(ValueError, match="Empty response"):
            await client.request({"ping": 1})
        await client.close()


@pytest.mark.asyncio
async def test_request_without_connect():
    client = DerivClient(url="wss://example.test/ws")
    with pytest.raises(ConnectionError, match="Not connected"):
        await client.request({"ping": 1})


@pytest.mark.asyncio
async def test_context_manager_cleanup(mock_ws):
    mock_ws.recv = AsyncMock(
        return_value=json.dumps({"msg_type": "ping", "ping": "pong"})
    )
    mock_ws.send = AsyncMock()

    with patch("smb.deriv.client.websockets.connect", AsyncMock(return_value=mock_ws)):
        async with DerivClient(url="wss://example.test/ws") as client:
            assert client.connected
            await client.request({"ping": 1})
        # after exit, close should have been called
        mock_ws.close.assert_awaited()
