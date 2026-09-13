"""Milestone 5B — campaign-scale baseline experiment analysis.

Observational layer over CampaignRunner / HistoricalResearchExperiment results.
Does **not** change strategy, trade construction, simulation, or risk semantics.

Produces per-instrument segmentation (direction, M15 bias, outcomes, MAE/MFE,
rejections, duration) plus a multi-instrument comparison table and a research
interpretation suitable for deciding the *next* milestone — not for optimizing
this one.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from smb.research.baseline import (
    BaselineAnalysisCalculator,
    BaselineAnalysisReport,
    format_baseline_analysis,
)
from smb.research.campaign import (
    CampaignConfig,
    CampaignResults,
    CampaignRunner,
    CampaignSummary,
    _maximum_drawdown_r,
    _profit_factor,
)
from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.simulation.models import SimulationOutcome

InterpretationLabel = Literal[
    "BASELINE PROMISING",
    "BASELINE MIXED",
    "BASELINE WEAK",
    "BASELINE INCONCLUSIVE",
]

# Heuristic only — not statistical significance.
CAMPAIGN_SCALE_SIGNAL_THRESHOLD = 200
SMALL_SAMPLE_SIGNAL_THRESHOLD = 80


@dataclass(frozen=True, slots=True)
class SegmentMetrics:
    """Performance metrics for one observational cohort."""

    label: str
    signals: int
    accepted: int
    rejected: int
    filled: int
    wins: int
    losses: int
    timeouts: int
    no_fills: int
    win_rate: float | None
    average_r: float | None
    total_r: float | None
    profit_factor: float | None
    maximum_drawdown_r: float | None
    average_mae: float | None
    average_mfe: float | None
    median_mae: float | None
    median_mfe: float | None
    average_duration_seconds: float | None
    median_duration_seconds: float | None
    sample_note: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # JSON-safe
        if d.get("profit_factor") is not None and not math.isfinite(d["profit_factor"]):
            d["profit_factor"] = None
        return d


@dataclass(frozen=True, slots=True)
class CampaignBaselineAnalysis:
    """Full 5B analysis for one instrument campaign."""

    campaign_id: str
    instrument: str
    start_epoch: int | None
    end_epoch: int | None
    start_utc: str | None
    end_utc: str | None
    ticks_processed: int
    m1_candles: int
    m15_candles: int
    overall: SegmentMetrics
    by_direction: Mapping[str, SegmentMetrics]
    by_m15_bias: Mapping[str, SegmentMetrics]
    rejection_counts: Mapping[str, int]
    outcome_counts: Mapping[str, int]
    baseline_report: BaselineAnalysisReport | None
    interpretation: InterpretationLabel
    interpretation_notes: tuple[str, ...]
    sample_scale: str  # "campaign_scale" | "small_sample" | "borderline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "instrument": self.instrument,
            "start_epoch": self.start_epoch,
            "end_epoch": self.end_epoch,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "ticks_processed": self.ticks_processed,
            "m1_candles": self.m1_candles,
            "m15_candles": self.m15_candles,
            "overall": self.overall.to_dict(),
            "by_direction": {k: v.to_dict() for k, v in self.by_direction.items()},
            "by_m15_bias": {k: v.to_dict() for k, v in self.by_m15_bias.items()},
            "rejection_counts": dict(self.rejection_counts),
            "outcome_counts": dict(self.outcome_counts),
            "baseline_report": (
                self.baseline_report.to_dict() if self.baseline_report else None
            ),
            "interpretation": self.interpretation,
            "interpretation_notes": list(self.interpretation_notes),
            "sample_scale": self.sample_scale,
        }


@dataclass(frozen=True, slots=True)
class MultiInstrumentComparison:
    """Side-by-side instrument comparison (not a portfolio)."""

    instruments: tuple[str, ...]
    rows: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruments": list(self.instruments),
            "rows": [dict(r) for r in self.rows],
        }


def _epoch_utc(epoch: int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _sample_note(n_signals: int) -> str:
    if n_signals < SMALL_SAMPLE_SIGNAL_THRESHOLD:
        return (
            f"small-sample evidence (n_signals={n_signals} "
            f"< {SMALL_SAMPLE_SIGNAL_THRESHOLD}); do not over-interpret"
        )
    if n_signals < CAMPAIGN_SCALE_SIGNAL_THRESHOLD:
        return (
            f"borderline sample (n_signals={n_signals}); "
            f"larger than original ~80-signal baseline but below "
            f"campaign-scale threshold {CAMPAIGN_SCALE_SIGNAL_THRESHOLD}"
        )
    return f"campaign-scale sample (n_signals={n_signals})"


def _sample_scale(n_signals: int) -> str:
    if n_signals < SMALL_SAMPLE_SIGNAL_THRESHOLD:
        return "small_sample"
    if n_signals < CAMPAIGN_SCALE_SIGNAL_THRESHOLD:
        return "borderline"
    return "campaign_scale"


def _realized_rs_chronological(rows: Sequence[TradeExperimentRow]) -> list[float]:
    ordered = sorted(
        (r for r in rows if r.realized_r is not None and math.isfinite(float(r.realized_r))),
        key=lambda r: (r.signal_epoch, str(r.direction)),
    )
    return [float(r.realized_r) for r in ordered if r.realized_r is not None]


def _segment_from_rows(label: str, rows: Sequence[TradeExperimentRow]) -> SegmentMetrics:
    signals = len(rows)
    accepted = [r for r in rows if r.accepted]
    rejected = signals - len(accepted)

    def _out(r: TradeExperimentRow) -> str | None:
        if r.outcome is None:
            return None
        return r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome)

    wins = sum(1 for r in accepted if _out(r) == SimulationOutcome.TP.value)
    losses = sum(1 for r in accepted if _out(r) == SimulationOutcome.SL.value)
    timeouts = sum(1 for r in accepted if _out(r) == SimulationOutcome.TIMEOUT.value)
    no_fills = sum(1 for r in accepted if _out(r) == SimulationOutcome.NO_FILL.value)
    filled = wins + losses + timeouts

    rs = _realized_rs_chronological(accepted)
    pf = _profit_factor(rs)
    if pf is not None and not math.isfinite(pf):
        pf_out: float | None = None
    else:
        pf_out = pf

    mae_vals = [float(r.mae) for r in accepted if r.mae is not None and math.isfinite(r.mae)]
    mfe_vals = [float(r.mfe) for r in accepted if r.mfe is not None and math.isfinite(r.mfe)]
    dur_vals = [
        float(r.duration_seconds)
        for r in accepted
        if r.duration_seconds is not None and r.duration_seconds >= 0
    ]

    win_rate = (wins / filled) if filled > 0 else None
    avg_r = _mean(rs)
    total_r = sum(rs) if rs else None

    return SegmentMetrics(
        label=label,
        signals=signals,
        accepted=len(accepted),
        rejected=rejected,
        filled=filled,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        no_fills=no_fills,
        win_rate=win_rate,
        average_r=avg_r,
        total_r=total_r,
        profit_factor=pf_out,
        maximum_drawdown_r=_maximum_drawdown_r(rs),
        average_mae=_mean(mae_vals),
        average_mfe=_mean(mfe_vals),
        median_mae=_median(mae_vals),
        median_mfe=_median(mfe_vals),
        average_duration_seconds=_mean(dur_vals),
        median_duration_seconds=_median(dur_vals),
        sample_note=_sample_note(signals),
    )


def _m15_bias(row: TradeExperimentRow) -> str:
    sig = row.signal
    if sig is None:
        return "unknown"
    ctx = getattr(sig, "m15_context", None)
    if ctx is None:
        return "unknown"
    bias = getattr(ctx, "directional_bias", None)
    return str(bias) if bias is not None else "unknown"


def _interpret(
    overall: SegmentMetrics,
    by_direction: Mapping[str, SegmentMetrics],
    by_m15: Mapping[str, SegmentMetrics],
) -> tuple[InterpretationLabel, tuple[str, ...]]:
    notes: list[str] = []
    n = overall.signals
    notes.append(_sample_note(n))

    if n < SMALL_SAMPLE_SIGNAL_THRESHOLD:
        notes.append(
            "Signal count remains in the small-sample regime; "
            "treat any profitability conclusion as inconclusive."
        )
        return "BASELINE INCONCLUSIVE", tuple(notes)

    total_r = overall.total_r
    avg_r = overall.average_r
    pf = overall.profit_factor
    dd = overall.maximum_drawdown_r

    # Direction concentration
    dir_totals = {
        k: v.total_r
        for k, v in by_direction.items()
        if v.total_r is not None and v.filled >= 5
    }
    if len(dir_totals) >= 2:
        vals = list(dir_totals.values())
        if min(vals) < 0 < max(vals):
            notes.append(
                "Directions disagree in sign of total R — performance may be "
                "direction-concentrated (diagnostic only; strategy unchanged)."
            )

    # M15 concentration
    m15_totals = {
        k: v.total_r
        for k, v in by_m15.items()
        if v.total_r is not None and v.filled >= 5 and k != "unknown"
    }
    if len(m15_totals) >= 2:
        vals = list(m15_totals.values())
        if min(vals) < 0 < max(vals):
            notes.append(
                "M15 bias cohorts disagree in sign of total R — existing context "
                "may matter (observational only)."
            )

    if total_r is None or avg_r is None:
        notes.append("Insufficient realized-R observations (TP/SL) for expectancy.")
        return "BASELINE INCONCLUSIVE", tuple(notes)

    if avg_r > 0.05 and (pf is None or pf >= 1.2) and total_r > 0:
        notes.append(
            f"Positive average R ({avg_r:.3f}) with total R {total_r:.2f}; "
            "not optimized — observational only."
        )
        if dd is not None and dd > abs(total_r) * 2:
            notes.append(
                f"Drawdown in R ({dd:.2f}) is large relative to cumulative R; "
                "path dependency remains a concern."
            )
            return "BASELINE MIXED", tuple(notes)
        return "BASELINE PROMISING", tuple(notes)

    if avg_r < -0.05 and total_r < 0:
        notes.append(
            f"Negative average R ({avg_r:.3f}) and total R {total_r:.2f} "
            f"across n_signals={n}."
        )
        if pf is not None and pf < 0.8:
            notes.append(f"Profit factor {pf:.3f} is well below 1.")
        return "BASELINE WEAK", tuple(notes)

    notes.append(
        f"Average R {avg_r:.3f}, total R {total_r:.2f}, PF {pf} — "
        "neither clearly positive nor strongly negative."
    )
    return "BASELINE MIXED", tuple(notes)


class CampaignBaselineAnalyzer:
    """Build a :class:`CampaignBaselineAnalysis` from campaign results."""

    def analyze(self, results: CampaignResults) -> CampaignBaselineAnalysis:
        summary = results.summary
        experiment = results.experiment
        rows: list[TradeExperimentRow] = list(experiment.rows) if experiment else []

        overall = _segment_from_rows("overall", rows)

        by_dir: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        by_m15: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        rejection_counts: dict[str, int] = defaultdict(int)
        outcome_counts: dict[str, int] = defaultdict(int)

        for r in rows:
            by_dir[str(r.direction)].append(r)
            by_m15[_m15_bias(r)].append(r)
            if not r.accepted and r.rejection_reason is not None:
                key = (
                    r.rejection_reason.value
                    if hasattr(r.rejection_reason, "value")
                    else str(r.rejection_reason)
                )
                rejection_counts[key] += 1
            if r.outcome is not None:
                ok = r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome)
                outcome_counts[ok] += 1

        dir_metrics = {
            k: _segment_from_rows(k, v) for k, v in sorted(by_dir.items())
        }
        m15_metrics = {
            k: _segment_from_rows(k, v) for k, v in sorted(by_m15.items())
        }

        baseline_report: BaselineAnalysisReport | None = None
        if experiment is not None:
            baseline_report = BaselineAnalysisCalculator().analyze(experiment)

        interpretation, notes = _interpret(overall, dir_metrics, m15_metrics)

        return CampaignBaselineAnalysis(
            campaign_id=results.campaign_id,
            instrument=summary.instrument,
            start_epoch=summary.start_epoch,
            end_epoch=summary.end_epoch,
            start_utc=_epoch_utc(summary.start_epoch),
            end_utc=_epoch_utc(summary.end_epoch),
            ticks_processed=summary.ticks_processed,
            m1_candles=summary.m1_candles,
            m15_candles=summary.m15_candles,
            overall=overall,
            by_direction=dir_metrics,
            by_m15_bias=m15_metrics,
            rejection_counts=dict(rejection_counts),
            outcome_counts=dict(outcome_counts),
            baseline_report=baseline_report,
            interpretation=interpretation,
            interpretation_notes=notes,
            sample_scale=_sample_scale(overall.signals),
        )


def compare_instruments(
    analyses: Sequence[CampaignBaselineAnalysis],
) -> MultiInstrumentComparison:
    """Build a comparison table (no portfolio aggregation)."""
    instruments = tuple(a.instrument for a in analyses)
    metric_keys = [
        ("signals", lambda a: a.overall.signals),
        ("accepted", lambda a: a.overall.accepted),
        ("filled", lambda a: a.overall.filled),
        ("win_rate", lambda a: a.overall.win_rate),
        ("avg_r", lambda a: a.overall.average_r),
        ("total_r", lambda a: a.overall.total_r),
        ("profit_factor", lambda a: a.overall.profit_factor),
        ("max_dd_r", lambda a: a.overall.maximum_drawdown_r),
        ("avg_mae", lambda a: a.overall.average_mae),
        ("avg_mfe", lambda a: a.overall.average_mfe),
        ("avg_duration_s", lambda a: a.overall.average_duration_seconds),
        ("interpretation", lambda a: a.interpretation),
        ("sample_scale", lambda a: a.sample_scale),
    ]
    rows: list[dict[str, Any]] = []
    for name, fn in metric_keys:
        row: dict[str, Any] = {"metric": name}
        for a in analyses:
            row[a.instrument] = fn(a)
        rows.append(row)
    return MultiInstrumentComparison(instruments=instruments, rows=tuple(rows))


def format_campaign_baseline_report(
    analysis: CampaignBaselineAnalysis,
    *,
    include_baseline_detail: bool = True,
) -> str:
    """Human-readable Markdown research report for one instrument."""
    o = analysis.overall
    lines: list[str] = [
        f"# Campaign-scale baseline report: {analysis.instrument}",
        "",
        "## 1. Executive summary",
        f"- **instrument:** {analysis.instrument}",
        f"- **campaign_id:** `{analysis.campaign_id}`",
        f"- **period (UTC):** {analysis.start_utc} → {analysis.end_utc}",
        f"- **ticks processed:** {analysis.ticks_processed}",
        f"- **signals:** {o.signals}",
        f"- **accepted / filled:** {o.accepted} / {o.filled}",
        f"- **win rate (among filled):** {o.win_rate}",
        f"- **average R:** {o.average_r}",
        f"- **total R:** {o.total_r}",
        f"- **profit factor:** {o.profit_factor}",
        f"- **max drawdown (R):** {o.maximum_drawdown_r}",
        f"- **sample scale:** {analysis.sample_scale}",
        f"- **interpretation:** **{analysis.interpretation}**",
        "",
        "### Interpretation notes",
    ]
    for n in analysis.interpretation_notes:
        lines.append(f"- {n}")

    lines += [
        "",
        "## 2. Data coverage",
        f"- ticks: {analysis.ticks_processed}",
        f"- M1 candles: {analysis.m1_candles}",
        f"- M15 candles: {analysis.m15_candles}",
        f"- start_epoch: {analysis.start_epoch}",
        f"- end_epoch: {analysis.end_epoch}",
        "",
        "## 3. Baseline strategy results",
        f"- wins / losses / timeouts / no-fill: "
        f"{o.wins} / {o.losses} / {o.timeouts} / {o.no_fills}",
        f"- average MAE / MFE: {o.average_mae} / {o.average_mfe}",
        f"- median MAE / MFE: {o.median_mae} / {o.median_mfe}",
        f"- average / median duration (s): "
        f"{o.average_duration_seconds} / {o.median_duration_seconds}",
        "",
        "## 4. Long vs short",
    ]
    for key, seg in analysis.by_direction.items():
        lines.append(
            f"- **{key}:** signals={seg.signals} filled={seg.filled} "
            f"WR={seg.win_rate} avgR={seg.average_r} totalR={seg.total_r} "
            f"PF={seg.profit_factor} maxDD={seg.maximum_drawdown_r}"
        )

    lines += ["", "## 5. Existing M15 context (causal at signal time)"]
    if not analysis.by_m15_bias or set(analysis.by_m15_bias) == {"unknown"}:
        lines.append("- M15 directional bias segmentation unavailable or all unknown.")
    else:
        for key, seg in analysis.by_m15_bias.items():
            lines.append(
                f"- **{key}:** signals={seg.signals} filled={seg.filled} "
                f"WR={seg.win_rate} avgR={seg.average_r} totalR={seg.total_r} "
                f"PF={seg.profit_factor}"
            )

    lines += [
        "",
        "## 6. Volatility / ATR context",
        "- No separate volatility-bucket classification is exposed as a first-class "
        "causal signal field beyond existing ATR ratios inside setup geometry. "
        "Bucketed low/normal/high volatility segmentation is **unavailable** "
        "without inventing thresholds (out of scope for 5B).",
        "",
        "## 7. MAE / MFE (post-entry research only)",
        f"- average MAE: {o.average_mae}",
        f"- median MAE: {o.median_mae}",
        f"- average MFE: {o.average_mfe}",
        f"- median MFE: {o.median_mfe}",
        "- MAE/MFE are not used for signal generation or trade selection.",
        "",
        "## 8. Rejections and no-fills",
        f"- rejected candidates: {o.rejected}",
        f"- no-fill among accepted: {o.no_fills}",
    ]
    if analysis.rejection_counts:
        lines.append("- rejection reasons:")
        for k, v in sorted(analysis.rejection_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  - {k}: {v}")
    else:
        lines.append("- no rejection reasons recorded (or zero rejections).")

    lines.append("- outcome counts:")
    for k, v in sorted(analysis.outcome_counts.items()):
        lines.append(f"  - {k}: {v}")

    lines += [
        "",
        "## 9. Duration",
        f"- average duration (s): {o.average_duration_seconds}",
        f"- median duration (s): {o.median_duration_seconds}",
        f"- timeout count: {o.timeouts}",
        "- Intended research horizon remains the simulation max_duration "
        "(default 900s / 15m). Compare median duration and timeout proportion "
        "against that design assumption.",
        "",
        "## 10. Failure modes (observational)",
    ]
    if o.losses > o.wins and o.filled > 0:
        lines.append("- SL hits outnumber TP hits among filled trades.")
    if o.timeouts > 0 and o.filled > 0 and o.timeouts / o.filled >= 0.25:
        lines.append(
            f"- Timeouts are material ({o.timeouts}/{o.filled} filled); "
            "many trades neither hit TP nor SL within the horizon."
        )
    if o.no_fills > 0 and o.accepted > 0 and o.no_fills / o.accepted >= 0.2:
        lines.append(
            f"- No-fill rate is material ({o.no_fills}/{o.accepted} accepted); "
            "usable frequency is reduced before performance is measured."
        )
    if not any(l.startswith("- ") for l in lines[-6:]):
        lines.append("- No single dominant failure mode flagged by simple heuristics.")

    lines += [
        "",
        "## 11. Research interpretation",
        f"- Label: **{analysis.interpretation}**",
    ]
    for n in analysis.interpretation_notes:
        lines.append(f"- {n}")

    lines += [
        "",
        "## 12. Recommendation",
        "No strategy change is made in this milestone. "
        "Use the interpretation label and segment tables to choose the *next* "
        "research question (e.g. more data, direction diagnostic depth, "
        "timeout geometry, or context-conditioned analysis) — not to optimize "
        "parameters here.",
        "",
    ]

    if include_baseline_detail and analysis.baseline_report is not None:
        lines += [
            "## Appendix — Milestone 3B baseline detail",
            "",
            format_baseline_analysis(analysis.baseline_report),
        ]

    return "\n".join(lines)


def format_comparison_report(comparison: MultiInstrumentComparison) -> str:
    lines = [
        "# Multi-instrument baseline comparison",
        "",
        "Not a portfolio. Each column is an independent campaign.",
        "",
        "| Metric | " + " | ".join(comparison.instruments) + " |",
        "| --- | " + " | ".join("---" for _ in comparison.instruments) + " |",
    ]
    for row in comparison.rows:
        cells = [str(row.get("metric", ""))]
        for inst in comparison.instruments:
            val = row.get(inst)
            if isinstance(val, float):
                cells.append(f"{val:.4f}")
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_analysis_artifacts(
    results: CampaignResults,
    analysis: CampaignBaselineAnalysis,
    *,
    output_dir: Path | None = None,
) -> dict[str, Path]:
    """Write analysis.json and analysis.md next to campaign artifacts."""
    out = Path(output_dir) if output_dir is not None else Path(results.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    analysis_json = out / "analysis.json"
    analysis_md = out / "analysis.md"
    analysis_json.write_text(
        json.dumps(analysis.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    analysis_md.write_text(
        format_campaign_baseline_report(analysis),
        encoding="utf-8",
    )
    return {"analysis_json": analysis_json, "analysis_md": analysis_md}


def run_baseline_campaign(
    data_root: str | Path,
    *,
    instrument: str,
    output_dir: str | Path,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
    campaign_id: str | None = None,
    risk_equity: float = 10_000.0,
) -> tuple[CampaignResults, CampaignBaselineAnalysis]:
    """Run one campaign and attach 5B analysis artifacts."""
    from smb.research.campaign import run_campaign

    results = run_campaign(
        data_root,
        instrument=instrument,
        output_dir=output_dir,
        start_epoch=start_epoch,
        end_epoch=end_epoch,
        campaign_id=campaign_id,
        risk_equity=risk_equity,
    )
    analysis = CampaignBaselineAnalyzer().analyze(results)
    write_analysis_artifacts(results, analysis)
    return results, analysis
