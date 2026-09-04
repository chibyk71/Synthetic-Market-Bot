"""Historical tick replay and normalized tick-stream abstraction.

HistoricalReplay and a future LiveFeed both expose the same TickStream
interface so downstream consumers (CandleBuilder, etc.) do not care about
the data source.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Protocol, runtime_checkable

from smb.deriv.history import Tick


@runtime_checkable
class TickStream(Protocol):
    """Minimal interface for a source of normalized ticks.

    Implementations must yield ticks in the order they are supplied /
    received — never silently reorder.
    """

    def __iter__(self) -> Iterator[Tick]:
        ...


class HistoricalReplay:
    """Deterministic replay of a sequence of :class:`~smb.deriv.history.Tick`.

    Ticks are emitted strictly in the supplied source order. Identical
    input always produces identical output. No wall-clock sleeping is
    performed; optional ``on_tick`` is invoked synchronously for each
    tick so tests remain instant.

    Parameters
    ----------
    ticks:
        Iterable of normalized ticks. Materialised once so the sequence
        can be replayed more than once if needed.
    """

    def __init__(self, ticks: Iterable[Tick]) -> None:
        # Materialise so empty iterables and generators behave cleanly
        # and replay is repeatable from the same instance if desired.
        self._ticks: tuple[Tick, ...] = tuple(ticks)

    def __iter__(self) -> Iterator[Tick]:
        """Yield ticks in exact source order."""
        yield from self._ticks

    def __len__(self) -> int:
        return len(self._ticks)

    @property
    def ticks(self) -> Sequence[Tick]:
        """Read-only view of the stored tick sequence."""
        return self._ticks

    def run(self, on_tick: Callable[[Tick], None] | None = None) -> list[Tick]:
        """Replay all ticks, optionally invoking ``on_tick`` for each.

        Returns the list of ticks in source order (same as iteration).
        """
        emitted: list[Tick] = []
        for tick in self._ticks:
            if on_tick is not None:
                on_tick(tick)
            emitted.append(tick)
        return emitted
