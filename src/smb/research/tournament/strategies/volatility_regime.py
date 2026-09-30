"""Strategy F — Volatility-Regime Strategy (frozen)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.research.tournament.strategies._common import (
    bar_return,
    completed_atr,
    rolling_mean_close,
)
from smb.strategy.models import Direction, StrategyConfig

ATR_PERIOD = 14
ATR_RANK_LOOKBACK = 50
LOW_PCT = 0.25
HIGH_PCT = 0.75
MR_LOOKBACK = 15
MR_DEV_ATR = 1.5
MOM_LOOKBACK = 5
MOM_STRENGTH_ATR = 1.2
STOP_ATR_MULT = 1.0


def _atr_percentile_rank(
    candles: Sequence[Candle], end_index: int, period: int, rank_lookback: int
) -> float | None:
    """Percentile rank of current ATR among last ``rank_lookback`` ATR values."""
    if end_index < period + rank_lookback - 1:
        return None
    atrs: list[float] = []
    for j in range(end_index - rank_lookback + 1, end_index + 1):
        a = completed_atr(candles, j, period)
        if a is None or a <= 0.0:
            return None
        atrs.append(a)
    current = atrs[-1]
    below = sum(1 for a in atrs if a < current)
    return below / len(atrs)


class VolatilityRegimeStrategy:
    """Simple fixed vol regimes with regime-appropriate short-horizon entries."""

    @property
    def strategy_id(self) -> str:
        return "volatility_regime"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "Short-term behavior may differ materially between low-, normal-, "
                "and high-volatility regimes."
            ),
            "frozen_params": {
                "atr_period": ATR_PERIOD,
                "atr_rank_lookback": ATR_RANK_LOOKBACK,
                "low_pct": LOW_PCT,
                "high_pct": HIGH_PCT,
                "mr_lookback": MR_LOOKBACK,
                "mr_dev_atr": MR_DEV_ATR,
                "mom_lookback": MOM_LOOKBACK,
                "mom_strength_atr": MOM_STRENGTH_ATR,
                "stop_atr_mult": STOP_ATR_MULT,
                "normal_regime_action": "skip",
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
        min_i = ATR_PERIOD + ATR_RANK_LOOKBACK
        for i in range(min_i, n):
            atr = completed_atr(m1_candles, i, ATR_PERIOD)
            rank = _atr_percentile_rank(m1_candles, i, ATR_PERIOD, ATR_RANK_LOOKBACK)
            if atr is None or atr <= 0.0 or rank is None:
                continue
            c = m1_candles[i]
            if rank < LOW_PCT:
                regime = "low"
                mean = rolling_mean_close(m1_candles, i, MR_LOOKBACK)
                if mean is None:
                    continue
                dev = c.close - mean
                if abs(dev) / atr < MR_DEV_ATR:
                    continue
                direction = Direction.SHORT if dev > 0 else Direction.LONG
                reason = "vol_regime_low_mean_reversion"
            elif rank > HIGH_PCT:
                regime = "high"
                ret = bar_return(m1_candles, i, MOM_LOOKBACK)
                if ret is None:
                    continue
                strength = abs(ret) * c.close / atr
                if strength < MOM_STRENGTH_ATR:
                    continue
                direction = Direction.LONG if ret > 0 else Direction.SHORT
                reason = "vol_regime_high_momentum"
            else:
                continue

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
                        "reason": reason,
                        "regime": regime,
                        "atr_rank": rank,
                        "atr": atr,
                        "candle_index": i,
                    },
                )
            )
        return signals
