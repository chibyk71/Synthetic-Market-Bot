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


from smb.research.horizon_exit_study import (  # noqa: E402
    DEFAULT_BASELINE_HORIZON_SECONDS,
    DEFAULT_INSTRUMENTS,
    DEFAULT_R_THRESHOLDS,
    FROZEN_BASELINE_HORIZON_SECONDS,
    HorizonExitStudyReport,
    InstrumentStudyResult,
    OutcomeCounts,
    ScenarioResult,
    StudyTradeRecord,
    analyze_horizon_exit_study,
    analyze_instrument,
    format_horizon_exit_study_report,
    paired_horizon_comparison,
    records_from_experiment_result,
    run_horizon_exit_study_on_results,
    study_record_from_row,
    write_horizon_exit_study_artifacts,
)

__all__ += [
    "DEFAULT_BASELINE_HORIZON_SECONDS",
    "FROZEN_BASELINE_HORIZON_SECONDS",
    "DEFAULT_INSTRUMENTS",
    "DEFAULT_R_THRESHOLDS",
    "HorizonExitStudyReport",
    "InstrumentStudyResult",
    "OutcomeCounts",
    "ScenarioResult",
    "StudyTradeRecord",
    "analyze_horizon_exit_study",
    "paired_horizon_comparison",
    "analyze_instrument",
    "format_horizon_exit_study_report",
    "records_from_experiment_result",
    "run_horizon_exit_study_on_results",
    "study_record_from_row",
    "write_horizon_exit_study_artifacts",
]

from smb.research.strategy_filter_experiments import (  # noqa: E402
    DEFAULT_INSTRUMENTS as FILTER_DEFAULT_INSTRUMENTS,
)
from smb.research.strategy_filter_experiments import (  # noqa: E402
    FROZEN_BASELINE_HORIZON_SECONDS as FILTER_FROZEN_BASELINE_HORIZON_SECONDS,
)
from smb.research.strategy_filter_experiments import (  # noqa: E402
    CohortMetrics,
    DisplacementFVGQualityFilterConfig,
    ExperimentFamily,
    FilterDecision,
    FilterDecisionKind,
    FilterExperimentConfig,
    FilterExperimentReport,
    InstrumentFilterResult,
    M15ContextFilterConfig,
    SessionRegimeFilterConfig,
    TrendDirectionFilterConfig,
    analyze_filter_experiment,
    analyze_instrument_filter,
    build_filter_config_from_args,
    evaluate_filter,
    format_filter_experiment_report,
    parse_experiment_family,
    run_filter_experiment_on_results,
    write_filter_experiment_artifacts,
)
from smb.research.strategy_filter_experiments import (  # noqa: E402
    leakage_review_notes as filter_leakage_review_notes,
)

__all__ += [
    "FILTER_DEFAULT_INSTRUMENTS",
    "FILTER_FROZEN_BASELINE_HORIZON_SECONDS",
    "CohortMetrics",
    "DisplacementFVGQualityFilterConfig",
    "ExperimentFamily",
    "FilterDecision",
    "FilterDecisionKind",
    "FilterExperimentConfig",
    "FilterExperimentReport",
    "InstrumentFilterResult",
    "M15ContextFilterConfig",
    "SessionRegimeFilterConfig",
    "TrendDirectionFilterConfig",
    "analyze_filter_experiment",
    "analyze_instrument_filter",
    "build_filter_config_from_args",
    "evaluate_filter",
    "format_filter_experiment_report",
    "filter_leakage_review_notes",
    "parse_experiment_family",
    "run_filter_experiment_on_results",
    "write_filter_experiment_artifacts",
]
