"""Domain models for Milestone 6E strategy tournament."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from smb.research.models import TradeResearchMetrics
from smb.simulation.models import SimulationOutcome, TradeSimulationResult
from smb.strategy.models import Direction
from smb.trade.models import TradeCandidate

# Frozen reward targets for the tournament (risk remains 1R).
TARGET_R_MULTIPLES: tuple[float, ...] = (0.30, 0.40, 0.50)

# Canonical scalping simulation horizon (seconds). Shorter than 6B 900s baseline.
DEFAULT_TOURNAMENT_HORIZON_SECONDS = 300

# Milestone 6C canonical spread cost in R units (reuse, do not invent a new model).
DEFAULT_CANONICAL_SPREAD_COST_R = 0.05

STUDY_VERSION = "6e.2"
INSTRUMENT_V75 = "volatility_75_1s"


def _targets_match_frozen(targets: tuple[float, ...]) -> bool:
    if len(targets) != len(TARGET_R_MULTIPLES):
        return False
    return all(abs(a - b) < 1e-12 for a, b in zip(targets, TARGET_R_MULTIPLES, strict=True))


@dataclass(frozen=True, slots=True)
class TournamentSignal:
    """Strategy-agnostic research signal for the tournament pipeline.

    Compatible with common trade construction → existing SimulationEngine.
    All numeric fields must be finite; strategies must use only information
    available at or before ``signal_epoch`` (no lookahead).
    """

    strategy_id: str
    instrument: str
    signal_epoch: int
    direction: Direction
    entry_price: float
    stop_loss: float
    risk_distance: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.risk_distance <= 0.0:
            raise ValueError("risk_distance must be > 0")
        if self.direction == Direction.LONG and not (self.stop_loss < self.entry_price):
            raise ValueError("LONG requires stop_loss < entry_price")
        if self.direction == Direction.SHORT and not (self.stop_loss > self.entry_price):
            raise ValueError("SHORT requires stop_loss > entry_price")


@dataclass(frozen=True, slots=True)
class TournamentConfig:
    """Immutable tournament parameters.

    Targets and horizon are **frozen** for Milestone 6E:
    ``target_r_multiples`` must be exactly ``(0.30, 0.40, 0.50)`` and
    ``max_duration_seconds`` must be exactly ``300``.
    """

    instrument: str = INSTRUMENT_V75
    start_epoch: int | None = None
    end_epoch: int | None = None
    target_r_multiples: tuple[float, ...] = TARGET_R_MULTIPLES
    max_duration_seconds: int = DEFAULT_TOURNAMENT_HORIZON_SECONDS
    equity: float = 10_000.0
    risk_per_trade: float = 0.01
    # Minimum RR gate disabled for sub-1R scalping targets.
    minimum_rr: float = 0.01
    strategy_ids: tuple[str, ...] | None = None  # None = all registered
    # Explicit 6C-style spread cost in R (subtracted from gross realized_r).
    spread_cost_r: float = DEFAULT_CANONICAL_SPREAD_COST_R

    def __post_init__(self) -> None:
        if not _targets_match_frozen(self.target_r_multiples):
            raise ValueError(
                "Milestone 6E freezes target_r_multiples to exactly "
                f"{TARGET_R_MULTIPLES}; got {self.target_r_multiples}"
            )
        if self.max_duration_seconds != DEFAULT_TOURNAMENT_HORIZON_SECONDS:
            raise ValueError(
                "Milestone 6E freezes max_duration_seconds to exactly "
                f"{DEFAULT_TOURNAMENT_HORIZON_SECONDS}; got {self.max_duration_seconds}"
            )
        if self.spread_cost_r < 0.0 or not (
            self.spread_cost_r == self.spread_cost_r  # not NaN
        ):
            raise ValueError("spread_cost_r must be finite and >= 0")


@dataclass(frozen=True, slots=True)
class TournamentRow:
    """One simulated signal × target combination."""

    strategy_id: str
    target_rr: float
    instrument: str
    signal_epoch: int
    direction: str
    accepted: bool
    filled: bool
    outcome: SimulationOutcome | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    entry_time: int | None
    exit_time: int | None
    duration_seconds: int | None
    realized_r: float | None  # gross R (pre-cost); alias of gross_r for compat
    mfe: float | None
    mae: float | None
    signal_metadata: Mapping[str, Any]
    candidate: TradeCandidate | None
    simulation: TradeSimulationResult | None
    metrics: TradeResearchMetrics | None
    # Explicit cost treatment (6C semantics): net = gross - cost when filled
    gross_r: float | None = None
    cost_r: float | None = None
    net_r: float | None = None


@dataclass(frozen=True, slots=True)
class TournamentStrategySummary:
    """Aggregate metrics for one strategy × target_rr cell."""

    strategy_id: str
    target_rr: float
    signals: int
    accepted: int
    fills: int
    no_fill: int
    tp: int
    sl: int
    timeout: int
    win_rate: float | None
    total_r: float | None  # sum of gross_r (pre-cost)
    average_r: float | None
    median_r: float | None
    profit_factor: float | None
    mean_duration_seconds: float | None
    median_duration_seconds: float | None
    mean_mae: float | None
    mean_mfe: float | None
    median_mae: float | None
    median_mfe: float | None
    target_hit_rate: float | None
    stop_hit_rate: float | None
    timeout_rate: float | None
    # Cost-adjusted aggregates (filled trades only)
    total_gross_r: float | None = None
    total_net_r: float | None = None
    average_gross_r: float | None = None
    average_net_r: float | None = None
    median_net_r: float | None = None
    profit_factor_net: float | None = None
    spread_cost_r: float | None = None


@dataclass(frozen=True, slots=True)
class TournamentResult:
    """Full deterministic tournament output."""

    study_version: str
    config: TournamentConfig
    dataset: Mapping[str, Any]
    strategy_definitions: Mapping[str, Any]
    summaries: tuple[TournamentStrategySummary, ...]
    rows: tuple[TournamentRow, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_version": self.study_version,
            "config": {
                "instrument": self.config.instrument,
                "start_epoch": self.config.start_epoch,
                "end_epoch": self.config.end_epoch,
                "target_r_multiples": list(self.config.target_r_multiples),
                "max_duration_seconds": self.config.max_duration_seconds,
                "equity": self.config.equity,
                "risk_per_trade": self.config.risk_per_trade,
                "minimum_rr": self.config.minimum_rr,
                "spread_cost_r": self.config.spread_cost_r,
                "strategy_ids": list(self.config.strategy_ids)
                if self.config.strategy_ids
                else None,
            },
            "dataset": dict(self.dataset),
            "strategy_definitions": dict(self.strategy_definitions),
            "summaries": [
                {
                    "strategy_id": s.strategy_id,
                    "target_rr": s.target_rr,
                    "signals": s.signals,
                    "accepted": s.accepted,
                    "fills": s.fills,
                    "no_fill": s.no_fill,
                    "tp": s.tp,
                    "sl": s.sl,
                    "timeout": s.timeout,
                    "win_rate": s.win_rate,
                    "total_r": s.total_r,
                    "average_r": s.average_r,
                    "median_r": s.median_r,
                    "profit_factor": s.profit_factor,
                    "total_gross_r": s.total_gross_r,
                    "total_net_r": s.total_net_r,
                    "average_gross_r": s.average_gross_r,
                    "average_net_r": s.average_net_r,
                    "median_net_r": s.median_net_r,
                    "profit_factor_net": s.profit_factor_net,
                    "spread_cost_r": s.spread_cost_r,
                    "mean_duration_seconds": s.mean_duration_seconds,
                    "median_duration_seconds": s.median_duration_seconds,
                    "mean_mae": s.mean_mae,
                    "mean_mfe": s.mean_mfe,
                    "median_mae": s.median_mae,
                    "median_mfe": s.median_mfe,
                    "target_hit_rate": s.target_hit_rate,
                    "stop_hit_rate": s.stop_hit_rate,
                    "timeout_rate": s.timeout_rate,
                }
                for s in self.summaries
            ],
            "row_count": len(self.rows),
        }
