"""Dataset statistics — answer 'what historical data do we have?'."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from smb.data.repository import TickRepository
from smb.data.validation import validate_ticks


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
    """Scan each instrument and produce coverage / integrity statistics."""
    rows: list[InstrumentStats] = []
    for instrument in repo.list_instruments():
        ticks = list(repo.iter_ticks(instrument))
        report = validate_ticks(ticks, expected_instrument=instrument)
        rows.append(
            InstrumentStats(
                instrument=instrument,
                tick_count=report.tick_count,
                earliest_epoch=report.earliest_epoch,
                latest_epoch=report.latest_epoch,
                duration_seconds=report.duration_seconds,
                min_price=report.min_price,
                max_price=report.max_price,
                duplicate_count=report.duplicate_count,
                non_monotonic_count=report.non_monotonic_count,
            )
        )
    return DatasetStats(instruments=tuple(rows))
