"""Common strategy interface for the 6E tournament."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.strategy.models import StrategyConfig


@runtime_checkable
class TournamentStrategy(Protocol):
    """Protocol every tournament strategy must satisfy.

    Signal generation must use only information available at or before the
    decision ``signal_epoch`` (no future candle OHLC, no future volatility).
    """

    @property
    def strategy_id(self) -> str:
        """Stable identifier used in reports and JSON."""
        ...

    @property
    def definition(self) -> dict:
        """Frozen hypothesis description and parameters for the report."""
        ...

    def generate_signals(
        self,
        instrument: str,
        m1_candles: Sequence[Candle],
        *,
        strategy_config: StrategyConfig | None = None,
    ) -> list[TournamentSignal]:
        """Emit signals in chronological order from completed M1 candles.

        Implementations must not look ahead past the signal candle's end_epoch.
        """
        ...
