"""Milestone 6C — Controlled Strategy Filter Experiments.

Research-only framework for evaluating **one** explicitly selected,
interpretable strategy filter against a frozen baseline experiment.

Does **not** modify strategy, trade construction, simulation semantics,
live/demo execution, or production risk rules. Filters use only information
available at signal time (no post-entry leakage). Thresholds are fixed by
configuration — never optimized against evaluation outcomes.

Results are descriptive observational comparisons, not proof of predictive
improvement.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.research.stats import DistributionStats, distribution
from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction

logger = logging.getLogger(__name__)

STUDY_VERSION = "6c.2"
INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_INSTRUMENTS: tuple[str, ...] = (INSTRUMENT_V75, INSTRUMENT_STEP)
# Frozen production-research baseline horizon. 6C compares filters against
# experiment rows produced at this horizon only — it does not reparameterize
# simulation exits. Any other horizon value is rejected.
DEFAULT_BASELINE_HORIZON_SECONDS = 900
FROZEN_BASELINE_HORIZON_SECONDS = DEFAULT_BASELINE_HORIZON_SECONDS
MIN_FILLED_FOR_STABLE_RATES = 10
MIN_SIGNALS_FOR_STABLE_COVERAGE = 5
SPARSE_SEGMENT_SIGNAL_THRESHOLD = 5


class ExperimentFamily(StrEnum):
    """Explicit, single-select experiment families (no combinatorial search)."""

    TREND_DIRECTION = "trend_direction"
    M15_CONTEXT = "m15_context"
    DISPLACEMENT_FVG_QUALITY = "displacement_fvg_quality"
    SESSION_REGIME = "session_regime"


class FilterDecisionKind(StrEnum):
    """Outcome of applying a filter to one signal row."""

    RETAIN = "retain"
    REJECT = "reject"
    UNAVAILABLE = "unavailable"


class CohortStatus(StrEnum):
    """Status label for a cohort metric block."""

    COMPLETE = "complete"
    ZERO_SIGNAL = "zero_signal"
    EMPTY = "empty"
    LOW_SAMPLE = "low_sample"
    NOT_ESTIMABLE = "not_estimable"


# ---------------------------------------------------------------------------
# Filter configuration (explicit, bounded, non-optimized)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrendDirectionFilterConfig:
    """Retain signals aligned with M15 directional bias; reject countertrend.

    ``treat_neutral_as`` controls neutral / missing bias:
    - ``unavailable``: mark as unavailable (excluded from retain/reject)
    - ``reject``: treat neutral as reject
    - ``retain``: treat neutral as retain (generally not recommended)
    """

    treat_neutral_as: Literal["unavailable", "reject", "retain"] = "unavailable"
    treat_missing_as: Literal["unavailable", "reject", "retain"] = "unavailable"


@dataclass(frozen=True, slots=True)
class M15ContextFilterConfig:
    """Stricter higher-timeframe alignment than simple direction match.

    Requires directional bias alignment **and** that last M15 close sits in
    the upper (long) or lower (short) portion of the recent M15 high-low
    range by at least ``min_close_range_position`` (0 = bottom, 1 = top).
    """

    min_close_range_position: float = 0.50
    require_bias_alignment: bool = True
    treat_missing_context_as: Literal["unavailable", "reject"] = "unavailable"

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_close_range_position):
            raise ValueError("min_close_range_position must be finite")
        if not (0.0 <= self.min_close_range_position <= 1.0):
            raise ValueError("min_close_range_position must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class DisplacementFVGQualityFilterConfig:
    """Bounded quality gate on displacement / FVG geometry at signal time.

    A signal is retained only when **all** enabled thresholds are met.
    Disabled thresholds use ``None``. No grid search — values are fixed.
    """

    min_body_range_ratio: float | None = 0.60
    min_body_atr_ratio: float | None = None
    min_fvg_size_atr_ratio: float | None = None
    treat_missing_metrics_as: Literal["unavailable", "reject"] = "unavailable"

    def __post_init__(self) -> None:
        for name in (
            "min_body_range_ratio",
            "min_body_atr_ratio",
            "min_fvg_size_atr_ratio",
        ):
            val = getattr(self, name)
            if val is None:
                continue
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(f"{name} must be a real number or None")
            if not math.isfinite(float(val)):
                raise ValueError(f"{name} must be finite")
            if float(val) < 0.0:
                raise ValueError(f"{name} must be >= 0")
        if (
            self.min_body_range_ratio is None
            and self.min_body_atr_ratio is None
            and self.min_fvg_size_atr_ratio is None
        ):
            raise ValueError(
                "at least one of min_body_range_ratio, min_body_atr_ratio, "
                "min_fvg_size_atr_ratio must be set"
            )


@dataclass(frozen=True, slots=True)
class SessionRegimeFilterConfig:
    """Segment signals by UTC hour-of-day buckets available at signal time.

    Buckets are inclusive start, exclusive end on the hour clock [0, 24).
    When ``retain_buckets`` is non-empty, only those buckets are retained;
    others are rejected. When empty, the experiment is pure segmentation
    (all signals retained, metrics reported per bucket).
    """

    # (name, start_hour_inclusive, end_hour_exclusive) — UTC
    buckets: tuple[tuple[str, int, int], ...] = (
        ("asia", 0, 8),
        ("london", 8, 16),
        ("new_york", 16, 24),
    )
    retain_buckets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.buckets:
            raise ValueError("buckets must be non-empty")
        names: set[str] = set()
        for name, start, end in self.buckets:
            if not name:
                raise ValueError("bucket name must be non-empty")
            if name in names:
                raise ValueError(f"duplicate bucket name: {name}")
            names.add(name)
            if not (0 <= start < end <= 24):
                raise ValueError(
                    f"bucket {name!r}: require 0 <= start < end <= 24 "
                    f"(got {start}, {end})"
                )
        for rb in self.retain_buckets:
            if rb not in names:
                raise ValueError(
                    f"retain_buckets entry {rb!r} is not a defined bucket name"
                )


@dataclass(frozen=True, slots=True)
class FilterExperimentConfig:
    """Single explicit experiment selection for one run.

    ``horizon_seconds`` is recorded for artifact provenance only and **must**
    equal :data:`FROZEN_BASELINE_HORIZON_SECONDS`. Milestone 6C does not
    re-simulate exits; filter cohorts are computed on experiment rows that
    were produced at the frozen baseline horizon.
    """

    family: ExperimentFamily
    trend: TrendDirectionFilterConfig = field(
        default_factory=TrendDirectionFilterConfig
    )
    m15_context: M15ContextFilterConfig = field(
        default_factory=M15ContextFilterConfig
    )
    displacement_fvg: DisplacementFVGQualityFilterConfig = field(
        default_factory=DisplacementFVGQualityFilterConfig
    )
    session: SessionRegimeFilterConfig = field(
        default_factory=SessionRegimeFilterConfig
    )
    horizon_seconds: int = FROZEN_BASELINE_HORIZON_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.family, ExperimentFamily):
            raise ValueError(f"unknown experiment family: {self.family!r}")
        if (
            not isinstance(self.horizon_seconds, int)
            or isinstance(self.horizon_seconds, bool)
            or self.horizon_seconds <= 0
        ):
            raise ValueError("horizon_seconds must be a positive int")
        if self.horizon_seconds != FROZEN_BASELINE_HORIZON_SECONDS:
            raise ValueError(
                f"horizon_seconds must equal frozen baseline "
                f"{FROZEN_BASELINE_HORIZON_SECONDS}s (got {self.horizon_seconds}); "
                "Milestone 6C analyzes filters against a frozen baseline and "
                "does not reparameterize simulation exits"
            )


# ---------------------------------------------------------------------------
# Filter decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FilterDecision:
    """Result of evaluating one row against the selected filter."""

    kind: FilterDecisionKind
    reason: str
    # Optional segment label (e.g. session bucket name)
    segment: str | None = None


def _aligned_with_bias(direction: str, bias: str | None) -> bool | None:
    """Return True if aligned, False if countertrend, None if not decidable."""
    if bias is None or bias == "neutral":
        return None
    d = direction.lower()
    if d == Direction.LONG.value:
        if bias == "bullish":
            return True
        if bias == "bearish":
            return False
        return None
    if d == Direction.SHORT.value:
        if bias == "bearish":
            return True
        if bias == "bullish":
            return False
        return None
    return None


def evaluate_trend_direction(
    row: TradeExperimentRow, cfg: TrendDirectionFilterConfig
) -> FilterDecision:
    """Trend-direction filter using M15 directional_bias at signal time."""
    ctx = row.signal.m15_context
    bias = ctx.directional_bias if ctx is not None else None
    if bias is None:
        mode = cfg.treat_missing_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="missing_m15_directional_bias",
        )
    if bias == "neutral":
        mode = cfg.treat_neutral_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="neutral_m15_directional_bias",
        )
    aligned = _aligned_with_bias(row.direction, bias)
    if aligned is True:
        return FilterDecision(
            FilterDecisionKind.RETAIN,
            reason=f"aligned_{bias}",
        )
    if aligned is False:
        return FilterDecision(
            FilterDecisionKind.REJECT,
            reason=f"countertrend_{bias}",
        )
    return FilterDecision(
        FilterDecisionKind.UNAVAILABLE,
        reason=f"undecidable_bias_{bias}",
    )


def evaluate_m15_context(
    row: TradeExperimentRow, cfg: M15ContextFilterConfig
) -> FilterDecision:
    """Stricter M15 context: bias alignment + close position in recent range."""
    ctx = row.signal.m15_context
    if ctx is None or ctx.last_m15_close is None:
        mode = cfg.treat_missing_context_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="missing_m15_context",
        )
    recent_high = ctx.recent_high
    recent_low = ctx.recent_low
    close = ctx.last_m15_close
    if recent_high is None or recent_low is None:
        mode = cfg.treat_missing_context_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="missing_m15_recent_range",
        )
    span = recent_high - recent_low
    if span <= 0.0 or not math.isfinite(span):
        mode = cfg.treat_missing_context_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="degenerate_m15_range",
        )
    # 0 = at low, 1 = at high
    position = (close - recent_low) / span
    if not math.isfinite(position):
        mode = cfg.treat_missing_context_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="nonfinite_close_range_position",
        )

    direction = row.direction.lower()
    if cfg.require_bias_alignment:
        aligned = _aligned_with_bias(row.direction, ctx.directional_bias)
        if aligned is None:
            mode = cfg.treat_missing_context_as
            return FilterDecision(
                FilterDecisionKind(mode),
                reason="missing_or_neutral_bias_for_alignment",
            )
        if aligned is False:
            return FilterDecision(
                FilterDecisionKind.REJECT,
                reason="countertrend_bias",
            )

    if direction == Direction.LONG.value:
        if position >= cfg.min_close_range_position:
            return FilterDecision(
                FilterDecisionKind.RETAIN,
                reason=f"long_close_position_{position:.3f}",
            )
        return FilterDecision(
            FilterDecisionKind.REJECT,
            reason=f"long_close_position_below_{cfg.min_close_range_position:.3f}",
        )
    if direction == Direction.SHORT.value:
        # For shorts, prefer close near the low end of the range
        max_pos = 1.0 - cfg.min_close_range_position
        if position <= max_pos:
            return FilterDecision(
                FilterDecisionKind.RETAIN,
                reason=f"short_close_position_{position:.3f}",
            )
        return FilterDecision(
            FilterDecisionKind.REJECT,
            reason=f"short_close_position_above_{max_pos:.3f}",
        )
    return FilterDecision(
        FilterDecisionKind.UNAVAILABLE,
        reason=f"unknown_direction_{row.direction}",
    )


def evaluate_displacement_fvg(
    row: TradeExperimentRow, cfg: DisplacementFVGQualityFilterConfig
) -> FilterDecision:
    """Bounded displacement / FVG quality gate (signal-time geometry only)."""
    disp = row.signal.displacement
    fvg = row.signal.fvg
    missing: list[str] = []

    def _check(name: str, value: float | None, threshold: float | None) -> str | None:
        if threshold is None:
            return None
        if value is None or not math.isfinite(value):
            missing.append(name)
            return None
        if value < threshold:
            return f"{name}_below_{threshold}"
        return None

    fail_reasons: list[str] = []
    r = _check(
        "body_range_ratio",
        getattr(disp, "body_range_ratio", None),
        cfg.min_body_range_ratio,
    )
    if r:
        fail_reasons.append(r)
    r = _check(
        "body_atr_ratio",
        getattr(disp, "body_atr_ratio", None),
        cfg.min_body_atr_ratio,
    )
    if r:
        fail_reasons.append(r)
    r = _check(
        "fvg_size_atr_ratio",
        getattr(fvg, "size_atr_ratio", None),
        cfg.min_fvg_size_atr_ratio,
    )
    if r:
        fail_reasons.append(r)

    if missing:
        mode = cfg.treat_missing_metrics_as
        return FilterDecision(
            FilterDecisionKind(mode),
            reason="missing_metrics:" + ",".join(missing),
        )
    if fail_reasons:
        return FilterDecision(
            FilterDecisionKind.REJECT,
            reason=";".join(fail_reasons),
        )
    return FilterDecision(
        FilterDecisionKind.RETAIN,
        reason="quality_thresholds_met",
    )


def _utc_hour(epoch: int) -> int:
    """UTC hour-of-day from Unix epoch (no timezone libs required)."""
    # 86400 seconds per day; epoch 0 is 1970-01-01 00:00:00 UTC
    return int((epoch % 86400) // 3600)


def evaluate_session_regime(
    row: TradeExperimentRow, cfg: SessionRegimeFilterConfig
) -> FilterDecision:
    """UTC session/regime segmentation; optional retain-bucket gate."""
    hour = _utc_hour(int(row.signal_epoch))
    bucket_name: str | None = None
    for name, start, end in cfg.buckets:
        if start <= hour < end:
            bucket_name = name
            break
    if bucket_name is None:
        return FilterDecision(
            FilterDecisionKind.UNAVAILABLE,
            reason=f"hour_{hour}_outside_defined_buckets",
            segment=None,
        )
    if not cfg.retain_buckets:
        # Pure segmentation: all signals retained, labeled by segment
        return FilterDecision(
            FilterDecisionKind.RETAIN,
            reason=f"session_{bucket_name}",
            segment=bucket_name,
        )
    if bucket_name in cfg.retain_buckets:
        return FilterDecision(
            FilterDecisionKind.RETAIN,
            reason=f"session_{bucket_name}_retained",
            segment=bucket_name,
        )
    return FilterDecision(
        FilterDecisionKind.REJECT,
        reason=f"session_{bucket_name}_not_in_retain_list",
        segment=bucket_name,
    )


def evaluate_filter(
    row: TradeExperimentRow, config: FilterExperimentConfig
) -> FilterDecision:
    """Dispatch to the single selected experiment family."""
    if config.family is ExperimentFamily.TREND_DIRECTION:
        return evaluate_trend_direction(row, config.trend)
    if config.family is ExperimentFamily.M15_CONTEXT:
        return evaluate_m15_context(row, config.m15_context)
    if config.family is ExperimentFamily.DISPLACEMENT_FVG_QUALITY:
        return evaluate_displacement_fvg(row, config.displacement_fvg)
    if config.family is ExperimentFamily.SESSION_REGIME:
        return evaluate_session_regime(row, config.session)
    raise ValueError(f"unsupported experiment family: {config.family}")


# ---------------------------------------------------------------------------
# Cohort metrics
# ---------------------------------------------------------------------------


def _pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _maximum_drawdown_r(realized_rs: Sequence[float]) -> float | None:
    if not realized_rs:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in realized_rs:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _outcome_str(row: TradeExperimentRow) -> str | None:
    if row.outcome is None:
        return None
    return row.outcome.value if hasattr(row.outcome, "value") else str(row.outcome)


@dataclass(frozen=True, slots=True)
class CohortMetrics:
    """Metrics for one cohort with explicit denominators."""

    label: str
    status: CohortStatus
    # Funnel
    signals: int
    candidates_accepted: int
    candidates_rejected: int
    filter_retained: int
    filter_rejected: int
    filter_unavailable: int
    no_fill: int
    filled: int
    tp: int
    sl: int
    timeout: int
    # Rates (None when not estimable)
    win_rate: float | None
    total_r: float | None
    average_r: float | None
    median_r: float | None
    maximum_drawdown_r: float | None
    coverage_pct: float | None  # filter_retained / baseline signals
    retention_pct: float | None  # same as coverage when baseline = all signals
    # Distributions over filled trades only (NO_FILL excluded)
    mae: DistributionStats | None
    mfe: DistributionStats | None
    duration: DistributionStats | None
    rejection_reasons: Mapping[str, int]
    filter_reasons: Mapping[str, int]
    warnings: tuple[str, ...]
    # Denominator reconciliation notes
    denominator_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "status": self.status.value,
            "signals": self.signals,
            "candidates_accepted": self.candidates_accepted,
            "candidates_rejected": self.candidates_rejected,
            "filter_retained": self.filter_retained,
            "filter_rejected": self.filter_rejected,
            "filter_unavailable": self.filter_unavailable,
            "no_fill": self.no_fill,
            "filled": self.filled,
            "tp": self.tp,
            "sl": self.sl,
            "timeout": self.timeout,
            "win_rate": self.win_rate,
            "total_r": self.total_r,
            "average_r": self.average_r,
            "median_r": self.median_r,
            "maximum_drawdown_r": self.maximum_drawdown_r,
            "coverage_pct": self.coverage_pct,
            "retention_pct": self.retention_pct,
            "mae": self.mae.to_dict() if self.mae else None,
            "mfe": self.mfe.to_dict() if self.mfe else None,
            "duration": self.duration.to_dict() if self.duration else None,
            "rejection_reasons": dict(self.rejection_reasons),
            "filter_reasons": dict(self.filter_reasons),
            "warnings": list(self.warnings),
            "denominator_notes": list(self.denominator_notes),
        }


def compute_cohort_metrics(
    *,
    label: str,
    rows: Sequence[TradeExperimentRow],
    decisions: Sequence[FilterDecision] | None = None,
    baseline_signal_count: int | None = None,
    cohort_kind: Literal["baseline", "retained", "rejected", "unavailable", "segment"] = "baseline",
) -> CohortMetrics:
    """Compute metrics for a cohort of experiment rows.

    When ``decisions`` is provided it must align 1:1 with ``rows`` (used only
    for filter reason tallies on non-baseline cohorts). Baseline cohort uses
    all rows; retained/rejected subsets are pre-filtered by the caller.
    """
    warnings: list[str] = []
    notes: list[str] = [
        "win_rate denominator = filled = TP + SL + TIMEOUT (NO_FILL excluded).",
        "realized R uses filled trades with finite realized_r only.",
        "MAE/MFE/duration distributions exclude NO_FILL and nonfinite values.",
        "candidates_accepted/rejected = trade-construction funnel "
        "(pre-filter risk gate), not the strategy filter.",
        "filter_retained/rejected/unavailable = selected filter classification "
        "of this cohort's rows (on the baseline cohort these are the full "
        "population funnel totals, not a post-filter trade count).",
    ]

    n = len(rows)
    if n == 0:
        status = CohortStatus.EMPTY
        if cohort_kind == "baseline":
            status = CohortStatus.ZERO_SIGNAL
        warnings.append(f"cohort {label!r} has zero rows")
        return CohortMetrics(
            label=label,
            status=status,
            signals=0,
            candidates_accepted=0,
            candidates_rejected=0,
            filter_retained=0,
            filter_rejected=0,
            filter_unavailable=0,
            no_fill=0,
            filled=0,
            tp=0,
            sl=0,
            timeout=0,
            win_rate=None,
            total_r=None,
            average_r=None,
            median_r=None,
            maximum_drawdown_r=None,
            coverage_pct=None,
            retention_pct=None,
            mae=None,
            mfe=None,
            duration=None,
            rejection_reasons={},
            filter_reasons={},
            warnings=tuple(warnings),
            denominator_notes=tuple(notes),
        )

    accepted = [r for r in rows if r.accepted]
    rejected_rows = [r for r in rows if not r.accepted]
    candidates_accepted = len(accepted)
    candidates_rejected = len(rejected_rows)

    rej_counter: Counter[str] = Counter()
    for r in rejected_rows:
        key = (
            r.rejection_reason.value
            if r.rejection_reason is not None
            and hasattr(r.rejection_reason, "value")
            else (str(r.rejection_reason) if r.rejection_reason else "unknown")
        )
        rej_counter[key] += 1

    filt_counter: Counter[str] = Counter()
    filter_retained = 0
    filter_rejected = 0
    filter_unavailable = 0
    if decisions is not None:
        if len(decisions) != len(rows):
            raise ValueError("decisions must align 1:1 with rows")
        for d in decisions:
            filt_counter[d.reason] += 1
            if d.kind is FilterDecisionKind.RETAIN:
                filter_retained += 1
            elif d.kind is FilterDecisionKind.REJECT:
                filter_rejected += 1
            else:
                filter_unavailable += 1
    elif cohort_kind == "baseline":
        filter_retained = n  # baseline includes all signals
        filter_rejected = 0
        filter_unavailable = 0
    elif cohort_kind == "retained":
        filter_retained = n
    elif cohort_kind == "rejected":
        filter_rejected = n
    elif cohort_kind == "unavailable":
        filter_unavailable = n

    tp = sl = timeout = no_fill = 0
    realized_rs: list[float] = []
    mae_vals: list[float] = []
    mfe_vals: list[float] = []
    dur_vals: list[float] = []

    for r in accepted:
        out = _outcome_str(r)
        if out == SimulationOutcome.NO_FILL.value:
            no_fill += 1
            continue
        if out == SimulationOutcome.TP.value:
            tp += 1
        elif out == SimulationOutcome.SL.value:
            sl += 1
        elif out == SimulationOutcome.TIMEOUT.value:
            timeout += 1
        # filled path
        if r.realized_r is not None and math.isfinite(r.realized_r):
            realized_rs.append(float(r.realized_r))
        if r.mae is not None and math.isfinite(r.mae):
            mae_vals.append(float(r.mae))
        if r.mfe is not None and math.isfinite(r.mfe):
            mfe_vals.append(float(r.mfe))
        if r.duration_seconds is not None and r.duration_seconds >= 0:
            dur_vals.append(float(r.duration_seconds))

    filled = tp + sl + timeout
    # Sanity: accepted should equal filled + no_fill
    if candidates_accepted != filled + no_fill:
        warnings.append(
            f"denominator mismatch: accepted={candidates_accepted} "
            f"!= filled({filled})+no_fill({no_fill})"
        )

    win_rate = (tp / filled) if filled > 0 else None
    total_r = sum(realized_rs) if realized_rs else None
    average_r = _mean(realized_rs)
    median_r = _median(realized_rs)
    max_dd = _maximum_drawdown_r(realized_rs)

    base_n = baseline_signal_count if baseline_signal_count is not None else n
    coverage = _pct(filter_retained if cohort_kind == "baseline" else n, base_n)
    # For retained cohort, coverage = retained / baseline signals
    if cohort_kind == "retained":
        coverage = _pct(n, base_n)
    elif cohort_kind == "rejected":
        coverage = _pct(n, base_n)
    elif cohort_kind == "unavailable":
        coverage = _pct(n, base_n)

    status = CohortStatus.COMPLETE
    if n == 0:
        status = CohortStatus.ZERO_SIGNAL
    elif filled < MIN_FILLED_FOR_STABLE_RATES or n < MIN_SIGNALS_FOR_STABLE_COVERAGE:
        status = CohortStatus.LOW_SAMPLE
        warnings.append(
            f"low-sample cohort (signals={n}, filled={filled}); "
            "rates are descriptive only"
        )

    mae_dist = distribution(mae_vals) if mae_vals else None
    mfe_dist = distribution(mfe_vals) if mfe_vals else None
    dur_dist = distribution(dur_vals) if dur_vals else None

    return CohortMetrics(
        label=label,
        status=status,
        signals=n,
        candidates_accepted=candidates_accepted,
        candidates_rejected=candidates_rejected,
        filter_retained=filter_retained,
        filter_rejected=filter_rejected,
        filter_unavailable=filter_unavailable,
        no_fill=no_fill,
        filled=filled,
        tp=tp,
        sl=sl,
        timeout=timeout,
        win_rate=win_rate,
        total_r=total_r,
        average_r=average_r,
        median_r=median_r,
        maximum_drawdown_r=max_dd,
        coverage_pct=coverage,
        retention_pct=coverage,
        mae=mae_dist,
        mfe=mfe_dist,
        duration=dur_dist,
        rejection_reasons=dict(sorted(rej_counter.items())),
        filter_reasons=dict(sorted(filt_counter.items())),
        warnings=tuple(warnings),
        denominator_notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstrumentFilterResult:
    """Per-instrument filter experiment result."""

    instrument: str
    start_epoch: int | None
    end_epoch: int | None
    horizon_seconds: int
    baseline: CohortMetrics
    retained: CohortMetrics
    rejected: CohortMetrics
    unavailable: CohortMetrics
    segments: Mapping[str, CohortMetrics]
    filter_reason_counts: Mapping[str, int]
    denominator_ok: bool
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "horizon_seconds": self.horizon_seconds,
            "baseline": self.baseline.to_dict(),
            "retained": self.retained.to_dict(),
            "rejected": self.rejected.to_dict(),
            "unavailable": self.unavailable.to_dict(),
            "segments": {k: v.to_dict() for k, v in self.segments.items()},
            "filter_reason_counts": dict(self.filter_reason_counts),
            "denominator_ok": self.denominator_ok,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class FilterExperimentReport:
    """Full multi-instrument controlled filter experiment report."""

    study_version: str
    family: str
    config: dict[str, Any]
    instruments: tuple[str, ...]
    horizon_seconds: int
    results: Mapping[str, InstrumentFilterResult]
    global_limitations: tuple[str, ...]
    research_disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_version": self.study_version,
            "family": self.family,
            "config": self.config,
            "instruments": list(self.instruments),
            "horizon_seconds": self.horizon_seconds,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "global_limitations": list(self.global_limitations),
            "research_disclaimer": self.research_disclaimer,
        }


RESEARCH_DISCLAIMER = (
    "These results are descriptive observational research only. "
    "They do not prove predictive improvement, causal filter efficacy, "
    "or out-of-sample edge. The baseline strategy and simulation remain "
    "unchanged. Do not use these outputs as trading advice."
)


def _config_to_dict(config: FilterExperimentConfig) -> dict[str, Any]:
    d: dict[str, Any] = {
        "family": config.family.value,
        "horizon_seconds": config.horizon_seconds,
    }
    if config.family is ExperimentFamily.TREND_DIRECTION:
        d["trend"] = asdict(config.trend)
    elif config.family is ExperimentFamily.M15_CONTEXT:
        d["m15_context"] = asdict(config.m15_context)
    elif config.family is ExperimentFamily.DISPLACEMENT_FVG_QUALITY:
        d["displacement_fvg"] = asdict(config.displacement_fvg)
    elif config.family is ExperimentFamily.SESSION_REGIME:
        d["session"] = {
            "buckets": [
                {"name": n, "start_hour": s, "end_hour": e}
                for n, s, e in config.session.buckets
            ],
            "retain_buckets": list(config.session.retain_buckets),
        }
    return d


def analyze_instrument_filter(
    result: ExperimentResult,
    config: FilterExperimentConfig,
) -> InstrumentFilterResult:
    """Apply the selected filter to one instrument experiment result."""
    rows = list(result.rows)
    decisions = [evaluate_filter(r, config) for r in rows]

    retained_rows = [
        r
        for r, d in zip(rows, decisions, strict=True)
        if d.kind is FilterDecisionKind.RETAIN
    ]
    rejected_rows = [
        r
        for r, d in zip(rows, decisions, strict=True)
        if d.kind is FilterDecisionKind.REJECT
    ]
    unavailable_rows = [
        r
        for r, d in zip(rows, decisions, strict=True)
        if d.kind is FilterDecisionKind.UNAVAILABLE
    ]
    retained_decisions = [
        d for d in decisions if d.kind is FilterDecisionKind.RETAIN
    ]
    rejected_decisions = [
        d for d in decisions if d.kind is FilterDecisionKind.REJECT
    ]
    unavailable_decisions = [
        d for d in decisions if d.kind is FilterDecisionKind.UNAVAILABLE
    ]

    base_n = len(rows)
    baseline = compute_cohort_metrics(
        label="baseline",
        rows=rows,
        decisions=decisions,
        baseline_signal_count=base_n,
        cohort_kind="baseline",
    )
    retained = compute_cohort_metrics(
        label="filter_retained",
        rows=retained_rows,
        decisions=retained_decisions,
        baseline_signal_count=base_n,
        cohort_kind="retained",
    )
    rejected = compute_cohort_metrics(
        label="filter_rejected",
        rows=rejected_rows,
        decisions=rejected_decisions,
        baseline_signal_count=base_n,
        cohort_kind="rejected",
    )
    unavailable = compute_cohort_metrics(
        label="filter_unavailable",
        rows=unavailable_rows,
        decisions=unavailable_decisions,
        baseline_signal_count=base_n,
        cohort_kind="unavailable",
    )

    # Segment metrics (session family or any decision with segment labels)
    segments: dict[str, CohortMetrics] = {}
    segment_names = sorted(
        {d.segment for d in decisions if d.segment is not None}
    )
    for seg_name in segment_names:
        seg_rows = [
            r
            for r, d in zip(rows, decisions, strict=True)
            if d.segment == seg_name
        ]
        seg_decs = [d for d in decisions if d.segment == seg_name]
        segments[seg_name] = compute_cohort_metrics(
            label=f"segment:{seg_name}",
            rows=seg_rows,
            decisions=seg_decs,
            baseline_signal_count=base_n,
            cohort_kind="segment",
        )
        if seg_rows and len(seg_rows) < SPARSE_SEGMENT_SIGNAL_THRESHOLD:
            # already flagged LOW_SAMPLE inside compute; add explicit note
            pass

    reason_counts: Counter[str] = Counter(d.reason for d in decisions)

    # Denominator reconciliation: baseline signals == retained+rejected+unavailable
    denom_ok = base_n == (
        len(retained_rows) + len(rejected_rows) + len(unavailable_rows)
    )
    limitations: list[str] = []
    if not denom_ok:
        limitations.append(
            "denominator mismatch: baseline signals != "
            "retained + rejected + unavailable"
        )
    if base_n == 0:
        limitations.append("zero-signal instrument")
    if retained.status is CohortStatus.LOW_SAMPLE:
        limitations.append("retained cohort is low-sample")
    if rejected.status is CohortStatus.LOW_SAMPLE and len(rejected_rows) > 0:
        limitations.append("rejected cohort is low-sample")
    for seg_name, seg in segments.items():
        if seg.status is CohortStatus.LOW_SAMPLE:
            limitations.append(f"segment {seg_name!r} is sparse/low-sample")
    limitations.append(
        "Observational segmentation only; no causal claim of filter improvement."
    )
    limitations.append(
        "Filter uses signal-time context only (no post-entry information)."
    )

    summary = result.summary
    return InstrumentFilterResult(
        instrument=summary.instrument,
        start_epoch=summary.start_epoch,
        end_epoch=summary.end_epoch,
        horizon_seconds=config.horizon_seconds,
        baseline=baseline,
        retained=retained,
        rejected=rejected,
        unavailable=unavailable,
        segments=segments,
        filter_reason_counts=dict(sorted(reason_counts.items())),
        denominator_ok=denom_ok,
        limitations=tuple(limitations),
    )


def analyze_filter_experiment(
    results: Mapping[str, ExperimentResult],
    config: FilterExperimentConfig,
) -> FilterExperimentReport:
    """Analyze filter experiment across instruments."""
    instrument_results: dict[str, InstrumentFilterResult] = {}
    for name, exp in results.items():
        instrument_results[name] = analyze_instrument_filter(exp, config)

    global_lims = [
        "Single explicitly selected experiment per run (no multi-filter search).",
        f"Simulation horizon is frozen at {FROZEN_BASELINE_HORIZON_SECONDS}s; "
        "6C does not reparameterize TP/SL/TIMEOUT exits when analyzing filters.",
        "Baseline simulation configuration is frozen for this comparison.",
        "Thresholds are configuration inputs, not optimized on this evaluation set.",
        RESEARCH_DISCLAIMER,
    ]
    if not results:
        global_lims.append("No experiment results provided (empty dataset).")

    return FilterExperimentReport(
        study_version=STUDY_VERSION,
        family=config.family.value,
        config=_config_to_dict(config),
        instruments=tuple(results.keys()),
        horizon_seconds=config.horizon_seconds,
        results=instrument_results,
        global_limitations=tuple(global_lims),
        research_disclaimer=RESEARCH_DISCLAIMER,
    )


def run_filter_experiment_on_results(
    results: Mapping[str, ExperimentResult],
    config: FilterExperimentConfig,
) -> FilterExperimentReport:
    """Public entry: analyze pre-computed experiment results under one filter."""
    return analyze_filter_experiment(results, config)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def write_filter_experiment_artifacts(
    report: FilterExperimentReport, output_dir: Path
) -> tuple[Path, Path]:
    """Write JSON + Markdown artifacts; return paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "strategy_filter_experiment.json"
    md_path = output_dir / "strategy_filter_experiment_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_filter_experiment_report(report) + "\n", encoding="utf-8")
    return json_path, md_path


def _fmt(x: float | None, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    if not math.isfinite(x):
        return "n/a"
    return f"{x:.{digits}f}"


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{100.0 * x:.1f}%"


def _cohort_md(c: CohortMetrics) -> list[str]:
    lines = [
        f"**{c.label}** — status=`{c.status.value}`",
        f"- signals: {c.signals} | accepted: {c.candidates_accepted} | "
        f"rejected (construction): {c.candidates_rejected}",
        f"- filter retained/rejected/unavailable: "
        f"{c.filter_retained}/{c.filter_rejected}/{c.filter_unavailable}",
        f"- NO_FILL: {c.no_fill} | filled: {c.filled} "
        f"(TP={c.tp}, SL={c.sl}, TIMEOUT={c.timeout})",
        f"- win rate (filled): {_fmt_pct(c.win_rate)}",
        f"- total R / avg R / median R: "
        f"{_fmt(c.total_r)} / {_fmt(c.average_r)} / {_fmt(c.median_r)}",
        f"- max drawdown R: {_fmt(c.maximum_drawdown_r)}",
        f"- coverage/retention vs baseline signals: {_fmt_pct(c.coverage_pct)}",
    ]
    if c.mae is not None:
        lines.append(
            f"- MAE (filled): n={c.mae.count} median={_fmt(c.mae.median)} "
            f"mean={_fmt(c.mae.mean)}"
        )
    if c.mfe is not None:
        lines.append(
            f"- MFE (filled): n={c.mfe.count} median={_fmt(c.mfe.median)} "
            f"mean={_fmt(c.mfe.mean)}"
        )
    if c.duration is not None:
        lines.append(
            f"- duration s (filled): n={c.duration.count} "
            f"median={_fmt(c.duration.median)} mean={_fmt(c.duration.mean)}"
        )
    if c.filter_reasons:
        lines.append(f"- filter reasons: {dict(c.filter_reasons)}")
    if c.rejection_reasons:
        lines.append(f"- construction rejection reasons: {dict(c.rejection_reasons)}")
    if c.warnings:
        lines.append(f"- warnings: {list(c.warnings)}")
    return lines


def format_filter_experiment_report(report: FilterExperimentReport) -> str:
    """Human-readable Markdown report."""
    lines: list[str] = [
        f"# Controlled Strategy Filter Experiment (Milestone 6C / {report.study_version})",
        "",
        f"**Experiment family:** `{report.family}`",
        f"**Horizon (seconds):** {report.horizon_seconds} "
        f"(frozen baseline; filter analysis does not re-simulate exits)",
        f"**Instruments:** {', '.join(report.instruments) or '(none)'}",
        "",
        "## Run configuration",
        "",
        "```json",
        json.dumps(report.config, indent=2, sort_keys=True),
        "```",
        "",
        "## Research disclaimer",
        "",
        report.research_disclaimer,
        "",
        "## Global limitations",
        "",
    ]
    for lim in report.global_limitations:
        lines.append(f"- {lim}")
    lines.append("")

    if not report.results:
        lines.extend(
            [
                "## Results",
                "",
                "_No instrument results (empty dataset or no signals)._",
                "",
            ]
        )
        return "\n".join(lines)

    for inst, ir in report.results.items():
        lines.extend(
            [
                f"## Instrument: `{inst}`",
                "",
                f"- date range (epochs): {ir.start_epoch} → {ir.end_epoch}",
                f"- horizon: {ir.horizon_seconds}s",
                f"- denominator reconciliation OK: {ir.denominator_ok}",
                f"- filter reason counts: {dict(ir.filter_reason_counts)}",
                "",
                "### Baseline cohort",
                "",
            ]
        )
        lines.extend(_cohort_md(ir.baseline))
        lines.extend(["", "### Filter-retained cohort", ""])
        lines.extend(_cohort_md(ir.retained))
        lines.extend(["", "### Filter-rejected cohort", ""])
        lines.extend(_cohort_md(ir.rejected))
        lines.extend(["", "### Filter-unavailable cohort", ""])
        lines.extend(_cohort_md(ir.unavailable))
        if ir.segments:
            lines.extend(["", "### Segments", ""])
            for seg_name, seg in ir.segments.items():
                lines.append(f"#### `{seg_name}`")
                lines.extend(_cohort_md(seg))
                lines.append("")
        lines.extend(["", "### Instrument limitations", ""])
        for lim in ir.limitations:
            lines.append(f"- {lim}")
        lines.append("")

    lines.extend(
        [
            "## Cohort definitions",
            "",
            "- **Baseline cohort:** the full strategy-signal population for the "
            "run (construction-accepted + construction-rejected). Outcome and R "
            "metrics on this block describe the unfiltered baseline.",
            "- **Filter classification funnel (on baseline block):** "
            "`filter_retained` / `filter_rejected` / `filter_unavailable` on the "
            "baseline cohort are the selected filter's classification of the "
            "*entire* signal population — not a count of baseline trades.",
            "- **Filter-retained cohort:** signals the selected filter keeps; "
            "outcome metrics here are the retained subset only.",
            "- **Filter-rejected cohort:** signals the selected filter rejects.",
            "- **Filter-unavailable cohort:** signals lacking required context "
            "or undecidable under the filter rule.",
            "- **Construction funnel:** `candidates_accepted` / "
            "`candidates_rejected` are the trade-construction gate "
            "(independent of the strategy filter).",
            "- **NO_FILL:** accepted candidates that never filled; excluded from "
            "win rate and R / MAE / MFE / duration distributions.",
            "- **Executed outcomes:** TP / SL / TIMEOUT among filled trades.",
            "",
            "## Metric denominators",
            "",
            "- Signal count = strategy signals in the cohort.",
            "- Candidate accepted/rejected = trade construction gate.",
            "- Filter retained/rejected/unavailable partition the signal set "
            "(on baseline: full-population classification funnel).",
            "- Win rate = TP / (TP + SL + TIMEOUT).",
            "- Coverage/retention = cohort size / baseline signal count.",
            f"- Simulation horizon is frozen at {FROZEN_BASELINE_HORIZON_SECONDS}s; "
            "6C does not reparameterize exits.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_experiment_family(name: str) -> ExperimentFamily:
    """Parse CLI experiment name; raise ValueError on unknown."""
    key = name.strip().lower().replace("-", "_")
    try:
        return ExperimentFamily(key)
    except ValueError as exc:
        valid = ", ".join(f.value for f in ExperimentFamily)
        raise ValueError(
            f"unknown experiment {name!r}; valid choices: {valid}"
        ) from exc


def build_filter_config_from_args(
    family: ExperimentFamily,
    *,
    horizon_seconds: int = FROZEN_BASELINE_HORIZON_SECONDS,
    treat_neutral_as: str = "unavailable",
    treat_missing_as: str = "unavailable",
    min_close_range_position: float = 0.50,
    require_bias_alignment: bool = True,
    min_body_range_ratio: float | None = 0.60,
    min_body_atr_ratio: float | None = None,
    min_fvg_size_atr_ratio: float | None = None,
    session_retain_buckets: Sequence[str] | None = None,
) -> FilterExperimentConfig:
    """Build a validated FilterExperimentConfig from explicit CLI-style args."""
    trend = TrendDirectionFilterConfig(
        treat_neutral_as=treat_neutral_as,  # type: ignore[arg-type]
        treat_missing_as=treat_missing_as,  # type: ignore[arg-type]
    )
    m15 = M15ContextFilterConfig(
        min_close_range_position=min_close_range_position,
        require_bias_alignment=require_bias_alignment,
    )
    disp = DisplacementFVGQualityFilterConfig(
        min_body_range_ratio=min_body_range_ratio,
        min_body_atr_ratio=min_body_atr_ratio,
        min_fvg_size_atr_ratio=min_fvg_size_atr_ratio,
    )
    session = SessionRegimeFilterConfig(
        retain_buckets=tuple(session_retain_buckets or ()),
    )
    return FilterExperimentConfig(
        family=family,
        trend=trend,
        m15_context=m15,
        displacement_fvg=disp,
        session=session,
        horizon_seconds=horizon_seconds,
    )


def leakage_review_notes() -> tuple[str, ...]:
    """Document anti-leakage guarantees for the study."""
    return (
        "Filters read only StrategySignal fields fixed at signal_epoch "
        "(m15_context, displacement, fvg, signal_epoch for UTC hour).",
        "No use of entry_time, exit_time, outcome, realized_r, MAE, or MFE "
        "inside filter decision logic.",
        "Simulation and trade construction are unchanged from the baseline run.",
        "Thresholds are supplied a priori via configuration; not fit on outcomes.",
        f"Horizon is frozen at {FROZEN_BASELINE_HORIZON_SECONDS}s; analysis does "
        "not reparameterize simulation exits for filter comparisons.",
    )
