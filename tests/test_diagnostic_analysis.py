"""Milestone 5F — structured baseline diagnostic research tests.

Strategy / trade / simulation remain frozen. Diagnostics must derive from
supplied campaign analysis data (no hard-coded 5D metrics).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
    SegmentMetrics,
)
from smb.research.diagnostic_analysis import (
    BaselineDiagnosticAnalyzer,
    format_diagnostic_report,
    sample_size_class,
    sample_size_warning,
    write_diagnostic_artifacts,
)
from smb.research.experiment import TradeExperimentRow
from smb.simulation.models import SimulationOutcome


def _seg(
    *,
    label: str = "overall",
    signals: int = 0,
    accepted: int | None = None,
    filled: int = 0,
    wins: int = 0,
    losses: int = 0,
    timeouts: int = 0,
    no_fills: int = 0,
    win_rate: float | None = None,
    average_r: float | None = None,
    total_r: float | None = None,
) -> SegmentMetrics:
    if accepted is None:
        accepted = signals
    return SegmentMetrics(
        label=label,
        signals=signals,
        accepted=accepted,
        rejected=signals - accepted,
        filled=filled,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        no_fills=no_fills,
        win_rate=win_rate,
        average_r=average_r,
        total_r=total_r,
        profit_factor=None,
        maximum_drawdown_r=None,
        average_mae=None,
        average_mfe=None,
        median_mae=None,
        median_mfe=None,
        average_duration_seconds=None,
        median_duration_seconds=None,
        sample_note="test",
    )


def _analysis(
    instrument: str,
    overall: SegmentMetrics,
    *,
    campaign_id: str = "test-campaign",
    ticks: int = 1000,
) -> CampaignBaselineAnalysis:
    return CampaignBaselineAnalysis(
        campaign_id=campaign_id,
        instrument=instrument,
        start_epoch=1,
        end_epoch=1000,
        start_utc="2026-01-01T00:00:00+00:00",
        end_utc="2026-01-01T00:16:40+00:00",
        ticks_processed=ticks,
        m1_candles=10,
        m15_candles=1,
        overall=overall,
        by_direction={},
        by_m15_bias={},
        rejection_counts={},
        outcome_counts={},
        baseline_report=None,
        interpretation="BASELINE INCONCLUSIVE",
        interpretation_notes=("test",),
        sample_scale="small_sample",
    )


def _fake_signal(direction: str = "LONG", m15_bias: str | None = "bullish") -> Any:
    ctx = SimpleNamespace(directional_bias=m15_bias)
    disp = SimpleNamespace(body_atr_ratio=1.2, body_range_ratio=0.7)
    fvg = SimpleNamespace(size=5.0, size_atr_ratio=0.5)
    msb = SimpleNamespace(bars_after_sweep=2)
    return SimpleNamespace(
        m15_context=ctx,
        displacement=disp,
        fvg=fvg,
        msb=msb,
        direction=direction,
    )


def _row(
    *,
    instrument: str = "volatility_75_1s",
    direction: str = "LONG",
    accepted: bool = True,
    outcome: SimulationOutcome | None = SimulationOutcome.SL,
    realized_r: float | None = -1.0,
    mae: float | None = 10.0,
    mfe: float | None = 5.0,
    duration: int | None = 100,
    m15_bias: str | None = "bullish",
) -> TradeExperimentRow:
    return TradeExperimentRow(
        instrument=instrument,
        signal_epoch=100,
        direction=direction,
        accepted=accepted,
        rejection_reason=None,
        entry_price=100.0 if accepted else None,
        stop_loss=90.0 if accepted else None,
        take_profit=120.0 if accepted else None,
        risk_reward=2.0 if accepted else None,
        risk_amount=1.0 if accepted else None,
        outcome=outcome,
        entry_time=100 if accepted and outcome != SimulationOutcome.NO_FILL else None,
        exit_time=200 if accepted and outcome not in (None, SimulationOutcome.NO_FILL) else None,
        duration_seconds=duration,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        signal=_fake_signal(direction=direction, m15_bias=m15_bias),
        candidate=None,
        simulation=None,
        metrics=None,
    )


# ---------------------------------------------------------------------------
# Sample-size classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "very_small"),
        (1, "very_small"),
        (29, "very_small"),
        (30, "small"),
        (79, "small"),
        (80, "preliminary"),
        (199, "preliminary"),
        (200, "stronger"),
        (500, "stronger"),
    ],
)
def test_sample_size_class(n: int, expected: str) -> None:
    assert sample_size_class(n) == expected


def test_sample_size_warning_thresholds() -> None:
    assert sample_size_warning(10) is not None
    assert sample_size_warning(50) is not None
    assert sample_size_warning(100) is not None
    assert sample_size_warning(250) is None


# ---------------------------------------------------------------------------
# Outcome / R / MAE calculations from synthetic rows (anti hard-coding)
# ---------------------------------------------------------------------------


def test_outcome_counts_and_rates_from_supplied_data() -> None:
    """Regression: metrics must reflect supplied rows, not 5D constants."""
    rows = [
        _row(outcome=SimulationOutcome.TP, realized_r=2.0, mae=1.0, mfe=20.0),
        _row(outcome=SimulationOutcome.SL, realized_r=-1.0, mae=15.0, mfe=2.0),
        _row(outcome=SimulationOutcome.SL, realized_r=-1.0, mae=12.0, mfe=3.0),
        _row(outcome=SimulationOutcome.TIMEOUT, realized_r=None, mae=8.0, mfe=9.0, duration=900),
        _row(outcome=SimulationOutcome.NO_FILL, realized_r=None, mae=None, mfe=None, duration=None),
        _row(outcome=SimulationOutcome.NO_FILL, realized_r=None, mae=None, mfe=None, duration=None),
    ]
    # Deliberately different from published 5D numbers
    overall = _seg(signals=6, accepted=6, filled=4, wins=1, losses=2, timeouts=1, no_fills=2)
    analysis = _analysis("synth_a", overall, ticks=999)

    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis],
        rows_by_instrument={"synth_a": rows},
        configuration_identity={"test": True},
        source="unit_test",
    )

    o = report.overall_metrics
    assert o.total_signals == 6
    assert o.accepted == 6
    assert o.tp == 1
    assert o.sl == 2
    assert o.timeout == 1
    assert o.no_fill == 2
    assert o.filled == 4
    assert o.fill_rate == pytest.approx(4 / 6)
    assert o.win_rate_filled == pytest.approx(1 / 4)
    assert o.win_rate_all_accepted == pytest.approx(1 / 6)

    # Must NOT equal hard-coded 5D-style aggregates
    assert o.total_signals != 148
    assert o.tp != 5 or o.sl != 16  # not the prompt's 5D snapshot

    r = report.r_metrics
    assert r.count == 3  # TP + 2 SL
    assert r.total_r == pytest.approx(0.0)  # 2 + (-1) + (-1)
    assert r.average_r == pytest.approx(0.0)
    assert r.positive_r_count == 1
    assert r.negative_r_count == 2

    assert report.mae_metrics["aggregate"]["count"] == 4
    assert report.mfe_metrics["aggregate"]["count"] == 4
    assert report.source == "unit_test"


def test_r_by_outcome() -> None:
    rows = [
        _row(outcome=SimulationOutcome.TP, realized_r=2.0),
        _row(outcome=SimulationOutcome.TP, realized_r=2.0),
        _row(outcome=SimulationOutcome.SL, realized_r=-1.0),
    ]
    overall = _seg(signals=3, filled=3, wins=2, losses=1)
    analysis = _analysis("x", overall)
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    by = report.r_metrics.by_outcome
    assert by["tp"]["count"] == 2
    assert by["tp"]["total_r"] == pytest.approx(4.0)
    assert by["sl"]["count"] == 1
    assert by["sl"]["total_r"] == pytest.approx(-1.0)


def test_duration_timeout_near_horizon() -> None:
    rows = [
        _row(outcome=SimulationOutcome.TIMEOUT, realized_r=None, duration=900),
        _row(outcome=SimulationOutcome.TIMEOUT, realized_r=None, duration=880),
        _row(outcome=SimulationOutcome.SL, realized_r=-1.0, duration=50),
    ]
    overall = _seg(signals=3, filled=3, wins=0, losses=1, timeouts=2)
    analysis = _analysis("x", overall)
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    dur = report.instrument_metrics["x"].duration_by_outcome["timeout"]
    assert dur.count == 2
    assert dur.near_horizon_count == 2  # both >= 0.95 * 900


def test_direction_grouping_and_small_sample_warning() -> None:
    rows = [
        _row(direction="LONG", outcome=SimulationOutcome.SL, realized_r=-1.0),
        _row(direction="SHORT", outcome=SimulationOutcome.TP, realized_r=2.0),
    ]
    overall = _seg(signals=2, filled=2, wins=1, losses=1)
    analysis = _analysis("x", overall)
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    assert "LONG" in report.direction_metrics
    assert "SHORT" in report.direction_metrics
    assert report.direction_metrics["LONG"].sl == 1
    assert report.direction_metrics["SHORT"].tp == 1
    # n=1 per direction -> very_small warning
    assert report.direction_metrics["LONG"].sample_class == "very_small"
    assert report.direction_metrics["LONG"].warning is not None


def test_m15_context_grouping() -> None:
    rows = [
        _row(m15_bias="bullish", outcome=SimulationOutcome.SL, realized_r=-1.0),
        _row(m15_bias="bearish", outcome=SimulationOutcome.TP, realized_r=2.0),
        _row(m15_bias="neutral", outcome=SimulationOutcome.TIMEOUT, realized_r=None),
    ]
    overall = _seg(signals=3, filled=3, wins=1, losses=1, timeouts=1)
    analysis = _analysis("x", overall)
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    assert set(report.context_metrics) >= {"bullish", "bearish", "neutral"}


def test_instrument_separation() -> None:
    rows_a = [
        _row(instrument="A", outcome=SimulationOutcome.TP, realized_r=2.0),
        _row(instrument="A", outcome=SimulationOutcome.SL, realized_r=-1.0),
    ]
    rows_b = [
        _row(
            instrument="B",
            outcome=SimulationOutcome.NO_FILL,
            realized_r=None,
            duration=None,
            mae=None,
            mfe=None,
        ),
    ]
    a = _analysis("A", _seg(signals=2, filled=2, wins=1, losses=1), ticks=100)
    b = _analysis("B", _seg(signals=1, filled=0, no_fills=1), ticks=200)
    report = BaselineDiagnosticAnalyzer().analyze(
        [a, b],
        rows_by_instrument={"A": rows_a, "B": rows_b},
    )
    assert report.instrument_metrics["A"].outcome.tp == 1
    assert report.instrument_metrics["B"].outcome.no_fill == 1
    assert report.instrument_metrics["A"].ticks_processed == 100
    assert report.instrument_metrics["B"].ticks_processed == 200


def test_empty_rows_fallback_uses_analysis_counts() -> None:
    overall = _seg(signals=10, accepted=10, filled=6, wins=1, losses=2, timeouts=3, no_fills=4)
    analysis = _analysis("only_counts", overall)
    report = BaselineDiagnosticAnalyzer().analyze([analysis], rows_by_instrument={})
    o = report.overall_metrics
    assert o.total_signals == 10
    assert o.tp == 1
    assert o.sl == 2
    assert o.timeout == 3
    assert o.no_fill == 4
    assert any("No TradeExperimentRow" in w for w in report.warnings)


def test_failure_modes_ranked() -> None:
    rows = (
        [_row(outcome=SimulationOutcome.TIMEOUT, realized_r=None, duration=900) for _ in range(5)]
        + [
            _row(
                outcome=SimulationOutcome.NO_FILL,
                realized_r=None,
                duration=None,
                mae=None,
                mfe=None,
            )
            for _ in range(3)
        ]
        + [_row(outcome=SimulationOutcome.SL, realized_r=-1.0) for _ in range(2)]
        + [_row(outcome=SimulationOutcome.TP, realized_r=2.0)]
    )
    overall = _seg(signals=11, filled=8, wins=1, losses=2, timeouts=5, no_fills=3)
    analysis = _analysis("x", overall)
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    modes = {f.mode: f for f in report.failure_modes}
    assert "TIMEOUT" in modes
    assert modes["TIMEOUT"].frequency == 5
    assert modes["NO_FILL"].frequency == 3
    assert modes["SL"].frequency == 2
    # First rank should be a failure mode by frequency
    assert report.failure_modes[0].mode in {"TIMEOUT", "NO_FILL", "SL"}


def test_write_artifacts(tmp_path) -> None:
    rows = [_row(outcome=SimulationOutcome.SL, realized_r=-1.0)]
    analysis = _analysis("x", _seg(signals=1, filled=1, losses=1))
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}
    )
    jpath, mpath = write_diagnostic_artifacts(report, tmp_path)
    assert jpath.exists()
    assert mpath.exists()
    text = mpath.read_text()
    assert "Milestone 5F" in text
    assert "Integrity statement" in text
    assert "Recommendation" in text


def test_report_markdown_contains_required_sections() -> None:
    rows = [_row()]
    analysis = _analysis("x", _seg(signals=1, filled=1, losses=1))
    report = BaselineDiagnosticAnalyzer().analyze(
        [analysis], rows_by_instrument={"x": rows}, configuration_identity={"k": "v"}
    )
    md = format_diagnostic_report(report)
    for section in [
        "Executive summary",
        "Integrity statement",
        "Configuration identity",
        "Data / sample coverage",
        "Overall outcome distribution",
        "Per-instrument comparison",
        "R distribution",
        "MAE / MFE",
        "Duration analysis",
        "Direction analysis",
        "M15 context",
        "Signal characteristic",
        "Failure-mode",
        "Sample-size warnings",
        "Limitations",
        "Research interpretation",
        "Recommendation",
    ]:
        assert section in md


def test_no_hardcoded_5d_values_in_module_source() -> None:
    """Guard: diagnostic_analysis.py must not embed published 5D aggregates."""
    from pathlib import Path

    src = Path("src/smb/research/diagnostic_analysis.py").read_text()
    # Prompt context numbers must not appear as literals driving metrics
    for bad in ["2973000", "2,973,000", "average R: -0.2857", "total R: -15.0"]:
        assert bad not in src


def test_analyze_rejects_empty_analyses() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        BaselineDiagnosticAnalyzer().analyze([])


# ---------------------------------------------------------------------------
# CampaignRunner construction regression (5F CLI orchestration)
# ---------------------------------------------------------------------------


def test_run_baseline_diagnostics_does_not_call_campaign_runner_without_deps() -> None:
    """Regression: CampaignRunner requires repository + config.

    The original 5F CLI used ``CampaignRunner()`` and ``runner.run(cfg, ...)``,
    which raises TypeError before any campaign can execute.
    """
    from pathlib import Path

    src = Path("src/smb/research/diagnostic_analysis.py").read_text()
    assert "CampaignRunner()" not in src
    assert "run_campaign(" in src


def test_run_baseline_diagnostics_executes_campaign_path(
    tmp_path: Path,
) -> None:
    """End-to-end: run_baseline_diagnostics must reach campaign execution.

    Uses a flat tick series (zero signals is a legitimate empty campaign).
    Must not raise TypeError about missing CampaignRunner constructor args.
    """
    from smb.data.models import StoredTick
    from smb.data.store import ParquetTickStore
    from smb.research.diagnostic_analysis import run_baseline_diagnostics

    data_root = tmp_path / "data"
    store = ParquetTickStore(data_root)
    instrument = "volatility_75_1s"
    # ~2 hours of 1s ticks — enough for candle builders; strategy may yield 0 signals
    base = 1_700_000_000
    ticks = [
        StoredTick(instrument=instrument, epoch=base + i, price=100.0)
        for i in range(7200)
    ]
    store.write_ticks(ticks)
    store.reindex_source_order(instrument)

    out = tmp_path / "milestone-5f"
    report = run_baseline_diagnostics(
        data_root=data_root,
        output=out,
        instruments=[instrument],
        equity=10_000.0,
    )

    assert report.source == "campaign_execution"
    assert (out / "diagnostic.json").is_file()
    assert (out / "report.md").is_file()
    # configuration identity records frozen baseline defaults
    cfg = report.configuration_identity
    assert cfg.get("strategy", {}).get("swing_x") == 2
    assert cfg.get("trade", {}).get("target_rr") == 2.0
    assert cfg.get("simulation", {}).get("max_duration_seconds") == 900
    # metrics derived from this run, not hard-coded 5D numbers
    assert report.overall_metrics.total_signals != 148
