"""Milestone 2D — research metrics (MAE / MFE) over simulated trades.

Observer layer: consumes :class:`~smb.simulation.models.TradeSimulationResult`
plus chronological ticks and produces :class:`TradeResearchMetrics`.

Does **not** change simulation outcomes, strategy logic, or execution.
"""

from smb.research.metrics import ResearchMetricsCalculator
from smb.research.models import TradeResearchMetrics

__all__ = [
    "ResearchMetricsCalculator",
    "TradeResearchMetrics",
]
