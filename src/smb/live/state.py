"""In-memory live market state for one instrument (bounded, non-persistent)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from smb.live.models import ConnectionState, LiveTick, StreamStatus
from smb.market.candles import Candle


@dataclass
class LiveMarketState:
    instrument: str
    symbol: str
    max_finalized: int = 256
    connection: ConnectionState = ConnectionState.DISCONNECTED
    subscribed: bool = False
    reconnect_count: int = 0
    last_tick: LiveTick | None = None
    current_m1: Candle | None = None
    current_m15: Candle | None = None
    _finalized_m1: deque[Candle] = field(default_factory=deque)
    _finalized_m15: deque[Candle] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.max_finalized < 1:
            raise ValueError("max_finalized must be >= 1")
        self._finalized_m1 = deque(maxlen=self.max_finalized)
        self._finalized_m15 = deque(maxlen=self.max_finalized)

    def record_tick(self, tick: LiveTick) -> None:
        if tick.instrument != self.instrument:
            raise ValueError("instrument mismatch")
        self.last_tick = tick

    def set_current(self, timeframe: str, candle: Candle) -> None:
        if candle.finalized:
            raise ValueError("current candle must have finalized=False")
        if timeframe == "M1":
            self.current_m1 = candle
        elif timeframe == "M15":
            self.current_m15 = candle

    def append_finalized(self, timeframe: str, candle: Candle) -> None:
        if not candle.finalized:
            raise ValueError("finalized candle must have finalized=True")
        if timeframe == "M1":
            self._finalized_m1.append(candle)
            if self.current_m1 is not None and self.current_m1.start_epoch == candle.start_epoch:
                self.current_m1 = None
        elif timeframe == "M15":
            self._finalized_m15.append(candle)
            if (
                self.current_m15 is not None
                and self.current_m15.start_epoch == candle.start_epoch
            ):
                self.current_m15 = None

    @property
    def finalized_m1(self) -> tuple[Candle, ...]:
        return tuple(self._finalized_m1)

    @property
    def finalized_m15(self) -> tuple[Candle, ...]:
        return tuple(self._finalized_m15)

    def status(self) -> StreamStatus:
        return StreamStatus(
            connection=self.connection,
            instrument=self.instrument,
            symbol=self.symbol,
            last_tick_epoch=self.last_tick.epoch if self.last_tick else None,
            last_tick_price=self.last_tick.price if self.last_tick else None,
            subscribed=self.subscribed,
            reconnect_count=self.reconnect_count,
        )
