"""Deterministic OHLC candle builder for fixed UTC timeframes.

Boundary rule (half-open interval)::

    bucket = [T, T + N)   where N is the timeframe length in seconds
    bucket_start = (epoch // N) * N

A tick at exactly ``T + N`` belongs to the *next* candle.

Out-of-order policy
-------------------
If a tick's epoch is strictly earlier than the previously processed
tick, :class:`OutOfOrderTickError` is raised. The builder never silently
reorders or drops ticks.

Gaps
----
Periods with no ticks produce no candles. Missing intervals are not
fabricated.

``tick_count`` records how many ticks contributed to a candle. It is
**not** traded volume.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from smb.deriv.history import Tick


class OutOfOrderTickError(ValueError):
    """Raised when a tick epoch is earlier than the previous tick."""

    def __init__(self, previous_epoch: int, tick_epoch: int) -> None:
        self.previous_epoch = previous_epoch
        self.tick_epoch = tick_epoch
        super().__init__(
            f"Out-of-order tick: epoch {tick_epoch} is earlier than "
            f"previous epoch {previous_epoch}"
        )


@dataclass(frozen=True)
class Timeframe:
    """Named fixed-duration candle timeframe in seconds."""

    name: str
    seconds: int

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("timeframe seconds must be positive")

    def bucket_start(self, epoch: int) -> int:
        """UTC epoch-second start of the candle containing ``epoch``."""
        return (epoch // self.seconds) * self.seconds


TIMEFRAME_M1: Final = Timeframe("M1", 60)
TIMEFRAME_M5: Final = Timeframe("M5", 300)
TIMEFRAME_M15: Final = Timeframe("M15", 900)

TIMEFRAMES: Final[dict[str, Timeframe]] = {
    "M1": TIMEFRAME_M1,
    "M5": TIMEFRAME_M5,
    "M15": TIMEFRAME_M15,
}


@dataclass(frozen=True)
class Candle:
    """One OHLC candle for a fixed timeframe.

    ``tick_count`` is the number of ticks that formed this candle.
    It must not be treated as market/traded volume.
    """

    timeframe: str
    start_epoch: int
    end_epoch: int  # exclusive upper bound: start + timeframe_seconds
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    finalized: bool = True

    @property
    def start_time(self) -> datetime:
        return datetime.fromtimestamp(self.start_epoch, tz=UTC)

    @property
    def end_time(self) -> datetime:
        return datetime.fromtimestamp(self.end_epoch, tz=UTC)


class CandleBuilder:
    """Streaming OHLC candle builder for a single timeframe.

    Feed ticks via :meth:`on_tick`. Completed candles are returned as
    they are finalized (when a tick falls into a new bucket). Call
    :meth:`flush` at the end of a historical stream to finalize the
    last open candle.
    """

    def __init__(self, timeframe: Timeframe) -> None:
        self._tf = timeframe
        self._last_epoch: int | None = None
        self._bucket_start: int | None = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._tick_count: int = 0

    @property
    def timeframe(self) -> Timeframe:
        return self._tf

    def on_tick(self, tick: Tick) -> Candle | None:
        """Process one tick; return a finalized candle if a boundary was crossed.

        Raises
        ------
        OutOfOrderTickError
            If ``tick.epoch`` is strictly less than the previous tick's epoch.
        """
        if self._last_epoch is not None and tick.epoch < self._last_epoch:
            raise OutOfOrderTickError(self._last_epoch, tick.epoch)
        self._last_epoch = tick.epoch

        bucket = self._tf.bucket_start(tick.epoch)

        if self._bucket_start is None:
            self._start_bucket(bucket, tick.price)
            return None

        if bucket == self._bucket_start:
            self._update_bucket(tick.price)
            return None

        # New bucket → finalize previous, then start new.
        completed = self._finalize_current()
        self._start_bucket(bucket, tick.price)
        return completed

    def flush(self) -> Candle | None:
        """Finalize and return the current open candle, if any."""
        if self._bucket_start is None:
            return None
        candle = self._finalize_current()
        self._bucket_start = None
        self._open = self._high = self._low = self._close = None
        self._tick_count = 0
        return candle

    def process(self, ticks: Iterable[Tick]) -> list[Candle]:
        """Process an iterable of ticks and flush at the end.

        Returns all finalized candles in chronological order.
        """
        candles: list[Candle] = []
        for tick in ticks:
            done = self.on_tick(tick)
            if done is not None:
                candles.append(done)
        final = self.flush()
        if final is not None:
            candles.append(final)
        return candles

    def _start_bucket(self, bucket_start: int, price: float) -> None:
        self._bucket_start = bucket_start
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._tick_count = 1

    def _update_bucket(self, price: float) -> None:
        assert self._high is not None and self._low is not None
        if price > self._high:
            self._high = price
        if price < self._low:
            self._low = price
        self._close = price
        self._tick_count += 1

    def _finalize_current(self) -> Candle:
        assert self._bucket_start is not None
        assert self._open is not None
        assert self._high is not None
        assert self._low is not None
        assert self._close is not None
        return Candle(
            timeframe=self._tf.name,
            start_epoch=self._bucket_start,
            end_epoch=self._bucket_start + self._tf.seconds,
            open=self._open,
            high=self._high,
            low=self._low,
            close=self._close,
            tick_count=self._tick_count,
            finalized=True,
        )


class MultiTimeframeCandleBuilder:
    """Feed one tick stream into several timeframe builders.

    Example::

        mt = MultiTimeframeCandleBuilder([TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15])
        results = mt.process(ticks)
        # results["M1"], results["M5"], results["M15"]
    """

    def __init__(self, timeframes: Sequence[Timeframe] | None = None) -> None:
        tfs = list(timeframes) if timeframes is not None else [
            TIMEFRAME_M1,
            TIMEFRAME_M5,
            TIMEFRAME_M15,
        ]
        self._builders = {tf.name: CandleBuilder(tf) for tf in tfs}

    def on_tick(self, tick: Tick) -> dict[str, Candle]:
        """Process one tick; return any candles finalized on this tick."""
        finalized: dict[str, Candle] = {}
        for name, builder in self._builders.items():
            candle = builder.on_tick(tick)
            if candle is not None:
                finalized[name] = candle
        return finalized

    def flush(self) -> dict[str, Candle]:
        """Flush all builders; return any remaining open candles."""
        finalized: dict[str, Candle] = {}
        for name, builder in self._builders.items():
            candle = builder.flush()
            if candle is not None:
                finalized[name] = candle
        return finalized

    def process(self, ticks: Iterable[Tick]) -> dict[str, list[Candle]]:
        """Process all ticks and flush; return candles per timeframe name."""
        results: dict[str, list[Candle]] = {name: [] for name in self._builders}
        for tick in ticks:
            done = self.on_tick(tick)
            for name, candle in done.items():
                results[name].append(candle)
        for name, candle in self.flush().items():
            results[name].append(candle)
        return results
