"""Strategy B — Short-Term Momentum Continuation (frozen)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.research.tournament.strategies._common import bar_return, completed_atr
from smb.strategy.models import Direction, StrategyConfig

LOOKBACK_BARS = 5
ATR_PERIOD = 14
MIN_RETURN_ATR = 1.5
STOP_ATR_MULT = 1.0


class MomentumContinuationStrategy:
    """Unusually strong short-horizon directional move → short-term continuation."""

    @property
    def strategy_id(self) -> str:
        return "momentum_continuation"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "An unusually strong short-horizon directional move may exhibit "
                "short-term continuation."
            ),
            "frozen_params": {
                "lookback_bars": LOOKBACK_BARS,
                "atr_period": ATR_PERIOD,
                "min_return_atr": MIN_RETURN_ATR,
                "stop_atr_mult": STOP_ATR_MULT,
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
        min_i = max(LOOKBACK_BARS, ATR_PERIOD)
        for i in range(min_i, n):
            atr = completed_atr(m1_candles, i, ATR_PERIOD)
            ret = bar_return(m1_candles, i, LOOKBACK_BARS)
            if atr is None or atr <= 0.0 or ret is None:
                continue
            c = m1_candles[i]
            price_move = abs(ret) * c.close
            strength = price_move / atr
            if strength < MIN_RETURN_ATR:
                continue
            direction = Direction.LONG if ret > 0 else Direction.SHORT
            entry = c.close
            if direction == Direction.LONG:
                stop = entry - STOP_ATR_MULT * atr
            else:
                stop = entry + STOP_ATR_MULT * atr
            risk = abs(entry - stop)
            if risk <= 0.0:
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
                        "reason": "momentum_continuation",
                        "return": ret,
                        "strength_atr": strength,
                        "atr": atr,
                        "lookback": LOOKBACK_BARS,
                        "candle_index": i,
                    },
                )
            )
        return signals
