"""Strategy D — Short-Term Range Breakout (frozen)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.research.tournament.strategies._common import completed_atr, range_high_low
from smb.strategy.models import Direction, StrategyConfig

RANGE_LOOKBACK = 10
ATR_PERIOD = 14
BREAK_ATR_BUFFER = 0.25
STOP_BUFFER_ATR = 0.10


class RangeBreakoutStrategy:
    """Break of a recent short-horizon range with confirmation may continue."""

    @property
    def strategy_id(self) -> str:
        return "range_breakout"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "A break of a recent short-horizon range accompanied by "
                "sufficient movement may continue."
            ),
            "frozen_params": {
                "range_lookback": RANGE_LOOKBACK,
                "atr_period": ATR_PERIOD,
                "break_atr_buffer": BREAK_ATR_BUFFER,
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
        min_i = max(RANGE_LOOKBACK, ATR_PERIOD)
        for i in range(min_i, n):
            atr = completed_atr(m1_candles, i, ATR_PERIOD)
            prior = range_high_low(m1_candles, i - 1, RANGE_LOOKBACK)
            if atr is None or atr <= 0.0 or prior is None:
                continue
            range_hi, range_lo = prior
            c = m1_candles[i]
            buffer = BREAK_ATR_BUFFER * atr
            direction: Direction | None = None
            if c.close > range_hi + buffer:
                direction = Direction.LONG
            elif c.close < range_lo - buffer:
                direction = Direction.SHORT
            if direction is None:
                continue
            entry = c.close
            stop_buf = STOP_BUFFER_ATR * atr
            if direction == Direction.LONG:
                stop = range_lo - stop_buf
            else:
                stop = range_hi + stop_buf
            risk = abs(entry - stop)
            if risk <= 0.0:
                continue
            if direction == Direction.LONG and not (stop < entry):
                continue
            if direction == Direction.SHORT and not (stop > entry):
                continue
            signals.append(
                TournamentSignal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    signal_epoch=c.end_epoch,
                    direction=direction,
                    entry_price=entry,
                    stop_loss=stop,
                    risk_distance=risk,
                    metadata={
                        "reason": "range_breakout",
                        "range_high": range_hi,
                        "range_low": range_lo,
                        "atr": atr,
                        "lookback": RANGE_LOOKBACK,
                        "candle_index": i,
                    },
                )
            )
        return signals
