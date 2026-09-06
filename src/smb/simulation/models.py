"""Domain models for Milestone 2C tick-level trade simulation.

Consumes an immutable :class:`~smb.trade.models.TradeCandidate` and a
chronological historical tick stream; produces an immutable
:class:`TradeSimulationResult`.

No strategy logic, no MAE/MFE, no broker execution, no live data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from smb.strategy.models import Direction
from smb.trade.models import TradeCandidate


class SimulationOutcome(StrEnum):
    """Terminal outcome of a candidate simulation."""

    NO_FILL = "no_fill"
    TP = "tp"
    SL = "sl"
    TIMEOUT = "timeout"


class ExitReason(StrEnum):
    """Why the simulation stopped after (or without) a fill."""

    TP = "tp"
    SL = "sl"
    TIMEOUT = "timeout"
    NONE = "none"  # never filled


def _require_finite(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Immutable parameters for the simulation engine.

    ``max_duration_seconds`` bounds the research horizon after
    ``signal_epoch``. Default is 15 minutes (900 seconds).
    """

    max_duration_seconds: int = 900

    def __post_init__(self) -> None:
        if not isinstance(self.max_duration_seconds, int) or isinstance(
            self.max_duration_seconds, bool
        ):
            raise ValueError("max_duration_seconds must be an int")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class TradeSimulationResult:
    """Immutable result of simulating one :class:`TradeCandidate`.

    ``filled`` is False only for :attr:`SimulationOutcome.NO_FILL`.
    Entry / exit times and prices are None when not applicable.
    """

    instrument: str
    direction: Direction
    signal_epoch: int

    outcome: SimulationOutcome
    filled: bool

    entry_time: int | None
    entry_price: float | None

    exit_time: int | None
    exit_price: float | None
    exit_reason: ExitReason

    duration_seconds: int | None

    candidate: TradeCandidate

    def __post_init__(self) -> None:
        if self.outcome == SimulationOutcome.NO_FILL:
            if self.filled:
                raise ValueError("NO_FILL result must have filled=False")
            if self.entry_time is not None or self.entry_price is not None:
                raise ValueError("NO_FILL must not record entry")
            if self.exit_time is not None or self.exit_price is not None:
                raise ValueError("NO_FILL must not record exit prices/times")
            if self.exit_reason is not ExitReason.NONE:
                raise ValueError("NO_FILL requires exit_reason=NONE")
            if self.duration_seconds is not None:
                raise ValueError("NO_FILL must not record duration")
        else:
            if not self.filled:
                raise ValueError(f"{self.outcome} result must have filled=True")
            if self.entry_time is None or self.entry_price is None:
                raise ValueError(f"{self.outcome} requires entry_time and entry_price")
            _require_finite(self.entry_price, "entry_price")
            if self.outcome in (SimulationOutcome.TP, SimulationOutcome.SL):
                if self.exit_time is None or self.exit_price is None:
                    raise ValueError(f"{self.outcome} requires exit_time and exit_price")
                _require_finite(self.exit_price, "exit_price")
                expected = (
                    ExitReason.TP
                    if self.outcome == SimulationOutcome.TP
                    else ExitReason.SL
                )
                if self.exit_reason is not expected:
                    raise ValueError(
                        f"{self.outcome} requires exit_reason={expected}"
                    )
            elif self.outcome == SimulationOutcome.TIMEOUT:
                if self.exit_reason is not ExitReason.TIMEOUT:
                    raise ValueError("TIMEOUT requires exit_reason=TIMEOUT")
                if self.exit_time is None:
                    raise ValueError("TIMEOUT requires exit_time")
            if self.duration_seconds is None or self.duration_seconds < 0:
                raise ValueError("filled result requires non-negative duration_seconds")
