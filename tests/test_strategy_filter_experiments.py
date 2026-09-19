"""Tests for Milestone 6C — Controlled Strategy Filter Experiments."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smb.research.experiment import ExperimentResult, ExperimentSummary, TradeExperimentRow
from smb.research.strategy_filter_experiments import (
    DEFAULT_BASELINE_HORIZON_SECONDS,
    CohortStatus,
    DisplacementFVGQualityFilterConfig,
    ExperimentFamily,
    FilterDecisionKind,
    FilterExperimentConfig,
    M15ContextFilterConfig,
    SessionRegimeFilterConfig,
    TrendDirectionFilterConfig,
    analyze_filter_experiment,
    analyze_instrument_filter,
    build_filter_config_from_args,
    compute_cohort_metrics,
    evaluate_displacement_fvg,
    evaluate_filter,
    evaluate_m15_context,
    evaluate_session_regime,
    evaluate_trend_direction,
    format_filter_experiment_report,
    leakage_review_notes,
    parse_experiment_family,
    run_filter_experiment_on_results,
    write_filter_experiment_artifacts,
)
from smb.simulation.models import SimulationOutcome
from smb.strategy.models import (
    Direction,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategySignal,
    SwingPoint,
)
from smb.trade.models import RejectionReason

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _swing(kind: str = "low") -> SwingPoint:
    return SwingPoint(
        kind=kind,  # type: ignore[arg-type]
        price=100.0,
        candle_start_epoch=1_699_999_000,
        candle_end_epoch=1_699_999_060,
        index=0,
        confirmed_at_epoch=1_699_999_180,
    )


def _disp(
    *,
    direction: Direction = Direction.LONG,
    body_range_ratio: float = 0.70,
    body_atr_ratio: float = 1.0,
) -> Displacement:
    return Displacement(
        direction=direction,
        candle_start_epoch=1_700_000_000,
        candle_end_epoch=1_700_000_060,
        open=100.0,
        high=110.0,
        low=99.0,
        close=109.0,
        body=9.0,
        range_=11.0,
        body_range_ratio=body_range_ratio,
        body_atr_ratio=body_atr_ratio,
        atr=9.0,
    )


def _fvg(
    *,
    direction: Direction = Direction.LONG,
    size_atr_ratio: float | None = 0.5,
) -> FairValueGap:
    return FairValueGap(
        direction=direction,
        gap_low=105.0,
        gap_high=108.0,
        size=3.0,
        size_atr_ratio=size_atr_ratio,
        candle1_start_epoch=1_700_000_000,
        candle2_start_epoch=1_700_000_060,
        candle3_start_epoch=1_700_000_120,
        candle3_end_epoch=1_700_000_180,
    )


def _m15(
    *,
    bias: str | None = "bullish",
    close: float | None = 110.0,
    high: float | None = 120.0,
    low: float | None = 100.0,
) -> M15Context:
    return M15Context(
        last_m15_start_epoch=1_699_999_100,
        last_m15_end_epoch=1_700_000_000,
        last_m15_close=close,
        recent_high=high,
        recent_low=low,
        directional_bias=bias,  # type: ignore[arg-type]
    )


def _sweep(direction: Direction = Direction.LONG) -> LiquiditySweep:
    return LiquiditySweep(
        direction=direction,
        swept_level=100.0,
        sweep_candle_start_epoch=1_700_000_000,
        sweep_candle_end_epoch=1_700_000_060,
        sweep_candle_low=99.0,
        sweep_candle_high=101.0,
        sweep_candle_close=100.5,
        swing=_swing("low" if direction is Direction.LONG else "high"),
    )


def _msb(direction: Direction = Direction.LONG) -> MarketStructureBreak:
    return MarketStructureBreak(
        direction=direction,
        broken_level=105.0,
        msb_candle_start_epoch=1_700_000_060,
        msb_candle_end_epoch=1_700_000_120,
        msb_candle_close=106.0,
        bars_after_sweep=1,
        structure_swing=_swing("high" if direction is Direction.LONG else "low"),
    )


def _signal(
    *,
    instrument: str = "volatility_75_1s",
    direction: Direction = Direction.LONG,
    epoch: int = 1_700_000_180,
    m15: M15Context | None = None,
    disp: Displacement | None = None,
    fvg: FairValueGap | None = None,
) -> StrategySignal:
    d = direction
    return StrategySignal(
        instrument=instrument,
        direction=d,
        signal_epoch=epoch,
        timeframe_context="M15+M1",
        sweep=_sweep(d),
        msb=_msb(d),
        displacement=disp or _disp(direction=d),
        fvg=fvg or _fvg(direction=d),
        m15_context=m15 if m15 is not None else _m15(),
    )


def _row(
    *,
    instrument: str = "volatility_75_1s",
    epoch: int = 1_700_000_180,
    direction: str = "long",
    accepted: bool = True,
    outcome: SimulationOutcome | None = SimulationOutcome.TP,
    realized_r: float | None = 1.5,
    mfe: float | None = 12.0,
    mae: float | None = 3.0,
    duration: int | None = 200,
    rejection_reason: RejectionReason | None = None,
    signal: StrategySignal | None = None,
) -> TradeExperimentRow:
    dir_enum = Direction.LONG if direction == "long" else Direction.SHORT
    sig = signal or _signal(
        instrument=instrument,
        direction=dir_enum,
        epoch=epoch,
    )
    return TradeExperimentRow(
        instrument=instrument,
        signal_epoch=epoch,
        direction=direction,
        accepted=accepted,
        rejection_reason=rejection_reason,
        entry_price=100.0 if accepted else None,
        stop_loss=90.0 if accepted else None,
        take_profit=120.0 if accepted else None,
        risk_reward=2.0 if accepted else None,
        risk_amount=100.0 if accepted else None,
        outcome=outcome if accepted else None,
        entry_time=epoch + 10 if accepted else None,
        exit_time=epoch + 10 + (duration or 0) if accepted else None,
        duration_seconds=duration if accepted else None,
        realized_r=realized_r if accepted else None,
        mfe=mfe if accepted else None,
        mae=mae if accepted else None,
        signal=sig,
        candidate=SimpleNamespace() if accepted else None,  # type: ignore[arg-type]
        simulation=None,
        metrics=None,
    )


def _experiment_result(
    rows: list[TradeExperimentRow],
    instrument: str = "volatility_75_1s",
) -> ExperimentResult:
    accepted = sum(1 for r in rows if r.accepted)
    rejected = len(rows) - accepted
    outcomes: dict[str, int] = {}
    for r in rows:
        if r.outcome is not None:
            key = r.outcome.value
            outcomes[key] = outcomes.get(key, 0) + 1
    summary = ExperimentSummary(
        instrument=instrument,
        start_epoch=1_700_000_000,
        end_epoch=1_700_100_000,
        ticks_processed=1000,
        m1_candles=100,
        m15_candles=10,
        signals=len(rows),
        candidates_accepted=accepted,
        candidates_rejected=rejected,
        outcomes=outcomes,
        win_rate=None,
        average_r=None,
        total_r=None,
        average_duration_seconds=None,
        average_mae=None,
        average_mfe=None,
    )
    return ExperimentResult(
        config=SimpleNamespace(instrument=instrument),  # type: ignore[arg-type]
        summary=summary,
        rows=tuple(rows),
        simulations=(),
        metrics=(),
        validation=None,
    )


# ---------------------------------------------------------------------------
# parse / config validation
# ---------------------------------------------------------------------------


def test_parse_experiment_family_valid() -> None:
    assert parse_experiment_family("trend_direction") is ExperimentFamily.TREND_DIRECTION
    assert parse_experiment_family("M15-Context") is ExperimentFamily.M15_CONTEXT
    assert (
        parse_experiment_family("displacement_fvg_quality")
        is ExperimentFamily.DISPLACEMENT_FVG_QUALITY
    )
    assert parse_experiment_family("session_regime") is ExperimentFamily.SESSION_REGIME


def test_parse_experiment_family_invalid() -> None:
    with pytest.raises(ValueError, match="unknown experiment"):
        parse_experiment_family("optimize_everything")


def test_invalid_m15_config_nonfinite() -> None:
    with pytest.raises(ValueError, match="finite"):
        M15ContextFilterConfig(min_close_range_position=float("nan"))


def test_invalid_displacement_config_no_thresholds() -> None:
    with pytest.raises(ValueError, match="at least one"):
        DisplacementFVGQualityFilterConfig(
            min_body_range_ratio=None,
            min_body_atr_ratio=None,
            min_fvg_size_atr_ratio=None,
        )


def test_invalid_session_bucket_range() -> None:
    with pytest.raises(ValueError, match="start < end"):
        SessionRegimeFilterConfig(buckets=(("bad", 10, 5),))


def test_invalid_session_retain_unknown_bucket() -> None:
    with pytest.raises(ValueError, match="not a defined bucket"):
        SessionRegimeFilterConfig(retain_buckets=("mars",))


def test_filter_experiment_config_rejects_bad_horizon() -> None:
    with pytest.raises(ValueError, match="horizon_seconds"):
        FilterExperimentConfig(
            family=ExperimentFamily.TREND_DIRECTION,
            horizon_seconds=0,
        )


# ---------------------------------------------------------------------------
# Trend direction filter
# ---------------------------------------------------------------------------


def test_trend_direction_long_aligned() -> None:
    row = _row(
        direction="long",
        signal=_signal(direction=Direction.LONG, m15=_m15(bias="bullish")),
    )
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.RETAIN
    assert "aligned" in d.reason


def test_trend_direction_long_countertrend() -> None:
    row = _row(
        direction="long",
        signal=_signal(direction=Direction.LONG, m15=_m15(bias="bearish")),
    )
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.REJECT
    assert "countertrend" in d.reason


def test_trend_direction_short_aligned() -> None:
    row = _row(
        direction="short",
        signal=_signal(direction=Direction.SHORT, m15=_m15(bias="bearish")),
    )
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.RETAIN


def test_trend_direction_short_countertrend() -> None:
    row = _row(
        direction="short",
        signal=_signal(direction=Direction.SHORT, m15=_m15(bias="bullish")),
    )
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.REJECT


def test_trend_direction_neutral_unavailable() -> None:
    row = _row(signal=_signal(m15=_m15(bias="neutral")))
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.UNAVAILABLE


def test_trend_direction_missing_bias_unavailable() -> None:
    row = _row(signal=_signal(m15=_m15(bias=None)))
    d = evaluate_trend_direction(row, TrendDirectionFilterConfig())
    assert d.kind is FilterDecisionKind.UNAVAILABLE
    assert "missing" in d.reason


def test_trend_direction_neutral_as_reject() -> None:
    row = _row(signal=_signal(m15=_m15(bias="neutral")))
    d = evaluate_trend_direction(
        row, TrendDirectionFilterConfig(treat_neutral_as="reject")
    )
    assert d.kind is FilterDecisionKind.REJECT


# ---------------------------------------------------------------------------
# M15 context filter
# ---------------------------------------------------------------------------


def test_m15_context_long_retained() -> None:
    # close near top of range, bullish bias
    row = _row(
        direction="long",
        signal=_signal(
            direction=Direction.LONG,
            m15=_m15(bias="bullish", close=115.0, high=120.0, low=100.0),
        ),
    )
    d = evaluate_m15_context(row, M15ContextFilterConfig(min_close_range_position=0.5))
    assert d.kind is FilterDecisionKind.RETAIN


def test_m15_context_long_rejected_low_close() -> None:
    row = _row(
        direction="long",
        signal=_signal(
            direction=Direction.LONG,
            m15=_m15(bias="bullish", close=105.0, high=120.0, low=100.0),
        ),
    )
    d = evaluate_m15_context(row, M15ContextFilterConfig(min_close_range_position=0.5))
    assert d.kind is FilterDecisionKind.REJECT


def test_m15_context_short_retained() -> None:
    row = _row(
        direction="short",
        signal=_signal(
            direction=Direction.SHORT,
            m15=_m15(bias="bearish", close=105.0, high=120.0, low=100.0),
        ),
    )
    d = evaluate_m15_context(row, M15ContextFilterConfig(min_close_range_position=0.5))
    assert d.kind is FilterDecisionKind.RETAIN


def test_m15_context_missing() -> None:
    row = _row(
        signal=_signal(
            m15=M15Context(None, None, None, None, None, None),
        )
    )
    d = evaluate_m15_context(row, M15ContextFilterConfig())
    assert d.kind is FilterDecisionKind.UNAVAILABLE


def test_m15_context_countertrend_rejected() -> None:
    row = _row(
        direction="long",
        signal=_signal(
            direction=Direction.LONG,
            m15=_m15(bias="bearish", close=115.0, high=120.0, low=100.0),
        ),
    )
    d = evaluate_m15_context(row, M15ContextFilterConfig())
    assert d.kind is FilterDecisionKind.REJECT
    assert "countertrend" in d.reason


# ---------------------------------------------------------------------------
# Displacement / FVG quality
# ---------------------------------------------------------------------------


def test_displacement_fvg_retained() -> None:
    row = _row(
        signal=_signal(
            disp=_disp(body_range_ratio=0.75),
            fvg=_fvg(size_atr_ratio=0.6),
        )
    )
    cfg = DisplacementFVGQualityFilterConfig(min_body_range_ratio=0.60)
    d = evaluate_displacement_fvg(row, cfg)
    assert d.kind is FilterDecisionKind.RETAIN


def test_displacement_fvg_rejected_low_body_ratio() -> None:
    row = _row(signal=_signal(disp=_disp(body_range_ratio=0.40)))
    cfg = DisplacementFVGQualityFilterConfig(min_body_range_ratio=0.60)
    d = evaluate_displacement_fvg(row, cfg)
    assert d.kind is FilterDecisionKind.REJECT
    assert "body_range_ratio" in d.reason


def test_displacement_fvg_missing_metric_unavailable() -> None:
    row = _row(signal=_signal(fvg=_fvg(size_atr_ratio=None)))
    cfg = DisplacementFVGQualityFilterConfig(
        min_body_range_ratio=None,
        min_fvg_size_atr_ratio=0.3,
    )
    d = evaluate_displacement_fvg(row, cfg)
    assert d.kind is FilterDecisionKind.UNAVAILABLE
    assert "missing" in d.reason


# ---------------------------------------------------------------------------
# Session regime
# ---------------------------------------------------------------------------


def test_session_regime_segmentation_all_retained() -> None:
    # epoch 1_700_000_180 -> hour from (1700000180 % 86400) // 3600
    epoch = 1_700_000_180
    row = _row(epoch=epoch)
    d = evaluate_session_regime(row, SessionRegimeFilterConfig())
    assert d.kind is FilterDecisionKind.RETAIN
    assert d.segment is not None


def test_session_regime_retain_list() -> None:
    # Force asia hour: epoch such that hour is 3 UTC
    # hour = (epoch % 86400) // 3600 == 3 → epoch % 86400 == 3*3600 = 10800
    epoch = 1_700_000_000 - (1_700_000_000 % 86400) + 10800
    row = _row(epoch=epoch)
    cfg = SessionRegimeFilterConfig(retain_buckets=("london",))
    d = evaluate_session_regime(row, cfg)
    assert d.kind is FilterDecisionKind.REJECT
    assert d.segment == "asia"

    cfg2 = SessionRegimeFilterConfig(retain_buckets=("asia",))
    d2 = evaluate_session_regime(row, cfg2)
    assert d2.kind is FilterDecisionKind.RETAIN
    assert d2.segment == "asia"


def test_session_uses_utc_consistently() -> None:
    # Two epochs 12 hours apart should land in different default buckets
    base = 1_700_000_000 - (1_700_000_000 % 86400)
    row_asia = _row(epoch=base + 2 * 3600)  # 02:00 UTC
    row_ny = _row(epoch=base + 18 * 3600)  # 18:00 UTC
    cfg = SessionRegimeFilterConfig()
    d1 = evaluate_session_regime(row_asia, cfg)
    d2 = evaluate_session_regime(row_ny, cfg)
    assert d1.segment == "asia"
    assert d2.segment == "new_york"


# ---------------------------------------------------------------------------
# No post-entry leakage
# ---------------------------------------------------------------------------


def test_filter_ignores_post_entry_fields() -> None:
    """Decision must not depend on outcome / realized_r / MAE / MFE."""
    sig = _signal(m15=_m15(bias="bullish"))
    row_tp = _row(
        direction="long",
        outcome=SimulationOutcome.TP,
        realized_r=5.0,
        signal=sig,
    )
    row_sl = _row(
        direction="long",
        outcome=SimulationOutcome.SL,
        realized_r=-1.0,
        mae=50.0,
        signal=sig,
    )
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    assert evaluate_filter(row_tp, cfg).kind == evaluate_filter(row_sl, cfg).kind
    assert evaluate_filter(row_tp, cfg).reason == evaluate_filter(row_sl, cfg).reason


def test_leakage_review_notes_present() -> None:
    notes = leakage_review_notes()
    assert any(
        "signal_epoch" in n or "signal-time" in n.lower() or "StrategySignal" in n
        for n in notes
    )
    assert any("post-entry" in n.lower() or "MAE" in n for n in notes)


# ---------------------------------------------------------------------------
# Cohort metrics & denominator reconciliation
# ---------------------------------------------------------------------------


def test_cohort_baseline_denominator_reconciliation() -> None:
    rows = [
        _row(epoch=1, outcome=SimulationOutcome.TP, realized_r=1.0),
        _row(epoch=2, outcome=SimulationOutcome.SL, realized_r=-1.0),
        _row(
            epoch=3,
            outcome=SimulationOutcome.NO_FILL,
            realized_r=None,
            mfe=None,
            mae=None,
            duration=None,
        ),
        _row(
            epoch=4,
            accepted=False,
            outcome=None,
            realized_r=None,
            rejection_reason=RejectionReason.INSUFFICIENT_RR,
        ),
    ]
    m = compute_cohort_metrics(label="baseline", rows=rows, cohort_kind="baseline")
    assert m.signals == 4
    assert m.candidates_accepted == 3
    assert m.candidates_rejected == 1
    assert m.no_fill == 1
    assert m.filled == 2
    assert m.tp == 1
    assert m.sl == 1
    assert m.win_rate == pytest.approx(0.5)
    assert m.total_r == pytest.approx(0.0)
    assert "INSUFFICIENT_RR" in m.rejection_reasons or "insufficient_rr" in m.rejection_reasons


def test_no_fill_excluded_from_distributions() -> None:
    rows = [
        _row(epoch=1, outcome=SimulationOutcome.TP, realized_r=1.0, mae=2.0, mfe=5.0),
        _row(
            epoch=2,
            outcome=SimulationOutcome.NO_FILL,
            realized_r=None,
            mae=None,
            mfe=None,
            duration=None,
        ),
    ]
    m = compute_cohort_metrics(label="baseline", rows=rows, cohort_kind="baseline")
    assert m.filled == 1
    assert m.no_fill == 1
    assert m.mae is not None
    assert m.mae.count == 1
    assert m.win_rate == pytest.approx(1.0)


def test_zero_signal_instrument() -> None:
    m = compute_cohort_metrics(label="baseline", rows=[], cohort_kind="baseline")
    assert m.status is CohortStatus.ZERO_SIGNAL
    assert m.signals == 0
    assert m.win_rate is None
    assert m.total_r is None


def test_empty_retained_cohort() -> None:
    m = compute_cohort_metrics(
        label="filter_retained",
        rows=[],
        baseline_signal_count=10,
        cohort_kind="retained",
    )
    assert m.status is CohortStatus.EMPTY
    assert m.coverage_pct is None or m.coverage_pct == 0.0 or m.signals == 0


def test_instrument_filter_denominator_ok() -> None:
    rows = [
        _row(
            epoch=1,
            direction="long",
            signal=_signal(direction=Direction.LONG, m15=_m15(bias="bullish")),
        ),
        _row(
            epoch=2,
            direction="long",
            signal=_signal(direction=Direction.LONG, m15=_m15(bias="bearish")),
        ),
        _row(
            epoch=3,
            direction="long",
            signal=_signal(direction=Direction.LONG, m15=_m15(bias="neutral")),
        ),
    ]
    exp = _experiment_result(rows)
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    ir = analyze_instrument_filter(exp, cfg)
    assert ir.denominator_ok
    assert (
        ir.baseline.signals
        == ir.retained.signals + ir.rejected.signals + ir.unavailable.signals
    )
    assert ir.retained.signals == 1
    assert ir.rejected.signals == 1
    assert ir.unavailable.signals == 1


def test_both_directions_in_cohorts() -> None:
    rows = [
        _row(
            epoch=1,
            direction="long",
            outcome=SimulationOutcome.TP,
            signal=_signal(direction=Direction.LONG, m15=_m15(bias="bullish")),
        ),
        _row(
            epoch=2,
            direction="short",
            outcome=SimulationOutcome.SL,
            realized_r=-1.0,
            signal=_signal(direction=Direction.SHORT, m15=_m15(bias="bearish")),
        ),
    ]
    exp = _experiment_result(rows)
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    ir = analyze_instrument_filter(exp, cfg)
    assert ir.retained.signals == 2
    assert ir.retained.tp == 1
    assert ir.retained.sl == 1


def test_sparse_segment_flagged() -> None:
    base = 1_700_000_000 - (1_700_000_000 % 86400)
    rows = [_row(epoch=base + 2 * 3600)]  # asia only, n=1
    exp = _experiment_result(rows)
    cfg = FilterExperimentConfig(family=ExperimentFamily.SESSION_REGIME)
    ir = analyze_instrument_filter(exp, cfg)
    assert "asia" in ir.segments
    assert ir.segments["asia"].status in (
        CohortStatus.LOW_SAMPLE,
        CohortStatus.COMPLETE,
    )
    assert any("sparse" in lim or "low-sample" in lim for lim in ir.limitations)


# ---------------------------------------------------------------------------
# Full report / artifacts / reproducibility
# ---------------------------------------------------------------------------


def test_analyze_filter_experiment_multi_instrument() -> None:
    rows_v = [
        _row(
            instrument="volatility_75_1s",
            epoch=1,
            signal=_signal(m15=_m15(bias="bullish")),
        )
    ]
    rows_s = [
        _row(
            instrument="step_index",
            epoch=2,
            direction="short",
            signal=_signal(direction=Direction.SHORT, m15=_m15(bias="bullish")),
        )
    ]
    results = {
        "volatility_75_1s": _experiment_result(rows_v, "volatility_75_1s"),
        "step_index": _experiment_result(rows_s, "step_index"),
    }
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    report = analyze_filter_experiment(results, cfg)
    assert report.family == "trend_direction"
    assert set(report.results.keys()) == {"volatility_75_1s", "step_index"}
    assert report.results["volatility_75_1s"].retained.signals == 1
    assert report.results["step_index"].rejected.signals == 1
    disc = report.research_disclaimer.lower()
    assert "descriptive" in disc or "observational" in disc


def test_json_and_markdown_artifacts(tmp_path: Path) -> None:
    rows = [
        _row(epoch=1, signal=_signal(m15=_m15(bias="bullish"))),
        _row(
            epoch=2,
            signal=_signal(m15=_m15(bias="bearish")),
            outcome=SimulationOutcome.SL,
            realized_r=-1.0,
        ),
    ]
    results = {"volatility_75_1s": _experiment_result(rows)}
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    report = run_filter_experiment_on_results(results, cfg)
    json_path, md_path = write_filter_experiment_artifacts(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["family"] == "trend_direction"
    assert data["study_version"]
    assert "volatility_75_1s" in data["results"]
    md = md_path.read_text(encoding="utf-8")
    assert "Baseline cohort" in md or "baseline" in md.lower()
    assert "disclaimer" in md.lower() or "descriptive" in md.lower()


def test_format_report_includes_limitations() -> None:
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    report = analyze_filter_experiment({}, cfg)
    text = format_filter_experiment_report(report)
    assert "limitations" in text.lower() or "disclaimer" in text.lower()
    assert "trend_direction" in text


def test_reproducibility_same_inputs_same_output() -> None:
    rows = [
        _row(epoch=i, signal=_signal(m15=_m15(bias="bullish" if i % 2 == 0 else "bearish")))
        for i in range(6)
    ]
    results = {"volatility_75_1s": _experiment_result(rows)}
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    r1 = analyze_filter_experiment(results, cfg).to_dict()
    r2 = analyze_filter_experiment(results, cfg).to_dict()
    assert r1 == r2


def test_build_filter_config_from_args() -> None:
    cfg = build_filter_config_from_args(
        ExperimentFamily.DISPLACEMENT_FVG_QUALITY,
        min_body_range_ratio=0.55,
        horizon_seconds=900,
    )
    assert cfg.family is ExperimentFamily.DISPLACEMENT_FVG_QUALITY
    assert cfg.displacement_fvg.min_body_range_ratio == 0.55
    assert cfg.horizon_seconds == DEFAULT_BASELINE_HORIZON_SECONDS


def test_evaluate_filter_dispatch() -> None:
    row = _row(signal=_signal(m15=_m15(bias="bullish")))
    for family in ExperimentFamily:
        cfg = FilterExperimentConfig(family=family)
        d = evaluate_filter(row, cfg)
        assert d.kind in (
            FilterDecisionKind.RETAIN,
            FilterDecisionKind.REJECT,
            FilterDecisionKind.UNAVAILABLE,
        )


def test_baseline_preservation_signal_counts() -> None:
    """Filter never changes underlying experiment rows / baseline counts."""
    rows = [
        _row(epoch=1, signal=_signal(m15=_m15(bias="bullish"))),
        _row(epoch=2, signal=_signal(m15=_m15(bias="bearish"))),
    ]
    exp = _experiment_result(rows)
    before = exp.summary.signals
    cfg = FilterExperimentConfig(family=ExperimentFamily.TREND_DIRECTION)
    ir = analyze_instrument_filter(exp, cfg)
    assert ir.baseline.signals == before
    assert exp.summary.signals == before  # unchanged


def test_low_sample_status() -> None:
    rows = [_row(epoch=1, outcome=SimulationOutcome.TP)]
    m = compute_cohort_metrics(label="x", rows=rows, cohort_kind="baseline")
    assert m.status is CohortStatus.LOW_SAMPLE


def test_drawdown_and_median_r() -> None:
    rows = [
        _row(epoch=1, outcome=SimulationOutcome.TP, realized_r=1.0),
        _row(epoch=2, outcome=SimulationOutcome.SL, realized_r=-1.0),
        _row(epoch=3, outcome=SimulationOutcome.TP, realized_r=2.0),
    ]
    m = compute_cohort_metrics(label="b", rows=rows, cohort_kind="baseline")
    assert m.median_r is not None
    assert m.maximum_drawdown_r is not None
    assert m.maximum_drawdown_r >= 0.0
    assert m.total_r == pytest.approx(2.0)


def test_cli_help_mentions_experiment(tmp_path: Path) -> None:
    from smb.research.__main__ import main

    with pytest.raises(SystemExit) as ei:
        main(["run-strategy-filter-experiment", "--help"])
    assert ei.value.code == 0


def test_cli_requires_experiment() -> None:
    from smb.research.__main__ import main

    with pytest.raises(SystemExit) as ei:
        main(["run-strategy-filter-experiment", "--output", "/tmp/out"])
    assert ei.value.code != 0


def test_cli_invalid_experiment(tmp_path: Path) -> None:
    from smb.research.__main__ import main

    code = main(
        [
            "run-strategy-filter-experiment",
            "--experiment",
            "not_a_real_filter",
            "--output",
            str(tmp_path),
        ]
    )
    assert code == 2
