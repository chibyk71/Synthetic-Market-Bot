"""Milestone 3B — strategy baseline research analysis.

Pure observer layer over :class:`~smb.research.experiment.ExperimentResult`.
Does **not** change strategy, risk, simulation, MAE/MFE, or validation semantics.

Metric denominators (preserved from the harness / 3A)::

    filled      = TP + SL + TIMEOUT
    win_rate    = TP / filled
    realized R  = TP and SL only (TIMEOUT has no exit_price)
    excursions  = filled trades only (NO_FILL excluded)
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.research.stats import DistributionStats, distribution
from smb.simulation.models import SimulationOutcome

# Sparse time-bucket warning threshold (observation count).
SPARSE_BUCKET_THRESHOLD = 5

# MFE-in-R thresholds for timeout descriptive analysis only.
TIMEOUT_MFE_R_THRESHOLDS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)


def _safe_rate(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


@dataclass(frozen=True, slots=True)
class DirectionAnalysis:
    """Per-direction cohort with explicit denominators."""

    direction: str
    signals: int
    accepted: int
    rejected: int
    no_fill: int
    filled: int
    tp: int
    sl: int
    timeout: int
    # fill_rate = filled / accepted  (accepted = candidates that entered simulation)
    fill_rate: float | None
    # rates among filled (TP+SL+TIMEOUT)
    tp_rate_among_filled: float | None
    sl_rate_among_filled: float | None
    timeout_rate_among_filled: float | None
    # realized R: TP/SL only (TIMEOUT excluded — no exit_price)
    total_r: float | None
    average_r: float | None
    average_mae: float | None
    average_mfe: float | None
    average_duration_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeBreakdownRow:
    """One outcome with dual percentage denominators."""

    outcome: str
    count: int
    pct_of_all_signals: float | None
    """count / total signals (includes NO_FILL and rejections if present as rows)."""
    pct_of_filled: float | None
    """count / filled; always None for NO_FILL."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeExcursionRow:
    """MAE/MFE distribution for one filled outcome (TP / SL / TIMEOUT)."""

    outcome: str
    count: int
    mae: DistributionStats
    mfe: DistributionStats
    duration: DistributionStats

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "count": self.count,
            "mae": self.mae.to_dict(),
            "mfe": self.mfe.to_dict(),
            "duration": self.duration.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TimeoutAnalysis:
    """Descriptive analysis for TIMEOUT trades (no realized exit price)."""

    count: int
    average_mfe: float | None
    median_mfe: float | None
    p75_mfe: float | None
    p90_mfe: float | None
    max_mfe: float | None
    average_mae: float | None
    median_mae: float | None
    # Counts reaching at least threshold * risk_distance in MFE (price → R)
    mfe_at_least_0_5r: int
    mfe_at_least_1_0r: int
    mfe_at_least_1_5r: int
    mfe_at_least_2_0r: int
    # Denominator for the R-threshold counts (timeouts with risk_distance > 0)
    r_threshold_sample_size: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NoFillAnalysis:
    """NO_FILL cohort (accepted candidates that never filled)."""

    count: int
    pct_of_all_signals: float | None
    long_count: int
    short_count: int
    long_pct_of_no_fill: float | None
    short_pct_of_no_fill: float | None
    long_pct_of_long_signals: float | None
    short_pct_of_short_signals: float | None
    # Geometry when candidate is present (accepted then no-fill)
    average_risk_distance: float | None
    average_entry_zone_width: float | None
    average_rr: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TradeGeometryRecord:
    """Immutable research view of one accepted candidate's construction + signal."""

    signal_epoch: int
    direction: str
    accepted: bool
    outcome: str | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_distance: float | None
    reward_distance: float | None
    risk_reward: float | None
    atr: float | None
    fvg_low: float | None
    fvg_high: float | None
    fvg_size: float | None
    fvg_size_atr_ratio: float | None
    swept_level: float | None
    msb_level: float | None
    displacement_body_range_ratio: float | None
    displacement_body_atr_ratio: float | None
    m15_bias: str | None
    m15_last_end_epoch: int | None
    realized_r: float | None
    mae: float | None
    mfe: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SetupGroupStats:
    """Grouped counts for one categorical setup attribute value."""

    attribute: str
    value: str
    signals: int
    filled: int
    tp: int
    sl: int
    timeout: int
    no_fill: int
    total_r: float | None
    average_r: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TimeBucketStats:
    """UTC time-bucket performance (hour-of-day or day-of-week)."""

    bucket: str
    signals: int
    fills: int
    tp: int
    sl: int
    timeout: int
    no_fill: int
    total_r: float | None
    sparse: bool
    """True when signals < SPARSE_BUCKET_THRESHOLD."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BaselineAnalysisReport:
    """Full baseline analysis for one historical experiment."""

    instrument: str
    start_epoch: int | None
    end_epoch: int | None
    total_signals: int
    total_accepted: int
    total_rejected: int
    total_filled: int
    sparse_bucket_threshold: int
    by_direction: Mapping[str, DirectionAnalysis]
    outcomes: tuple[OutcomeBreakdownRow, ...]
    mae_distribution: DistributionStats
    mfe_distribution: DistributionStats
    duration_distribution: DistributionStats
    excursions_by_outcome: tuple[OutcomeExcursionRow, ...]
    timeout: TimeoutAnalysis
    no_fill: NoFillAnalysis
    geometry: tuple[TradeGeometryRecord, ...]
    setup_groups: tuple[SetupGroupStats, ...]
    by_hour_utc: tuple[TimeBucketStats, ...]
    by_day_of_week_utc: tuple[TimeBucketStats, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "total_signals": self.total_signals,
            "total_accepted": self.total_accepted,
            "total_rejected": self.total_rejected,
            "total_filled": self.total_filled,
            "sparse_bucket_threshold": self.sparse_bucket_threshold,
            "by_direction": {k: v.to_dict() for k, v in self.by_direction.items()},
            "outcomes": [o.to_dict() for o in self.outcomes],
            "mae_distribution": self.mae_distribution.to_dict(),
            "mfe_distribution": self.mfe_distribution.to_dict(),
            "duration_distribution": self.duration_distribution.to_dict(),
            "excursions_by_outcome": [e.to_dict() for e in self.excursions_by_outcome],
            "timeout": self.timeout.to_dict(),
            "no_fill": self.no_fill.to_dict(),
            "geometry": [g.to_dict() for g in self.geometry],
            "setup_groups": [s.to_dict() for s in self.setup_groups],
            "by_hour_utc": [b.to_dict() for b in self.by_hour_utc],
            "by_day_of_week_utc": [b.to_dict() for b in self.by_day_of_week_utc],
            "notes": list(self.notes),
        }


class BaselineAnalysisCalculator:
    """Build a :class:`BaselineAnalysisReport` from an experiment result."""

    def __init__(self, *, sparse_bucket_threshold: int = SPARSE_BUCKET_THRESHOLD) -> None:
        if sparse_bucket_threshold < 1:
            raise ValueError("sparse_bucket_threshold must be >= 1")
        self.sparse_bucket_threshold = sparse_bucket_threshold

    def analyze(self, result: ExperimentResult) -> BaselineAnalysisReport:
        rows = list(result.rows)
        summary = result.summary
        total_signals = summary.signals
        total_accepted = summary.candidates_accepted
        total_rejected = summary.candidates_rejected

        # Prefer simulation.filled when present (authoritative 2C flag).
        filled_rows = [
            r
            for r in rows
            if r.accepted
            and r.simulation is not None
            and r.simulation.filled
        ]
        total_filled = len(filled_rows)

        by_direction = self._by_direction(rows)
        outcomes = self._outcome_breakdown(rows, total_signals, total_filled)
        mae_vals = [r.mae for r in filled_rows if r.mae is not None]
        mfe_vals = [r.mfe for r in filled_rows if r.mfe is not None]
        dur_vals = [
            float(r.duration_seconds)
            for r in filled_rows
            if r.duration_seconds is not None
        ]
        excursions_by_outcome = self._excursions_by_outcome(filled_rows)
        timeout = self._timeout_analysis(rows)
        no_fill = self._no_fill_analysis(rows, total_signals)
        geometry = tuple(self._geometry_record(r) for r in rows if r.accepted)
        setup_groups = self._setup_groups(rows)
        by_hour = self._time_buckets(rows, kind="hour")
        by_dow = self._time_buckets(rows, kind="dow")

        notes = (
            "win_rate denominator = filled = TP + SL + TIMEOUT (NO_FILL excluded).",
            "realized R includes TP and SL only; TIMEOUT has no exit_price by design.",
            "MAE/MFE distributions exclude NO_FILL.",
            "Time buckets use UTC derived from signal_epoch.",
            f"Time buckets with fewer than {self.sparse_bucket_threshold} signals are marked sparse.",
            "Timeout MFE-in-R thresholds are descriptive only; TIMEOUT is not marked to market.",
        )

        return BaselineAnalysisReport(
            instrument=summary.instrument,
            start_epoch=summary.start_epoch,
            end_epoch=summary.end_epoch,
            total_signals=total_signals,
            total_accepted=total_accepted,
            total_rejected=total_rejected,
            total_filled=total_filled,
            sparse_bucket_threshold=self.sparse_bucket_threshold,
            by_direction=by_direction,
            outcomes=outcomes,
            mae_distribution=distribution(mae_vals),
            mfe_distribution=distribution(mfe_vals),
            duration_distribution=distribution(dur_vals),
            excursions_by_outcome=excursions_by_outcome,
            timeout=timeout,
            no_fill=no_fill,
            geometry=geometry,
            setup_groups=setup_groups,
            by_hour_utc=by_hour,
            by_day_of_week_utc=by_dow,
            notes=notes,
        )

    def _by_direction(
        self, rows: Sequence[TradeExperimentRow]
    ) -> dict[str, DirectionAnalysis]:
        buckets: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        for r in rows:
            buckets[str(r.direction)].append(r)
        out: dict[str, DirectionAnalysis] = {}
        for direction in sorted(buckets):
            out[direction] = self._direction_cohort(direction, buckets[direction])
        # Ensure both directions appear when empty overall is fine; only present keys
        return out

    def _direction_cohort(
        self, direction: str, rows: Sequence[TradeExperimentRow]
    ) -> DirectionAnalysis:
        signals = len(rows)
        accepted = sum(1 for r in rows if r.accepted)
        rejected = sum(1 for r in rows if not r.accepted)
        no_fill = sum(
            1
            for r in rows
            if r.accepted and r.outcome == SimulationOutcome.NO_FILL
        )
        tp = sum(1 for r in rows if r.outcome == SimulationOutcome.TP)
        sl = sum(1 for r in rows if r.outcome == SimulationOutcome.SL)
        timeout = sum(1 for r in rows if r.outcome == SimulationOutcome.TIMEOUT)
        filled = tp + sl + timeout
        r_vals = [r.realized_r for r in rows if r.realized_r is not None]
        maes = [r.mae for r in rows if r.mae is not None]
        mfes = [r.mfe for r in rows if r.mfe is not None]
        durs = [
            float(r.duration_seconds)
            for r in rows
            if r.duration_seconds is not None
        ]
        total_r = sum(r_vals) if r_vals else None
        return DirectionAnalysis(
            direction=direction,
            signals=signals,
            accepted=accepted,
            rejected=rejected,
            no_fill=no_fill,
            filled=filled,
            tp=tp,
            sl=sl,
            timeout=timeout,
            fill_rate=_safe_rate(filled, accepted),
            tp_rate_among_filled=_safe_rate(tp, filled),
            sl_rate_among_filled=_safe_rate(sl, filled),
            timeout_rate_among_filled=_safe_rate(timeout, filled),
            total_r=total_r,
            average_r=(total_r / len(r_vals)) if r_vals else None,
            average_mae=_mean(maes),
            average_mfe=_mean(mfes),
            average_duration_seconds=_mean(durs),
        )

    def _outcome_breakdown(
        self,
        rows: Sequence[TradeExperimentRow],
        total_signals: int,
        total_filled: int,
    ) -> tuple[OutcomeBreakdownRow, ...]:
        counts = {o.value: 0 for o in SimulationOutcome}
        for r in rows:
            if not r.accepted or r.outcome is None:
                continue
            counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
        ordered = (
            SimulationOutcome.TP,
            SimulationOutcome.SL,
            SimulationOutcome.TIMEOUT,
            SimulationOutcome.NO_FILL,
        )
        rows_out: list[OutcomeBreakdownRow] = []
        for outcome in ordered:
            c = counts[outcome.value]
            pct_all = _safe_rate(c, total_signals)
            if outcome == SimulationOutcome.NO_FILL:
                pct_filled = None
            else:
                pct_filled = _safe_rate(c, total_filled)
            rows_out.append(
                OutcomeBreakdownRow(
                    outcome=outcome.value,
                    count=c,
                    pct_of_all_signals=pct_all,
                    pct_of_filled=pct_filled,
                )
            )
        return tuple(rows_out)

    def _excursions_by_outcome(
        self, filled_rows: Sequence[TradeExperimentRow]
    ) -> tuple[OutcomeExcursionRow, ...]:
        by_out: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        for r in filled_rows:
            if r.outcome is None:
                continue
            by_out[r.outcome.value].append(r)
        result: list[OutcomeExcursionRow] = []
        for key in (
            SimulationOutcome.TP.value,
            SimulationOutcome.SL.value,
            SimulationOutcome.TIMEOUT.value,
        ):
            group = by_out.get(key, [])
            maes = [r.mae for r in group if r.mae is not None]
            mfes = [r.mfe for r in group if r.mfe is not None]
            durs = [
                float(r.duration_seconds)
                for r in group
                if r.duration_seconds is not None
            ]
            result.append(
                OutcomeExcursionRow(
                    outcome=key,
                    count=len(group),
                    mae=distribution(maes),
                    mfe=distribution(mfes),
                    duration=distribution(durs),
                )
            )
        return tuple(result)

    def _timeout_analysis(
        self, rows: Sequence[TradeExperimentRow]
    ) -> TimeoutAnalysis:
        timeouts = [
            r for r in rows if r.accepted and r.outcome == SimulationOutcome.TIMEOUT
        ]
        mfes = [r.mfe for r in timeouts if r.mfe is not None]
        maes = [r.mae for r in timeouts if r.mae is not None]
        mfe_dist = distribution(mfes)
        mae_dist = distribution(maes)

        threshold_counts = {t: 0 for t in TIMEOUT_MFE_R_THRESHOLDS}
        sample = 0
        for r in timeouts:
            if r.mfe is None or r.candidate is None:
                continue
            risk = r.candidate.risk_distance
            if risk is None or not math.isfinite(risk) or risk <= 0.0:
                continue
            sample += 1
            mfe_r = r.mfe / risk
            for t in TIMEOUT_MFE_R_THRESHOLDS:
                if mfe_r >= t:
                    threshold_counts[t] += 1

        return TimeoutAnalysis(
            count=len(timeouts),
            average_mfe=mfe_dist.mean,
            median_mfe=mfe_dist.median,
            p75_mfe=mfe_dist.p75,
            p90_mfe=mfe_dist.p90,
            max_mfe=mfe_dist.max,
            average_mae=mae_dist.mean,
            median_mae=mae_dist.median,
            mfe_at_least_0_5r=threshold_counts[0.5],
            mfe_at_least_1_0r=threshold_counts[1.0],
            mfe_at_least_1_5r=threshold_counts[1.5],
            mfe_at_least_2_0r=threshold_counts[2.0],
            r_threshold_sample_size=sample,
        )

    def _no_fill_analysis(
        self, rows: Sequence[TradeExperimentRow], total_signals: int
    ) -> NoFillAnalysis:
        no_fills = [
            r for r in rows if r.accepted and r.outcome == SimulationOutcome.NO_FILL
        ]
        long_nf = [r for r in no_fills if str(r.direction) == "long"]
        short_nf = [r for r in no_fills if str(r.direction) == "short"]
        long_signals = sum(1 for r in rows if str(r.direction) == "long")
        short_signals = sum(1 for r in rows if str(r.direction) == "short")
        n = len(no_fills)

        risks: list[float] = []
        widths: list[float] = []
        rrs: list[float] = []
        for r in no_fills:
            c = r.candidate
            if c is None:
                continue
            if c.risk_distance is not None and math.isfinite(c.risk_distance):
                risks.append(c.risk_distance)
            if (
                c.entry_zone_high is not None
                and c.entry_zone_low is not None
                and math.isfinite(c.entry_zone_high)
                and math.isfinite(c.entry_zone_low)
            ):
                widths.append(c.entry_zone_high - c.entry_zone_low)
            if c.risk_reward is not None and math.isfinite(c.risk_reward):
                rrs.append(c.risk_reward)

        return NoFillAnalysis(
            count=n,
            pct_of_all_signals=_safe_rate(n, total_signals),
            long_count=len(long_nf),
            short_count=len(short_nf),
            long_pct_of_no_fill=_safe_rate(len(long_nf), n),
            short_pct_of_no_fill=_safe_rate(len(short_nf), n),
            long_pct_of_long_signals=_safe_rate(len(long_nf), long_signals),
            short_pct_of_short_signals=_safe_rate(len(short_nf), short_signals),
            average_risk_distance=_mean(risks),
            average_entry_zone_width=_mean(widths),
            average_rr=_mean(rrs),
        )

    def _geometry_record(self, row: TradeExperimentRow) -> TradeGeometryRecord:
        sig = row.signal
        cand = row.candidate
        disp = sig.displacement
        fvg = sig.fvg
        m15 = sig.m15_context
        return TradeGeometryRecord(
            signal_epoch=row.signal_epoch,
            direction=str(row.direction),
            accepted=row.accepted,
            outcome=row.outcome.value if row.outcome is not None else None,
            entry_price=row.entry_price if cand is not None else None,
            stop_loss=row.stop_loss if cand is not None else None,
            take_profit=row.take_profit if cand is not None else None,
            risk_distance=cand.risk_distance if cand is not None else None,
            reward_distance=cand.reward_distance if cand is not None else None,
            risk_reward=row.risk_reward if cand is not None else None,
            atr=disp.atr if disp is not None else None,
            fvg_low=fvg.gap_low if fvg is not None else None,
            fvg_high=fvg.gap_high if fvg is not None else None,
            fvg_size=fvg.size if fvg is not None else None,
            fvg_size_atr_ratio=fvg.size_atr_ratio if fvg is not None else None,
            swept_level=sig.sweep.swept_level if sig.sweep is not None else None,
            msb_level=sig.msb.broken_level if sig.msb is not None else None,
            displacement_body_range_ratio=(
                disp.body_range_ratio if disp is not None else None
            ),
            displacement_body_atr_ratio=(
                disp.body_atr_ratio if disp is not None else None
            ),
            m15_bias=m15.directional_bias if m15 is not None else None,
            m15_last_end_epoch=m15.last_m15_end_epoch if m15 is not None else None,
            realized_r=row.realized_r,
            mae=row.mae,
            mfe=row.mfe,
        )

    def _setup_groups(
        self, rows: Sequence[TradeExperimentRow]
    ) -> tuple[SetupGroupStats, ...]:
        """Group by attributes already present on StrategySignal (no new indicators)."""
        groups: list[SetupGroupStats] = []

        def _attr_value(row: TradeExperimentRow, attr: str) -> str:
            sig = row.signal
            if attr == "direction":
                return str(row.direction)
            if attr == "m15_bias":
                bias = sig.m15_context.directional_bias
                return str(bias) if bias is not None else "unknown"
            if attr == "sweep_direction":
                return str(sig.sweep.direction)
            if attr == "msb_direction":
                return str(sig.msb.direction)
            raise KeyError(attr)

        for attr in ("direction", "m15_bias", "sweep_direction", "msb_direction"):
            buckets: dict[str, list[TradeExperimentRow]] = defaultdict(list)
            for r in rows:
                buckets[_attr_value(r, attr)].append(r)
            for value in sorted(buckets):
                group = buckets[value]
                tp = sum(1 for r in group if r.outcome == SimulationOutcome.TP)
                sl = sum(1 for r in group if r.outcome == SimulationOutcome.SL)
                timeout = sum(
                    1 for r in group if r.outcome == SimulationOutcome.TIMEOUT
                )
                no_fill = sum(
                    1 for r in group if r.outcome == SimulationOutcome.NO_FILL
                )
                filled = tp + sl + timeout
                r_vals = [r.realized_r for r in group if r.realized_r is not None]
                total_r = sum(r_vals) if r_vals else None
                groups.append(
                    SetupGroupStats(
                        attribute=attr,
                        value=value,
                        signals=len(group),
                        filled=filled,
                        tp=tp,
                        sl=sl,
                        timeout=timeout,
                        no_fill=no_fill,
                        total_r=total_r,
                        average_r=(total_r / len(r_vals)) if r_vals else None,
                    )
                )
        return tuple(groups)

    def _time_buckets(
        self, rows: Sequence[TradeExperimentRow], *, kind: str
    ) -> tuple[TimeBucketStats, ...]:
        buckets: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        for r in rows:
            key = self._bucket_key(r.signal_epoch, kind)
            buckets[key].append(r)

        def _sort_key(k: str) -> tuple:
            if kind == "hour":
                return (int(k),)
            # Monday=0 ... Sunday=6 already encoded in key order via name
            order = {
                "Monday": 0,
                "Tuesday": 1,
                "Wednesday": 2,
                "Thursday": 3,
                "Friday": 4,
                "Saturday": 5,
                "Sunday": 6,
            }
            return (order.get(k, 99), k)

        result: list[TimeBucketStats] = []
        for key in sorted(buckets, key=_sort_key):
            group = buckets[key]
            tp = sum(1 for r in group if r.outcome == SimulationOutcome.TP)
            sl = sum(1 for r in group if r.outcome == SimulationOutcome.SL)
            timeout = sum(1 for r in group if r.outcome == SimulationOutcome.TIMEOUT)
            no_fill = sum(1 for r in group if r.outcome == SimulationOutcome.NO_FILL)
            fills = tp + sl + timeout
            r_vals = [r.realized_r for r in group if r.realized_r is not None]
            total_r = sum(r_vals) if r_vals else None
            result.append(
                TimeBucketStats(
                    bucket=key,
                    signals=len(group),
                    fills=fills,
                    tp=tp,
                    sl=sl,
                    timeout=timeout,
                    no_fill=no_fill,
                    total_r=total_r,
                    sparse=len(group) < self.sparse_bucket_threshold,
                )
            )
        return tuple(result)

    @staticmethod
    def _bucket_key(signal_epoch: int, kind: str) -> str:
        dt = datetime.fromtimestamp(signal_epoch, tz=UTC)
        if kind == "hour":
            return f"{dt.hour:02d}"
        if kind == "dow":
            return dt.strftime("%A")
        raise ValueError(f"unknown time bucket kind: {kind!r}")


def format_baseline_analysis(report: BaselineAnalysisReport) -> str:
    """Human-readable expanded baseline analysis for CLI."""
    lines: list[str] = [
        "Baseline strategy analysis",
        f"  instrument:        {report.instrument}",
        f"  start_epoch:       {report.start_epoch}",
        f"  end_epoch:         {report.end_epoch}",
        f"  total_signals:     {report.total_signals}",
        f"  total_accepted:    {report.total_accepted}",
        f"  total_rejected:    {report.total_rejected}",
        f"  total_filled:      {report.total_filled}  (TP+SL+TIMEOUT)",
        "",
        "Notes:",
    ]
    for n in report.notes:
        lines.append(f"  - {n}")

    lines.append("")
    lines.append("By direction (fill_rate = filled/accepted; rates among filled):")
    for direction, d in report.by_direction.items():
        lines.append(f"  [{direction}]")
        lines.append(
            f"    signals={d.signals} accepted={d.accepted} rejected={d.rejected} "
            f"no_fill={d.no_fill} filled={d.filled}"
        )
        lines.append(
            f"    tp={d.tp} sl={d.sl} timeout={d.timeout} "
            f"fill_rate={d.fill_rate} "
            f"tp_rate_filled={d.tp_rate_among_filled} "
            f"sl_rate_filled={d.sl_rate_among_filled} "
            f"timeout_rate_filled={d.timeout_rate_among_filled}"
        )
        lines.append(
            f"    total_r={d.total_r} average_r={d.average_r} "
            f"avg_mae={d.average_mae} avg_mfe={d.average_mfe} "
            f"avg_duration={d.average_duration_seconds}"
        )

    lines.append("")
    lines.append(
        "Outcomes (pct_of_all_signals = count/signals; "
        "pct_of_filled = count/filled, None for NO_FILL):"
    )
    for o in report.outcomes:
        lines.append(
            f"  {o.outcome}: count={o.count} "
            f"pct_of_all_signals={o.pct_of_all_signals} "
            f"pct_of_filled={o.pct_of_filled}"
        )

    def _fmt_dist(label: str, dist: DistributionStats) -> None:
        lines.append(
            f"  {label}: n={dist.count} min={dist.min} median={dist.median} "
            f"mean={dist.mean} p75={dist.p75} p90={dist.p90} max={dist.max}"
        )

    lines.append("")
    lines.append("Filled-trade distributions (NO_FILL excluded):")
    _fmt_dist("MAE", report.mae_distribution)
    _fmt_dist("MFE", report.mfe_distribution)
    _fmt_dist("duration_s", report.duration_distribution)

    lines.append("")
    lines.append("Excursions by filled outcome:")
    for e in report.excursions_by_outcome:
        lines.append(f"  [{e.outcome}] n={e.count}")
        _fmt_dist("    MAE", e.mae)
        _fmt_dist("    MFE", e.mfe)
        _fmt_dist("    duration_s", e.duration)

    t = report.timeout
    lines.append("")
    lines.append("Timeout descriptive analysis (not realized P&L):")
    lines.append(f"  count={t.count}")
    lines.append(
        f"  mfe: avg={t.average_mfe} median={t.median_mfe} "
        f"p75={t.p75_mfe} p90={t.p90_mfe} max={t.max_mfe}"
    )
    lines.append(f"  mae: avg={t.average_mae} median={t.median_mae}")
    lines.append(
        f"  mfe_in_R thresholds (sample={t.r_threshold_sample_size}): "
        f">=0.5R:{t.mfe_at_least_0_5r} >=1.0R:{t.mfe_at_least_1_0r} "
        f">=1.5R:{t.mfe_at_least_1_5r} >=2.0R:{t.mfe_at_least_2_0r}"
    )

    nf = report.no_fill
    lines.append("")
    lines.append("No-fill analysis:")
    lines.append(
        f"  count={nf.count} pct_of_all_signals={nf.pct_of_all_signals} "
        f"long={nf.long_count} short={nf.short_count}"
    )
    lines.append(
        f"  long_pct_of_no_fill={nf.long_pct_of_no_fill} "
        f"short_pct_of_no_fill={nf.short_pct_of_no_fill}"
    )
    lines.append(
        f"  long_pct_of_long_signals={nf.long_pct_of_long_signals} "
        f"short_pct_of_short_signals={nf.short_pct_of_short_signals}"
    )
    lines.append(
        f"  avg_risk_distance={nf.average_risk_distance} "
        f"avg_entry_zone_width={nf.average_entry_zone_width} "
        f"avg_rr={nf.average_rr}"
    )

    lines.append("")
    lines.append("Setup groups (existing signal attributes only):")
    current_attr: str | None = None
    for g in report.setup_groups:
        if g.attribute != current_attr:
            current_attr = g.attribute
            lines.append(f"  attribute={current_attr}")
        lines.append(
            f"    value={g.value}: signals={g.signals} filled={g.filled} "
            f"tp={g.tp} sl={g.sl} timeout={g.timeout} no_fill={g.no_fill} "
            f"total_r={g.total_r} average_r={g.average_r}"
        )

    lines.append("")
    lines.append(
        f"Time buckets UTC (sparse if signals < {report.sparse_bucket_threshold}):"
    )
    lines.append("  By hour:")
    for b in report.by_hour_utc:
        sparse = " SPARSE" if b.sparse else ""
        lines.append(
            f"    hour={b.bucket}: signals={b.signals} fills={b.fills} "
            f"tp={b.tp} sl={b.sl} timeout={b.timeout} no_fill={b.no_fill} "
            f"total_r={b.total_r}{sparse}"
        )
    lines.append("  By day-of-week:")
    for b in report.by_day_of_week_utc:
        sparse = " SPARSE" if b.sparse else ""
        lines.append(
            f"    {b.bucket}: signals={b.signals} fills={b.fills} "
            f"tp={b.tp} sl={b.sl} timeout={b.timeout} no_fill={b.no_fill} "
            f"total_r={b.total_r}{sparse}"
        )

    lines.append("")
    lines.append(f"Geometry records: {len(report.geometry)} accepted candidates")
    return "\n".join(lines)
