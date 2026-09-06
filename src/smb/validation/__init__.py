"""Milestone 3A — strategy validation over completed 2C/2D results.

Aggregates historical simulation and research metrics into an immutable
:class:`StrategyValidationReport`. Does not re-simulate or alter outcomes.
"""

from smb.validation.calculator import StrategyValidationCalculator
from smb.validation.models import (
    CohortStats,
    DurationStats,
    ExcursionStats,
    OutcomeCounts,
    RateStats,
    StrategyValidationReport,
)

__all__ = [
    "StrategyValidationCalculator",
    "StrategyValidationReport",
    "CohortStats",
    "OutcomeCounts",
    "RateStats",
    "ExcursionStats",
    "DurationStats",
]
