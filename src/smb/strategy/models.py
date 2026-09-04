"""Domain models for the Milestone 2A strategy engine.

Output is a raw strategy signal only — never a trade, order, or risk object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Mapping


class Direction(StrEnum):
    """Trade direction implied by the detected setup."""

    LONG = "long"
    SHORT = "short"


class StrategyState(StrEnum):
    """Internal state-machine phases (research / debugging only)."""

    IDLE = "idle"
    SWEEP_DETECTED = "sweep_detected"
    MSB_DETECTED = "msb_detected"
    DISPLACEMENT_DETECTED = "displacement_detected"
    SIGNAL = "signal"


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Research parameters for the mechanical strategy.

    Defaults match the initial Milestone 2A research definition.
    """

    swing_x: int = 2
    msb_window_bars: int = 3
    displacement_body_range_ratio: float = 0.60
    displacement_body_atr_ratio: float = 0.80
    atr_period: int = 14

    def __post_init__(self) -> None:
        if self.swing_x < 1:
            raise ValueError("swing_x must be >= 1")
        if self.msb_window_bars < 1:
            raise ValueError("msb_window_bars must be >= 1")
        if not (0.0 < self.displacement_body_range_ratio <= 1.0):
            raise ValueError("displacement_body_range_ratio must be in (0, 1]")
        if self.displacement_body_atr_ratio <= 0.0:
            raise ValueError("displacement_body_atr_ratio must be > 0")
        if self.atr_period < 1:
            raise ValueError("atr_period must be >= 1")


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """A confirmed swing high or swing low.

    Confirmation requires ``swing_x`` candles on the right side to have closed.
    ``confirmed_at_epoch`` is the end_epoch of the confirming candle
    (i.e. the earliest moment the swing may be used).
    """

    kind: Literal["high", "low"]
    price: float
    candle_start_epoch: int
    candle_end_epoch: int
    index: int  # position in the M1 history at detection time
    confirmed_at_epoch: int


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A confirmed liquidity sweep of a prior swing level."""

    direction: Direction  # LONG = bullish sweep of swing low
    swept_level: float
    sweep_candle_start_epoch: int
    sweep_candle_end_epoch: int
    sweep_candle_low: float
    sweep_candle_high: float
    sweep_candle_close: float
    swing: SwingPoint


@dataclass(frozen=True, slots=True)
class MarketStructureBreak:
    """Market structure break following a liquidity sweep."""

    direction: Direction
    broken_level: float
    msb_candle_start_epoch: int
    msb_candle_end_epoch: int
    msb_candle_close: float
    bars_after_sweep: int
    structure_swing: SwingPoint


@dataclass(frozen=True, slots=True)
class Displacement:
    """Displacement candle after MSB."""

    direction: Direction
    candle_start_epoch: int
    candle_end_epoch: int
    open: float
    high: float
    low: float
    close: float
    body: float
    range_: float
    body_range_ratio: float
    body_atr_ratio: float
    atr: float


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """Three-candle fair value gap."""

    direction: Direction
    gap_low: float
    gap_high: float
    size: float
    size_atr_ratio: float | None
    candle1_start_epoch: int
    candle2_start_epoch: int
    candle3_start_epoch: int
    candle3_end_epoch: int


@dataclass(frozen=True, slots=True)
class M15Context:
    """Broader M15 context available at signal time (descriptive only)."""

    last_m15_start_epoch: int | None
    last_m15_end_epoch: int | None
    last_m15_close: float | None
    recent_high: float | None
    recent_low: float | None
    directional_bias: Literal["bullish", "bearish", "neutral"] | None


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Immutable raw strategy signal.

    This is **not** a trade, order, or position. Later milestones own
    trade construction, risk, and execution.
    """

    instrument: str
    direction: Direction
    signal_epoch: int  # end_epoch of the candle that completed the setup (FVG)
    timeframe_context: str  # e.g. "M15+M1"
    sweep: LiquiditySweep
    msb: MarketStructureBreak
    displacement: Displacement
    fvg: FairValueGap
    m15_context: M15Context
    # Descriptive reference levels only — not executable entry/SL/TP.
    # Stored as MappingProxyType so the signal remains immutable.
    reference_levels: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reference_levels", MappingProxyType(dict(self.reference_levels))
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
