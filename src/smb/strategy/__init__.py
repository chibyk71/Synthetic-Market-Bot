"""Milestone 2A — deterministic research strategy engine.

Consumes completed M1/M15 candles chronologically and emits raw strategy
signals. Does **not** construct trades, calculate risk, or execute.
"""

from smb.strategy.engine import OutOfOrderCandleError, StrategyEngine
from smb.strategy.models import (
    Direction,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategyConfig,
    StrategySignal,
    StrategyState,
    SwingPoint,
)
from smb.strategy.swings import is_swing_high, is_swing_low, newly_confirmed_swings

__all__ = [
    "StrategyEngine",
    "OutOfOrderCandleError",
    "StrategyConfig",
    "StrategySignal",
    "StrategyState",
    "Direction",
    "SwingPoint",
    "LiquiditySweep",
    "MarketStructureBreak",
    "Displacement",
    "FairValueGap",
    "M15Context",
    "is_swing_high",
    "is_swing_low",
    "newly_confirmed_swings",
]
