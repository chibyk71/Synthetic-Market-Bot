"""Canonical tick representation for durable historical storage.

Reuses the Milestone 1B :class:`~smb.deriv.history.Tick` fields and adds
the semantic instrument key used by project configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from smb.deriv.history import Tick


@dataclass(frozen=True, slots=True)
class StoredTick:
    """One durable historical tick.

    Identity for duplicate detection is ``(instrument, epoch, price)``.
    ``epoch`` is the Deriv Unix-second timestamp. ``price`` is the source
    float as returned by Deriv (no string conversion).
    """

    instrument: str
    epoch: int
    price: float

    def to_tick(self) -> Tick:
        """Convert to the in-memory :class:`~smb.deriv.history.Tick`."""
        return Tick(
            timestamp=datetime.fromtimestamp(self.epoch, tz=timezone.utc),
            price=self.price,
            epoch=self.epoch,
        )

    @classmethod
    def from_tick(cls, instrument: str, tick: Tick) -> StoredTick:
        return cls(instrument=instrument, epoch=tick.epoch, price=tick.price)
