"""Live market data (4A) and live strategy + simulation (4B).

4B composes existing strategy / risk / simulation — no real or demo execution.
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
from smb.live.records import (
    LiveEventKind,
    LiveResearchRecord,
    LiveSignalRecord,
    LiveTradeClosedRecord,
    LiveTradeOpenedRecord,
    signal_identity,
)
from smb.live.runner import LiveRunnerConfig, LiveStrategyRunner
from smb.live.sim_session import LiveSimulationSession
from smb.live.state import LiveMarketState
from smb.live.stream import LiveMarketDataService, make_fake_symbol
from smb.live.transport import DerivTickTransport, FakeTickTransport, TickTransport

__all__ = [
    # 4A
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
    # 4B
    "LiveEventKind",
    "LiveResearchRecord",
    "LiveSignalRecord",
    "LiveTradeOpenedRecord",
    "LiveTradeClosedRecord",
    "signal_identity",
    "LiveSimulationSession",
    "LiveRunnerConfig",
    "LiveStrategyRunner",
]
