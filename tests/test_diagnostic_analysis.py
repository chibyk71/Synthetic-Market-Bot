"""Milestone 5F — structured baseline diagnostic research tests.

Strategy / trade / simulation remain frozen. Diagnostics must derive from
supplied campaign analysis data (no hard-coded 5D metrics).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from smb.research.campaign_baseline import (
    CampaignBaselineAnalysis,
    SegmentMetrics,
)
from smb.research.diagnostic_analysis import (
    BaselineDiagnosticAnalyzer,
    format_diagnostic_report,
    sample_size_class,
    sample_size_warning,
    write_diagnostic_artifacts,
)
from smb.research.experiment import TradeExperimentRow
from smb.simulation.models import SimulationOutcome


def _seg(
    *,
    label: str = "overall",
    signals: int = 0,
    accepted: int | None = None,
    filled: int = 0,
    wins: int = 0,
    losses: int = 0,
    timeouts: int = 0,
    no_fills: int = 0,
    win_rate: float | None = None,
    average_r: float | None = None,
    total_r: float | None = None,
) -> SegmentMetrics:
    if accepted is None:
        accepted = signals
    return SegmentMetrics(
        label=label,
        signals=signals,
        accepted=accepted,
        rejected=signals - accepted,
        filled=filled,
        wins=wins,
        losses=losses,
        timeouts=timeouts,
        no_fills=no_fills,
        win_rate=win_rate,
        average_r=average_r,
        total_r=total_r,
        profit_factor=None,
        maximum_drawdown_r=None,
        average_mae=None,
        average_mfe=None,
        median_mae=None,
        median_mfe=None,
        average_duration_seconds=None,
        median_duration_seconds=None,
        sample_note="test",
    )
