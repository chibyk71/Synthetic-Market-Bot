"""Dataset validation — structural, ordering, and coverage checks.

Validation does **not** silently re-sort source data. Ordering problems
are reported so integrity issues remain visible.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from smb.data.models import StoredTick


@dataclass
class ValidationReport:
    """Result of validating a tick sequence or dataset slice."""

    valid: bool
    tick_count: int = 0
    earliest_epoch: int | None = None
    latest_epoch: int | None = None
    duration_seconds: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    duplicate_count: int = 0
    non_monotonic_count: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        self.valid = False


def validate_ticks(
    ticks: Iterable[StoredTick],
    *,
    expected_instrument: str | None = None,
) -> ValidationReport:
    """Validate an iterable of stored ticks.

    Checks:
    * required fields (non-empty instrument, epoch >= 0, finite price)
    * optional instrument match
    * duplicate (instrument, epoch, price) occurrences
    * non-monotonic consecutive epochs (epoch_i < epoch_{i-1})
    """
    report = ValidationReport(valid=True)
    seen: set[tuple[str, int, float]] = set()
    prev_epoch: int | None = None
    min_price: float | None = None
    max_price: float | None = None
    earliest: int | None = None
    latest: int | None = None
    count = 0

    for tick in ticks:
        count += 1
        if not tick.instrument or not str(tick.instrument).strip():
            report.add_error(f"empty instrument at position {count}")
            continue
        if expected_instrument is not None and tick.instrument != expected_instrument:
            report.add_error(
                f"instrument mismatch at position {count}: "
                f"{tick.instrument!r} != {expected_instrument!r}"
            )
        if not isinstance(tick.epoch, int) or tick.epoch < 0:
            report.add_error(f"invalid epoch at position {count}: {tick.epoch!r}")
            continue
        try:
            price = float(tick.price)
            if price != price:  # NaN
                raise ValueError("NaN")
        except (TypeError, ValueError):
            report.add_error(f"invalid price at position {count}: {tick.price!r}")
            continue

        key = (tick.instrument, tick.epoch, tick.price)
        if key in seen:
            report.duplicate_count += 1
        else:
            seen.add(key)

        if prev_epoch is not None and tick.epoch < prev_epoch:
            report.non_monotonic_count += 1
        prev_epoch = tick.epoch

        if earliest is None or tick.epoch < earliest:
            earliest = tick.epoch
        if latest is None or tick.epoch > latest:
            latest = tick.epoch
        if min_price is None or price < min_price:
            min_price = price
        if max_price is None or price > max_price:
            max_price = price

    report.tick_count = count
    report.earliest_epoch = earliest
    report.latest_epoch = latest
    report.min_price = min_price
    report.max_price = max_price
    if earliest is not None and latest is not None and count > 1:
        report.duration_seconds = float(latest - earliest)
    if report.duplicate_count > 0:
        report.add_error(f"{report.duplicate_count} duplicate tick(s)")
    if report.non_monotonic_count > 0:
        report.add_error(f"{report.non_monotonic_count} non-monotonic pair(s)")
    return report


def validate_tick_list(ticks: Sequence[StoredTick], **kwargs) -> ValidationReport:
    return validate_ticks(ticks, **kwargs)
