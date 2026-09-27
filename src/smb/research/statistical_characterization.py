"""Milestone 6D — Instrument Statistical Characterization.

Research-only characterization of temporal structure in synthetic tick
processes. Does **not** modify strategy, trade construction, simulation,
live/demo execution, or production baselines.

Preregistered structures (each instrument independently):
  Gate A — directional dependence (ACF of signed increments)
  Gate B — volatility dependence (ACF of abs / squared increments)
  Gate C — directional transitions and run lengths
  Gate D — overall synthesis

Detection requires BOTH statistical significance AND a frozen
minimum-effect-size floor.

Correct negative language:
  "no measurable structure was detected under the preregistered tests
   and effect-size thresholds."
Correct positive language:
  "a candidate statistical property has been detected and may justify a
   separately preregistered hypothesis study."
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.research.stats import percentile

# ---------------------------------------------------------------------------
# Frozen study configuration (committed before any study execution)
# ---------------------------------------------------------------------------

STUDY_ID = "milestone-6d-statistical-characterization"
STUDY_VERSION = "6d.1"
DEFAULT_SEED = 20260927
ACF_LAGS: tuple[int, ...] = (1, 2, 5, 10, 30, 60, 120, 300, 600)
EXTREME_MOVE_WINDOW = 300
EXTREME_MOVE_THRESHOLD_SIGMA = 3.0
FUTURE_RESPONSE_HORIZONS: tuple[int, ...] = (1, 5, 10, 30, 60, 180, 300)
NULL_SIMULATIONS = 100
SIGNIFICANCE_LEVEL = 0.05
MINIMUM_EFFECT_SIZE_ACF = 0.02
MINIMUM_EFFECT_SIZE_TRANSITION = 0.03
MIN_OBSERVATIONS_FOR_ACF = 50
MIN_TRANSITION_PAIRS = 30
MIN_RUNS_PER_DIRECTION = 10
MIN_EXTREME_EVENTS = 20
MIN_TICKS_FOR_STUDY = 100

INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_INSTRUMENTS: tuple[str, ...] = (INSTRUMENT_V75, INSTRUMENT_STEP)

GateState = Literal["DETECTED", "NOT_DETECTED", "INCONCLUSIVE"]
OverallState = Literal[
    "NO_MEASURABLE_STRUCTURE",
    "CANDIDATE_STRUCTURE",
    "NEEDS_MORE_DATA",
    "INVALID_STUDY",
]
RUN_LENGTH_BUCKETS: tuple[int, ...] = (1, 2, 3, 4, 5, 10)


@dataclass(frozen=True, slots=True)
class StudyConfiguration:
    """Immutable preregistered study configuration."""

    study_id: str = STUDY_ID
    study_version: str = STUDY_VERSION
    seed: int = DEFAULT_SEED
    acf_lags: tuple[int, ...] = ACF_LAGS
    extreme_move_window: int = EXTREME_MOVE_WINDOW
    extreme_move_threshold_sigma: float = EXTREME_MOVE_THRESHOLD_SIGMA
    future_response_horizons: tuple[int, ...] = FUTURE_RESPONSE_HORIZONS
    null_simulations: int = NULL_SIMULATIONS
    significance_level: float = SIGNIFICANCE_LEVEL
    minimum_effect_size_acf: float = MINIMUM_EFFECT_SIZE_ACF
    minimum_effect_size_transition: float = MINIMUM_EFFECT_SIZE_TRANSITION
    min_observations_for_acf: int = MIN_OBSERVATIONS_FOR_ACF
    min_transition_pairs: int = MIN_TRANSITION_PAIRS
    min_runs_per_direction: int = MIN_RUNS_PER_DIRECTION
    min_extreme_events: int = MIN_EXTREME_EVENTS
    min_ticks_for_study: int = MIN_TICKS_FOR_STUDY

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["acf_lags"] = list(self.acf_lags)
        d["future_response_horizons"] = list(self.future_response_horizons)
        return d


FROZEN_STUDY_CONFIG = StudyConfiguration()


def _is_finite_number(v: object) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return math.isfinite(float(v))


def _finite_list(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    for v in values:
        if not _is_finite_number(v):
            continue
        out.append(float(v))
    return out


def _std_sample(values: Sequence[float]) -> float | None:
    data = _finite_list(values)
    n = len(data)
    if n < 2:
        return None
    mu = sum(data) / n
    var = sum((x - mu) ** 2 for x in data) / (n - 1)
    return math.sqrt(var)


def _skewness_bias_corrected(values: Sequence[float]) -> float | None:
    """Bias-corrected Fisher-Pearson standardized moment coefficient G1."""
    data = _finite_list(values)
    n = len(data)
    if n < 3:
        return None
    mu = sum(data) / n
    s = _std_sample(data)
    if s is None or s == 0.0:
        return 0.0 if s == 0.0 else None
    m3 = sum(((x - mu) / s) ** 3 for x in data)
    return (n / ((n - 1) * (n - 2))) * m3


def _excess_kurtosis_bias_corrected(values: Sequence[float]) -> float | None:
    """Bias-corrected excess kurtosis (Fisher; normal = 0)."""
    data = _finite_list(values)
    n = len(data)
    if n < 4:
        return None
    mu = sum(data) / n
    s = _std_sample(data)
    if s is None or s == 0.0:
        return 0.0 if s == 0.0 else None
    m4 = sum(((x - mu) / s) ** 4 for x in data)
    term1 = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * m4
    term2 = 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    return term1 - term2


def _percentile_map(values: Sequence[float], ps: Sequence[float]) -> dict[str, float | None]:
    data = _finite_list(values)
    out: dict[str, float | None] = {}
    key_map = {
        1.0: "p01", 5.0: "p05", 25.0: "p25", 50.0: "median",
        75.0: "p75", 90.0: "p90", 95.0: "p95", 99.0: "p99",
    }
    for p in ps:
        key = key_map.get(p, f"p{p}")
        out[key] = percentile(data, p) if data else None
    return out


@dataclass(frozen=True, slots=True)
class TickSeries:
    epochs: tuple[int, ...]
    prices: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.prices)


def load_tick_series(
    repo: TickRepository,
    instrument: str,
    *,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
) -> TickSeries:
    ticks = repo.get_ticks(instrument, start_epoch=start_epoch, end_epoch=end_epoch)
    epochs: list[int] = []
    prices: list[float] = []
    for t in ticks:
        if not _is_finite_number(t.price):
            continue
        if not isinstance(t.epoch, int):
            continue
        epochs.append(int(t.epoch))
        prices.append(float(t.price))
    return TickSeries(epochs=tuple(epochs), prices=tuple(prices))


def compute_increments(prices: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(prices)):
        d = float(prices[i]) - float(prices[i - 1])
        if math.isfinite(d):
            out.append(d)
    return out


def compute_inter_tick_intervals(epochs: Sequence[int]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(epochs)):
        d = int(epochs[i]) - int(epochs[i - 1])
        if d >= 0:
            out.append(float(d))
    return out


@dataclass(frozen=True, slots=True)
class CoverageReport:
    instrument: str
    tick_count: int
    earliest_epoch: int | None
    latest_epoch: int | None
    calendar_span_seconds: int | None
    calendar_span_days: float | None
    inter_tick: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _empty_interval_stats() -> dict[str, Any]:
    return {
        "count": 0, "mean": None, "median": None, "p01": None, "p05": None,
        "p25": None, "p75": None, "p95": None, "p99": None, "min": None, "max": None,
    }


def _interval_stats(intervals: Sequence[float]) -> dict[str, Any]:
    data = _finite_list(intervals)
    if not data:
        return _empty_interval_stats()
    ordered = sorted(data)
    pct = _percentile_map(data, [1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0])
    return {
        "count": len(data),
        "mean": sum(data) / len(data),
        "median": pct["median"],
        "p01": pct["p01"], "p05": pct["p05"], "p25": pct["p25"],
        "p75": pct["p75"], "p95": pct["p95"], "p99": pct["p99"],
        "min": ordered[0], "max": ordered[-1],
    }


def coverage_from_series(instrument: str, series: TickSeries) -> CoverageReport:
    n = series.n
    if n == 0:
        return CoverageReport(
            instrument=instrument, tick_count=0,
            earliest_epoch=None, latest_epoch=None,
            calendar_span_seconds=None, calendar_span_days=None,
            inter_tick=_empty_interval_stats(),
        )
    earliest = series.epochs[0]
    latest = series.epochs[-1]
    span = latest - earliest
    return CoverageReport(
        instrument=instrument, tick_count=n,
        earliest_epoch=earliest, latest_epoch=latest,
        calendar_span_seconds=span, calendar_span_days=span / 86400.0,
        inter_tick=_interval_stats(compute_inter_tick_intervals(series.epochs)),
    )


@dataclass(frozen=True, slots=True)
class IncrementDistribution:
    n: int
    mean: float | None
    std: float | None
    median: float | None
    min: float | None
    max: float | None
    p01: float | None
    p05: float | None
    p25: float | None
    p75: float | None
    p95: float | None
    p99: float | None
    positive_count: int
    negative_count: int
    zero_count: int
    positive_fraction: float | None
    negative_fraction: float | None
    zero_fraction: float | None
    skewness: float | None
    excess_kurtosis: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def increment_distribution(deltas: Sequence[float]) -> IncrementDistribution:
    data = _finite_list(deltas)
    n = len(data)
    if n == 0:
        return IncrementDistribution(
            n=0, mean=None, std=None, median=None, min=None, max=None,
            p01=None, p05=None, p25=None, p75=None, p95=None, p99=None,
            positive_count=0, negative_count=0, zero_count=0,
            positive_fraction=None, negative_fraction=None, zero_fraction=None,
            skewness=None, excess_kurtosis=None,
        )
    ordered = sorted(data)
    pos = sum(1 for x in data if x > 0)
    neg = sum(1 for x in data if x < 0)
    zero = sum(1 for x in data if x == 0)
    pct = _percentile_map(data, [1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0])
    return IncrementDistribution(
        n=n, mean=sum(data) / n, std=_std_sample(data), median=pct["median"],
        min=ordered[0], max=ordered[-1],
        p01=pct["p01"], p05=pct["p05"], p25=pct["p25"],
        p75=pct["p75"], p95=pct["p95"], p99=pct["p99"],
        positive_count=pos, negative_count=neg, zero_count=zero,
        positive_fraction=pos / n, negative_fraction=neg / n, zero_fraction=zero / n,
        skewness=_skewness_bias_corrected(data),
        excess_kurtosis=_excess_kurtosis_bias_corrected(data),
    )


def acf_at_lag(series: Sequence[float], lag: int) -> tuple[float | None, int]:
    if lag < 1:
        raise ValueError("lag must be >= 1")
    data = _finite_list(series)
    n = len(data)
    if n <= lag:
        return None, 0
    mu = sum(data) / n
    denom = sum((x - mu) ** 2 for x in data)
    if denom == 0.0:
        return None, n - lag
    num = sum((data[i] - mu) * (data[i + lag] - mu) for i in range(n - lag))
    return num / denom, n - lag


def acf_table(
    series: Sequence[float],
    lags: Sequence[int],
    *,
    mean_interval_seconds: float | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lag in lags:
        acf, n_pairs = acf_at_lag(series, lag)
        approx_wall: float | None = None
        if mean_interval_seconds is not None and math.isfinite(mean_interval_seconds):
            approx_wall = float(lag) * float(mean_interval_seconds)
        rows.append({
            "tick_lag": lag,
            "approximate_wall_clock_duration_seconds": approx_wall,
            "n": n_pairs,
            "acf": acf,
        })
    return rows


def direction_labels(deltas: Sequence[float]) -> list[str]:
    labels: list[str] = []
    for d in deltas:
        if not math.isfinite(d) or d == 0.0:
            continue
        labels.append("UP" if d > 0 else "DOWN")
    return labels


@dataclass(frozen=True, slots=True)
class TransitionStats:
    n_nonzero: int
    n_pairs: int
    p_up: float | None
    p_down: float | None
    p_up_given_up: float | None
    p_down_given_up: float | None
    p_up_given_down: float | None
    p_down_given_down: float | None
    same_direction_transition_rate: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def transition_statistics(deltas: Sequence[float]) -> TransitionStats:
    labels = direction_labels(deltas)
    n = len(labels)
    if n == 0:
        return TransitionStats(
            n_nonzero=0, n_pairs=0, p_up=None, p_down=None,
            p_up_given_up=None, p_down_given_up=None,
            p_up_given_down=None, p_down_given_down=None,
            same_direction_transition_rate=None,
        )
    n_up = sum(1 for x in labels if x == "UP")
    n_down = n - n_up
    uu = ud = du = dd = 0
    for i in range(len(labels) - 1):
        a, b = labels[i], labels[i + 1]
        if a == "UP" and b == "UP":
            uu += 1
        elif a == "UP" and b == "DOWN":
            ud += 1
        elif a == "DOWN" and b == "UP":
            du += 1
        else:
            dd += 1
    n_pairs = uu + ud + du + dd
    from_up = uu + ud
    from_down = du + dd
    same = uu + dd

    def _rate(num: int, den: int) -> float | None:
        return num / den if den > 0 else None

    return TransitionStats(
        n_nonzero=n, n_pairs=n_pairs,
        p_up=n_up / n, p_down=n_down / n,
        p_up_given_up=_rate(uu, from_up), p_down_given_up=_rate(ud, from_up),
        p_up_given_down=_rate(du, from_down), p_down_given_down=_rate(dd, from_down),
        same_direction_transition_rate=_rate(same, n_pairs),
    )


@dataclass(frozen=True, slots=True)
class RunStats:
    direction: str
    count: int
    mean: float | None
    median: float | None
    p90: float | None
    maximum: int | None
    length_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_length_statistics(deltas: Sequence[float]) -> dict[str, RunStats]:
    labels = direction_labels(deltas)
    runs_up: list[int] = []
    runs_down: list[int] = []
    empty_counts = {str(k): 0 for k in RUN_LENGTH_BUCKETS}
    if not labels:
        return {
            "UP": RunStats("UP", 0, None, None, None, None, empty_counts),
            "DOWN": RunStats("DOWN", 0, None, None, None, None, dict(empty_counts)),
        }
    current = labels[0]
    length = 1
    for lab in labels[1:]:
        if lab == current:
            length += 1
        else:
            (runs_up if current == "UP" else runs_down).append(length)
            current = lab
            length = 1
    (runs_up if current == "UP" else runs_down).append(length)

    def _summarize(direction: str, runs: list[int]) -> RunStats:
        counts = {str(k): 0 for k in RUN_LENGTH_BUCKETS}
        for r in runs:
            for k in RUN_LENGTH_BUCKETS:
                if r == k:
                    counts[str(k)] += 1
        if not runs:
            return RunStats(direction, 0, None, None, None, None, counts)
        return RunStats(
            direction=direction, count=len(runs),
            mean=sum(runs) / len(runs),
            median=percentile([float(r) for r in runs], 50.0),
            p90=percentile([float(r) for r in runs], 90.0),
            maximum=max(runs), length_counts=counts,
        )

    return {"UP": _summarize("UP", runs_up), "DOWN": _summarize("DOWN", runs_down)}


def causal_extreme_threshold(
    deltas: Sequence[float],
    index: int,
    *,
    window: int,
    sigma: float,
) -> float | None:
    """Threshold at index using only observations at or before index."""
    if index < 0 or index >= len(deltas):
        return None
    start = max(0, index - window + 1)
    window_vals = [
        float(deltas[i]) for i in range(start, index + 1) if math.isfinite(deltas[i])
    ]
    if len(window_vals) < 2:
        return None
    s = _std_sample(window_vals)
    if s is None or s == 0.0:
        return None
    return float(sigma) * s


def detect_extreme_events(
    deltas: Sequence[float],
    *,
    window: int,
    sigma: float,
) -> list[int]:
    events: list[int] = []
    for t in range(len(deltas)):
        thr = causal_extreme_threshold(deltas, t, window=window, sigma=sigma)
        if thr is None:
            continue
        if abs(float(deltas[t])) >= thr:
            events.append(t)
    return events


def future_response_at_horizon(
    deltas: Sequence[float],
    event_index: int,
    horizon: int,
) -> dict[str, Any] | None:
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    end = event_index + horizon
    if end >= len(deltas):
        return None
    init = float(deltas[event_index])
    if not math.isfinite(init) or init == 0.0:
        return None
    future = [float(deltas[i]) for i in range(event_index + 1, end + 1)]
    if not all(math.isfinite(x) for x in future):
        return None
    signed = sum(future)
    sign = 1.0 if init > 0 else -1.0
    direction_adjusted = sign * signed
    abs_excursion = max(abs(sum(future[: k + 1])) for k in range(len(future)))
    return {
        "signed_future_return": signed,
        "direction_adjusted_future_return": direction_adjusted,
        "absolute_future_excursion": abs_excursion,
        "continuation_indicator": direction_adjusted > 0,
        "reversion_indicator": direction_adjusted < 0,
        "initiating_sign": "positive" if init > 0 else "negative",
    }


def extreme_move_analysis(
    deltas: Sequence[float],
    *,
    window: int,
    sigma: float,
    horizons: Sequence[int],
    min_events: int,
) -> dict[str, Any]:
    events = detect_extreme_events(deltas, window=window, sigma=sigma)
    by_sign: dict[str, list[int]] = {"positive": [], "negative": []}
    for t in events:
        d = float(deltas[t])
        if d > 0:
            by_sign["positive"].append(t)
        elif d < 0:
            by_sign["negative"].append(t)

    horizon_summaries: list[dict[str, Any]] = []
    for h in horizons:
        responses: list[dict[str, Any]] = []
        for t in events:
            r = future_response_at_horizon(deltas, t, h)
            if r is not None:
                responses.append(r)
        if len(responses) < min_events:
            horizon_summaries.append({
                "horizon_ticks": h,
                "n_events_with_response": len(responses),
                "status": "INCONCLUSIVE",
                "mean_direction_adjusted_return": None,
                "continuation_rate": None,
                "reversion_rate": None,
                "mean_absolute_excursion": None,
            })
            continue
        da = [r["direction_adjusted_future_return"] for r in responses]
        cont = sum(1 for r in responses if r["continuation_indicator"])
        rev = sum(1 for r in responses if r["reversion_indicator"])
        abs_ex = [r["absolute_future_excursion"] for r in responses]
        horizon_summaries.append({
            "horizon_ticks": h,
            "n_events_with_response": len(responses),
            "status": "OK",
            "mean_direction_adjusted_return": sum(da) / len(da),
            "continuation_rate": cont / len(responses),
            "reversion_rate": rev / len(responses),
            "mean_absolute_excursion": sum(abs_ex) / len(abs_ex),
        })

    return {
        "n_extreme_events": len(events),
        "n_positive": len(by_sign["positive"]),
        "n_negative": len(by_sign["negative"]),
        "window": window,
        "threshold_sigma": sigma,
        "horizons": horizon_summaries,
        "status": "OK" if len(events) >= min_events else "INCONCLUSIVE",
    }


def shuffle_increments(deltas: Sequence[float], rng: random.Random) -> list[float]:
    data = list(deltas)
    rng.shuffle(data)
    return data


def null_comparison(
    observed_stat: float | None,
    null_values: Sequence[float | None],
    *,
    significance_level: float,
) -> dict[str, Any]:
    clean = [float(v) for v in null_values if v is not None and math.isfinite(float(v))]
    if observed_stat is None or not math.isfinite(observed_stat) or not clean:
        return {
            "observed_statistic": observed_stat,
            "null_mean": None, "null_std": None,
            "null_lower_quantile": None, "null_upper_quantile": None,
            "observed_minus_null": None,
            "statistically_significant": False,
            "n_null": len(clean),
        }
    mu = sum(clean) / len(clean)
    std = _std_sample(clean)
    alpha = significance_level
    lower = percentile(clean, 100.0 * (alpha / 2.0))
    upper = percentile(clean, 100.0 * (1.0 - alpha / 2.0))
    sig = False
    if lower is not None and upper is not None:
        sig = observed_stat < lower or observed_stat > upper
    return {
        "observed_statistic": observed_stat,
        "null_mean": mu, "null_std": std,
        "null_lower_quantile": lower, "null_upper_quantile": upper,
        "observed_minus_null": observed_stat - mu,
        "statistically_significant": sig,
        "n_null": len(clean),
    }


def classify_acf_gate(
    acf_rows: Sequence[dict[str, Any]],
    null_by_lag: dict[int, dict[str, Any]],
    *,
    config: StudyConfiguration,
) -> tuple[GateState, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    any_valid = False
    any_detected = False
    for row in acf_rows:
        lag = int(row["tick_lag"])
        acf = row["acf"]
        n = int(row["n"])
        valid = n >= config.min_observations_for_acf and acf is not None
        null = null_by_lag.get(lag, {})
        stat_sig = bool(null.get("statistically_significant", False)) if valid else False
        effect = abs(float(acf)) if acf is not None else 0.0
        effect_ok = effect >= config.minimum_effect_size_acf
        if not valid:
            classification: GateState = "INCONCLUSIVE"
        elif stat_sig and effect_ok:
            classification = "DETECTED"
            any_detected = True
            any_valid = True
        else:
            classification = "NOT_DETECTED"
            any_valid = True
        details.append({
            "tick_lag": lag, "acf": acf, "n": n,
            "statistically_significant": stat_sig,
            "effect_size": effect if acf is not None else None,
            "effect_size_above_floor": effect_ok if acf is not None else False,
            "minimum_effect_size": config.minimum_effect_size_acf,
            "classification": classification,
        })
    if any_detected:
        return "DETECTED", details
    if not any_valid:
        return "INCONCLUSIVE", details
    return "NOT_DETECTED", details


def classify_transition_gate(
    transitions: TransitionStats,
    null_same_rate: dict[str, Any],
    *,
    config: StudyConfiguration,
) -> tuple[GateState, dict[str, Any]]:
    if transitions.n_pairs < config.min_transition_pairs:
        return "INCONCLUSIVE", {
            "n_pairs": transitions.n_pairs,
            "same_direction_transition_rate": transitions.same_direction_transition_rate,
            "statistically_significant": False,
            "effect_size": None, "effect_size_above_floor": False,
            "classification": "INCONCLUSIVE",
        }
    rate = transitions.same_direction_transition_rate
    if rate is None:
        return "INCONCLUSIVE", {
            "n_pairs": transitions.n_pairs,
            "same_direction_transition_rate": None,
            "statistically_significant": False,
            "effect_size": None, "effect_size_above_floor": False,
            "classification": "INCONCLUSIVE",
        }
    effect = abs(rate - 0.5)
    effect_ok = effect >= config.minimum_effect_size_transition
    stat_sig = bool(null_same_rate.get("statistically_significant", False))
    cls: GateState = "DETECTED" if (stat_sig and effect_ok) else "NOT_DETECTED"
    return cls, {
        "n_pairs": transitions.n_pairs,
        "same_direction_transition_rate": rate,
        "statistically_significant": stat_sig,
        "effect_size": effect, "effect_size_above_floor": effect_ok,
        "minimum_effect_size": config.minimum_effect_size_transition,
        "classification": cls,
    }


def synthesize_overall(
    gate_a: GateState,
    gate_b: GateState,
    gate_c: GateState,
    *,
    study_valid: bool,
) -> OverallState:
    if not study_valid:
        return "INVALID_STUDY"
    gates = (gate_a, gate_b, gate_c)
    if any(g == "INCONCLUSIVE" for g in gates):
        return "NEEDS_MORE_DATA"
    if any(g == "DETECTED" for g in gates):
        return "CANDIDATE_STRUCTURE"
    return "NO_MEASURABLE_STRUCTURE"


@dataclass
class InstrumentCharacterization:
    instrument: str
    coverage: CoverageReport
    increment_distribution: IncrementDistribution
    acf_directional: list[dict[str, Any]]
    acf_absolute: list[dict[str, Any]]
    acf_squared: list[dict[str, Any]]
    transitions: TransitionStats
    runs: dict[str, RunStats]
    extreme_moves: dict[str, Any]
    null_comparison: dict[str, Any]
    gate_a: GateState
    gate_a_details: list[dict[str, Any]]
    gate_b: GateState
    gate_b_details: dict[str, Any]
    gate_c: GateState
    gate_c_details: dict[str, Any]
    overall: OverallState
    pip_size: float | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "coverage": self.coverage.to_dict(),
            "increment_distribution": self.increment_distribution.to_dict(),
            "acf_directional": self.acf_directional,
            "acf_absolute": self.acf_absolute,
            "acf_squared": self.acf_squared,
            "transitions": self.transitions.to_dict(),
            "runs": {k: v.to_dict() for k, v in self.runs.items()},
            "extreme_moves": self.extreme_moves,
            "null_comparison": self.null_comparison,
            "gate_a_directional_dependence": self.gate_a,
            "gate_a_details": self.gate_a_details,
            "gate_b_volatility_dependence": self.gate_b,
            "gate_b_details": self.gate_b_details,
            "gate_c_transition_structure": self.gate_c,
            "gate_c_details": self.gate_c_details,
            "overall": self.overall,
            "pip_size": self.pip_size,
            "notes": list(self.notes),
        }


def characterize_instrument(
    instrument: str,
    series: TickSeries,
    *,
    config: StudyConfiguration = FROZEN_STUDY_CONFIG,
    pip_size: float | None = None,
) -> InstrumentCharacterization:
    notes: list[str] = []
    coverage = coverage_from_series(instrument, series)

    if series.n < config.min_ticks_for_study:
        notes.append(
            f"Insufficient ticks ({series.n} < {config.min_ticks_for_study}); "
            "study marked INVALID_STUDY for this instrument."
        )
        return InstrumentCharacterization(
            instrument=instrument, coverage=coverage,
            increment_distribution=increment_distribution([]),
            acf_directional=[], acf_absolute=[], acf_squared=[],
            transitions=transition_statistics([]),
            runs=run_length_statistics([]),
            extreme_moves={"status": "INCONCLUSIVE", "n_extreme_events": 0},
            null_comparison={},
            gate_a="INCONCLUSIVE", gate_a_details=[],
            gate_b="INCONCLUSIVE", gate_b_details={},
            gate_c="INCONCLUSIVE", gate_c_details={},
            overall="INVALID_STUDY", pip_size=pip_size, notes=notes,
        )

    deltas = compute_increments(series.prices)
    dist = increment_distribution(deltas)
    mean_interval = coverage.inter_tick.get("mean")

    acf_dir = acf_table(deltas, config.acf_lags, mean_interval_seconds=mean_interval)
    abs_deltas = [abs(d) for d in deltas]
    sq_deltas = [d * d for d in deltas]
    acf_abs = acf_table(abs_deltas, config.acf_lags, mean_interval_seconds=mean_interval)
    acf_sq = acf_table(sq_deltas, config.acf_lags, mean_interval_seconds=mean_interval)

    transitions = transition_statistics(deltas)
    runs = run_length_statistics(deltas)
    extreme = extreme_move_analysis(
        deltas,
        window=config.extreme_move_window,
        sigma=config.extreme_move_threshold_sigma,
        horizons=config.future_response_horizons,
        min_events=config.min_extreme_events,
    )

    rng = random.Random(config.seed)
    null_acf_dir: dict[int, list[float | None]] = {lag: [] for lag in config.acf_lags}
    null_acf_abs: dict[int, list[float | None]] = {lag: [] for lag in config.acf_lags}
    null_acf_sq: dict[int, list[float | None]] = {lag: [] for lag in config.acf_lags}
    null_same_rates: list[float | None] = []

    for _ in range(config.null_simulations):
        shuffled = shuffle_increments(deltas, rng)
        for lag in config.acf_lags:
            a, _ = acf_at_lag(shuffled, lag)
            null_acf_dir[lag].append(a)
            aa, _ = acf_at_lag([abs(x) for x in shuffled], lag)
            null_acf_abs[lag].append(aa)
            asq, _ = acf_at_lag([x * x for x in shuffled], lag)
            null_acf_sq[lag].append(asq)
        tr = transition_statistics(shuffled)
        null_same_rates.append(tr.same_direction_transition_rate)

    null_dir_by_lag = {
        lag: null_comparison(
            next((r["acf"] for r in acf_dir if r["tick_lag"] == lag), None),
            null_acf_dir[lag],
            significance_level=config.significance_level,
        )
        for lag in config.acf_lags
    }
    null_abs_by_lag = {
        lag: null_comparison(
            next((r["acf"] for r in acf_abs if r["tick_lag"] == lag), None),
            null_acf_abs[lag],
            significance_level=config.significance_level,
        )
        for lag in config.acf_lags
    }
    null_sq_by_lag = {
        lag: null_comparison(
            next((r["acf"] for r in acf_sq if r["tick_lag"] == lag), None),
            null_acf_sq[lag],
            significance_level=config.significance_level,
        )
        for lag in config.acf_lags
    }
    null_trans = null_comparison(
        transitions.same_direction_transition_rate,
        null_same_rates,
        significance_level=config.significance_level,
    )

    gate_a, gate_a_details = classify_acf_gate(acf_dir, null_dir_by_lag, config=config)
    gate_b_abs, details_abs = classify_acf_gate(acf_abs, null_abs_by_lag, config=config)
    gate_b_sq, details_sq = classify_acf_gate(acf_sq, null_sq_by_lag, config=config)
    if gate_b_abs == "DETECTED" or gate_b_sq == "DETECTED":
        gate_b: GateState = "DETECTED"
    elif gate_b_abs == "INCONCLUSIVE" and gate_b_sq == "INCONCLUSIVE":
        gate_b = "INCONCLUSIVE"
    else:
        gate_b = "NOT_DETECTED"
    gate_b_details: dict[str, Any] = {
        "absolute_return": details_abs,
        "squared_return": details_sq,
    }
    gate_c, gate_c_details = classify_transition_gate(
        transitions, null_trans, config=config
    )
    overall = synthesize_overall(gate_a, gate_b, gate_c, study_valid=True)

    notes.append(
        "Tick lags are index lags, not guaranteed wall-clock seconds. "
        "approximate_wall_clock_duration_seconds uses mean inter-tick interval only."
    )
    notes.append(
        "Null model preserves the marginal increment distribution while destroying "
        "temporal order (shuffle). This is a temporal-order null, not proof that "
        "the true generator is IID."
    )
    notes.append(
        "Detection requires BOTH statistical significance under the preregistered "
        "null AND the frozen minimum-effect-size floor."
    )
    if pip_size is not None:
        notes.append(
            f"Instrument pip_size={pip_size}. Statistical dependence below meaningful "
            "instrument resolution is not automatically an economically actionable "
            "candidate. No execution-cost model is applied in this milestone."
        )
    else:
        notes.append(
            "pip_size metadata unavailable for this instrument; quantization caveat "
            "cannot be fully quantified."
        )

    return InstrumentCharacterization(
        instrument=instrument, coverage=coverage, increment_distribution=dist,
        acf_directional=acf_dir, acf_absolute=acf_abs, acf_squared=acf_sq,
        transitions=transitions, runs=runs, extreme_moves=extreme,
        null_comparison={
            "directional_acf": {str(k): v for k, v in null_dir_by_lag.items()},
            "absolute_acf": {str(k): v for k, v in null_abs_by_lag.items()},
            "squared_acf": {str(k): v for k, v in null_sq_by_lag.items()},
            "same_direction_transition_rate": null_trans,
            "null_simulations": config.null_simulations,
            "seed": config.seed,
            "method": "shuffle_increments",
        },
        gate_a=gate_a, gate_a_details=gate_a_details,
        gate_b=gate_b, gate_b_details=gate_b_details,
        gate_c=gate_c, gate_c_details=gate_c_details,
        overall=overall, pip_size=pip_size, notes=notes,
    )


@dataclass
class StatisticalCharacterizationReport:
    configuration: StudyConfiguration
    instruments: list[InstrumentCharacterization]
    overall_synthesis: OverallState
    software_study_version: str = STUDY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.configuration.study_id,
            "study_version": self.configuration.study_version,
            "software_study_version": self.software_study_version,
            "configuration": self.configuration.to_dict(),
            "instruments": [i.to_dict() for i in self.instruments],
            "overall_synthesis": self.overall_synthesis,
            "interpretation_rules": {
                "negative": (
                    "no measurable structure was detected under the preregistered "
                    "tests and effect-size thresholds"
                ),
                "positive": (
                    "a candidate statistical property has been detected and may "
                    "justify a separately preregistered hypothesis study"
                ),
                "not_a_trading_strategy": True,
            },
        }


def _combine_overall(states: Sequence[OverallState]) -> OverallState:
    if not states:
        return "INVALID_STUDY"
    if all(s == "INVALID_STUDY" for s in states):
        return "INVALID_STUDY"
    if any(s == "CANDIDATE_STRUCTURE" for s in states):
        return "CANDIDATE_STRUCTURE"
    if any(s in ("NEEDS_MORE_DATA", "INVALID_STUDY") for s in states):
        return "NEEDS_MORE_DATA"
    return "NO_MEASURABLE_STRUCTURE"


def run_statistical_characterization(
    *,
    data_root: Path | str,
    instruments: Sequence[str] = DEFAULT_INSTRUMENTS,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
    config: StudyConfiguration = FROZEN_STUDY_CONFIG,
    pip_sizes: dict[str, float | None] | None = None,
) -> StatisticalCharacterizationReport:
    store = ParquetTickStore(Path(data_root))
    repo = TickRepository(store)
    pip_sizes = pip_sizes or {}
    results: list[InstrumentCharacterization] = []
    for inst in instruments:
        series = load_tick_series(
            repo, inst, start_epoch=start_epoch, end_epoch=end_epoch
        )
        results.append(
            characterize_instrument(
                inst, series, config=config, pip_size=pip_sizes.get(inst)
            )
        )
    overall = _combine_overall([r.overall for r in results])
    return StatisticalCharacterizationReport(
        configuration=config, instruments=results, overall_synthesis=overall,
    )


def format_markdown_report(report: StatisticalCharacterizationReport) -> str:
    lines: list[str] = []
    cfg = report.configuration
    lines.append("# Milestone 6D — Instrument Statistical Characterization")
    lines.append("")
    lines.append("## 1. Study scope")
    lines.append("")
    lines.append(
        "Research-only characterization of temporal structure in synthetic tick "
        "processes. This is **not** a trading strategy and does not generate signals."
    )
    lines.append("")
    lines.append("## 2. Frozen configuration")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(cfg.to_dict(), indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append(f"**Overall synthesis:** `{report.overall_synthesis}`")
    lines.append("")

    for inst in report.instruments:
        lines.append(f"## Instrument: `{inst.instrument}`")
        lines.append("")
        lines.append("### 3. Dataset coverage")
        lines.append("")
        cov = inst.coverage
        lines.append(f"- tick_count: {cov.tick_count}")
        lines.append(f"- earliest_epoch: {cov.earliest_epoch}")
        lines.append(f"- latest_epoch: {cov.latest_epoch}")
        lines.append(f"- calendar_span_seconds: {cov.calendar_span_seconds}")
        lines.append(f"- calendar_span_days: {cov.calendar_span_days}")
        lines.append("")
        lines.append("### 4. Tick interval characteristics")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(cov.inter_tick, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### 5. Return distribution")
        lines.append("")
        lines.append("```json")
        lines.append(
            json.dumps(inst.increment_distribution.to_dict(), indent=2, sort_keys=True)
        )
        lines.append("```")
        lines.append("")
        lines.append(
            "Skewness: bias-corrected Fisher-Pearson. "
            "Excess kurtosis: bias-corrected Fisher (normal = 0). "
            "Percentiles: Hyndman-Fan type 7."
        )
        lines.append("")
        lines.append("### 6. Directional dependence (Gate A)")
        lines.append("")
        lines.append(f"**Gate A:** `{inst.gate_a}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(inst.gate_a_details, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### 7. Volatility dependence (Gate B)")
        lines.append("")
        lines.append(f"**Gate B:** `{inst.gate_b}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(inst.gate_b_details, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### 8. Transition / run analysis (Gate C)")
        lines.append("")
        lines.append(f"**Gate C:** `{inst.gate_c}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(inst.transitions.to_dict(), indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("```json")
        lines.append(
            json.dumps(
                {k: v.to_dict() for k, v in inst.runs.items()}, indent=2, sort_keys=True
            )
        )
        lines.append("```")
        lines.append("")
        lines.append("### 9. Extreme-move analysis")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(inst.extreme_moves, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### 10. Randomized null comparison")
        lines.append("")
        lines.append(
            "Null preserves marginal increment distribution; destroys temporal order "
            "(deterministic shuffle)."
        )
        lines.append("")
        lines.append("### 11–14. Gate summary")
        lines.append("")
        lines.append(f"- Gate A (directional): `{inst.gate_a}`")
        lines.append(f"- Gate B (volatility): `{inst.gate_b}`")
        lines.append(f"- Gate C (transitions): `{inst.gate_c}`")
        lines.append(f"- Overall: `{inst.overall}`")
        lines.append("")
        lines.append("### 15. Limitations")
        lines.append("")
        for n in inst.notes:
            lines.append(f"- {n}")
        lines.append("")
        lines.append("### 16. Explicit non-strategy statement")
        lines.append("")
        lines.append(
            "This characterization is **not** a trading strategy. Detected structure "
            "(if any) is only a candidate statistical property that may justify a "
            "separately preregistered hypothesis study. Absence of detection means "
            "no measurable structure under the preregistered tests and effect-size "
            "thresholds — not that the instrument is proven random."
        )
        lines.append("")

    return "\n".join(lines)


def write_artifacts(
    report: StatisticalCharacterizationReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "statistical_characterization.json"
    md_path = out / "statistical_characterization_report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(format_markdown_report(report), encoding="utf-8")
    return json_path, md_path
