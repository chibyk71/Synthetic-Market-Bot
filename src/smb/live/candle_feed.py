"""Live candle aggregation with explicit UPDATE vs FINALIZED events.

Boundary rule matches CandleBuilder: bucket_start = (epoch // T) * T
"""

from __future__ import annotations

from collections.abc import Sequence

from smb.live.models import CandleEvent, CandleEventKind, LiveTick
from smb.market.candles import TIMEFRAME_M1, TIMEFRAME_M15, Candle, Timeframe


class LiveCandleTracker:
    def __init__(self, timeframe: Timeframe, *, instrument: str) -> None:
        self._tf = timeframe
        self._instrument = instrument
        self._bucket_start: int | None = None
        self._open: float | None = None
        self._high: float | None = None
        self._low: float | None = None
        self._close: float | None = None
        self._tick_count: int = 0
        # Only the most recent finalized bucket — O(1) memory.
        self._last_finalized_start: int | None = None

    @property
    def timeframe(self) -> Timeframe:
        return self._tf

    @property
    def current(self) -> Candle | None:
        if self._bucket_start is None:
            return None
        return self._snapshot(finalized=False)

    def on_tick(self, tick: LiveTick) -> list[CandleEvent]:
        events: list[CandleEvent] = []
        bucket = self._tf.bucket_start(tick.epoch)
        if self._bucket_start is None:
            self._start(bucket, tick.price)
            events.append(
                CandleEvent(
                    kind=CandleEventKind.UPDATE,
                    instrument=self._instrument,
                    candle=self._snapshot(finalized=False),
                )
            )
            return events
        if bucket == self._bucket_start:
            self._update(tick.price)
            events.append(
                CandleEvent(
                    kind=CandleEventKind.UPDATE,
                    instrument=self._instrument,
                    candle=self._snapshot(finalized=False),
                )
            )
            return events
        # New bucket → finalize previous at most once (O(1) bookkeeping).
        if self._bucket_start != self._last_finalized_start:
            finalized = self._snapshot(finalized=True)
            self._last_finalized_start = self._bucket_start
            events.append(
                CandleEvent(
                    kind=CandleEventKind.FINALIZED,
                    instrument=self._instrument,
                    candle=finalized,
                )
            )
        self._start(bucket, tick.price)
        events.append(
            CandleEvent(
                kind=CandleEventKind.UPDATE,
                instrument=self._instrument,
                candle=self._snapshot(finalized=False),
            )
        )
        return events

    def _start(self, bucket_start: int, price: float) -> None:
        self._bucket_start = bucket_start
        self._open = price
        self._high = price
        self._low = price
        self._close = price
        self._tick_count = 1

    def _update(self, price: float) -> None:
        assert self._high is not None and self._low is not None
        if price > self._high:
            self._high = price
        if price < self._low:
            self._low = price
        self._close = price
        self._tick_count += 1

    def _snapshot(self, *, finalized: bool) -> Candle:
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
            finalized=finalized,
        )


# Longer timeframes first on equal end_epoch so context (M15) is available
# before the decision timeframe (M1) evaluates at a shared boundary.
_TF_SECONDS: dict[str, int] = {
    TIMEFRAME_M1.name: TIMEFRAME_M1.seconds,
    TIMEFRAME_M15.name: TIMEFRAME_M15.seconds,
}


def order_candle_events(events: Sequence[CandleEvent]) -> list[CandleEvent]:
    """Order events for strategy-safe multi-timeframe decisions.

    Sort key (ascending):
    1. ``candle.end_epoch`` — chronological
    2. timeframe duration descending — M15 before M1 when they share an end
    3. FINALIZED before UPDATE — completed context before forming updates

    At an M15 boundary where both M1 and M15 finalize at the same ``T``::

        M15 FINALIZED (end=T) → M1 FINALIZED (end=T) → updates…

    so ``StrategyEngine.on_m15`` runs before ``on_m1`` and the decision at ``T``
    can use M15 context with ``end_epoch <= decision_epoch``.
    """

    def _key(event: CandleEvent) -> tuple[int, int, int]:
        tf_secs = _TF_SECONDS.get(event.candle.timeframe, 0)
        kind_rank = 0 if event.kind is CandleEventKind.FINALIZED else 1
        return (event.candle.end_epoch, -tf_secs, kind_rank)

    return sorted(events, key=_key)


class MultiTimeframeLiveCandles:
    def __init__(
        self,
        instrument: str,
        timeframes: Sequence[Timeframe] | None = None,
    ) -> None:
        tfs = list(timeframes) if timeframes is not None else [TIMEFRAME_M1, TIMEFRAME_M15]
        self._instrument = instrument
        self._trackers = {
            tf.name: LiveCandleTracker(tf, instrument=instrument) for tf in tfs
        }

    def on_tick(self, tick: LiveTick) -> list[CandleEvent]:
        events: list[CandleEvent] = []
        for tracker in self._trackers.values():
            events.extend(tracker.on_tick(tick))
        return order_candle_events(events)

    def current(self, timeframe: str) -> Candle | None:
        tr = self._trackers.get(timeframe)
        return tr.current if tr is not None else None

    @property
    def trackers(self) -> dict[str, LiveCandleTracker]:
        return dict(self._trackers)
