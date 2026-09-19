"""Tests for Milestone 6B — Horizon-Aware Trade Construction & Exit Study."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest  # type: ignore[import-not-found]

from smb.research.experiment import ExperimentResult, TradeExperimentRow
from smb.research.horizon_exit_study import (
    DEFAULT_BASELINE_HORIZON_SECONDS,
    FROZEN_BASELINE_HORIZON_SECONDS,
    HorizonExitStudyReport,
    analyze_horizon_exit_study,
    analyze_instrument,
    analyze_timeouts,
    count_outcomes,
    format_horizon_exit_study_report,
    leakage_review_notes,
    paired_horizon_comparison,
    run_horizon_exit_study_on_results,
    study_record_from_row,
    target_reachability_summary,
    threshold_reach,
)
from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction
from smb.trade.models import RejectionReason


def _candidate(
    *,
    entry: float = 100.0,
    stop: float = 90.0,
    target: float = 120.0,
    risk: float = 10.0,
    reward: float = 20.0,
    direction: Direction = Direction.LONG,
):
    return SimpleNamespace(
        entry_price=entry,
        stop_loss=stop,
        take_profit=target,
        risk_distance=risk,
        reward_distance=reward,
        risk_reward=reward / risk if risk else 0.0,
        direction=direction,
    )


def _row(
    *,
    instrument: str = "volatility_75_1s",
    epoch: int = 1_700_000_000,
    direction: str = "long",
    accepted: bool = True,
    outcome: SimulationOutcome | None = SimulationOutcome.SL,
    entry_price: float | None = 100.0,
    stop_loss: float | None = 90.0,
    take_profit: float | None = 120.0,
    risk_distance: float = 10.0,
    reward_distance: float = 20.0,
    mfe: float | None = 5.0,
    mae: float | None = 8.0,
    duration: int | None = 120,
    entry_time: int | None = 1_700_000_010,
    exit_time: int | None = 1_700_000_130,
    rejection_reason: RejectionReason | None = None,
) -> TradeExperimentRow:
    candidate_obj = None
    if accepted:
        candidate_obj = _candidate(
            entry=entry_price or 100.0,
            stop=stop_loss or 90.0,
            target=take_profit or 120.0,
            risk=risk_distance,
            reward=reward_distance,
            direction=Direction.LONG if direction == "long" else Direction.SHORT,
        )
    return cast(
        TradeExperimentRow,
        SimpleNamespace(
            instrument=instrument,
            signal_epoch=epoch,
            direction=direction,
            accepted=accepted,
            rejection_reason=rejection_reason,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=reward_distance / risk_distance if risk_distance else None,
            risk_amount=None,
            outcome=outcome,
            entry_time=entry_time,
            exit_time=exit_time,
            duration_seconds=duration,
            realized_r=None,
            mfe=mfe,
            mae=mae,
            signal=None,
            candidate=candidate_obj,
            simulation=None,
            metrics=None,
        ),
    )


def _synthetic_records(
    *,
    n_tp: int = 2,
    n_sl: int = 3,
    n_timeout: int = 2,
    n_no_fill: int = 1,
    n_rejected: int = 1,
    instrument: str = "volatility_75_1s",
    epoch0: int = 1_700_000_000,
) -> list[TradeExperimentRow]:
    rows: list[TradeExperimentRow] = []
    i = 0
    for _ in range(n_tp):
        rows.append(
            _row(
                instrument=instrument,
                epoch=epoch0 + i,
                outcome=SimulationOutcome.TP,
                mfe=22.0,
                mae=3.0,
                duration=200,
            )
        )
        i += 1
    for _ in range(n_sl):
        rows.append(
            _row(
                instrument=instrument,
                epoch=epoch0 + i,
                outcome=SimulationOutcome.SL,
                mfe=4.0,
                mae=10.0,
                duration=80,
            )
        )
        i += 1
    for _ in range(n_timeout):
        rows.append(
            _row(
                instrument=instrument,
                epoch=epoch0 + i,
                outcome=SimulationOutcome.TIMEOUT,
                mfe=8.0,
                mae=5.0,
                duration=900,
            )
        )
        i += 1
    for _ in range(n_no_fill):
        rows.append(
            _row(
                instrument=instrument,
                epoch=epoch0 + i,
                outcome=SimulationOutcome.NO_FILL,
                mfe=None,
                mae=None,
                duration=None,
                entry_time=None,
                exit_time=None,
            )
        )
        i += 1
    for _ in range(n_rejected):
        rows.append(
            _row(
                instrument=instrument,
                epoch=epoch0 + i,
                accepted=False,
                outcome=None,
                mfe=None,
                mae=None,
                duration=None,
                rejection_reason=RejectionReason.INSUFFICIENT_RR,
            )
        )
        i += 1
    return rows


def _fake_experiment_result(
    rows: list,
    *,
    instrument: str = "volatility_75_1s",
    signals: int | None = None,
    accepted: int | None = None,
    rejected: int | None = None,
    horizon: int = 900,
) -> ExperimentResult:
    n_signals = signals if signals is not None else len(rows)
    n_acc = accepted if accepted is not None else sum(1 for r in rows if r.accepted)
    n_rej = rejected if rejected is not None else sum(1 for r in rows if not r.accepted)
    outcomes: dict[str, int] = {}
    for r in rows:
        if r.outcome is not None:
            outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
    summary = SimpleNamespace(
        instrument=instrument,
        start_epoch=1_700_000_000,
        end_epoch=1_700_100_000,
        ticks_processed=50_000,
        m1_candles=100,
        m15_candles=10,
        signals=n_signals,
        candidates_accepted=n_acc,
        candidates_rejected=n_rej,
        outcomes=outcomes,
        win_rate=None,
        average_r=None,
        total_r=None,
        average_duration_seconds=None,
        average_mae=None,
        average_mfe=None,
    )
    config = SimpleNamespace(
        instrument=instrument,
        simulation=SimpleNamespace(max_duration_seconds=horizon),
    )
    return cast(
        ExperimentResult,
        SimpleNamespace(
            config=config,
            summary=summary,
            rows=tuple(rows),
            simulations=(),
            metrics=(),
            validation=None,
        ),
    )


class TestStudyRecord:
    def test_filled_sl_computes_mfe_r(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.SL, mfe=5.0, risk_distance=10.0)
        )
        assert rec.filled is True
        assert rec.outcome == "sl"
        assert rec.mfe_r == pytest.approx(0.5)
        assert rec.mfe_target_ratio == pytest.approx(5.0 / 20.0)

    def test_no_fill_excluded_from_filled(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.NO_FILL, mfe=None, mae=None)
        )
        assert rec.filled is False
        assert rec.outcome == "no_fill"
        assert rec.mfe_r is None

    def test_rejected_excluded(self):
        rec = study_record_from_row(
            _row(
                accepted=False,
                outcome=None,
                rejection_reason=RejectionReason.INSUFFICIENT_RR,
            )
        )
        assert rec.filled is False
        assert rec.exclusion_reason == "rejected"

    def test_invalid_risk_excluded(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.TP, risk_distance=0.0, mfe=5.0)
        )
        assert rec.exclusion_reason == "invalid_risk"
        assert rec.filled is False

    def test_non_finite_mfe_not_zeroed(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.TP, mfe=float("nan"), mae=3.0)
        )
        assert rec.mfe is None
        assert rec.mfe_r is None
        assert rec.mae == pytest.approx(3.0)

    def test_negative_mfe_treated_invalid(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.TP, mfe=-1.0, mae=2.0)
        )
        assert rec.mfe is None


class TestOutcomeCounts:
    def test_no_fill_not_in_filled_pct(self):
        rows = _synthetic_records(
            n_tp=2, n_sl=2, n_timeout=1, n_no_fill=3, n_rejected=0
        )
        records = [study_record_from_row(r) for r in rows]
        oc = count_outcomes(records, signals=8, accepted=8, rejected=0)
        assert oc.no_fill == 3
        assert oc.filled == 5
        assert oc.tp_pct_of_filled == pytest.approx(2 / 5)

    def test_empty_filled(self):
        rows = _synthetic_records(
            n_tp=0, n_sl=0, n_timeout=0, n_no_fill=2, n_rejected=1
        )
        records = [study_record_from_row(r) for r in rows]
        oc = count_outcomes(records)
        assert oc.filled == 0
        assert oc.tp_pct_of_filled is None


class TestThresholdsAndReachability:
    def test_partial_target_thresholds(self):
        rows = [
            _row(outcome=SimulationOutcome.TP, mfe=3.0, risk_distance=10.0),
            _row(outcome=SimulationOutcome.SL, mfe=6.0, risk_distance=10.0),
            _row(outcome=SimulationOutcome.TIMEOUT, mfe=12.0, risk_distance=10.0),
        ]
        records = [study_record_from_row(r) for r in rows]
        thr = threshold_reach(records, (0.25, 0.50, 0.75, 1.00))
        assert thr[0].n_reached == 3
        assert thr[1].n_reached == 2
        assert thr[3].n_reached == 1

    def test_target_reachability(self):
        rows = [
            _row(outcome=SimulationOutcome.TP, mfe=25.0, reward_distance=20.0),
            _row(outcome=SimulationOutcome.SL, mfe=5.0, reward_distance=20.0),
        ]
        records = [study_record_from_row(r) for r in rows]
        summary = target_reachability_summary(records)
        assert summary["n_mfe_reached_target"] == 1

    def test_timeout_analysis_separate(self):
        rows = _synthetic_records(
            n_tp=1, n_sl=1, n_timeout=3, n_no_fill=1, n_rejected=0
        )
        records = [study_record_from_row(r) for r in rows]
        ta = analyze_timeouts(records)
        assert ta.n_timeout == 3


class TestInstrumentAnalysis:
    def test_instrument_separation(self):
        v75 = _synthetic_records(instrument="volatility_75_1s", n_tp=3, n_sl=2)
        step = _synthetic_records(
            instrument="step_index", n_tp=1, n_sl=4, epoch0=1_800_000_000
        )
        records = [study_record_from_row(r) for r in v75 + step]
        r_v75 = analyze_instrument(records, instrument="volatility_75_1s")
        r_step = analyze_instrument(records, instrument="step_index")
        assert r_v75.outcomes.tp == 3
        assert r_step.outcomes.tp == 1

    def test_small_sample_limitations(self):
        rows = _synthetic_records(n_tp=1, n_sl=1, n_timeout=0, n_no_fill=0, n_rejected=0)
        records = [study_record_from_row(r) for r in rows]
        result = analyze_instrument(records, instrument="volatility_75_1s")
        assert any("high-variance" in s or "filled n=" in s for s in result.statistical_limitations)

    def test_baseline_scenario_present(self):
        rows = _synthetic_records()
        records = [study_record_from_row(r) for r in rows]
        result = analyze_instrument(records, instrument="volatility_75_1s")
        ids = {s.scenario_id for s in result.scenarios}
        assert "baseline_horizon" in ids or "primary_horizon" in ids
        assert "partial_target_mfe_r" in ids
        assert "target_reachability" in ids
        assert "extended_horizon_comparison" in ids


class TestFullReport:
    def test_analyze_and_write_artifacts(self, tmp_path: Path):
        v75_rows = _synthetic_records(instrument="volatility_75_1s")
        step_rows = _synthetic_records(
            instrument="step_index", n_tp=1, n_sl=2, epoch0=1_800_000_000
        )
        results = [
            _fake_experiment_result(v75_rows, instrument="volatility_75_1s"),
            _fake_experiment_result(step_rows, instrument="step_index"),
        ]
        report = run_horizon_exit_study_on_results(results, tmp_path)
        assert isinstance(report, HorizonExitStudyReport)
        assert report.baseline_preserved is True
        assert report.production_execution_unchanged is True
        assert (tmp_path / "horizon_exit_study.json").is_file()
        assert (tmp_path / "horizon_exit_study_report.md").is_file()
        payload = json.loads((tmp_path / "horizon_exit_study.json").read_text())
        assert payload["baseline_preserved"] is True
        md = (tmp_path / "horizon_exit_study_report.md").read_text()
        assert "Leakage review" in md

    def test_json_schema_deterministic_keys(self, tmp_path: Path):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study([_fake_experiment_result(rows)])
        d1 = json.dumps(report.to_dict(), sort_keys=True, default=str)
        d2 = json.dumps(report.to_dict(), sort_keys=True, default=str)
        assert d1 == d2

    def test_extended_horizon_scenario(self):
        base_rows = _synthetic_records(
            n_tp=1, n_sl=1, n_timeout=2, n_no_fill=0, n_rejected=0
        )
        ext_rows = [
            _row(outcome=SimulationOutcome.TP, mfe=25.0, epoch=1_700_000_000),
            _row(outcome=SimulationOutcome.SL, mfe=4.0, epoch=1_700_000_001),
            _row(outcome=SimulationOutcome.TP, mfe=18.0, epoch=1_700_000_002),
            _row(outcome=SimulationOutcome.TIMEOUT, mfe=7.0, epoch=1_700_000_003),
        ]
        report = analyze_horizon_exit_study(
            [_fake_experiment_result(base_rows)],
            extended_results=[_fake_experiment_result(ext_rows, horizon=1800)],
            extended_horizon_seconds=1800,
        )
        ext_sc = next(
            s
            for s in report.instruments[0].scenarios
            if s.scenario_id == "extended_horizon_comparison"
        )
        assert ext_sc.label == "exploratory"
        assert "paired_comparison" in ext_sc.metrics
        assert "timeout_to_tp" in ext_sc.metrics["paired_comparison"]

    def test_empty_dataset(self):
        results = [
            _fake_experiment_result(
                [], instrument="volatility_75_1s", signals=0, accepted=0, rejected=0
            )
        ]
        report = analyze_horizon_exit_study(results)
        assert report.dataset_audit["total_filled"] == 0

    def test_markdown_independent(self):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study([_fake_experiment_result(rows)])
        md = format_horizon_exit_study_report(report)
        assert md.startswith("# ")
        assert "Scope" in md

    def test_leakage_review_present(self):
        notes = leakage_review_notes()
        assert any("post-entry" in n.lower() or "post-trade" in n.lower() for n in notes)


class TestBaselinePreservation:
    def test_default_horizon_constant(self):
        assert DEFAULT_BASELINE_HORIZON_SECONDS == 900

    def test_report_flags_unchanged(self):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study([_fake_experiment_result(rows)])
        assert report.baseline_preserved is True
        assert report.production_execution_unchanged is True


class TestCLIRegistration:
    def test_parser_has_horizon_command(self):
        from smb.research.__main__ import main

        with pytest.raises(SystemExit) as exc:
            main(["run-horizon-exit-study", "--help"])
        assert exc.value.code == 0



class TestBaselinePreservationEnforced:
    def test_canonical_900_preserves_baseline(self):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study(
            [_fake_experiment_result(rows)], horizon_seconds=900
        )
        assert report.baseline_preserved is True
        assert report.is_canonical_baseline_run is True
        assert report.analysis_horizon_seconds == 900
        assert report.canonical_baseline_horizon_seconds == FROZEN_BASELINE_HORIZON_SECONDS
        ids = {s.scenario_id for s in report.instruments[0].scenarios}
        assert "baseline_horizon" in ids

    def test_non_900_does_not_claim_baseline_preserved(self):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study(
            [_fake_experiment_result(rows, horizon=600)], horizon_seconds=600
        )
        assert report.baseline_preserved is False
        assert report.is_canonical_baseline_run is False
        assert report.analysis_horizon_seconds == 600
        ids = {s.scenario_id for s in report.instruments[0].scenarios}
        assert "primary_horizon" in ids
        assert "baseline_horizon" not in ids
        scenarios = report.instruments[0].scenarios
        primary = next(s for s in scenarios if s.scenario_id == "primary_horizon")
        assert primary.label == "exploratory"


class TestPairedExtendedHorizon:
    def test_timeout_to_tp_conversion_paired(self):
        # Same signal keys; baseline TIMEOUT becomes TP at extended
        base_rows = [
            _row(epoch=100, outcome=SimulationOutcome.TIMEOUT, mfe=8.0),
            _row(epoch=101, outcome=SimulationOutcome.SL, mfe=2.0),
        ]
        ext_rows = [
            _row(epoch=100, outcome=SimulationOutcome.TP, mfe=25.0),
            _row(epoch=101, outcome=SimulationOutcome.SL, mfe=2.0),
        ]
        base_recs = [study_record_from_row(r) for r in base_rows]
        ext_recs = [study_record_from_row(r) for r in ext_rows]
        paired = paired_horizon_comparison(base_recs, ext_recs)
        assert paired["n_shared"] == 2
        assert paired["timeout_to_tp"] == 1
        assert paired["timeout_to_sl"] == 0
        assert paired["timeout_to_timeout"] == 0
        assert paired["outcome_transitions"]["timeout->tp"] == 1

    def test_extended_not_greater_not_estimable(self):
        rows = _synthetic_records()
        report = analyze_horizon_exit_study(
            [_fake_experiment_result(rows)],
            horizon_seconds=900,
            extended_results=[_fake_experiment_result(rows, horizon=900)],
            extended_horizon_seconds=900,
        )
        ext = next(
            s
            for s in report.instruments[0].scenarios
            if s.scenario_id == "extended_horizon_comparison"
        )
        assert ext.label == "not_estimable"
        assert ext.metrics["status"] == "extended_not_greater_than_primary"


class TestShortDirection:
    def test_short_mfe_r_and_target_ratio(self):
        # Short: mfe is favorable move in price terms; ratio still mfe/risk
        rec = study_record_from_row(
            _row(
                direction="short",
                outcome=SimulationOutcome.TP,
                mfe=15.0,
                mae=4.0,
                risk_distance=10.0,
                reward_distance=20.0,
                entry_price=100.0,
                stop_loss=110.0,
                take_profit=80.0,
            )
        )
        assert rec.filled is True
        assert rec.mfe_r == pytest.approx(1.5)
        assert rec.mae_r == pytest.approx(0.4)
        assert rec.mfe_target_ratio == pytest.approx(0.75)

    def test_short_in_instrument_analysis(self):
        rows = [
            _row(direction="short", outcome=SimulationOutcome.TP, mfe=20.0, epoch=1),
            _row(direction="short", outcome=SimulationOutcome.SL, mfe=3.0, epoch=2),
        ]
        records = [study_record_from_row(r) for r in rows]
        result = analyze_instrument(records, instrument="volatility_75_1s")
        assert result.outcomes.tp == 1
        assert result.outcomes.sl == 1
        assert result.mfe_r_distribution is not None
        assert result.mfe_r_distribution.count == 2


class TestTargetReachabilitySemantics:
    def test_by_outcome_breakdown(self):
        rows = [
            _row(outcome=SimulationOutcome.TP, mfe=25.0, reward_distance=20.0, epoch=1),
            _row(outcome=SimulationOutcome.SL, mfe=5.0, reward_distance=20.0, epoch=2),
            _row(outcome=SimulationOutcome.TIMEOUT, mfe=22.0, reward_distance=20.0, epoch=3),
            _row(outcome=SimulationOutcome.TIMEOUT, mfe=2.0, reward_distance=20.0, epoch=4),
        ]
        records = [study_record_from_row(r) for r in rows]
        summary = target_reachability_summary(records)
        assert summary["metric_name"] == "pre_terminal_exit_mfe_target_reachability"
        assert "by_outcome" in summary
        assert summary["by_outcome"]["tp"]["n_mfe_reached_target"] == 1
        assert summary["by_outcome"]["sl"]["n_mfe_reached_target"] == 0
        assert summary["by_outcome"]["timeout"]["n_mfe_reached_target"] == 1
        assert any("pre-terminal" in n.lower() or "Pre-terminal" in n for n in summary["notes"])


class TestNoFillAndDenominators:
    def test_no_fill_excluded_from_threshold_distributions(self):
        rows = _synthetic_records(
            n_tp=2, n_sl=0, n_timeout=0, n_no_fill=5, n_rejected=0
        )
        records = [study_record_from_row(r) for r in rows]
        thr = threshold_reach(records, (0.25,))
        # only 2 filled TP with mfe
        assert thr[0].n_eligible == 2
        oc = count_outcomes(records)
        assert oc.no_fill == 5
        assert oc.filled == 2
        # no_fill must not appear in mfe_r dist via instrument analysis
        result = analyze_instrument(records, instrument="volatility_75_1s")
        assert result.mfe_r_distribution is not None
        assert result.mfe_r_distribution.count == 2

    def test_denominator_reconciliation(self):
        rows = _synthetic_records(
            n_tp=2, n_sl=3, n_timeout=1, n_no_fill=2, n_rejected=1
        )
        records = [study_record_from_row(r) for r in rows]
        oc = count_outcomes(records, signals=9, accepted=8, rejected=1)
        assert oc.tp + oc.sl + oc.timeout == oc.filled
        assert oc.no_fill == 2
        assert oc.filled + oc.no_fill + oc.rejected == 2 + 2 + 1 + 3 + 1  # all rows

    def test_invalid_target_distance_excluded(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.TP, reward_distance=0.0, mfe=5.0)
        )
        assert rec.exclusion_reason == "invalid_target"
        assert rec.filled is False
        assert rec.mfe_target_ratio is None

    def test_non_finite_target_distance(self):
        rec = study_record_from_row(
            _row(outcome=SimulationOutcome.TP, reward_distance=float("nan"), mfe=5.0)
        )
        assert rec.exclusion_reason == "invalid_target"
        assert rec.filled is False


class TestZeroSignalInstrument:
    def test_zero_signal_instrument_audited_independently(self):
        v75 = _synthetic_records(instrument="volatility_75_1s", n_tp=2, n_sl=1)
        empty_step = _fake_experiment_result(
            [], instrument="step_index", signals=0, accepted=0, rejected=0
        )
        report = analyze_horizon_exit_study(
            [
                _fake_experiment_result(v75, instrument="volatility_75_1s"),
                empty_step,
            ]
        )
        inst_map = {i.instrument: i for i in report.instruments}
        assert "step_index" in inst_map
        assert inst_map["step_index"].outcomes.signals == 0
        assert inst_map["step_index"].outcomes.filled == 0
        assert inst_map["volatility_75_1s"].outcomes.filled >= 1


class TestObservationWindowIntegration:
    """Prove MFE/MAE use fill → exit/horizon only (via ResearchMetricsCalculator)."""

    def test_prefill_and_post_exit_ticks_do_not_affect_mfe_mae(self):
        from datetime import UTC, datetime

        from smb.deriv.history import Tick
        from smb.research.metrics import ResearchMetricsCalculator
        from smb.simulation.models import ExitReason, TradeSimulationResult
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
        from smb.trade.models import TradeCandidate

        def tick(epoch: int, price: float) -> Tick:
            return Tick(
                timestamp=datetime.fromtimestamp(epoch, tz=UTC),
                price=price,
                epoch=epoch,
            )

        direction = Direction.LONG
        swing = SwingPoint(
            kind="low",
            price=100.0,
            candle_start_epoch=100,
            candle_end_epoch=160,
            index=2,
            confirmed_at_epoch=280,
        )
        structure = SwingPoint(
            kind="high",
            price=110.0,
            candle_start_epoch=0,
            candle_end_epoch=60,
            index=0,
            confirmed_at_epoch=180,
        )
        sweep = LiquiditySweep(
            direction=direction,
            swept_level=100.0,
            sweep_candle_start_epoch=300,
            sweep_candle_end_epoch=360,
            sweep_candle_low=98.0,
            sweep_candle_high=103.0,
            sweep_candle_close=102.0,
            swing=swing,
        )
        msb = MarketStructureBreak(
            direction=direction,
            broken_level=structure.price,
            msb_candle_start_epoch=420,
            msb_candle_end_epoch=480,
            msb_candle_close=112.0,
            bars_after_sweep=1,
            structure_swing=structure,
        )
        disp = Displacement(
            direction=direction,
            candle_start_epoch=480,
            candle_end_epoch=540,
            open=112.0,
            high=120.0,
            low=111.0,
            close=119.0,
            body=7.0,
            range_=9.0,
            body_range_ratio=7 / 9,
            body_atr_ratio=1.0,
            atr=5.0,
        )
        fvg = FairValueGap(
            direction=direction,
            gap_low=115.0,
            gap_high=117.0,
            size=2.0,
            size_atr_ratio=0.4,
            candle1_start_epoch=480,
            candle2_start_epoch=540,
            candle3_start_epoch=600,
            candle3_end_epoch=660,
        )
        m15 = M15Context(
            last_m15_start_epoch=None,
            last_m15_end_epoch=None,
            last_m15_close=None,
            recent_high=None,
            recent_low=None,
            directional_bias=None,
        )
        signal = StrategySignal(
            instrument="vol75",
            direction=direction,
            signal_epoch=1000,
            timeframe_context="M15+M1",
            sweep=sweep,
            msb=msb,
            displacement=disp,
            fvg=fvg,
            m15_context=m15,
        )
        candidate = TradeCandidate(
            instrument="vol75",
            direction=direction,
            signal_epoch=1000,
            entry_price=105.0,
            entry_zone_low=104.0,
            entry_zone_high=106.0,
            stop_loss=95.0,
            take_profit=125.0,
            risk_distance=10.0,
            reward_distance=20.0,
            risk_reward=2.0,
            risk_percent=0.01,
            risk_amount=100.0,
            position_size=10.0,
            source_signal=signal,
        )
        sim = TradeSimulationResult(
            instrument="vol75",
            direction=direction,
            signal_epoch=1000,
            outcome=SimulationOutcome.TIMEOUT,
            filled=True,
            entry_time=1010,
            entry_price=105.0,
            exit_time=1100,
            exit_price=None,
            exit_reason=ExitReason.TIMEOUT,
            duration_seconds=90,
            candidate=candidate,
        )
        ticks = [
            tick(1005, 200.0),  # pre-fill extreme — must NOT count
            tick(1010, 105.0),  # fill
            tick(1020, 110.0),  # +5 favorable
            tick(1030, 103.0),  # -2 adverse
            tick(1090, 112.0),  # +7 favorable (max MFE)
            tick(1100, 111.0),  # exit boundary
            tick(1110, 300.0),  # post-exit extreme — must NOT count
        ]
        metrics = ResearchMetricsCalculator().calculate(sim, ticks)
        assert metrics.mfe == pytest.approx(7.0)
        assert metrics.mae == pytest.approx(2.0)
        assert metrics.observation_start == 1010
        assert metrics.observation_end == 1100


class TestCLIExecution:
    def test_cli_help_and_missing_output(self):
        from smb.research.__main__ import main

        with pytest.raises(SystemExit) as exc:
            main(["run-horizon-exit-study", "--help"])
        assert exc.value.code == 0

        # missing required --output
        with pytest.raises(SystemExit) as exc2:
            main(["run-horizon-exit-study"])
        assert exc2.value.code == 2

    def test_cli_empty_data_returns_error(self, tmp_path: Path):
        from smb.research.__main__ import main

        # Nonexistent data root should fail with experiment/missing data
        code = main(
            [
                "run-horizon-exit-study",
                "--output",
                str(tmp_path / "out"),
                "--data-root",
                str(tmp_path / "no_such_data"),
                "--instruments",
                "volatility_75_1s",
            ]
        )
        assert code != 0
