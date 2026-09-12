"""Research metrics, historical harness, baseline analysis, and diagnostics.

Observer layer only — does **not** change strategy, risk, simulation, or execution.
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
from smb.research.diagnostic import (
    BaselineDiagnosticCalculator,
    BaselineDiagnosticReport,
    OutcomeDecomposition,
    format_diagnostic_report,
    sample_status,
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
    "BaselineDiagnosticCalculator",
    "BaselineDiagnosticReport",
    "OutcomeDecomposition",
    "format_diagnostic_report",
    "sample_status",
    "DistributionStats",
    "distribution",
    "percentile",
]
