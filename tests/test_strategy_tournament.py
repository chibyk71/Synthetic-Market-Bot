"""Unit and integration tests for Milestone 6E strategy tournament."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
from smb.market.candles import Candle
from smb.research.tournament.constructor import construct_from_tournament_signal
from smb.research.tournament.interface import TournamentStrategy
from smb.research.tournament.models import (
    TARGET_R_MULTIPLES,
    TournamentConfig,
    TournamentSignal,
)
from smb.research.tournament.runner import build_m1_m15_from_ticks
from smb.research.tournament.strategies import STRATEGY_REGISTRY, build_strategies
from smb.research.tournament.strategies.mean_reversion import MeanReversionStrategy
from smb.research.tournament.strategies.momentum import MomentumContinuationStrategy
from smb.research.tournament.strategies.range_breakout import RangeBreakoutStrategy
from smb.strategy.models import Direction
from smb.trade.models import RiskContext


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


def _synthetic_trending_ticks(
    start_epoch: int = 1_700_000_000,
    n_minutes: int = 60,
    start_price: float = 100.0,
    step: float = 0.05,
) -> list[Tick]:
    """1 tick/s trending series for ~n_minutes."""
    ticks: list[Tick] = []
    for s in range(n_minutes * 60):
        price = start_price + step * s
        ticks.append(_tick(start_epoch + s, price))
    return ticks


def _candles_from_ticks(ticks: list[Tick]) -> list[Candle]:
    m1, _ = build_m1_m15_from_ticks(ticks)
    return m1


def test_all_strategies_implement_interface() -> None:
    for strat in build_strategies():
        assert isinstance(strat, TournamentStrategy)
        assert strat.strategy_id
        assert "hypothesis" in strat.definition
        assert "frozen_params" in strat.definition


def test_registry_contains_six_strategies() -> None:
    assert len(STRATEGY_REGISTRY) == 6
    assert "ict_control" in STRATEGY_REGISTRY
    assert "momentum_continuation" in STRATEGY_REGISTRY
    assert "mean_reversion" in STRATEGY_REGISTRY
    assert "range_breakout" in STRATEGY_REGISTRY
    assert "impulse_pullback" in STRATEGY_REGISTRY
    assert "volatility_regime" in STRATEGY_REGISTRY


def test_build_strategies_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown strategy_id"):
        build_strategies(("not_a_strategy",))


def test_construct_long_target() -> None:
    sig = TournamentSignal(
        strategy_id="test",
        instrument="volatility_75_1s",
        signal_epoch=100,
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=99.0,
        risk_distance=1.0,
        metadata={"reason": "unit"},
    )
    result = construct_from_tournament_signal(
        sig,
        target_rr=0.40,
        risk_context=RiskContext(equity=10_000.0),
        risk_per_trade=0.01,
    )
    assert result.accepted
    assert result.trade is not None
    assert result.trade.take_profit == pytest.approx(100.4)
    assert result.trade.risk_reward == pytest.approx(0.40)
    assert result.trade.source_signal is None


def test_construct_short_target() -> None:
    sig = TournamentSignal(
        strategy_id="test",
        instrument="volatility_75_1s",
        signal_epoch=100,
        direction=Direction.SHORT,
        entry_price=100.0,
        stop_loss=101.0,
        risk_distance=1.0,
    )
    result = construct_from_tournament_signal(
        sig,
        target_rr=0.30,
        risk_context=RiskContext(equity=10_000.0),
        risk_per_trade=0.01,
    )
    assert result.accepted
    assert result.trade is not None
    assert result.trade.take_profit == pytest.approx(99.7)


def test_tournament_signal_rejects_bad_stop() -> None:
    with pytest.raises(ValueError):
        TournamentSignal(
            strategy_id="x",
            instrument="v",
            signal_epoch=1,
            direction=Direction.LONG,
            entry_price=100.0,
            stop_loss=101.0,
            risk_distance=1.0,
        )


def test_momentum_direction_on_uptrend() -> None:
    ticks = _synthetic_trending_ticks(step=0.1)
    candles = _candles_from_ticks(ticks)
    strat = MomentumContinuationStrategy()
    signals = strat.generate_signals("volatility_75_1s", candles)
    for s in signals:
        assert s.direction == Direction.LONG
        idx = s.metadata["candle_index"]
        assert 0 <= idx < len(candles)
        assert candles[idx].end_epoch == s.signal_epoch


def test_momentum_deterministic() -> None:
    ticks = _synthetic_trending_ticks()
    candles = _candles_from_ticks(ticks)
    strat = MomentumContinuationStrategy()
    a = strat.generate_signals("volatility_75_1s", candles)
    b = strat.generate_signals("volatility_75_1s", candles)
    assert len(a) == len(b)
    assert [s.signal_epoch for s in a] == [s.signal_epoch for s in b]
    assert [s.direction for s in a] == [s.direction for s in b]


def test_mean_reversion_deterministic() -> None:
    ticks: list[Tick] = []
    epoch = 1_700_000_000
    for i in range(120 * 60):
        price = 100.0 + 2.0 * ((i // 30) % 2)
        ticks.append(_tick(epoch + i, price))
    candles = _candles_from_ticks(ticks)
    strat = MeanReversionStrategy()
    a = strat.generate_signals("volatility_75_1s", candles)
    b = strat.generate_signals("volatility_75_1s", candles)
    assert [s.signal_epoch for s in a] == [s.signal_epoch for s in b]


def test_range_breakout_no_lookahead_range() -> None:
    ticks = _synthetic_trending_ticks(n_minutes=40, step=0.05)
    candles = _candles_from_ticks(ticks)
    strat = RangeBreakoutStrategy()
    signals = strat.generate_signals("volatility_75_1s", candles)
    for s in signals:
        idx = s.metadata["candle_index"]
        assert s.metadata["range_high"] is not None
        assert idx >= 10
        if s.direction == Direction.LONG:
            assert candles[idx].close > s.metadata["range_high"]
        else:
            assert candles[idx].close < s.metadata["range_low"]


def test_ict_adapter_runs_on_synthetic() -> None:
    from smb.research.tournament.strategies.ict_adapter import ICTControlStrategy

    ticks = _synthetic_trending_ticks(n_minutes=120, step=0.02)
    m1, m15 = build_m1_m15_from_ticks(ticks)
    strat = ICTControlStrategy()
    signals = strat.generate_signals("volatility_75_1s", m1, m15_candles=m15)
    assert isinstance(signals, list)
    for s in signals:
        assert s.strategy_id == "ict_control"
        assert s.risk_distance > 0


def test_build_m1_m15_from_ticks() -> None:
    ticks = _synthetic_trending_ticks(n_minutes=30)
    m1, m15 = build_m1_m15_from_ticks(ticks)
    assert len(m1) >= 29
    assert all(c.timeframe == "M1" for c in m1)
    assert all(c.finalized for c in m1)
    for i in range(1, len(m1)):
        assert m1[i].start_epoch >= m1[i - 1].start_epoch


def test_target_r_multiples_frozen() -> None:
    assert TARGET_R_MULTIPLES == (0.30, 0.40, 0.50)


def test_tournament_config_defaults() -> None:
    cfg = TournamentConfig()
    assert cfg.instrument == "volatility_75_1s"
    assert cfg.max_duration_seconds == 300
    assert cfg.target_r_multiples == (0.30, 0.40, 0.50)


def test_summary_aggregation_empty_rows() -> None:
    from smb.research.tournament.runner import StrategyTournamentRunner

    runner = StrategyTournamentRunner.__new__(StrategyTournamentRunner)
    summaries = runner._aggregate([], ["momentum_continuation"], [0.30])
    assert len(summaries) == 1
    s = summaries[0]
    assert s.signals == 0
    assert s.fills == 0
    assert s.win_rate is None


def test_no_lookahead_prefix_property() -> None:
    """Signals on a prefix must match the same signals from the full series."""
    ticks = _synthetic_trending_ticks(n_minutes=90, step=0.08)
    full = _candles_from_ticks(ticks)
    prefix = full[:40]
    strat = MomentumContinuationStrategy()
    full_sigs = strat.generate_signals("volatility_75_1s", full)
    prefix_sigs = strat.generate_signals("volatility_75_1s", prefix)
    prefix_epochs = {s.signal_epoch for s in prefix_sigs}
    for s in full_sigs:
        if s.signal_epoch in prefix_epochs:
            match = [p for p in prefix_sigs if p.signal_epoch == s.signal_epoch]
            if match:
                assert match[0].direction == s.direction
                assert match[0].entry_price == pytest.approx(s.entry_price)
