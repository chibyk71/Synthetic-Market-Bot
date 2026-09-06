"""Milestone 3C — expanding-window walk-forward validation tests."""

from __future__ import annotations

import pytest

from smb.ml import (
    WalkForwardConfig,
    build_dataset,
    default_walk_forward_config,
    feature_names,
    generate_folds,
    run_walk_forward_validation,
)
from smb.ml.models import FEATURE_NAMES as MODEL_FN
from smb.simulation.models import ExitReason, SimulationOutcome, TradeSimulationResult
from smb.strategy.models import (
    Direction,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    M15Context,
    MarketStructureBreak,
    StrategySignal,
    SwingPoint,
)
from smb.trade.models import TradeCandidate


def _make_signal(
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "1HZ75V",
    signal_epoch: int = 1_700_000_000,
    atr: float = 1.5,
    body_atr_ratio: float = 1.0,
    fvg_size: float = 0.8,
    bars_after_sweep: int = 2,
) -> StrategySignal:
    swing = SwingPoint(
        kind="low" if direction is Direction.LONG else "high",
        price=100.0,
        candle_start_epoch=signal_epoch - 900,
        candle_end_epoch=signal_epoch - 840,
        index=2,
        confirmed_at_epoch=signal_epoch - 720,
    )
    structure = SwingPoint(
        kind="high" if direction is Direction.LONG else "low",
        price=110.0 if direction is Direction.LONG else 90.0,
        candle_start_epoch=signal_epoch - 1200,
        candle_end_epoch=signal_epoch - 1140,
        index=0,
        confirmed_at_epoch=signal_epoch - 1020,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=100.0,
        sweep_candle_start_epoch=signal_epoch - 600,
        sweep_candle_end_epoch=signal_epoch - 540,
        sweep_candle_low=98.0,
        sweep_candle_high=103.0,
        sweep_candle_close=101.0,
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure.price,
        msb_candle_start_epoch=signal_epoch - 420,
        msb_candle_end_epoch=signal_epoch - 360,
        msb_candle_close=structure.price + 2.0,
        bars_after_sweep=bars_after_sweep,
        structure_swing=structure,
    )
    body = 1.4
    disp = Displacement(
        direction=direction,
        candle_start_epoch=signal_epoch - 300,
        candle_end_epoch=signal_epoch - 240,
        open=110.0,
        high=112.0,
        low=109.0,
        close=111.4,
        body=body,
        range_=2.0,
        body_range_ratio=0.7,
        body_atr_ratio=body_atr_ratio,
        atr=atr,
    )
    fvg = FairValueGap(
        direction=direction,
        gap_low=105.0,
        gap_high=105.0 + fvg_size,
        size=fvg_size,
        size_atr_ratio=fvg_size / atr if atr else None,
        candle1_start_epoch=signal_epoch - 180,
        candle2_start_epoch=signal_epoch - 120,
        candle3_start_epoch=signal_epoch - 60,
        candle3_end_epoch=signal_epoch,
    )
    m15 = M15Context(
        last_m15_start_epoch=signal_epoch - 900,
        last_m15_end_epoch=signal_epoch - 1,
        last_m15_close=108.0,
        recent_high=120.0,
        recent_low=90.0,
        directional_bias="bullish",
    )
    return StrategySignal(
        instrument=instrument,
        direction=direction,
        signal_epoch=signal_epoch,
        timeframe_context="M15+M1",
        sweep=sweep,
        msb=msb,
        displacement=disp,
        fvg=fvg,
        m15_context=m15,
    )


def _make_candidate(signal: StrategySignal) -> TradeCandidate:
    mid = (signal.fvg.gap_low + signal.fvg.gap_high) / 2.0
    if signal.direction is Direction.LONG:
        sl = signal.sweep.swept_level - 1.0
        tp = mid + 2.0 * (mid - sl)
    else:
        sl = signal.sweep.swept_level + 1.0
        tp = mid - 2.0 * (sl - mid)
    risk = abs(mid - sl)
    return TradeCandidate(
        instrument=signal.instrument,
        direction=signal.direction,
        signal_epoch=signal.signal_epoch,
        entry_price=mid,
        entry_zone_low=signal.fvg.gap_low,
        entry_zone_high=signal.fvg.gap_high,
        stop_loss=sl,
        take_profit=tp,
        risk_distance=risk,
        reward_distance=abs(tp - mid),
        risk_reward=abs(tp - mid) / risk if risk else 0.0,
        risk_percent=0.01,
        risk_amount=100.0,
        position_size=100.0 / risk if risk else 0.0,
        source_signal=signal,
    )


def _make_sim(signal: StrategySignal, outcome: SimulationOutcome) -> TradeSimulationResult:
    candidate = _make_candidate(signal)
    if outcome is SimulationOutcome.NO_FILL:
        return TradeSimulationResult(
            instrument=signal.instrument,
            direction=signal.direction,
            signal_epoch=signal.signal_epoch,
            outcome=outcome,
            filled=False,
            entry_time=None,
            entry_price=None,
            exit_time=None,
            exit_price=None,
            exit_reason=ExitReason.NONE,
            duration_seconds=None,
            candidate=candidate,
        )
    et = signal.signal_epoch + 10
    ep = candidate.entry_price
    if outcome is SimulationOutcome.TP:
        xt, xp, reason = et + 100, candidate.take_profit, ExitReason.TP
    elif outcome is SimulationOutcome.SL:
        xt, xp, reason = et + 50, candidate.stop_loss, ExitReason.SL
    else:
        xt, xp, reason = signal.signal_epoch + 900, None, ExitReason.TIMEOUT
    return TradeSimulationResult(
        instrument=signal.instrument,
        direction=signal.direction,
        signal_epoch=signal.signal_epoch,
        outcome=outcome,
        filled=True,
        entry_time=et,
        entry_price=ep,
        exit_time=xt,
        exit_price=xp,
        exit_reason=reason,
        duration_seconds=int(xt - et),
        candidate=candidate,
    )


def _labeled_dataset(n: int = 40, *, include_no_fill: bool = False):
    sims = []
    for i in range(n):
        outcome = SimulationOutcome.TP if i % 2 == 0 else SimulationOutcome.SL
        sig = _make_signal(
            signal_epoch=1_700_000_000 + i * 3600,
            direction=Direction.LONG if i % 3 else Direction.SHORT,
            atr=1.0 + (i % 5) * 0.1,
            body_atr_ratio=0.8 + (i % 4) * 0.05,
            fvg_size=0.5 + (i % 7) * 0.1,
            bars_after_sweep=1 + (i % 3),
            instrument="1HZ75V" if i % 2 == 0 else "stpRNG",
        )
        sims.append(_make_sim(sig, outcome))
    if include_no_fill:
        nf = _make_signal(signal_epoch=1_700_000_000 + n * 3600, instrument="1HZ75V")
        sims.append(_make_sim(nf, SimulationOutcome.NO_FILL))
    return build_dataset(sims)


def test_fold_generation_expanding_and_chronological():
    ds = _labeled_dataset(20)
    config = WalkForwardConfig(initial_train_size=10, test_size=2, step_size=2)
    folds = generate_folds(ds, config)
    assert len(folds) >= 1
    labeled = ds.labeled
    for fold in folds:
        train_epochs = [labeled[i].signal_epoch for i in fold.train_indices]
        test_epochs = [labeled[i].signal_epoch for i in fold.test_indices]
        assert max(train_epochs) < min(test_epochs)
    for a, b in zip(folds, folds[1:], strict=False):
        assert b.train_count >= a.train_count


def test_fold_generation_deterministic():
    ds = _labeled_dataset(30)
    config = WalkForwardConfig(initial_train_size=12, test_size=3, step_size=3)
    f1 = generate_folds(ds, config)
    f2 = generate_folds(ds, config)
    assert len(f1) == len(f2)
    for a, b in zip(f1, f2, strict=True):
        assert a.train_indices == b.train_indices
        assert a.test_indices == b.test_indices


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        WalkForwardConfig(initial_train_size=0, test_size=5)
    with pytest.raises(ValueError):
        WalkForwardConfig(initial_train_size=5, test_size=0)
    ds = _labeled_dataset(10)
    config = WalkForwardConfig(initial_train_size=9, test_size=5)
    with pytest.raises(ValueError, match="insufficient"):
        generate_folds(ds, config)


def test_test_identities_unique_across_folds():
    ds = _labeled_dataset(40)
    config = WalkForwardConfig(initial_train_size=16, test_size=4, step_size=4)
    folds = generate_folds(ds, config)
    labeled = ds.labeled
    seen: set[tuple] = set()
    for fold in folds:
        for i in fold.test_indices:
            key = (labeled[i].instrument, labeled[i].signal_epoch, str(labeled[i].direction))
            assert key not in seen
            seen.add(key)


def test_run_walk_forward_leakage_invariant():
    ds = _labeled_dataset(40)
    config = WalkForwardConfig(initial_train_size=16, test_size=4, step_size=4)
    result = run_walk_forward_validation(ds, config)
    assert result.total_folds >= 1
    labeled = ds.labeled
    for fr in result.fold_results:
        train_ep = [labeled[i].signal_epoch for i in fr.fold.train_indices]
        test_ep = [labeled[i].signal_epoch for i in fr.fold.test_indices]
        assert max(train_ep) < min(test_ep)


def test_no_fill_excluded_from_supervised():
    ds = _labeled_dataset(20, include_no_fill=True)
    assert any(o.target is None for o in ds.observations)
    assert all(o.target is not None for o in ds.labeled)
    config = WalkForwardConfig(initial_train_size=10, test_size=2, step_size=2)
    result = run_walk_forward_validation(ds, config)
    labeled = ds.labeled
    for fr in result.fold_results:
        for i in list(fr.fold.train_indices) + list(fr.fold.test_indices):
            assert labeled[i].target is not None


def test_fresh_model_per_fold_and_predictions():
    ds = _labeled_dataset(30)
    config = WalkForwardConfig(initial_train_size=12, test_size=3, step_size=3)
    result = run_walk_forward_validation(ds, config)
    for fr in result.fold_results:
        if fr.evaluable:
            assert fr.predictions_count == fr.labeled_test_count
            assert len(fr.y_pred) == fr.labeled_test_count
            assert fr.evaluation is not None


def test_aggregate_from_combined_predictions_not_mean():
    ds = _labeled_dataset(40)
    config = WalkForwardConfig(initial_train_size=16, test_size=4, step_size=4)
    result = run_walk_forward_validation(ds, config)
    assert result.aggregate_evaluation is not None
    combined = sum(fr.predictions_count for fr in result.fold_results if fr.evaluable)
    assert result.aggregate_evaluation.n_samples == combined


def test_determinism_full_run():
    ds = _labeled_dataset(36)
    config = WalkForwardConfig(initial_train_size=12, test_size=4, step_size=4)
    r1 = run_walk_forward_validation(ds, config)
    r2 = run_walk_forward_validation(ds, config)
    assert r1.total_folds == r2.total_folds
    for a, b in zip(r1.fold_results, r2.fold_results, strict=True):
        assert a.y_pred == b.y_pred
        assert a.y_true == b.y_true


def test_source_dataset_not_mutated():
    ds = _labeled_dataset(24)
    before = tuple((o.instrument, o.signal_epoch, o.target, o.features) for o in ds.observations)
    config = WalkForwardConfig(initial_train_size=10, test_size=2, step_size=2)
    run_walk_forward_validation(ds, config)
    after = tuple((o.instrument, o.signal_epoch, o.target, o.features) for o in ds.observations)
    assert before == after


def test_feature_schema_unchanged():
    assert feature_names() == MODEL_FN
    assert len(feature_names()) == 18


def test_one_class_training_window_safe():
    sims = []
    for i in range(15):
        outcome = SimulationOutcome.TP if i < 12 else SimulationOutcome.SL
        sig = _make_signal(signal_epoch=1_000_000 + i * 100, instrument="1HZ75V")
        sims.append(_make_sim(sig, outcome))
    ds = build_dataset(sims)
    config = WalkForwardConfig(initial_train_size=8, test_size=2, step_size=2)
    result = run_walk_forward_validation(ds, config)
    assert result.total_folds >= 1
    for fr in result.fold_results:
        assert fr.fold.train_count >= 1


def test_default_config_helper():
    cfg = default_walk_forward_config(100)
    assert cfg.initial_train_size == 60
    assert cfg.test_size == 10
    assert cfg.step_size == 10
    with pytest.raises(ValueError):
        default_walk_forward_config(5)


def test_regression_future_not_in_train():
    ds = _labeled_dataset(25)
    config = WalkForwardConfig(initial_train_size=10, test_size=3, step_size=3)
    folds = generate_folds(ds, config)
    labeled = ds.labeled
    for fold in folds:
        max_train = max(labeled[i].signal_epoch for i in fold.train_indices)
        min_test = min(labeled[i].signal_epoch for i in fold.test_indices)
        assert max_train < min_test
        assert not (set(fold.train_indices) & set(fold.test_indices))
