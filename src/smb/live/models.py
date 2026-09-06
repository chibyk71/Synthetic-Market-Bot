"""Typed models for Milestone 4A live market data.

No raw Deriv protocol objects are exposed beyond optional ``raw`` audit fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from smb.market.candles import Candle


class ConnectionState(StrEnum):
    """Lifecycle of the live transport."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


class CandleEventKind(StrEnum):
    """Whether a candle snapshot is still open or permanently closed."""

    UPDATE = "update"
    FINALIZED = "finalized"


@dataclass(frozen=True, slots=True)
class LiveTick:
    """Normalized live tick for one instrument."""

    instrument: str
    symbol: str
    price: float
    epoch: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

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
            raw=dict(self.raw),
        )


@dataclass(frozen=True, slots=True)
class CandleEvent:
    """Candle snapshot with explicit update vs finalized semantics."""

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
    """Observable health of a live instrument stream (for 4C, not a full gate)."""

    connection: ConnectionState
    instrument: str | None
    symbol: str | None
    last_tick_epoch: int | None
    last_tick_price: float | None
    subscribed: bool
    reconnect_count: int = 0
