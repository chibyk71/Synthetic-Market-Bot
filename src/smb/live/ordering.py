"""Per-instrument tick ordering and duplicate policy for live streams.

- epoch < last → drop stale
- epoch == last and same price → drop duplicate
- epoch == last and new price → accept same-second update
- epoch > last → accept
"""

from __future__ import annotations

from dataclasses import dataclass

from smb.live.models import LiveTick


@dataclass(frozen=True, slots=True)
class TickDecision:
    accepted: bool
    reason: str


class TickOrderingGate:
    def __init__(self) -> None:
        self._last_epoch: int | None = None
        self._last_price: float | None = None

    @property
    def last_epoch(self) -> int | None:
        return self._last_epoch

    @property
    def last_price(self) -> float | None:
        return self._last_price

    def evaluate(self, tick: LiveTick) -> TickDecision:
        if self._last_epoch is None:
            return TickDecision(True, "ok")
        if tick.epoch < self._last_epoch:
            return TickDecision(False, "stale")
        if tick.epoch == self._last_epoch and tick.price == self._last_price:
            return TickDecision(False, "duplicate")
        return TickDecision(True, "ok")

    def accept(self, tick: LiveTick) -> TickDecision:
        decision = self.evaluate(tick)
        if decision.accepted:
            self._last_epoch = tick.epoch
            self._last_price = tick.price
        return decision
