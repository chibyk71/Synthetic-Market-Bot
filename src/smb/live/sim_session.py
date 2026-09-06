"""Streaming live simulation session adapter (Milestone 4B).

Uses the **same** entry/exit/timeout semantics as :class:`SimulationEngine`
without forking a second algorithm. Ticks are fed incrementally; terminal
results are identical to batch ``SimulationEngine.simulate`` on the same
ordered tick sequence.
"""

from __future__ import annotations

from smb.deriv.history import Tick
from smb.simulation.engine import SimulationEngine
from smb.simulation.models import (
    ExitReason,
    SimulationConfig,
    SimulationOutcome,
    TradeSimulationResult,
)
from smb.trade.models import TradeCandidate


class LiveSimulationSession:
    """One open live simulation driven by subsequent live ticks.

    Lifecycle mirrors :meth:`SimulationEngine.simulate`:
    - only ticks with ``epoch > signal_epoch`` participate
    - horizon is ``signal_epoch + max_duration_seconds`` (inclusive)
    - touch entry at planned price; same-tick SL preferred over TP
    - TIMEOUT reports ``horizon_end`` (not last tick) when filled but no exit
    """

    def __init__(
        self,
        candidate: TradeCandidate,
        config: SimulationConfig | None = None,
    ) -> None:
        self.candidate = candidate
        self.config = config if config is not None else SimulationConfig()
        self.signal_epoch = candidate.signal_epoch
        self.horizon_end = self.signal_epoch + self.config.max_duration_seconds
        self._ticks: list[Tick] = []
        self._filled = False
        self._entry_time: int | None = None
        self._fill_price: float | None = None
        self._closed = False
        self._result: TradeSimulationResult | None = None
        self._engine = SimulationEngine(self.config)

    @property
    def is_open(self) -> bool:
        return not self._closed

    @property
    def result(self) -> TradeSimulationResult | None:
        return self._result

    @property
    def tick_count(self) -> int:
        return len(self._ticks)

    def on_tick(self, tick: Tick) -> TradeSimulationResult | None:
        """Feed one chronological tick. Returns a result when the session closes."""
        if self._closed:
            return None

        epoch = tick.epoch
        price = tick.price
        if epoch <= self.signal_epoch:
            return None
        if epoch > self.horizon_end:
            return self._finalize_timeout_or_nofill()

        self._ticks.append(tick)

        direction = self.candidate.direction
        entry_price = self.candidate.entry_price
        stop_loss = self.candidate.stop_loss
        take_profit = self.candidate.take_profit

        if not self._filled:
            if SimulationEngine._entry_touched(direction, price, entry_price):
                self._filled = True
                self._entry_time = epoch
                self._fill_price = entry_price
                exit_hit = SimulationEngine._exit_hit(
                    direction, price, stop_loss, take_profit
                )
                if exit_hit is not None:
                    return self._close_exit(exit_hit, epoch)
            return None

        exit_hit = SimulationEngine._exit_hit(direction, price, stop_loss, take_profit)
        if exit_hit is not None:
            return self._close_exit(exit_hit, epoch)
        return None

    def force_close_at_horizon(self) -> TradeSimulationResult:
        """Close as TIMEOUT/NO_FILL when the live clock reaches the horizon."""
        if self._closed and self._result is not None:
            return self._result
        return self._finalize_timeout_or_nofill()

    def finalize_with_engine(self) -> TradeSimulationResult:
        """Recompute terminal result via batch engine for determinism checks."""
        return self._engine.simulate(self.candidate, list(self._ticks))

    def _close_exit(self, outcome: SimulationOutcome, epoch: int) -> TradeSimulationResult:
        assert self._entry_time is not None and self._fill_price is not None
        exit_price = SimulationEngine._exit_price(
            outcome, self.candidate.stop_loss, self.candidate.take_profit
        )
        reason = ExitReason.SL if outcome == SimulationOutcome.SL else ExitReason.TP
        result = SimulationEngine._result(
            self.candidate,
            outcome=outcome,
            filled=True,
            entry_time=self._entry_time,
            entry_price=self._fill_price,
            exit_time=epoch,
            exit_price=exit_price,
            exit_reason=reason,
            duration_seconds=epoch - self._entry_time,
        )
        self._closed = True
        self._result = result
        return result

    def _finalize_timeout_or_nofill(self) -> TradeSimulationResult:
        if not self._filled:
            result = SimulationEngine._result(
                self.candidate,
                outcome=SimulationOutcome.NO_FILL,
                filled=False,
                entry_time=None,
                entry_price=None,
                exit_time=None,
                exit_price=None,
                exit_reason=ExitReason.NONE,
                duration_seconds=None,
            )
        else:
            assert self._entry_time is not None and self._fill_price is not None
            result = SimulationEngine._result(
                self.candidate,
                outcome=SimulationOutcome.TIMEOUT,
                filled=True,
                entry_time=self._entry_time,
                entry_price=self._fill_price,
                exit_time=self.horizon_end,
                exit_price=None,
                exit_reason=ExitReason.TIMEOUT,
                duration_seconds=self.horizon_end - self._entry_time,
            )
        self._closed = True
        self._result = result
        return result
