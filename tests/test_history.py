"""Offline unit tests for historical tick parsing and helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from smb.deriv.client import DerivAPIError, DerivClient
from smb.deriv.history import (
    MAX_TICKS_PER_REQUEST,
    HistoryPage,
    Tick,
    compute_tick_stats,
    fetch_ticks,
    fetch_ticks_paginated,
    flatten_pages,
    parse_history_response,
)


SAMPLE_HISTORY = {
    "echo_req": {
        "ticks_history": "1HZ75V",
        "end": "latest",
        "count": 5,
        "style": "ticks",
        "req_id": 1,
    },
    "history": {
        "prices": [100.0, 100.1, 100.2, 100.15, 100.25],
        "times": [1700000001, 1700000002, 1700000003, 1700000004, 1700000005],
    },
    "msg_type": "history",
    "pip_size": 2,
    "req_id": 1,
}


def test_parse_history_success():
    page = parse_history_response(SAMPLE_HISTORY, symbol="1HZ75V")
    assert isinstance(page, HistoryPage)
    assert page.symbol == "1HZ75V"
    assert page.count == 5
    assert page.pip_size == 2.0
    assert page.earliest is not None
    assert page.earliest.epoch == 1700000001
    assert page.earliest.price == 100.0
    assert page.earliest.timestamp == datetime(2023, 11, 14, 22, 13, 21, tzinfo=timezone.utc)
    assert page.latest.epoch == 1700000005
    assert page.ticks[0].timestamp.tzinfo is timezone.utc


def test_pip_size_preserves_decimal_precision():
    """Live API returns fractional pip_size (e.g. 0.01, 0.1); must not truncate to int."""
    for raw, expected in [(0.01, 0.01), (0.1, 0.1), (0.001, 0.001), (2, 2.0)]:
        resp = {
            "msg_type": "history",
            "history": {"prices": [100.0], "times": [1]},
            "pip_size": raw,
        }
        page = parse_history_response(resp, symbol="X")
        assert page.pip_size == expected
        assert isinstance(page.pip_size, float)


def test_pip_size_missing_or_invalid():
    resp_missing = {
        "msg_type": "history",
        "history": {"prices": [1.0], "times": [1]},
    }
    assert parse_history_response(resp_missing, symbol="X").pip_size is None

    resp_bad = {
        "msg_type": "history",
        "history": {"prices": [1.0], "times": [1]},
        "pip_size": "not-a-number",
    }
    assert parse_history_response(resp_bad, symbol="X").pip_size is None


def test_parse_empty_history():
    resp = {"msg_type": "history", "history": {"prices": [], "times": []}, "req_id": 1}
    page = parse_history_response(resp, symbol="X")
    assert page.count == 0
    assert page.earliest is None


def test_parse_missing_history_object():
    resp = {"msg_type": "history", "req_id": 1}
    page = parse_history_response(resp, symbol="X")
    assert page.count == 0


def test_parse_length_mismatch():
    resp = {
        "msg_type": "history",
        "history": {"prices": [1.0, 2.0], "times": [1]},
    }
    with pytest.raises(ValueError, match="length mismatch"):
        parse_history_response(resp, symbol="X")


def test_parse_malformed_price():
    resp = {
        "msg_type": "history",
        "history": {"prices": ["not-a-number"], "times": [1]},
    }
    with pytest.raises(ValueError, match="Invalid tick pair"):
        parse_history_response(resp, symbol="X")


def test_parse_preserves_source_order():
    """Parser must not silently re-sort ticks received from Deriv."""
    resp = {
        "msg_type": "history",
        "history": {
            "prices": [3.0, 1.0, 2.0],
            "times": [3, 1, 2],
        },
    }
    page = parse_history_response(resp, symbol="X")
    assert [t.epoch for t in page.ticks] == [3, 1, 2]
    assert [t.price for t in page.ticks] == [3.0, 1.0, 2.0]
    # earliest/latest are still epoch-correct for pagination cursor use
    assert page.earliest.epoch == 1
    assert page.latest.epoch == 3


def test_stats_detects_non_monotonic_source_order():
    """Statistics layer reports integrity problems; parser does not hide them."""
    resp = {
        "msg_type": "history",
        "history": {
            "prices": [3.0, 1.0, 2.0],
            "times": [3, 1, 2],
        },
    }
    page = parse_history_response(resp, symbol="X")
    stats = compute_tick_stats(page.ticks)
    assert stats.non_monotonic_pairs == 1  # 3 -> 1 is a step backward
    assert stats.count == 3


def test_compute_tick_stats_basic():
    ticks = [
        Tick(timestamp=datetime.fromtimestamp(10, tz=timezone.utc), price=1.0, epoch=10),
        Tick(timestamp=datetime.fromtimestamp(11, tz=timezone.utc), price=1.5, epoch=11),
        Tick(timestamp=datetime.fromtimestamp(13, tz=timezone.utc), price=1.25, epoch=13),
    ]
    stats = compute_tick_stats(ticks)
    assert stats.count == 3
    assert stats.min_interval == 1.0
    assert stats.max_interval == 2.0
    assert stats.median_interval == 1.5
    assert stats.min_price == 1.0
    assert stats.max_price == 1.5
    assert stats.duplicate_epochs == 0
    assert stats.non_monotonic_pairs == 0
    assert stats.ticks_per_second == pytest.approx(2 / 3)


def test_compute_tick_stats_duplicates_and_nonmonotonic():
    ticks = [
        Tick(timestamp=datetime.fromtimestamp(10, tz=timezone.utc), price=1.0, epoch=10),
        Tick(timestamp=datetime.fromtimestamp(10, tz=timezone.utc), price=1.1, epoch=10),
        Tick(timestamp=datetime.fromtimestamp(9, tz=timezone.utc), price=0.9, epoch=9),
    ]
    stats = compute_tick_stats(ticks)
    assert stats.duplicate_epochs == 1
    assert stats.non_monotonic_pairs == 2


def test_compute_tick_stats_empty():
    stats = compute_tick_stats([])
    assert stats.count == 0
    assert stats.earliest is None


def test_flatten_pages_dedup_and_order():
    p1 = HistoryPage(
        symbol="X",
        ticks=(
            Tick(timestamp=datetime.fromtimestamp(3, tz=timezone.utc), price=3.0, epoch=3),
            Tick(timestamp=datetime.fromtimestamp(4, tz=timezone.utc), price=4.0, epoch=4),
        ),
    )
    p2 = HistoryPage(
        symbol="X",
        ticks=(
            Tick(timestamp=datetime.fromtimestamp(1, tz=timezone.utc), price=1.0, epoch=1),
            Tick(timestamp=datetime.fromtimestamp(2, tz=timezone.utc), price=2.0, epoch=2),
            Tick(timestamp=datetime.fromtimestamp(3, tz=timezone.utc), price=3.0, epoch=3),
        ),
    )
    merged = flatten_pages([p1, p2])
    assert [t.epoch for t in merged] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_fetch_ticks_success():
    mock_client = AsyncMock(spec=DerivClient)
    mock_client.request = AsyncMock(return_value=SAMPLE_HISTORY)
    page = await fetch_ticks(mock_client, "1HZ75V", count=5, end="latest")
    assert page.count == 5
    mock_client.request.assert_awaited_once()
    call_payload = mock_client.request.await_args.args[0]
    assert call_payload["ticks_history"] == "1HZ75V"
    assert call_payload["count"] == 5
    assert call_payload["style"] == "ticks"


@pytest.mark.asyncio
async def test_fetch_ticks_caps_count():
    mock_client = AsyncMock(spec=DerivClient)
    mock_client.request = AsyncMock(return_value=SAMPLE_HISTORY)
    await fetch_ticks(mock_client, "1HZ75V", count=50000)
    call_payload = mock_client.request.await_args.args[0]
    assert call_payload["count"] == MAX_TICKS_PER_REQUEST


@pytest.mark.asyncio
async def test_fetch_ticks_api_error():
    mock_client = AsyncMock(spec=DerivClient)
    mock_client.request = AsyncMock(
        side_effect=DerivAPIError("bad", code="InputValidationFailed")
    )
    with pytest.raises(DerivAPIError):
        await fetch_ticks(mock_client, "BAD", count=10)


@pytest.mark.asyncio
async def test_fetch_ticks_paginated_cursor():
    page_a = {
        "msg_type": "history",
        "history": {"prices": [2.0, 2.1], "times": [200, 201]},
        "req_id": 1,
    }
    page_b = {
        "msg_type": "history",
        "history": {"prices": [1.0, 1.1], "times": [100, 101]},
        "req_id": 2,
    }
    mock_client = AsyncMock(spec=DerivClient)
    mock_client.request = AsyncMock(side_effect=[page_a, page_b])
    pages = await fetch_ticks_paginated(mock_client, "X", pages=2, count_per_page=2)
    assert len(pages) == 2
    assert pages[0].earliest.epoch == 200
    assert pages[1].earliest.epoch == 100
    second_payload = mock_client.request.await_args_list[1].args[0]
    assert second_payload["end"] == "199"
