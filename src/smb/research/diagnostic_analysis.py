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

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smb.research.campaign import CampaignResults
from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
    CampaignBaselineAnalyzer,
)
from smb.research.diagnostic_core import (
    DEFAULT_HORIZON_SECONDS,
    SAMPLE_PRELIMINARY,
    SAMPLE_SMALL,
    SAMPLE_VERY_SMALL,
    BaselineDiagnosticReport,
    FailureModeEntry,
    InstrumentDiagnostic,
    OutcomeDistribution,
    RDistribution,
    _build_recommendation,
    _cohort_metrics,
    _default_limitations,
    _duration_from_values,
    _excursion_from_values,
    _instrument_diagnostic,
    _is_filled_outcome,
    _m15_bias,
    _outcome_distribution,
    _outcome_from_analyses,
    _outcome_str,
    _r_distribution,
    _rank_failure_modes,
    _signal_characteristic_summary,
    sample_size_class,
    sample_size_warning,
)
from smb.research.experiment import TradeExperimentRow
from smb.simulation.models import SimulationOutcome

# Re-export public surface for callers that import from diagnostic_analysis
__all__ = [
    "BaselineDiagnosticAnalyzer",
    "BaselineDiagnosticReport",
    "FailureModeEntry",
    "InstrumentDiagnostic",
    "OutcomeDistribution",
    "format_diagnostic_report",
    "run_baseline_diagnostics",
    "sample_size_class",
    "sample_size_warning",
    "write_diagnostic_artifacts",
    "SAMPLE_VERY_SMALL",
    "SAMPLE_SMALL",
    "SAMPLE_PRELIMINARY",
]


class BaselineDiagnosticAnalyzer:
    """Build a BaselineDiagnosticReport from campaign execution data."""

    def analyze(
        self,
        analyses: Sequence[CampaignBaselineAnalysis],
        *,
        rows_by_instrument: Mapping[str, Sequence[TradeExperimentRow]] | None = None,
        configuration_identity: Mapping[str, Any] | None = None,
        source: str = "campaign_execution",
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
    ) -> BaselineDiagnosticReport:
        if not analyses:
            raise ValueError("analyses must be non-empty")

        rows_map: dict[str, list[TradeExperimentRow]] = {}
        if rows_by_instrument:
            for k, v in rows_by_instrument.items():
                rows_map[k] = list(v)

        instrument_diags: dict[str, InstrumentDiagnostic] = {}
        all_rows: list[TradeExperimentRow] = []

        for a in analyses:
            rows = rows_map.get(a.instrument, [])
            inst = _instrument_diagnostic(a, rows, horizon=horizon_seconds)
            instrument_diags[a.instrument] = inst
            all_rows.extend(rows)

        overall_od = (
            _outcome_distribution(all_rows)
            if all_rows
            else _outcome_from_analyses(analyses)
        )
        overall_r = (
            _r_distribution(all_rows)
            if all_rows
            else RDistribution(
                count=0, total_r=None, average_r=None, median_r=None,
                min_r=None, max_r=None, std_r=None,
                positive_r_count=0, negative_r_count=0, zero_r_count=0,
                by_outcome={},
            )
        )

        dir_groups: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        ctx_groups: dict[str, list[TradeExperimentRow]] = defaultdict(list)
        for r in all_rows:
            dir_groups[str(r.direction)].append(r)
            ctx_groups[_m15_bias(r)].append(r)
        direction_metrics = {
            k: _cohort_metrics(k, v) for k, v in sorted(dir_groups.items())
        }
        context_metrics = {
            k: _cohort_metrics(k, v) for k, v in sorted(ctx_groups.items())
        }

        failure_modes = _rank_failure_modes(instrument_diags, overall_od, overall_r)

        warnings: list[str] = []
        w = sample_size_warning(overall_od.total_signals, "aggregate")
        if w:
            warnings.append(w)
        for inst in instrument_diags.values():
            warnings.extend(inst.sample_warnings)
        if len(instrument_diags) > 1:
            warnings.append(
                "Instrument behavior may differ; comparison is side-by-side, "
                "not a pooled portfolio."
            )
        if not all_rows:
            warnings.append(
                "No TradeExperimentRow data supplied; deep R/MAE/MFE/characteristic "
                "metrics are limited. Prefer passing rows_by_instrument from campaign execution."
            )

        cfg = dict(configuration_identity or {})
        if not cfg:
            cfg = {"note": "configuration_identity not supplied; use campaign manifests"}

        sample_sizes = {
            "aggregate_signals": overall_od.total_signals,
            "aggregate_accepted": overall_od.accepted,
            "aggregate_filled": overall_od.filled,
            "aggregate_sample_class": overall_od.sample_class,
            "per_instrument": {
                k: {
                    "signals": v.outcome.total_signals,
                    "accepted": v.outcome.accepted,
                    "filled": v.outcome.filled,
                    "sample_class": v.outcome.sample_class,
                    "ticks": v.ticks_processed,
                }
                for k, v in instrument_diags.items()
            },
        }

        outcome_metrics = {
            "aggregate": overall_od.to_dict(),
            "per_instrument": {
                k: v.outcome.to_dict() for k, v in instrument_diags.items()
            },
        }

        def _filled_mae(rows: Sequence[TradeExperimentRow]) -> list[float]:
            return [
                float(r.mae) for r in rows
                if r.accepted and _is_filled_outcome(_outcome_str(r))
                and r.mae is not None and math.isfinite(float(r.mae))
            ]

        def _filled_mfe(rows: Sequence[TradeExperimentRow]) -> list[float]:
            return [
                float(r.mfe) for r in rows
                if r.accepted and _is_filled_outcome(_outcome_str(r))
                and r.mfe is not None and math.isfinite(float(r.mfe))
            ]

        mae_metrics = {
            "aggregate": _excursion_from_values(_filled_mae(all_rows)).to_dict(),
            "per_instrument": {k: v.mae.to_dict() for k, v in instrument_diags.items()},
            "by_outcome_aggregate": {
                o: _excursion_from_values([
                    float(r.mae) for r in all_rows
                    if r.accepted and _outcome_str(r) == o
                    and r.mae is not None and math.isfinite(float(r.mae))
                ]).to_dict()
                for o in (
                    SimulationOutcome.TP.value,
                    SimulationOutcome.SL.value,
                    SimulationOutcome.TIMEOUT.value,
                )
            },
        }
        mfe_metrics = {
            "aggregate": _excursion_from_values(_filled_mfe(all_rows)).to_dict(),
            "per_instrument": {k: v.mfe.to_dict() for k, v in instrument_diags.items()},
            "by_outcome_aggregate": {
                o: _excursion_from_values([
                    float(r.mfe) for r in all_rows
                    if r.accepted and _outcome_str(r) == o
                    and r.mfe is not None and math.isfinite(float(r.mfe))
                ]).to_dict()
                for o in (
                    SimulationOutcome.TP.value,
                    SimulationOutcome.SL.value,
                    SimulationOutcome.TIMEOUT.value,
                )
            },
        }
        duration_metrics = {
            "aggregate": _duration_from_values([
                float(r.duration_seconds) for r in all_rows
                if r.accepted and _is_filled_outcome(_outcome_str(r))
                and r.duration_seconds is not None and r.duration_seconds >= 0
            ], horizon=horizon_seconds).to_dict(),
            "per_instrument": {
                k: v.duration.to_dict() for k, v in instrument_diags.items()
            },
        }

        sig_chars = {
            "aggregate": _signal_characteristic_summary(all_rows),
            "per_instrument": {
                k: v.signal_characteristics for k, v in instrument_diags.items()
            },
        }

        recommendation = _build_recommendation(overall_od, overall_r, failure_modes)

        metadata = {
            "milestone": "5F",
            "title": "Structured Baseline Diagnostic Research",
            "generated_at_utc": datetime.now(tz=UTC).isoformat(),
            "analysis_version": "milestone-5f-v1",
            "horizon_seconds": horizon_seconds,
            "instruments": [a.instrument for a in analyses],
            "campaign_ids": [a.campaign_id for a in analyses],
        }

        # unique warnings preserving order
        seen: set[str] = set()
        uniq_warnings: list[str] = []
        for wmsg in warnings:
            if wmsg not in seen:
                seen.add(wmsg)
                uniq_warnings.append(wmsg)

        return BaselineDiagnosticReport(
            metadata=metadata,
            configuration_identity=cfg,
            source=source,
            sample_sizes=sample_sizes,
            overall_metrics=overall_od,
            instrument_metrics=instrument_diags,
            outcome_metrics=outcome_metrics,
            r_metrics=overall_r,
            mae_metrics=mae_metrics,
            mfe_metrics=mfe_metrics,
            duration_metrics=duration_metrics,
            direction_metrics=direction_metrics,
            context_metrics=context_metrics,
            signal_characteristic_metrics=sig_chars,
            failure_modes=failure_modes,
            warnings=tuple(uniq_warnings),
            limitations=_default_limitations(),
            recommendation=recommendation,
        )

    def analyze_from_campaign_results(
        self,
        results_list: Sequence[CampaignResults],
        *,
        configuration_identity: Mapping[str, Any] | None = None,
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
    ) -> BaselineDiagnosticReport:
        analyzer = CampaignBaselineAnalyzer()
        analyses: list[CampaignBaselineAnalysis] = []
        rows_by: dict[str, list[TradeExperimentRow]] = {}
        for res in results_list:
            analysis = analyzer.analyze(res)
            analyses.append(analysis)
            exp = res.experiment
            if exp is not None:
                rows_by[analysis.instrument] = list(exp.rows)
        return self.analyze(
            analyses,
            rows_by_instrument=rows_by,
            configuration_identity=configuration_identity,
            source="campaign_execution",
            horizon_seconds=horizon_seconds,
        )


def format_diagnostic_report(report: BaselineDiagnosticReport) -> str:
    lines: list[str] = [
        "# Milestone 5F — Structured Baseline Diagnostic Research",
        "",
        "## 1. Executive summary",
        "",
        f"- **source:** {report.source}",
        f"- **instruments:** {', '.join(report.metadata.get('instruments', []))}",
        f"- **aggregate signals:** {report.overall_metrics.total_signals}",
        f"- **accepted / filled:** {report.overall_metrics.accepted} / "
        f"{report.overall_metrics.filled}",
        f"- **TP / SL / TIMEOUT / NO_FILL:** "
        f"{report.overall_metrics.tp} / {report.overall_metrics.sl} / "
        f"{report.overall_metrics.timeout} / {report.overall_metrics.no_fill}",
        f"- **win rate (filled):** {report.overall_metrics.win_rate_filled}",
        f"- **average realized R:** {report.r_metrics.average_r}",
        f"- **total realized R:** {report.r_metrics.total_r}",
        f"- **sample class:** {report.overall_metrics.sample_class}",
        "",
        "### Top observed failure modes (descriptive)",
    ]
    for fm in report.failure_modes:
        lines.append(
            f"- **#{fm.rank} {fm.mode}:** frequency={fm.frequency} "
            f"share={fm.frequency_share} r_impact={fm.r_impact} — {fm.notes}"
        )

    lines += [
        "",
        "## 2. Integrity statement",
        "",
        "- StrategyEngine, sweep/MSB/displacement/FVG detection, and M15 context: **unchanged**.",
        "- TradeConstructor, entry/SL/TP geometry, RR, ATR buffer, risk-per-trade: **unchanged**.",
        "- Simulation fill / NO_FILL / TP / SL / TIMEOUT semantics and horizon: **unchanged**.",
        "- No filters, parameter sweeps, ML, or optimization were applied.",
        "- All metrics derive from campaign execution data (not hard-coded baselines).",
        "",
        "## 3. Configuration identity",
        "",
        "```json",
        json.dumps(dict(report.configuration_identity), indent=2, default=str),
        "```",
        "",
        "## 4. Data / sample coverage",
        "",
        f"- aggregate signals: {report.sample_sizes.get('aggregate_signals')}",
        f"- aggregate accepted: {report.sample_sizes.get('aggregate_accepted')}",
        f"- aggregate filled: {report.sample_sizes.get('aggregate_filled')}",
        f"- aggregate sample class: {report.sample_sizes.get('aggregate_sample_class')}",
        "",
        "### Per instrument",
    ]
    for inst, info in (report.sample_sizes.get("per_instrument") or {}).items():
        lines.append(
            f"- **{inst}:** signals={info.get('signals')} accepted={info.get('accepted')} "
            f"filled={info.get('filled')} ticks={info.get('ticks')} "
            f"class={info.get('sample_class')}"
        )

    lines += [
        "",
        "## 5. Overall outcome distribution",
        "",
        "Denominators are explicit:",
        "- `fill_rate = filled / accepted`",
        "- `win_rate_filled = TP / filled`",
        "- `win_rate_all_accepted = TP / accepted`",
        "",
        f"- total_signals: {report.overall_metrics.total_signals}",
        f"- accepted: {report.overall_metrics.accepted}",
        f"- filled (TP+SL+TIMEOUT): {report.overall_metrics.filled}",
        f"- TP / SL / TIMEOUT / NO_FILL: "
        f"{report.overall_metrics.tp} / {report.overall_metrics.sl} / "
        f"{report.overall_metrics.timeout} / {report.overall_metrics.no_fill}",
        f"- fill_rate: {report.overall_metrics.fill_rate}",
        f"- win_rate_filled: {report.overall_metrics.win_rate_filled}",
        f"- win_rate_all_accepted: {report.overall_metrics.win_rate_all_accepted}",
        "",
        "## 6. Per-instrument comparison",
        "",
        "**Warning:** instrument behavior may differ; do not pool blindly.",
        "",
    ]
    for inst, diag in report.instrument_metrics.items():
        o = diag.outcome
        lines += [
            f"### {inst}",
            f"- campaign_id: `{diag.campaign_id}`",
            f"- ticks: {diag.ticks_processed}",
            f"- period UTC: {diag.start_utc} → {diag.end_utc}",
            f"- signals / accepted / filled: {o.total_signals} / {o.accepted} / {o.filled}",
            f"- TP / SL / TIMEOUT / NO_FILL: {o.tp} / {o.sl} / {o.timeout} / {o.no_fill}",
            f"- fill_rate: {o.fill_rate}",
            f"- win_rate_filled: {o.win_rate_filled}",
            f"- average R / total R: {diag.r_metrics.average_r} / {diag.r_metrics.total_r}",
            f"- avg MAE / MFE: {diag.mae.mean} / {diag.mfe.mean}",
            f"- avg duration (s): {diag.duration.mean}",
            "",
        ]

    lines += [
        "## 7. R distribution",
        "",
        "Realized R follows existing simulation semantics (typically TP/SL only; "
        "TIMEOUT has no exit_price).",
        "",
        f"- count (with realized_r): {report.r_metrics.count}",
        f"- total R: {report.r_metrics.total_r}",
        f"- average / median R: {report.r_metrics.average_r} / {report.r_metrics.median_r}",
        f"- min / max R: {report.r_metrics.min_r} / {report.r_metrics.max_r}",
        f"- std R: {report.r_metrics.std_r}",
        f"- positive / negative / zero: "
        f"{report.r_metrics.positive_r_count} / "
        f"{report.r_metrics.negative_r_count} / "
        f"{report.r_metrics.zero_r_count}",
        "",
        "### By outcome",
    ]
    for o, stats in report.r_metrics.by_outcome.items():
        lines.append(f"- **{o}:** {stats}")

    lines += [
        "",
        "## 8. MAE / MFE analysis",
        "",
        f"- aggregate MAE: {report.mae_metrics.get('aggregate')}",
        f"- aggregate MFE: {report.mfe_metrics.get('aggregate')}",
        "",
        "### By outcome (aggregate)",
        f"- MAE by outcome: {report.mae_metrics.get('by_outcome_aggregate')}",
        f"- MFE by outcome: {report.mfe_metrics.get('by_outcome_aggregate')}",
        "",
        "TIMEOUT MFE is descriptive only — positive MFE does **not** imply the trade "
        "should have been a win.",
        "",
        "## 9. Duration analysis",
        "",
        f"- aggregate: {report.duration_metrics.get('aggregate')}",
        "",
        "TIMEOUT trades near the simulation horizon (900s) indicate unfinished paths, "
        "not a mandate to change the horizon in this milestone.",
        "",
        "## 10. Direction analysis",
        "",
    ]
    if not report.direction_metrics:
        lines.append("- Direction metadata unavailable or no rows supplied.")
    else:
        for k, c in report.direction_metrics.items():
            lines.append(
                f"- **{k}:** signals={c.signals} filled={c.filled} "
                f"TP/SL/TO/NF={c.tp}/{c.sl}/{c.timeout}/{c.no_fill} "
                f"WR={c.win_rate_filled} avgR={c.average_r} totalR={c.total_r} "
                f"class={c.sample_class}"
            )
            if c.warning:
                lines.append(f"  - warning: {c.warning}")

    lines += [
        "",
        "## 11. M15 context analysis",
        "",
        "Uses existing `m15_context.directional_bias` only — no new classifications.",
        "",
    ]
    if not report.context_metrics or set(report.context_metrics) == {"unknown"}:
        lines.append("- M15 context unavailable or all unknown.")
    else:
        for k, c in report.context_metrics.items():
            lines.append(
                f"- **{k}:** signals={c.signals} filled={c.filled} "
                f"TP/SL/TO/NF={c.tp}/{c.sl}/{c.timeout}/{c.no_fill} "
                f"WR={c.win_rate_filled} avgR={c.average_r} totalR={c.total_r} "
                f"class={c.sample_class}"
            )
            if c.warning:
                lines.append(f"  - warning: {c.warning}")

    lines += [
        "",
        "## 12. Signal characteristic analysis",
        "",
        "Only fields already recorded by StrategyEngine / TradeCandidate are summarized.",
        "",
        "```json",
        json.dumps(dict(report.signal_characteristic_metrics), indent=2, default=str),
        "```",
        "",
        "## 13. Failure-mode analysis",
        "",
        "Ranking considers frequency and R impact separately. A mode can be frequent "
        "with little realized-R impact (e.g. NO_FILL) or less frequent with large impact (SL).",
        "",
    ]
    for fm in report.failure_modes:
        lines.append(
            f"{fm.rank}. **{fm.mode}** — freq={fm.frequency}, "
            f"share={fm.frequency_share}, r_impact={fm.r_impact}, "
            f"instruments={list(fm.instruments)}"
        )
        lines.append(f"   - {fm.notes}")

    lines += [
        "",
        "## 14. Sample-size warnings",
        "",
        "Thresholds (research interpretation only):",
        f"- <{SAMPLE_VERY_SMALL}: very_small",
        f"- {SAMPLE_VERY_SMALL}–{SAMPLE_SMALL - 1}: small",
        f"- {SAMPLE_SMALL}–{SAMPLE_PRELIMINARY - 1}: preliminary",
        f"- ≥{SAMPLE_PRELIMINARY}: stronger",
        "",
    ]
    if report.warnings:
        for wmsg in report.warnings:
            lines.append(f"- {wmsg}")
    else:
        lines.append("- No sample-size warnings.")

    lines += [
        "",
        "## 15. Limitations",
        "",
    ]
    for lim in report.limitations:
        lines.append(f"- {lim}")

    lines += [
        "",
        "## 16. Research interpretation",
        "",
        "Observations above are descriptive. Associations in small samples are "
        "hypotheses for further testing, not evidence of causality and not a "
        "license to change the frozen baseline.",
        "",
        "## 17. Recommendation for next milestone",
        "",
        report.recommendation,
        "",
        "---",
        f"*Generated at {report.metadata.get('generated_at_utc')} · "
        f"analysis_version={report.metadata.get('analysis_version')}*",
    ]
    return "\n".join(lines)


def write_diagnostic_artifacts(
    report: BaselineDiagnosticReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "diagnostic.json"
    md_path = out / "report.md"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2, default=str)
        f.write("\n")
    md_path.write_text(format_diagnostic_report(report), encoding="utf-8")
    return json_path, md_path


def run_baseline_diagnostics(
    *,
    data_root: Path | str,
    output: Path | str,
    instruments: Sequence[str],
    start_epoch: int | None = None,
    end_epoch: int | None = None,
    equity: float = 10_000.0,
    configuration_identity: Mapping[str, Any] | None = None,
) -> BaselineDiagnosticReport:
    from smb.research.campaign import CampaignConfig, CampaignRunner
    from smb.simulation.models import SimulationConfig
    from smb.strategy.models import StrategyConfig
    from smb.trade.models import TradeConfig

    data_root = Path(data_root)
    output = Path(output)
    runner = CampaignRunner()
    results_list = []
    for inst in instruments:
        cfg = CampaignConfig(
            instrument=inst,
            start_epoch=start_epoch,
            end_epoch=end_epoch,
            strategy=StrategyConfig(),
            trade=TradeConfig(),
            simulation=SimulationConfig(),
            risk_equity=equity,
            output_dir=str(output / inst),
            campaign_id=f"milestone-5f-{inst}",
        )
        results_list.append(runner.run(cfg, data_root=data_root))

    cfg_id = dict(configuration_identity or {})
    if not cfg_id:
        tc = TradeConfig()
        sc = SimulationConfig()
        cfg_id = {
            "strategy": {
                "swing_x": StrategyConfig().swing_x,
                "msb_window_bars": StrategyConfig().msb_window_bars,
                "displacement_body_range_ratio": StrategyConfig().displacement_body_range_ratio,
                "displacement_body_atr_ratio": StrategyConfig().displacement_body_atr_ratio,
                "atr_period": StrategyConfig().atr_period,
            },
            "trade": {
                "risk_per_trade": tc.risk_per_trade,
                "target_rr": tc.target_rr,
                "minimum_rr": tc.minimum_rr,
                "sl_atr_buffer": tc.sl_atr_buffer,
            },
            "simulation": {"max_duration_seconds": sc.max_duration_seconds},
        }

    report = BaselineDiagnosticAnalyzer().analyze_from_campaign_results(
        results_list,
        configuration_identity=cfg_id,
    )
    write_diagnostic_artifacts(report, output)
    return report
