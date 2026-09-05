"""TradeConstructor: LONG/SHORT geometry, rejections, determinism, causality."""

from __future__ import annotations

import inspect
import math

import pytest

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
from smb.trade import (
    RejectionReason,
    RiskContext,
    TradeConfig,
    TradeConstructor,
)


def _make_signal(
    *,
    direction: Direction,
    swept_level: float,
    gap_low: float,
    gap_high: float,
    atr: float,
    instrument: str = "vol75",
    signal_epoch: int = 1000,
) -> StrategySignal:
    """Build a minimal but complete StrategySignal for construction tests."""
    if direction == Direction.LONG:
        swing_kind: str = "low"
        structure_kind: str = "high"
        structure_price = swept_level + 10.0
        msb_close = structure_price + 2.0
        disp_open, disp_close = structure_price, structure_price + 5.0
    else:
        swing_kind = "high"
        structure_kind = "low"
        structure_price = swept_level - 10.0
        msb_close = structure_price - 2.0
        disp_open, disp_close = structure_price, structure_price - 5.0

    swing = SwingPoint(
        kind=swing_kind,  # type: ignore[arg-type]
        price=swept_level,
        candle_start_epoch=100,
        candle_end_epoch=160,
        index=2,
        confirmed_at_epoch=280,
    )
    structure = SwingPoint(
        kind=structure_kind,  # type: ignore[arg-type]
        price=structure_price,
        candle_start_epoch=0,
        candle_end_epoch=60,
        index=0,
        confirmed_at_epoch=180,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=swept_level,
        sweep_candle_start_epoch=300,
        sweep_candle_end_epoch=360,
        sweep_candle_low=min(swept_level - 2, swept_level + 2),
        sweep_candle_high=max(swept_level - 2, swept_level + 2),
        sweep_candle_close=swept_level + (1 if direction == Direction.LONG else -1),
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure_price,
        msb_candle_start_epoch=420,
        msb_candle_end_epoch=480,
        msb_candle_close=msb_close,
        bars_after_sweep=1,
        structure_swing=structure,
    )
    body = abs(disp_close - disp_open)
    disp = Displacement(
        direction=direction,
        candle_start_epoch=480,
        candle_end_epoch=540,
        open=disp_open,
        high=max(disp_open, disp_close) + 1,
        low=min(disp_open, disp_close) - 1,
        close=disp_close,
        body=body,
        range_=body + 2,
        body_range_ratio=body / (body + 2),
        body_atr_ratio=1.0,
        atr=atr,
    )
    fvg = FairValueGap(
        direction=direction,
        gap_low=gap_low,
        gap_high=gap_high,
        size=gap_high - gap_low,
        size_atr_ratio=(gap_high - gap_low) / atr if atr > 0 else None,
        candle1_start_epoch=480,
        candle2_start_epoch=540,
        candle3_start_epoch=600,
        candle3_end_epoch=signal_epoch,
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
        m15_context=M15Context(None, None, None, None, None, None),
    )


def test_long_construction_geometry():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig(
        risk_per_trade=0.01,
        target_rr=2.0,
        minimum_rr=1.5,
        sl_atr_buffer=0.10,
    )
    ctor = TradeConstructor(cfg)
    result = ctor.construct(signal, RiskContext(equity=10_000.0))

    assert result.accepted is True
    assert result.rejection_reason is None
    trade = result.trade
    assert trade is not None

    assert trade.instrument == "vol75"
    assert trade.direction == Direction.LONG
    assert trade.signal_epoch == signal.signal_epoch
    assert trade.entry_zone_low == 115.0
    assert trade.entry_zone_high == 117.0
    assert trade.entry_price == 116.0
    assert trade.stop_loss == 99.5
    assert trade.stop_loss < trade.entry_price
    assert trade.take_profit == 149.0
    assert trade.take_profit > trade.entry_price
    assert trade.risk_distance == 16.5
    assert trade.reward_distance == 33.0
    assert trade.risk_reward == 2.0
    assert trade.risk_percent == 0.01
    assert trade.risk_amount == 100.0
    assert trade.position_size == pytest.approx(100.0 / 16.5)
    assert trade.source_signal is signal


def test_long_zero_buffer():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=110.0,
        gap_high=112.0,
        atr=10.0,
    )
    cfg = TradeConfig(sl_atr_buffer=0.0, target_rr=2.0, minimum_rr=1.5)
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=5_000.0))
    assert result.accepted is True
    assert result.trade is not None
    assert result.trade.stop_loss == 100.0
    assert result.trade.entry_price == 111.0
    assert result.trade.risk_distance == 11.0
    assert result.trade.take_profit == 133.0
    assert result.trade.risk_amount == 50.0
    assert result.trade.position_size == pytest.approx(50.0 / 11.0)


def test_short_construction_geometry():
    signal = _make_signal(
        direction=Direction.SHORT,
        swept_level=100.0,
        gap_low=83.0,
        gap_high=85.0,
        atr=5.0,
    )
    cfg = TradeConfig(
        risk_per_trade=0.01,
        target_rr=2.0,
        minimum_rr=1.5,
        sl_atr_buffer=0.10,
    )
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    trade = result.trade
    assert trade is not None
    assert trade.direction == Direction.SHORT
    assert trade.entry_zone_low == 83.0
    assert trade.entry_zone_high == 85.0
    assert trade.entry_price == 84.0
    assert trade.stop_loss == 100.5
    assert trade.stop_loss > trade.entry_price
    assert trade.take_profit == 51.0
    assert trade.take_profit < trade.entry_price
    assert trade.risk_distance == 16.5
    assert trade.reward_distance == 33.0
    assert trade.risk_reward == 2.0
    assert trade.risk_amount == 100.0
    assert trade.position_size == pytest.approx(100.0 / 16.5)


def test_reject_invalid_fvg_geometry():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=117.0,
        gap_high=115.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.trade is None
    assert result.rejection_reason == RejectionReason.INVALID_FVG


def test_reject_equal_fvg_bounds():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=116.0,
        gap_high=116.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_FVG


def test_reject_stop_on_wrong_side_long():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=90.0,
        gap_high=92.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_STOP


def test_reject_stop_on_wrong_side_short():
    signal = _make_signal(
        direction=Direction.SHORT,
        swept_level=100.0,
        gap_low=110.0,
        gap_high=112.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_STOP


def test_reject_insufficient_rr():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig(target_rr=1.2, minimum_rr=1.5)
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INSUFFICIENT_RR


def test_reject_minimum_rr_boundary_accepted():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig(target_rr=1.5, minimum_rr=1.5)
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    assert result.trade is not None
    assert result.trade.risk_reward == 1.5


def test_reject_rr_just_below_minimum():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig(target_rr=1.499, minimum_rr=1.5)
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INSUFFICIENT_RR


def test_reject_negative_atr():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=-1.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_ATR


def test_zero_atr_with_zero_buffer_ok():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=0.0,
    )
    cfg = TradeConfig(sl_atr_buffer=0.10, target_rr=2.0, minimum_rr=1.5)
    result = TradeConstructor(cfg).construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    assert result.trade is not None
    assert result.trade.stop_loss == 100.0


def test_large_and_small_equity():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig(risk_per_trade=0.01, target_rr=2.0, minimum_rr=1.5)
    ctor = TradeConstructor(cfg)
    big = ctor.construct(signal, RiskContext(equity=1_000_000.0))
    assert big.accepted is True
    assert big.trade is not None
    assert big.trade.risk_amount == 10_000.0
    assert big.trade.position_size == pytest.approx(10_000.0 / 16.5)
    small = ctor.construct(signal, RiskContext(equity=100.0))
    assert small.accepted is True
    assert small.trade is not None
    assert small.trade.risk_amount == 1.0
    assert small.trade.position_size == pytest.approx(1.0 / 16.5)


def test_determinism_identical_inputs():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    cfg = TradeConfig()
    risk = RiskContext(equity=10_000.0)
    ctor = TradeConstructor(cfg)
    r1 = ctor.construct(signal, risk)
    r2 = ctor.construct(signal, risk)
    assert r1.accepted is True and r2.accepted is True
    assert r1.trade is not None and r2.trade is not None
    assert r1.trade.entry_price == r2.trade.entry_price
    assert r1.trade.stop_loss == r2.trade.stop_loss
    assert r1.trade.take_profit == r2.trade.take_profit
    assert r1.trade.position_size == r2.trade.position_size
    assert r1.trade.risk_reward == r2.trade.risk_reward


def test_determinism_new_constructor_instances():
    signal = _make_signal(
        direction=Direction.SHORT,
        swept_level=200.0,
        gap_low=180.0,
        gap_high=182.0,
        atr=8.0,
    )
    cfg = TradeConfig(target_rr=2.5, minimum_rr=1.5, risk_per_trade=0.02)
    risk = RiskContext(equity=25_000.0)
    results = [TradeConstructor(cfg).construct(signal, risk) for _ in range(5)]
    assert all(r.accepted for r in results)
    ref = results[0].trade
    assert ref is not None
    for r in results[1:]:
        t = r.trade
        assert t is not None
        assert t.entry_price == ref.entry_price
        assert t.stop_loss == ref.stop_loss
        assert t.take_profit == ref.take_profit
        assert t.position_size == ref.position_size


def test_construct_signature_has_no_market_data():
    sig = inspect.signature(TradeConstructor.construct)
    param_names = set(sig.parameters) - {"self"}
    assert param_names == {"signal", "risk_context"}
    for forbidden in ("market", "candles", "data", "provider", "history", "client"):
        assert not any(forbidden in n for n in param_names)


def test_constructor_source_has_no_market_imports():
    import smb.trade.constructor as mod

    source = inspect.getsource(mod)
    for banned in (
        "HistoricalReplay",
        "CandleBuilder",
        "DerivClient",
        "fetch",
        "ticks_history",
        "CandleStore",
    ):
        assert banned not in source


def test_accepted_trade_immutable():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    trade = result.trade
    assert trade is not None
    with pytest.raises(AttributeError):
        trade.entry_price = 0.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        trade.position_size = 1.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        result.accepted = False  # type: ignore[misc]


def _assert_candidate_all_finite(trade) -> None:
    numeric_fields = (
        "entry_price",
        "entry_zone_low",
        "entry_zone_high",
        "stop_loss",
        "take_profit",
        "risk_distance",
        "reward_distance",
        "risk_reward",
        "risk_percent",
        "risk_amount",
        "position_size",
    )
    for name in numeric_fields:
        value = getattr(trade, name)
        assert math.isfinite(value), f"{name}={value!r} is not finite"


def test_accepted_candidate_all_numeric_fields_are_finite():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    assert result.trade is not None
    _assert_candidate_all_finite(result.trade)


def test_reject_nan_atr():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=float("nan"),
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_ATR


def test_reject_inf_atr():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=float("inf"),
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_ATR


def test_reject_nan_swept_level():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=float("nan"),
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_STOP


def test_reject_nan_fvg_bounds():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=float("nan"),
        gap_high=117.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_FVG


def test_reject_inf_fvg_bounds():
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=float("inf"),
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is False
    assert result.rejection_reason == RejectionReason.INVALID_FVG


def test_reject_inf_position_size_cannot_be_accepted():
    """Accepted candidate never carries non-finite position_size."""
    signal = _make_signal(
        direction=Direction.LONG,
        swept_level=100.0,
        gap_low=115.0,
        gap_high=117.0,
        atr=5.0,
    )
    result = TradeConstructor().construct(signal, RiskContext(equity=10_000.0))
    assert result.accepted is True
    assert result.trade is not None
    assert math.isfinite(result.trade.position_size)
    assert result.trade.position_size > 0.0
