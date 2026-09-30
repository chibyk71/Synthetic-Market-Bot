"""Registered tournament strategies (A–F)."""

from __future__ import annotations

from smb.research.tournament.interface import TournamentStrategy
from smb.research.tournament.strategies.ict_adapter import ICTControlStrategy
from smb.research.tournament.strategies.impulse_pullback import ImpulsePullbackStrategy
from smb.research.tournament.strategies.mean_reversion import MeanReversionStrategy
from smb.research.tournament.strategies.momentum import MomentumContinuationStrategy
from smb.research.tournament.strategies.range_breakout import RangeBreakoutStrategy
from smb.research.tournament.strategies.volatility_regime import VolatilityRegimeStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "ict_control": ICTControlStrategy,
    "momentum_continuation": MomentumContinuationStrategy,
    "mean_reversion": MeanReversionStrategy,
    "range_breakout": RangeBreakoutStrategy,
    "impulse_pullback": ImpulsePullbackStrategy,
    "volatility_regime": VolatilityRegimeStrategy,
}


def build_strategies(
    strategy_ids: tuple[str, ...] | None = None,
) -> list[TournamentStrategy]:
    """Instantiate registered strategies (deterministic order)."""
    ids = strategy_ids if strategy_ids is not None else tuple(STRATEGY_REGISTRY.keys())
    out: list[TournamentStrategy] = []
    for sid in ids:
        cls = STRATEGY_REGISTRY.get(sid)
        if cls is None:
            raise ValueError(f"Unknown strategy_id: {sid}")
        out.append(cls())
    return out


__all__ = [
    "STRATEGY_REGISTRY",
    "ICTControlStrategy",
    "ImpulsePullbackStrategy",
    "MeanReversionStrategy",
    "MomentumContinuationStrategy",
    "RangeBreakoutStrategy",
    "VolatilityRegimeStrategy",
    "build_strategies",
]
