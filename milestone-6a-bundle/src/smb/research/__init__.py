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
from smb.research.expanded_baseline import (
    BASELINE_5B_REFERENCE,
    ExpandedBaselineReport,
    InstrumentExpandedComparison,
    MetricDelta,
    analysis_to_5d_metrics,
    build_expanded_baseline_report,
    compare_instrument,
    format_expanded_baseline_report,
    run_expanded_baseline,
    write_expanded_baseline_artifacts,
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
    "BASELINE_5B_REFERENCE",
    "ExpandedBaselineReport",
    "InstrumentExpandedComparison",
    "MetricDelta",
    "analysis_to_5d_metrics",
    "build_expanded_baseline_report",
    "compare_instrument",
    "format_expanded_baseline_report",
    "run_expanded_baseline",
    "write_expanded_baseline_artifacts",
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

from smb.research.diagnostic_analysis import (
    BaselineDiagnosticAnalyzer,
    BaselineDiagnosticReport,
    FailureModeEntry,
    InstrumentDiagnostic,
    OutcomeDistribution,
    format_diagnostic_report,
    run_baseline_diagnostics,
    sample_size_class,
    sample_size_warning,
    write_diagnostic_artifacts,
)

__all__ += [
    "BaselineDiagnosticAnalyzer",
    "BaselineDiagnosticReport",
    "FailureModeEntry",
    "InstrumentDiagnostic",
    "OutcomeDistribution",
    "format_diagnostic_report",
    "run_baseline_diagnostics",
    "sample_size_class",
    "sample_size_warning",
    "write_diagnostic_artifacts",
]

