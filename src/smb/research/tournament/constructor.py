"""Build TradeCandidate from TournamentSignal (common construction path)."""

from __future__ import annotations

import math

from smb.research.tournament.models import TournamentSignal
from smb.strategy.models import Direction
from smb.trade.models import (
    RejectionReason,
    RiskContext,
    TradeCandidate,
    TradeConstructionResult,
)


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(
        value
    )


def construct_from_tournament_signal(
    signal: TournamentSignal,
    *,
    target_rr: float,
    risk_context: RiskContext,
    risk_per_trade: float,
    minimum_rr: float = 0.01,
) -> TradeConstructionResult:
    """Apples-to-apples trade construction for any tournament strategy.

    Uses the signal's entry and stop; places take-profit at ``target_rr`` × risk.
    Does not require an ICT StrategySignal or FVG.
    """
    if not _is_finite(risk_context.equity) or risk_context.equity <= 0.0:
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_EQUITY
        )

    entry = signal.entry_price
    stop = signal.stop_loss
    if not (_is_finite(entry) and _is_finite(stop)):
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_ENTRY
        )

    risk_distance = abs(entry - stop)
    if not _is_finite(risk_distance) or risk_distance <= 0.0:
        return TradeConstructionResult(
            accepted=False,
            trade=None,
            rejection_reason=RejectionReason.INVALID_RISK_DISTANCE,
        )

    if signal.direction == Direction.LONG and not (stop < entry):
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_STOP
        )
    if signal.direction == Direction.SHORT and not (stop > entry):
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_STOP
        )

    reward_distance = risk_distance * target_rr
    if not _is_finite(reward_distance) or reward_distance <= 0.0:
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_TARGET
        )

    if signal.direction == Direction.LONG:
        take_profit = entry + reward_distance
    else:
        take_profit = entry - reward_distance

    if not _is_finite(take_profit):
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_TARGET
        )

    risk_reward = reward_distance / risk_distance
    if risk_reward < minimum_rr:
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INSUFFICIENT_RR
        )

    risk_amount = risk_context.equity * risk_per_trade
    if not _is_finite(risk_amount) or risk_amount <= 0.0:
        return TradeConstructionResult(
            accepted=False, trade=None, rejection_reason=RejectionReason.INVALID_EQUITY
        )

    position_size = risk_amount / risk_distance
    if not _is_finite(position_size) or position_size <= 0.0:
        return TradeConstructionResult(
            accepted=False,
            trade=None,
            rejection_reason=RejectionReason.INVALID_POSITION_SIZE,
        )

    candidate = TradeCandidate(
        instrument=signal.instrument,
        direction=signal.direction,
        signal_epoch=signal.signal_epoch,
        entry_price=entry,
        entry_zone_low=entry,
        entry_zone_high=entry,
        stop_loss=stop,
        take_profit=take_profit,
        risk_distance=risk_distance,
        reward_distance=reward_distance,
        risk_reward=risk_reward,
        risk_percent=risk_per_trade,
        risk_amount=risk_amount,
        position_size=position_size,
        source_signal=None,
    )
    return TradeConstructionResult(accepted=True, trade=candidate, rejection_reason=None)
