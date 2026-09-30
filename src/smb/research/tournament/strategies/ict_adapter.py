"""Strategy A — Existing ICT strategy (control, unchanged semantics)."""

from __future__ import annotations

from collections.abc import Sequence

from smb.market.candles import Candle
from smb.research.tournament.models import TournamentSignal
from smb.strategy.engine import StrategyEngine
from smb.strategy.models import StrategyConfig, StrategySignal
from smb.trade.constructor import TradeConstructor
from smb.trade.models import RiskContext, TradeConfig


class ICTControlStrategy:
    """Control: production/frozen ICT sweep→MSB→displacement→FVG path.

    Semantics are not rewritten. Signals are produced by StrategyEngine;
    entry/stop come from TradeConstructor with a neutral target_rr so the
    structural stop is preserved. Tournament targets are applied later.
    """

    @property
    def strategy_id(self) -> str:
        return "ict_control"

    @property
    def definition(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "hypothesis": (
                "Existing ICT-style sequence: M15 context → M1 sweep → MSB → "
                "displacement → FVG. Control strategy; semantics unchanged."
            ),
            "frozen_params": {
                "source": "smb.strategy.engine.StrategyEngine",
                "trade_construction": "smb.trade.constructor.TradeConstructor",
                "note": "target_rr overridden per tournament cell; structural stop retained",
            },
        }

    def generate_signals(
        self,
        instrument: str,
        m1_candles: Sequence[Candle],
        *,
        strategy_config: StrategyConfig | None = None,
        m15_candles: Sequence[Candle] | None = None,
    ) -> list[TournamentSignal]:
        cfg = strategy_config if strategy_config is not None else StrategyConfig()
        engine = StrategyEngine(instrument, cfg)
        if m15_candles:
            for c in m15_candles:
                engine.on_candle(c)
        raw: list[StrategySignal] = []
        for c in m1_candles:
            emitted = engine.on_candle(c)
            raw.extend(emitted)

        constructor = TradeConstructor(
            TradeConfig(
                risk_per_trade=0.01,
                target_rr=1.0,
                minimum_rr=0.5,
                sl_atr_buffer=0.10,
            )
        )
        risk = RiskContext(equity=10_000.0)
        out: list[TournamentSignal] = []
        for sig in raw:
            result = constructor.construct(sig, risk)
            if not result.accepted or result.trade is None:
                continue
            t = result.trade
            out.append(
                TournamentSignal(
                    strategy_id=self.strategy_id,
                    instrument=instrument,
                    signal_epoch=sig.signal_epoch,
                    direction=sig.direction,
                    entry_price=t.entry_price,
                    stop_loss=t.stop_loss,
                    risk_distance=t.risk_distance,
                    metadata={
                        "reason": "ict_control",
                        "timeframe_context": sig.timeframe_context,
                        "fvg_low": sig.fvg.gap_low,
                        "fvg_high": sig.fvg.gap_high,
                        "sweep_level": sig.sweep.swept_level,
                        "msb_level": sig.msb.broken_level,
                        "source_signal_present": True,
                    },
                )
            )
        return out
