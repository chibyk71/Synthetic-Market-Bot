"""Milestone 4A — Live market data (ticks, candles, stream state).

Data infrastructure only. No strategy, execution, ML, or health gates.
"""

from smb.live.candle_feed import LiveCandleTracker, MultiTimeframeLiveCandles
from smb.live.models import (
    CandleEvent,
    CandleEventKind,
    ConnectionState,
    LiveTick,
    StreamStatus,
)
from smb.live.normalize import MalformedTickError, normalize_tick_message
from smb.live.ordering import TickDecision, TickOrderingGate
from smb.live.state import LiveMarketState
from smb.live.stream import LiveMarketDataService, make_fake_symbol
from smb.live.transport import DerivTickTransport, FakeTickTransport, TickTransport

__all__ = [
    "LiveTick",
    "CandleEvent",
    "CandleEventKind",
    "ConnectionState",
    "StreamStatus",
    "MalformedTickError",
    "normalize_tick_message",
    "TickOrderingGate",
    "TickDecision",
    "LiveCandleTracker",
    "MultiTimeframeLiveCandles",
    "LiveMarketState",
    "LiveMarketDataService",
    "TickTransport",
    "DerivTickTransport",
    "FakeTickTransport",
    "make_fake_symbol",
]
