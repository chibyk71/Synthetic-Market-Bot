"""Structured research records for live strategy + simulation (Milestone 4B).

Records distinguish: no signal vs signal generated vs risk-rejected vs
simulated trade lifecycle. No real/demo execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from smb.simulation.models import TradeSimulationResult
from smb.strategy.models import Direction, StrategySignal
from smb.trade.models import RejectionReason, TradeCandidate


class LiveEventKind(StrEnum):
    """Observable lifecycle events from the live runner."""

    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_REJECTED = "signal_rejected"
    TRADE_OPENED = "trade_opened"
    TRADE_CLOSED = "trade_closed"
    SIGNAL_DUPLICATE = "signal_duplicate"


@dataclass(frozen=True, slots=True)
class LiveSignalRecord:
    """A strategy signal observed in live mode (before or without risk accept)."""

    kind: LiveEventKind
    instrument: str
    signal_epoch: int
    direction: Direction
    signal: StrategySignal
    rejection_reason: RejectionReason | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LiveTradeOpenedRecord:
    """Accepted risk construction → simulation opened."""

    kind: LiveEventKind
    instrument: str
    signal_epoch: int
    direction: Direction
    candidate: TradeCandidate
    opened_at_epoch: int


@dataclass(frozen=True, slots=True)
class LiveTradeClosedRecord:
    """Simulation completed with a terminal outcome."""

    kind: LiveEventKind
    instrument: str
    signal_epoch: int
    direction: Direction
    candidate: TradeCandidate
    result: TradeSimulationResult
    closed_at_epoch: int


LiveResearchRecord = LiveSignalRecord | LiveTradeOpenedRecord | LiveTradeClosedRecord


def signal_identity(signal: StrategySignal) -> tuple[str, int, str]:
    """Deterministic identity for deduplication of finalized strategy events."""
    return (signal.instrument, signal.signal_epoch, signal.direction.value)
