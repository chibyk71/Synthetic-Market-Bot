"""Dataset statistics — answer 'what historical data do we have?'.

Uses DuckDB SQL aggregates so ~15M-tick instruments are never loaded into
Python lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from smb.data.repository import TickRepository


@dataclass(frozen=True)
class InstrumentStats:
    instrument: str
    tick_count: int
    earliest_epoch: int | None
    latest_epoch: int | None
    duration_seconds: float | None
    min_price: float | None
    max_price: float | None
    duplicate_count: int
    non_monotonic_count: int

    @property
    def earliest_timestamp(self) -> datetime | None:
        if self.earliest_epoch is None:
            return None
        return datetime.fromtimestamp(self.earliest_epoch, tz=timezone.utc)

    @property
    def latest_timestamp(self) -> datetime | None:
        if self.latest_epoch is None:
            return None
        return datetime.fromtimestamp(self.latest_epoch, tz=timezone.utc)


@dataclass(frozen=True)
class DatasetStats:
    instruments: tuple[InstrumentStats, ...]

    def for_instrument(self, key: str) -> InstrumentStats | None:
        for item in self.instruments:
            if item.instrument == key:
                return item
        return None


def compute_dataset_stats(repo: TickRepository) -> DatasetStats:
    """Per-instrument coverage via SQL; does not materialise all ticks."""
    rows: list[InstrumentStats] = []
    for instrument in repo.list_instruments():
        cov = repo.coverage(instrument)
        earliest = cov["earliest_epoch"]
        latest = cov["latest_epoch"]
        duration = None
        if earliest is not None and latest is not None and cov["tick_count"] > 1:
            duration = float(latest - earliest)
        rows.append(
            InstrumentStats(
                instrument=instrument,
                tick_count=cov["tick_count"],
                earliest_epoch=earliest,
                latest_epoch=latest,
                duration_seconds=duration,
                min_price=cov["min_price"],
                max_price=cov["max_price"],
                duplicate_count=cov["duplicate_count"],
                non_monotonic_count=cov["non_monotonic_count"],
            )
        )
    return DatasetStats(instruments=tuple(rows))
