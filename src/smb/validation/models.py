"""Domain models for Milestone 3A strategy validation reports.

Aggregates completed 2C simulation and 2D research-metrics results into
immutable summary statistics. Does not re-simulate or alter outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction


def _require_non_neg_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def _require_finite_or_none(value: float | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number or None")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    """Absolute outcome counts for a cohort of candidates."""

    total: int
    filled: int
    no_fill: int
    wins: int  # TP among filled
    losses: int  # SL among filled
    timeouts: int  # TIMEOUT among filled

    def __post_init__(self) -> None:
        for name in (
            "total",
            "filled",
            "no_fill",
            "wins",
            "losses",
            "timeouts",
        ):
            _require_non_neg_int(getattr(self, name), name)
        if self.filled + self.no_fill != self.total:
            raise ValueError("filled + no_fill must equal total")
        if self.wins + self.losses + self.timeouts != self.filled:
            raise ValueError("wins + losses + timeouts must equal filled")


@dataclass(frozen=True, slots=True)
class RateStats:
    """Rates derived from :class:`OutcomeCounts`.

    ``fill_rate`` is relative to all candidates.
    ``win_rate`` / ``loss_rate`` / ``timeout_rate`` are relative to **filled**
    trades only. Undefined rates are ``None`` (never NaN).
    """

    fill_rate: float | None
    win_rate: float | None
    loss_rate: float | None
    timeout_rate: float | None

    def __post_init__(self) -> None:
        for name in ("fill_rate", "win_rate", "loss_rate", "timeout_rate"):
            _require_finite_or_none(getattr(self, name), name)
            value = getattr(self, name)
            if value is not None and not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} must be in [0, 1] or None")


@dataclass(frozen=True, slots=True)
class ExcursionStats:
    """MFE / MAE aggregates over filled trades with defined excursions."""

    avg_mfe: float | None
    avg_mae: float | None
    median_mfe: float | None
    median_mae: float | None
    sample_size: int
    """Number of filled trades contributing to excursion stats."""

    def __post_init__(self) -> None:
        _require_non_neg_int(self.sample_size, "sample_size")
        for name in ("avg_mfe", "avg_mae", "median_mfe", "median_mae"):
            _require_finite_or_none(getattr(self, name), name)
            value = getattr(self, name)
            if value is not None and value < 0.0:
                raise ValueError(f"{name} must be >= 0 or None")
        if self.sample_size == 0:
            if any(
                getattr(self, n) is not None
                for n in ("avg_mfe", "avg_mae", "median_mfe", "median_mae")
            ):
                raise ValueError("empty excursion sample requires None aggregates")


@dataclass(frozen=True, slots=True)
class DurationStats:
    """Trade duration aggregates over filled trades with known duration."""

    avg_duration_seconds: float | None
    sample_size: int

    def __post_init__(self) -> None:
        _require_non_neg_int(self.sample_size, "sample_size")
        _require_finite_or_none(self.avg_duration_seconds, "avg_duration_seconds")
        if self.avg_duration_seconds is not None and self.avg_duration_seconds < 0.0:
            raise ValueError("avg_duration_seconds must be >= 0 or None")
        if self.sample_size == 0 and self.avg_duration_seconds is not None:
            raise ValueError("empty duration sample requires avg_duration_seconds=None")


@dataclass(frozen=True, slots=True)
class CohortStats:
    """Full statistic bundle for one cohort (overall, by direction, etc.)."""

    counts: OutcomeCounts
    rates: RateStats
    excursions: ExcursionStats
    duration: DurationStats


@dataclass(frozen=True, slots=True)
class StrategyValidationReport:
    """Immutable strategy-level validation summary over historical results.

    Populations are explicit:
    - ``counts.total`` = all candidates
    - ``counts.filled`` = filled subset
    - rates and excursion/duration stats document which population they use
    """

    overall: CohortStats
    by_direction: Mapping[str, CohortStats]
    by_instrument: Mapping[str, CohortStats]
    by_outcome: Mapping[str, int]
    """Counts keyed by :class:`~smb.simulation.models.SimulationOutcome` value."""

    def __post_init__(self) -> None:
        # Freeze mappings so the report is deeply immutable at the public boundary
        object.__setattr__(
            self, "by_direction", MappingProxyType(dict(self.by_direction))
        )
        object.__setattr__(
            self, "by_instrument", MappingProxyType(dict(self.by_instrument))
        )
        object.__setattr__(self, "by_outcome", MappingProxyType(dict(self.by_outcome)))
        for key, count in self.by_outcome.items():
            _require_non_neg_int(count, f"by_outcome[{key!r}]")
        # Direction keys must be valid Direction values when present
        for key in self.by_direction:
            if key not in (Direction.LONG.value, Direction.SHORT.value):
                raise ValueError(f"invalid direction key: {key!r}")
        for key, count in self.by_outcome.items():
            if key not in {o.value for o in SimulationOutcome}:
                raise ValueError(f"invalid outcome key: {key!r}")
