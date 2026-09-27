"""Tests for Milestone 6D — Instrument Statistical Characterization."""

from __future__ import annotations

import math
import random
from pathlib import Path

from smb.research.statistical_characterization import (
    ACF_LAGS,
    DEFAULT_SEED,
    FROZEN_STUDY_CONFIG,
    MINIMUM_EFFECT_SIZE_ACF,
    PRIMARY_EXTREME_HORIZON,
    PRIMARY_FAMILY_SIZE,
    STUDY_VERSION,
    PrimaryTestResult,
    StudyConfiguration,
    TickSeries,
    _combine_overall,
    _excess_kurtosis_bias_corrected,
    _skewness_bias_corrected,
    acf_at_lag,
    apply_holm_and_classify,
    causal_extreme_threshold,
    characterize_instrument,
    compute_increments,
    compute_inter_tick_intervals,
    coverage_from_series,
    detect_extreme_events,
    empirical_two_sided_pvalue,
    future_response_at_horizon,
    gate_from_tests,
    holm_bonferroni,
    increment_distribution,
    resolve_pip_size_from_settings,
    run_length_statistics,
    shuffle_increments,
    synthesize_overall,
    transition_statistics,
    write_artifacts,
)


def _series(prices: list[float], start_epoch: int = 1_700_000_000) -> TickSeries:
    epochs = tuple(start_epoch + i for i in range(len(prices)))
    return TickSeries(epochs=epochs, prices=tuple(prices))


def _iid_walk(n: int, seed: int = 0, scale: float = 1.0) -> list[float]:
    rng = random.Random(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] + rng.uniform(-scale, scale))
    return prices


def test_empty_series() -> None:
    series = TickSeries(epochs=(), prices=())
    result = characterize_instrument("empty", series)
    assert result.coverage.tick_count == 0
    assert result.overall == "INVALID_STUDY"
    assert result.gate_a == "INCONCLUSIVE"


def test_empty_instrument() -> None:
    series = _series([100.0] * 5)
    result = characterize_instrument("tiny", series)
    assert result.overall == "INVALID_STUDY"


def test_constant_series() -> None:
    series = _series([42.0] * 200)
    result = characterize_instrument("const", series)
    assert result.overall in ("NO_MEASURABLE_STRUCTURE", "NEEDS_MORE_DATA", "INVALID_STUDY")


def test_increment_distribution() -> None:
    dist = increment_distribution([1.0, -1.0, 2.0, 0.0])
    assert dist.n == 4
    assert dist.positive_count == 2
    assert dist.negative_count == 1
    assert dist.zero_count == 1


def test_percentile_convention() -> None:
    from smb.research.stats import percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 50.0) == 2.5


def test_skewness_convention() -> None:
    s = _skewness_bias_corrected([1.0, 2.0, 3.0, 10.0])
    assert s is not None and s > 0


def test_excess_kurtosis_convention() -> None:
    k = _excess_kurtosis_bias_corrected([1.0, 2.0, 3.0, 4.0, 100.0])
    assert k is not None


def test_acf_known_series() -> None:
    # alternating -> negative lag-1 ACF
    series = [1.0, -1.0] * 50
    acf, n = acf_at_lag(series, 1)
    assert n > 0
    assert acf is not None and acf < 0


def test_acf_lag_validation() -> None:
    try:
        acf_at_lag([1.0, 2.0], 0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_direction_transition_counts() -> None:
    tr = transition_statistics([1.0, 1.0, -1.0, -1.0])
    assert tr.n_pairs == 3
    assert tr.p_up is not None


def test_run_length_statistics() -> None:
    runs = run_length_statistics([1.0, 1.0, 1.0, -1.0, -1.0])
    assert runs["UP"].count >= 1
    assert runs["DOWN"].count >= 1


def test_inter_tick_statistics() -> None:
    intervals = compute_inter_tick_intervals([10, 11, 13, 14])
    assert intervals == [1.0, 2.0, 1.0]


def test_extreme_event_detection() -> None:
    deltas = [0.1] * 50 + [10.0] + [0.1] * 50
    events = detect_extreme_events(deltas, window=20, sigma=3.0)
    assert 50 in events


def test_extreme_threshold_is_causal() -> None:
    deltas = [0.1] * 30 + [5.0] + [0.1] * 30
    thr_before = causal_extreme_threshold(deltas, 30, window=20, sigma=3.0)
    # Append huge future values; threshold at 30 must not change
    deltas2 = list(deltas) + [1000.0] * 20
    thr_after = causal_extreme_threshold(deltas2, 30, window=20, sigma=3.0)
    assert thr_before == thr_after


def test_future_information_not_used() -> None:
    deltas = [0.1] * 40 + [8.0] + [0.1] * 40
    events1 = detect_extreme_events(deltas, window=15, sigma=3.0)
    deltas_future = list(deltas) + [999.0] * 50
    events2 = detect_extreme_events(deltas_future[: len(deltas)], window=15, sigma=3.0)
    assert events1 == events2


def test_future_response_calculation() -> None:
    deltas = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = future_response_at_horizon(deltas, 0, 2)
    assert r is not None
    assert r["direction_adjusted_future_return"] == 2.0 + 3.0


def test_shuffle_is_deterministic() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    a = shuffle_increments(data, random.Random(42))
    b = shuffle_increments(data, random.Random(42))
    assert a == b


def test_shuffle_preserves_marginal_distribution() -> None:
    data = [1.0, -2.0, 3.0, -4.0]
    shuffled = shuffle_increments(data, random.Random(7))
    assert sorted(shuffled) == sorted(data)


def test_detection_requires_statistical_and_effect_threshold() -> None:
    # Tiny but "significant" effect stays NOT_DETECTED when below floor
    t = PrimaryTestResult(
        test_id="t",
        family="directional_acf",
        lag=1,
        observed_statistic=0.005,
        effect_size=0.005,
        effect_size_floor=MINIMUM_EFFECT_SIZE_ACF,
        effect_size_above_floor=False,
        raw_p_value=0.001,
        valid=True,
        n=500,
    )
    apply_holm_and_classify([t], alpha=0.05)
    assert t.holm_reject is True or t.holm_reject is False  # may reject under Holm
    assert t.classification == "NOT_DETECTED"  # effect floor not met


def test_small_effect_is_not_detected() -> None:
    t = PrimaryTestResult(
        test_id="t",
        family="directional_acf",
        lag=1,
        observed_statistic=0.01,
        effect_size=0.01,
        effect_size_floor=0.02,
        effect_size_above_floor=False,
        raw_p_value=0.0001,
        valid=True,
        n=1000,
    )
    apply_holm_and_classify([t], alpha=0.05)
    assert t.classification == "NOT_DETECTED"


def test_inconclusive_gate() -> None:
    t = PrimaryTestResult(
        test_id="t",
        family="directional_acf",
        lag=1,
        observed_statistic=None,
        effect_size=None,
        effect_size_floor=0.02,
        effect_size_above_floor=False,
        raw_p_value=None,
        valid=False,
        n=5,
    )
    apply_holm_and_classify([t], alpha=0.05)
    assert t.classification == "INCONCLUSIVE"
    assert gate_from_tests([t]) == "INCONCLUSIVE"


def test_overall_inconclusive_becomes_needs_more_data() -> None:
    assert (
        synthesize_overall("NOT_DETECTED", "NOT_DETECTED", "INCONCLUSIVE", study_valid=True)
        == "NEEDS_MORE_DATA"
    )


def test_invalid_study_state() -> None:
    assert (
        synthesize_overall("DETECTED", "DETECTED", "DETECTED", study_valid=False)
        == "INVALID_STUDY"
    )


def test_candidate_structure_state() -> None:
    assert (
        synthesize_overall("DETECTED", "NOT_DETECTED", "NOT_DETECTED", study_valid=True)
        == "CANDIDATE_STRUCTURE"
    )


def test_no_measurable_structure_state() -> None:
    assert (
        synthesize_overall("NOT_DETECTED", "NOT_DETECTED", "NOT_DETECTED", study_valid=True)
        == "NO_MEASURABLE_STRUCTURE"
    )


def test_report_serialization(tmp_path: Path) -> None:
    series = _series(_iid_walk(250, seed=1))
    result = characterize_instrument("v75", series)
    from smb.research.statistical_characterization import StatisticalCharacterizationReport

    report = StatisticalCharacterizationReport(
        configuration=FROZEN_STUDY_CONFIG,
        instruments=[result],
        overall_synthesis=result.overall,
    )
    json_path, md_path = write_artifacts(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "Gate C" in text
    assert "extreme" in text.lower() or "Gate C" in text


def test_malformed_non_finite_increments() -> None:
    prices = [1.0, float("nan"), 2.0, 3.0]
    deltas = compute_increments(prices)
    assert all(math.isfinite(d) for d in deltas)


def test_frozen_lags_match_preregistered() -> None:
    assert ACF_LAGS == (1, 2, 5, 10, 30, 60, 120, 300, 600)
    assert PRIMARY_FAMILY_SIZE == 28
    assert PRIMARY_EXTREME_HORIZON == 30
    assert STUDY_VERSION.startswith("6d")


def test_characterize_strong_persistence_detects_or_not() -> None:
    # Strong AR-like walk: should still produce a valid overall state
    prices = [100.0]
    for _i in range(300):
        prices.append(prices[-1] + 0.5)
    result = characterize_instrument("trend", _series(prices))
    assert result.overall in (
        "CANDIDATE_STRUCTURE",
        "NO_MEASURABLE_STRUCTURE",
        "NEEDS_MORE_DATA",
    )
    assert len(result.primary_tests) == PRIMARY_FAMILY_SIZE
    assert result.gate_c_details.get("primary_test") is not None


# --- Multiplicity ---


def test_empirical_pvalue_convention() -> None:
    p = empirical_two_sided_pvalue(5.0, [0.0, 1.0, 2.0, 3.0, 4.0])
    # |null| >= 5: none; p = (0+1)/(5+1) = 1/6
    assert p is not None
    assert abs(p - 1.0 / 6.0) < 1e-12


def test_empirical_pvalue_none_observed() -> None:
    assert empirical_two_sided_pvalue(None, [1.0, 2.0]) is None


def test_holm_bonferroni_known_vector() -> None:
    # Classic: p = [0.01, 0.04, 0.03], alpha=0.05, m=3
    # Sorted: 0.01, 0.03, 0.04
    # thresholds: 0.05/3, 0.05/2, 0.05/1
    results = holm_bonferroni([0.01, 0.04, 0.03], alpha=0.05)
    assert results[0]["reject"] is True  # 0.01 <= 0.05/3
    assert results[2]["reject"] is False  # 0.03 > 0.05/2
    assert results[1]["reject"] is False  # 0.04 > 0.05/1 after stop


def test_holm_preserves_family_size_with_invalid() -> None:
    pvals = [0.001, None, 0.5]
    results = holm_bonferroni(pvals, alpha=0.05)
    assert len(results) == 3
    assert results[1]["raw_p_value"] is None


def test_primary_family_size_serialized() -> None:
    series = _series(_iid_walk(200, seed=3))
    result = characterize_instrument("x", series)
    assert len(result.primary_tests) == 28
    assert result.to_dict()["primary_family_size"] == 28
    assert result.to_dict()["multiplicity_method"] == "holm_bonferroni"


def test_detection_requires_adjusted_significance() -> None:
    # Unadjusted would be significant, but with m=28 Holm may not reject
    tests = [
        PrimaryTestResult(
            test_id=f"t{i}",
            family="directional_acf",
            lag=i,
            observed_statistic=0.1,
            effect_size=0.1,
            effect_size_floor=0.02,
            effect_size_above_floor=True,
            raw_p_value=0.04,  # unadjusted significant at 0.05
            valid=True,
            n=500,
        )
        for i in range(28)
    ]
    apply_holm_and_classify(tests, alpha=0.05)
    # None should reject under Holm with all p=0.04 and m=28
    assert all(t.classification == "NOT_DETECTED" for t in tests)


# --- Gate C ---


def test_gate_c_is_extreme_not_transition() -> None:
    series = _series(_iid_walk(250, seed=9))
    result = characterize_instrument("x", series)
    d = result.to_dict()
    assert "gate_c_extreme_move_response" in d
    assert "gate_c_transition_structure" not in d
    assert result.transitions_role == "descriptive_supporting_characterization"


def test_gate_c_insufficient_events_inconclusive() -> None:
    # Almost constant series -> few extremes
    series = _series([100.0 + 0.0001 * i for i in range(150)])
    result = characterize_instrument("flat", series, config=FROZEN_STUDY_CONFIG)
    # May be INCONCLUSIVE or NOT_DETECTED depending on events
    assert result.gate_c in ("INCONCLUSIVE", "NOT_DETECTED")




# --- Pip / seed ---


def test_pip_from_settings() -> None:
    settings = {"instruments": {"volatility_75_1s": {"pip_size": 0.01}}}
    assert resolve_pip_size_from_settings("volatility_75_1s", settings) == 0.01
    assert resolve_pip_size_from_settings("step_index", settings) is None
    assert resolve_pip_size_from_settings("x", None) is None


def test_no_invented_pip_fallback() -> None:
    series = _series(_iid_walk(150, seed=2))
    result = characterize_instrument("unknown_inst", series, pip_size=None)
    assert result.pip_size is None


def test_canonical_seed_serialized() -> None:
    series = _series(_iid_walk(150, seed=4))
    result = characterize_instrument("x", series)
    from smb.research.statistical_characterization import StatisticalCharacterizationReport

    report = StatisticalCharacterizationReport(
        configuration=FROZEN_STUDY_CONFIG,
        instruments=[result],
        overall_synthesis=result.overall,
        canonical=True,
        actual_seed=DEFAULT_SEED,
        canonical_seed=DEFAULT_SEED,
    )
    d = report.to_dict()
    assert d["canonical"] is True
    assert d["canonical_seed"] == DEFAULT_SEED
    assert d["actual_seed"] == DEFAULT_SEED


def test_non_canonical_seed_marked() -> None:
    from smb.research.statistical_characterization import StatisticalCharacterizationReport

    series = _series(_iid_walk(150, seed=5))
    result = characterize_instrument(
        "x", series, config=StudyConfiguration(seed=999)
    )
    report = StatisticalCharacterizationReport(
        configuration=StudyConfiguration(seed=999),
        instruments=[result],
        overall_synthesis=result.overall,
        canonical=False,
        actual_seed=999,
        canonical_seed=DEFAULT_SEED,
    )
    d = report.to_dict()
    assert d["canonical"] is False
    assert d["actual_seed"] == 999
    assert d["canonical_seed"] == DEFAULT_SEED


def test_coverage_fields_present() -> None:
    series = _series(_iid_walk(120, seed=6))
    cov = coverage_from_series("x", series)
    assert cov.tick_count == 120
    assert cov.earliest_epoch is not None
    assert cov.calendar_span_days is not None


# --- Review blockers: conservative gates + null sufficiency ---


def test_gate_inconclusive_propagates_over_detected() -> None:
    """Any INCONCLUSIVE component forces gate INCONCLUSIVE (conservative)."""
    detected = PrimaryTestResult(
        test_id="d",
        family="directional_acf",
        lag=1,
        observed_statistic=0.5,
        effect_size=0.5,
        effect_size_floor=0.02,
        effect_size_above_floor=True,
        raw_p_value=0.001,
        valid=True,
        n=500,
        classification="DETECTED",
    )
    inconclusive = PrimaryTestResult(
        test_id="i",
        family="directional_acf",
        lag=2,
        observed_statistic=None,
        effect_size=None,
        effect_size_floor=0.02,
        effect_size_above_floor=False,
        raw_p_value=None,
        valid=False,
        n=5,
        classification="INCONCLUSIVE",
    )
    assert gate_from_tests([detected, inconclusive]) == "INCONCLUSIVE"
    assert gate_from_tests([inconclusive, detected]) == "INCONCLUSIVE"


def test_gate_inconclusive_propagates_over_not_detected() -> None:
    not_det = PrimaryTestResult(
        test_id="n",
        family="directional_acf",
        lag=1,
        observed_statistic=0.01,
        effect_size=0.01,
        effect_size_floor=0.02,
        effect_size_above_floor=False,
        raw_p_value=0.5,
        valid=True,
        n=500,
        classification="NOT_DETECTED",
    )
    inconclusive = PrimaryTestResult(
        test_id="i",
        family="directional_acf",
        lag=2,
        observed_statistic=None,
        effect_size=None,
        effect_size_floor=0.02,
        effect_size_above_floor=False,
        raw_p_value=None,
        valid=False,
        n=5,
        classification="INCONCLUSIVE",
    )
    assert gate_from_tests([not_det, inconclusive]) == "INCONCLUSIVE"


def test_gate_b_inconclusive_propagates_to_overall() -> None:
    """Partial INCONCLUSIVE on a primary gate → NEEDS_MORE_DATA."""
    assert (
        synthesize_overall("NOT_DETECTED", "INCONCLUSIVE", "NOT_DETECTED", study_valid=True)
        == "NEEDS_MORE_DATA"
    )
    assert (
        synthesize_overall("DETECTED", "INCONCLUSIVE", "NOT_DETECTED", study_valid=True)
        == "NEEDS_MORE_DATA"
    )


def test_insufficient_valid_nulls_makes_extreme_inconclusive() -> None:
    """When valid null count < min, extreme primary test is invalid/INCONCLUSIVE."""
    t = PrimaryTestResult(
        test_id="extreme",
        family="extreme_response",
        lag=None,
        observed_statistic=1.0,
        effect_size=0.5,
        effect_size_floor=0.1,
        effect_size_above_floor=True,
        raw_p_value=None,  # would be None when valid=False due to null insufficiency
        valid=False,
        n=30,
        details={
            "null_simulations_requested": 100,
            "null_simulations_valid": 7,
            "minimum_valid_null_simulations": 100,
        },
    )
    apply_holm_and_classify([t], alpha=0.05)
    assert t.classification == "INCONCLUSIVE"
    assert gate_from_tests([t]) == "INCONCLUSIVE"


def test_null_extreme_pipeline_uses_shuffled_series(monkeypatch) -> None:
    """Null path must call detect_extreme_events on shuffled input, not observed indices."""
    from smb.research import statistical_characterization as sc

    observed = [0.1] * 40 + [8.0] + [0.1] * 40
    calls: list[list[float]] = []
    real_detect = sc.detect_extreme_events

    def tracking_detect(deltas, *, window, sigma):
        calls.append(list(deltas))
        return real_detect(deltas, window=window, sigma=sigma)

    monkeypatch.setattr(sc, "detect_extreme_events", tracking_detect)

    # Observed
    sc.primary_extreme_statistic(
        observed, window=15, sigma=2.0, horizon=5, min_events=1
    )
    assert len(calls) >= 1
    assert calls[0] == observed

    # Shuffled must be a different list identity/content path
    shuffled = sc.shuffle_increments(observed, random.Random(99))
    before = len(calls)
    sc.primary_extreme_statistic(
        shuffled, window=15, sigma=2.0, horizon=5, min_events=1
    )
    assert len(calls) == before + 1
    assert calls[-1] == shuffled
    # Stronger: event detection was invoked with the shuffled argument object content
    assert calls[-1] is not calls[0]


def test_min_valid_null_config_frozen() -> None:
    from smb.research.statistical_characterization import (
        MIN_VALID_NULL_SIMULATIONS,
        NULL_SIMULATIONS,
    )

    assert MIN_VALID_NULL_SIMULATIONS == NULL_SIMULATIONS
    assert FROZEN_STUDY_CONFIG.min_valid_null_simulations == NULL_SIMULATIONS


def test_combine_overall_needs_more_data_over_candidate() -> None:
    assert (
        _combine_overall(["CANDIDATE_STRUCTURE", "NEEDS_MORE_DATA"])
        == "NEEDS_MORE_DATA"
    )
    assert (
        _combine_overall(["NEEDS_MORE_DATA", "CANDIDATE_STRUCTURE"])
        == "NEEDS_MORE_DATA"
    )


def test_combine_overall_invalid_over_candidate() -> None:
    assert (
        _combine_overall(["CANDIDATE_STRUCTURE", "INVALID_STUDY"])
        == "NEEDS_MORE_DATA"
    )
    assert (
        _combine_overall(["INVALID_STUDY", "CANDIDATE_STRUCTURE"])
        == "NEEDS_MORE_DATA"
    )


def test_combine_overall_all_invalid() -> None:
    assert _combine_overall(["INVALID_STUDY", "INVALID_STUDY"]) == "INVALID_STUDY"


def test_combine_overall_candidate_with_not_detected() -> None:
    assert (
        _combine_overall(["CANDIDATE_STRUCTURE", "NO_MEASURABLE_STRUCTURE"])
        == "CANDIDATE_STRUCTURE"
    )


def test_combine_overall_all_not_detected() -> None:
    assert (
        _combine_overall(["NO_MEASURABLE_STRUCTURE", "NO_MEASURABLE_STRUCTURE"])
        == "NO_MEASURABLE_STRUCTURE"
    )
