"""Milestone 6B — Horizon-Aware Trade Construction & Exit Study.

Research-only analysis of whether existing trade construction (target/risk
distances), simulation outcomes (TP / SL / TIMEOUT), and the configured
simulation horizon are compatible with observed post-entry behavior.

Does **not** modify strategy, trade construction, simulation, live/demo
execution, or production baselines. Alternative horizon or partial-target
views are labeled exploratory / descriptive only.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.research.stats import DistributionStats, distribution
from smb.simulation.models import SimulationOutcome

logger = logging.getLogger(__name__)

INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_INSTRUMENTS: tuple[str, ...] = (INSTRUMENT_V75, INSTRUMENT_STEP)
DEFAULT_R_THRESHOLDS: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00)
SCENARIO_BASELINE = "baseline"
SCENARIO_EXPLORATORY = "exploratory"
SCENARIO_DESCRIPTIVE = "descriptive"
SCENARIO_NOT_ESTIMABLE = "not_estimable"
STUDY_VERSION = "6b.1"
DEFAULT_BASELINE_HORIZON_SECONDS = 900
MIN_FILLED_FOR_STABLE_RATES = 10
MIN_TIMEOUT_FOR_EXCURSION = 3


@dataclass(frozen=True, slots=True)
class StudyTradeRecord:
    """Validated internal representation of one trade for the exit study."""

    instrument: str
    direction: str
    signal_epoch: int
    outcome: str
    filled: bool
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    risk_distance: float | None
    target_distance: float | None
    mfe: float | None
    mae: float | None
    mfe_r: float | None
    mae_r: float | None
    mfe_target_ratio: float | None
    duration_seconds: int | None
    entry_time: int | None
    exit_time: int | None
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class OutcomeCounts:
    signals: int
    accepted: int
    rejected: int
    no_fill: int
    filled: int
    tp: int
    sl: int
    timeout: int

    @property
    def tp_pct_of_filled(self) -> float | None:
        return _pct(self.tp, self.filled)

    @property
    def sl_pct_of_filled(self) -> float | None:
        return _pct(self.sl, self.filled)

    @property
    def timeout_pct_of_filled(self) -> float | None:
        return _pct(self.timeout, self.filled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": self.signals,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "no_fill": self.no_fill,
            "filled": self.filled,
            "tp": self.tp,
            "sl": self.sl,
            "timeout": self.timeout,
            "tp_pct_of_filled": self.tp_pct_of_filled,
            "sl_pct_of_filled": self.sl_pct_of_filled,
            "timeout_pct_of_filled": self.timeout_pct_of_filled,
        }


@dataclass(frozen=True, slots=True)
class ThresholdReachSummary:
    threshold_r: float
    n_eligible: int
    n_reached: int
    pct_reached: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TimeoutExcursionSummary:
    n_timeout: int
    n_with_mfe_r: int
    mfe_r_distribution: DistributionStats | None
    mae_r_distribution: DistributionStats | None
    reached_thresholds: tuple[ThresholdReachSummary, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_timeout": self.n_timeout,
            "n_with_mfe_r": self.n_with_mfe_r,
            "mfe_r_distribution": (
                self.mfe_r_distribution.to_dict() if self.mfe_r_distribution else None
            ),
            "mae_r_distribution": (
                self.mae_r_distribution.to_dict() if self.mae_r_distribution else None
            ),
            "reached_thresholds": [t.to_dict() for t in self.reached_thresholds],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario_id: str
    label: str
    title: str
    description: str
    limitations: tuple[str, ...]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "title": self.title,
            "description": self.description,
            "limitations": list(self.limitations),
            "metrics": self.metrics,
        }


@dataclass(frozen=True, slots=True)
class InstrumentStudyResult:
    instrument: str
    horizon_seconds: int
    dataset_coverage: dict[str, Any]
    outcomes: OutcomeCounts
    exclusions: dict[str, int]
    mfe_distribution: DistributionStats | None
    mae_distribution: DistributionStats | None
    mfe_r_distribution: DistributionStats | None
    mae_r_distribution: DistributionStats | None
    mfe_target_ratio_distribution: DistributionStats | None
    risk_distance_distribution: DistributionStats | None
    target_distance_distribution: DistributionStats | None
    duration_distribution: DistributionStats | None
    partial_target_reach: tuple[ThresholdReachSummary, ...]
    target_reachability: dict[str, Any]
    timeout_analysis: TimeoutExcursionSummary
    scenarios: tuple[ScenarioResult, ...]
    statistical_limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "horizon_seconds": self.horizon_seconds,
            "dataset_coverage": self.dataset_coverage,
            "outcomes": self.outcomes.to_dict(),
            "exclusions": dict(self.exclusions),
            "mfe_distribution": (
                self.mfe_distribution.to_dict() if self.mfe_distribution else None
            ),
            "mae_distribution": (
                self.mae_distribution.to_dict() if self.mae_distribution else None
            ),
            "mfe_r_distribution": (
                self.mfe_r_distribution.to_dict() if self.mfe_r_distribution else None
            ),
            "mae_r_distribution": (
                self.mae_r_distribution.to_dict() if self.mae_r_distribution else None
            ),
            "mfe_target_ratio_distribution": (
                self.mfe_target_ratio_distribution.to_dict()
                if self.mfe_target_ratio_distribution
                else None
            ),
            "risk_distance_distribution": (
                self.risk_distance_distribution.to_dict()
                if self.risk_distance_distribution
                else None
            ),
            "target_distance_distribution": (
                self.target_distance_distribution.to_dict()
                if self.target_distance_distribution
                else None
            ),
            "duration_distribution": (
                self.duration_distribution.to_dict()
                if self.duration_distribution
                else None
            ),
            "partial_target_reach": [t.to_dict() for t in self.partial_target_reach],
            "target_reachability": dict(self.target_reachability),
            "timeout_analysis": self.timeout_analysis.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "statistical_limitations": list(self.statistical_limitations),
        }


@dataclass(frozen=True, slots=True)
class HorizonExitStudyReport:
    study_version: str
    study_title: str
    configuration: dict[str, Any]
    dataset_audit: dict[str, Any]
    baseline_preserved: bool
    instruments: tuple[InstrumentStudyResult, ...]
    pooled_notes: tuple[str, ...]
    leakage_review: tuple[str, ...]
    research_conclusions: dict[str, str]
    production_execution_unchanged: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_version": self.study_version,
            "study_title": self.study_title,
            "configuration": dict(self.configuration),
            "dataset_audit": dict(self.dataset_audit),
            "baseline_preserved": self.baseline_preserved,
            "instruments": [i.to_dict() for i in self.instruments],
            "pooled_notes": list(self.pooled_notes),
            "leakage_review": list(self.leakage_review),
            "research_conclusions": dict(self.research_conclusions),
            "production_execution_unchanged": self.production_execution_unchanged,
        }


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _is_finite(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    if not _is_finite(num) or not _is_finite(den):
        return None
    assert num is not None and den is not None
    if den <= 0.0:
        return None
    return float(num) / float(den)


def study_record_from_row(row: TradeExperimentRow) -> StudyTradeRecord:
    """Map TradeExperimentRow to a validated study record.

    Exclusion rules: rejected candidates; invalid/non-positive risk or target;
    NO_FILL kept for counts but filled=False. Non-finite MFE/MAE become None
    (never zeroed). Negative excursions treated as invalid.
    """
    outcome_str = row.outcome.value if row.outcome is not None else "unknown"
    filled = bool(row.outcome is not None and row.outcome != SimulationOutcome.NO_FILL)
    risk_distance: float | None = None
    target_distance: float | None = None
    stop_price: float | None = row.stop_loss
    target_price: float | None = row.take_profit
    entry_price: float | None = row.entry_price
    if row.candidate is not None:
        risk_distance = row.candidate.risk_distance
        target_distance = row.candidate.reward_distance
        stop_price = row.candidate.stop_loss
        target_price = row.candidate.take_profit
        if entry_price is None:
            entry_price = row.candidate.entry_price
    exclusion: str | None = None
    if not row.accepted or row.candidate is None:
        exclusion = "rejected"
        filled = False
    elif row.outcome is None:
        exclusion = "missing_outcome"
        filled = False
    elif row.outcome == SimulationOutcome.NO_FILL:
        filled = False
    elif not _is_finite(risk_distance) or (risk_distance is not None and risk_distance <= 0.0):
        exclusion = "invalid_risk"
    elif not _is_finite(target_distance) or (
        target_distance is not None and target_distance <= 0.0
    ):
        exclusion = "invalid_target"
    mfe = row.mfe if _is_finite(row.mfe) else None
    mae = row.mae if _is_finite(row.mae) else None
    if mfe is not None and mfe < 0.0:
        mfe = None
    if mae is not None and mae < 0.0:
        mae = None
    mfe_r = _safe_ratio(mfe, risk_distance) if filled and exclusion is None else None
    mae_r = _safe_ratio(mae, risk_distance) if filled and exclusion is None else None
    mfe_target_ratio = (
        _safe_ratio(mfe, target_distance) if filled and exclusion is None else None
    )
    return StudyTradeRecord(
        instrument=row.instrument,
        direction=str(row.direction),
        signal_epoch=int(row.signal_epoch),
        outcome=outcome_str,
        filled=filled and exclusion is None,
        entry_price=entry_price if _is_finite(entry_price) else None,
        stop_price=stop_price if _is_finite(stop_price) else None,
        target_price=target_price if _is_finite(target_price) else None,
        risk_distance=risk_distance if _is_finite(risk_distance) else None,
        target_distance=target_distance if _is_finite(target_distance) else None,
        mfe=mfe,
        mae=mae,
        mfe_r=mfe_r,
        mae_r=mae_r,
        mfe_target_ratio=mfe_target_ratio,
        duration_seconds=row.duration_seconds,
        entry_time=row.entry_time,
        exit_time=row.exit_time,
        exclusion_reason=exclusion,
    )


def records_from_experiment_result(result: ExperimentResult) -> list[StudyTradeRecord]:
    return [study_record_from_row(r) for r in result.rows]


def count_outcomes(
    records: Sequence[StudyTradeRecord],
    *,
    signals: int | None = None,
    accepted: int | None = None,
    rejected: int | None = None,
) -> OutcomeCounts:
    n_signals = signals if signals is not None else len(records)
    n_accepted = accepted
    n_rejected = rejected
    if n_accepted is None or n_rejected is None:
        n_rej = sum(1 for r in records if r.exclusion_reason == "rejected")
        n_acc = sum(1 for r in records if r.exclusion_reason != "rejected")
        n_accepted = n_accepted if n_accepted is not None else n_acc
        n_rejected = n_rejected if n_rejected is not None else n_rej
    no_fill = sum(1 for r in records if r.outcome == SimulationOutcome.NO_FILL.value)
    filled_records = [r for r in records if r.filled]
    tp = sum(1 for r in filled_records if r.outcome == SimulationOutcome.TP.value)
    sl = sum(1 for r in filled_records if r.outcome == SimulationOutcome.SL.value)
    timeout = sum(
        1 for r in filled_records if r.outcome == SimulationOutcome.TIMEOUT.value
    )
    return OutcomeCounts(
        signals=n_signals,
        accepted=n_accepted,
        rejected=n_rejected,
        no_fill=no_fill,
        filled=len(filled_records),
        tp=tp,
        sl=sl,
        timeout=timeout,
    )


def _dist_or_none(values: list[float]) -> DistributionStats | None:
    if not values:
        return None
    return distribution(values)


def threshold_reach(
    records: Sequence[StudyTradeRecord],
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
    *,
    use_mfe_r: bool = True,
) -> tuple[ThresholdReachSummary, ...]:
    if use_mfe_r:
        eligible = [r for r in records if r.filled and r.mfe_r is not None]
        vals = [r.mfe_r for r in eligible if r.mfe_r is not None]
    else:
        eligible = [r for r in records if r.filled and r.mfe_target_ratio is not None]
        vals = [r.mfe_target_ratio for r in eligible if r.mfe_target_ratio is not None]
    n = len(vals)
    out: list[ThresholdReachSummary] = []
    for thr in thresholds:
        reached = sum(1 for v in vals if v >= thr)
        out.append(
            ThresholdReachSummary(
                threshold_r=float(thr),
                n_eligible=n,
                n_reached=reached,
                pct_reached=_pct(reached, n),
            )
        )
    return tuple(out)


def analyze_timeouts(
    records: Sequence[StudyTradeRecord],
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
) -> TimeoutExcursionSummary:
    timeouts = [
        r for r in records if r.filled and r.outcome == SimulationOutcome.TIMEOUT.value
    ]
    mfe_r_vals = [r.mfe_r for r in timeouts if r.mfe_r is not None]
    mae_r_vals = [r.mae_r for r in timeouts if r.mae_r is not None]
    notes: list[str] = []
    if len(timeouts) < MIN_TIMEOUT_FOR_EXCURSION:
        notes.append(
            f"TIMEOUT sample size n={len(timeouts)} is below "
            f"{MIN_TIMEOUT_FOR_EXCURSION}; excursion categories are descriptive only."
        )
    notes.append(
        "TIMEOUT trades did not hit TP or SL within the configured horizon; "
        "MFE/MAE are measured only within the observation window (fill → exit/horizon)."
    )
    notes.append(
        "MFE and MAE are post-trade path metrics (lifecycle: post-entry). "
        "They must not be used as pre-entry predictive features."
    )
    return TimeoutExcursionSummary(
        n_timeout=len(timeouts),
        n_with_mfe_r=len(mfe_r_vals),
        mfe_r_distribution=_dist_or_none(mfe_r_vals),  # type: ignore[arg-type]
        mae_r_distribution=_dist_or_none(mae_r_vals),  # type: ignore[arg-type]
        reached_thresholds=threshold_reach(timeouts, thresholds, use_mfe_r=True),
        notes=tuple(notes),
    )


def target_reachability_summary(records: Sequence[StudyTradeRecord]) -> dict[str, Any]:
    filled = [r for r in records if r.filled and r.mfe_target_ratio is not None]
    n = len(filled)
    reached = sum(
        1
        for r in filled
        if r.mfe_target_ratio is not None and r.mfe_target_ratio >= 1.0
    )
    ratios = [r.mfe_target_ratio for r in filled if r.mfe_target_ratio is not None]
    return {
        "n_eligible": n,
        "n_mfe_reached_target": reached,
        "pct_mfe_reached_target": _pct(reached, n),
        "mfe_target_ratio_distribution": (
            distribution(ratios).to_dict() if ratios else None  # type: ignore[arg-type]
        ),
        "notes": [
            "Target reachability uses actual candidate reward_distance (target distance), "
            "not a reconstructed RR from config.",
            "MFE >= target_distance does not imply a TP fill (path may reverse before TP).",
            "This is a descriptive scenario, not an alternative exit rule.",
        ],
    }


def build_scenarios(
    records: Sequence[StudyTradeRecord],
    outcomes: OutcomeCounts,
    *,
    horizon_seconds: int,
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
    extended_horizon_seconds: int | None = None,
    extended_records: Sequence[StudyTradeRecord] | None = None,
) -> tuple[ScenarioResult, ...]:
    scenarios: list[ScenarioResult] = []
    scenarios.append(
        ScenarioResult(
            scenario_id="baseline_horizon",
            label=SCENARIO_BASELINE,
            title="Existing baseline simulation horizon",
            description=(
                f"Outcomes under the frozen baseline simulation horizon of "
                f"{horizon_seconds}s. Production simulation semantics unchanged."
            ),
            limitations=(
                "Small filled-trade samples yield high-variance rates.",
                "TIMEOUT mixes path incompleteness with true non-touch within horizon.",
            ),
            metrics={"horizon_seconds": horizon_seconds, "outcomes": outcomes.to_dict()},
        )
    )
    partial = threshold_reach(records, thresholds, use_mfe_r=True)
    scenarios.append(
        ScenarioResult(
            scenario_id="partial_target_mfe_r",
            label=SCENARIO_DESCRIPTIVE,
            title="Partial-target analysis (MFE in R thresholds)",
            description=(
                "Fraction of filled trades whose maximum favorable excursion "
                "reached predefined fractions of risk (0.25R–1.00R). "
                "Thresholds are predefined and not tuned on this dataset."
            ),
            limitations=(
                "MFE is a path maximum within the observation window, not a fill.",
                "Does not change exit logic or declare an optimal partial target.",
            ),
            metrics={"thresholds": [t.to_dict() for t in partial]},
        )
    )
    reach = target_reachability_summary(records)
    scenarios.append(
        ScenarioResult(
            scenario_id="target_reachability",
            label=SCENARIO_DESCRIPTIVE,
            title="Target-distance reachability (MFE vs reward_distance)",
            description=(
                "Whether observed MFE reached the candidate target distance. "
                "Uses actual trade target distance from construction."
            ),
            limitations=tuple(reach.get("notes", [])),
            metrics={k: v for k, v in reach.items() if k != "notes"},
        )
    )
    if extended_horizon_seconds is None or extended_records is None:
        scenarios.append(
            ScenarioResult(
                scenario_id="extended_horizon_comparison",
                label=SCENARIO_NOT_ESTIMABLE,
                title="Extended-horizon comparison",
                description=(
                    "Optional re-simulation at a longer horizon was not requested "
                    "or dataset coverage was insufficient."
                ),
                limitations=(
                    "Extended horizon requires sufficient post-entry tick coverage "
                    "beyond the baseline horizon; outcomes beyond available data "
                    "are not fabricated.",
                    "When not run, this scenario is explicitly not estimable.",
                ),
                metrics={"status": "not_run"},
            )
        )
    else:
        ext_outcomes = count_outcomes(extended_records)
        scenarios.append(
            ScenarioResult(
                scenario_id="extended_horizon_comparison",
                label=SCENARIO_EXPLORATORY,
                title=f"Extended-horizon comparison ({extended_horizon_seconds}s)",
                description=(
                    f"Isolated research re-simulation at {extended_horizon_seconds}s. "
                    "Does not replace the baseline. Not a production configuration."
                ),
                limitations=(
                    "Exploratory only; sample sizes remain small.",
                    "Longer horizon can only convert TIMEOUT→TP/SL when post-entry "
                    "ticks exist in the store; otherwise TIMEOUT persists.",
                ),
                metrics={
                    "baseline_horizon_seconds": horizon_seconds,
                    "extended_horizon_seconds": extended_horizon_seconds,
                    "baseline_outcomes": outcomes.to_dict(),
                    "extended_outcomes": ext_outcomes.to_dict(),
                },
            )
        )
    return tuple(scenarios)


def statistical_limitations_for(
    outcomes: OutcomeCounts, *, instrument: str
) -> tuple[str, ...]:
    notes: list[str] = []
    if outcomes.filled < MIN_FILLED_FOR_STABLE_RATES:
        notes.append(
            f"{instrument}: filled n={outcomes.filled} < {MIN_FILLED_FOR_STABLE_RATES}; "
            "outcome percentages and quantiles are high-variance descriptive only."
        )
    if outcomes.timeout > 0 and outcomes.timeout < MIN_TIMEOUT_FOR_EXCURSION:
        notes.append(
            f"{instrument}: TIMEOUT n={outcomes.timeout} is very small; "
            "TIMEOUT excursion categories lack statistical power."
        )
    if outcomes.filled == 0:
        notes.append(
            f"{instrument}: no filled trades; TP/SL/TIMEOUT and excursion analyses "
            "are not estimable."
        )
    notes.append(
        "Statistical power is limited; do not claim production readiness from this study."
    )
    notes.append(
        "Results are observational under a frozen strategy; no causal claims about "
        "parameter changes."
    )
    return tuple(notes)


def analyze_instrument(
    records: Sequence[StudyTradeRecord],
    *,
    instrument: str,
    horizon_seconds: int = DEFAULT_BASELINE_HORIZON_SECONDS,
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
    signals: int | None = None,
    accepted: int | None = None,
    rejected: int | None = None,
    dataset_coverage: dict[str, Any] | None = None,
    extended_horizon_seconds: int | None = None,
    extended_records: Sequence[StudyTradeRecord] | None = None,
) -> InstrumentStudyResult:
    inst_records = [r for r in records if r.instrument == instrument]
    outcomes = count_outcomes(
        inst_records, signals=signals, accepted=accepted, rejected=rejected
    )
    exclusions: dict[str, int] = {}
    for r in inst_records:
        if r.exclusion_reason:
            exclusions[r.exclusion_reason] = exclusions.get(r.exclusion_reason, 0) + 1
    filled = [r for r in inst_records if r.filled]
    mfe_vals = [r.mfe for r in filled if r.mfe is not None]
    mae_vals = [r.mae for r in filled if r.mae is not None]
    mfe_r_vals = [r.mfe_r for r in filled if r.mfe_r is not None]
    mae_r_vals = [r.mae_r for r in filled if r.mae_r is not None]
    mfe_t_vals = [r.mfe_target_ratio for r in filled if r.mfe_target_ratio is not None]
    risk_vals = [r.risk_distance for r in filled if r.risk_distance is not None]
    tgt_vals = [r.target_distance for r in filled if r.target_distance is not None]
    dur_vals = [
        float(r.duration_seconds)
        for r in filled
        if r.duration_seconds is not None and r.duration_seconds >= 0
    ]
    scenarios = build_scenarios(
        inst_records,
        outcomes,
        horizon_seconds=horizon_seconds,
        thresholds=thresholds,
        extended_horizon_seconds=extended_horizon_seconds,
        extended_records=extended_records,
    )
    return InstrumentStudyResult(
        instrument=instrument,
        horizon_seconds=horizon_seconds,
        dataset_coverage=dict(dataset_coverage or {}),
        outcomes=outcomes,
        exclusions=exclusions,
        mfe_distribution=_dist_or_none(mfe_vals),  # type: ignore[arg-type]
        mae_distribution=_dist_or_none(mae_vals),  # type: ignore[arg-type]
        mfe_r_distribution=_dist_or_none(mfe_r_vals),  # type: ignore[arg-type]
        mae_r_distribution=_dist_or_none(mae_r_vals),  # type: ignore[arg-type]
        mfe_target_ratio_distribution=_dist_or_none(mfe_t_vals),  # type: ignore[arg-type]
        risk_distance_distribution=_dist_or_none(risk_vals),  # type: ignore[arg-type]
        target_distance_distribution=_dist_or_none(tgt_vals),  # type: ignore[arg-type]
        duration_distribution=_dist_or_none(dur_vals),
        partial_target_reach=threshold_reach(inst_records, thresholds, use_mfe_r=True),
        target_reachability=target_reachability_summary(inst_records),
        timeout_analysis=analyze_timeouts(inst_records, thresholds),
        scenarios=scenarios,
        statistical_limitations=statistical_limitations_for(
            outcomes, instrument=instrument
        ),
    )


def leakage_review_notes() -> tuple[str, ...]:
    return (
        "Lifecycle classification: MFE, MAE, final outcome, and future price path "
        "are post-entry / post-trade information.",
        "This study uses them only as descriptive path metrics after a fill; "
        "they are not introduced as pre-entry predictive features or gates.",
        "NO_FILL is an entry-failure state and is reported separately from "
        "post-entry TP/SL/TIMEOUT outcomes.",
        "Partial-target and target-reachability scenarios are observational "
        "summaries of path maxima, not alternative live exit rules.",
        "Extended-horizon re-simulation (when run) remains an isolated research "
        "scenario and does not alter the production simulation path.",
    )


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{100.0 * x:.1f}%"


def research_conclusions_for(
    instruments: Sequence[InstrumentStudyResult],
) -> dict[str, str]:
    conclusions: dict[str, str] = {}
    for inst in instruments:
        o = inst.outcomes
        key = f"outcomes_{inst.instrument}"
        if o.filled == 0:
            conclusions[key] = (
                f"{inst.instrument}: no filled trades under the baseline horizon; "
                "exit/horizon compatibility is not estimable."
            )
        else:
            conclusions[key] = (
                f"{inst.instrument}: filled={o.filled} "
                f"TP={o.tp} ({_fmt_pct(o.tp_pct_of_filled)}) "
                f"SL={o.sl} ({_fmt_pct(o.sl_pct_of_filled)}) "
                f"TIMEOUT={o.timeout} ({_fmt_pct(o.timeout_pct_of_filled)}). "
                "Rates are descriptive under the frozen baseline."
            )
        mfe_r = inst.mfe_r_distribution
        if mfe_r is not None and mfe_r.median is not None:
            conclusions[f"mfe_r_{inst.instrument}"] = (
                f"{inst.instrument}: median MFE/R={mfe_r.median:.3f} "
                f"(n={mfe_r.count}); compare to configured target RR descriptively only."
            )
        else:
            conclusions[f"mfe_r_{inst.instrument}"] = (
                f"{inst.instrument}: MFE/R not estimable (insufficient finite values)."
            )
        reach = inst.target_reachability
        pct = reach.get("pct_mfe_reached_target")
        conclusions[f"target_reach_{inst.instrument}"] = (
            f"{inst.instrument}: MFE reached target distance in "
            f"{_fmt_pct(pct)} of eligible filled trades "
            f"(n={reach.get('n_eligible', 0)}). Descriptive only; not an exit rule."
        )
    conclusions["horizon_adequacy"] = (
        "Horizon adequacy is judged by TIMEOUT share among filled trades and by "
        "whether MFE distributions approach the configured target. High TIMEOUT "
        "with low median MFE/R suggests the horizon or target may be mismatched; "
        "this study does not select a replacement configuration."
    )
    conclusions["scope"] = (
        "No parameter optimization, no winning configuration selection, and no "
        "change to production trading behavior. Alternative scenarios remain "
        "labeled exploratory or descriptive."
    )
    conclusions["power"] = (
        "Sample sizes are small relative to stable rate estimation; "
        "do not claim production readiness."
    )
    return conclusions


def analyze_horizon_exit_study(
    results: Sequence[ExperimentResult],
    *,
    horizon_seconds: int = DEFAULT_BASELINE_HORIZON_SECONDS,
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
    extended_results: Sequence[ExperimentResult] | None = None,
    extended_horizon_seconds: int | None = None,
) -> HorizonExitStudyReport:
    all_records: list[StudyTradeRecord] = []
    per_inst_meta: dict[str, dict[str, Any]] = {}
    for res in results:
        recs = records_from_experiment_result(res)
        all_records.extend(recs)
        inst = res.summary.instrument
        per_inst_meta[inst] = {
            "signals": res.summary.signals,
            "accepted": res.summary.candidates_accepted,
            "rejected": res.summary.candidates_rejected,
            "start_epoch": res.summary.start_epoch,
            "end_epoch": res.summary.end_epoch,
            "ticks_processed": res.summary.ticks_processed,
            "outcomes_from_summary": dict(res.summary.outcomes),
            "config_horizon_seconds": res.config.simulation.max_duration_seconds,
        }
    ext_by_inst: dict[str, list[StudyTradeRecord]] = {}
    if extended_results is not None:
        for res in extended_results:
            ext_by_inst.setdefault(res.summary.instrument, []).extend(
                records_from_experiment_result(res)
            )
    instruments_found = sorted({r.instrument for r in all_records} | set(per_inst_meta))
    inst_results: list[InstrumentStudyResult] = []
    for inst in instruments_found:
        meta = per_inst_meta.get(inst, {})
        ext_recs = ext_by_inst.get(inst)
        inst_results.append(
            analyze_instrument(
                all_records,
                instrument=inst,
                horizon_seconds=horizon_seconds,
                thresholds=thresholds,
                signals=meta.get("signals"),
                accepted=meta.get("accepted"),
                rejected=meta.get("rejected"),
                dataset_coverage={
                    "start_epoch": meta.get("start_epoch"),
                    "end_epoch": meta.get("end_epoch"),
                    "ticks_processed": meta.get("ticks_processed"),
                    "config_horizon_seconds": meta.get(
                        "config_horizon_seconds", horizon_seconds
                    ),
                },
                extended_horizon_seconds=extended_horizon_seconds if ext_recs else None,
                extended_records=ext_recs,
            )
        )
    total_signals = sum(m.get("signals", 0) for m in per_inst_meta.values())
    total_accepted = sum(m.get("accepted", 0) for m in per_inst_meta.values())
    total_rejected = sum(m.get("rejected", 0) for m in per_inst_meta.values())
    filled_all = sum(1 for r in all_records if r.filled)
    no_fill_all = sum(
        1 for r in all_records if r.outcome == SimulationOutcome.NO_FILL.value
    )
    audit = {
        "instruments": instruments_found,
        "total_signals": total_signals,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "total_study_records": len(all_records),
        "total_filled": filled_all,
        "total_no_fill": no_fill_all,
        "baseline_horizon_seconds": horizon_seconds,
        "extended_horizon_seconds": extended_horizon_seconds,
        "extended_run": extended_results is not None,
        "r_thresholds": list(thresholds),
    }
    return HorizonExitStudyReport(
        study_version=STUDY_VERSION,
        study_title="Milestone 6B — Horizon-Aware Trade Construction & Exit Study",
        configuration={
            "baseline_horizon_seconds": horizon_seconds,
            "extended_horizon_seconds": extended_horizon_seconds,
            "r_thresholds": list(thresholds),
            "study_version": STUDY_VERSION,
        },
        dataset_audit=audit,
        baseline_preserved=True,
        instruments=tuple(inst_results),
        pooled_notes=(
            "Instrument results are reported separately; do not pool in a way that "
            "hides instrument-specific behavior.",
            "Pooled counts in dataset_audit are for audit only, not for inference.",
        ),
        leakage_review=leakage_review_notes(),
        research_conclusions=research_conclusions_for(inst_results),
        production_execution_unchanged=True,
    )


def write_horizon_exit_study_artifacts(
    report: HorizonExitStudyReport,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "horizon_exit_study.json"
    md_path = out / "horizon_exit_study_report.md"
    payload = report.to_dict()
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_horizon_exit_study_report(report), encoding="utf-8")
    return json_path, md_path


def _fmt(x: float | None) -> str:
    if x is None:
        return "n/a"
    if abs(x) >= 1000 or (0 < abs(x) < 1e-3):
        return f"{x:.6g}"
    return f"{x:.4f}"


def format_horizon_exit_study_report(report: HorizonExitStudyReport) -> str:
    lines: list[str] = []
    lines.append(f"# {report.study_title}")
    lines.append("")
    lines.append(f"**Study version:** `{report.study_version}`")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(
        "This is a **research study**. It does not optimize parameters, select a "
        "winning configuration, or change production trading behavior. "
        "Alternative exit or horizon views are labeled exploratory or descriptive."
    )
    lines.append("")
    lines.append(f"- Baseline preserved: **{report.baseline_preserved}**")
    lines.append(
        f"- Production execution unchanged: **{report.production_execution_unchanged}**"
    )
    lines.append("")
    lines.append("## Configuration")
    lines.append("")
    for k, v in sorted(report.configuration.items()):
        lines.append(f"- `{k}`: `{v}`")
    lines.append("")
    lines.append("## Dataset audit")
    lines.append("")
    for k, v in sorted(report.dataset_audit.items()):
        lines.append(f"- `{k}`: `{v}`")
    lines.append("")
    for inst in report.instruments:
        lines.append(f"## Instrument: `{inst.instrument}`")
        lines.append("")
        lines.append(f"- Horizon (seconds): **{inst.horizon_seconds}**")
        lines.append(f"- Coverage: `{inst.dataset_coverage}`")
        o = inst.outcomes
        lines.append("")
        lines.append("### Outcomes")
        lines.append("")
        lines.append("| Metric | Count | % of filled |")
        lines.append("|--------|------:|------------:|")
        lines.append(f"| Signals | {o.signals} | — |")
        lines.append(f"| Accepted | {o.accepted} | — |")
        lines.append(f"| Rejected | {o.rejected} | — |")
        lines.append(f"| NO_FILL | {o.no_fill} | — |")
        lines.append(f"| Filled | {o.filled} | — |")
        lines.append(f"| TP | {o.tp} | {_fmt_pct(o.tp_pct_of_filled)} |")
        lines.append(f"| SL | {o.sl} | {_fmt_pct(o.sl_pct_of_filled)} |")
        lines.append(f"| TIMEOUT | {o.timeout} | {_fmt_pct(o.timeout_pct_of_filled)} |")
        lines.append("")
        if inst.exclusions:
            lines.append(f"- Exclusions: `{dict(inst.exclusions)}`")
            lines.append("")
        lines.append("### Excursion & distance summaries (filled trades)")
        lines.append("")
        for label, dist in (
            ("MFE (price)", inst.mfe_distribution),
            ("MAE (price)", inst.mae_distribution),
            ("MFE in R", inst.mfe_r_distribution),
            ("MAE in R", inst.mae_r_distribution),
            ("MFE / target distance", inst.mfe_target_ratio_distribution),
            ("Risk distance", inst.risk_distance_distribution),
            ("Target distance", inst.target_distance_distribution),
            ("Duration (s)", inst.duration_distribution),
        ):
            lines.append(f"**{label}**")
            if dist is None or dist.count == 0:
                lines.append("- *not estimable (empty)*")
            else:
                lines.append(
                    f"- n={dist.count}, min={_fmt(dist.min)}, median={_fmt(dist.median)}, "
                    f"p75={_fmt(dist.p75)}, p90={_fmt(dist.p90)}, max={_fmt(dist.max)}"
                )
            lines.append("")
        lines.append("### Partial-target reach (MFE in R)")
        lines.append("")
        lines.append("| Threshold (R) | Eligible | Reached | % |")
        lines.append("|--------------:|---------:|--------:|--:|")
        for t in inst.partial_target_reach:
            lines.append(
                f"| {t.threshold_r:.2f} | {t.n_eligible} | {t.n_reached} | "
                f"{_fmt_pct(t.pct_reached)} |"
            )
        lines.append("")
        lines.append("### Target reachability")
        lines.append("")
        tr = inst.target_reachability
        lines.append(
            f"- Eligible: {tr.get('n_eligible')}; "
            f"MFE ≥ target: {tr.get('n_mfe_reached_target')} "
            f"({_fmt_pct(tr.get('pct_mfe_reached_target'))})"
        )
        lines.append("")
        lines.append("### TIMEOUT analysis")
        lines.append("")
        ta = inst.timeout_analysis
        lines.append(f"- TIMEOUT n={ta.n_timeout}, with MFE/R={ta.n_with_mfe_r}")
        if ta.mfe_r_distribution and ta.mfe_r_distribution.count:
            d = ta.mfe_r_distribution
            lines.append(
                f"- TIMEOUT MFE/R: n={d.count}, median={_fmt(d.median)}, "
                f"p75={_fmt(d.p75)}, max={_fmt(d.max)}"
            )
        for note in ta.notes:
            lines.append(f"- Note: {note}")
        lines.append("")
        lines.append("### Scenarios")
        lines.append("")
        for s in inst.scenarios:
            lines.append(f"#### `{s.scenario_id}` ({s.label})")
            lines.append("")
            lines.append(f"**{s.title}**")
            lines.append("")
            lines.append(s.description)
            lines.append("")
            if s.limitations:
                lines.append("Limitations:")
                for lim in s.limitations:
                    lines.append(f"- {lim}")
                lines.append("")
        lines.append("### Statistical limitations")
        lines.append("")
        for lim in inst.statistical_limitations:
            lines.append(f"- {lim}")
        lines.append("")
    lines.append("## Leakage review")
    lines.append("")
    for note in report.leakage_review:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Pooled notes")
    lines.append("")
    for note in report.pooled_notes:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## Research conclusions")
    lines.append("")
    for k, v in report.research_conclusions.items():
        lines.append(f"- **{k}:** {v}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*End of report. This artifact is research-only and does not authorize "
        "production configuration changes.*"
    )
    lines.append("")
    return "\n".join(lines)


def run_horizon_exit_study_on_results(
    results: Sequence[ExperimentResult],
    output_dir: str | Path,
    *,
    horizon_seconds: int = DEFAULT_BASELINE_HORIZON_SECONDS,
    thresholds: Sequence[float] = DEFAULT_R_THRESHOLDS,
    extended_results: Sequence[ExperimentResult] | None = None,
    extended_horizon_seconds: int | None = None,
) -> HorizonExitStudyReport:
    report = analyze_horizon_exit_study(
        results,
        horizon_seconds=horizon_seconds,
        thresholds=thresholds,
        extended_results=extended_results,
        extended_horizon_seconds=extended_horizon_seconds,
    )
    write_horizon_exit_study_artifacts(report, output_dir)
    return report
