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

STUDY_VERSION = "6e.1"
INSTRUMENT_V75 = "volatility_75_1s"


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
    """Immutable tournament parameters."""

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
    realized_r: float | None
    mfe: float | None
    mae: float | None
    signal_metadata: Mapping[str, Any]
    candidate: TradeCandidate | None
    simulation: TradeSimulationResult | None
    metrics: TradeResearchMetrics | None


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
    total_r: float | None
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
