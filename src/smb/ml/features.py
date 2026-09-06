"""Signal-time feature extraction for Milestone 3B.

All features are derived exclusively from :class:`StrategySignal` (and its
nested components). No simulation, MAE/MFE, exit, or post-signal candle data
is used.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from smb.ml.models import DEFAULT_FEATURE_SCHEMA, FEATURE_NAMES, FeatureSchema
from smb.strategy.models import Direction, StrategySignal

# Canonical instrument keys for the two supported research instruments.
# Match on Deriv underlying_symbol IDs and official display names only
# (see config/settings.toml and tests/test_symbols.py). Keys are stored
# lower-cased; comparison uses the same normalization (case-fold + strip).
_INSTRUMENT_V75_KEYS = frozenset(
    {
        "1hz75v",  # Deriv underlying_symbol for Volatility 75 (1s)
        "volatility 75 (1s) index",  # official display name
    }
)
_INSTRUMENT_STEP100_KEYS = frozenset(
    {
        "stprng",  # Deriv underlying_symbol for Step Index 100
        "step index 100",  # official display name
    }
)


def _normalize_instrument_key(instrument: str) -> str:
    """Case-fold and strip for deterministic instrument matching."""
    return instrument.strip().casefold()


def _finite_or_zero(value: float | None) -> float:
    if value is None:
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return float(value)


def _encode_direction(direction: Direction) -> float:
    if direction is Direction.LONG:
        return 1.0
    if direction is Direction.SHORT:
        return -1.0
    raise ValueError(f"unknown direction: {direction}")


def _instrument_flags(instrument: str) -> tuple[float, float]:
    """Return (v75_flag, step100_flag). Unknown instruments → (0, 0).

    Only exact matches against the canonical Deriv IDs and display names
    are accepted. No substring / fuzzy classification.
    """
    key = _normalize_instrument_key(instrument)
    if not key:
        return 0.0, 0.0
    if key in _INSTRUMENT_V75_KEYS:
        return 1.0, 0.0
    if key in _INSTRUMENT_STEP100_KEYS:
        return 0.0, 1.0
    return 0.0, 0.0


def _sweep_depth(signal: StrategySignal) -> float:
    sweep = signal.sweep
    if sweep.direction is Direction.LONG:
        # bullish sweep of swing low: extreme is the candle low
        return abs(sweep.sweep_candle_low - sweep.swept_level)
    return abs(sweep.sweep_candle_high - sweep.swept_level)


def _m15_bias_and_flag(signal: StrategySignal) -> tuple[float, float]:
    bias = signal.m15_context.directional_bias
    if bias is None:
        return 0.0, 1.0
    if bias == "bullish":
        return 1.0, 0.0
    if bias == "bearish":
        return -1.0, 0.0
    if bias == "neutral":
        return 0.0, 0.0
    return 0.0, 1.0


def _m15_range_features(signal: StrategySignal) -> tuple[float, float, float, float]:
    """Return (range, range_missing, signal_vs_mid, vs_missing)."""
    ctx = signal.m15_context
    high = ctx.recent_high
    low = ctx.recent_low
    if high is None or low is None or not math.isfinite(high) or not math.isfinite(low):
        return 0.0, 1.0, 0.0, 1.0
    rng = float(high - low)
    if rng < 0:
        rng = abs(rng)
    # Signal reference price: prefer FVG midpoint, else displacement close
    fvg = signal.fvg
    mid_fvg = (fvg.gap_low + fvg.gap_high) / 2.0
    m15_mid = (high + low) / 2.0
    if rng == 0.0:
        rel = 0.0
    else:
        rel = (mid_fvg - m15_mid) / rng
    if not math.isfinite(rel):
        rel = 0.0
    return rng, 0.0, float(rel), 0.0


def _hour_of_day(signal_epoch: int) -> float:
    """UTC hour 0–23 from unix epoch seconds."""
    dt = datetime.fromtimestamp(int(signal_epoch), tz=UTC)
    return float(dt.hour)


def extract_features(signal: StrategySignal) -> tuple[float, ...]:
    """Extract the fixed-order feature vector from a strategy signal.

    Information boundary: only fields present on ``signal`` (available at
    ``signal_epoch``). No future prices, outcomes, MFE/MAE, or candles.
    """
    v75, step = _instrument_flags(signal.instrument)
    depth = _sweep_depth(signal)
    msb_bars = float(signal.msb.bars_after_sweep)
    disp = signal.displacement
    fvg = signal.fvg
    fvg_atr = fvg.size_atr_ratio
    fvg_atr_missing = 1.0 if fvg_atr is None else 0.0
    fvg_atr_val = _finite_or_zero(fvg_atr)

    m15_bias, m15_bias_miss = _m15_bias_and_flag(signal)
    m15_range, m15_range_miss, vs_mid, vs_miss = _m15_range_features(signal)

    values = (
        _encode_direction(signal.direction),
        v75,
        step,
        float(depth),
        msb_bars,
        float(disp.body_range_ratio),
        float(disp.body_atr_ratio),
        float(disp.atr),
        float(fvg.size),
        fvg_atr_val,
        fvg_atr_missing,
        m15_bias,
        m15_bias_miss,
        m15_range,
        m15_range_miss,
        vs_mid,
        vs_miss,
        _hour_of_day(signal.signal_epoch),
    )
    if len(values) != len(FEATURE_NAMES):
        raise RuntimeError("feature vector width mismatch")
    for i, v in enumerate(values):
        if not math.isfinite(v):
            raise ValueError(f"non-finite feature at {FEATURE_NAMES[i]}: {v}")
    return values


def feature_schema() -> FeatureSchema:
    return DEFAULT_FEATURE_SCHEMA


def feature_names() -> tuple[str, ...]:
    return FEATURE_NAMES
