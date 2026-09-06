"""Milestone 2C — deterministic historical tick-level trade simulation.

Consumes :class:`~smb.trade.models.TradeCandidate` + chronological ticks and
produces :class:`~smb.simulation.models.TradeSimulationResult`.

Does **not** generate signals, construct trades, compute MAE/MFE, or talk
to live markets / brokers.
"""

from smb.simulation.engine import SimulationEngine
from smb.simulation.models import (
    ExitReason,
    SimulationConfig,
    SimulationOutcome,
    TradeSimulationResult,
)

__all__ = [
    "SimulationEngine",
    "SimulationConfig",
    "SimulationOutcome",
    "ExitReason",
    "TradeSimulationResult",
]
