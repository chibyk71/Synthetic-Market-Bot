"""Tests for Milestone 6D \u2014 Instrument Statistical Characterization."""

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
