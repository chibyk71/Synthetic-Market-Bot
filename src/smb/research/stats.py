"""Deterministic distribution helpers for baseline research analysis.

No external stats libraries. Uses only the standard library so results are
stable across environments.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class DistributionStats:
    """Summary of a numeric series used in baseline analysis.

    Percentiles use **Hyndman & Fan type 7** (R ``quantile`` default /
    NumPy default): linear interpolation between closest ranks.

    Empty series → ``count=0`` and all numeric fields ``None``.
    """

    count: int
    min: float | None
    median: float | None
    mean: float | None
    p75: float | None
    p90: float | None
    max: float | None

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


def _require_finite(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError("distribution values must be real numbers")
        if not math.isfinite(v):
            raise ValueError("distribution values must be finite")
        out.append(float(v))
    return out


def percentile(values: Sequence[float], p: float) -> float | None:
    """Return the p-th percentile of ``values`` (p in [0, 100]).

    Hyndman & Fan type 7 (inclusive linear interpolation)::

        h = 1 + (n - 1) * (p / 100)
        result = x[floor(h)] + (h - floor(h)) * (x[ceil(h)] - x[floor(h)])

    with 1-based indexing into the sorted sample. Empty input → ``None``.
    """
    if not (0.0 <= p <= 100.0):
        raise ValueError("percentile p must be in [0, 100]")
    data = _require_finite(values)
    n = len(data)
    if n == 0:
        return None
    if n == 1:
        return data[0]
    ordered = sorted(data)
    if p == 0.0:
        return ordered[0]
    if p == 100.0:
        return ordered[-1]
    h = 1.0 + (n - 1) * (p / 100.0)
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    # 1-based ranks → 0-based indices
    x_lo = ordered[lo - 1]
    x_hi = ordered[hi - 1]
    if lo == hi:
        return x_lo
    return x_lo + (h - lo) * (x_hi - x_lo)


def distribution(values: Sequence[float]) -> DistributionStats:
    """Build :class:`DistributionStats` for a finite numeric series."""
    data = _require_finite(values)
    n = len(data)
    if n == 0:
        return DistributionStats(
            count=0,
            min=None,
            median=None,
            mean=None,
            p75=None,
            p90=None,
            max=None,
        )
    ordered = sorted(data)
    mean = sum(data) / n
    return DistributionStats(
        count=n,
        min=ordered[0],
        median=percentile(ordered, 50.0),
        mean=mean,
        p75=percentile(ordered, 75.0),
        p90=percentile(ordered, 90.0),
        max=ordered[-1],
    )
