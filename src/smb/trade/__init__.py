"""Milestone 2B — trade construction and risk sizing.

Transforms a completed 2A :class:`~smb.strategy.models.StrategySignal` into an
immutable :class:`TradeCandidate` (or an explicit rejection).

Does **not** simulate fills, P&L, or execution — that is Milestone 2C.
"""

from smb.trade.constructor import TradeConstructor
from smb.trade.models import (
    RejectionReason,
    RiskContext,
    TradeCandidate,
    TradeConfig,
    TradeConstructionResult,
)

__all__ = [
    "TradeConstructor",
    "TradeConfig",
    "RiskContext",
    "TradeCandidate",
    "TradeConstructionResult",
    "RejectionReason",
]
