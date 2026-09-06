"""Domain models for Milestone 2D research metrics (MAE / MFE).

Observer layer over :class:`~smb.simulation.models.TradeSimulationResult`.
Does not alter simulation, entry, exit, or strategy semantics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from smb.simulation.models import SimulationOutcome, TradeSimulationResult
from smb.strategy.models import Direction


def _require_finite(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class TradeResearchMetrics:
    """Immutable path metrics for one simulated trade candidate.

    Excursions are non-negative price distances from the **actual** fill price
    (``entry_price`` on the simulation result).

    For :attr:`~smb.simulation.models.SimulationOutcome.NO_FILL`, there is no
    entry, so ``mfe``, ``mae``, and their timestamps are ``None``.
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

    mfe: float | None
    """Maximum favorable excursion (price distance); ``None`` if not filled."""

    mae: float | None
    """Maximum adverse excursion (price distance); ``None`` if not filled."""

    mfe_time: int | None
    """Epoch at which MFE was first observed; ``None`` if not filled."""

    mae_time: int | None
    """Epoch at which MAE was first observed; ``None`` if not filled."""

    observation_start: int | None
    """First epoch included in excursion measurement (fill time)."""

    observation_end: int | None
    """Last epoch considered (exit or horizon); ``None`` if not filled."""

    simulation: TradeSimulationResult

    def __post_init__(self) -> None:
        if not self.filled:
            if self.outcome is not SimulationOutcome.NO_FILL:
                raise ValueError("unfilled metrics require outcome=NO_FILL")
            for name in (
                "entry_time",
                "entry_price",
                "mfe",
                "mae",
                "mfe_time",
                "mae_time",
                "observation_start",
                "observation_end",
            ):
                if getattr(self, name) is not None:
                    raise ValueError(f"NO_FILL requires {name}=None")
            return

        if self.entry_time is None or self.entry_price is None:
            raise ValueError("filled metrics require entry_time and entry_price")
        _require_finite(self.entry_price, "entry_price")
        if self.mfe is None or self.mae is None:
            raise ValueError("filled metrics require mfe and mae")
        _require_finite(self.mfe, "mfe")
        _require_finite(self.mae, "mae")
        if self.mfe < 0.0 or self.mae < 0.0:
            raise ValueError("mfe and mae must be >= 0")
        if self.mfe_time is None or self.mae_time is None:
            raise ValueError("filled metrics require mfe_time and mae_time")
        if self.observation_start is None or self.observation_end is None:
            raise ValueError("filled metrics require observation window")
        if self.observation_start > self.observation_end:
            raise ValueError("observation_start must be <= observation_end")
        if self.mfe_time < self.observation_start or self.mfe_time > self.observation_end:
            raise ValueError("mfe_time outside observation window")
        if self.mae_time < self.observation_start or self.mae_time > self.observation_end:
            raise ValueError("mae_time outside observation window")
