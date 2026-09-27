"""Tests for Milestone 6D — Instrument Statistical Characterization."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from smb.research.stats import percentile
from smb.research.statistical_characterization import (
    ACF_LAGS,
    FROZEN_STUDY_CONFIG,
    MINIMUM_EFFECT_SIZE_ACF,
    STUDY_VERSION,
    StudyConfiguration,
    TickSeries,
    _excess_kurtosis_bias_corrected,
    _skewness_bias_corrected,
    acf_at_lag,
    causal_extreme_threshold,
    characterize_instrument,
    classify_acf_gate,
    compute_increments,
    compute_inter_tick_intervals,
    coverage_from_series,
    detect_extreme_events,
    format_markdown_report,
    future_response_at_horizon,
    increment_distribution,
    null_comparison,
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
    series = _series([])
    cov = coverage_from_series("x", series)
    assert cov.tick_count == 0
    assert cov.earliest_epoch is None
    dist = increment_distribution([])
    assert dist.n == 0
    assert dist.mean is None


def test_constant_series() -> None:
    prices = [50.0] * 200
    series = _series(prices)
    result = characterize_instrument("const", series, config=FROZEN_STUDY_CONFIG)
    assert result.increment_distribution.n == 199
    assert result.gate_a in ("NOT_DETECTED", "INCONCLUSIVE")


def test_increment_distribution() -> None:
    deltas = [1.0, -1.0, 2.0, -2.0, 0.0]
    dist = increment_distribution(deltas)
    assert dist.n == 5
    assert dist.positive_count == 2
    assert dist.negative_count == 2
    assert dist.zero_count == 1
    assert dist.mean == 0.0
    assert dist.min == -2.0
    assert dist.max == 2.0


def test_percentile_convention() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 50.0) == 3.0
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 100.0) == 5.0


def test_skewness_convention() -> None:
    sym = [-2.0, -1.0, 0.0, 1.0, 2.0]
    sk = _skewness_bias_corrected(sym)
    assert sk is not None
    assert abs(sk) < 1e-9
    right = [0.0, 0.0, 0.0, 0.0, 10.0]
    sk_r = _skewness_bias_corrected(right)
    assert sk_r is not None and sk_r > 0


def test_excess_kurtosis_convention() -> None:
    vals = [float(x) for x in range(20)]
    k = _excess_kurtosis_bias_corrected(vals)
    assert k is not None
    assert math.isfinite(k)
    assert _excess_kurtosis_bias_corrected([1.0, 1.0, 1.0, 1.0, 1.0]) == 0.0


def test_acf_known_series() -> None:
    alt = [1.0, -1.0] * 50
    acf_alt, n_alt = acf_at_lag(alt, 1)
    assert n_alt == 99
    assert acf_alt is not None
    assert acf_alt < -0.5


def test_acf_lag_validation() -> None:
    with pytest.raises(ValueError):
        acf_at_lag([1.0, 2.0, 3.0], 0)
    acf, n = acf_at_lag([1.0, 2.0], 5)
    assert acf is None
    assert n == 0


def test_direction_transition_counts() -> None:
    deltas = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    tr = transition_statistics(deltas)
    assert tr.n_nonzero == 6
    assert tr.n_pairs == 5
    assert tr.same_direction_transition_rate == 0.0
    ups = [1.0, 2.0, 3.0, 4.0]
    tr2 = transition_statistics(ups)
    assert tr2.same_direction_transition_rate == 1.0
    assert tr2.p_up == 1.0


def test_run_length_statistics() -> None:
    deltas = [1.0, 1.0, -1.0, -1.0, -1.0, 1.0]
    runs = run_length_statistics(deltas)
    assert runs["UP"].count == 2
    assert runs["DOWN"].count == 1
    assert runs["DOWN"].maximum == 3
    assert runs["UP"].length_counts["1"] == 1
    assert runs["UP"].length_counts["2"] == 1


def test_inter_tick_statistics() -> None:
    epochs = [100, 101, 103, 104]
    intervals = compute_inter_tick_intervals(epochs)
    assert intervals == [1.0, 2.0, 1.0]
    series = TickSeries(epochs=tuple(epochs), prices=(1.0, 2.0, 3.0, 4.0))
    cov = coverage_from_series("t", series)
    assert cov.tick_count == 4
    assert cov.calendar_span_seconds == 4
    assert cov.inter_tick["count"] == 3
    assert cov.inter_tick["min"] == 1.0
    assert cov.inter_tick["max"] == 2.0


def test_extreme_event_detection() -> None:
    deltas = [0.1] * 50 + [10.0] + [0.1] * 50
    events = detect_extreme_events(deltas, window=20, sigma=3.0)
    assert len(events) >= 1


def test_extreme_threshold_is_causal() -> None:
    deltas = [0.1] * 100
    thr_base = causal_extreme_threshold(deltas, 10, window=20, sigma=3.0)
    deltas2 = list(deltas)
    for i in range(11, 100):
        deltas2[i] = 1000.0
    thr_future = causal_extreme_threshold(deltas2, 10, window=20, sigma=3.0)
    assert thr_base == thr_future


def test_future_information_not_used() -> None:
    rng = random.Random(1)
    base = [rng.gauss(0, 1) for _ in range(200)]
    for t in (30, 50, 80, 120):
        thr1 = causal_extreme_threshold(base, t, window=30, sigma=2.5)
        altered = list(base)
        for j in range(t + 1, len(altered)):
            altered[j] = rng.gauss(100, 50)
        thr2 = causal_extreme_threshold(altered, t, window=30, sigma=2.5)
        assert thr1 == thr2, f"threshold at t={t} changed when future was altered"


def test_future_response_calculation() -> None:
    deltas = [1.0, 2.0, 3.0, -1.0, -2.0]
    r = future_response_at_horizon(deltas, 0, 2)
    assert r is not None
    assert r["signed_future_return"] == 5.0
    assert r["direction_adjusted_future_return"] == 5.0
    assert r["continuation_indicator"] is True
    assert future_response_at_horizon(deltas, 0, 10) is None


def test_shuffle_is_deterministic() -> None:
    data = [float(i) for i in range(50)]
    a = shuffle_increments(data, random.Random(42))
    b = shuffle_increments(data, random.Random(42))
    assert a == b
    c = shuffle_increments(data, random.Random(43))
    assert a != c


def test_shuffle_preserves_marginal_distribution() -> None:
    data = [1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0]
    shuffled = shuffle_increments(data, random.Random(7))
    assert sorted(shuffled) == sorted(data)


def test_detection_requires_statistical_and_effect_threshold() -> None:
    observed = 0.005
    null_vals = [0.0] * 100
    cmp = null_comparison(observed, null_vals, significance_level=0.05)
    assert cmp["statistically_significant"] is True
    rows = [{"tick_lag": 1, "acf": observed, "n": 10_000}]
    null_by_lag = {1: cmp}
    cfg = StudyConfiguration(minimum_effect_size_acf=MINIMUM_EFFECT_SIZE_ACF)
    gate, details = classify_acf_gate(rows, null_by_lag, config=cfg)
    assert gate == "NOT_DETECTED"
    assert details[0]["statistically_significant"] is True
    assert details[0]["effect_size_above_floor"] is False


def test_small_effect_is_not_detected() -> None:
    rows = [{"tick_lag": 1, "acf": 0.001, "n": 1_000_000}]
    null_by_lag = {1: {"statistically_significant": True, "observed_statistic": 0.001}}
    gate, details = classify_acf_gate(rows, null_by_lag, config=FROZEN_STUDY_CONFIG)
    assert gate == "NOT_DETECTED"
    assert details[0]["classification"] == "NOT_DETECTED"


def test_inconclusive_gate() -> None:
    rows = [{"tick_lag": 1, "acf": 0.5, "n": 5}]
    null_by_lag = {1: {"statistically_significant": True}}
    gate, details = classify_acf_gate(rows, null_by_lag, config=FROZEN_STUDY_CONFIG)
    assert gate == "INCONCLUSIVE"
    assert details[0]["classification"] == "INCONCLUSIVE"


def test_overall_inconclusive_becomes_needs_more_data() -> None:
    assert (
        synthesize_overall("INCONCLUSIVE", "NOT_DETECTED", "NOT_DETECTED", study_valid=True)
        == "NEEDS_MORE_DATA"
    )


def test_invalid_study_state() -> None:
    assert (
        synthesize_overall("NOT_DETECTED", "NOT_DETECTED", "NOT_DETECTED", study_valid=False)
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
    prices = _iid_walk(300, seed=1)
    series = _series(prices)
    cfg = StudyConfiguration(null_simulations=5, min_ticks_for_study=50)
    result = characterize_instrument("volatility_75_1s", series, config=cfg)
    from smb.research.statistical_characterization import StatisticalCharacterizationReport

    report = StatisticalCharacterizationReport(
        configuration=cfg,
        instruments=[result],
        overall_synthesis=result.overall,
    )
    d = report.to_dict()
    assert d["study_version"] == cfg.study_version
    assert "configuration" in d
    assert d["instruments"][0]["instrument"] == "volatility_75_1s"
    json_path, md_path = write_artifacts(report, tmp_path)
    assert json_path.exists()
    assert md_path.exists()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["overall_synthesis"] == result.overall
    md = format_markdown_report(report)
    assert "not** a trading strategy" in md or "not a trading strategy" in md.lower()
    assert "Gate A" in md


def test_malformed_non_finite_increments() -> None:
    prices = [1.0, float("nan"), 2.0, float("inf"), 3.0]
    finite_prices = [p for p in prices if math.isfinite(p)]
    deltas = compute_increments(finite_prices)
    assert all(math.isfinite(d) for d in deltas)
    dist = increment_distribution([1.0, float("nan"), -1.0])
    assert dist.n == 2


def test_frozen_lags_match_preregistered() -> None:
    assert list(ACF_LAGS) == [1, 2, 5, 10, 30, 60, 120, 300, 600]
    assert FROZEN_STUDY_CONFIG.study_version == STUDY_VERSION
    assert FROZEN_STUDY_CONFIG.minimum_effect_size_acf == MINIMUM_EFFECT_SIZE_ACF


def test_characterize_strong_persistence_detects_or_not() -> None:
    deltas_src: list[float] = []
    for _ in range(40):
        deltas_src.extend([1.0] * 5 + [-1.0] * 5)
    prices = [100.0]
    for d in deltas_src:
        prices.append(prices[-1] + d)
    series = _series(prices)
    cfg = StudyConfiguration(null_simulations=20, min_ticks_for_study=50)
    result = characterize_instrument("step_index", series, config=cfg)
    assert result.overall in (
        "CANDIDATE_STRUCTURE",
        "NO_MEASURABLE_STRUCTURE",
        "NEEDS_MORE_DATA",
    )
    assert result.gate_a in ("DETECTED", "NOT_DETECTED", "INCONCLUSIVE")
    assert result.coverage.tick_count == len(prices)
