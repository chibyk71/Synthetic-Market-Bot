"""Tests for Milestone 6A — Real-Data Predictive Evidence Study."""

from __future__ import annotations

import json
from pathlib import Path

import pytest  # type: ignore[import-not-found]

from smb.research.predictive_evidence import (
    ALL_FEATURE_NAMES,
    FEATURE_NAMES,
    GEOMETRY_FEATURE_NAMES,
    EvidenceRow,
    PredictiveEvidenceReport,
    analyze_evidence,
    audit_dataset,
    cliffs_delta,
    feature_group,
    format_predictive_evidence_report,
    mann_whitney_u_and_p,
    mfe_diagnostics,
    run_chronological_model,
    spearman_rho,
    univariate_diagnostics,
    write_predictive_evidence_artifacts,
)


def _feat(**overrides: float) -> dict[str, float]:
    base = {name: 0.0 for name in ALL_FEATURE_NAMES}
    base.update(
        {
            "direction": 1.0,
            "instrument_v75": 1.0,
            "instrument_step100": 0.0,
            "sweep_depth": 1.5,
            "msb_bars_after_sweep": 1.0,
            "displacement_body_range_ratio": 0.7,
            "displacement_body_atr_ratio": 1.0,
            "atr": 10.0,
            "fvg_size": 5.0,
            "fvg_size_atr_ratio": 0.5,
            "fvg_size_atr_missing": 0.0,
            "m15_bias": 1.0,
            "m15_bias_missing": 0.0,
            "m15_recent_range": 20.0,
            "m15_range_missing": 0.0,
            "signal_vs_m15_mid": 0.1,
            "signal_vs_m15_missing": 0.0,
            "hour_of_day": 12.0,
            "risk_distance": 10.0,
            "reward_distance": 20.0,
            "risk_reward": 2.0,
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "take_profit": 120.0,
        }
    )
    base.update(overrides)
    return base


def _row(
    *,
    instrument: str = "volatility_75_1s",
    epoch: int = 1_700_000_000,
    direction: str = "long",
    outcome: str = "sl",
    target: int | None = 0,
    features: dict[str, float] | None = None,
    realized_r: float | None = -1.0,
    mfe: float | None = 5.0,
    mae: float | None = 8.0,
    duration: int | None = 100,
    filled: bool = True,
    **feat_overrides: float,
) -> EvidenceRow:
    feats = features if features is not None else _feat(**feat_overrides)
    return EvidenceRow(
        instrument=instrument,
        signal_epoch=epoch,
        direction=direction,
        outcome=outcome,
        target=target,
        features=feats,
        realized_r=realized_r,
        mfe=mfe,
        mae=mae,
        duration_seconds=duration,
        risk_distance=feats.get("risk_distance"),
        reward_distance=feats.get("reward_distance"),
        risk_reward=feats.get("risk_reward"),
        filled=filled,
    )


def _synthetic_campaign(
    n_tp: int = 3,
    n_sl: int = 5,
    n_timeout: int = 4,
    n_no_fill: int = 2,
    instrument: str = "volatility_75_1s",
    epoch0: int = 1_700_000_000,
) -> list[EvidenceRow]:
    rows: list[EvidenceRow] = []
    t = epoch0
    for i in range(n_tp):
        rows.append(
            _row(
                instrument=instrument,
                epoch=t,
                outcome="tp",
                target=1,
                realized_r=2.0,
                mfe=25.0,
                sweep_depth=3.0 + i * 0.1,
                atr=12.0,
            )
        )
        t += 60
    for i in range(n_sl):
        rows.append(
            _row(
                instrument=instrument,
                epoch=t,
                outcome="sl",
                target=0,
                realized_r=-1.0,
                mfe=3.0,
                sweep_depth=1.0 + i * 0.05,
                atr=8.0,
            )
        )
        t += 60
    for i in range(n_timeout):
        rows.append(
            _row(
                instrument=instrument,
                epoch=t,
                outcome="timeout",
                target=0,
                realized_r=0.0,
                mfe=12.0,
                sweep_depth=1.2,
                atr=9.0,
            )
        )
        t += 60
    for i in range(n_no_fill):
        rows.append(
            _row(
                instrument=instrument,
                epoch=t,
                outcome="no_fill",
                target=None,
                realized_r=None,
                mfe=None,
                mae=None,
                filled=False,
                duration=None,
            )
        )
        t += 60
    return rows


class TestDatasetAudit:
    def test_tp_non_tp_counts(self):
        rows = _synthetic_campaign(n_tp=3, n_sl=4, n_timeout=2, n_no_fill=1)
        audit = audit_dataset(rows)
        assert audit.tp_count == 3
        assert audit.sl_count == 4
        assert audit.timeout_count == 2
        assert audit.no_fill_count == 1
        assert audit.positive_count == 3
        assert audit.non_positive_count == 6
        assert audit.labeled_closed_trades == 9
        assert audit.positive_rate == pytest.approx(3 / 9)
        assert audit.evidence_rows_analyzed == 10
        assert audit.total_signals == 10  # alias
        assert audit.strategy_signals is None
        assert audit.candidates_rejected is None
        assert audit.accepted_candidates == 10
        assert any("not supplied" in n for n in audit.funnel_notes)

    def test_audit_funnel_with_upstream_counts(self):
        rows = _synthetic_campaign(n_tp=2, n_sl=2, n_timeout=1, n_no_fill=1)
        audit = audit_dataset(
            rows,
            strategy_signals=20,
            candidates_accepted=10,
            candidates_rejected=10,
        )
        assert audit.strategy_signals == 20
        assert audit.accepted_candidates == 10
        assert audit.candidates_rejected == 10
        assert audit.evidence_rows_analyzed == 6
        assert audit.filled_trades == 5
        assert audit.no_fill_count == 1
        assert audit.labeled_closed_trades == 5
        assert any("supplied" in n for n in audit.funnel_notes)

    def test_no_fill_excluded_from_target(self):
        rows = _synthetic_campaign(n_tp=1, n_sl=1, n_timeout=0, n_no_fill=3)
        labeled = [r for r in rows if r.target is not None]
        assert all(r.outcome != "no_fill" for r in labeled)
        assert sum(1 for r in rows if r.target is None) == 3

    def test_explicit_positive_ceiling_note(self):
        rows = _synthetic_campaign(n_tp=2, n_sl=5, n_timeout=3, n_no_fill=0)
        audit = audit_dataset(rows)
        assert "2 positive" in audit.explicit_positive_ceiling_note

    def test_duplicate_detection(self):
        r1 = _row(epoch=100, outcome="tp", target=1)
        r2 = _row(epoch=100, outcome="tp", target=1)
        audit = audit_dataset([r1, r2])
        assert audit.duplicate_count == 1

    def test_outcome_inconsistency_flagged(self):
        bad = _row(outcome="tp", target=1, filled=False)
        audit = audit_dataset([bad])
        assert any("filled=False" in s for s in audit.outcome_inconsistencies)

    def test_instrument_counts(self):
        rows = (
            _synthetic_campaign(n_tp=1, n_sl=1, n_timeout=0, n_no_fill=0, instrument="volatility_75_1s")
            + _synthetic_campaign(
                n_tp=0, n_sl=2, n_timeout=0, n_no_fill=0, instrument="step_index", epoch0=1_800_000_000
            )
        )
        audit = audit_dataset(rows)
        assert audit.instrument_counts["volatility_75_1s"] == 2
        assert audit.instrument_counts["step_index"] == 2


class TestUnivariate:
    def test_grouping_and_counts(self):
        rows = _synthetic_campaign(n_tp=4, n_sl=6, n_timeout=2, n_no_fill=0)
        results = univariate_diagnostics(rows, feature_names=["sweep_depth", "atr"])
        assert len(results) == 2
        by_name = {r.feature: r for r in results}
        assert by_name["sweep_depth"].n_tp == 4
        assert by_name["sweep_depth"].n_non_tp == 8
        assert by_name["sweep_depth"].group == "structural"
        assert by_name["atr"].group == "volatility"

    def test_insufficient_sample_direction(self):
        rows = [_row(outcome="tp", target=1, epoch=1, sweep_depth=5.0)]
        results = univariate_diagnostics(rows, feature_names=["sweep_depth"])
        assert results[0].direction == "insufficient_data"
        assert results[0].p_value is None

    def test_constant_feature(self):
        rows = _synthetic_campaign(n_tp=3, n_sl=3, n_timeout=0, n_no_fill=0)
        fixed = []
        for r in rows:
            feats = dict(r.features)
            feats["atr"] = 10.0
            fixed.append(
                EvidenceRow(
                    instrument=r.instrument,
                    signal_epoch=r.signal_epoch,
                    direction=r.direction,
                    outcome=r.outcome,
                    target=r.target,
                    features=feats,
                    realized_r=r.realized_r,
                    mfe=r.mfe,
                    mae=r.mae,
                    duration_seconds=r.duration_seconds,
                    risk_distance=r.risk_distance,
                    reward_distance=r.reward_distance,
                    risk_reward=r.risk_reward,
                    filled=r.filled,
                )
            )
        results = univariate_diagnostics(fixed, feature_names=["atr"])
        assert results[0].constant_feature is True
        assert results[0].direction == "overlapping"

    def test_cliffs_delta_sign(self):
        d = cliffs_delta([3.0, 4.0, 5.0], [1.0, 2.0, 2.5])
        assert d is not None and d > 0
        d2 = cliffs_delta([1.0, 2.0], [3.0, 4.0, 5.0])
        assert d2 is not None and d2 < 0

    def test_mann_whitney_runs(self):
        u, p = mann_whitney_u_and_p([1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0])
        assert u is not None and p is not None
        assert 0.0 <= p <= 1.0

    def test_missing_feature_counted(self):
        rows = _synthetic_campaign(n_tp=3, n_sl=3, n_timeout=0, n_no_fill=0)
        r0 = rows[0]
        feats = {k: v for k, v in r0.features.items() if k != "sweep_depth"}
        rows[0] = EvidenceRow(
            instrument=r0.instrument,
            signal_epoch=r0.signal_epoch,
            direction=r0.direction,
            outcome=r0.outcome,
            target=r0.target,
            features=feats,
            realized_r=r0.realized_r,
            mfe=r0.mfe,
            mae=r0.mae,
            duration_seconds=r0.duration_seconds,
            risk_distance=r0.risk_distance,
            reward_distance=r0.risk_reward,
            risk_reward=r0.risk_reward,
            filled=r0.filled,
        )
        results = univariate_diagnostics(rows, feature_names=["sweep_depth"])
        assert results[0].missing_count == 1


class TestMFE:
    def test_mfe_extraction_and_normalization(self):
        rows = _synthetic_campaign(n_tp=2, n_sl=2, n_timeout=2, n_no_fill=1)
        mfe = mfe_diagnostics(rows)
        assert mfe.n_with_mfe == 6
        assert mfe.mfe_median is not None
        assert "tp" in mfe.mfe_by_outcome

    def test_missing_mfe_handled(self):
        rows = [
            _row(outcome="sl", target=0, mfe=None, filled=True),
            _row(outcome="tp", target=1, mfe=10.0, epoch=2, filled=True),
        ]
        mfe = mfe_diagnostics(rows)
        assert mfe.n_with_mfe == 1

    def test_spearman_association(self):
        rows = []
        for i in range(10):
            rows.append(
                _row(
                    epoch=1_700_000_000 + i * 60,
                    outcome="timeout" if i % 2 else "sl",
                    target=0,
                    atr=float(i + 1),
                    mfe=float((i + 1) * 2),
                    risk_distance=1.0,
                )
            )
        mfe = mfe_diagnostics(rows)
        atr_corr = next(x for x in mfe.feature_spearman if x["feature"] == "atr")
        assert atr_corr["spearman_rho"] is not None
        assert atr_corr["spearman_rho"] > 0.8

    def test_instrument_separation_in_mfe(self):
        rows = (
            _synthetic_campaign(n_tp=1, n_sl=1, n_timeout=0, n_no_fill=0, instrument="volatility_75_1s")
            + _synthetic_campaign(
                n_tp=1, n_sl=1, n_timeout=0, n_no_fill=0, instrument="step_index", epoch0=1_800_000_000
            )
        )
        mfe = mfe_diagnostics(rows)
        assert "volatility_75_1s" in mfe.mfe_by_instrument
        assert "step_index" in mfe.mfe_by_instrument


class TestChronologicalModel:
    def test_no_shuffle_ordering(self):
        rows = _synthetic_campaign(n_tp=4, n_sl=6, n_timeout=4, n_no_fill=0)
        pooled, split = run_chronological_model(rows, train_ratio=0.5)
        assert split["shuffling"] is False
        assert split["status"] == "ok"
        assert split.get("evaluation_design") == "single_chronological_holdout"
        assert split["train_epoch_max"] <= split["oos_epoch_min"]

    def test_training_precedes_oos(self):
        rows = _synthetic_campaign(n_tp=5, n_sl=8, n_timeout=5, n_no_fill=0)
        pooled, split = run_chronological_model(rows, train_ratio=0.6)
        assert pooled is not None
        assert split["train_n"] + split["oos_n"] == split["n_labeled"]

    def test_insufficient_positive_handling(self):
        """Zero TP: model must not invent a positive class."""
        rows = _synthetic_campaign(n_tp=0, n_sl=5, n_timeout=3, n_no_fill=0)
        pooled, split = run_chronological_model(rows, train_ratio=0.5)
        assert split["n_labeled"] == 8
        assert split.get("shuffling") is False
        if split.get("status") == "insufficient_labeled":
            assert pooled is None
            return
        # Enough labeled rows for a split: train has zero positives
        assert split.get("train_positive", 0) == 0 or (
            pooled is not None and pooled.train_positive == 0
        )
        if pooled is not None:
            assert pooled.train_positive == 0
            assert pooled.model_type in ("constant_majority", "RandomForestClassifier")
            # With zero train positives, predicted probs should not favor class 1
            # (constant model uses p=0; RF with single class also collapses)
            summary = pooled.predicted_prob_summary
            max_p = summary.get("max")
            if max_p is not None:
                assert float(max_p) <= 0.5 + 1e-9
            notes_joined = " ".join(pooled.notes).lower()
            assert (
                "zero positive" in notes_joined
                or "single-class" in notes_joined
                or pooled.model_type == "constant_majority"
            )


    def test_naive_baseline_present(self):
        rows = _synthetic_campaign(n_tp=4, n_sl=6, n_timeout=4, n_no_fill=0)
        pooled, _ = run_chronological_model(rows, train_ratio=0.5)
        assert pooled is not None
        assert pooled.naive_accuracy is not None
        assert pooled.naive_majority_class in (0, 1)
        # Always-trade R is distinct from majority-classifier R naming
        assert pooled.oos_all_trades_total_r is not None
        assert pooled.naive_majority_total_r is not None
        if pooled.naive_majority_class == 0:
            assert pooled.naive_majority_total_r == 0.0
            assert pooled.naive_majority_average_r is None
        else:
            assert pooled.naive_majority_total_r == pooled.oos_all_trades_total_r
        # Deprecated aliases still hold always-trade R for compatibility
        assert pooled.naive_total_r == pooled.oos_all_trades_total_r

    def test_threshold_sensitivity(self):
        rows = _synthetic_campaign(n_tp=5, n_sl=8, n_timeout=5, n_no_fill=0)
        pooled, _ = run_chronological_model(rows, train_ratio=0.5, thresholds=(0.5, 0.7, 0.9))
        assert pooled is not None
        assert len(pooled.selected_by_threshold) == 3
        counts = [t.n_selected for t in pooled.selected_by_threshold]
        assert counts[0] >= counts[-1]

    def test_too_few_rows_returns_none(self):
        rows = _synthetic_campaign(n_tp=1, n_sl=1, n_timeout=0, n_no_fill=0)
        pooled, split = run_chronological_model(rows)
        assert pooled is None
        assert split["status"] == "insufficient_labeled"


class TestInstrumentSeparation:
    def test_v75_and_step_separated(self):
        rows = (
            _synthetic_campaign(n_tp=5, n_sl=5, n_timeout=3, n_no_fill=1, instrument="volatility_75_1s")
            + _synthetic_campaign(
                n_tp=1, n_sl=4, n_timeout=2, n_no_fill=1, instrument="step_index", epoch0=1_800_000_000
            )
        )
        report = analyze_evidence(rows)
        assert "volatility_75_1s" in report.instruments
        assert "step_index" in report.instruments
        assert report.instruments["volatility_75_1s"].audit.positive_count == 5
        assert report.instruments["step_index"].audit.positive_count == 1
        assert any("1 positive" in n or "descriptive" in n.lower() for n in report.instruments["step_index"].notes)

    def test_step_small_sample_limitation_in_report(self):
        rows = (
            _synthetic_campaign(n_tp=5, n_sl=5, n_timeout=0, n_no_fill=0)
            + _synthetic_campaign(
                n_tp=1, n_sl=3, n_timeout=0, n_no_fill=0, instrument="step_index", epoch0=2_000_000_000
            )
        )
        report = analyze_evidence(rows)
        assert any("step_index" in lim.lower() or "Step" in lim for lim in report.limitations)


class TestReportArtifacts:
    def test_json_schema_keys(self, tmp_path: Path):
        rows = _synthetic_campaign(n_tp=4, n_sl=6, n_timeout=3, n_no_fill=1)
        report = analyze_evidence(rows)
        json_path, md_path = write_predictive_evidence_artifacts(report, tmp_path)
        assert json_path.exists() and md_path.exists()
        data = json.loads(json_path.read_text())
        for key in (
            "schema_version",
            "audit",
            "statistical_power_notes",
            "univariate",
            "mfe",
            "model_config",
            "chronological_split",
            "pooled_oos",
            "instruments",
            "leakage_audit",
            "limitations",
            "research_conclusions",
        ):
            assert key in data
        assert data["audit"]["positive_count"] == 4
        assert "A_univariate_separation" in data["research_conclusions"]

    def test_markdown_contains_limitations_and_ceiling(self, tmp_path: Path):
        rows = _synthetic_campaign(n_tp=2, n_sl=5, n_timeout=2, n_no_fill=0)
        report = analyze_evidence(rows)
        md = format_predictive_evidence_report(report)
        assert "positive" in md.lower()
        assert "Limitations" in md
        assert "Research conclusions" in md
        assert "Scope freeze" in md

    def test_conclusions_calibrated_language(self):
        rows = _synthetic_campaign(n_tp=2, n_sl=8, n_timeout=4, n_no_fill=0)
        report = analyze_evidence(rows)
        joined = " ".join(report.research_conclusions.values()).lower()
        assert "exploratory" in joined or "insufficient" in joined or "detectable" in joined
        assert "the strategy has no edge" not in joined


class TestHelpers:
    def test_feature_group_mapping(self):
        assert feature_group("sweep_depth") == "structural"
        assert feature_group("atr") == "volatility"
        assert feature_group("fvg_size") == "fvg_displacement"
        assert feature_group("m15_bias") == "context"
        assert feature_group("risk_distance") == "trade_geometry"

    def test_spearman_rho_insufficient(self):
        rho, p = spearman_rho([1.0], [2.0])
        assert rho is None and p is None

    def test_all_feature_names_cover_geometry(self):
        assert set(GEOMETRY_FEATURE_NAMES).issubset(set(ALL_FEATURE_NAMES))
        assert set(FEATURE_NAMES).issubset(set(ALL_FEATURE_NAMES))


class TestAnalyzeEvidence:
    def test_full_pipeline_smoke(self):
        rows = (
            _synthetic_campaign(n_tp=5, n_sl=7, n_timeout=4, n_no_fill=2)
            + _synthetic_campaign(
                n_tp=1, n_sl=4, n_timeout=2, n_no_fill=1, instrument="step_index", epoch0=1_900_000_000
            )
        )
        report = analyze_evidence(rows)
        assert isinstance(report, PredictiveEvidenceReport)
        assert report.audit.positive_count == 6
        assert report.univariate_test_count == len(report.univariate)
        assert report.pooled_oos is not None
        assert len(report.leakage_audit) >= 3
        assert "H_proceed_to_6b" in report.research_conclusions
