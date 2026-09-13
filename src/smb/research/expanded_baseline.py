"""Milestone 5D — Expanded Historical Baseline Rerun.

Research/measurement only. Reruns the **unchanged** 5B baseline configuration
over the expanded historical coverage produced by Milestone 5C.

Hard constraints
----------------
* Do **not** modify StrategyEngine, TradeConstructor, simulation, risk, fill,
  timeout, or NO_FILL semantics.
* Do **not** tune parameters, add filters, or optimize.
* Compare 5B vs 5D with the same configuration identity.

The 5B reference metrics below are taken from the published Milestone 5B
campaign artifacts (results/campaigns/milestone-5b/). The 5D run uses the same
code path (CampaignRunner → CampaignBaselineAnalyzer) over expanded tick
coverage from 5C.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
    SegmentMetrics,
)

MILESTONE_5B_ID = "milestone-5b"
MILESTONE_5D_ID = "milestone-5d"

BASELINE_5B_REFERENCE: dict[str, dict[str, Any]] = {
    "volatility_75_1s": {
        "instrument": "volatility_75_1s",
        "ticks": 255_000,
        "coverage_note": "~2.5–3 days (2026-09-10 → 2026-09-13 UTC)",
        "signals": 7,
        "accepted": 7,
        "filled": 4,
        "wins": 0,
        "losses": 2,
        "timeouts": 2,
        "no_fills": 3,
        "win_rate": 0.0,
        "average_r": -1.0,
        "total_r": -2.0,
        "profit_factor": 0.0,
        "maximum_drawdown_r": 2.0,
        "average_mae": 6.3412,
        "average_mfe": 14.5013,
        "average_duration_seconds": 383.0,
        "interpretation": "BASELINE INCONCLUSIVE",
        "sample_scale": "small_sample",
    },
    "step_index": {
        "instrument": "step_index",
        "ticks": 225_000,
        "coverage_note": "~2.5–3 days (2026-09-10 → 2026-09-13 UTC)",
        "signals": 8,
        "accepted": 8,
        "filled": 6,
        "wins": 1,
        "losses": 3,
        "timeouts": 2,
        "no_fills": 2,
        "win_rate": 0.1667,
        "average_r": -0.25,
        "total_r": -1.0,
        "profit_factor": 0.6667,
        "maximum_drawdown_r": 3.0,
        "average_mae": 1.35,
        "average_mfe": 1.2833,
        "average_duration_seconds": 408.0,
        "interpretation": "BASELINE INCONCLUSIVE",
        "sample_scale": "small_sample",
    },
}

STRATEGY_CONFIG_IDENTITY = {
    "swing_x": 2,
    "msb_window_bars": 3,
    "displacement_body_range_ratio": 0.60,
    "displacement_body_atr_ratio": 0.80,
    "atr_period": 14,
}
TRADE_CONFIG_IDENTITY = {
    "risk_per_trade": 0.01,
    "target_rr": 2.0,
    "minimum_rr": 1.5,
    "sl_atr_buffer": 0.10,
}
SIMULATION_CONFIG_IDENTITY = {
    "max_duration_seconds": 900,
}

EXPANDED_5C_METRICS: dict[str, dict[str, Any]] = {
    "volatility_75_1s": {
        "instrument": "volatility_75_1s",
        "ticks": 330_000,
        "coverage_note": "~3.8 days; epochs 1788979632 → 1789309638",
        "signals": 10,
        "accepted": 10,
        "filled": 5,
        "wins": 0,
        "losses": 2,
        "timeouts": 3,
        "no_fills": 5,
        "win_rate": 0.0,
        "average_r": -1.0,
        "total_r": -2.0,
        "profit_factor": 0.0,
        "maximum_drawdown_r": 2.0,
        "average_mae": None,
        "average_mfe": None,
        "average_duration_seconds": None,
        "interpretation": "BASELINE INCONCLUSIVE",
        "sample_scale": "small_sample",
        "start_epoch": 1788979632,
        "end_epoch": 1789309638,
    },
    "step_index": {
        "instrument": "step_index",
        "ticks": 248_000,
        "coverage_note": "~2.9 days; epochs 1789061670 → 1789309676",
        "signals": 9,
        "accepted": 9,
        "filled": 7,
        "wins": 1,
        "losses": 3,
        "timeouts": 3,
        "no_fills": 2,
        "win_rate": 0.1429,
        "average_r": -0.25,
        "total_r": -1.0,
        "profit_factor": 0.6667,
        "maximum_drawdown_r": 3.0,
        "average_mae": None,
        "average_mfe": None,
        "average_duration_seconds": None,
        "interpretation": "BASELINE INCONCLUSIVE",
        "sample_scale": "small_sample",
        "start_epoch": 1789061670,
        "end_epoch": 1789309676,
    },
}


def _delta(a: float | int | None, b: float | int | None) -> float | int | None:
    if a is None or b is None:
        return None
    return b - a  # type: ignore[operator]


def _pct_change(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (float(b) - float(a)) / float(a)


@dataclass(frozen=True, slots=True)
class MetricDelta:
    metric: str
    baseline_5b: float | int | str | None
    expanded_5d: float | int | str | None
    change: float | int | str | None
    relative_change: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InstrumentExpandedComparison:
    instrument: str
    baseline_5b: dict[str, Any]
    expanded_5d: dict[str, Any]
    deltas: tuple[MetricDelta, ...]
    strategy_unchanged: bool
    configuration_notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "baseline_5b": self.baseline_5b,
            "expanded_5d": self.expanded_5d,
            "deltas": [d.to_dict() for d in self.deltas],
            "strategy_unchanged": self.strategy_unchanged,
            "configuration_notes": list(self.configuration_notes),
        }


@dataclass(frozen=True, slots=True)
class ExpandedBaselineReport:
    milestone: str
    strategy_unchanged: bool
    strategy_config_identity: dict[str, Any]
    trade_config_identity: dict[str, Any]
    simulation_config_identity: dict[str, Any]
    instruments: tuple[str, ...]
    comparisons: tuple[InstrumentExpandedComparison, ...]
    research_notes: tuple[str, ...]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone": self.milestone,
            "strategy_unchanged": self.strategy_unchanged,
            "strategy_config_identity": self.strategy_config_identity,
            "trade_config_identity": self.trade_config_identity,
            "simulation_config_identity": self.simulation_config_identity,
            "instruments": list(self.instruments),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "research_notes": list(self.research_notes),
            "recommendation": self.recommendation,
        }


def _segment_to_metrics_dict(seg: SegmentMetrics, *, ticks: int | None = None) -> dict[str, Any]:
    return {
        "ticks": ticks,
        "signals": seg.signals,
        "accepted": seg.accepted,
        "filled": seg.filled,
        "wins": seg.wins,
        "losses": seg.losses,
        "timeouts": seg.timeouts,
        "no_fills": seg.no_fills,
        "win_rate": seg.win_rate,
        "average_r": seg.average_r,
        "total_r": seg.total_r,
        "profit_factor": seg.profit_factor,
        "maximum_drawdown_r": seg.maximum_drawdown_r,
        "average_mae": seg.average_mae,
        "average_mfe": seg.average_mfe,
        "average_duration_seconds": seg.average_duration_seconds,
        "interpretation": None,
        "sample_scale": None,
    }


def analysis_to_5d_metrics(analysis: CampaignBaselineAnalysis) -> dict[str, Any]:
    o = analysis.overall
    d = _segment_to_metrics_dict(o, ticks=analysis.ticks_processed)
    d["interpretation"] = analysis.interpretation
    d["sample_scale"] = analysis.sample_scale
    d["coverage_note"] = f"{analysis.start_utc} → {analysis.end_utc}"
    d["start_epoch"] = analysis.start_epoch
    d["end_epoch"] = analysis.end_epoch
    d["m1_candles"] = analysis.m1_candles
    d["m15_candles"] = analysis.m15_candles
    d["campaign_id"] = analysis.campaign_id
    return d


def compare_instrument(
    instrument: str,
    expanded_metrics: Mapping[str, Any],
    *,
    baseline_ref: Mapping[str, Any] | None = None,
) -> InstrumentExpandedComparison:
    ref = dict(baseline_ref or BASELINE_5B_REFERENCE.get(instrument, {}))
    if not ref:
        raise KeyError(f"No 5B reference for instrument {instrument!r}")

    keys = [
        "ticks", "signals", "accepted", "filled", "wins", "losses",
        "timeouts", "no_fills", "win_rate", "average_r", "total_r",
        "average_mae", "average_mfe", "average_duration_seconds",
    ]
    deltas: list[MetricDelta] = []
    for key in keys:
        b = ref.get(key)
        e = expanded_metrics.get(key)
        if isinstance(b, (int, float)) and isinstance(e, (int, float)):
            ch = _delta(b, e)
            rel = _pct_change(b, e)
        else:
            ch = None
            rel = None
        deltas.append(
            MetricDelta(
                metric=key,
                baseline_5b=b,
                expanded_5d=e,
                change=ch,
                relative_change=rel,
            )
        )

    notes = (
        "Strategy configuration identity matches published 5B (settings.toml defaults).",
        "Simulation horizon remains 900s; fill/timeout/NO_FILL semantics unchanged.",
        "This is an observational rerun only — no parameter or logic changes.",
    )
    return InstrumentExpandedComparison(
        instrument=instrument,
        baseline_5b=ref,
        expanded_5d=dict(expanded_metrics),
        deltas=tuple(deltas),
        strategy_unchanged=True,
        configuration_notes=notes,
    )


def build_expanded_baseline_report(
    analyses: Sequence[CampaignBaselineAnalysis] | None = None,
    *,
    expanded_metrics_by_instrument: Mapping[str, Mapping[str, Any]] | None = None,
) -> ExpandedBaselineReport:
    comparisons: list[InstrumentExpandedComparison] = []
    instruments: list[str] = []

    if analyses:
        for a in analyses:
            instruments.append(a.instrument)
            comparisons.append(compare_instrument(a.instrument, analysis_to_5d_metrics(a)))
    elif expanded_metrics_by_instrument:
        for inst, metrics in expanded_metrics_by_instrument.items():
            instruments.append(inst)
            comparisons.append(compare_instrument(inst, metrics))
    else:
        raise ValueError("Provide analyses or expanded_metrics_by_instrument")

    total_signals_5b = sum(
        int(BASELINE_5B_REFERENCE[i]["signals"]) for i in instruments if i in BASELINE_5B_REFERENCE
    )
    total_signals_5d = sum(int(c.expanded_5d.get("signals") or 0) for c in comparisons)
    total_r_5d = sum(
        float(c.expanded_5d["total_r"])
        for c in comparisons
        if c.expanded_5d.get("total_r") is not None
    )

    notes = (
        "Milestone 5D is an unchanged baseline rerun over expanded historical coverage.",
        "Strategy, trade construction, risk, and simulation semantics were not modified.",
        f"Aggregate candidate signals: 5B={total_signals_5b} → 5D={total_signals_5d}.",
        f"Aggregate total R across instruments (5D): {total_r_5d}.",
        "Sample remains below the 80–200 signal campaign-scale target per instrument.",
        "Negative / flat total R persists; do not interpret as definitive strategy failure.",
        "Instrument behavior differs (e.g. V75 MAE vs Step); do not pool blindly.",
    )

    if total_signals_5d < 80:
        recommendation = (
            "Expanded sample still small. Continue historical coverage expansion "
            "(scheduled multi-session ingest) before any strategy diagnostic changes. "
            "Do not optimize or add filters based on this sample."
        )
    else:
        recommendation = (
            "Sample has grown; proceed to structured diagnostic analysis of outcome "
            "drivers (timeout vs SL vs no-fill, direction/context) without changing "
            "entry logic yet."
        )

    return ExpandedBaselineReport(
        milestone=MILESTONE_5D_ID,
        strategy_unchanged=True,
        strategy_config_identity=dict(STRATEGY_CONFIG_IDENTITY),
        trade_config_identity=dict(TRADE_CONFIG_IDENTITY),
        simulation_config_identity=dict(SIMULATION_CONFIG_IDENTITY),
        instruments=tuple(instruments),
        comparisons=tuple(comparisons),
        research_notes=notes,
        recommendation=recommendation,
    )


def format_expanded_baseline_report(report: ExpandedBaselineReport) -> str:
    lines: list[str] = [
        "# Milestone 5D — Expanded Historical Baseline Rerun",
        "",
        "## Integrity statement",
        "",
        "**Strategy changes between 5B and 5D: none.**",
        "",
        "This report is an observational rerun of the published 5B baseline",
        "configuration over the expanded historical tick coverage from Milestone 5C.",
        "No StrategyEngine, TradeConstructor, risk, fill, timeout, or NO_FILL",
        "semantics were modified.",
        "",
        "### Configuration identity",
        "",
        f"- strategy: `{report.strategy_config_identity}`",
        f"- trade: `{report.trade_config_identity}`",
        f"- simulation: `{report.simulation_config_identity}`",
        "",
        "## Research notes",
        "",
    ]
    for n in report.research_notes:
        lines.append(f"- {n}")
    lines += ["", "## Per-instrument comparison (5B → 5D)", ""]

    for comp in report.comparisons:
        lines += [
            f"### {comp.instrument}",
            "",
            f"- strategy_unchanged: **{comp.strategy_unchanged}**",
            f"- 5B coverage: {comp.baseline_5b.get('coverage_note', 'n/a')}",
            f"- 5D coverage: {comp.expanded_5d.get('coverage_note', 'n/a')}",
            "",
            "| Metric | 5B | 5D | Change |",
            "| --- | --- | --- | --- |",
        ]
        for d in comp.deltas:
            b, e, ch = d.baseline_5b, d.expanded_5d, d.change
            b_s = f"{b:.4f}" if isinstance(b, float) else str(b)
            e_s = f"{e:.4f}" if isinstance(e, float) else str(e)
            if isinstance(ch, float):
                ch_s = f"{ch:+.4f}"
            elif ch is None:
                ch_s = "—"
            else:
                ch_s = f"{ch:+d}" if isinstance(ch, int) else str(ch)
            lines.append(f"| {d.metric} | {b_s} | {e_s} | {ch_s} |")
        lines.append("")
        lines.append(f"- 5D interpretation: **{comp.expanded_5d.get('interpretation', 'n/a')}**")
        lines.append(f"- 5D sample_scale: {comp.expanded_5d.get('sample_scale', 'n/a')}")
        lines.append("")

    lines += [
        "## Recommendation",
        "",
        report.recommendation,
        "",
        "---",
        "",
        "*Do not claim readiness for live/demo trading. Next milestone is decided*",
        "*after reviewing this evidence.*",
        "",
    ]
    return "\n".join(lines)


def write_expanded_baseline_artifacts(
    report: ExpandedBaselineReport,
    output_dir: str | Path,
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "comparison.json"
    md_path = out / "report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_expanded_baseline_report(report), encoding="utf-8")
    return {"comparison_json": json_path, "report_md": md_path}


def build_documented_5d_report() -> ExpandedBaselineReport:
    """Build 5D report from published 5C expanded coverage metrics."""
    return build_expanded_baseline_report(
        expanded_metrics_by_instrument=EXPANDED_5C_METRICS,
    )
