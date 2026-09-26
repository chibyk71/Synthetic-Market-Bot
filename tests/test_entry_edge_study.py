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
    realized_r: float | None = None,
) -> EntryEdgeTradeRecord:
    if realized_r is None and outcome == "tp":
        realized_r = 2.0
    if realized_r is None and outcome == "sl":
        realized_r = -1.0
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
        realized_r=realized_r,
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
        _filled_record(
            epoch=3,
            outcome="tp",
            mtm_r_900=1.0,
            mtm_r_1800=1.0,
            mfe_r=1.0,
            mae_r=0.1,
            realized_r=1.0,
        ),
    ]
    cm = CostModel(canonical_spread_cost_r=0.05)
    acct = analyze_timeout_accounting(records, instrument="volatility_75_1s", cost_model=cm)
    assert acct.n_timeout == 2
    assert acct.n_filled == 3
    assert acct.expectancy_timeout_approx_0r is not None
    # timeout→0, tp realized_r→1.0 → mean (0+0+1)/3 = 1/3
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


# ---------------------------------------------------------------------------
# Direction normalization (fail-loud)
# ---------------------------------------------------------------------------


def test_normalize_direction_accepts_aliases() -> None:
    from smb.research.entry_edge_study import normalize_direction

    assert normalize_direction("long") == "long"
    assert normalize_direction("LONG") == "long"
    assert normalize_direction("buy") == "long"
    assert normalize_direction("short") == "short"
    assert normalize_direction("SELL") == "short"


def test_normalize_direction_rejects_unexpected() -> None:
    from smb.research.entry_edge_study import normalize_direction

    with pytest.raises(ValueError, match="unexpected direction"):
        normalize_direction("sideways")
    with pytest.raises(ValueError, match="unexpected direction"):
        normalize_direction("")


def test_compute_mtm_r_rejects_unexpected_direction() -> None:
    with pytest.raises(ValueError, match="unexpected direction"):
        compute_mtm_r(
            direction="flat", entry_price=100.0, mark_price=105.0, risk_distance=10.0
        )


# ---------------------------------------------------------------------------
# Tick-based early excursion + MTM (actual pipeline)
# ---------------------------------------------------------------------------


def test_early_excursion_long_from_ticks() -> None:
    from smb.research.entry_edge_study import compute_early_excursions_from_ticks

    entry_time = 1_000
    entry_price = 100.0
    risk = 10.0
    ticks = [
        (entry_time, 100.0),
        (entry_time + 10, 102.0),
        (entry_time + 30, 105.0),
        (entry_time + 60, 97.0),
        (entry_time + 90, 103.0),
        (entry_time + 120, 104.0),
        (entry_time + 180, 106.0),
        (entry_time + 300, 101.0),
    ]
    result = compute_early_excursions_from_ticks(
        direction="long",
        entry_price=entry_price,
        risk_distance=risk,
        entry_time=entry_time,
        ticks=ticks,
    )
    assert set(result.keys()) == set(EXCURSION_TIMEPOINTS)
    assert result[30][0] == pytest.approx(0.5)
    assert result[30][1] == pytest.approx(0.0)
    assert result[60][0] == pytest.approx(0.5)
    assert result[60][1] == pytest.approx(0.3)
    assert result[180][0] == pytest.approx(0.6)
    assert result[180][1] == pytest.approx(0.3)


def test_early_excursion_short_from_ticks() -> None:
    from smb.research.entry_edge_study import compute_early_excursions_from_ticks

    entry_time = 2_000
    ticks = [
        (entry_time, 100.0),
        (entry_time + 30, 95.0),
        (entry_time + 60, 103.0),
        (entry_time + 180, 94.0),
        (entry_time + 300, 98.0),
    ]
    result = compute_early_excursions_from_ticks(
        direction="short",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=entry_time,
        ticks=ticks,
    )
    assert result[30][0] == pytest.approx(0.5)
    assert result[30][1] == pytest.approx(0.0)
    assert result[60][0] == pytest.approx(0.5)
    assert result[60][1] == pytest.approx(0.3)
    assert result[180][0] == pytest.approx(0.6)


def test_early_excursion_exact_horizon_boundary() -> None:
    from smb.research.entry_edge_study import compute_early_excursions_from_ticks

    entry_time = 500
    ticks = [
        (entry_time, 100.0),
        (entry_time + 30, 110.0),
        (entry_time + 31, 120.0),
    ]
    result = compute_early_excursions_from_ticks(
        direction="long",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=entry_time,
        ticks=ticks,
        timepoints=(30,),
    )
    assert result[30][0] == pytest.approx(1.0)


def test_early_excursion_missing_coverage_omitted() -> None:
    from smb.research.entry_edge_study import compute_early_excursions_from_ticks

    entry_time = 1_000
    ticks = [
        (entry_time, 100.0),
        (entry_time + 20, 101.0),
        (entry_time + 50, 102.0),
    ]
    result = compute_early_excursions_from_ticks(
        direction="long",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=entry_time,
        ticks=ticks,
    )
    assert 30 in result
    assert 60 not in result
    assert 180 not in result


def test_mtm_900_boundary_from_ticks() -> None:
    from smb.research.entry_edge_study import compute_mtm_at_horizon_from_ticks

    signal_epoch = 1_000
    entry_time = 1_010
    ticks = [
        (entry_time, 100.0),
        (1_500, 102.0),
        (1_900, 108.0),
        (1_901, 200.0),
    ]
    out = compute_mtm_at_horizon_from_ticks(
        direction="long",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=entry_time,
        signal_epoch=signal_epoch,
        horizon_seconds=900,
        ticks=ticks,
    )
    assert out is not None
    mtm_r, dist = out
    assert dist == pytest.approx(8.0)
    assert mtm_r == pytest.approx(0.8)


def test_mtm_1800_exploratory_from_ticks() -> None:
    from smb.research.entry_edge_study import compute_mtm_at_horizon_from_ticks

    signal_epoch = 1_000
    entry_time = 1_010
    ticks = [
        (entry_time, 100.0),
        (1_900, 105.0),
        (2_800, 110.0),
        (2_801, 50.0),
    ]
    out = compute_mtm_at_horizon_from_ticks(
        direction="long",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=entry_time,
        signal_epoch=signal_epoch,
        horizon_seconds=1800,
        ticks=ticks,
    )
    assert out is not None
    assert out[0] == pytest.approx(1.0)


def test_mtm_missing_when_no_ticks() -> None:
    from smb.research.entry_edge_study import compute_mtm_at_horizon_from_ticks

    out = compute_mtm_at_horizon_from_ticks(
        direction="long",
        entry_price=100.0,
        risk_distance=10.0,
        entry_time=100,
        signal_epoch=100,
        horizon_seconds=900,
        ticks=[(50, 99.0)],
    )
    assert out is None


def test_compute_post_entry_diagnostics_populates_maps() -> None:
    from types import SimpleNamespace

    from smb.research.entry_edge_study import compute_post_entry_diagnostics
    from smb.simulation.models import SimulationOutcome

    entry_time = 1_010
    signal_epoch = 1_000
    candidate = SimpleNamespace(risk_distance=10.0)
    row = SimpleNamespace(
        instrument="volatility_75_1s",
        signal_epoch=signal_epoch,
        direction="long",
        accepted=True,
        outcome=SimulationOutcome.TIMEOUT,
        entry_price=100.0,
        stop_loss=90.0,
        entry_time=entry_time,
        candidate=candidate,
    )
    ticks = [(entry_time + t, 100.0 + 0.01 * t) for t in range(0, 2001)]
    early, mtm, audit = compute_post_entry_diagnostics(
        [row],
        {"volatility_75_1s": ticks},
    )
    key = ("volatility_75_1s", signal_epoch, "long")
    assert key in early
    assert set(early[key].keys()) == set(EXCURSION_TIMEPOINTS)
    assert key in mtm
    assert mtm[key]["mtm_r_900"] is not None
    assert mtm[key]["mtm_r_1800"] is not None
    assert audit["n_filled_for_diagnostics"] == 1
    assert audit["n_excursion_complete"] == 1
    assert audit["n_mtm_900_observed"] == 1


def test_compute_post_entry_skips_no_fill() -> None:
    from types import SimpleNamespace

    from smb.research.entry_edge_study import compute_post_entry_diagnostics
    from smb.simulation.models import SimulationOutcome

    row = SimpleNamespace(
        instrument="volatility_75_1s",
        signal_epoch=1,
        direction="long",
        accepted=True,
        outcome=SimulationOutcome.NO_FILL,
        entry_price=None,
        stop_loss=None,
        entry_time=None,
        candidate=None,
    )
    early, mtm, audit = compute_post_entry_diagnostics(
        [row], {"volatility_75_1s": [(1, 100.0)]}
    )
    assert early == {}
    assert mtm == {}
    assert audit["n_filled_for_diagnostics"] == 0


def test_write_and_load_cohort_keys(tmp_path: Path) -> None:
    from smb.research.entry_edge_study import load_cohort_keys, write_cohort_keys

    keys = [
        CohortKey("volatility_75_1s", 100, "long"),
        CohortKey("step_index", 200, "short"),
    ]
    path = write_cohort_keys(keys, tmp_path / "cohort.json")
    loaded = load_cohort_keys(path)
    assert [(k.instrument, k.signal_epoch, k.direction) for k in loaded] == [
        ("volatility_75_1s", 100, "long"),
        ("step_index", 200, "short"),
    ]


def test_load_cohort_keys_missing_raises(tmp_path: Path) -> None:
    from smb.research.entry_edge_study import load_cohort_keys

    with pytest.raises(FileNotFoundError, match="frozen 6B cohort"):
        load_cohort_keys(tmp_path / "does_not_exist.json")


def test_cohort_includes_no_fill_in_integrity() -> None:
    filled = _filled_record(epoch=1, outcome="timeout")
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
    keys = [filled.cohort_key, no_fill.cohort_key]
    audit = verify_cohort_integrity(keys, keys)
    assert audit["n_baseline"] == 2
    acct = analyze_timeout_accounting(
        [filled, no_fill],
        instrument="volatility_75_1s",
        cost_model=CostModel(canonical_spread_cost_r=0.05),
    )
    assert acct.n_filled == 1
    assert acct.n_no_fill == 1


def test_timeout_accounting_uses_realized_r_not_flat_1r() -> None:
    rec = _filled_record(
        epoch=1, outcome="tp", mtm_r_900=None, mtm_r_1800=None, realized_r=2.0
    )
    acct = analyze_timeout_accounting(
        [rec],
        instrument="volatility_75_1s",
        cost_model=CostModel(canonical_spread_cost_r=0.05),
    )
    assert acct.expectancy_timeout_approx_0r == pytest.approx(2.0)


def test_run_entry_edge_study_on_results_requires_injected_diagnostics() -> None:
    from types import SimpleNamespace

    from smb.research.entry_edge_study import run_entry_edge_study_on_results
    from smb.research.experiment import ExperimentResult, ExperimentSummary
    from smb.simulation.models import SimulationOutcome

    row = _row(
        epoch=1_700_000_000,
        outcome=SimulationOutcome.TIMEOUT,
        entry_time=1_700_000_010,
        exit_time=1_700_000_910,
        direction="long",
    )
    summary = ExperimentSummary(
        instrument="volatility_75_1s",
        start_epoch=None,
        end_epoch=None,
        ticks_processed=100,
        m1_candles=10,
        m15_candles=1,
        signals=1,
        candidates_accepted=1,
        candidates_rejected=0,
        outcomes={"timeout": 1},
        win_rate=None,
        average_r=None,
        total_r=None,
        average_duration_seconds=900.0,
        average_mae=None,
        average_mfe=None,
    )
    result = ExperimentResult(
        config=SimpleNamespace(),
        summary=summary,
        rows=(row,),
        simulations=(),
        metrics=(),
        validation=None,
    )
    report_empty = run_entry_edge_study_on_results(
        {"volatility_75_1s": result},
        baseline_keys=[CohortKey("volatility_75_1s", 1_700_000_000, "long")],
    )
    assert all(c.n == 0 for c in report_empty.instruments[0].excursion_cells)

    key = ("volatility_75_1s", 1_700_000_000, "long")
    early = {key: {tp: (0.4, 0.2) for tp in EXCURSION_TIMEPOINTS}}
    mtm = {
        key: {
            "mtm_r_900": 0.1,
            "mtm_price_distance_900": 1.0,
            "mtm_r_1800": 0.2,
            "mtm_price_distance_1800": 2.0,
        }
    }
    report = run_entry_edge_study_on_results(
        {"volatility_75_1s": result},
        baseline_keys=[CohortKey("volatility_75_1s", 1_700_000_000, "long")],
        early_excursions_by_key=early,
        mtm_by_key=mtm,
    )
    assert any(c.n > 0 for c in report.instruments[0].excursion_cells)
    assert report.instruments[0].timeout_accounting.mtm_r_900_summary is not None
    assert report.instruments[0].timeout_accounting.mtm_r_900_summary.n == 1


def test_cli_pipeline_tick_fixture_integration(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from smb.data.models import StoredTick
    from smb.data.repository import TickRepository
    from smb.data.store import ParquetTickStore
    from smb.research.entry_edge_study import (
        compute_post_entry_diagnostics,
        load_cohort_keys,
        run_entry_edge_study_on_results,
        write_cohort_keys,
    )
    from smb.research.experiment import ExperimentResult, ExperimentSummary
    from smb.simulation.models import SimulationOutcome

    instrument = "volatility_75_1s"
    signal_epoch = 1_700_000_000
    entry_time = signal_epoch + 10
    store = ParquetTickStore(tmp_path / "dataset")
    ticks = [
        StoredTick(instrument=instrument, epoch=entry_time + t, price=100.0 + 0.005 * t)
        for t in range(0, 2001)
    ]
    store.write_ticks(ticks)
    store.reindex_source_order(instrument)

    candidate = SimpleNamespace(risk_distance=10.0)
    base_row = _row(
        instrument=instrument,
        epoch=signal_epoch,
        outcome=SimulationOutcome.TIMEOUT,
        entry_time=entry_time,
        exit_time=signal_epoch + 910,
        direction="long",
    )
    row = SimpleNamespace(
        instrument=instrument,
        signal_epoch=signal_epoch,
        direction="long",
        accepted=True,
        rejection_reason=None,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        risk_reward=2.0,
        risk_amount=100.0,
        outcome=SimulationOutcome.TIMEOUT,
        entry_time=entry_time,
        exit_time=signal_epoch + 910,
        duration_seconds=900,
        realized_r=None,
        mfe=5.0,
        mae=2.0,
        signal=base_row.signal,
        candidate=candidate,
        simulation=None,
        metrics=None,
    )
    summary = ExperimentSummary(
        instrument=instrument,
        start_epoch=None,
        end_epoch=None,
        ticks_processed=len(ticks),
        m1_candles=1,
        m15_candles=1,
        signals=1,
        candidates_accepted=1,
        candidates_rejected=0,
        outcomes={"timeout": 1},
        win_rate=None,
        average_r=None,
        total_r=None,
        average_duration_seconds=900.0,
        average_mae=None,
        average_mfe=None,
    )
    result = ExperimentResult(
        config=SimpleNamespace(),
        summary=summary,
        rows=(row,),  # type: ignore[arg-type]
        simulations=(),
        metrics=(),
        validation=None,
    )
    cohort_path = write_cohort_keys(
        [CohortKey(instrument, signal_epoch, "long")],
        tmp_path / "frozen_6b_cohort.json",
        source="test_fixture",
    )
    frozen = load_cohort_keys(cohort_path)
    repo = TickRepository(store)
    stored = repo.get_ticks(instrument)
    tick_pairs = [(t.epoch, t.price) for t in stored]
    early, mtm, audit = compute_post_entry_diagnostics(
        [row],  # type: ignore[list-item]
        {instrument: tick_pairs},
    )
    assert audit["n_filled_for_diagnostics"] == 1
    assert audit["n_excursion_complete"] == 1
    assert audit["n_mtm_900_observed"] == 1
    assert audit["n_mtm_1800_observed"] == 1
    key = (instrument, signal_epoch, "long")
    assert key in early and len(early[key]) == 6
    assert mtm[key]["mtm_r_900"] is not None
    assert mtm[key]["mtm_r_1800"] is not None

    report = run_entry_edge_study_on_results(
        {instrument: result},
        baseline_keys=frozen,
        early_excursions_by_key=early,
        mtm_by_key=mtm,
        coverage_audit=audit,
    )
    assert report.cohort_integrity["status"] == "ok"
    ir = report.instruments[0]
    assert any(c.n > 0 for c in ir.excursion_cells)
    primary = [c for c in ir.excursion_cells if c.timepoint_seconds == 180]
    assert primary and primary[0].n == 1
    assert ir.timeout_accounting.mtm_r_900_summary is not None
    assert ir.timeout_accounting.mtm_r_900_summary.n == 1
    out_dir = tmp_path / "out"
    path, _ = write_entry_edge_study_artifacts(report, out_dir)
    data = json.loads(path.read_text())
    assert data["dataset_audit"]["post_entry_coverage"]["n_excursion_complete"] == 1
    cells = data["instruments"][0]["excursion_cells"]
    assert any(c["n"] > 0 for c in cells)
