"""Milestone 6E — Short-Horizon Strategy Tournament (research-only).

Hypothesis tournament over frozen short-horizon strategies on identical
historical data. No live/demo execution, no ML, no parameter optimization.
"""

from smb.research.tournament.models import (
    TARGET_R_MULTIPLES,
    TournamentConfig,
    TournamentResult,
    TournamentRow,
    TournamentSignal,
    TournamentStrategySummary,
)
from smb.research.tournament.runner import StrategyTournamentRunner

__all__ = [
    "TARGET_R_MULTIPLES",
    "StrategyTournamentRunner",
    "TournamentConfig",
    "TournamentResult",
    "TournamentRow",
    "TournamentSignal",
    "TournamentStrategySummary",
]
