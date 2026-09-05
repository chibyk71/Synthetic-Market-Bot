"""Trade construction from completed 2A strategy signals (Milestone 2B).

Pure, deterministic transformation:

    StrategySignal + TradeConfig + RiskContext → TradeConstructionResult

No market-data access, no fills, no simulation.
"""

from __future__ import annotations

import math

from smb.strategy.models import Direction, StrategySignal
from smb.trade.models import (
    RejectionReason,
    RiskContext,
    TradeCandidate,
    TradeConfig,
    TradeConstructionResult,
)


def _reject(reason: RejectionReason) -> TradeConstructionResult:
    return TradeConstructionResult(accepted=False, trade=None, rejection_reason=reason)


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(
        value
    )


class TradeConstructor:
    """Constructs an immutable :class:`TradeCandidate` from a 2A signal.

    Uses only information available on the completed signal plus explicit
    config and risk context. ATR for the stop buffer is taken from
    ``signal.displacement.atr`` (already computed at signal time).

    Every numeric field on an accepted candidate is finite and physically
    meaningful (no NaN / ±inf).
    """

    def __init__(self, config: TradeConfig | None = None) -> None:
        self.config = config if config is not None else TradeConfig()

    def construct(
        self,
        signal: StrategySignal,
        risk_context: RiskContext,
    ) -> TradeConstructionResult:
        """Build a trade candidate or return an explicit rejection."""
        cfg = self.config

        # --- equity / risk amount ---
        equity = risk_context.equity
        if not _is_finite(equity) or equity <= 0.0:
            return _reject(RejectionReason.INVALID_EQUITY)

        risk_amount = equity * cfg.risk_per_trade
        if not _is_finite(risk_amount) or risk_amount <= 0.0:
            return _reject(RejectionReason.INVALID_EQUITY)

        # --- FVG entry zone ---
        fvg = signal.fvg
        if not (_is_finite(fvg.gap_low) and _is_finite(fvg.gap_high)):
            return _reject(RejectionReason.INVALID_FVG)
        if not (fvg.gap_low < fvg.gap_high):
            return _reject(RejectionReason.INVALID_FVG)

        entry_zone_low = fvg.gap_low
        entry_zone_high = fvg.gap_high
        entry_price = (entry_zone_low + entry_zone_high) / 2.0
        if not _is_finite(entry_price):
            return _reject(RejectionReason.INVALID_ENTRY)

        # --- ATR for SL buffer (from 2A displacement — no future data) ---
        atr = signal.displacement.atr
        if atr is None or not _is_finite(atr) or atr < 0.0:
            return _reject(RejectionReason.INVALID_ATR)

        swept = signal.sweep.swept_level
        if not _is_finite(swept):
            return _reject(RejectionReason.INVALID_STOP)

        buffer = atr * cfg.sl_atr_buffer
        if not _is_finite(buffer):
            return _reject(RejectionReason.INVALID_ATR)

        # --- stop-loss (structural + ATR buffer) ---
        if signal.direction == Direction.LONG:
            stop_loss = swept - buffer
            if not _is_finite(stop_loss) or not (stop_loss < entry_price):
                return _reject(RejectionReason.INVALID_STOP)
        elif signal.direction == Direction.SHORT:
            stop_loss = swept + buffer
            if not _is_finite(stop_loss) or not (stop_loss > entry_price):
                return _reject(RejectionReason.INVALID_STOP)
        else:
            return _reject(RejectionReason.INVALID_ENTRY)

        # --- risk distance ---
        risk_distance = abs(entry_price - stop_loss)
        if not _is_finite(risk_distance) or risk_distance <= 0.0:
            return _reject(RejectionReason.INVALID_RISK_DISTANCE)

        # --- take-profit (fixed R-multiple) ---
        reward_distance = risk_distance * cfg.target_rr
        if not _is_finite(reward_distance) or reward_distance <= 0.0:
            return _reject(RejectionReason.INVALID_TARGET)

        if signal.direction == Direction.LONG:
            take_profit = entry_price + reward_distance
            if not _is_finite(take_profit) or not (take_profit > entry_price):
                return _reject(RejectionReason.INVALID_TARGET)
        else:
            take_profit = entry_price - reward_distance
            if not _is_finite(take_profit) or not (take_profit < entry_price):
                return _reject(RejectionReason.INVALID_TARGET)

        # --- risk/reward check ---
        risk_reward = reward_distance / risk_distance
        if not _is_finite(risk_reward) or risk_reward < cfg.minimum_rr:
            return _reject(RejectionReason.INSUFFICIENT_RR)

        # --- position size (generic research units: risk_amount / risk_distance) ---
        position_size = risk_amount / risk_distance
        if not _is_finite(position_size) or position_size <= 0.0:
            return _reject(RejectionReason.INVALID_POSITION_SIZE)

        candidate = TradeCandidate(
            instrument=signal.instrument,
            direction=signal.direction,
            signal_epoch=signal.signal_epoch,
            entry_price=entry_price,
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_distance=risk_distance,
            reward_distance=reward_distance,
            risk_reward=risk_reward,
            risk_percent=cfg.risk_per_trade,
            risk_amount=risk_amount,
            position_size=position_size,
            source_signal=signal,
        )
        return TradeConstructionResult(
            accepted=True,
            trade=candidate,
            rejection_reason=None,
        )
