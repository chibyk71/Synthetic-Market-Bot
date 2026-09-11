"""Milestone 2D research metrics + historical experiment harness + 3B baseline.

Observer layer: MAE/MFE over simulated trades, offline composition of
strategy → risk → simulation → validation, and baseline analytical reports.

Does **not** change strategy, risk, simulation, or execution semantics.
"""

from smb.research.baseline import (
    BaselineAnalysisCalculator,
    BaselineAnalysisReport,
    DirectionAnalysis,
    NoFillAnalysis,
    OutcomeBreakdownRow,
    TimeoutAnalysis,
    format_baseline_analysis,
)
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
from smb.research.stats import DistributionStats, distribution, percentile

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
    "BaselineAnalysisCalculator",
    "BaselineAnalysisReport",
    "DirectionAnalysis",
    "NoFillAnalysis",
    "OutcomeBreakdownRow",
    "TimeoutAnalysis",
    "format_baseline_analysis",
    "DistributionStats",
    "distribution",
    "percentile",
]
