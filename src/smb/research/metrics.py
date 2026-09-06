"""Research metrics calculator (Milestone 2D) — MAE / MFE observer.

Consumes an immutable :class:`~smb.simulation.models.TradeSimulationResult`
and the same chronological tick stream used for simulation. Does **not**
re-simulate fills or change 2C outcomes.

Observation bounds come exclusively from the simulation result (``entry_time``
and ``exit_time``), never from a separately configured horizon.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from smb.deriv.history import Tick
from smb.research.models import TradeResearchMetrics
from smb.simulation.models import SimulationOutcome, TradeSimulationResult
from smb.strategy.models import Direction


def _is_finite_price(price: float) -> bool:
    return (
        isinstance(price, (int, float))
        and not isinstance(price, bool)
        and math.isfinite(price)
    )


class ResearchMetricsCalculator:
    """Compute direction-aware MFE / MAE for a completed simulation.

    Observation window for filled trades:
    - starts at the actual fill epoch (``simulation.entry_time``)
    - ends at the authoritative simulation exit (``simulation.exit_time``)
      for TP, SL, and TIMEOUT alike
    - only ticks with ``epoch >= entry_time`` and ``epoch <= exit_time``
      participate (and still ``epoch > signal_epoch``)
    - pre-fill ticks never affect MAE/MFE

    The calculator does **not** reconstruct a horizon from
    :class:`~smb.simulation.models.SimulationConfig`; the 2C result is the
    sole source of truth for the observation boundary.
    """

    def calculate(
        self,
        simulation: TradeSimulationResult,
        ticks: Iterable[Tick],
    ) -> TradeResearchMetrics:
        """Derive :class:`TradeResearchMetrics` from a simulation result.

        Parameters
        ----------
        simulation:
            Authoritative 2C outcome (not recomputed here).
        ticks:
            Chronological historical ticks (same stream semantics as 2C).
        """
        if simulation.outcome == SimulationOutcome.NO_FILL or not simulation.filled:
            return TradeResearchMetrics(
                instrument=simulation.instrument,
                direction=simulation.direction,
                signal_epoch=simulation.signal_epoch,
                outcome=simulation.outcome,
                filled=False,
                entry_time=None,
                entry_price=None,
                exit_time=None,
                exit_price=None,
                mfe=None,
                mae=None,
                mfe_time=None,
                mae_time=None,
                observation_start=None,
                observation_end=None,
                simulation=simulation,
            )

        if simulation.entry_time is None or simulation.entry_price is None:
            raise ValueError("filled simulation requires entry_time and entry_price")
        if simulation.exit_time is None:
            raise ValueError(
                "filled simulation requires exit_time "
                "(TP/SL exit epoch or TIMEOUT horizon_end)"
            )
        if not _is_finite_price(simulation.entry_price):
            raise ValueError("entry_price must be finite")

        signal_epoch = simulation.signal_epoch
        entry_time = simulation.entry_time
        entry_price = simulation.entry_price
        window_end = simulation.exit_time
        direction = simulation.direction

        mfe = 0.0
        mae = 0.0
        mfe_time = entry_time
        mae_time = entry_time
        saw_post_fill = False

        for tick in ticks:
            epoch = tick.epoch
            price = tick.price

            if epoch <= signal_epoch:
                continue
            if epoch < entry_time:
                continue
            if epoch > window_end:
                break

            if not _is_finite_price(price):
                raise ValueError(f"tick price must be finite (epoch={epoch})")

            saw_post_fill = True
            fav, adv = self._excursions(direction, entry_price, price)
            if fav > mfe:
                mfe = fav
                mfe_time = epoch
            if adv > mae:
                mae = adv
                mae_time = epoch

        # No post-fill ticks observed: zero excursions at fill time
        if not saw_post_fill:
            mfe = 0.0
            mae = 0.0
            mfe_time = entry_time
            mae_time = entry_time

        return TradeResearchMetrics(
            instrument=simulation.instrument,
            direction=simulation.direction,
            signal_epoch=simulation.signal_epoch,
            outcome=simulation.outcome,
            filled=True,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=simulation.exit_time,
            exit_price=simulation.exit_price,
            mfe=mfe,
            mae=mae,
            mfe_time=mfe_time,
            mae_time=mae_time,
            observation_start=entry_time,
            observation_end=window_end,
            simulation=simulation,
        )

    @staticmethod
    def _excursions(
        direction: Direction, entry_price: float, price: float
    ) -> tuple[float, float]:
        """Return (favorable, adverse) non-negative price distances."""
        if direction == Direction.LONG:
            favorable = price - entry_price
            adverse = entry_price - price
        else:
            favorable = entry_price - price
            adverse = price - entry_price
        return max(favorable, 0.0), max(adverse, 0.0)
