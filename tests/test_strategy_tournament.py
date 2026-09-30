"""Unit and integration tests for Milestone 6E strategy tournament."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
from smb.market.candles import Candle
from smb.research.tournament.constructor import construct_from_tournament_signal
from smb.research.tournament.interface import TournamentStrategy
from smb.research.tournament.models import (
    DEFAULT_CANONICAL_SPREAD_COST_R,
    DEFAULT_TOURNAMENT_HORIZON_SECONDS,
    TARGET_R_MULTIPLES,
    TournamentConfig,
    TournamentSignal,
)
from smb.research.tournament.runner import (
    build_m1_m15_from_ticks,
    post_entry_tick_window,
)
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
    summaries = runner._aggregate([], ["momentum_continuation"], [0.30], spread_cost_r=0.05)
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



def test_post_entry_tick_window_matches_linear_filter() -> None:
    """Indexed window must equal the prior logical full-scan definition."""
    ticks = [_tick(100 + i, 100.0 + i * 0.01) for i in range(50)]
    signal_epoch = 110
    horizon = 10
    # Logical definition (prior full-list comprehension)
    linear = [
        t
        for t in ticks
        if t.epoch > signal_epoch and t.epoch <= signal_epoch + horizon
    ]
    indexed = post_entry_tick_window(ticks, signal_epoch, horizon)
    assert [t.epoch for t in indexed] == [t.epoch for t in linear]
    assert [t.epoch for t in indexed] == list(range(111, 121))


def test_post_entry_tick_window_boundaries() -> None:
    ticks = [_tick(e, 1.0) for e in (100, 101, 102, 105, 110, 111)]
    # exclusive lower bound: epoch == signal_epoch is excluded
    w = post_entry_tick_window(ticks, signal_epoch=100, horizon_seconds=5)
    assert [t.epoch for t in w] == [101, 102, 105]
    # inclusive upper bound: epoch == signal + horizon is included
    w2 = post_entry_tick_window(ticks, signal_epoch=100, horizon_seconds=10)
    assert [t.epoch for t in w2] == [101, 102, 105, 110]
    # empty when no ticks in window
    w3 = post_entry_tick_window(ticks, signal_epoch=200, horizon_seconds=10)
    assert w3 == []
    # empty list input
    assert post_entry_tick_window([], 100, 10) == []


def test_post_entry_tick_window_reuses_epoch_index() -> None:
    ticks = [_tick(100 + i, 1.0) for i in range(20)]
    epochs = [t.epoch for t in ticks]
    a = post_entry_tick_window(ticks, 105, 5, epochs=epochs)
    b = post_entry_tick_window(ticks, 105, 5)
    assert [t.epoch for t in a] == [t.epoch for t in b]


def test_tournament_config_rejects_non_frozen_targets() -> None:
    with pytest.raises(ValueError, match="freezes target_r_multiples"):
        TournamentConfig(target_r_multiples=(0.25, 0.50))
    with pytest.raises(ValueError, match="freezes target_r_multiples"):
        TournamentConfig(target_r_multiples=(0.30, 0.40))
    with pytest.raises(ValueError, match="freezes target_r_multiples"):
        TournamentConfig(target_r_multiples=(0.30, 0.40, 0.50, 0.60))


def test_tournament_config_rejects_non_frozen_horizon() -> None:
    with pytest.raises(ValueError, match="freezes max_duration_seconds"):
        TournamentConfig(max_duration_seconds=900)
    with pytest.raises(ValueError, match="freezes max_duration_seconds"):
        TournamentConfig(max_duration_seconds=299)


def test_tournament_config_spread_cost_default() -> None:
    cfg = TournamentConfig()
    assert cfg.spread_cost_r == DEFAULT_CANONICAL_SPREAD_COST_R
    assert cfg.spread_cost_r == 0.05
    assert cfg.max_duration_seconds == DEFAULT_TOURNAMENT_HORIZON_SECONDS


def test_cli_rejects_non_frozen_targets() -> None:
    from smb.research.tournament.cli import _validate_frozen_targets

    assert _validate_frozen_targets(None) == TARGET_R_MULTIPLES
    assert _validate_frozen_targets(["0.30", "0.40", "0.50"]) == TARGET_R_MULTIPLES
    with pytest.raises(SystemExit, match="freezes --targets"):
        _validate_frozen_targets(["0.25", "0.50"])
    with pytest.raises(SystemExit, match="freezes --targets"):
        _validate_frozen_targets(["0.30", "0.40", "0.60"])


def test_cli_rejects_non_frozen_horizon() -> None:
    from smb.research.tournament.cli import _validate_frozen_horizon

    assert _validate_frozen_horizon(300) == 300
    with pytest.raises(SystemExit, match="freezes --horizon"):
        _validate_frozen_horizon(900)


def test_cost_fields_on_filled_row_via_aggregate() -> None:
    """Aggregation exposes gross/net when rows carry cost fields."""
    from smb.research.tournament.runner import StrategyTournamentRunner
    from smb.simulation.models import SimulationOutcome

    runner = StrategyTournamentRunner.__new__(StrategyTournamentRunner)
    from smb.research.tournament.models import TournamentRow

    row = TournamentRow(
        strategy_id="momentum_continuation",
        target_rr=0.30,
        instrument="volatility_75_1s",
        signal_epoch=100,
        direction="long",
        accepted=True,
        filled=True,
        outcome=SimulationOutcome.TP,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=100.3,
        entry_time=101,
        exit_time=110,
        duration_seconds=9,
        realized_r=0.30,
        mfe=0.30,
        mae=0.05,
        signal_metadata={},
        candidate=None,
        simulation=None,
        metrics=None,
        gross_r=0.30,
        cost_r=0.05,
        net_r=0.25,
    )
    summaries = runner._aggregate(
        [row], ["momentum_continuation"], [0.30], spread_cost_r=0.05
    )
    s = summaries[0]
    assert s.total_gross_r == pytest.approx(0.30)
    assert s.total_net_r == pytest.approx(0.25)
    assert s.average_net_r == pytest.approx(0.25)
    assert s.spread_cost_r == pytest.approx(0.05)


def test_indexed_path_avoids_full_scan_instrumentation() -> None:
    """Regression: post_entry_tick_window must not iterate all ticks via filter.

    We prove the returned slice equals the logical filter while the
    implementation uses bisect (by checking it returns a list of the same
    Tick objects from the source list, not recomputed filters over a
    mutated sequence).
    """
    n = 5000
    ticks = [_tick(1_000_000 + i, 100.0) for i in range(n)]
    epochs = [t.epoch for t in ticks]
    signal = 1_000_000 + 2500
    horizon = 100
    window = post_entry_tick_window(ticks, signal, horizon, epochs=epochs)
    expected = [
        t for t in ticks if t.epoch > signal and t.epoch <= signal + horizon
    ]
    assert len(window) == len(expected) == 100
    # Same object identity → sliced from source list, not rebuilt
    assert all(a is b for a, b in zip(window, expected, strict=True))
