"""Tests for Milestone 6B — Horizon-Aware Trade Construction & Exit Study."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smb.research.horizon_exit_study import (
    DEFAULT_BASELINE_HORIZON_SECONDS,
    HorizonExitStudyReport,
    analyze_horizon_exit_study,
    analyze_instrument,
    analyze_timeouts,
    count_outcomes,
    format_horizon_exit_study_report,
    leakage_review_notes,
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
):
    cand = None
    if accepted:
        cand = _candidate(
            entry=entry_price or 100.0,
            stop=stop_loss or 90.0,
            target=take_profit or 120.0,
            risk=risk_distance,
            reward=reward_distance,
            direction=Direction.LONG if direction == "long" else Direction.SHORT,
        )
    return SimpleNamespace(
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
        candidate=cand,
        simulation=None,
        metrics=None,
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
) -> list:
    rows = []
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
):
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
    return SimpleNamespace(
        config=config,
        summary=summary,
        rows=tuple(rows),
        simulations=(),
        metrics=(),
        validation=None,
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

    def test_nonfinite_mfe_not_zeroed(self):
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
        assert "baseline_horizon" in ids
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
