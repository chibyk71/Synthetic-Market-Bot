"""Typed models for Milestone 4A live market data.

Live ticks are genuinely immutable — no nested mutable containers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from smb.market.candles import Candle


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


class CandleEventKind(StrEnum):
    UPDATE = "update"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class LiveTick:
    """Normalized live tick for one instrument (genuinely immutable)."""

    instrument: str
    symbol: str
    price: float
    epoch: int

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("instrument must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not isinstance(self.price, (int, float)) or isinstance(self.price, bool):
            raise ValueError("price must be numeric")
        if self.price != self.price:
            raise ValueError("price must be finite")
        if self.price in (float("inf"), float("-inf")):
            raise ValueError("price must be finite")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool):
            raise ValueError("epoch must be int")
        if self.epoch < 0:
            raise ValueError("epoch must be non-negative")

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.epoch, tz=UTC)

    def as_history_tick(self):
        from smb.deriv.history import Tick

        return Tick(
            timestamp=self.timestamp,
            price=float(self.price),
            epoch=self.epoch,
        )


@dataclass(frozen=True, slots=True)
class CandleEvent:
    kind: CandleEventKind
    instrument: str
    candle: Candle

    def __post_init__(self) -> None:
        if self.kind is CandleEventKind.FINALIZED and not self.candle.finalized:
            raise ValueError("FINALIZED event requires candle.finalized=True")
        if self.kind is CandleEventKind.UPDATE and self.candle.finalized:
            raise ValueError("UPDATE event requires candle.finalized=False")


@dataclass(frozen=True, slots=True)
class StreamStatus:
    connection: ConnectionState
    instrument: str | None
    symbol: str | None
    last_tick_epoch: int | None
    last_tick_price: float | None
    subscribed: bool
    reconnect_count: int = 0
