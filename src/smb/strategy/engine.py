"""Deterministic chronological strategy engine (Milestone 2A).

Consumes completed M1 and M15 candles in time order, detects the mechanical
setup sequence, and emits immutable :class:`StrategySignal` objects.

No trades, risk, execution, or live data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from smb.market.candles import Candle
from smb.strategy.atr import atr as compute_atr
from smb.strategy.models import (
    Direction,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategyConfig,
    StrategySignal,
    StrategyState,
    SwingPoint,
)
from smb.strategy.swings import newly_confirmed_swings


class OutOfOrderCandleError(ValueError):
    """Raised when a candle is strictly earlier than the previous one of the same TF."""

    def __init__(self, timeframe: str, previous_end: int, candle_end: int) -> None:
        self.timeframe = timeframe
        self.previous_end = previous_end
        self.candle_end = candle_end
        super().__init__(
            f"Out-of-order {timeframe} candle: end_epoch {candle_end} is earlier than "
            f"previous end_epoch {previous_end}"
        )


class StrategyEngine:
    """Streaming strategy engine with explicit state and no lookahead.

    Chronological setup sequence (strict)::

        M15 context → M1 sweep → later M1 MSB → later M1 displacement → FVG → signal

    Displacement is never evaluated on the same candle as the MSB.
    MSB structure is frozen at sweep detection time.
    Swings used for sweep/structure must be confirmed on an earlier candle.
    """

    def __init__(self, instrument: str, config: StrategyConfig | None = None) -> None:
        self.instrument = instrument
        self.config = config if config is not None else StrategyConfig()
        self._m1: list[Candle] = []
        self._m15: list[Candle] = []
        self._last_m1_end: int | None = None
        self._last_m15_end: int | None = None
        self._confirmed_swing_highs: list[SwingPoint] = []
        self._confirmed_swing_lows: list[SwingPoint] = []
        self._state = StrategyState.IDLE
        self._active_sweep: LiquiditySweep | None = None
        self._active_structure_swing: SwingPoint | None = None
        self._active_msb: MarketStructureBreak | None = None
        self._active_displacement: Displacement | None = None
        self._sweep_m1_index: int | None = None
        self._msb_m1_index: int | None = None
        self._signals: list[StrategySignal] = []

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def signals(self) -> Sequence[StrategySignal]:
        return tuple(self._signals)

    def reset(self) -> None:
        self._m1.clear()
        self._m15.clear()
        self._last_m1_end = None
        self._last_m15_end = None
        self._confirmed_swing_highs.clear()
        self._confirmed_swing_lows.clear()
        self._state = StrategyState.IDLE
        self._active_sweep = None
        self._active_structure_swing = None
        self._active_msb = None
        self._active_displacement = None
        self._sweep_m1_index = None
        self._msb_m1_index = None
        self._signals.clear()

    def on_candle(self, candle: Candle) -> list[StrategySignal]:
        if candle.timeframe == "M1":
            return self.on_m1(candle)
        if candle.timeframe == "M15":
            self.on_m15(candle)
            return []
        return []

    def on_m15(self, candle: Candle) -> None:
        if candle.timeframe != "M15":
            raise ValueError(f"expected M15 candle, got {candle.timeframe}")
        if not candle.finalized:
            raise ValueError("strategy only accepts finalized candles")
        if self._last_m15_end is not None and candle.end_epoch < self._last_m15_end:
            raise OutOfOrderCandleError("M15", self._last_m15_end, candle.end_epoch)
        if self._last_m15_end is not None and candle.end_epoch == self._last_m15_end:
            return
        self._m15.append(candle)
        self._last_m15_end = candle.end_epoch

    def on_m1(self, candle: Candle) -> list[StrategySignal]:
        if candle.timeframe != "M1":
            raise ValueError(f"expected M1 candle, got {candle.timeframe}")
        if not candle.finalized:
            raise ValueError("strategy only accepts finalized candles")
        if self._last_m1_end is not None and candle.end_epoch < self._last_m1_end:
            raise OutOfOrderCandleError("M1", self._last_m1_end, candle.end_epoch)
        if self._last_m1_end is not None and candle.end_epoch == self._last_m1_end:
            return []

        prev_len = len(self._m1)
        self._m1.append(candle)
        self._last_m1_end = candle.end_epoch
        idx = len(self._m1) - 1

        new_swings = newly_confirmed_swings(self._m1, self.config.swing_x, prev_len=prev_len)
        for sw in new_swings:
            if sw.kind == "high":
                self._confirmed_swing_highs.append(sw)
            else:
                self._confirmed_swing_lows.append(sw)

        emitted: list[StrategySignal] = []

        if self._state == StrategyState.IDLE:
            result = self._detect_sweep(candle, idx)
            if result is not None:
                sweep, structure = result
                self._active_sweep = sweep
                self._active_structure_swing = structure
                self._sweep_m1_index = idx
                self._state = StrategyState.SWEEP_DETECTED

        elif self._state == StrategyState.SWEEP_DETECTED:
            assert self._active_sweep is not None and self._sweep_m1_index is not None
            bars_after = idx - self._sweep_m1_index
            if bars_after < 1:
                pass
            elif bars_after > self.config.msb_window_bars:
                self._expire_setup()
            else:
                msb = self._detect_msb(candle, idx, bars_after)
                if msb is not None:
                    self._active_msb = msb
                    self._msb_m1_index = idx
                    self._state = StrategyState.MSB_DETECTED

        elif self._state == StrategyState.MSB_DETECTED:
            assert self._msb_m1_index is not None
            if idx <= self._msb_m1_index:
                return emitted
            disp = self._detect_displacement(candle, idx)
            if disp is not None:
                self._active_displacement = disp
                self._state = StrategyState.DISPLACEMENT_DETECTED
                fvg = self._detect_fvg(idx)
                if fvg is not None:
                    signal = self._build_signal(fvg, candle)
                    self._signals.append(signal)
                    emitted.append(signal)
                    self._state = StrategyState.SIGNAL
                    self._expire_setup()
            else:
                if idx - self._msb_m1_index > 1:
                    self._expire_setup()

        elif self._state == StrategyState.DISPLACEMENT_DETECTED:
            fvg = self._detect_fvg(idx)
            if fvg is not None:
                assert self._active_displacement is not None
                signal = self._build_signal(fvg, candle)
                self._signals.append(signal)
                emitted.append(signal)
                self._state = StrategyState.SIGNAL
                self._expire_setup()
            else:
                assert self._msb_m1_index is not None
                if idx - self._msb_m1_index > 3:
                    self._expire_setup()

        return emitted

    def process(self, candles: Iterable[Candle]) -> list[StrategySignal]:
        all_signals: list[StrategySignal] = []
        for c in candles:
            all_signals.extend(self.on_candle(c))
        return all_signals

    def _expire_setup(self) -> None:
        self._state = StrategyState.IDLE
        self._active_sweep = None
        self._active_structure_swing = None
        self._active_msb = None
        self._active_displacement = None
        self._sweep_m1_index = None
        self._msb_m1_index = None

    def _available_m15_at(self, decision_epoch: int) -> list[Candle]:
        return [c for c in self._m15 if c.end_epoch <= decision_epoch]

    def _m15_context_at(self, decision_epoch: int) -> M15Context:
        available = self._available_m15_at(decision_epoch)
        if not available:
            return M15Context(None, None, None, None, None, None)
        last = available[-1]
        window = available[-5:] if len(available) >= 5 else available
        recent_high = max(c.high for c in window)
        recent_low = min(c.low for c in window)
        if last.close > last.open:
            bias = "bullish"
        elif last.close < last.open:
            bias = "bearish"
        else:
            bias = "neutral"
        return M15Context(
            last.start_epoch, last.end_epoch, last.close,
            recent_high, recent_low, bias,  # type: ignore[arg-type]
        )

    def _prior_confirmed_swing_low(self, before_epoch: int) -> SwingPoint | None:
        """Most recent swing low confirmed *strictly before* ``before_epoch``.

        A swing whose right-side confirmation completes on the current candle
        (``confirmed_at_epoch == before_epoch``) is not available for sweep or
        structure selection on that same candle.
        """
        candidates = [
            s for s in self._confirmed_swing_lows
            if s.confirmed_at_epoch < before_epoch
            and s.candle_end_epoch < before_epoch
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.candle_start_epoch)

    def _prior_confirmed_swing_high(self, before_epoch: int) -> SwingPoint | None:
        """Most recent swing high confirmed *strictly before* ``before_epoch``."""
        candidates = [
            s for s in self._confirmed_swing_highs
            if s.confirmed_at_epoch < before_epoch
            and s.candle_end_epoch < before_epoch
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.candle_start_epoch)

    def _detect_sweep(
        self, candle: Candle, idx: int
    ) -> tuple[LiquiditySweep, SwingPoint] | None:
        """Detect sweep and freeze MSB structure at this decision time."""
        decision = candle.end_epoch
        swing_low = self._prior_confirmed_swing_low(decision)
        if swing_low is not None:
            if candle.low < swing_low.price and candle.close > swing_low.price:
                structure = self._prior_confirmed_swing_high(decision)
                if structure is None:
                    return None
                sweep = LiquiditySweep(
                    Direction.LONG, swing_low.price,
                    candle.start_epoch, candle.end_epoch,
                    candle.low, candle.high, candle.close, swing_low,
                )
                return sweep, structure
        swing_high = self._prior_confirmed_swing_high(decision)
        if swing_high is not None:
            if candle.high > swing_high.price and candle.close < swing_high.price:
                structure = self._prior_confirmed_swing_low(decision)
                if structure is None:
                    return None
                sweep = LiquiditySweep(
                    Direction.SHORT, swing_high.price,
                    candle.start_epoch, candle.end_epoch,
                    candle.low, candle.high, candle.close, swing_high,
                )
                return sweep, structure
        return None

    def _detect_msb(
        self, candle: Candle, idx: int, bars_after: int
    ) -> MarketStructureBreak | None:
        sweep = self._active_sweep
        structure = self._active_structure_swing
        assert sweep is not None
        if structure is None:
            return None
        if structure.confirmed_at_epoch >= sweep.sweep_candle_end_epoch:
            return None
        if sweep.direction == Direction.LONG:
            if candle.close > structure.price:
                return MarketStructureBreak(
                    Direction.LONG, structure.price,
                    candle.start_epoch, candle.end_epoch, candle.close,
                    bars_after, structure,
                )
        else:
            if candle.close < structure.price:
                return MarketStructureBreak(
                    Direction.SHORT, structure.price,
                    candle.start_epoch, candle.end_epoch, candle.close,
                    bars_after, structure,
                )
        return None

    def _detect_displacement(self, candle: Candle, idx: int) -> Displacement | None:
        msb = self._active_msb
        assert msb is not None
        direction = msb.direction
        body = abs(candle.close - candle.open)
        range_ = candle.high - candle.low
        if range_ <= 0.0:
            return None
        body_range_ratio = body / range_
        if body_range_ratio < self.config.displacement_body_range_ratio:
            return None
        atr_val = compute_atr(self._m1, self.config.atr_period, end_index=idx)
        if atr_val is None or atr_val <= 0.0:
            return None
        body_atr_ratio = body / atr_val
        if body_atr_ratio < self.config.displacement_body_atr_ratio:
            return None
        if direction == Direction.LONG and candle.close <= candle.open:
            return None
        if direction == Direction.SHORT and candle.close >= candle.open:
            return None
        return Displacement(
            direction, candle.start_epoch, candle.end_epoch,
            candle.open, candle.high, candle.low, candle.close,
            body, range_, body_range_ratio, body_atr_ratio, atr_val,
        )

    def _detect_fvg(self, idx: int) -> FairValueGap | None:
        if idx < 2:
            return None
        c1, c2, c3 = self._m1[idx - 2], self._m1[idx - 1], self._m1[idx]
        direction = self._active_msb.direction if self._active_msb is not None else None
        if direction is None:
            return None
        atr_val = compute_atr(self._m1, self.config.atr_period, end_index=idx)
        if direction == Direction.LONG:
            if c3.low > c1.high:
                gap_low, gap_high = c1.high, c3.low
                size = gap_high - gap_low
                size_atr = (size / atr_val) if atr_val and atr_val > 0 else None
                return FairValueGap(
                    Direction.LONG, gap_low, gap_high, size, size_atr,
                    c1.start_epoch, c2.start_epoch, c3.start_epoch, c3.end_epoch,
                )
        else:
            if c3.high < c1.low:
                gap_low, gap_high = c3.high, c1.low
                size = gap_high - gap_low
                size_atr = (size / atr_val) if atr_val and atr_val > 0 else None
                return FairValueGap(
                    Direction.SHORT, gap_low, gap_high, size, size_atr,
                    c1.start_epoch, c2.start_epoch, c3.start_epoch, c3.end_epoch,
                )
        return None

    def _build_signal(self, fvg: FairValueGap, candle: Candle) -> StrategySignal:
        assert self._active_sweep is not None
        assert self._active_msb is not None
        assert self._active_displacement is not None
        m15_ctx = self._m15_context_at(candle.end_epoch)
        ref = {
            "swept_level": self._active_sweep.swept_level,
            "msb_level": self._active_msb.broken_level,
            "fvg_low": fvg.gap_low,
            "fvg_high": fvg.gap_high,
            "displacement_close": self._active_displacement.close,
        }
        return StrategySignal(
            instrument=self.instrument,
            direction=self._active_sweep.direction,
            signal_epoch=candle.end_epoch,
            timeframe_context="M15+M1",
            sweep=self._active_sweep,
            msb=self._active_msb,
            displacement=self._active_displacement,
            fvg=fvg,
            m15_context=m15_ctx,
            reference_levels=ref,
            metadata={
                "state_at_signal": StrategyState.SIGNAL.value,
                "m1_index": len(self._m1) - 1,
            },
        )
