"""Deterministic historical tick-level trade simulation (Milestone 2C).

Takes an immutable :class:`~smb.trade.models.TradeCandidate` and a
chronological stream of :class:`~smb.deriv.history.Tick` objects and
returns an immutable :class:`~smb.simulation.models.TradeSimulationResult`.

Causality rules (strict):
- Only ticks with ``epoch > candidate.signal_epoch`` are eligible.
- Simulation stops at ``signal_epoch + max_duration_seconds`` (inclusive).
- Ticks at or before the signal, and after the horizon, never affect the result.

Entry is touch-based at the candidate's planned ``entry_price`` (no slippage).
Same-tick TP+SL ambiguity is resolved conservatively as SL.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from smb.deriv.history import Tick
from smb.simulation.models import (
    ExitReason,
    SimulationConfig,
    SimulationOutcome,
    TradeSimulationResult,
)
from smb.strategy.models import Direction
from smb.trade.models import TradeCandidate


def _is_finite_price(price: float) -> bool:
    return isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(
        price
    )


class SimulationEngine:
    """Simulate one trade candidate against a chronological tick stream.

    The engine does not fetch data, generate signals, construct trades, or
    compute MAE/MFE. Callers supply the bounded tick stream covering
    ``(signal_epoch, signal_epoch + max_duration]``.
    """

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config if config is not None else SimulationConfig()

    def simulate(
        self,
        candidate: TradeCandidate,
        ticks: Iterable[Tick],
    ) -> TradeSimulationResult:
        """Run a deterministic simulation for ``candidate``.

        Parameters
        ----------
        candidate:
            Immutable trade specification from Milestone 2B.
        ticks:
            Chronological historical ticks. Only epochs strictly after
            ``candidate.signal_epoch`` and at or before the horizon are used.
            Source order is respected; the engine does not reorder.

        Returns
        -------
        TradeSimulationResult
            Immutable outcome (NO_FILL / TP / SL / TIMEOUT).
        """
        signal_epoch = candidate.signal_epoch
        horizon_end = signal_epoch + self.config.max_duration_seconds
        direction = candidate.direction
        entry_price = candidate.entry_price
        stop_loss = candidate.stop_loss
        take_profit = candidate.take_profit

        if not all(
            _is_finite_price(p) for p in (entry_price, stop_loss, take_profit)
        ):
            raise ValueError("candidate prices must be finite")

        filled = False
        entry_time: int | None = None
        fill_price: float | None = None
        # Last eligible tick epoch observed (for TIMEOUT exit_time)
        last_eligible_epoch: int | None = None

        for tick in ticks:
            epoch = tick.epoch
            price = tick.price

            # Strict causality: signal tick and earlier never participate
            if epoch <= signal_epoch:
                continue
            # Do not simulate beyond the research horizon
            if epoch > horizon_end:
                break

            if not _is_finite_price(price):
                raise ValueError(f"tick price must be finite (epoch={epoch})")

            last_eligible_epoch = epoch

            if not filled:
                if self._entry_touched(direction, price, entry_price):
                    filled = True
                    entry_time = epoch
                    fill_price = entry_price  # planned price; no slippage

                    # Same tick may also satisfy exit — evaluate immediately
                    exit_hit = self._exit_hit(
                        direction, price, stop_loss, take_profit
                    )
                    if exit_hit is not None:
                        return self._result(
                            candidate,
                            outcome=exit_hit,
                            filled=True,
                            entry_time=entry_time,
                            entry_price=fill_price,
                            exit_time=epoch,
                            exit_price=self._exit_price(exit_hit, stop_loss, take_profit),
                            exit_reason=(
                                ExitReason.SL
                                if exit_hit == SimulationOutcome.SL
                                else ExitReason.TP
                            ),
                            duration_seconds=epoch - entry_time,
                        )
                continue

            # Already filled: evaluate exit on subsequent ticks
            exit_hit = self._exit_hit(direction, price, stop_loss, take_profit)
            if exit_hit is not None:
                return self._result(
                    candidate,
                    outcome=exit_hit,
                    filled=True,
                    entry_time=entry_time,
                    entry_price=fill_price,
                    exit_time=epoch,
                    exit_price=self._exit_price(exit_hit, stop_loss, take_profit),
                    exit_reason=(
                        ExitReason.SL
                        if exit_hit == SimulationOutcome.SL
                        else ExitReason.TP
                    ),
                    duration_seconds=epoch - entry_time,  # type: ignore[operator]
                )

        if not filled:
            return self._result(
                candidate,
                outcome=SimulationOutcome.NO_FILL,
                filled=False,
                entry_time=None,
                entry_price=None,
                exit_time=None,
                exit_price=None,
                exit_reason=ExitReason.NONE,
                duration_seconds=None,
            )

        # Filled but neither TP nor SL within horizon
        assert entry_time is not None and fill_price is not None
        timeout_epoch = (
            last_eligible_epoch if last_eligible_epoch is not None else horizon_end
        )
        return self._result(
            candidate,
            outcome=SimulationOutcome.TIMEOUT,
            filled=True,
            entry_time=entry_time,
            entry_price=fill_price,
            exit_time=timeout_epoch,
            exit_price=None,  # no exit price on pure timeout
            exit_reason=ExitReason.TIMEOUT,
            duration_seconds=timeout_epoch - entry_time,
        )

    @staticmethod
    def _entry_touched(
        direction: Direction, price: float, entry_price: float
    ) -> bool:
        if direction == Direction.LONG:
            return price <= entry_price
        return price >= entry_price

    @staticmethod
    def _exit_hit(
        direction: Direction,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> SimulationOutcome | None:
        """Return SL, TP, or None. If both would hit, prefer SL (conservative)."""
        if direction == Direction.LONG:
            hit_sl = price <= stop_loss
            hit_tp = price >= take_profit
        else:
            hit_sl = price >= stop_loss
            hit_tp = price <= take_profit

        if hit_sl and hit_tp:
            return SimulationOutcome.SL
        if hit_sl:
            return SimulationOutcome.SL
        if hit_tp:
            return SimulationOutcome.TP
        return None

    @staticmethod
    def _exit_price(
        outcome: SimulationOutcome, stop_loss: float, take_profit: float
    ) -> float:
        if outcome == SimulationOutcome.SL:
            return stop_loss
        if outcome == SimulationOutcome.TP:
            return take_profit
        raise ValueError(f"no exit price for outcome={outcome}")

    @staticmethod
    def _result(
        candidate: TradeCandidate,
        *,
        outcome: SimulationOutcome,
        filled: bool,
        entry_time: int | None,
        entry_price: float | None,
        exit_time: int | None,
        exit_price: float | None,
        exit_reason: ExitReason,
        duration_seconds: int | None,
    ) -> TradeSimulationResult:
        return TradeSimulationResult(
            instrument=candidate.instrument,
            direction=candidate.direction,
            signal_epoch=candidate.signal_epoch,
            outcome=outcome,
            filled=filled,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=exit_reason,
            duration_seconds=duration_seconds,
            candidate=candidate,
        )
