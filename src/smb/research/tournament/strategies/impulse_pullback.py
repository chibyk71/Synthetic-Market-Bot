"""Strategy E — Impulse → Pullback Continuation (frozen, non-ICT)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.research.tournament.strategies._common import completed_atr
from smb.strategy.models import Direction, StrategyConfig

IMPULSE_BARS = 3
MIN_IMPULSE_ATR = 1.5
PULLBACK_MIN_FRAC = 0.30
PULLBACK_MAX_FRAC = 0.60
PULLBACK_MAX_BARS = 5
ATR_PERIOD = 14
STOP_BUFFER_ATR = 0.15


class ImpulsePullbackStrategy:
    """Strong impulse, controlled retracement, then renewed movement in impulse direction."""

    @property
    def strategy_id(self) -> str:
        return "impulse_pullback"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "After a strong short-term directional impulse, a controlled "
                "retracement followed by renewed movement may continue in the "
                "impulse direction. Does not use ICT sweep/MSB/FVG logic."
            ),
            "frozen_params": {
                "impulse_bars": IMPULSE_BARS,
                "min_impulse_atr": MIN_IMPULSE_ATR,
                "pullback_min_frac": PULLBACK_MIN_FRAC,
                "pullback_max_frac": PULLBACK_MAX_FRAC,
                "pullback_max_bars": PULLBACK_MAX_BARS,
                "atr_period": ATR_PERIOD,
                "stop_buffer_atr": STOP_BUFFER_ATR,
            },
        }

    def generate_signals(
        self,
        instrument: str,
        m1_candles: Sequence[Candle],
        *,
        strategy_config: StrategyConfig | None = None,
    ) -> list[TournamentSignal]:
        del strategy_config
        signals: list[TournamentSignal] = []
        n = len(m1_candles)
        min_i = ATR_PERIOD + IMPULSE_BARS + PULLBACK_MAX_BARS
        for i in range(min_i, n):
            atr = completed_atr(m1_candles, i, ATR_PERIOD)
            if atr is None or atr <= 0.0:
                continue
            for pb_len in range(1, PULLBACK_MAX_BARS + 1):
                impulse_end = i - pb_len
                impulse_start = impulse_end - IMPULSE_BARS
                if impulse_start < 0:
                    continue
                imp_slice = m1_candles[impulse_start + 1 : impulse_end + 1]
                if len(imp_slice) < IMPULSE_BARS:
                    continue
                imp_hi = max(c.high for c in imp_slice)
                imp_lo = min(c.low for c in imp_slice)
                imp_open = m1_candles[impulse_start + 1].open
                imp_close = m1_candles[impulse_end].close
                imp_range = imp_hi - imp_lo
                if imp_range < MIN_IMPULSE_ATR * atr:
                    continue
                if imp_close > imp_open:
                    impulse_dir = Direction.LONG
                    impulse_extreme = imp_hi
                elif imp_close < imp_open:
                    impulse_dir = Direction.SHORT
                    impulse_extreme = imp_lo
                else:
                    continue

                pb_slice = m1_candles[impulse_end + 1 : i]
                if not pb_slice:
                    continue
                if impulse_dir == Direction.LONG:
                    pb_extreme = min(c.low for c in pb_slice)
                    retrace = (impulse_extreme - pb_extreme) / imp_range
                else:
                    pb_extreme = max(c.high for c in pb_slice)
                    retrace = (pb_extreme - impulse_extreme) / imp_range
                if not (PULLBACK_MIN_FRAC <= retrace <= PULLBACK_MAX_FRAC):
                    continue

                c = m1_candles[i]
                if impulse_dir == Direction.LONG and c.close <= impulse_extreme:
                    continue
                if impulse_dir == Direction.SHORT and c.close >= impulse_extreme:
                    continue

                entry = c.close
                buf = STOP_BUFFER_ATR * atr
                if impulse_dir == Direction.LONG:
                    stop = pb_extreme - buf
                else:
                    stop = pb_extreme + buf
                risk = abs(entry - stop)
                if risk <= 0.0:
                    continue
                if impulse_dir == Direction.LONG and not (stop < entry):
                    continue
                if impulse_dir == Direction.SHORT and not (stop > entry):
                    continue

                signals.append(
                    TournamentSignal(
                        strategy_id=self.strategy_id,
                        instrument=instrument,
                        signal_epoch=c.end_epoch,
                        direction=impulse_dir,
                        entry_price=entry,
                        stop_loss=stop,
                        risk_distance=risk,
                        metadata={
                            "reason": "impulse_pullback",
                            "impulse_range": imp_range,
                            "retrace_frac": retrace,
                            "pullback_bars": pb_len,
                            "atr": atr,
                            "impulse_end_index": impulse_end,
                            "candle_index": i,
                        },
                    )
                )
                break
        return signals
