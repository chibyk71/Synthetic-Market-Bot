"""Domain models for Milestone 2B trade construction and risk.

Transforms a completed 2A :class:`~smb.strategy.models.StrategySignal` into an
immutable :class:`TradeCandidate` specification (or an explicit rejection).

No fills, execution, P&L, or simulation — those belong to Milestone 2C.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from smb.strategy.models import Direction, StrategySignal


class RejectionReason(StrEnum):
    """Deterministic reasons a signal cannot become a trade candidate."""

    INVALID_FVG = "invalid_fvg"
    INVALID_ENTRY = "invalid_entry"
    INVALID_STOP = "invalid_stop"
    INVALID_TARGET = "invalid_target"
    INVALID_RISK_DISTANCE = "invalid_risk_distance"
    INSUFFICIENT_RR = "insufficient_rr"
    INVALID_EQUITY = "invalid_equity"
    INVALID_POSITION_SIZE = "invalid_position_size"
    INVALID_ATR = "invalid_atr"


def _require_finite(value: float, name: str) -> None:
    """Reject NaN and ±inf for configuration / risk inputs."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class TradeConfig:
    """Immutable research parameters for trade construction.

    Defaults match the initial Milestone 2B research definition.
    All numeric parameters must be finite (no NaN / ±inf).
    """

    risk_per_trade: float = 0.01
    target_rr: float = 2.0
    minimum_rr: float = 1.5
    sl_atr_buffer: float = 0.10

    def __post_init__(self) -> None:
        _require_finite(self.risk_per_trade, "risk_per_trade")
        _require_finite(self.target_rr, "target_rr")
        _require_finite(self.minimum_rr, "minimum_rr")
        _require_finite(self.sl_atr_buffer, "sl_atr_buffer")
        if not (0.0 < self.risk_per_trade < 1.0):
            raise ValueError("risk_per_trade must be in (0, 1)")
        if self.target_rr <= 0.0:
            raise ValueError("target_rr must be > 0")
        if self.minimum_rr <= 0.0:
            raise ValueError("minimum_rr must be > 0")
        if self.sl_atr_buffer < 0.0:
            raise ValueError("sl_atr_buffer must be >= 0")


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Explicit risk inputs for construction — no broker or account APIs."""

    equity: float

    def __post_init__(self) -> None:
        _require_finite(self.equity, "equity")
        if self.equity <= 0.0:
            raise ValueError("equity must be > 0")


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    """Immutable hypothetical trade specification derived from a 2A signal.

    This is **not** an order, fill, or position. Milestone 2C owns simulation
    and fill determination.
    """

    instrument: str
    direction: Direction
    signal_epoch: int

    entry_price: float
    entry_zone_low: float
    entry_zone_high: float

    stop_loss: float
    take_profit: float

    risk_distance: float
    reward_distance: float
    risk_reward: float

    risk_percent: float
    risk_amount: float
    position_size: float

    source_signal: StrategySignal


@dataclass(frozen=True, slots=True)
class TradeConstructionResult:
    """Accepted candidate or explicit deterministic rejection."""

    accepted: bool
    trade: TradeCandidate | None
    rejection_reason: RejectionReason | None

    def __post_init__(self) -> None:
        if self.accepted:
            if self.trade is None:
                raise ValueError("accepted result requires a TradeCandidate")
            if self.rejection_reason is not None:
                raise ValueError("accepted result must not carry a rejection_reason")
        else:
            if self.trade is not None:
                raise ValueError("rejected result must not carry a TradeCandidate")
            if self.rejection_reason is None:
                raise ValueError("rejected result requires a RejectionReason")
