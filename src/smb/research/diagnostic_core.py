"""Milestone 5F — Structured Baseline Diagnostic Research.

Pure observer / research layer over actual campaign execution results.
Does **not** change strategy, trade construction, risk, simulation, fill,
TIMEOUT, or NO_FILL semantics. No optimization, filters, or ML.

Canonical flow::

    CampaignRunner -> CampaignBaselineAnalyzer -> BaselineDiagnosticAnalyzer
        -> diagnostic.json + report.md

All metrics are derived from supplied campaign analysis / experiment rows.
Nothing is hard-coded from a particular 5D run.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
)
from smb.research.experiment import TradeExperimentRow
from smb.research.stats import distribution, percentile
from smb.simulation.models import SimulationOutcome

SAMPLE_VERY_SMALL = 30
SAMPLE_SMALL = 80
SAMPLE_PRELIMINARY = 200

SampleSizeClass = Literal["very_small", "small", "preliminary", "stronger"]
DEFAULT_HORIZON_SECONDS = 900


def sample_size_class(n: int) -> SampleSizeClass:
    if n < SAMPLE_VERY_SMALL:
        return "very_small"
    if n < SAMPLE_SMALL:
        return "small"
    if n < SAMPLE_PRELIMINARY:
        return "preliminary"
    return "stronger"


def sample_size_warning(n: int, label: str = "subgroup") -> str | None:
    cls = sample_size_class(n)
    if cls == "very_small":
        return (
            f"{label}: n={n} is very small (<{SAMPLE_VERY_SMALL}); "
            "insufficient for directional or causal conclusions."
        )
    if cls == "small":
        return (
            f"{label}: n={n} is small ({SAMPLE_VERY_SMALL}–{SAMPLE_SMALL - 1}); "
            "treat findings as hypotheses only."
        )
    if cls == "preliminary":
        return (
            f"{label}: n={n} is preliminary/usable "
            f"({SAMPLE_SMALL}–{SAMPLE_PRELIMINARY - 1}); still not definitive."
        )
    return None


def _safe_rate(num: int, den: int) -> float | None:
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
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _outcome_str(row: TradeExperimentRow) -> str | None:
    if row.outcome is None:
        return None
    return row.outcome.value if hasattr(row.outcome, "value") else str(row.outcome)


def _m15_bias(row: TradeExperimentRow) -> str:
    sig = row.signal
    if sig is None:
        return "unknown"
    ctx = getattr(sig, "m15_context", None)
    if ctx is None:
        return "unknown"
    bias = getattr(ctx, "directional_bias", None)
    return str(bias) if bias is not None else "unknown"


def _is_filled_outcome(outcome: str | None) -> bool:
    return outcome in {
        SimulationOutcome.TP.value,
        SimulationOutcome.SL.value,
        SimulationOutcome.TIMEOUT.value,
    }


@dataclass(frozen=True, slots=True)
class OutcomeDistribution:
    total_signals: int
    accepted: int
    rejected: int
    filled: int
    tp: int
    sl: int
    timeout: int
    no_fill: int
    fill_rate: float | None
    tp_rate_accepted: float | None
    sl_rate_accepted: float | None
    timeout_rate_accepted: float | None
    no_fill_rate_accepted: float | None
    tp_rate_filled: float | None
    sl_rate_filled: float | None
    timeout_rate_filled: float | None
    win_rate_filled: float | None
    win_rate_all_accepted: float | None
    win_rate_all_signals: float | None
    sample_class: SampleSizeClass

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RDistribution:
    count: int
    total_r: float | None
    average_r: float | None
    median_r: float | None
    min_r: float | None
    max_r: float | None
    std_r: float | None
    positive_r_count: int
    negative_r_count: int
    zero_r_count: int
    by_outcome: Mapping[str, dict[str, float | int | None]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total_r": self.total_r,
            "average_r": self.average_r,
            "median_r": self.median_r,
            "min_r": self.min_r,
            "max_r": self.max_r,
            "std_r": self.std_r,
            "positive_r_count": self.positive_r_count,
            "negative_r_count": self.negative_r_count,
            "zero_r_count": self.zero_r_count,
            "by_outcome": dict(self.by_outcome),
        }


@dataclass(frozen=True, slots=True)
class ExcursionStats:
    count: int
    mean: float | None
    median: float | None
    minimum: float | None
    maximum: float | None
    p25: float | None
    p75: float | None
    p90: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DurationStats:
    count: int
    mean: float | None
    median: float | None
    minimum: float | None
    maximum: float | None
    near_horizon_count: int
    horizon_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CohortMetrics:
    label: str
    signals: int
    accepted: int
    filled: int
    tp: int
    sl: int
    timeout: int
    no_fill: int
    fill_rate: float | None
    win_rate_filled: float | None
    total_r: float | None
    average_r: float | None
    average_mae: float | None
    average_mfe: float | None
    average_duration: float | None
    sample_class: SampleSizeClass
    warning: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FailureModeEntry:
    rank: int
    mode: str
    frequency: int
    frequency_share: float | None
    r_impact: float | None
    instruments: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["instruments"] = list(self.instruments)
        return d


@dataclass(frozen=True, slots=True)
class InstrumentDiagnostic:
    instrument: str
    campaign_id: str
    ticks_processed: int
    start_epoch: int | None
    end_epoch: int | None
    start_utc: str | None
    end_utc: str | None
    outcome: OutcomeDistribution
    r_metrics: RDistribution
    mae: ExcursionStats
    mfe: ExcursionStats
    mae_by_outcome: Mapping[str, ExcursionStats]
    mfe_by_outcome: Mapping[str, ExcursionStats]
    duration: DurationStats
    duration_by_outcome: Mapping[str, DurationStats]
    by_direction: Mapping[str, CohortMetrics]
    by_m15_context: Mapping[str, CohortMetrics]
    signal_characteristics: Mapping[str, Any]
    sample_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "campaign_id": self.campaign_id,
            "ticks_processed": self.ticks_processed,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "outcome": self.outcome.to_dict(),
            "r_metrics": self.r_metrics.to_dict(),
            "mae": self.mae.to_dict(),
            "mfe": self.mfe.to_dict(),
            "mae_by_outcome": {k: v.to_dict() for k, v in self.mae_by_outcome.items()},
            "mfe_by_outcome": {k: v.to_dict() for k, v in self.mfe_by_outcome.items()},
            "duration": self.duration.to_dict(),
            "duration_by_outcome": {
                k: v.to_dict() for k, v in self.duration_by_outcome.items()
            },
            "by_direction": {k: v.to_dict() for k, v in self.by_direction.items()},
            "by_m15_context": {k: v.to_dict() for k, v in self.by_m15_context.items()},
            "signal_characteristics": dict(self.signal_characteristics),
            "sample_warnings": list(self.sample_warnings),
        }


@dataclass(frozen=True, slots=True)
class BaselineDiagnosticReport:
    metadata: Mapping[str, Any]
    configuration_identity: Mapping[str, Any]
    source: str
    sample_sizes: Mapping[str, Any]
    overall_metrics: OutcomeDistribution
    instrument_metrics: Mapping[str, InstrumentDiagnostic]
    outcome_metrics: Mapping[str, Any]
    r_metrics: RDistribution
    mae_metrics: Mapping[str, Any]
    mfe_metrics: Mapping[str, Any]
    duration_metrics: Mapping[str, Any]
    direction_metrics: Mapping[str, CohortMetrics]
    context_metrics: Mapping[str, CohortMetrics]
    signal_characteristic_metrics: Mapping[str, Any]
    failure_modes: tuple[FailureModeEntry, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": dict(self.metadata),
            "configuration_identity": dict(self.configuration_identity),
            "source": self.source,
            "sample_sizes": dict(self.sample_sizes),
            "overall_metrics": self.overall_metrics.to_dict(),
            "instrument_metrics": {
                k: v.to_dict() for k, v in self.instrument_metrics.items()
            },
            "outcome_metrics": dict(self.outcome_metrics),
            "r_metrics": self.r_metrics.to_dict(),
            "mae_metrics": dict(self.mae_metrics),
            "mfe_metrics": dict(self.mfe_metrics),
            "duration_metrics": dict(self.duration_metrics),
            "direction_metrics": {
                k: v.to_dict() for k, v in self.direction_metrics.items()
            },
            "context_metrics": {
                k: v.to_dict() for k, v in self.context_metrics.items()
            },
            "signal_characteristic_metrics": dict(self.signal_characteristic_metrics),
            "failure_modes": [f.to_dict() for f in self.failure_modes],
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
            "recommendation": self.recommendation,
        }


def _excursion_from_values(values: Sequence[float]) -> ExcursionStats:
    if not values:
        return ExcursionStats(
            count=0, mean=None, median=None, minimum=None, maximum=None,
            p25=None, p75=None, p90=None,
        )
    dist = distribution(list(values))
    return ExcursionStats(
        count=dist.count,
        mean=dist.mean,
        median=dist.median,
        minimum=dist.min,
        maximum=dist.max,
        p25=percentile(list(values), 25),
        p75=dist.p75,
        p90=dist.p90,
    )


def _duration_from_values(
    values: Sequence[float], horizon: int = DEFAULT_HORIZON_SECONDS
) -> DurationStats:
    if not values:
        return DurationStats(
            count=0, mean=None, median=None, minimum=None, maximum=None,
            near_horizon_count=0, horizon_seconds=horizon,
        )
    near = sum(1 for v in values if v >= horizon * 0.95)
    return DurationStats(
        count=len(values),
        mean=_mean(values),
        median=_median(values),
        minimum=min(values),
        maximum=max(values),
        near_horizon_count=near,
        horizon_seconds=horizon,
    )


def _outcome_distribution(rows: Sequence[TradeExperimentRow]) -> OutcomeDistribution:
    total = len(rows)
    accepted_rows = [r for r in rows if r.accepted]
    accepted = len(accepted_rows)
    rejected = total - accepted
    tp = sl = timeout = no_fill = 0
    for r in accepted_rows:
        o = _outcome_str(r)
        if o == SimulationOutcome.TP.value:
            tp += 1
        elif o == SimulationOutcome.SL.value:
            sl += 1
        elif o == SimulationOutcome.TIMEOUT.value:
            timeout += 1
        elif o == SimulationOutcome.NO_FILL.value:
            no_fill += 1
    filled = tp + sl + timeout
    return OutcomeDistribution(
        total_signals=total,
        accepted=accepted,
        rejected=rejected,
        filled=filled,
        tp=tp,
        sl=sl,
        timeout=timeout,
        no_fill=no_fill,
        fill_rate=_safe_rate(filled, accepted),
        tp_rate_accepted=_safe_rate(tp, accepted),
        sl_rate_accepted=_safe_rate(sl, accepted),
        timeout_rate_accepted=_safe_rate(timeout, accepted),
        no_fill_rate_accepted=_safe_rate(no_fill, accepted),
        tp_rate_filled=_safe_rate(tp, filled),
        sl_rate_filled=_safe_rate(sl, filled),
        timeout_rate_filled=_safe_rate(timeout, filled),
        win_rate_filled=_safe_rate(tp, filled),
        win_rate_all_accepted=_safe_rate(tp, accepted),
        win_rate_all_signals=_safe_rate(tp, total),
        sample_class=sample_size_class(total),
    )


def _r_distribution(rows: Sequence[TradeExperimentRow]) -> RDistribution:
    with_r = [
        r for r in rows
        if r.accepted and r.realized_r is not None and math.isfinite(float(r.realized_r))
    ]
    rs = [float(r.realized_r) for r in with_r if r.realized_r is not None]
    by_out: dict[str, list[float]] = defaultdict(list)
    for r in with_r:
        o = _outcome_str(r) or "unknown"
        if r.realized_r is not None:
            by_out[o].append(float(r.realized_r))
    by_outcome: dict[str, dict[str, float | int | None]] = {}
    for o, vals in sorted(by_out.items()):
        by_outcome[o] = {
            "count": len(vals),
            "total_r": sum(vals) if vals else None,
            "average_r": _mean(vals),
            "median_r": _median(vals),
        }
    timeout_rows = [
        r for r in rows
        if r.accepted and _outcome_str(r) == SimulationOutcome.TIMEOUT.value
    ]
    if timeout_rows and SimulationOutcome.TIMEOUT.value not in by_outcome:
        by_outcome[SimulationOutcome.TIMEOUT.value] = {
            "count": len(timeout_rows),
            "total_r": None,
            "average_r": None,
            "median_r": None,
            "note": "TIMEOUT has no realized_r under current simulation semantics",
        }
    return RDistribution(
        count=len(rs),
        total_r=sum(rs) if rs else None,
        average_r=_mean(rs),
        median_r=_median(rs),
        min_r=min(rs) if rs else None,
        max_r=max(rs) if rs else None,
        std_r=_std(rs),
        positive_r_count=sum(1 for x in rs if x > 0),
        negative_r_count=sum(1 for x in rs if x < 0),
        zero_r_count=sum(1 for x in rs if x == 0),
        by_outcome=by_outcome,
    )


def _cohort_metrics(label: str, rows: Sequence[TradeExperimentRow]) -> CohortMetrics:
    od = _outcome_distribution(rows)
    rd = _r_distribution(rows)
    filled_rows = [
        r for r in rows if r.accepted and _is_filled_outcome(_outcome_str(r))
    ]
    mae_vals = [
        float(r.mae) for r in filled_rows
        if r.mae is not None and math.isfinite(float(r.mae))
    ]
    mfe_vals = [
        float(r.mfe) for r in filled_rows
        if r.mfe is not None and math.isfinite(float(r.mfe))
    ]
    dur_vals = [
        float(r.duration_seconds) for r in filled_rows
        if r.duration_seconds is not None and r.duration_seconds >= 0
    ]
    n = od.total_signals
    return CohortMetrics(
        label=label,
        signals=n,
        accepted=od.accepted,
        filled=od.filled,
        tp=od.tp,
        sl=od.sl,
        timeout=od.timeout,
        no_fill=od.no_fill,
        fill_rate=od.fill_rate,
        win_rate_filled=od.win_rate_filled,
        total_r=rd.total_r,
        average_r=rd.average_r,
        average_mae=_mean(mae_vals),
        average_mfe=_mean(mfe_vals),
        average_duration=_mean(dur_vals),
        sample_class=sample_size_class(n),
        warning=sample_size_warning(n, label),
    )


def _signal_characteristic_summary(rows: Sequence[TradeExperimentRow]) -> dict[str, Any]:
    accepted = [r for r in rows if r.accepted]
    if not accepted:
        return {"available": False, "note": "no accepted signals"}

    body_atr: list[float] = []
    body_range: list[float] = []
    fvg_size: list[float] = []
    fvg_size_atr: list[float] = []
    risk_dist: list[float] = []
    reward_dist: list[float] = []
    rr: list[float] = []
    bars_after_sweep: list[int] = []

    for r in accepted:
        sig = r.signal
        if sig is None:
            continue
        disp = getattr(sig, "displacement", None)
        if disp is not None:
            if getattr(disp, "body_atr_ratio", None) is not None:
                body_atr.append(float(disp.body_atr_ratio))
            if getattr(disp, "body_range_ratio", None) is not None:
                body_range.append(float(disp.body_range_ratio))
        fvg = getattr(sig, "fvg", None)
        if fvg is not None:
            if getattr(fvg, "size", None) is not None:
                fvg_size.append(float(fvg.size))
            if getattr(fvg, "size_atr_ratio", None) is not None:
                fvg_size_atr.append(float(fvg.size_atr_ratio))
        msb = getattr(sig, "msb", None)
        if msb is not None and getattr(msb, "bars_after_sweep", None) is not None:
            bars_after_sweep.append(int(msb.bars_after_sweep))
        cand = r.candidate
        if cand is not None:
            if getattr(cand, "risk_distance", None) is not None:
                risk_dist.append(float(cand.risk_distance))
            if getattr(cand, "reward_distance", None) is not None:
                reward_dist.append(float(cand.reward_distance))
            if getattr(cand, "risk_reward", None) is not None:
                rr.append(float(cand.risk_reward))

    def _summ(vals: Sequence[float]) -> dict[str, Any]:
        if not vals:
            return {"count": 0}
        return {
            "count": len(vals),
            "mean": _mean(vals),
            "median": _median(vals),
            "min": min(vals),
            "max": max(vals),
        }

    return {
        "available": True,
        "displacement_body_atr_ratio": _summ(body_atr),
        "displacement_body_range_ratio": _summ(body_range),
        "fvg_size": _summ(fvg_size),
        "fvg_size_atr_ratio": _summ(fvg_size_atr),
        "msb_bars_after_sweep": {
            "count": len(bars_after_sweep),
            "mean": _mean([float(x) for x in bars_after_sweep]) if bars_after_sweep else None,
            "median": _median([float(x) for x in bars_after_sweep]) if bars_after_sweep else None,
        },
        "entry_to_stop_distance": _summ(risk_dist),
        "entry_to_target_distance": _summ(reward_dist),
        "risk_reward": _summ(rr),
        "limitation": (
            "Only fields already present on StrategySignal / TradeCandidate "
            "are summarized; no new strategy logic was added."
        ),
    }


def _instrument_diagnostic(
    analysis: CampaignBaselineAnalysis,
    rows: Sequence[TradeExperimentRow],
    horizon: int = DEFAULT_HORIZON_SECONDS,
) -> InstrumentDiagnostic:
    od = _outcome_distribution(rows)
    rd = _r_distribution(rows)
    filled_rows = [
        r for r in rows if r.accepted and _is_filled_outcome(_outcome_str(r))
    ]
    mae_vals = [
        float(r.mae) for r in filled_rows
        if r.mae is not None and math.isfinite(float(r.mae))
    ]
    mfe_vals = [
        float(r.mfe) for r in filled_rows
        if r.mfe is not None and math.isfinite(float(r.mfe))
    ]
    dur_vals = [
        float(r.duration_seconds) for r in filled_rows
        if r.duration_seconds is not None and r.duration_seconds >= 0
    ]

    mae_by: dict[str, ExcursionStats] = {}
    mfe_by: dict[str, ExcursionStats] = {}
    dur_by: dict[str, DurationStats] = {}
    for outcome in (
        SimulationOutcome.TP.value,
        SimulationOutcome.SL.value,
        SimulationOutcome.TIMEOUT.value,
    ):
        cohort = [r for r in filled_rows if _outcome_str(r) == outcome]
        mae_by[outcome] = _excursion_from_values([
            float(r.mae) for r in cohort
            if r.mae is not None and math.isfinite(float(r.mae))
        ])
        mfe_by[outcome] = _excursion_from_values([
            float(r.mfe) for r in cohort
            if r.mfe is not None and math.isfinite(float(r.mfe))
        ])
        dur_by[outcome] = _duration_from_values([
            float(r.duration_seconds) for r in cohort
            if r.duration_seconds is not None and r.duration_seconds >= 0
        ], horizon=horizon)

    by_dir: dict[str, CohortMetrics] = {}
    dir_groups: dict[str, list[TradeExperimentRow]] = defaultdict(list)
    for r in rows:
        dir_groups[str(r.direction)].append(r)
    for k, v in sorted(dir_groups.items()):
        by_dir[k] = _cohort_metrics(k, v)

    by_ctx: dict[str, CohortMetrics] = {}
    ctx_groups: dict[str, list[TradeExperimentRow]] = defaultdict(list)
    for r in rows:
        ctx_groups[_m15_bias(r)].append(r)
    for k, v in sorted(ctx_groups.items()):
        by_ctx[k] = _cohort_metrics(k, v)

    warnings: list[str] = []
    w = sample_size_warning(od.total_signals, f"{analysis.instrument} overall")
    if w:
        warnings.append(w)
    for c in by_dir.values():
        if c.warning:
            warnings.append(c.warning)
    for k, c in by_ctx.items():
        if c.warning and k != "unknown":
            warnings.append(c.warning)

    return InstrumentDiagnostic(
        instrument=analysis.instrument,
        campaign_id=analysis.campaign_id,
        ticks_processed=analysis.ticks_processed,
        start_epoch=analysis.start_epoch,
        end_epoch=analysis.end_epoch,
        start_utc=analysis.start_utc,
        end_utc=analysis.end_utc,
        outcome=od,
        r_metrics=rd,
        mae=_excursion_from_values(mae_vals),
        mfe=_excursion_from_values(mfe_vals),
        mae_by_outcome=mae_by,
        mfe_by_outcome=mfe_by,
        duration=_duration_from_values(dur_vals, horizon=horizon),
        duration_by_outcome=dur_by,
        by_direction=by_dir,
        by_m15_context=by_ctx,
        signal_characteristics=_signal_characteristic_summary(rows),
        sample_warnings=tuple(warnings),
    )


def _rank_failure_modes(
    instrument_diags: Mapping[str, InstrumentDiagnostic],
    overall: OutcomeDistribution,
    overall_r: RDistribution,
) -> tuple[FailureModeEntry, ...]:
    entries: list[tuple[str, int, float | None, list[str], str]] = []

    timeout_freq = sum(d.outcome.timeout for d in instrument_diags.values())
    timeout_inst = [d.instrument for d in instrument_diags.values() if d.outcome.timeout > 0]
    entries.append((
        "TIMEOUT", timeout_freq, None, timeout_inst,
        "Frequent unfinished trades at horizon; MFE/MAE descriptive only — "
        "do not treat positive MFE as a missed win.",
    ))

    no_fill_freq = sum(d.outcome.no_fill for d in instrument_diags.values())
    no_fill_inst = [d.instrument for d in instrument_diags.values() if d.outcome.no_fill > 0]
    entries.append((
        "NO_FILL", no_fill_freq, 0.0, no_fill_inst,
        "Accepted candidates that never filled; opportunity cost, zero realized R.",
    ))

    sl_freq = sum(d.outcome.sl for d in instrument_diags.values())
    sl_r = None
    if overall_r.by_outcome.get(SimulationOutcome.SL.value):
        sl_r = overall_r.by_outcome[SimulationOutcome.SL.value].get("total_r")
    sl_inst = [d.instrument for d in instrument_diags.values() if d.outcome.sl > 0]
    entries.append((
        "SL", sl_freq, float(sl_r) if sl_r is not None else None, sl_inst,
        "Stop-loss exits; primary source of negative realized R when present.",
    ))

    tp_freq = sum(d.outcome.tp for d in instrument_diags.values())
    tp_r = None
    if overall_r.by_outcome.get(SimulationOutcome.TP.value):
        tp_r = overall_r.by_outcome[SimulationOutcome.TP.value].get("total_r")
    entries.append((
        "insufficient_TP_realization", tp_freq,
        float(tp_r) if tp_r is not None else None,
        [d.instrument for d in instrument_diags.values() if d.outcome.tp > 0],
        "Low TP count relative to filled trades reduces positive R contribution.",
    ))

    def sort_key(item: tuple[str, int, float | None, list[str], str]) -> tuple:
        mode, freq, r_imp, _, _ = item
        is_failure = 0 if mode != "insufficient_TP_realization" else 1
        r_mag = abs(r_imp) if r_imp is not None else -1.0
        return (is_failure, -freq, -r_mag)

    ranked = sorted(entries, key=sort_key)
    result: list[FailureModeEntry] = []
    for i, (mode, freq, r_imp, insts, notes) in enumerate(ranked, start=1):
        share = _safe_rate(freq, overall.accepted) if overall.accepted else None
        result.append(FailureModeEntry(
            rank=i, mode=mode, frequency=freq, frequency_share=share,
            r_impact=r_imp, instruments=tuple(insts), notes=notes,
        ))
    return tuple(result)


def _default_limitations() -> tuple[str, ...]:
    return (
        "Diagnostics are observational only; association is not causation.",
        "Small subgroups must not be used to declare a configuration profitable or invalid.",
        "TIMEOUT has no realized exit price under current simulation semantics; "
        "MFE on timeouts is descriptive only.",
        "Instrument behavior may differ substantially; do not pool blindly.",
        "Strategy, trade construction, and simulation were frozen for this analysis.",
        "No live or demo execution was performed.",
    )


def _build_recommendation(
    overall: OutcomeDistribution,
    overall_r: RDistribution,
    failure_modes: Sequence[FailureModeEntry],
) -> str:
    parts: list[str] = [
        "Continue research-only investigation of the frozen baseline.",
        "Do not deploy live/demo and do not modify strategy "
        "parameters based on this diagnostic alone.",
    ]
    if overall.sample_class in ("very_small", "small"):
        parts.append(
            "Primary next step: expand historical coverage further to reach "
            "preliminary/campaign-scale samples per instrument before any design change."
        )
    top = failure_modes[0].mode if failure_modes else None
    if top == "TIMEOUT":
        parts.append(
            "Hypothesis for later testing (not implementation): timeout frequency "
            "and path-to-TP behavior under the fixed 900s horizon."
        )
    elif top == "NO_FILL":
        parts.append(
            "Hypothesis for later testing (not implementation): entry-zone / fill "
            "conditions contributing to NO_FILL rate."
        )
    elif top == "SL":
        parts.append(
            "Hypothesis for later testing (not implementation): stop placement and "
            "adverse excursion relative to structure under frozen geometry."
        )
    if overall_r.average_r is not None and overall_r.average_r < 0:
        parts.append(
            "Observed average realized R is negative in this sample; treat as "
            "evidence of baseline weakness under current data, not a mandate to curve-fit."
        )
    parts.append(
        "Any promising subgroup should be recorded as a research hypothesis only."
    )
    return " ".join(parts)


def _outcome_from_analyses(
    analyses: Sequence[CampaignBaselineAnalysis],
) -> OutcomeDistribution:
    total = accepted = filled = tp = sl = timeout = no_fill = 0
    for a in analyses:
        o = a.overall
        total += o.signals
        accepted += o.accepted
        filled += o.filled
        tp += o.wins
        sl += o.losses
        timeout += o.timeouts
        no_fill += o.no_fills
    rejected = total - accepted
    return OutcomeDistribution(
        total_signals=total,
        accepted=accepted,
        rejected=rejected,
        filled=filled,
        tp=tp,
        sl=sl,
        timeout=timeout,
        no_fill=no_fill,
        fill_rate=_safe_rate(filled, accepted),
        tp_rate_accepted=_safe_rate(tp, accepted),
        sl_rate_accepted=_safe_rate(sl, accepted),
        timeout_rate_accepted=_safe_rate(timeout, accepted),
        no_fill_rate_accepted=_safe_rate(no_fill, accepted),
        tp_rate_filled=_safe_rate(tp, filled),
        sl_rate_filled=_safe_rate(sl, filled),
        timeout_rate_filled=_safe_rate(timeout, filled),
        win_rate_filled=_safe_rate(tp, filled),
        win_rate_all_accepted=_safe_rate(tp, accepted),
        win_rate_all_signals=_safe_rate(tp, total),
        sample_class=sample_size_class(total),
    )


