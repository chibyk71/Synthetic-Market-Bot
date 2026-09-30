"""Strategy C — Short-Term Mean Reversion (frozen)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.research.tournament.strategies._common import completed_atr, rolling_mean_close
from smb.strategy.models import Direction, StrategyConfig

LOOKBACK_BARS = 20
ATR_PERIOD = 14
MIN_DEV_ATR = 2.0
STOP_ATR_MULT = 1.0


class MeanReversionStrategy:
    """Large short-horizon deviation from rolling mean may partially revert."""

    @property
    def strategy_id(self) -> str:
        return "mean_reversion"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "An unusually large short-horizon deviation from a rolling "
                "reference may partially revert."
            ),
            "frozen_params": {
                "lookback_bars": LOOKBACK_BARS,
                "atr_period": ATR_PERIOD,
                "min_dev_atr": MIN_DEV_ATR,
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
        min_i = max(LOOKBACK_BARS - 1, ATR_PERIOD)
        for i in range(min_i, n):
            atr = completed_atr(m1_candles, i, ATR_PERIOD)
            mean = rolling_mean_close(m1_candles, i, LOOKBACK_BARS)
            if atr is None or atr <= 0.0 or mean is None:
                continue
            c = m1_candles[i]
            dev = c.close - mean
            dev_atr = abs(dev) / atr
            if dev_atr < MIN_DEV_ATR:
                continue
            direction = Direction.SHORT if dev > 0 else Direction.LONG
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
                        "reason": "mean_reversion",
                        "deviation": dev,
                        "dev_atr": dev_atr,
                        "mean": mean,
                        "atr": atr,
                        "lookback": LOOKBACK_BARS,
                        "candle_index": i,
                    },
                )
            )
        return signals
