"""Tests for Milestone 6C — Entry Edge / Early Excursion Study."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest  # type: ignore[import-not-found]

from smb.research.entry_edge_study import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    DEFAULT_CANONICAL_SPREAD_COST_R,
    EXCURSION_TIMEPOINTS,
    EXTENDED_CROSS_MARK_SECONDS,
    FROZEN_BASELINE_HORIZON_SECONDS,
    PLAUSIBLE_EFFECT_MEDIAN_DIFF_R,
    PRIMARY_ENDPOINT_TIMEPOINT,
    STUDY_VERSION,
    VERDICT_CONDITIONAL_EDGE,
    VERDICT_EDGE_DETECTED,
    VERDICT_NO_EDGE,
    VERDICT_UNDERPOWERED,
    BootstrapResult,
    CohortIntegrityError,
    CohortKey,
    CostModel,
    DirectionBootstrapResult,
    EntryEdgeTradeRecord,
    MDEResult,
    analyze_early_excursions,
    analyze_entry_edge_study,
    analyze_instrument_entry_edge,
    analyze_timeout_accounting,
    bootstrap_primary_endpoint,
    compute_mtm_r,
    determine_verdict,
    estimate_mde,
    format_entry_edge_study_report,
    leakage_review_notes,
    record_from_row,
    verify_cohort_integrity,
    write_entry_edge_study_artifacts,
)
from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction
from smb.trade.models import RejectionReason

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


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
    outcome: SimulationOutcome | None = SimulationOutcome.TIMEOUT,
    entry_price: float | None = 100.0,
    stop_loss: float | None = 90.0,
    take_profit: float | None = 120.0,
    risk_distance: float = 10.0,
    reward_distance: float = 20.0,
    mfe: float | None = 5.0,
    mae: float | None = 3.0,
    duration: int | None = 900,
    entry_time: int | None = 1_700_000_010,
    exit_time: int | None = 1_700_000_910,
    rejection_reason: RejectionReason | None = None,
):
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
        object,  # TradeExperimentRow-like
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


def _make_excursions(
    mfe_r: float,
    mae_r: float,
    timepoints: tuple[int, ...] = EXCURSION_TIMEPOINTS,
) -> dict[int, tuple[float, float]]:
    return {tp: (mfe_r, mae_r) for tp in timepoints}


def _filled_record(
    *,
    instrument: str = "volatility_75_1s",
    epoch: int = 1_700_000_000,
    direction: str = "long",
    outcome: str = "timeout",
    mfe_r: float = 0.4,
    mae_r: float = 0.2,
    mtm_r_900: float | None = 0.1,
    mtm_r_1800: float | None = 0.15,
    risk: float = 10.0,
    entry: float = 100.0,
) -> EntryEdgeTradeRecord:
    return EntryEdgeTradeRecord(
        instrument=instrument,
        direction=direction,
        signal_epoch=epoch,
        outcome=outcome,
        filled=True,
        entry_price=entry,
        stop_price=entry - risk if direction == "long" else entry + risk,
        target_price=entry + 2 * risk if direction == "long" else entry - 2 * risk,
        risk_distance=risk,
        entry_time=epoch + 10,
        exit_time=epoch + 910,
        duration_seconds=900,
        mfe=mfe_r * risk,
        mae=mae_r * risk,
        mtm_r_900=mtm_r_900,
        mtm_price_distance_900=(mtm_r_900 * risk) if mtm_r_900 is not None else None,
        mtm_r_1800=mtm_r_1800,
        mtm_price_distance_1800=(mtm_r_1800 * risk) if mtm_r_1800 is not None else None,
        early_excursions=_make_excursions(mfe_r, mae_r),
        exclusion_reason=None,
    )


# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------


def test_study_version_and_constants() -> None:
    assert STUDY_VERSION == "6c.1"
    assert FROZEN_BASELINE_HORIZON_SECONDS == 900
    assert EXTENDED_CROSS_MARK_SECONDS == 1800
    assert PRIMARY_ENDPOINT_TIMEPOINT == 180
    assert EXCURSION_TIMEPOINTS == (30, 60, 90, 120, 180, 300)
    assert PLAUSIBLE_EFFECT_MEDIAN_DIFF_R == 0.20
    assert BOOTSTRAP_RESAMPLES == 10_000
    assert BOOTSTRAP_SEED == 6_031_800


# ---------------------------------------------------------------------------
# MTM computation
# ---------------------------------------------------------------------------


def test_compute_mtm_r_long() -> None:
    mtm_r, dist = compute_mtm_r(
        direction="long", entry_price=100.0, mark_price=105.0, risk_distance=10.0
    )
    assert mtm_r == pytest.approx(0.5)
    assert dist == pytest.approx(5.0)


def test_compute_mtm_r_short() -> None:
    mtm_r, dist = compute_mtm_r(
        direction="short", entry_price=100.0, mark_price=95.0, risk_distance=10.0
    )
    assert mtm_r == pytest.approx(0.5)
    assert dist == pytest.approx(5.0)


def test_compute_mtm_r_adverse_long() -> None:
    mtm_r, dist = compute_mtm_r(
        direction="long", entry_price=100.0, mark_price=92.0, risk_distance=10.0
    )
    assert mtm_r == pytest.approx(-0.8)
    assert dist == pytest.approx(-8.0)


def test_compute_mtm_r_rejects_bad_risk() -> None:
    with pytest.raises(ValueError, match="risk_distance"):
        compute_mtm_r(direction="long", entry_price=100.0, mark_price=105.0, risk_distance=0.0)


# ---------------------------------------------------------------------------
# Spread cost model
# ---------------------------------------------------------------------------


def test_cost_model_canonical_and_sensitivity() -> None:
    cm = CostModel(canonical_spread_cost_r=0.05)
    assert cm.cost_r(0.0) == pytest.approx(0.05)
    assert cm.cost_r(-0.50) == pytest.approx(0.025)
    assert cm.cost_r(0.50) == pytest.approx(0.075)
    d = cm.to_dict()
    assert d["canonical_spread_cost_r"] == 0.05
    assert -0.5 in d["sensitivities"]


def test_default_canonical_spread_cost() -> None:
    assert DEFAULT_CANONICAL_SPREAD_COST_R == 0.05


# ---------------------------------------------------------------------------
# NO_FILL exclusion
# ---------------------------------------------------------------------------


def test_no_fill_excluded_from_timeout_accounting() -> None:
    filled = _filled_record(epoch=1, outcome="timeout", mtm_r_900=0.2)
    no_fill = EntryEdgeTradeRecord(
        instrument="volatility_75_1s",
        direction="long",
        signal_epoch=2,
        outcome="no_fill",
        filled=False,
        entry_price=None,
        stop_price=None,
        target_price=None,
        risk_distance=None,
        entry_time=None,
        exit_time=None,
        duration_seconds=None,
        exclusion_reason="no_fill",
    )
    acct = analyze_timeout_accounting(
        [filled, no_fill],
        instrument="volatility_75_1s",
        cost_model=CostModel(canonical_spread_cost_r=0.05),
    )
    assert acct.n_filled == 1
    assert acct.n_no_fill == 1
    assert acct.n_timeout == 1
    assert acct.expectancy_canonical_900_mtm == pytest.approx(0.2)


def test_record_from_row_no_fill() -> None:
    row = _row(outcome=SimulationOutcome.NO_FILL, accepted=True, entry_price=None)
    rec = record_from_row(row)
    assert rec.filled is False
    assert rec.exclusion_reason == "no_fill"


# ---------------------------------------------------------------------------
# Timeout MTM / expectancy scenarios
# ---------------------------------------------------------------------------


def test_timeout_accounting_scenarios() -> None:
    records = [
        _filled_record(epoch=1, outcome="timeout", mtm_r_900=0.1, mtm_r_1800=0.3),
        _filled_record(epoch=2, outcome="timeout", mtm_r_900=-0.2, mtm_r_1800=-0.1),
        _filled_record(epoch=3, outcome="tp", mtm_r_900=1.0, mtm_r_1800=1.0, mfe_r=1.0, mae_r=0.1),
    ]
    cm = CostModel(canonical_spread_cost_r=0.05)
    acct = analyze_timeout_accounting(records, instrument="volatility_75_1s", cost_model=cm)
    assert acct.n_timeout == 2
    assert acct.n_filled == 3
    assert acct.expectancy_timeout_approx_0r is not None
    # timeout→0, tp→1.0 → mean (0+0+1)/3 = 1/3
    assert acct.expectancy_timeout_approx_0r == pytest.approx(1.0 / 3.0)
    assert acct.expectancy_canonical_900_mtm is not None
    # cost scenarios present
    assert "spread_cost_sens_+0.00" in acct.expectancy_by_spread_cost or any(
        "0.00" in k for k in acct.expectancy_by_spread_cost
    )


# ---------------------------------------------------------------------------
# Early excursion — all six timepoints, direction normalization, reach
# ---------------------------------------------------------------------------


def test_all_six_excursion_timepoints() -> None:
    records = [
        _filled_record(epoch=i, direction="long", mfe_r=0.5, mae_r=0.2)
        for i in range(5)
    ] + [
        _filled_record(epoch=100 + i, direction="short", mfe_r=0.3, mae_r=0.1)
        for i in range(5)
    ]
    cells = analyze_early_excursions(records, instrument="volatility_75_1s")
    tps = {(c.direction, c.timepoint_seconds) for c in cells}
    for d in ("long", "short"):
        for tp in EXCURSION_TIMEPOINTS:
            assert (d, tp) in tps
    # explicit n
    for c in cells:
        assert c.n == 5
        assert c.mfe_r.n == 5
        assert c.mae_r.n == 5
        assert c.excursion_diff_r.n == 5


def test_excursion_diff_is_mfe_minus_mae() -> None:
    records = [_filled_record(epoch=1, mfe_r=0.6, mae_r=0.2)]
    cells = analyze_early_excursions(records, instrument="volatility_75_1s")
    primary = [c for c in cells if c.timepoint_seconds == 180 and c.direction == "long"]
    assert len(primary) == 1
    assert primary[0].excursion_diff_r.median == pytest.approx(0.4)


def test_reach_fractions() -> None:
    # All reach +0.5R favorable, none reach −0.5R adverse of 0.5
    records = [
        _filled_record(epoch=i, mfe_r=0.6, mae_r=0.1) for i in range(10)
    ]
    cells = analyze_early_excursions(records, instrument="volatility_75_1s")
    cell = next(c for c in cells if c.timepoint_seconds == 180 and c.direction == "long")
    fav_05 = next(r for r in cell.reach_fractions if r.threshold_r == 0.5 and r.side == "favorable")
    adv_05 = next(r for r in cell.reach_fractions if r.threshold_r == 0.5 and r.side == "adverse")
    assert fav_05.n_reached == 10
    assert fav_05.fraction == pytest.approx(1.0)
    assert adv_05.n_reached == 0
    assert adv_05.fraction == pytest.approx(0.0)


def test_primary_endpoint_flag() -> None:
    records = [_filled_record(epoch=1)]
    cells = analyze_early_excursions(records, instrument="volatility_75_1s")
    for c in cells:
        assert c.is_primary_endpoint == (c.timepoint_seconds == 180)


def test_empty_excursions_still_emit_cells_with_n_zero() -> None:
    # Filled but no early_excursions populated
    rec = EntryEdgeTradeRecord(
        instrument="volatility_75_1s",
        direction="long",
        signal_epoch=1,
        outcome="timeout",
        filled=True,
        entry_price=100.0,
        stop_price=90.0,
        target_price=120.0,
        risk_distance=10.0,
        entry_time=10,
        exit_time=910,
        duration_seconds=900,
        early_excursions={},
    )
    cells = analyze_early_excursions([rec], instrument="volatility_75_1s")
    assert len(cells) == len(EXCURSION_TIMEPOINTS)
    for c in cells:
        assert c.n == 0


# ---------------------------------------------------------------------------
# Cohort integrity
# ---------------------------------------------------------------------------


def test_cohort_match_ok() -> None:
    keys = [
        CohortKey("volatility_75_1s", 1, "long"),
        CohortKey("step_index", 2, "short"),
    ]
    audit = verify_cohort_integrity(keys, keys)
    assert audit["status"] == "ok"
    assert audit["n_shared"] == 2


def test_cohort_missing_raises() -> None:
    base = [CohortKey("volatility_75_1s", 1, "long"), CohortKey("volatility_75_1s", 2, "long")]
    study = [CohortKey("volatility_75_1s", 1, "long")]
    with pytest.raises(CohortIntegrityError, match="missing"):
        verify_cohort_integrity(base, study)


def test_cohort_extra_raises() -> None:
    base = [CohortKey("volatility_75_1s", 1, "long")]
    study = [
        CohortKey("volatility_75_1s", 1, "long"),
        CohortKey("volatility_75_1s", 99, "long"),
    ]
    with pytest.raises(CohortIntegrityError, match="additional"):
        verify_cohort_integrity(base, study)


def test_cohort_duplicate_raises() -> None:
    keys = [
        CohortKey("volatility_75_1s", 1, "long"),
        CohortKey("volatility_75_1s", 1, "long"),
    ]
    with pytest.raises(CohortIntegrityError, match="duplicate"):
        verify_cohort_integrity(keys, keys)


def test_cohort_direction_change_raises() -> None:
    base = [CohortKey("volatility_75_1s", 1, "long")]
    study = [CohortKey("volatility_75_1s", 1, "long")]
    with pytest.raises(CohortIntegrityError, match="changed directions"):
        verify_cohort_integrity(
            base,
            study,
            baseline_directions={("volatility_75_1s", 1, "long"): "long"},
            study_directions={("volatility_75_1s", 1, "long"): "short"},
        )


# ---------------------------------------------------------------------------
# Bootstrap reproducibility
# ---------------------------------------------------------------------------


def test_bootstrap_reproducible() -> None:
    records = [
        _filled_record(epoch=i, direction="long", mfe_r=0.5 + 0.01 * i, mae_r=0.2)
        for i in range(20)
    ] + [
        _filled_record(epoch=100 + i, direction="short", mfe_r=0.4, mae_r=0.15)
        for i in range(15)
    ]
    a = bootstrap_primary_endpoint(records, instrument="volatility_75_1s")
    b = bootstrap_primary_endpoint(records, instrument="volatility_75_1s")
    assert a.observed_median == b.observed_median
    assert a.ci_low == b.ci_low
    assert a.ci_high == b.ci_high
    assert a.seed == BOOTSTRAP_SEED
    assert a.n_resamples == BOOTSTRAP_RESAMPLES
    assert a.stratified_by == "direction"


def test_bootstrap_empty() -> None:
    r = bootstrap_primary_endpoint([], instrument="volatility_75_1s")
    assert r.n_total == 0
    assert r.observed_median is None
    assert r.ci_low is None


# ---------------------------------------------------------------------------
# MDE
# ---------------------------------------------------------------------------


def test_mde_calculation() -> None:
    diffs = [0.1, 0.2, 0.15, -0.05, 0.3, 0.25, 0.0, 0.4, -0.1, 0.2] * 3
    mde = estimate_mde(
        instrument="volatility_75_1s",
        sample_size=len(diffs),
        assumed_effect=0.2,
        observed_diffs=diffs,
    )
    assert mde.sample_size == len(diffs)
    assert mde.assumed_effect == 0.2
    assert mde.estimated_mde is not None
    assert mde.estimated_mde > 0
    assert mde.can_detect_assumed is not None


def test_mde_zero_sample() -> None:
    mde = estimate_mde(instrument="x", sample_size=0)
    assert mde.estimated_mde is None
    assert mde.can_detect_assumed is None


# ---------------------------------------------------------------------------
# Verdict gates
# ---------------------------------------------------------------------------


def _boot(
    *,
    excludes_zero: bool,
    positive: bool,
    observed: float | None = 0.3,
    lo: float | None = 0.1,
    hi: float | None = 0.5,
    n: int = 50,
) -> BootstrapResult:
    return BootstrapResult(
        instrument="volatility_75_1s",
        timepoint_seconds=180,
        n_total=n,
        n_long=n // 2,
        n_short=n - n // 2,
        observed_median=observed,
        ci_low=lo,
        ci_high=hi,
        excludes_zero=excludes_zero,
        positive=positive,
        n_resamples=BOOTSTRAP_RESAMPLES,
        seed=BOOTSTRAP_SEED,
        stratified_by="direction",
        notes=(),
    )


def _mde(*, mde_val: float, assumed: float = 0.2) -> MDEResult:
    return MDEResult(
        instrument="volatility_75_1s",
        sample_size=50,
        assumed_effect=assumed,
        estimated_mde=mde_val,
        can_detect_assumed=mde_val <= assumed,
        methodology="test",
        assumptions=(),
        notes=(),
    )


def test_verdict_edge_detected() -> None:
    v, _ = determine_verdict(
        _boot(excludes_zero=True, positive=True, lo=0.1, hi=0.5),
        _mde(mde_val=0.1),
        (),
    )
    assert v == VERDICT_EDGE_DETECTED


def test_verdict_underpowered() -> None:
    v, _ = determine_verdict(
        _boot(excludes_zero=False, positive=False, lo=-0.2, hi=0.3, observed=0.05),
        _mde(mde_val=0.5),  # MDE > 0.2
        (),
    )
    assert v == VERDICT_UNDERPOWERED


def test_verdict_no_edge() -> None:
    v, _ = determine_verdict(
        _boot(excludes_zero=False, positive=False, lo=-0.1, hi=0.15, observed=0.02),
        _mde(mde_val=0.1),  # MDE ≤ 0.2
        (),
    )
    assert v == VERDICT_NO_EDGE


def test_verdict_conditional_edge() -> None:
    dir_results = (
        DirectionBootstrapResult(
            instrument="volatility_75_1s",
            direction="long",
            n=30,
            observed_median=0.4,
            ci_low=0.1,
            ci_high=0.7,
            excludes_zero=True,
            positive=True,
        ),
        DirectionBootstrapResult(
            instrument="volatility_75_1s",
            direction="short",
            n=30,
            observed_median=0.05,
            ci_low=-0.2,
            ci_high=0.3,
            excludes_zero=False,
            positive=False,
        ),
    )
    v, _ = determine_verdict(
        _boot(excludes_zero=False, positive=False, lo=-0.05, hi=0.25, observed=0.1),
        _mde(mde_val=0.15),
        dir_results,
    )
    assert v == VERDICT_CONDITIONAL_EDGE


# ---------------------------------------------------------------------------
# Full instrument analysis + report
# ---------------------------------------------------------------------------


def test_analyze_instrument_entry_edge() -> None:
    records = [
        _filled_record(epoch=i, direction="long", mfe_r=0.5, mae_r=0.15, mtm_r_900=0.2)
        for i in range(12)
    ] + [
        _filled_record(epoch=100 + i, direction="short", mfe_r=0.45, mae_r=0.2, mtm_r_900=0.1)
        for i in range(10)
    ]
    result = analyze_instrument_entry_edge(records, instrument="volatility_75_1s")
    assert result.n_filled == 22
    assert result.bootstrap_primary.n_total == 22
    assert result.verdict in {
        VERDICT_EDGE_DETECTED,
        VERDICT_CONDITIONAL_EDGE,
        VERDICT_UNDERPOWERED,
        VERDICT_NO_EDGE,
    }
    assert len(result.excursion_cells) == 2 * len(EXCURSION_TIMEPOINTS)


def test_analyze_entry_edge_study_and_artifacts(tmp_path: Path) -> None:
    records = [
        _filled_record(
            instrument="volatility_75_1s",
            epoch=i,
            direction="long" if i % 2 == 0 else "short",
            mfe_r=0.35,
            mae_r=0.2,
        )
        for i in range(20)
    ] + [
        _filled_record(
            instrument="step_index",
            epoch=1000 + i,
            direction="long" if i % 2 == 0 else "short",
            mfe_r=0.1,
            mae_r=0.3,
        )
        for i in range(16)
    ]
    report = analyze_entry_edge_study(records)
    assert report.study_version == STUDY_VERSION
    assert report.production_execution_unchanged is True
    assert len(report.instruments) == 2
    assert len(report.leakage_review) >= 6

    json_path, md_path = write_entry_edge_study_artifacts(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["study_version"] == STUDY_VERSION
    assert "configuration" in data
    assert "dataset_audit" in data
    assert "cohort_integrity" in data
    assert "cost_model" in data
    assert "instruments" in data
    assert "leakage_review" in data
    assert "verdict" in data["instruments"][0]

    md = md_path.read_text(encoding="utf-8")
    for section in (
        "## 1. Scope",
        "## 2. Configuration",
        "## 3. Dataset audit",
        "## 4. Per-instrument results",
        "## 5. Timeout/cost scenarios",
        "## 6. Early-excursion results",
        "## 7. Statistical analysis",
        "## 8. Power/MDE",
        "## 9. Statistical limitations",
        "## 10. Leakage review",
        "## 11. Research conclusions",
        "## 12. Verdict",
    ):
        assert section in md


def test_report_deterministic() -> None:
    records = [
        _filled_record(epoch=i, mfe_r=0.4, mae_r=0.2) for i in range(8)
    ]
    r1 = analyze_entry_edge_study(records, instruments=["volatility_75_1s"])
    r2 = analyze_entry_edge_study(records, instruments=["volatility_75_1s"])
    assert format_entry_edge_study_report(r1) == format_entry_edge_study_report(r2)


def test_leakage_notes_cover_required_points() -> None:
    notes = " ".join(leakage_review_notes()).lower()
    assert "post-entry" in notes
    assert "pre-entry" in notes
    assert "signal gate" in notes
    assert "6b" in notes or "cohort" in notes
    assert "1800" in notes
    assert "exploratory" in notes


def test_cohort_integrity_in_full_study() -> None:
    records = [_filled_record(epoch=1), _filled_record(epoch=2)]
    base_keys = [r.cohort_key for r in records]
    # mismatch
    bad_base = [CohortKey("volatility_75_1s", 1, "long"), CohortKey("volatility_75_1s", 99, "long")]
    with pytest.raises(CohortIntegrityError):
        analyze_entry_edge_study(records, baseline_keys=bad_base)

    report = analyze_entry_edge_study(
        records, baseline_keys=base_keys, instruments=["volatility_75_1s"]
    )
    assert report.cohort_integrity["status"] == "ok"


# ---------------------------------------------------------------------------
# Boundary / direction normalization consistency
# ---------------------------------------------------------------------------


def test_direction_normalization_short() -> None:
    # For short, favorable is price down; mfe_r/mae_r already direction-normalized
    rec = _filled_record(direction="short", mfe_r=0.7, mae_r=0.1)
    cells = analyze_early_excursions([rec], instrument="volatility_75_1s")
    short_cells = [c for c in cells if c.direction == "short"]
    assert short_cells
    assert short_cells[0].mfe_r.median == pytest.approx(0.7)
    assert short_cells[0].mae_r.median == pytest.approx(0.1)
    assert short_cells[0].excursion_diff_r.median == pytest.approx(0.6)


def test_json_schema_minimum_keys(tmp_path: Path) -> None:
    records = [_filled_record(epoch=i) for i in range(5)]
    report = analyze_entry_edge_study(records, instruments=["volatility_75_1s"])
    path, _ = write_entry_edge_study_artifacts(report, tmp_path)
    data = json.loads(path.read_text())
    required = {
        "study_version",
        "configuration",
        "dataset_audit",
        "cohort_integrity",
        "cost_model",
        "instruments",
        "leakage_review",
        "research_conclusions",
        "production_execution_unchanged",
    }
    assert required.issubset(data.keys())
    inst = data["instruments"][0]
    for key in (
        "timeout_accounting",
        "excursion_cells",
        "bootstrap_primary",
        "mde",
        "verdict",
        "verdict_evidence",
    ):
        assert key in inst

