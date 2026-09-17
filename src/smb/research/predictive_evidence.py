"""Milestone 6A — Real-Data Predictive Evidence Study.

Research-only analysis of whether existing pre-entry features contain
measurable predictive information in real campaign data.

Does **not** modify strategy, trade construction, simulation, or execution.
Random Forest is exploratory secondary analysis only.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smb.ml.features import FEATURE_NAMES, extract_features
from smb.ml.models import MLDataset, TargetPolicy
from smb.ml.trainer import DEFAULT_MAX_DEPTH, DEFAULT_N_ESTIMATORS, DEFAULT_RANDOM_SEED
from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction

logger = logging.getLogger(__name__)

GEOMETRY_FEATURE_NAMES: tuple[str, ...] = (
    "risk_distance",
    "reward_distance",
    "risk_reward",
    "entry_price",
    "stop_loss",
    "take_profit",
)
ALL_FEATURE_NAMES: tuple[str, ...] = FEATURE_NAMES + GEOMETRY_FEATURE_NAMES
INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_THRESHOLDS: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80)
MIN_GROUP_N_FOR_TEST = 2


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    instrument: str
    signal_epoch: int
    direction: str
    outcome: str
    target: int | None
    features: dict[str, float]
    realized_r: float | None
    mfe: float | None
    mae: float | None
    duration_seconds: int | None
    risk_distance: float | None
    reward_distance: float | None
    risk_reward: float | None
    filled: bool


@dataclass(frozen=True, slots=True)
class DatasetAudit:
    """Dataset denominators for the evidence study.

    Funnel (when upstream experiment counts are provided)::

        strategy_signals
          -> candidates_accepted / candidates_rejected
          -> evidence_rows_analyzed (accepted rows that entered this study)
          -> filled_trades vs no_fill_count
          -> labeled_closed_trades = TP + SL + TIMEOUT (binary target universe)

    When upstream counts are omitted (row-only audit), ``strategy_signals`` and
    ``candidates_rejected`` are None and ``accepted_candidates`` equals
    ``evidence_rows_analyzed`` because rejected-without-simulation rows never
    enter the evidence table.
    """

    # Funnel
    evidence_rows_analyzed: int
    strategy_signals: int | None
    accepted_candidates: int
    candidates_rejected: int | None
    filled_trades: int
    no_fill_count: int
    labeled_closed_trades: int
    # Outcomes
    tp_count: int
    sl_count: int
    timeout_count: int
    other_exit_count: int
    positive_count: int
    non_positive_count: int
    positive_rate: float | None
    # Quality
    instrument_counts: dict[str, int]
    epoch_min: int | None
    epoch_max: int | None
    feature_missingness: dict[str, int]
    duplicate_count: int
    non_finite_feature_rows: int
    outcome_inconsistencies: list[str]
    explicit_positive_ceiling_note: str
    funnel_notes: list[str]
    # Backward-compatible alias used in older report prose / tests
    total_signals: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnivariateFeatureResult:
    feature: str
    group: str
    n_tp: int
    n_non_tp: int
    median_tp: float | None
    median_non_tp: float | None
    mean_tp: float | None
    mean_non_tp: float | None
    iqr_tp: float | None
    iqr_non_tp: float | None
    effect_size_cliffs_delta: float | None
    mann_whitney_u: float | None
    p_value: float | None
    direction: str
    missing_count: int
    constant_feature: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MFEDiagnostics:
    n_with_mfe: int
    mfe_median: float | None
    mfe_mean: float | None
    mfe_by_outcome: dict[str, dict[str, float | int | None]]
    mfe_by_instrument: dict[str, dict[str, float | int | None]]
    mfe_over_target_median: float | None
    mfe_over_target_mean: float | None
    feature_spearman: list[dict[str, Any]]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ThresholdResult:
    threshold: float
    n_selected: int
    tp_count: int
    non_tp_count: int
    positive_rate: float | None
    total_r: float | None
    average_r: float | None
    median_r: float | None
    oos_sample_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PooledOOSResult:
    oos_n: int
    oos_positive: int
    oos_negative: int
    model_type: str
    train_n: int
    train_positive: int
    naive_majority_class: int
    naive_accuracy: float | None
    model_accuracy: float | None
    model_balanced_accuracy: float | None
    model_precision: float | None
    model_recall: float | None
    confusion: dict[str, int]
    predicted_prob_summary: dict[str, float | None]
    selected_by_threshold: list[ThresholdResult]
    oos_r_all: list[float]
    # Always-trade OOS realized R (every labeled OOS row with finite realized_r)
    oos_all_trades_total_r: float | None
    oos_all_trades_average_r: float | None
    # Majority-class *classifier* R: act only when predicted class is positive.
    # If train majority is non-TP (0), the classifier selects zero trades -> R = 0.
    naive_majority_total_r: float | None
    naive_majority_average_r: float | None
    # Deprecated aliases kept for JSON/readers that still expect naive_*_r
    naive_total_r: float | None
    naive_average_r: float | None
    notes: list[str]
    feature_importances: dict[str, float] | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["selected_by_threshold"] = [t.to_dict() for t in self.selected_by_threshold]
        return d


@dataclass(frozen=True, slots=True)
class InstrumentEvidence:
    instrument: str
    audit: DatasetAudit
    univariate: list[UnivariateFeatureResult]
    mfe: MFEDiagnostics
    oos: PooledOOSResult | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "audit": self.audit.to_dict(),
            "univariate": [u.to_dict() for u in self.univariate],
            "mfe": self.mfe.to_dict(),
            "oos": self.oos.to_dict() if self.oos else None,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PredictiveEvidenceReport:
    schema_version: str
    audit: DatasetAudit
    statistical_power_notes: list[str]
    univariate: list[UnivariateFeatureResult]
    univariate_test_count: int
    multiple_testing_note: str
    mfe: MFEDiagnostics
    model_config: dict[str, Any]
    chronological_split: dict[str, Any]
    pooled_oos: PooledOOSResult | None
    instruments: dict[str, InstrumentEvidence]
    leakage_audit: list[str]
    limitations: list[str]
    research_conclusions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "audit": self.audit.to_dict(),
            "statistical_power_notes": list(self.statistical_power_notes),
            "univariate": [u.to_dict() for u in self.univariate],
            "univariate_test_count": self.univariate_test_count,
            "multiple_testing_note": self.multiple_testing_note,
            "mfe": self.mfe.to_dict(),
            "model_config": dict(self.model_config),
            "chronological_split": dict(self.chronological_split),
            "pooled_oos": self.pooled_oos.to_dict() if self.pooled_oos else None,
            "instruments": {k: v.to_dict() for k, v in self.instruments.items()},
            "leakage_audit": list(self.leakage_audit),
            "limitations": list(self.limitations),
            "research_conclusions": dict(self.research_conclusions),
        }



def _geometry_features(row: TradeExperimentRow) -> dict[str, float]:
    out: dict[str, float] = {}
    mapping: dict[str, float | None] = {
        "risk_distance": None,
        "reward_distance": None,
        "risk_reward": row.risk_reward,
        "entry_price": row.entry_price,
        "stop_loss": row.stop_loss,
        "take_profit": row.take_profit,
    }
    cand = row.candidate
    if cand is not None:
        mapping["risk_distance"] = cand.risk_distance
        mapping["reward_distance"] = cand.reward_distance
        mapping["risk_reward"] = cand.risk_reward
        mapping["entry_price"] = cand.entry_price
        mapping["stop_loss"] = cand.stop_loss
        mapping["take_profit"] = cand.take_profit
    for name, val in mapping.items():
        if val is None:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val):
            out[name] = float(val)
    return out


def evidence_row_from_experiment(row: TradeExperimentRow) -> EvidenceRow:
    if row.simulation is None:
        outcome = SimulationOutcome.NO_FILL
        feat_tuple = extract_features(row.signal)
        features = dict(zip(FEATURE_NAMES, feat_tuple, strict=True))
        features.update(_geometry_features(row))
        return EvidenceRow(
            instrument=row.instrument,
            signal_epoch=row.signal_epoch,
            direction=row.direction if isinstance(row.direction, str) else str(row.direction),
            outcome=outcome.value,
            target=None,
            features=features,
            realized_r=row.realized_r,
            mfe=row.mfe,
            mae=row.mae,
            duration_seconds=row.duration_seconds,
            risk_distance=features.get("risk_distance"),
            reward_distance=features.get("reward_distance"),
            risk_reward=features.get("risk_reward"),
            filled=False,
        )
    from smb.ml.dataset import resolve_target

    sim = row.simulation
    outcome = sim.outcome
    filled = sim.filled
    target = resolve_target(outcome, TargetPolicy.FILLED_TP_POSITIVE)
    feat_tuple = extract_features(row.signal)
    features = dict(zip(FEATURE_NAMES, feat_tuple, strict=True))
    features.update(_geometry_features(row))
    return EvidenceRow(
        instrument=row.instrument,
        signal_epoch=row.signal_epoch,
        direction=row.direction if isinstance(row.direction, str) else str(row.direction),
        outcome=outcome.value,
        target=target,
        features=features,
        realized_r=row.realized_r,
        mfe=row.mfe if row.mfe is not None else getattr(sim, "mfe", None),
        mae=row.mae if row.mae is not None else getattr(sim, "mae", None),
        duration_seconds=row.duration_seconds,
        risk_distance=features.get("risk_distance"),
        reward_distance=features.get("reward_distance"),
        risk_reward=features.get("risk_reward"),
        filled=filled,
    )


def evidence_rows_from_experiment_result(result: ExperimentResult) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for r in result.rows:
        if not r.accepted and r.simulation is None:
            continue
        rows.append(evidence_row_from_experiment(r))
    return rows


def evidence_rows_from_ml_dataset(dataset: MLDataset) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    for obs in dataset.observations:
        features = dict(zip(FEATURE_NAMES, obs.features, strict=True))
        rows.append(
            EvidenceRow(
                instrument=obs.instrument,
                signal_epoch=obs.signal_epoch,
                direction=obs.direction.value if isinstance(obs.direction, Direction) else str(obs.direction),
                outcome=obs.outcome.value,
                target=obs.target,
                features=features,
                realized_r=None,
                mfe=obs.mfe,
                mae=obs.mae,
                duration_seconds=None,
                risk_distance=None,
                reward_distance=None,
                risk_reward=None,
                filled=obs.filled,
            )
        )
    return rows


def audit_dataset(
    rows: Sequence[EvidenceRow],
    *,
    strategy_signals: int | None = None,
    candidates_accepted: int | None = None,
    candidates_rejected: int | None = None,
) -> DatasetAudit:
    """Build denominator-aware dataset audit.

    Row-derived counts always cover the evidence table. Optional upstream
    experiment summary counts complete the funnel from strategy signals through
    acceptance/rejection.
    """
    total = len(rows)
    outcomes = [r.outcome for r in rows]
    tp = sum(1 for o in outcomes if o == SimulationOutcome.TP.value)
    sl = sum(1 for o in outcomes if o == SimulationOutcome.SL.value)
    timeout = sum(1 for o in outcomes if o == SimulationOutcome.TIMEOUT.value)
    no_fill = sum(1 for o in outcomes if o == SimulationOutcome.NO_FILL.value)
    known = {
        SimulationOutcome.TP.value,
        SimulationOutcome.SL.value,
        SimulationOutcome.TIMEOUT.value,
        SimulationOutcome.NO_FILL.value,
    }
    other = sum(1 for o in outcomes if o not in known)
    labeled = tp + sl + timeout
    filled = sum(1 for r in rows if r.filled)
    positive = tp
    non_positive = sl + timeout
    pos_rate = (positive / labeled) if labeled > 0 else None
    inst_counts: dict[str, int] = {}
    for r in rows:
        inst_counts[r.instrument] = inst_counts.get(r.instrument, 0) + 1
    epochs = [r.signal_epoch for r in rows]
    epoch_min = min(epochs) if epochs else None
    epoch_max = max(epochs) if epochs else None
    missingness: dict[str, int] = {name: 0 for name in ALL_FEATURE_NAMES}
    non_finite_rows = 0
    for r in rows:
        bad = False
        for name in ALL_FEATURE_NAMES:
            if name not in r.features:
                missingness[name] = missingness.get(name, 0) + 1
            else:
                v = r.features[name]
                if not isinstance(v, (int, float)) or not math.isfinite(v):
                    bad = True
        if bad:
            non_finite_rows += 1
    seen: set[tuple[str, int, str]] = set()
    dup = 0
    for r in rows:
        key = (r.instrument, r.signal_epoch, r.direction)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    inconsistencies: list[str] = []
    for r in rows:
        if r.outcome == SimulationOutcome.NO_FILL.value and r.filled:
            inconsistencies.append(
                f"NO_FILL with filled=True at epoch={r.signal_epoch} instrument={r.instrument}"
            )
        if r.outcome in (
            SimulationOutcome.TP.value,
            SimulationOutcome.SL.value,
            SimulationOutcome.TIMEOUT.value,
        ) and not r.filled:
            inconsistencies.append(
                f"{r.outcome} with filled=False at epoch={r.signal_epoch} instrument={r.instrument}"
            )
        if r.target == 1 and r.outcome != SimulationOutcome.TP.value:
            inconsistencies.append(
                f"target=1 but outcome={r.outcome} at epoch={r.signal_epoch}"
            )
    note = (
        f"There are only {positive} positive observation(s) (TP) in the current "
        f"real campaign dataset used for this analysis. Non-positive (SL+TIMEOUT) "
        f"count is {non_positive}. NO_FILL count is {no_fill} (excluded from binary target)."
    )

    # Funnel denominators
    accepted = candidates_accepted if candidates_accepted is not None else total
    funnel_notes: list[str] = [
        "evidence_rows_analyzed = rows that entered this study "
        "(accepted candidates with simulation context; rejected-without-simulation "
        "rows are filtered upstream and are not present in the evidence table).",
        "labeled_closed_trades = TP + SL + TIMEOUT (binary target universe; NO_FILL excluded).",
        "filled_trades counts rows with filled=True; no_fill_count counts NO_FILL outcomes.",
    ]
    if strategy_signals is None and candidates_rejected is None:
        funnel_notes.append(
            "strategy_signals / candidates_rejected were not supplied; "
            "audit cannot reconstruct the pre-acceptance funnel from evidence rows alone. "
            "accepted_candidates defaults to evidence_rows_analyzed."
        )
    else:
        funnel_notes.append(
            "Upstream experiment summary counts were supplied to complete the "
            "strategy_signals -> accepted/rejected -> evidence_rows funnel."
        )
        if strategy_signals is not None and accepted is not None:
            if strategy_signals < accepted:
                funnel_notes.append(
                    "Warning: strategy_signals < accepted_candidates; check upstream accounting."
                )

    return DatasetAudit(
        evidence_rows_analyzed=total,
        strategy_signals=strategy_signals,
        accepted_candidates=accepted,
        candidates_rejected=candidates_rejected,
        filled_trades=filled,
        no_fill_count=no_fill,
        labeled_closed_trades=labeled,
        tp_count=tp,
        sl_count=sl,
        timeout_count=timeout,
        other_exit_count=other,
        positive_count=positive,
        non_positive_count=non_positive,
        positive_rate=pos_rate,
        instrument_counts=inst_counts,
        epoch_min=epoch_min,
        epoch_max=epoch_max,
        feature_missingness=missingness,
        duplicate_count=dup,
        non_finite_feature_rows=non_finite_rows,
        outcome_inconsistencies=inconsistencies,
        explicit_positive_ceiling_note=note,
        funnel_notes=funnel_notes,
        total_signals=total,  # alias of evidence_rows_analyzed
    )



def _iqr(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    ordered = sorted(values)
    n = len(ordered)

    def _interp(idx: float) -> float:
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return ordered[lo]
        return ordered[lo] + (idx - lo) * (ordered[hi] - ordered[lo])

    return _interp((n - 1) * 0.75) - _interp((n - 1) * 0.25)


def cliffs_delta(x: Sequence[float], y: Sequence[float]) -> float | None:
    if not x or not y:
        return None
    nx, ny = len(x), len(y)
    more = less = 0
    for a in x:
        for b in y:
            if a > b:
                more += 1
            elif a < b:
                less += 1
    return (more - less) / float(nx * ny)


def mann_whitney_u_and_p(
    x: Sequence[float], y: Sequence[float]
) -> tuple[float | None, float | None]:
    if len(x) < MIN_GROUP_N_FOR_TEST or len(y) < MIN_GROUP_N_FOR_TEST:
        return None, None
    try:
        from scipy.stats import mannwhitneyu
    except ImportError:  # pragma: no cover
        return None, None
    try:
        res = mannwhitneyu(x, y, alternative="two-sided", method="auto")
        return float(res.statistic), float(res.pvalue)
    except ValueError:
        return None, None


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> tuple[float | None, float | None]:
    if len(x) < 3 or len(x) != len(y):
        return None, None
    try:
        from scipy.stats import spearmanr
    except ImportError:  # pragma: no cover
        return None, None
    try:
        res = spearmanr(x, y)
        rho = float(res.correlation) if res.correlation is not None else None
        p = float(res.pvalue) if res.pvalue is not None else None
        if rho is not None and not math.isfinite(rho):
            rho = None
        if p is not None and not math.isfinite(p):
            p = None
        return rho, p
    except ValueError:
        return None, None


def feature_group(name: str) -> str:
    structural = {"sweep_depth", "msb_bars_after_sweep", "direction"}
    volatility = {"atr", "displacement_body_atr_ratio"}
    fvg = {
        "fvg_size",
        "fvg_size_atr_ratio",
        "fvg_size_atr_missing",
        "displacement_body_range_ratio",
        "displacement_body_atr_ratio",
    }
    context = {
        "m15_bias",
        "m15_bias_missing",
        "m15_recent_range",
        "m15_range_missing",
        "signal_vs_m15_mid",
        "signal_vs_m15_missing",
        "hour_of_day",
        "instrument_v75",
        "instrument_step100",
    }
    geometry = set(GEOMETRY_FEATURE_NAMES)
    if name in structural:
        return "structural"
    if name in volatility:
        return "volatility"
    if name in fvg:
        return "fvg_displacement"
    if name in context:
        return "context"
    if name in geometry:
        return "trade_geometry"
    return "other"


def univariate_diagnostics(
    rows: Sequence[EvidenceRow],
    feature_names: Sequence[str] | None = None,
) -> list[UnivariateFeatureResult]:
    names = list(feature_names) if feature_names is not None else list(ALL_FEATURE_NAMES)
    labeled = [r for r in rows if r.target is not None]
    results: list[UnivariateFeatureResult] = []
    for name in names:
        tp_vals: list[float] = []
        non_vals: list[float] = []
        missing = 0
        for r in labeled:
            if name not in r.features:
                missing += 1
                continue
            v = r.features[name]
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                missing += 1
                continue
            if r.target == 1:
                tp_vals.append(float(v))
            else:
                non_vals.append(float(v))
        constant = False
        all_vals = tp_vals + non_vals
        if all_vals and max(all_vals) == min(all_vals):
            constant = True
        n_tp, n_non = len(tp_vals), len(non_vals)
        med_tp = statistics.median(tp_vals) if tp_vals else None
        med_non = statistics.median(non_vals) if non_vals else None
        mean_tp = statistics.mean(tp_vals) if tp_vals else None
        mean_non = statistics.mean(non_vals) if non_vals else None
        iqr_tp = _iqr(tp_vals)
        iqr_non = _iqr(non_vals)
        delta = cliffs_delta(tp_vals, non_vals) if not constant else 0.0
        u_stat, p_val = (None, None)
        if not constant:
            u_stat, p_val = mann_whitney_u_and_p(tp_vals, non_vals)
        if n_tp < MIN_GROUP_N_FOR_TEST or n_non < MIN_GROUP_N_FOR_TEST:
            direction = "insufficient_data"
        elif constant:
            direction = "overlapping"
        elif delta is not None and delta > 0.05:
            direction = "higher_in_tp"
        elif delta is not None and delta < -0.05:
            direction = "higher_in_non_tp"
        else:
            direction = "overlapping"
        results.append(
            UnivariateFeatureResult(
                feature=name,
                group=feature_group(name),
                n_tp=n_tp,
                n_non_tp=n_non,
                median_tp=med_tp,
                median_non_tp=med_non,
                mean_tp=mean_tp,
                mean_non_tp=mean_non,
                iqr_tp=iqr_tp,
                iqr_non_tp=iqr_non,
                effect_size_cliffs_delta=delta,
                mann_whitney_u=u_stat,
                p_value=p_val,
                direction=direction,
                missing_count=missing,
                constant_feature=constant,
            )
        )
    return results


def _mfe_in_r(row: EvidenceRow) -> float | None:
    if row.mfe is None or not math.isfinite(row.mfe):
        return None
    rd = row.risk_distance
    if rd is not None and math.isfinite(rd) and rd > 0:
        return float(row.mfe) / float(rd)
    return float(row.mfe)


def _mfe_over_target(row: EvidenceRow) -> float | None:
    if row.mfe is None or row.reward_distance is None:
        return None
    if not math.isfinite(row.mfe) or not math.isfinite(row.reward_distance):
        return None
    if row.reward_distance <= 0:
        return None
    return float(row.mfe) / float(row.reward_distance)


def mfe_diagnostics(rows: Sequence[EvidenceRow]) -> MFEDiagnostics:
    filled = [r for r in rows if r.filled]
    mfe_r_vals: list[float] = []
    mfe_over_t: list[float] = []
    by_outcome: dict[str, list[float]] = {}
    by_instrument: dict[str, list[float]] = {}
    for r in filled:
        m = _mfe_in_r(r)
        if m is None:
            continue
        mfe_r_vals.append(m)
        by_outcome.setdefault(r.outcome, []).append(m)
        by_instrument.setdefault(r.instrument, []).append(m)
        ot = _mfe_over_target(r)
        if ot is not None:
            mfe_over_t.append(ot)

    def _summ(vals: list[float]) -> dict[str, float | int | None]:
        if not vals:
            return {"n": 0, "median": None, "mean": None}
        return {"n": len(vals), "median": statistics.median(vals), "mean": statistics.mean(vals)}

    mfe_by_outcome = {k: _summ(v) for k, v in sorted(by_outcome.items())}
    mfe_by_instrument = {k: _summ(v) for k, v in sorted(by_instrument.items())}
    feature_corr: list[dict[str, Any]] = []
    for name in ALL_FEATURE_NAMES:
        xs: list[float] = []
        ys: list[float] = []
        for r in filled:
            m = _mfe_in_r(r)
            if m is None or name not in r.features:
                continue
            v = r.features[name]
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            xs.append(float(v))
            ys.append(m)
        rho, p = spearman_rho(xs, ys)
        feature_corr.append(
            {"feature": name, "group": feature_group(name), "n": len(xs), "spearman_rho": rho, "p_value": p}
        )
    notes = [
        "MFE is analyzed as a continuous dependent variable; it is never used as a predictive feature.",
        "MFE in R uses risk_distance when available; otherwise raw MFE is reported.",
        "TIMEOUT trades may carry partial favorable excursion even when TP is not reached.",
    ]
    return MFEDiagnostics(
        n_with_mfe=len(mfe_r_vals),
        mfe_median=statistics.median(mfe_r_vals) if mfe_r_vals else None,
        mfe_mean=statistics.mean(mfe_r_vals) if mfe_r_vals else None,
        mfe_by_outcome=mfe_by_outcome,
        mfe_by_instrument=mfe_by_instrument,
        mfe_over_target_median=statistics.median(mfe_over_t) if mfe_over_t else None,
        mfe_over_target_mean=statistics.mean(mfe_over_t) if mfe_over_t else None,
        feature_spearman=feature_corr,
        notes=notes,
    )



def _labeled_sorted(rows: Sequence[EvidenceRow]) -> list[EvidenceRow]:
    labeled = [r for r in rows if r.target is not None]
    return sorted(labeled, key=lambda r: (r.signal_epoch, r.instrument, r.direction))


def _feature_matrix(
    rows: Sequence[EvidenceRow], names: Sequence[str]
) -> tuple[list[list[float]], list[int]]:
    X: list[list[float]] = []
    y: list[int] = []
    for r in rows:
        vec: list[float] = []
        ok = True
        for n in names:
            if n not in r.features or not math.isfinite(r.features[n]):
                ok = False
                break
            vec.append(float(r.features[n]))
        if not ok:
            continue
        assert r.target is not None
        X.append(vec)
        y.append(int(r.target))
    return X, y


def run_chronological_model(
    rows: Sequence[EvidenceRow],
    *,
    feature_names: Sequence[str] | None = None,
    train_ratio: float = 0.6,
    random_seed: int = DEFAULT_RANDOM_SEED,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> tuple[PooledOOSResult | None, dict[str, Any]]:
    names = list(feature_names) if feature_names is not None else list(ALL_FEATURE_NAMES)
    labeled = _labeled_sorted(rows)
    split_info: dict[str, Any] = {
        "train_ratio": train_ratio,
        "n_labeled": len(labeled),
        "ordering": "signal_epoch ascending (instrument, direction tie-break)",
        "shuffling": False,
        "evaluation_design": "single_chronological_holdout",
        "evaluation_note": (
            "Single chronological holdout (train_ratio early / remainder OOS). "
            "Not an expanding-window walk-forward. Exploratory secondary analysis only."
        ),
    }
    if len(labeled) < 4:
        split_info["status"] = "insufficient_labeled"
        return None, split_info
    n = len(labeled)
    n_train = max(1, int(n * train_ratio))
    if n_train >= n:
        n_train = n - 1
    train_rows = labeled[:n_train]
    oos_rows = labeled[n_train:]
    split_info.update(
        {
            "status": "ok",
            "train_n": len(train_rows),
            "oos_n": len(oos_rows),
            "train_epoch_min": train_rows[0].signal_epoch if train_rows else None,
            "train_epoch_max": train_rows[-1].signal_epoch if train_rows else None,
            "oos_epoch_min": oos_rows[0].signal_epoch if oos_rows else None,
            "oos_epoch_max": oos_rows[-1].signal_epoch if oos_rows else None,
        }
    )
    usable_names = [
        n for n in names if all(n in r.features and math.isfinite(r.features[n]) for r in labeled)
    ]
    if not usable_names:
        usable_names = [
            n for n in FEATURE_NAMES if all(n in r.features and math.isfinite(r.features[n]) for r in labeled)
        ]
    split_info["feature_names"] = usable_names
    X_train, y_train = _feature_matrix(train_rows, usable_names)
    X_oos, y_oos = _feature_matrix(oos_rows, usable_names)
    if len(X_train) < 2 or len(X_oos) < 1:
        split_info["status"] = "insufficient_after_feature_filter"
        return None, split_info
    train_pos = sum(y_train)
    oos_pos = sum(y_oos)
    split_info["train_positive"] = train_pos
    split_info["oos_positive"] = oos_pos
    notes: list[str] = []
    if train_pos == 0:
        notes.append(
            "Training set contains zero positives; Random Forest cannot learn a positive class."
        )
    if oos_pos == 0:
        notes.append("OOS set contains zero positives; positive-class metrics are unstable.")
    majority = 1 if train_pos > (len(y_train) - train_pos) else 0
    naive_correct = sum(1 for yt in y_oos if yt == majority)
    naive_acc = naive_correct / len(y_oos) if y_oos else None
    feature_importances: dict[str, float] | None = None
    probs: list[float] = []
    preds: list[int] = []
    if train_pos == 0 or train_pos == len(y_train):
        notes.append("Single-class training set: using constant probability = train positive rate.")
        p_const = float(train_pos) / len(y_train)
        probs = [p_const] * len(y_oos)
        preds = [1 if p_const >= 0.5 else 0] * len(y_oos)
        model_type = "constant_majority"
    else:
        from sklearn.ensemble import RandomForestClassifier

        clf = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            random_state=random_seed,
            n_jobs=1,
        )
        clf.fit(X_train, y_train)
        proba = clf.predict_proba(X_oos)
        classes = list(clf.classes_)
        if 1 in classes:
            idx = classes.index(1)
            probs = [float(p[idx]) for p in proba]
        else:
            probs = [0.0] * len(y_oos)
        preds = [1 if p >= 0.5 else 0 for p in probs]
        model_type = "RandomForestClassifier"
        if hasattr(clf, "feature_importances_"):
            feature_importances = {
                usable_names[i]: float(clf.feature_importances_[i]) for i in range(len(usable_names))
            }
    tp = sum(1 for pr, yt in zip(preds, y_oos, strict=True) if pr == 1 and yt == 1)
    fp = sum(1 for pr, yt in zip(preds, y_oos, strict=True) if pr == 1 and yt == 0)
    tn = sum(1 for pr, yt in zip(preds, y_oos, strict=True) if pr == 0 and yt == 0)
    fn = sum(1 for pr, yt in zip(preds, y_oos, strict=True) if pr == 0 and yt == 1)
    confusion = {"tp": tp, "fp": fp, "tn": tn, "fn": fn}
    model_acc = (tp + tn) / len(y_oos) if y_oos else None
    sens = tp / (tp + fn) if (tp + fn) > 0 else None
    spec = tn / (tn + fp) if (tn + fp) > 0 else None
    bal_acc = (sens + spec) / 2.0 if sens is not None and spec is not None else None
    prec = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = sens
    prob_summ = {
        "min": min(probs) if probs else None,
        "median": statistics.median(probs) if probs else None,
        "mean": statistics.mean(probs) if probs else None,
        "max": max(probs) if probs else None,
    }
    oos_used: list[EvidenceRow] = []
    for r in oos_rows:
        ok = all(n in r.features and math.isfinite(r.features[n]) for n in usable_names)
        if ok:
            oos_used.append(r)
    assert len(oos_used) == len(probs)
    threshold_results: list[ThresholdResult] = []
    for thr in thresholds:
        selected_idx = [i for i, p in enumerate(probs) if p >= thr]
        n_sel = len(selected_idx)
        tp_c = sum(1 for i in selected_idx if y_oos[i] == 1)
        non_c = n_sel - tp_c
        pos_rate = (tp_c / n_sel) if n_sel > 0 else None
        rs = [
            oos_used[i].realized_r
            for i in selected_idx
            if oos_used[i].realized_r is not None and math.isfinite(oos_used[i].realized_r)  # type: ignore[arg-type]
        ]
        total_r = sum(rs) if rs else None  # type: ignore[arg-type]
        avg_r = (sum(rs) / len(rs)) if rs else None  # type: ignore[arg-type]
        med_r = statistics.median(rs) if rs else None  # type: ignore[arg-type]
        threshold_results.append(
            ThresholdResult(
                threshold=float(thr),
                n_selected=n_sel,
                tp_count=tp_c,
                non_tp_count=non_c,
                positive_rate=pos_rate,
                total_r=total_r,
                average_r=avg_r,
                median_r=med_r,
                oos_sample_size=len(y_oos),
            )
        )
    oos_r_all = [
        float(r.realized_r)
        for r in oos_used
        if r.realized_r is not None and math.isfinite(r.realized_r)
    ]
    # Always-trade baseline: sum/mean of all OOS realized R (not a classifier).
    oos_all_total_r = sum(oos_r_all) if oos_r_all else None
    oos_all_avg_r = (sum(oos_r_all) / len(oos_r_all)) if oos_r_all else None
    # Majority-class classifier R: trade only when predicted label is positive.
    # majority is computed on the train set; applied constantly to OOS.
    if majority == 1:
        maj_r_vals = oos_r_all
        maj_total_r = oos_all_total_r
        maj_avg_r = oos_all_avg_r
        notes.append(
            "Naive majority class is positive (1): majority classifier selects all OOS trades; "
            "naive_majority_*_r equals always-trade OOS R."
        )
    else:
        # Predict non-TP for every OOS row -> no trades taken under a long-only act-on-positive rule.
        maj_r_vals = []
        maj_total_r = 0.0
        maj_avg_r = None
        notes.append(
            "Naive majority class is non-positive (0): majority classifier selects zero OOS trades "
            "under act-on-predicted-positive semantics; naive_majority_total_r = 0."
        )
    notes.append(
        "oos_all_trades_*_r is the always-trade OOS realized-R baseline (not the majority classifier)."
    )
    notes.append("Threshold sensitivity is exploratory only; no threshold is declared optimal.")
    notes.append("Pooled OOS metrics are preferred over per-fold metrics when positives are sparse.")
    pooled = PooledOOSResult(
        oos_n=len(y_oos),
        oos_positive=oos_pos,
        oos_negative=len(y_oos) - oos_pos,
        model_type=model_type,
        train_n=len(y_train),
        train_positive=train_pos,
        naive_majority_class=majority,
        naive_accuracy=naive_acc,
        model_accuracy=model_acc,
        model_balanced_accuracy=bal_acc,
        model_precision=prec,
        model_recall=recall,
        confusion=confusion,
        predicted_prob_summary=prob_summ,
        selected_by_threshold=threshold_results,
        oos_r_all=oos_r_all,
        oos_all_trades_total_r=oos_all_total_r,
        oos_all_trades_average_r=oos_all_avg_r,
        naive_majority_total_r=maj_total_r,
        naive_majority_average_r=maj_avg_r,
        # Deprecated aliases: historically stored always-trade R under naive_* names.
        # Readers should prefer oos_all_trades_* and naive_majority_*.
        naive_total_r=oos_all_total_r,
        naive_average_r=oos_all_avg_r,
        notes=notes,
        feature_importances=feature_importances,
    )
    return pooled, split_info



def statistical_power_notes(audit: DatasetAudit) -> list[str]:
    pos = audit.positive_count
    neg = audit.non_positive_count
    prev = audit.positive_rate
    notes = [
        f"Positive observations (TP): {pos}.",
        f"Non-positive observations (SL + TIMEOUT): {neg}.",
        f"Positive prevalence among labeled trades: {prev if prev is not None else 'undefined'}.",
        "Train/test splitting with very few positives yields folds that may contain 0 or 1 positive.",
        "Model fitting with rare positives risks unstable estimates, separation, and noise selection.",
        "Confidence intervals around effect sizes and classification metrics are extremely wide.",
        "A reliable production decision threshold cannot be established from a handful of positives.",
        "A non-significant test must not be interpreted as proof that the null is true.",
        "With this sample size it is not possible to perfectly distinguish: "
        "(1) no detectable evidence, (2) insufficient power, (3) genuinely weak/no association.",
    ]
    if pos <= 6:
        notes.insert(
            0,
            f"CRITICAL: only {pos} positive outcome(s) are available. "
            "All predictive claims are severely power-limited.",
        )
    return notes


def leakage_audit_notes() -> list[str]:
    return [
        "Features are constructed exclusively from StrategySignal fields and TradeCandidate geometry available at signal/construction time.",
        "Outcome, exit price, future candles, MFE, MAE, and duration are never used as predictive features.",
        "MFE is analyzed only as a dependent variable in continuous analysis.",
        "Chronological split ensures training observations precede OOS by signal_epoch; no random shuffle.",
        "Random Forest hyperparameters are fixed (no search on the full dataset).",
        "Preprocessing that learns parameters is fit on training data only when used inside OOS modeling.",
    ]


def build_limitations(audit: DatasetAudit, inst_audits: dict[str, DatasetAudit]) -> list[str]:
    lim = [
        audit.explicit_positive_ceiling_note,
        "NO_FILL trades are excluded from the binary TP vs non-TP target but are reported in the dataset audit.",
        "Random Forest is secondary/exploratory and is not the primary evidence source.",
        "Univariate p-values are not corrected for multiplicity by default; raw p-values are reported alongside effect sizes.",
        "Feature importance from Random Forest must not be over-interpreted with this sample size.",
        "Threshold sensitivity is subject to selection bias and low power; no threshold is declared optimal.",
    ]
    for inst, a in inst_audits.items():
        if a.positive_count <= 1:
            lim.append(
                f"Instrument {inst}: only {a.positive_count} positive observation(s); "
                "results are descriptive only and insufficient for meaningful predictive inference."
            )
    return lim


def research_conclusions(
    audit: DatasetAudit,
    univariate: Sequence[UnivariateFeatureResult],
    mfe: MFEDiagnostics,
    pooled: PooledOOSResult | None,
    inst_map: dict[str, InstrumentEvidence],
) -> dict[str, str]:
    pos = audit.positive_count
    tested = [u for u in univariate if u.p_value is not None]
    strong = [
        u for u in tested
        if u.p_value is not None and u.p_value < 0.05
        and u.effect_size_cliffs_delta is not None and abs(u.effect_size_cliffs_delta) >= 0.3
    ]
    if pos < 3:
        a = (
            f"With only {pos} positive observation(s), univariate tests lack power. "
            "No statistically reliable evidence of feature separation between TP and non-TP can be claimed."
        )
    elif strong:
        names = ", ".join(u.feature for u in strong[:5])
        a = (
            f"A small number of features ({names}) showed comparatively larger descriptive separation. "
            "Given the positive ceiling, this is exploratory evidence, not proof of a deployable edge."
        )
    elif tested:
        a = (
            "No feature met both a moderate effect-size threshold and an uncorrected p<0.05 criterion. "
            "No clear univariate separation was detected; however, the sample is too small to distinguish "
            "weak signal from insufficient power."
        )
    else:
        a = "Insufficient sample size to compute reliable univariate tests for most features."

    ranked = sorted(
        [u for u in univariate if u.effect_size_cliffs_delta is not None],
        key=lambda u: abs(u.effect_size_cliffs_delta or 0.0),
        reverse=True,
    )
    if ranked and abs(ranked[0].effect_size_cliffs_delta or 0) > 0.05:
        top = ", ".join(f"{u.feature} (δ={u.effect_size_cliffs_delta:.3f})" for u in ranked[:3])
        b = f"Largest descriptive effect sizes (Cliff's δ): {top}."
    else:
        b = "No feature showed a meaningful descriptive effect size in this sample."

    c = (
        f"Evidence is primarily exploratory because of the {pos}-positive ceiling. "
        "Results must not be treated as statistically convincing for production decisions."
    )

    if mfe.n_with_mfe == 0:
        d = "No MFE observations were available for continuous analysis."
    else:
        corr_hits = [
            x for x in mfe.feature_spearman
            if x.get("p_value") is not None and x["p_value"] < 0.05
            and x.get("spearman_rho") is not None and abs(x["spearman_rho"]) >= 0.3
        ]
        if corr_hits:
            d = (
                f"Continuous MFE analysis found {len(corr_hits)} feature association(s) with |ρ|≥0.3 "
                "and uncorrected p<0.05. TIMEOUT trades may retain partial favorable excursion."
            )
        else:
            d = (
                f"Among {mfe.n_with_mfe} filled trades with MFE, no strong Spearman association "
                f"was detected. Median MFE={mfe.mfe_median}. This does not prove absence of association."
            )

    if pooled is None:
        e = "Chronological model was not fit (insufficient labeled data)."
    else:
        e = (
            f"OOS n={pooled.oos_n} (positives={pooled.oos_positive}). "
            f"Model accuracy={pooled.model_accuracy}, naive majority accuracy={pooled.naive_accuracy}. "
        )
        if pooled.oos_positive == 0:
            e += "OOS contained zero positives; no evidence beyond the naive baseline can be claimed."
        else:
            e += "Sparse positives prevent reliable claims of practical predictive gain over the naive baseline."

    if pooled is None or pooled.oos_n < 5:
        f = "Pooled OOS results are not stable enough to support any practical conclusion."
    else:
        f = (
            f"Pooled OOS used {pooled.oos_n} observations with {pooled.oos_positive} positive(s). "
            "Estimates remain high-variance; practical conclusions are not supported."
        )

    v75 = inst_map.get(INSTRUMENT_V75)
    step = inst_map.get(INSTRUMENT_STEP)
    v75_pos = v75.audit.positive_count if v75 else 0
    step_pos = step.audit.positive_count if step else 0
    g = (
        f"V75 positives={v75_pos}; Step Index positives={step_pos}. "
        "Step Index results are descriptive only when positives ≤ 1. "
        "Evidence is not sufficiently similar and abundant to justify treating a pooled model as "
        "equally supported for both instruments."
    )
    h = (
        "Milestone 6B (horizon-aware trade construction & exit study) is justified as a research "
        "follow-on because continuous MFE and TIMEOUT behavior can inform exit/horizon design without "
        "requiring a proven pre-entry classifier. 6A does not establish a production feature gate."
    )
    return {
        "A_univariate_separation": a,
        "B_strongest_descriptive_features": b,
        "C_convincing_or_exploratory": c,
        "D_mfe_information": d,
        "E_model_vs_naive": e,
        "F_pooled_oos_stability": f,
        "G_instrument_similarity": g,
        "H_proceed_to_6b": h,
    }


def analyze_evidence(
    rows: Sequence[EvidenceRow],
    *,
    train_ratio: float = 0.6,
    random_seed: int = DEFAULT_RANDOM_SEED,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    strategy_signals: int | None = None,
    candidates_accepted: int | None = None,
    candidates_rejected: int | None = None,
) -> PredictiveEvidenceReport:
    audit = audit_dataset(
        rows,
        strategy_signals=strategy_signals,
        candidates_accepted=candidates_accepted,
        candidates_rejected=candidates_rejected,
    )
    power = statistical_power_notes(audit)
    uni = univariate_diagnostics(rows)
    mfe = mfe_diagnostics(rows)
    pooled, split_info = run_chronological_model(
        rows, train_ratio=train_ratio, random_seed=random_seed, thresholds=thresholds
    )
    inst_map: dict[str, InstrumentEvidence] = {}
    inst_audits: dict[str, DatasetAudit] = {}
    for inst in sorted({r.instrument for r in rows}):
        sub = [r for r in rows if r.instrument == inst]
        ia = audit_dataset(sub)
        inst_audits[inst] = ia
        iuni = univariate_diagnostics(sub)
        imfe = mfe_diagnostics(sub)
        notes: list[str] = []
        if ia.positive_count <= 1:
            notes.append(
                f"Only {ia.positive_count} positive(s) for {inst}; predictive inference is not supported."
            )
        ioos = None
        if ia.labeled_closed_trades >= 4 and ia.positive_count >= 1:
            ioos, _ = run_chronological_model(
                sub, train_ratio=train_ratio, random_seed=random_seed, thresholds=thresholds
            )
        elif ia.positive_count == 0:
            notes.append("Zero positives: chronological model skipped.")
        inst_map[inst] = InstrumentEvidence(
            instrument=inst, audit=ia, univariate=iuni, mfe=imfe, oos=ioos, notes=notes
        )
    multi_note = (
        f"{len(uni)} univariate tests were performed (one per feature). "
        "Raw p-values are reported without multiplicity correction by default. "
        "Do not declare a feature useful solely because an uncorrected p-value is below 0.05."
    )
    model_config = {
        "model": "RandomForestClassifier (secondary/exploratory)",
        "n_estimators": DEFAULT_N_ESTIMATORS,
        "max_depth": DEFAULT_MAX_DEPTH,
        "random_seed": random_seed,
        "train_ratio": train_ratio,
        "thresholds": list(thresholds),
        "primary_analysis": "univariate + continuous MFE",
        "evaluation_design": "single_chronological_holdout",
        "evaluation_note": (
            "Not expanding-window walk-forward; one train/OOS cut by signal_epoch order."
        ),
    }
    return PredictiveEvidenceReport(
        schema_version="6a.1",
        audit=audit,
        statistical_power_notes=power,
        univariate=uni,
        univariate_test_count=len(uni),
        multiple_testing_note=multi_note,
        mfe=mfe,
        model_config=model_config,
        chronological_split=split_info,
        pooled_oos=pooled,
        instruments=inst_map,
        leakage_audit=leakage_audit_notes(),
        limitations=build_limitations(audit, inst_audits),
        research_conclusions=research_conclusions(audit, uni, mfe, pooled, inst_map),
    )


def analyze_from_experiment_result(result: ExperimentResult, **kwargs: Any) -> PredictiveEvidenceReport:
    return analyze_evidence(evidence_rows_from_experiment_result(result), **kwargs)


def analyze_from_ml_dataset(dataset: MLDataset, **kwargs: Any) -> PredictiveEvidenceReport:
    return analyze_evidence(evidence_rows_from_ml_dataset(dataset), **kwargs)


def _fmt(v: float | None, digits: int = 4) -> str:
    if v is None:
        return "—"
    if not math.isfinite(v):
        return "nan"
    return f"{v:.{digits}g}"


def format_predictive_evidence_report(report: PredictiveEvidenceReport) -> str:
    a = report.audit
    lines: list[str] = [
        "# Milestone 6A — Real-Data Predictive Evidence Study",
        "",
        "## Research question",
        "",
        "Do the existing pre-entry features contain evidence of predictive information "
        "in the real campaign data, and how much can we infer given the observed number of positive outcomes?",
        "",
        "## Scope freeze",
        "",
        "Strategy engine, sweep/MSB/displacement/FVG detection, trade construction, RR, "
        "simulation fill/timeout/NO_FILL semantics, and live/demo execution were **not** modified.",
        "",
        "## Dataset audit",
        "",
        f"- Strategy signals (upstream): **{a.strategy_signals if a.strategy_signals is not None else 'not supplied'}**",
        f"- Candidates accepted (upstream): **{a.accepted_candidates}**",
        f"- Candidates rejected (upstream): **{a.candidates_rejected if a.candidates_rejected is not None else 'not supplied'}**",
        f"- Evidence rows analyzed: **{a.evidence_rows_analyzed}**",
        f"- Filled trades: **{a.filled_trades}**",
        f"- NO_FILL: **{a.no_fill_count}** (excluded from binary target)",
        f"- Labeled closed (TP+SL+TIMEOUT): **{a.labeled_closed_trades}**",
        f"- TP (positive): **{a.tp_count}**",
        f"- SL: **{a.sl_count}**",
        f"- TIMEOUT: **{a.timeout_count}**",
        f"- Positive rate (among labeled): **{a.positive_rate}**",
        f"- Instrument counts: `{a.instrument_counts}`",
        f"- Epoch range: `{a.epoch_min}` → `{a.epoch_max}`",
        f"- Duplicate observations: **{a.duplicate_count}**",
        f"- Non-finite feature rows: **{a.non_finite_feature_rows}**",
        "",
        f"> **{a.explicit_positive_ceiling_note}**",
        "",
        "### Funnel notes",
        "",
    ]
    for n in a.funnel_notes:
        lines.append(f"- {n}")
    lines += [
        "",
        "## Statistical power / inference ceiling",
        "",
    ]
    for n in report.statistical_power_notes:
        lines.append(f"- {n}")
    lines += ["", "## Univariate feature diagnostics (PRIMARY)", "", report.multiple_testing_note, ""]
    lines.append(
        "| Feature | Group | n_TP | n_non | median_TP | median_non | Cliff δ | p (MWU) | direction |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for u in report.univariate:
        lines.append(
            f"| {u.feature} | {u.group} | {u.n_tp} | {u.n_non_tp} | "
            f"{_fmt(u.median_tp)} | {_fmt(u.median_non_tp)} | "
            f"{_fmt(u.effect_size_cliffs_delta)} | {_fmt(u.p_value)} | {u.direction} |"
        )
    lines += ["", "## Continuous MFE analysis (PRIMARY SECONDARY)", ""]
    m = report.mfe
    lines.append(f"- Observations with MFE: **{m.n_with_mfe}**")
    lines.append(f"- Median MFE (R or raw): **{_fmt(m.mfe_median)}**")
    lines.append(f"- Mean MFE: **{_fmt(m.mfe_mean)}**")
    lines.append(f"- Median MFE/target_distance: **{_fmt(m.mfe_over_target_median)}**")
    lines.append(f"- MFE by outcome: `{m.mfe_by_outcome}`")
    lines.append(f"- MFE by instrument: `{m.mfe_by_instrument}`")
    lines.append("")
    for note in m.notes:
        lines.append(f"- {note}")
    lines += [
        "",
        "## Chronological model (exploratory secondary)",
        "",
        "Evaluation design: **single chronological holdout** "
        "(early `train_ratio` for training, remainder for OOS). "
        "This is **not** an expanding-window walk-forward validation.",
        "",
    ]
    lines.append(f"Config: `{report.model_config}`")
    lines.append(f"Split: `{report.chronological_split}`")
    if report.pooled_oos is None:
        lines.append("Pooled OOS model was **not** produced (insufficient data).")
    else:
        p = report.pooled_oos
        lines.append(f"- Model type: **{p.model_type}**")
        lines.append(f"- Train n / positives: **{p.train_n}** / **{p.train_positive}**")
        lines.append(f"- OOS n / positives: **{p.oos_n}** / **{p.oos_positive}**")
        lines.append(f"- Naive majority class: **{p.naive_majority_class}**")
        lines.append(f"- Naive accuracy: **{_fmt(p.naive_accuracy)}**")
        lines.append(f"- Model accuracy: **{_fmt(p.model_accuracy)}**")
        lines.append(f"- Balanced accuracy: **{_fmt(p.model_balanced_accuracy)}**")
        lines.append(f"- Precision / recall: **{_fmt(p.model_precision)}** / **{_fmt(p.model_recall)}**")
        lines.append(f"- Confusion (0.5): `{p.confusion}`")
        lines.append(f"- Predicted probability summary: `{p.predicted_prob_summary}`")
        lines.append(
            f"- Always-trade OOS total R: **{_fmt(p.oos_all_trades_total_r)}** "
            f"(avg **{_fmt(p.oos_all_trades_average_r)}**)"
        )
        lines.append(
            f"- Naive majority-classifier total R: **{_fmt(p.naive_majority_total_r)}** "
            f"(avg **{_fmt(p.naive_majority_average_r)}**; act-on-predicted-positive)"
        )
        lines.append("")
        lines.append("### Threshold sensitivity (no winner selected)")
        lines.append("")
        lines.append("| threshold | n_selected | TP | non-TP | pos_rate | total_R | avg_R | median_R |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for t in p.selected_by_threshold:
            lines.append(
                f"| {t.threshold:.2f} | {t.n_selected} | {t.tp_count} | {t.non_tp_count} | "
                f"{_fmt(t.positive_rate)} | {_fmt(t.total_r)} | {_fmt(t.average_r)} | {_fmt(t.median_r)} |"
            )
        for note in p.notes:
            lines.append(f"- {note}")
    lines += ["", "## Instrument-separated analysis", ""]
    for inst, ev in sorted(report.instruments.items()):
        lines.append(f"### {inst}")
        lines.append(
            f"- TP={ev.audit.tp_count}, SL={ev.audit.sl_count}, "
            f"TIMEOUT={ev.audit.timeout_count}, NO_FILL={ev.audit.no_fill_count}"
        )
        lines.append(f"- Positives: **{ev.audit.positive_count}**")
        for n in ev.notes:
            lines.append(f"- {n}")
        lines.append("")
    lines += ["", "## Leakage audit", ""]
    for n in report.leakage_audit:
        lines.append(f"- {n}")
    lines += ["", "## Limitations", ""]
    for n in report.limitations:
        lines.append(f"- {n}")
    lines += ["", "## Research conclusions", ""]
    for key, text in report.research_conclusions.items():
        lines.append(f"### {key}")
        lines.append(text)
        lines.append("")
    lines += [
        "## Final principle",
        "",
        "6A is an evidence study, not a strategy optimization milestone. "
        "If the data are insufficient to answer a question, the correct result is "
        "to report insufficient evidence — not to manufacture confidence.",
        "",
    ]
    return "\n".join(lines)


def write_predictive_evidence_artifacts(
    report: PredictiveEvidenceReport,
    output_dir: str | Path,
) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "predictive_evidence.json"
    md_path = out / "predictive_evidence_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_predictive_evidence_report(report), encoding="utf-8")
    return json_path, md_path


def run_predictive_evidence_on_results(
    results: Sequence[ExperimentResult],
    output_dir: str | Path,
    **kwargs: Any,
) -> PredictiveEvidenceReport:
    rows: list[EvidenceRow] = []
    for res in results:
        rows.extend(evidence_rows_from_experiment_result(res))
    report = analyze_evidence(rows, **kwargs)
    write_predictive_evidence_artifacts(report, output_dir)
    return report

