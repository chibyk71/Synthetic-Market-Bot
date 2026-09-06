"""Milestone 3B — ML dataset, features, leakage, split, train, evaluate, persist."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from smb.ml import (
    DEFAULT_RANDOM_SEED,
    SCHEMA_VERSION,
    TargetPolicy,
    build_dataset,
    build_observation,
    chronological_split,
    chronological_split_by_epochs,
    evaluate_predictions,
    evaluate_split,
    extract_features,
    feature_names,
    feature_schema,
    load_model,
    resolve_target,
    save_model,
    train_baseline,
)
from smb.ml.features import FEATURE_NAMES
from smb.ml.models import MLObservation
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

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_signal(
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "1HZ75V",
    signal_epoch: int = 1_700_000_000,
    swept_level: float = 100.0,
    sweep_low: float = 98.0,
    sweep_high: float = 103.0,
    bars_after_sweep: int = 2,
    body_range_ratio: float = 0.7,
    body_atr_ratio: float = 1.2,
    atr: float = 1.5,
    fvg_size: float = 0.8,
    fvg_size_atr_ratio: float | None = 0.53,
    m15_bias: str | None = "bullish",
    m15_high: float | None = 120.0,
    m15_low: float | None = 90.0,
    gap_low: float = 105.0,
    gap_high: float = 105.8,
) -> StrategySignal:
    swing = SwingPoint(
        kind="low" if direction is Direction.LONG else "high",
        price=swept_level,
        candle_start_epoch=signal_epoch - 900,
        candle_end_epoch=signal_epoch - 840,
        index=2,
        confirmed_at_epoch=signal_epoch - 720,
    )
    structure = SwingPoint(
        kind="high" if direction is Direction.LONG else "low",
        price=swept_level + (10.0 if direction is Direction.LONG else -10.0),
        candle_start_epoch=signal_epoch - 1200,
        candle_end_epoch=signal_epoch - 1140,
        index=0,
        confirmed_at_epoch=signal_epoch - 1020,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=swept_level,
        sweep_candle_start_epoch=signal_epoch - 600,
        sweep_candle_end_epoch=signal_epoch - 540,
        sweep_candle_low=sweep_low,
        sweep_candle_high=sweep_high,
        sweep_candle_close=(sweep_low + sweep_high) / 2,
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure.price,
        msb_candle_start_epoch=signal_epoch - 420,
        msb_candle_end_epoch=signal_epoch - 360,
        msb_candle_close=structure.price + (2.0 if direction is Direction.LONG else -2.0),
        bars_after_sweep=bars_after_sweep,
        structure_swing=structure,
    )
    body = body_range_ratio * 2.0  # range fixed at 2.0 for tests
    range_ = 2.0
    open_ = 110.0
    close = open_ + body if direction is Direction.LONG else open_ - body
    disp = Displacement(
        direction=direction,
        candle_start_epoch=signal_epoch - 300,
        candle_end_epoch=signal_epoch - 240,
        open=open_,
        high=max(open_, close) + 0.2,
        low=min(open_, close) - 0.2,
        close=close,
        body=body,
        range_=range_,
        body_range_ratio=body_range_ratio,
        body_atr_ratio=body_atr_ratio,
        atr=atr,
    )
    fvg = FairValueGap(
        direction=direction,
        gap_low=gap_low,
        gap_high=gap_high,
        size=fvg_size,
        size_atr_ratio=fvg_size_atr_ratio,
        candle1_start_epoch=signal_epoch - 180,
        candle2_start_epoch=signal_epoch - 120,
        candle3_start_epoch=signal_epoch - 60,
        candle3_end_epoch=signal_epoch,
    )
    m15 = M15Context(
        last_m15_start_epoch=signal_epoch - 900,
        last_m15_end_epoch=signal_epoch - 1,
        last_m15_close=108.0,
        recent_high=m15_high,
        recent_low=m15_low,
        directional_bias=m15_bias,  # type: ignore[arg-type]
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
        reference_levels={"swept": swept_level},
        metadata={},
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


def _make_sim(
    signal: StrategySignal,
    outcome: SimulationOutcome,
    *,
    entry_time: int | None = None,
    exit_time: int | None = None,
    entry_price: float | None = None,
    exit_price: float | None = None,
) -> TradeSimulationResult:
    candidate = _make_candidate(signal)
    filled = outcome is not SimulationOutcome.NO_FILL
    if not filled:
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
    et = entry_time if entry_time is not None else signal.signal_epoch + 10
    ep = entry_price if entry_price is not None else candidate.entry_price
    if outcome is SimulationOutcome.TP:
        xt = exit_time if exit_time is not None else et + 100
        xp = exit_price if exit_price is not None else candidate.take_profit
        reason = ExitReason.TP
    elif outcome is SimulationOutcome.SL:
        xt = exit_time if exit_time is not None else et + 50
        xp = exit_price if exit_price is not None else candidate.stop_loss
        reason = ExitReason.SL
    else:
        xt = exit_time if exit_time is not None else signal.signal_epoch + 900
        xp = exit_price  # may be None for TIMEOUT
        reason = ExitReason.TIMEOUT
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


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def test_feature_names_stable_order():
    names = feature_names()
    assert names == FEATURE_NAMES
    assert len(names) == len(set(names))
    assert feature_schema().version == SCHEMA_VERSION


def test_extract_features_direction_and_sweep():
    long_sig = _make_signal(direction=Direction.LONG, sweep_low=97.0, swept_level=100.0)
    short_sig = _make_signal(
        direction=Direction.SHORT,
        sweep_high=104.0,
        swept_level=100.0,
        m15_bias="bearish",
    )
    fl = extract_features(long_sig)
    fs = extract_features(short_sig)
    assert fl[FEATURE_NAMES.index("direction")] == 1.0
    assert fs[FEATURE_NAMES.index("direction")] == -1.0
    assert fl[FEATURE_NAMES.index("sweep_depth")] == pytest.approx(3.0)
    assert fs[FEATURE_NAMES.index("sweep_depth")] == pytest.approx(4.0)
    assert fl[FEATURE_NAMES.index("msb_bars_after_sweep")] == 2.0


def test_extract_features_displacement_fvg_m15():
    sig = _make_signal(
        body_range_ratio=0.65,
        body_atr_ratio=1.1,
        atr=2.0,
        fvg_size=1.5,
        fvg_size_atr_ratio=0.75,
        m15_bias="neutral",
        m15_high=130.0,
        m15_low=100.0,
        gap_low=110.0,
        gap_high=111.5,
    )
    f = extract_features(sig)
    assert f[FEATURE_NAMES.index("displacement_body_range_ratio")] == pytest.approx(0.65)
    assert f[FEATURE_NAMES.index("displacement_body_atr_ratio")] == pytest.approx(1.1)
    assert f[FEATURE_NAMES.index("atr")] == pytest.approx(2.0)
    assert f[FEATURE_NAMES.index("fvg_size")] == pytest.approx(1.5)
    assert f[FEATURE_NAMES.index("fvg_size_atr_ratio")] == pytest.approx(0.75)
    assert f[FEATURE_NAMES.index("fvg_size_atr_missing")] == 0.0
    assert f[FEATURE_NAMES.index("m15_bias")] == 0.0
    assert f[FEATURE_NAMES.index("m15_bias_missing")] == 0.0
    assert f[FEATURE_NAMES.index("m15_recent_range")] == pytest.approx(30.0)


def test_missing_m15_and_fvg_atr():
    sig = _make_signal(
        fvg_size_atr_ratio=None,
        m15_bias=None,
        m15_high=None,
        m15_low=None,
    )
    f = extract_features(sig)
    assert f[FEATURE_NAMES.index("fvg_size_atr_ratio")] == 0.0
    assert f[FEATURE_NAMES.index("fvg_size_atr_missing")] == 1.0
    assert f[FEATURE_NAMES.index("m15_bias_missing")] == 1.0
    assert f[FEATURE_NAMES.index("m15_range_missing")] == 1.0
    assert f[FEATURE_NAMES.index("signal_vs_m15_missing")] == 1.0


def test_hour_of_day_deterministic():
    # 1700000000 → 2023-11-14 22:13:20 UTC → hour 22
    sig = _make_signal(signal_epoch=1_700_000_000)
    f = extract_features(sig)
    assert f[FEATURE_NAMES.index("hour_of_day")] == 22.0


def test_instrument_encoding_canonical_ids():
    """Canonical Deriv IDs and display names; unknowns stay (0, 0)."""
    from smb.ml.features import _instrument_flags

    assert _instrument_flags("1HZ75V") == (1.0, 0.0)
    assert _instrument_flags("1hz75v") == (1.0, 0.0)  # case normalization
    assert _instrument_flags(" 1HZ75V ") == (1.0, 0.0)
    assert _instrument_flags("Volatility 75 (1s) Index") == (1.0, 0.0)
    assert _instrument_flags("volatility 75 (1s) index") == (1.0, 0.0)

    assert _instrument_flags("stpRNG") == (0.0, 1.0)
    assert _instrument_flags("STPRNG") == (0.0, 1.0)  # case normalization
    assert _instrument_flags("Step Index 100") == (0.0, 1.0)
    assert _instrument_flags("step index 100") == (0.0, 1.0)

    # Unknown / generic must not silently classify as V75 or Step
    assert _instrument_flags("synthetic_index") == (0.0, 0.0)
    assert _instrument_flags("R_75") == (0.0, 0.0)
    assert _instrument_flags("Volatility 75 Index") == (0.0, 0.0)  # not the 1s variant
    assert _instrument_flags("Step Index 200") == (0.0, 0.0)
    assert _instrument_flags("unknown") == (0.0, 0.0)
    assert _instrument_flags("") == (0.0, 0.0)

    v75 = extract_features(_make_signal(instrument="1HZ75V"))
    step = extract_features(_make_signal(instrument="stpRNG"))
    assert v75[FEATURE_NAMES.index("instrument_v75")] == 1.0
    assert v75[FEATURE_NAMES.index("instrument_step100")] == 0.0
    assert step[FEATURE_NAMES.index("instrument_v75")] == 0.0
    assert step[FEATURE_NAMES.index("instrument_step100")] == 1.0


# ---------------------------------------------------------------------------
# Leakage regression tests (mandatory)
# ---------------------------------------------------------------------------


def test_features_ignore_future_exit_price():
    sig = _make_signal()
    base = extract_features(sig)
    sim_a = _make_sim(sig, SimulationOutcome.TP, exit_price=999.0)
    sim_b = _make_sim(sig, SimulationOutcome.TP, exit_price=1.0)
    # Features come from signal only
    assert extract_features(sim_a.candidate.source_signal) == base
    assert extract_features(sim_b.candidate.source_signal) == base
    obs_a = build_observation(sim_a)
    obs_b = build_observation(sim_b)
    assert obs_a.features == obs_b.features == base


def test_features_ignore_future_exit_time_and_mfe_mae():
    sig = _make_signal()
    base = extract_features(sig)
    sim = _make_sim(sig, SimulationOutcome.SL, exit_time=sig.signal_epoch + 50)
    obs = build_observation(sim)
    assert obs.features == base
    # Mutating conceptual future values: rebuild with different exit still same feats
    sim2 = _make_sim(sig, SimulationOutcome.SL, exit_time=sig.signal_epoch + 5000)
    assert build_observation(sim2).features == base


def test_features_independent_of_outcome():
    sig = _make_signal()
    base = extract_features(sig)
    for outcome in (
        SimulationOutcome.TP,
        SimulationOutcome.SL,
        SimulationOutcome.TIMEOUT,
        SimulationOutcome.NO_FILL,
    ):
        obs = build_observation(_make_sim(sig, outcome))
        assert obs.features == base


# ---------------------------------------------------------------------------
# Target policy
# ---------------------------------------------------------------------------


def test_resolve_target_filled_tp_positive():
    assert resolve_target(SimulationOutcome.TP, TargetPolicy.FILLED_TP_POSITIVE) == 1
    assert resolve_target(SimulationOutcome.SL, TargetPolicy.FILLED_TP_POSITIVE) == 0
    assert resolve_target(SimulationOutcome.TIMEOUT, TargetPolicy.FILLED_TP_POSITIVE) == 0
    assert resolve_target(SimulationOutcome.NO_FILL, TargetPolicy.FILLED_TP_POSITIVE) is None


def test_resolve_target_include_no_fill():
    assert resolve_target(SimulationOutcome.NO_FILL, TargetPolicy.INCLUDE_NO_FILL_AS_NEGATIVE) == 0
    assert resolve_target(SimulationOutcome.TP, TargetPolicy.INCLUDE_NO_FILL_AS_NEGATIVE) == 1


def test_observation_preserves_outcome():
    sig = _make_signal()
    sim = _make_sim(sig, SimulationOutcome.NO_FILL)
    obs = build_observation(sim, policy=TargetPolicy.FILLED_TP_POSITIVE)
    assert obs.target is None
    assert obs.outcome is SimulationOutcome.NO_FILL
    assert obs.filled is False


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


def test_build_dataset_ordering_and_counts():
    sigs = [
        _make_signal(signal_epoch=100 + i * 10, instrument="1HZ75V")
        for i in range(5)
    ]
    # reverse input order
    sims = [
        _make_sim(sigs[i], SimulationOutcome.TP if i % 2 == 0 else SimulationOutcome.SL)
        for i in (4, 2, 0, 3, 1)
    ]
    ds = build_dataset(sims)
    epochs = [o.signal_epoch for o in ds.observations]
    assert epochs == sorted(epochs)
    assert ds.n_samples == 5
    counts = ds.class_counts()
    assert counts["labeled"] == 5
    assert counts["positive"] == 3
    assert counts["negative"] == 2


def test_duplicate_identity_raises():
    sig = _make_signal()
    sim = _make_sim(sig, SimulationOutcome.TP)
    with pytest.raises(ValueError, match="duplicate"):
        build_dataset([sim, sim])


def test_dataset_excludes_no_fill_from_labels_by_default():
    sig_f = _make_signal(signal_epoch=100)
    sig_n = _make_signal(signal_epoch=200, instrument="Step Index 100")
    ds = build_dataset(
        [
            _make_sim(sig_f, SimulationOutcome.TP),
            _make_sim(sig_n, SimulationOutcome.NO_FILL),
        ]
    )
    assert ds.n_samples == 2
    assert len(ds.labeled) == 1
    assert ds.labels() == [1]


# ---------------------------------------------------------------------------
# Chronological split
# ---------------------------------------------------------------------------


def test_chronological_split_order_and_disjoint():
    sims = [
        _make_sim(
            _make_signal(signal_epoch=1000 + i * 100),
            SimulationOutcome.TP if i < 3 else SimulationOutcome.SL,
        )
        for i in range(10)
    ]
    ds = build_dataset(sims)
    split = chronological_split(ds, train_ratio=0.5, validation_ratio=0.2, test_ratio=0.3)
    labeled = ds.labeled
    train_epochs = [labeled[i].signal_epoch for i in split.train_indices]
    val_epochs = [labeled[i].signal_epoch for i in split.validation_indices]
    test_epochs = [labeled[i].signal_epoch for i in split.test_indices]
    if train_epochs and val_epochs:
        assert max(train_epochs) <= min(val_epochs)
    if val_epochs and test_epochs:
        assert max(val_epochs) <= min(test_epochs)
    all_i = set(split.train_indices) | set(split.validation_indices) | set(split.test_indices)
    assert len(all_i) == len(labeled)


def test_epoch_boundary_split():
    sims = [
        _make_sim(_make_signal(signal_epoch=e), SimulationOutcome.TP)
        for e in (100, 200, 300, 400, 500)
    ]
    ds = build_dataset(sims)
    split = chronological_split_by_epochs(ds, train_end_epoch=200, validation_end_epoch=400)
    assert len(split.train_indices) == 2
    assert len(split.validation_indices) == 2
    assert len(split.test_indices) == 1


# ---------------------------------------------------------------------------
# Training / evaluation / persistence
# ---------------------------------------------------------------------------


def _balanced_dataset(n: int = 40) -> object:
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
    return build_dataset(sims)


def test_train_and_evaluate_deterministic():
    ds = _balanced_dataset(40)
    split = chronological_split(ds)
    clf1, art1 = train_baseline(ds, split, random_seed=DEFAULT_RANDOM_SEED)
    clf2, art2 = train_baseline(ds, split, random_seed=DEFAULT_RANDOM_SEED)
    X_test = [list(ds.labeled[i].features) for i in split.test_indices]
    if X_test:
        p1 = list(clf1.predict(X_test))
        p2 = list(clf2.predict(X_test))
        assert p1 == p2
    assert art1.random_seed == DEFAULT_RANDOM_SEED
    assert art1.schema.names == feature_names()

    report = evaluate_split(clf1, ds, split, "test")
    assert report.n_samples == len(split.test_indices)
    assert report.accuracy is None or (0.0 <= report.accuracy <= 1.0)
    if report.confusion_matrix is not None:
        (tn, fp), (fn, tp) = report.confusion_matrix
        assert tn + fp + fn + tp == report.n_samples


def test_evaluate_one_class_safe():
    # all positive
    y_true = [1, 1, 1]
    y_pred = [1, 0, 1]
    r = evaluate_predictions(y_true, y_pred, partition="x")
    assert r.n_positive == 3
    assert r.n_negative == 0
    assert r.roc_auc is None  # undefined
    assert r.accuracy == pytest.approx(2 / 3)


def test_save_load_roundtrip(tmp_path: Path):
    ds = _balanced_dataset(30)
    split = chronological_split(ds)
    clf, artifact = train_baseline(ds, split)
    path = tmp_path / "model.joblib"
    save_model(path, clf, artifact)
    clf2, art2, payload = load_model(path)
    assert art2.schema.names == artifact.schema.names
    assert art2.random_seed == artifact.random_seed
    assert payload["schema_version"] == SCHEMA_VERSION
    X = [list(ds.labeled[i].features) for i in split.test_indices] or [
        list(ds.labeled[0].features)
    ]
    assert list(clf.predict(X)) == list(clf2.predict(X))


def test_ml_observation_rejects_bad_target():
    sig = _make_signal()
    feats = extract_features(sig)
    with pytest.raises(ValueError):
        MLObservation(
            instrument="1HZ75V",
            signal_epoch=1,
            direction=Direction.LONG,
            features=feats,
            target=2,
            outcome=SimulationOutcome.TP,
            filled=True,
        )


# ---------------------------------------------------------------------------
# Outcome / fill consistency at dataset boundary
# ---------------------------------------------------------------------------


def test_valid_outcome_fill_combinations():
    sig = _make_signal()
    for outcome in (
        SimulationOutcome.TP,
        SimulationOutcome.SL,
        SimulationOutcome.TIMEOUT,
    ):
        obs = build_observation(_make_sim(sig, outcome))
        assert obs.filled is True
        assert obs.outcome is outcome
        if outcome is SimulationOutcome.TP:
            assert obs.target == 1
        else:
            assert obs.target == 0

    nf = build_observation(_make_sim(sig, SimulationOutcome.NO_FILL))
    assert nf.filled is False
    assert nf.outcome is SimulationOutcome.NO_FILL
    assert nf.target is None  # default FILLED_TP_POSITIVE policy


def test_inconsistent_tp_sl_timeout_unfilled_raise():
    """TP/SL/TIMEOUT with filled=False must not produce a supervised label.

    Domain model rejects these at construction; the ML boundary also rejects
    any inconsistent object that reaches it.
    """
    from unittest.mock import MagicMock

    from smb.ml.dataset import _assert_outcome_fill_consistency

    sig = _make_signal()
    candidate = _make_candidate(sig)

    # Domain model: constructing TP/SL/TIMEOUT with filled=False raises
    for outcome in (
        SimulationOutcome.TP,
        SimulationOutcome.SL,
        SimulationOutcome.TIMEOUT,
    ):
        with pytest.raises(ValueError):
            TradeSimulationResult(
                instrument=sig.instrument,
                direction=sig.direction,
                signal_epoch=sig.signal_epoch,
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

    # ML boundary: same inconsistent states raise if they reach the builder
    for outcome in (
        SimulationOutcome.TP,
        SimulationOutcome.SL,
        SimulationOutcome.TIMEOUT,
    ):
        mock = MagicMock()
        mock.outcome = outcome
        mock.filled = False
        with pytest.raises(ValueError, match="filled=True"):
            _assert_outcome_fill_consistency(mock)

    mock_nf = MagicMock()
    mock_nf.outcome = SimulationOutcome.NO_FILL
    mock_nf.filled = True
    with pytest.raises(ValueError, match="filled=False"):
        _assert_outcome_fill_consistency(mock_nf)

    # Consistent pairs pass at the ML boundary
    for outcome, filled in (
        (SimulationOutcome.TP, True),
        (SimulationOutcome.SL, True),
        (SimulationOutcome.TIMEOUT, True),
        (SimulationOutcome.NO_FILL, False),
    ):
        mock = MagicMock()
        mock.outcome = outcome
        mock.filled = filled
        _assert_outcome_fill_consistency(mock)  # no raise


def test_feature_vector_all_finite():
    f = extract_features(_make_signal())
    assert all(math.isfinite(v) for v in f)
    assert len(f) == len(FEATURE_NAMES)
