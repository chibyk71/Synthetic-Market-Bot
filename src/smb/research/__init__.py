"""Milestone 2D research metrics + historical experiment harness.

Observer layer: MAE/MFE over simulated trades, and offline composition of
strategy → risk → simulation → validation for stored historical ticks.

Does **not** change strategy, risk, simulation, or execution semantics.
"""

from smb.research.experiment import (
    ExperimentConfig,
    ExperimentError,
    ExperimentResult,
    ExperimentSummary,
    HistoricalResearchExperiment,
    TradeExperimentRow,
    format_summary,
    run_experiment,
)
from smb.research.metrics import ResearchMetricsCalculator
from smb.research.models import TradeResearchMetrics

__all__ = [
    "ResearchMetricsCalculator",
    "TradeResearchMetrics",
    "ExperimentConfig",
    "ExperimentError",
    "ExperimentResult",
    "ExperimentSummary",
    "HistoricalResearchExperiment",
    "TradeExperimentRow",
    "format_summary",
    "run_experiment",
]
