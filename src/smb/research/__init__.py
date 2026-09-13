"""Research metrics, historical harness, baseline analysis, and campaigns.

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
from smb.research.campaign import (
    CampaignConfig,
    CampaignError,
    CampaignResults,
    CampaignRunner,
    CampaignSummary,
    format_campaign_report,
    run_campaign,
)
from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
    CampaignBaselineAnalyzer,
    MultiInstrumentComparison,
    SegmentMetrics,
    compare_instruments,
    format_campaign_baseline_report,
    format_comparison_report,
    run_baseline_campaign,
    write_analysis_artifacts,
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
    "CampaignConfig",
    "CampaignError",
    "CampaignResults",
    "CampaignRunner",
    "CampaignSummary",
    "format_campaign_report",
    "run_campaign",
    "CampaignBaselineAnalysis",
    "CampaignBaselineAnalyzer",
    "MultiInstrumentComparison",
    "SegmentMetrics",
    "compare_instruments",
    "format_campaign_baseline_report",
    "format_comparison_report",
    "run_baseline_campaign",
    "write_analysis_artifacts",
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

# Optional Milestone 3C exports when diagnostic module is present.
try:
    from smb.research.diagnostic import (  # noqa: E402
        BaselineDiagnosticCalculator,
        BaselineDiagnosticReport,
        OutcomeDecomposition,
        format_diagnostic_report,
        sample_status,
    )

    __all__ += [
        "BaselineDiagnosticCalculator",
        "BaselineDiagnosticReport",
        "OutcomeDecomposition",
        "format_diagnostic_report",
        "sample_status",
    ]
except ImportError:  # pragma: no cover
    pass
