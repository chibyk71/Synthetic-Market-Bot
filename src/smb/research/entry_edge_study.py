"""Milestone 6C — Entry Edge / Early Excursion Study.

Research-only observational diagnostic of the frozen 6B entry cohort.
Diagnoses whether filled trades show directional edge in the first 1–5 minutes
after fill via early excursion (MFE/MAE) and timeout mark-to-market accounting.

Does **not** modify strategy, signal generation, trade construction, simulation
semantics, live/demo execution, or the canonical 6B baseline.  Excursion is
post-entry diagnostic information only and is never used as a pre-entry feature
or signal gate.
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.research.stats import distribution, percentile
from smb.simulation.models import SimulationOutcome

logger = logging.getLogger(__name__)

STUDY_VERSION = "6c.1"
INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_INSTRUMENTS: tuple[str, ...] = (INSTRUMENT_V75, INSTRUMENT_STEP)

# Canonical frozen production-research baseline horizon (seconds).
FROZEN_BASELINE_HORIZON_SECONDS = 900
EXTENDED_CROSS_MARK_SECONDS = 1800

# Pre-registered early-excursion timepoints (seconds after fill).
EXCURSION_TIMEPOINTS: tuple[int, ...] = (30, 60, 90, 120, 180, 300)
PRIMARY_ENDPOINT_TIMEPOINT = 180  # confirmatory; others exploratory only

# Reach fractions (R multiples).
REACH_THRESHOLDS: tuple[float, ...] = (0.25, 0.50)

# Pre-registered plausible true effect for MDE (median excursion_diff_r).
PLAUSIBLE_EFFECT_MEDIAN_DIFF_R = 0.20

# Bootstrap configuration (deterministic).
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 6_031_800  # fixed for reproducibility

# Cost model: canonical spread cost expressed in R units.
DEFAULT_CANONICAL_SPREAD_COST_R = 0.05
SPREAD_SENSITIVITIES: tuple[float, ...] = (-0.50, 0.0, 0.50)  # −50%, canonical, +50%

VERDICT_EDGE_DETECTED = "EDGE_DETECTED"
VERDICT_CONDITIONAL_EDGE = "CONDITIONAL_EDGE"
VERDICT_UNDERPOWERED = "UNDERPOWERED"
VERDICT_NO_EDGE = "NO_EDGE"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CohortKey:
    """Frozen cohort identity: (instrument, signal_epoch, direction)."""

    instrument: str
    signal_epoch: int
    direction: str

    def as_tuple(self) -> tuple[str, int, str]:
        return (self.instrument, int(self.signal_epoch), str(self.direction))


@dataclass(frozen=True, slots=True)
class EntryEdgeTradeRecord:
    """Validated internal representation of one trade for the entry-edge study."""

    instrument: str
    direction: str
    signal_epoch: int
    outcome: str
    filled: bool
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    risk_distance: float | None
    entry_time: int | None
    exit_time: int | None
    duration_seconds: int | None
    # Full-window MFE/MAE (direction-normalized price distances) from 6B metrics
    mfe: float | None = None
    mae: float | None = None
    # Timeout MTM at canonical horizon (R and price distance); only for TIMEOUT
    mtm_r_900: float | None = None
    mtm_price_distance_900: float | None = None
    # Exploratory 1800s cross-mark (optimistic bound only)
    mtm_r_1800: float | None = None
    mtm_price_distance_1800: float | None = None
    # Early excursion at fixed timepoints: maps seconds -> (mfe_r, mae_r)
    # Only populated for filled trades when tick data / precomputed values available.
    early_excursions: Mapping[int, tuple[float, float]] = field(default_factory=dict)
    exclusion_reason: str | None = None

    @property
    def cohort_key(self) -> CohortKey:
        return CohortKey(self.instrument, int(self.signal_epoch), str(self.direction))


@dataclass(frozen=True, slots=True)
class CostModel:
    """Explicit transaction-cost model (spread cost in R)."""

    canonical_spread_cost_r: float
    sensitivities: tuple[float, ...] = SPREAD_SENSITIVITIES

    def cost_r(self, sensitivity: float) -> float:
        """Spread cost in R under a given sensitivity multiplier on the canonical cost."""
        return self.canonical_spread_cost_r * (1.0 + sensitivity)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_spread_cost_r": self.canonical_spread_cost_r,
            "sensitivities": list(self.sensitivities),
            "sensitivity_labels": {
                str(s): f"{s:+.0%} of canonical" if s != 0.0 else "canonical"
                for s in self.sensitivities
            },
            "notes": [
                "Spread cost is expressed in R units and subtracted from expectancy.",
                "−50% / canonical / +50% sensitivity scenarios are reported.",
                "Cost model is explicit in the artifact; not applied to production.",
            ],
        }


@dataclass(frozen=True, slots=True)
class MetricSummary:
    """Summary statistics for one metric over a cell."""

    median: float | None
    mean: float | None
    p25: float | None
    p75: float | None
    n: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReachFraction:
    threshold_r: float
    side: str  # "favorable" (+R) or "adverse" (−R)
    n_eligible: int
    n_reached: int
    fraction: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExcursionCell:
    """instrument × direction × timepoint early-excursion cell."""

    instrument: str
    direction: str
    timepoint_seconds: int
    n: int
    mfe_r: MetricSummary
    mae_r: MetricSummary
    excursion_diff_r: MetricSummary
    reach_fractions: tuple[ReachFraction, ...]
    is_primary_endpoint: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "direction": self.direction,
            "timepoint_seconds": self.timepoint_seconds,
            "n": self.n,
            "mfe_r": self.mfe_r.to_dict(),
            "mae_r": self.mae_r.to_dict(),
            "excursion_diff_r": self.excursion_diff_r.to_dict(),
            "reach_fractions": [r.to_dict() for r in self.reach_fractions],
            "is_primary_endpoint": self.is_primary_endpoint,
        }


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Deterministic bootstrap CI for median excursion_diff_r at a timepoint."""

    instrument: str
    timepoint_seconds: int
    n_total: int
    n_long: int
    n_short: int
    observed_median: float | None
    ci_low: float | None
    ci_high: float | None
    excludes_zero: bool
    positive: bool
    n_resamples: int
    seed: int
    stratified_by: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "timepoint_seconds": self.timepoint_seconds,
            "n_total": self.n_total,
            "n_long": self.n_long,
            "n_short": self.n_short,
            "observed_median": self.observed_median,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "excludes_zero": self.excludes_zero,
            "positive": self.positive,
            "n_resamples": self.n_resamples,
            "seed": self.seed,
            "stratified_by": self.stratified_by,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class DirectionBootstrapResult:
    """Per-direction bootstrap at the primary endpoint (for CONDITIONAL_EDGE)."""

    instrument: str
    direction: str
    n: int
    observed_median: float | None
    ci_low: float | None
    ci_high: float | None
    excludes_zero: bool
    positive: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MDEResult:
    """Minimum detectable effect / power analysis for the primary endpoint."""

    instrument: str
    sample_size: int
    assumed_effect: float
    estimated_mde: float | None
    can_detect_assumed: bool | None
    methodology: str
    assumptions: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "sample_size": self.sample_size,
            "assumed_effect": self.assumed_effect,
            "estimated_mde": self.estimated_mde,
            "can_detect_assumed": self.can_detect_assumed,
            "methodology": self.methodology,
            "assumptions": list(self.assumptions),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class TimeoutAccounting:
    """Lifecycle MTM and expectancy under timeout / cost scenarios."""

    instrument: str
    n_timeout: int
    n_filled: int
    n_no_fill: int
    # MTM distributions for TIMEOUT trades
    mtm_r_900_summary: MetricSummary | None
    mtm_r_1800_summary: MetricSummary | None
    # Expectancy scenarios (mean R among filled, or among timeout as specified)
    expectancy_timeout_approx_0r: float | None
    expectancy_canonical_900_mtm: float | None
    expectancy_1800_cross_mark: float | None
    expectancy_by_spread_cost: dict[str, float | None]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "n_timeout": self.n_timeout,
            "n_filled": self.n_filled,
            "n_no_fill": self.n_no_fill,
            "mtm_r_900_summary": (
                self.mtm_r_900_summary.to_dict() if self.mtm_r_900_summary else None
            ),
            "mtm_r_1800_summary": (
                self.mtm_r_1800_summary.to_dict() if self.mtm_r_1800_summary else None
            ),
            "expectancy_timeout_approx_0r": self.expectancy_timeout_approx_0r,
            "expectancy_canonical_900_mtm": self.expectancy_canonical_900_mtm,
            "expectancy_1800_cross_mark": self.expectancy_1800_cross_mark,
            "expectancy_by_spread_cost": dict(self.expectancy_by_spread_cost),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class InstrumentEntryEdgeResult:
    instrument: str
    n_signals: int
    n_filled: int
    n_no_fill: int
    n_timeout: int
    timeout_accounting: TimeoutAccounting
    excursion_cells: tuple[ExcursionCell, ...]
    bootstrap_primary: BootstrapResult
    bootstrap_by_direction: tuple[DirectionBootstrapResult, ...]
    mde: MDEResult
    verdict: str
    verdict_evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "n_signals": self.n_signals,
            "n_filled": self.n_filled,
            "n_no_fill": self.n_no_fill,
            "n_timeout": self.n_timeout,
            "timeout_accounting": self.timeout_accounting.to_dict(),
            "excursion_cells": [c.to_dict() for c in self.excursion_cells],
            "bootstrap_primary": self.bootstrap_primary.to_dict(),
            "bootstrap_by_direction": [b.to_dict() for b in self.bootstrap_by_direction],
            "mde": self.mde.to_dict(),
            "verdict": self.verdict,
            "verdict_evidence": dict(self.verdict_evidence),
        }


@dataclass(frozen=True, slots=True)
class EntryEdgeStudyReport:
    study_version: str
    study_title: str
    configuration: dict[str, Any]
    dataset_audit: dict[str, Any]
    cohort_integrity: dict[str, Any]
    cost_model: dict[str, Any]
    instruments: tuple[InstrumentEntryEdgeResult, ...]
    leakage_review: tuple[str, ...]
    research_conclusions: dict[str, str]
    production_execution_unchanged: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_version": self.study_version,
            "study_title": self.study_title,
            "configuration": dict(self.configuration),
            "dataset_audit": dict(self.dataset_audit),
            "cohort_integrity": dict(self.cohort_integrity),
            "cost_model": dict(self.cost_model),
            "instruments": [i.to_dict() for i in self.instruments],
            "leakage_review": list(self.leakage_review),
            "research_conclusions": dict(self.research_conclusions),
            "production_execution_unchanged": self.production_execution_unchanged,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_finite(value: float | None) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _metric_summary(values: Sequence[float]) -> MetricSummary:
    if not values:
        return MetricSummary(median=None, mean=None, p25=None, p75=None, n=0)
    finite = [float(v) for v in values if _is_finite(v)]
    if not finite:
        return MetricSummary(median=None, mean=None, p25=None, p75=None, n=0)
    dist = distribution(finite)
    return MetricSummary(
        median=dist.median,
        mean=dist.mean,
        p25=percentile(finite, 25.0),
        p75=percentile(finite, 75.0),
        n=len(finite),
    )


def _safe_mean(values: Sequence[float]) -> float | None:
    finite = [float(v) for v in values if _is_finite(v)]
    if not finite:
        return None
    return sum(finite) / len(finite)


def cohort_key_from_row(row: TradeExperimentRow) -> CohortKey:
    return CohortKey(
        instrument=str(row.instrument),
        signal_epoch=int(row.signal_epoch),
        direction=str(row.direction),
    )


def record_from_row(
    row: TradeExperimentRow,
    *,
    early_excursions: Mapping[int, tuple[float, float]] | None = None,
    mtm_r_900: float | None = None,
    mtm_price_distance_900: float | None = None,
    mtm_r_1800: float | None = None,
    mtm_price_distance_1800: float | None = None,
) -> EntryEdgeTradeRecord:
    """Build an EntryEdgeTradeRecord from a TradeExperimentRow."""
    filled = bool(
        row.accepted
        and row.outcome is not None
        and row.outcome != SimulationOutcome.NO_FILL
    )
    if row.outcome is not None:
        outcome = row.outcome.value
    else:
        outcome = "rejected" if not row.accepted else "unknown"
    risk = None
    if row.candidate is not None:
        risk = float(row.candidate.risk_distance)
    elif row.entry_price is not None and row.stop_loss is not None:
        risk = abs(float(row.entry_price) - float(row.stop_loss))

    exclusion = None
    if not row.accepted:
        exclusion = "rejected"
    elif row.outcome == SimulationOutcome.NO_FILL:
        exclusion = "no_fill"

    return EntryEdgeTradeRecord(
        instrument=str(row.instrument),
        direction=str(row.direction),
        signal_epoch=int(row.signal_epoch),
        outcome=outcome,
        filled=filled,
        entry_price=float(row.entry_price) if row.entry_price is not None else None,
        stop_price=float(row.stop_loss) if row.stop_loss is not None else None,
        target_price=float(row.take_profit) if row.take_profit is not None else None,
        risk_distance=risk,
        entry_time=int(row.entry_time) if row.entry_time is not None else None,
        exit_time=int(row.exit_time) if row.exit_time is not None else None,
        duration_seconds=int(row.duration_seconds) if row.duration_seconds is not None else None,
        mfe=float(row.mfe) if row.mfe is not None else None,
        mae=float(row.mae) if row.mae is not None else None,
        mtm_r_900=mtm_r_900,
        mtm_price_distance_900=mtm_price_distance_900,
        mtm_r_1800=mtm_r_1800,
        mtm_price_distance_1800=mtm_price_distance_1800,
        early_excursions=dict(early_excursions) if early_excursions else {},
        exclusion_reason=exclusion,
    )


# ---------------------------------------------------------------------------
# Cohort integrity
# ---------------------------------------------------------------------------


class CohortIntegrityError(ValueError):
    """Raised when the 6C cohort does not exactly match the frozen 6B cohort."""


def verify_cohort_integrity(
    baseline_keys: Sequence[CohortKey],
    study_keys: Sequence[CohortKey],
    *,
    baseline_directions: Mapping[tuple[str, int, str], str] | None = None,
    study_directions: Mapping[tuple[str, int, str], str] | None = None,
) -> dict[str, Any]:
    """Assert 6B cohort == 6C cohort.

    Fail loudly on missing records, additional signals, duplicate keys, or
    changed directions. Returns an audit dict on success.
    """
    base_set = {k.as_tuple() for k in baseline_keys}
    study_set = {k.as_tuple() for k in study_keys}

    if len(baseline_keys) != len(base_set):
        raise CohortIntegrityError(
            f"duplicate keys in baseline cohort: {len(baseline_keys)} keys, "
            f"{len(base_set)} unique"
        )
    if len(study_keys) != len(study_set):
        raise CohortIntegrityError(
            f"duplicate keys in study cohort: {len(study_keys)} keys, "
            f"{len(study_set)} unique"
        )

    missing = sorted(base_set - study_set)
    extra = sorted(study_set - base_set)
    if missing:
        raise CohortIntegrityError(
            f"missing cohort records in 6C relative to 6B: {missing[:10]}"
            + (f" ... ({len(missing)} total)" if len(missing) > 10 else "")
        )
    if extra:
        raise CohortIntegrityError(
            f"additional signals in 6C not present in 6B: {extra[:10]}"
            + (f" ... ({len(extra)} total)" if len(extra) > 10 else "")
        )

    direction_mismatches: list[tuple[Any, str, str]] = []
    if baseline_directions is not None and study_directions is not None:
        for key in base_set:
            bd = baseline_directions.get(key)
            sd = study_directions.get(key)
            if bd is not None and sd is not None and bd != sd:
                direction_mismatches.append((key, bd, sd))
        if direction_mismatches:
            raise CohortIntegrityError(
                f"changed directions for cohort keys: {direction_mismatches[:5]}"
            )

    return {
        "status": "ok",
        "n_baseline": len(base_set),
        "n_study": len(study_set),
        "n_shared": len(base_set),
        "n_missing": 0,
        "n_extra": 0,
        "n_direction_mismatches": 0,
        "pairing_key": ["instrument", "signal_epoch", "direction"],
        "notes": [
            "6C reuses the exact frozen 6B signal cohort; no new signals entered.",
            "Cohort identity is (instrument, signal_epoch, direction).",
        ],
    }


def extract_cohort_keys(records: Sequence[EntryEdgeTradeRecord]) -> list[CohortKey]:
    return [r.cohort_key for r in records]


# ---------------------------------------------------------------------------
# Timeout MTM and cost scenarios
# ---------------------------------------------------------------------------


def compute_mtm_r(
    *,
    direction: str,
    entry_price: float,
    mark_price: float,
    risk_distance: float,
) -> tuple[float, float]:
    """Direction-normalized mark-to-market R and price distance.

    Returns (mtm_r, mtm_price_distance) where price_distance is signed
    favorable positive.
    """
    if risk_distance <= 0.0 or not math.isfinite(risk_distance):
        raise ValueError("risk_distance must be finite and > 0")
    if direction.lower() in ("long", "buy"):
        dist = mark_price - entry_price
    else:
        dist = entry_price - mark_price
    return dist / risk_distance, dist


def analyze_timeout_accounting(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    instrument: str,
    cost_model: CostModel,
) -> TimeoutAccounting:
    """Lifecycle expectancy under timeout ≈ 0R, 900s MTM, 1800s cross-mark, costs."""
    filled = [r for r in records if r.filled]
    timeouts = [r for r in filled if r.outcome == SimulationOutcome.TIMEOUT.value]
    no_fills = [r for r in records if r.exclusion_reason == "no_fill"]

    mtm900 = [r.mtm_r_900 for r in timeouts if _is_finite(r.mtm_r_900)]
    mtm1800 = [r.mtm_r_1800 for r in timeouts if _is_finite(r.mtm_r_1800)]

    # Expectancy scenarios among filled trades:
    # 1) timeout ≈ 0R: assign 0R to TIMEOUT, keep realized for TP/SL if available via mtm proxy
    # For filled non-timeout we use mtm_r_900 if present else 0 for TP/SL approximation
    # when explicit realized_r is not on the record; prefer mtm_r_900 for TIMEOUT.
    def _r_for_timeout_zero(r: EntryEdgeTradeRecord) -> float | None:
        if r.outcome == SimulationOutcome.TIMEOUT.value:
            return 0.0
        if _is_finite(r.mtm_r_900):
            return float(r.mtm_r_900)
        # TP/SL without MTM: use sign from outcome
        if r.outcome == SimulationOutcome.TP.value:
            return 1.0  # approximate +1R if target distance == risk (not always true)
        if r.outcome == SimulationOutcome.SL.value:
            return -1.0
        return None

    exp_timeout_0 = _safe_mean(
        [v for r in filled if (v := _r_for_timeout_zero(r)) is not None]
    )

    exp_900 = _safe_mean(
        [float(r.mtm_r_900) for r in filled if _is_finite(r.mtm_r_900)]
    )
    # For filled trades without mtm_r_900, fall back to timeout-0 style
    if exp_900 is None and filled:
        exp_900 = exp_timeout_0

    exp_1800_vals: list[float] = []
    for r in filled:
        if r.outcome == SimulationOutcome.TIMEOUT.value and _is_finite(r.mtm_r_1800):
            exp_1800_vals.append(float(r.mtm_r_1800))
        elif _is_finite(r.mtm_r_900):
            exp_1800_vals.append(float(r.mtm_r_900))
        elif r.outcome == SimulationOutcome.TIMEOUT.value:
            exp_1800_vals.append(0.0)
    exp_1800 = _safe_mean(exp_1800_vals)

    # Spread-cost sensitivity applied to canonical 900s MTM expectancy
    cost_expectancy: dict[str, float | None] = {}
    base = exp_900
    for sens in cost_model.sensitivities:
        label = f"spread_cost_sens_{sens:+.2f}"
        if base is None:
            cost_expectancy[label] = None
        else:
            cost_expectancy[label] = base - cost_model.cost_r(sens)

    notes = (
        "NO_FILL excluded from post-entry inference.",
        "timeout ≈ 0R assigns 0R to TIMEOUT outcomes for lifecycle expectancy.",
        "Canonical 900s MTM uses last eligible tick at frozen baseline horizon.",
        "1800s cross-mark is an exploratory/optimistic bound only; never a production exit rule.",
        "Spread-cost scenarios subtract cost_model.cost_r(sensitivity) from 900s expectancy.",
    )
    return TimeoutAccounting(
        instrument=instrument,
        n_timeout=len(timeouts),
        n_filled=len(filled),
        n_no_fill=len(no_fills),
        mtm_r_900_summary=_metric_summary([float(v) for v in mtm900]),
        mtm_r_1800_summary=_metric_summary([float(v) for v in mtm1800]),
        expectancy_timeout_approx_0r=exp_timeout_0,
        expectancy_canonical_900_mtm=exp_900,
        expectancy_1800_cross_mark=exp_1800,
        expectancy_by_spread_cost=cost_expectancy,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Early excursion study
# ---------------------------------------------------------------------------


def _reach_fractions(
    mfe_r_vals: Sequence[float],
    mae_r_vals: Sequence[float],
    thresholds: Sequence[float] = REACH_THRESHOLDS,
) -> tuple[ReachFraction, ...]:
    n = len(mfe_r_vals)
    results: list[ReachFraction] = []
    for thr in thresholds:
        n_fav = sum(1 for v in mfe_r_vals if v >= thr)
        n_adv = sum(1 for v in mae_r_vals if v >= thr)
        results.append(
            ReachFraction(
                threshold_r=thr,
                side="favorable",
                n_eligible=n,
                n_reached=n_fav,
                fraction=(n_fav / n) if n else None,
            )
        )
        results.append(
            ReachFraction(
                threshold_r=thr,
                side="adverse",
                n_eligible=n,
                n_reached=n_adv,
                fraction=(n_adv / n) if n else None,
            )
        )
    return tuple(results)


def analyze_early_excursions(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    instrument: str,
    timepoints: Sequence[int] = EXCURSION_TIMEPOINTS,
) -> tuple[ExcursionCell, ...]:
    """Compute MFE/MAE/excursion_diff at each timepoint × direction for one instrument.

    Only filled trades with early_excursions populated for the timepoint contribute.
    Explicit sample count for every cell; no silent drops of cells.
    """
    filled = [r for r in records if r.filled and r.instrument == instrument]
    directions = sorted({r.direction for r in filled}) or ["long", "short"]
    cells: list[ExcursionCell] = []

    for direction in directions:
        dir_recs = [r for r in filled if r.direction == direction]
        for tp in timepoints:
            mfe_rs: list[float] = []
            mae_rs: list[float] = []
            diffs: list[float] = []
            for r in dir_recs:
                pair = r.early_excursions.get(tp)
                if pair is None:
                    continue
                mfe_r, mae_r = float(pair[0]), float(pair[1])
                if not (_is_finite(mfe_r) and _is_finite(mae_r)):
                    continue
                mfe_rs.append(mfe_r)
                mae_rs.append(mae_r)
                diffs.append(mfe_r - mae_r)

            n = len(diffs)
            cells.append(
                ExcursionCell(
                    instrument=instrument,
                    direction=direction,
                    timepoint_seconds=tp,
                    n=n,
                    mfe_r=_metric_summary(mfe_rs),
                    mae_r=_metric_summary(mae_rs),
                    excursion_diff_r=_metric_summary(diffs),
                    reach_fractions=_reach_fractions(mfe_rs, mae_rs),
                    is_primary_endpoint=(tp == PRIMARY_ENDPOINT_TIMEPOINT),
                )
            )
    return tuple(cells)


# ---------------------------------------------------------------------------
# Bootstrap (deterministic, stratified by direction)
# ---------------------------------------------------------------------------


def _stratified_bootstrap_median(
    long_vals: Sequence[float],
    short_vals: Sequence[float],
    *,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None, float | None]:
    """Return (observed_median, ci_low, ci_high) for combined sample.

    Stratified: each resample draws with replacement within long and within
    short, preserving stratum sizes, then pools and takes median.
    """
    long_list = [float(v) for v in long_vals if _is_finite(v)]
    short_list = [float(v) for v in short_vals if _is_finite(v)]
    combined = long_list + short_list
    if not combined:
        return None, None, None

    observed = percentile(combined, 50.0)
    rng = random.Random(seed)
    n_long = len(long_list)
    n_short = len(short_list)
    medians: list[float] = []

    for _ in range(n_resamples):
        sample: list[float] = []
        if n_long:
            sample.extend(rng.choices(long_list, k=n_long))
        if n_short:
            sample.extend(rng.choices(short_list, k=n_short))
        if not sample:
            continue
        m = percentile(sample, 50.0)
        if m is not None:
            medians.append(m)

    if not medians:
        return observed, None, None
    medians.sort()
    # Percentile of bootstrap distribution for 95% CI (2.5 / 97.5)
    lo = percentile(medians, 2.5)
    hi = percentile(medians, 97.5)
    return observed, lo, hi


def _single_stratum_bootstrap_median(
    values: Sequence[float],
    *,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None, float | None]:
    vals = [float(v) for v in values if _is_finite(v)]
    if not vals:
        return None, None, None
    observed = percentile(vals, 50.0)
    rng = random.Random(seed)
    medians: list[float] = []
    n = len(vals)
    for _ in range(n_resamples):
        sample = rng.choices(vals, k=n)
        m = percentile(sample, 50.0)
        if m is not None:
            medians.append(m)
    if not medians:
        return observed, None, None
    medians.sort()
    return observed, percentile(medians, 2.5), percentile(medians, 97.5)


def bootstrap_primary_endpoint(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    instrument: str,
    timepoint: int = PRIMARY_ENDPOINT_TIMEPOINT,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> BootstrapResult:
    """95% stratified bootstrap CI for median excursion_diff_r at the primary timepoint."""
    filled = [
        r
        for r in records
        if r.filled and r.instrument == instrument and timepoint in r.early_excursions
    ]
    long_diffs: list[float] = []
    short_diffs: list[float] = []
    for r in filled:
        mfe_r, mae_r = r.early_excursions[timepoint]
        diff = float(mfe_r) - float(mae_r)
        if r.direction.lower() in ("long", "buy"):
            long_diffs.append(diff)
        else:
            short_diffs.append(diff)

    observed, lo, hi = _stratified_bootstrap_median(
        long_diffs, short_diffs, n_resamples=n_resamples, seed=seed
    )
    excludes = (
        lo is not None and hi is not None and not (lo <= 0.0 <= hi)
    )
    positive = (
        excludes
        and observed is not None
        and observed > 0.0
        and lo is not None
        and lo > 0.0
    )

    return BootstrapResult(
        instrument=instrument,
        timepoint_seconds=timepoint,
        n_total=len(long_diffs) + len(short_diffs),
        n_long=len(long_diffs),
        n_short=len(short_diffs),
        observed_median=observed,
        ci_low=lo,
        ci_high=hi,
        excludes_zero=bool(excludes),
        positive=bool(positive),
        n_resamples=n_resamples,
        seed=seed,
        stratified_by="direction",
        notes=(
            f"Primary confirmatory endpoint: median excursion_diff_r at {timepoint}s.",
            "Bootstrap is stratified by direction (resample within long and short).",
            f"Deterministic seed={seed}, n_resamples={n_resamples}.",
            "30s/60s/90s/120s/300s timepoints are exploratory only.",
        ),
    )


def bootstrap_by_direction(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    instrument: str,
    timepoint: int = PRIMARY_ENDPOINT_TIMEPOINT,
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[DirectionBootstrapResult, ...]:
    filled = [
        r
        for r in records
        if r.filled and r.instrument == instrument and timepoint in r.early_excursions
    ]
    results: list[DirectionBootstrapResult] = []
    for direction in sorted({r.direction for r in filled}):
        diffs = [
            float(r.early_excursions[timepoint][0]) - float(r.early_excursions[timepoint][1])
            for r in filled
            if r.direction == direction
        ]
        # Offset seed per direction for independence while remaining deterministic
        dir_seed = seed + (1 if direction.lower() in ("long", "buy") else 2)
        observed, lo, hi = _single_stratum_bootstrap_median(
            diffs, n_resamples=n_resamples, seed=dir_seed
        )
        excludes = lo is not None and hi is not None and not (lo <= 0.0 <= hi)
        positive = (
            excludes
            and observed is not None
            and observed > 0.0
            and lo is not None
            and lo > 0.0
        )
        results.append(
            DirectionBootstrapResult(
                instrument=instrument,
                direction=direction,
                n=len(diffs),
                observed_median=observed,
                ci_low=lo,
                ci_high=hi,
                excludes_zero=bool(excludes),
                positive=bool(positive),
            )
        )
    return tuple(results)


# ---------------------------------------------------------------------------
# MDE / power analysis
# ---------------------------------------------------------------------------


def estimate_mde(
    *,
    instrument: str,
    sample_size: int,
    assumed_effect: float = PLAUSIBLE_EFFECT_MEDIAN_DIFF_R,
    observed_diffs: Sequence[float] | None = None,
) -> MDEResult:
    """Registered MDE analysis for median excursion_diff_r.

    Uses a bootstrap-scale heuristic: approximate SE of the median via
    interquartile range / sqrt(n), then MDE ≈ 1.96 * SE * sqrt(2) style bound
    for a two-sided test of median ≠ 0. When observed_diffs are provided,
    the IQR of the sample is used; otherwise a conservative unit-scale IQR
    of 1.0R is assumed.
    """
    assumptions = (
        "Two-sided test of median excursion_diff_r = 0 at the primary endpoint.",
        f"Pre-registered plausible true effect: +{assumed_effect}R median difference.",
        "SE of median approximated as (IQR / 1.349) / sqrt(n) (normal-order-stat approx).",
        "MDE ≈ 1.96 * SE (detectable effect at ~95% confidence under large-sample approx).",
        "No post-hoc power threshold; MDE is reported explicitly against the assumed effect.",
    )
    methodology = (
        "Large-sample normal approximation to the sampling distribution of the sample "
        "median using IQR-based scale estimate; 10_000-resample bootstrap CI is the "
        "inferential procedure for the primary endpoint; MDE is a planning diagnostic."
    )

    if sample_size <= 0:
        return MDEResult(
            instrument=instrument,
            sample_size=0,
            assumed_effect=assumed_effect,
            estimated_mde=None,
            can_detect_assumed=None,
            methodology=methodology,
            assumptions=assumptions,
            notes=("No filled trades with primary-endpoint excursions; MDE undefined.",),
        )

    if observed_diffs:
        finite = [float(v) for v in observed_diffs if _is_finite(v)]
        if len(finite) >= 2:
            q25 = percentile(finite, 25.0)
            q75 = percentile(finite, 75.0)
            iqr = (q75 - q25) if (q25 is not None and q75 is not None) else 1.0
        else:
            iqr = 1.0
    else:
        iqr = 1.0

    # Guard against zero/near-zero IQR
    iqr = max(abs(iqr), 1e-6)
    sigma_hat = iqr / 1.349  # approx SD
    se_median = sigma_hat / math.sqrt(sample_size)
    mde = 1.96 * se_median
    can_detect = mde <= assumed_effect

    notes = (
        f"sample_size={sample_size}, IQR≈{iqr:.4f}, SE_median≈{se_median:.4f}, MDE≈{mde:.4f}",
        f"Assumed effect {assumed_effect}R "
        f"{'≥' if can_detect else '<'} MDE → "
        + (
            "study can detect assumed effect under approx"
            if can_detect
            else "study may be underpowered for assumed effect"
        )
        + ".",
    )
    return MDEResult(
        instrument=instrument,
        sample_size=sample_size,
        assumed_effect=assumed_effect,
        estimated_mde=mde,
        can_detect_assumed=can_detect,
        methodology=methodology,
        assumptions=assumptions,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Verdict gates
# ---------------------------------------------------------------------------


def determine_verdict(
    bootstrap: BootstrapResult,
    mde: MDEResult,
    direction_bootstraps: Sequence[DirectionBootstrapResult],
) -> tuple[str, dict[str, Any]]:
    """Apply the pre-registered verdict gates exactly.

    EDGE_DETECTED: 180s CI excludes zero and is positive.
    UNDERPOWERED: 180s CI includes zero and MDE > plausible effect.
    NO_EDGE: 180s CI includes zero and MDE ≤ plausible effect.
    CONDITIONAL_EDGE: 180s CI excludes zero for one direction but not the other.
    """
    evidence: dict[str, Any] = {
        "primary_ci_low": bootstrap.ci_low,
        "primary_ci_high": bootstrap.ci_high,
        "primary_excludes_zero": bootstrap.excludes_zero,
        "primary_positive": bootstrap.positive,
        "primary_observed_median": bootstrap.observed_median,
        "mde": mde.estimated_mde,
        "assumed_effect": mde.assumed_effect,
        "can_detect_assumed": mde.can_detect_assumed,
        "direction_results": [d.to_dict() for d in direction_bootstraps],
    }

    # CONDITIONAL_EDGE: check per-direction first when primary does not fully qualify
    dir_excl = [d for d in direction_bootstraps if d.excludes_zero and d.positive]
    dir_not = [d for d in direction_bootstraps if not d.excludes_zero]
    if len(direction_bootstraps) >= 2 and dir_excl and dir_not:
        # One direction shows positive CI exclusion, the other does not
        if not (bootstrap.excludes_zero and bootstrap.positive):
            evidence["gate"] = "conditional: one direction excludes zero positively"
            return VERDICT_CONDITIONAL_EDGE, evidence

    if bootstrap.excludes_zero and bootstrap.positive:
        evidence["gate"] = "primary CI excludes zero and is positive"
        return VERDICT_EDGE_DETECTED, evidence

    # CI includes zero (or not positive)
    mde_val = mde.estimated_mde
    assumed = mde.assumed_effect
    if mde_val is not None and mde_val > assumed:
        evidence["gate"] = "CI includes zero (or not positive) and MDE > plausible effect"
        return VERDICT_UNDERPOWERED, evidence

    evidence["gate"] = "CI includes zero (or not positive) and MDE ≤ plausible effect"
    return VERDICT_NO_EDGE, evidence


# ---------------------------------------------------------------------------
# Leakage review
# ---------------------------------------------------------------------------


def leakage_review_notes() -> tuple[str, ...]:
    return (
        "Excursion is post-entry information only.",
        "Excursion is diagnostic only and is never used as a pre-entry feature.",
        "Excursion is never used as a signal gate.",
        "6C reuses the paired 6B cohort; no new signals may enter.",
        "1800s MTM uses information beyond the canonical production horizon.",
        "1800s MTM is therefore exploratory only and must not become a production exit rule.",
        "Strategy logic, signal generation, trade construction, "
        "and simulation semantics are unchanged.",
        "This study does not optimize entry rules, TP/SL, filters, or introduce ML.",
    )


# ---------------------------------------------------------------------------
# Instrument-level analysis
# ---------------------------------------------------------------------------


def analyze_instrument_entry_edge(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    instrument: str,
    cost_model: CostModel | None = None,
    timepoints: Sequence[int] = EXCURSION_TIMEPOINTS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    plausible_effect: float = PLAUSIBLE_EFFECT_MEDIAN_DIFF_R,
) -> InstrumentEntryEdgeResult:
    cost_model = cost_model or CostModel(canonical_spread_cost_r=DEFAULT_CANONICAL_SPREAD_COST_R)
    inst_recs = [r for r in records if r.instrument == instrument]
    filled = [r for r in inst_recs if r.filled]
    no_fills = [r for r in inst_recs if r.exclusion_reason == "no_fill"]
    timeouts = [r for r in filled if r.outcome == SimulationOutcome.TIMEOUT.value]

    timeout_acct = analyze_timeout_accounting(
        inst_recs, instrument=instrument, cost_model=cost_model
    )
    cells = analyze_early_excursions(
        inst_recs, instrument=instrument, timepoints=timepoints
    )
    boot = bootstrap_primary_endpoint(
        inst_recs,
        instrument=instrument,
        timepoint=PRIMARY_ENDPOINT_TIMEPOINT,
        n_resamples=bootstrap_resamples,
        seed=bootstrap_seed,
    )
    dir_boot = bootstrap_by_direction(
        inst_recs,
        instrument=instrument,
        timepoint=PRIMARY_ENDPOINT_TIMEPOINT,
        n_resamples=bootstrap_resamples,
        seed=bootstrap_seed,
    )

    # Collect primary-endpoint diffs for MDE
    primary_diffs: list[float] = []
    for r in filled:
        pair = r.early_excursions.get(PRIMARY_ENDPOINT_TIMEPOINT)
        if pair is None:
            continue
        primary_diffs.append(float(pair[0]) - float(pair[1]))

    mde = estimate_mde(
        instrument=instrument,
        sample_size=len(primary_diffs),
        assumed_effect=plausible_effect,
        observed_diffs=primary_diffs,
    )
    verdict, evidence = determine_verdict(boot, mde, dir_boot)

    return InstrumentEntryEdgeResult(
        instrument=instrument,
        n_signals=len(inst_recs),
        n_filled=len(filled),
        n_no_fill=len(no_fills),
        n_timeout=len(timeouts),
        timeout_accounting=timeout_acct,
        excursion_cells=cells,
        bootstrap_primary=boot,
        bootstrap_by_direction=dir_boot,
        mde=mde,
        verdict=verdict,
        verdict_evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Full study
# ---------------------------------------------------------------------------


def analyze_entry_edge_study(
    records: Sequence[EntryEdgeTradeRecord],
    *,
    baseline_keys: Sequence[CohortKey] | None = None,
    instruments: Sequence[str] = DEFAULT_INSTRUMENTS,
    cost_model: CostModel | None = None,
    timepoints: Sequence[int] = EXCURSION_TIMEPOINTS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    plausible_effect: float = PLAUSIBLE_EFFECT_MEDIAN_DIFF_R,
    dataset_audit: dict[str, Any] | None = None,
) -> EntryEdgeStudyReport:
    """Run the full entry-edge study on a frozen cohort of records."""
    cost_model = cost_model or CostModel(canonical_spread_cost_r=DEFAULT_CANONICAL_SPREAD_COST_R)
    study_keys = extract_cohort_keys(records)

    if baseline_keys is not None:
        integrity = verify_cohort_integrity(baseline_keys, study_keys)
    else:
        # Self-check: no internal duplicates
        integrity = verify_cohort_integrity(study_keys, study_keys)
        integrity["notes"] = list(integrity.get("notes", [])) + [
            "No external 6B baseline keys provided; self-consistency check only.",
        ]

    inst_results: list[InstrumentEntryEdgeResult] = []
    for inst in instruments:
        inst_results.append(
            analyze_instrument_entry_edge(
                records,
                instrument=inst,
                cost_model=cost_model,
                timepoints=timepoints,
                bootstrap_seed=bootstrap_seed,
                bootstrap_resamples=bootstrap_resamples,
                plausible_effect=plausible_effect,
            )
        )

    conclusions: dict[str, str] = {}
    for ir in inst_results:
        conclusions[ir.instrument] = (
            f"verdict={ir.verdict}; "
            f"primary_median={ir.bootstrap_primary.observed_median}; "
            f"CI=[{ir.bootstrap_primary.ci_low}, {ir.bootstrap_primary.ci_high}]; "
            f"n={ir.bootstrap_primary.n_total}; "
            f"MDE={ir.mde.estimated_mde}"
        )

    configuration = {
        "study_version": STUDY_VERSION,
        "frozen_baseline_horizon_seconds": FROZEN_BASELINE_HORIZON_SECONDS,
        "extended_cross_mark_seconds": EXTENDED_CROSS_MARK_SECONDS,
        "excursion_timepoints_seconds": list(timepoints),
        "primary_endpoint_timepoint_seconds": PRIMARY_ENDPOINT_TIMEPOINT,
        "reach_thresholds_r": list(REACH_THRESHOLDS),
        "plausible_effect_median_diff_r": plausible_effect,
        "bootstrap_resamples": bootstrap_resamples,
        "bootstrap_seed": bootstrap_seed,
        "instruments": list(instruments),
        "cost_model": cost_model.to_dict(),
    }

    return EntryEdgeStudyReport(
        study_version=STUDY_VERSION,
        study_title="Milestone 6C — Entry Edge / Early Excursion Study",
        configuration=configuration,
        dataset_audit=dataset_audit or {},
        cohort_integrity=integrity,
        cost_model=cost_model.to_dict(),
        instruments=tuple(inst_results),
        leakage_review=leakage_review_notes(),
        research_conclusions=conclusions,
        production_execution_unchanged=True,
    )


def run_entry_edge_study_on_results(
    results_by_instrument: Mapping[str, ExperimentResult],
    *,
    baseline_results: Mapping[str, ExperimentResult] | None = None,
    cost_model: CostModel | None = None,
    timepoints: Sequence[int] = EXCURSION_TIMEPOINTS,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    bootstrap_resamples: int = BOOTSTRAP_RESAMPLES,
    early_excursions_by_key: (
        Mapping[tuple[str, int, str], Mapping[int, tuple[float, float]]] | None
    ) = None,
    mtm_by_key: Mapping[tuple[str, int, str], dict[str, float | None]] | None = None,
) -> EntryEdgeStudyReport:
    """Build records from ExperimentResult maps and run the study.

    Optional early_excursions_by_key and mtm_by_key inject precomputed
    post-entry diagnostics (e.g. from a tick pass) without regenerating signals.
    """
    records: list[EntryEdgeTradeRecord] = []
    for _inst, result in results_by_instrument.items():
        for row in result.rows:
            key = (str(row.instrument), int(row.signal_epoch), str(row.direction))
            excursions = None
            if early_excursions_by_key is not None:
                excursions = early_excursions_by_key.get(key)
            mtm = (mtm_by_key or {}).get(key, {})
            records.append(
                record_from_row(
                    row,
                    early_excursions=excursions,
                    mtm_r_900=mtm.get("mtm_r_900"),
                    mtm_price_distance_900=mtm.get("mtm_price_distance_900"),
                    mtm_r_1800=mtm.get("mtm_r_1800"),
                    mtm_price_distance_1800=mtm.get("mtm_price_distance_1800"),
                )
            )

    baseline_keys: list[CohortKey] | None = None
    if baseline_results is not None:
        baseline_keys = []
        for result in baseline_results.values():
            for row in result.rows:
                baseline_keys.append(cohort_key_from_row(row))

    dataset_audit = {
        "instruments": {
            inst: {
                "n_rows": len(result.rows),
                "signals": result.summary.signals,
                "accepted": result.summary.candidates_accepted,
                "outcomes": dict(result.summary.outcomes),
            }
            for inst, result in results_by_instrument.items()
        }
    }

    return analyze_entry_edge_study(
        records,
        baseline_keys=baseline_keys,
        instruments=tuple(results_by_instrument.keys()),
        cost_model=cost_model,
        timepoints=timepoints,
        bootstrap_seed=bootstrap_seed,
        bootstrap_resamples=bootstrap_resamples,
        dataset_audit=dataset_audit,
    )


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def write_entry_edge_study_artifacts(
    report: EntryEdgeStudyReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    """Write entry_edge_study.json and entry_edge_study_report.md."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "entry_edge_study.json"
    md_path = out / "entry_edge_study_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_entry_edge_study_report(report), encoding="utf-8")
    return json_path, md_path


def _fmt(x: float | None, digits: int = 4) -> str:
    if x is None:
        return "n/a"
    if not math.isfinite(x):
        return "n/a"
    return f"{x:.{digits}f}"


def format_entry_edge_study_report(report: EntryEdgeStudyReport) -> str:
    """Markdown report following the established 6B.2 section structure."""
    lines: list[str] = []
    lines.append(f"# {report.study_title}")
    lines.append("")
    lines.append(f"**Study version:** {report.study_version}")
    lines.append("")

    # 1. Scope
    lines.append("## 1. Scope")
    lines.append("")
    lines.append(
        "Observational diagnostic of the frozen 6B entry cohort: whether filled "
        "trades demonstrate directional edge during the first 1–5 minutes after fill."
    )
    lines.append("")
    lines.append(
        "This study does **not** change entry rules, signal filters, TP/SL, indicators, "
        "or the production simulation horizon. Excursion is post-entry diagnostic only."
    )
    lines.append("")

    # 2. Configuration
    lines.append("## 2. Configuration")
    lines.append("")
    cfg = report.configuration
    lines.append(f"- Frozen baseline horizon: {cfg.get('frozen_baseline_horizon_seconds')}s")
    lines.append(f"- Extended cross-mark (exploratory): {cfg.get('extended_cross_mark_seconds')}s")
    lines.append(f"- Excursion timepoints (s): {cfg.get('excursion_timepoints_seconds')}")
    lines.append(
        f"- **Primary confirmatory endpoint:** median excursion_diff_r at "
        f"{cfg.get('primary_endpoint_timepoint_seconds')}s"
    )
    lines.append(
        f"- Bootstrap: {cfg.get('bootstrap_resamples')} resamples, "
        f"seed={cfg.get('bootstrap_seed')}"
    )
    lines.append(f"- Plausible effect (MDE): +{cfg.get('plausible_effect_median_diff_r')}R")
    lines.append(f"- Instruments: {cfg.get('instruments')}")
    lines.append("")

    # 3. Dataset audit
    lines.append("## 3. Dataset audit")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.dataset_audit, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("### Cohort integrity")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.cohort_integrity, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")

    # 4. Per-instrument results
    lines.append("## 4. Per-instrument results")
    lines.append("")
    for ir in report.instruments:
        lines.append(f"### {ir.instrument}")
        lines.append("")
        lines.append(f"- Signals: {ir.n_signals}")
        lines.append(f"- Filled: {ir.n_filled}")
        lines.append(f"- NO_FILL: {ir.n_no_fill}")
        lines.append(f"- TIMEOUT: {ir.n_timeout}")
        lines.append(f"- **Verdict: {ir.verdict}**")
        lines.append("")

    # 5. Timeout/cost scenarios
    lines.append("## 5. Timeout/cost scenarios")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.cost_model, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    for ir in report.instruments:
        ta = ir.timeout_accounting
        lines.append(f"### {ir.instrument}")
        lines.append("")
        lines.append(
            f"- n_timeout={ta.n_timeout}, n_filled={ta.n_filled}, "
            f"n_no_fill={ta.n_no_fill}"
        )
        lines.append(f"- Expectancy timeout≈0R: {_fmt(ta.expectancy_timeout_approx_0r)}")
        lines.append(f"- Expectancy canonical 900s MTM: {_fmt(ta.expectancy_canonical_900_mtm)}")
        lines.append(
            f"- Expectancy 1800s cross-mark (exploratory): "
            f"{_fmt(ta.expectancy_1800_cross_mark)}"
        )
        lines.append(f"- Spread-cost scenarios: {ta.expectancy_by_spread_cost}")
        lines.append("")

    # 6. Early-excursion results
    lines.append("## 6. Early-excursion results")
    lines.append("")
    lines.append(
        "MFE/MAE are direction-normalized (favorable/adverse price distance ÷ risk_distance). "
        "`excursion_diff_r = mfe_r − mae_r`. Every cell reports explicit `n`."
    )
    lines.append("")
    for ir in report.instruments:
        lines.append(f"### {ir.instrument}")
        lines.append("")
        lines.append(
            "| direction | t (s) | n | median diff_r | mean diff_r | p25 | p75 | primary |"
        )
        lines.append(
            "|-----------|------:|--:|--------------:|------------:|----:|----:|:-------:|"
        )
        for cell in ir.excursion_cells:
            d = cell.excursion_diff_r
            lines.append(
                f"| {cell.direction} | {cell.timepoint_seconds} | {cell.n} | "
                f"{_fmt(d.median)} | {_fmt(d.mean)} | {_fmt(d.p25)} | {_fmt(d.p75)} | "
                f"{'yes' if cell.is_primary_endpoint else ''} |"
            )
        lines.append("")

    # 7. Statistical analysis
    lines.append("## 7. Statistical analysis")
    lines.append("")
    lines.append(
        f"Primary endpoint: **median excursion_diff_r at {PRIMARY_ENDPOINT_TIMEPOINT}s**, "
        "analyzed separately per instrument. 95% bootstrap CI, 10,000 resamples, "
        f"stratified by direction, seed={BOOTSTRAP_SEED}."
    )
    lines.append("")
    for ir in report.instruments:
        b = ir.bootstrap_primary
        lines.append(f"### {ir.instrument}")
        lines.append("")
        lines.append(f"- n_total={b.n_total} (long={b.n_long}, short={b.n_short})")
        lines.append(f"- observed median: {_fmt(b.observed_median)}")
        lines.append(f"- 95% CI: [{_fmt(b.ci_low)}, {_fmt(b.ci_high)}]")
        lines.append(f"- excludes zero: {b.excludes_zero}; positive: {b.positive}")
        lines.append("")
        for d in ir.bootstrap_by_direction:
            lines.append(
                f"  - {d.direction}: n={d.n}, median={_fmt(d.observed_median)}, "
                f"CI=[{_fmt(d.ci_low)}, {_fmt(d.ci_high)}], "
                f"excludes_zero={d.excludes_zero}"
            )
        lines.append("")

    # 8. Power/MDE
    lines.append("## 8. Power/MDE")
    lines.append("")
    for ir in report.instruments:
        m = ir.mde
        lines.append(f"### {ir.instrument}")
        lines.append("")
        lines.append(f"- sample_size: {m.sample_size}")
        lines.append(f"- assumed effect: +{m.assumed_effect}R")
        lines.append(f"- estimated MDE: {_fmt(m.estimated_mde)}")
        lines.append(f"- can detect assumed: {m.can_detect_assumed}")
        lines.append(f"- methodology: {m.methodology}")
        for a in m.assumptions:
            lines.append(f"  - {a}")
        lines.append("")

    # 9. Statistical limitations
    lines.append("## 9. Statistical limitations")
    lines.append("")
    lines.append(
        "- Small-sample medians are noisy; bootstrap CIs widen with low n."
    )
    lines.append(
        "- MDE uses a large-sample IQR approximation; it is a planning diagnostic, "
        "not a substitute for the bootstrap CI."
    )
    lines.append(
        "- Instruments are analyzed separately; no pooled inference."
    )
    lines.append(
        "- 1800s cross-mark uses information beyond the canonical horizon and is exploratory only."
    )
    lines.append(
        "- Exploratory timepoints (30/60/90/120/300s) must not be used "
        "to manufacture a favorable verdict."
    )
    lines.append("")

    # 10. Leakage review
    lines.append("## 10. Leakage review")
    lines.append("")
    for note in report.leakage_review:
        lines.append(f"- {note}")
    lines.append("")

    # 11. Research conclusions
    lines.append("## 11. Research conclusions")
    lines.append("")
    for inst, text in report.research_conclusions.items():
        lines.append(f"- **{inst}:** {text}")
    lines.append("")
    lines.append(
        f"Production execution unchanged: {report.production_execution_unchanged}"
    )
    lines.append("")

    # 12. Verdict
    lines.append("## 12. Verdict")
    lines.append("")
    for ir in report.instruments:
        lines.append(f"### {ir.instrument}: **{ir.verdict}**")
        lines.append("")
        lines.append("Evidence:")
        lines.append("```json")
        lines.append(json.dumps(ir.verdict_evidence, indent=2, sort_keys=True, default=str))
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*Milestone 6C is research-only. Do not optimize strategy parameters from these results.*"
    )
    lines.append("")
    return "\n".join(lines)
