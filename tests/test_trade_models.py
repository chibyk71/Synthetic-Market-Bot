"""Trade domain models: config validation, risk context, immutability."""

from __future__ import annotations

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
from smb.trade.models import (
    RejectionReason,
    RiskContext,
    TradeCandidate,
    TradeConfig,
    TradeConstructionResult,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid signal for model tests
# ---------------------------------------------------------------------------


def _minimal_signal(*, direction: Direction = Direction.LONG) -> StrategySignal:
    swing = SwingPoint(
        kind="low" if direction == Direction.LONG else "high",
        price=100.0,
        candle_start_epoch=100,
        candle_end_epoch=160,
        index=2,
        confirmed_at_epoch=280,
    )
    structure = SwingPoint(
        kind="high" if direction == Direction.LONG else "low",
        price=110.0 if direction == Direction.LONG else 90.0,
        candle_start_epoch=0,
        candle_end_epoch=60,
        index=0,
        confirmed_at_epoch=180,
    )
    sweep = LiquiditySweep(
        direction=direction,
        swept_level=100.0,
        sweep_candle_start_epoch=300,
        sweep_candle_end_epoch=360,
        sweep_candle_low=98.0,
        sweep_candle_high=103.0,
        sweep_candle_close=102.0,
        swing=swing,
    )
    msb = MarketStructureBreak(
        direction=direction,
        broken_level=structure.price,
        msb_candle_start_epoch=420,
        msb_candle_end_epoch=480,
        msb_candle_close=112.0 if direction == Direction.LONG else 88.0,
        bars_after_sweep=1,
        structure_swing=structure,
    )
    disp = Displacement(
        direction=direction,
        candle_start_epoch=480,
        candle_end_epoch=540,
        open=112.0,
        high=120.0,
        low=111.0,
        close=119.0,
        body=7.0,
        range_=9.0,
        body_range_ratio=7 / 9,
        body_atr_ratio=1.0,
        atr=5.0,
    )
    if direction == Direction.LONG:
        fvg = FairValueGap(
            direction=direction,
            gap_low=115.0,
            gap_high=117.0,
            size=2.0,
            size_atr_ratio=0.4,
            candle1_start_epoch=480,
            candle2_start_epoch=540,
            candle3_start_epoch=600,
            candle3_end_epoch=660,
        )
    else:
        fvg = FairValueGap(
            direction=direction,
            gap_low=83.0,
            gap_high=85.0,
            size=2.0,
            size_atr_ratio=0.4,
            candle1_start_epoch=480,
            candle2_start_epoch=540,
            candle3_start_epoch=600,
            candle3_end_epoch=660,
        )
    return StrategySignal(
        instrument="vol75",
        direction=direction,
        signal_epoch=660,
        timeframe_context="M15+M1",
        sweep=sweep,
        msb=msb,
        displacement=disp,
        fvg=fvg,
        m15_context=M15Context(None, None, None, None, None, None),
    )


def _candidate_from(signal: StrategySignal | None = None) -> TradeCandidate:
    sig = signal or _minimal_signal()
    return TradeCandidate(
        instrument=sig.instrument,
        direction=sig.direction,
        signal_epoch=sig.signal_epoch,
        entry_price=116.0,
        entry_zone_low=115.0,
        entry_zone_high=117.0,
        stop_loss=99.5,
        take_profit=149.0,
        risk_distance=16.5,
        reward_distance=33.0,
        risk_reward=2.0,
        risk_percent=0.01,
        risk_amount=100.0,
        position_size=100.0 / 16.5,
        source_signal=sig,
    )


# ---------------------------------------------------------------------------
# TradeConfig
# ---------------------------------------------------------------------------


def test_trade_config_defaults():
    cfg = TradeConfig()
    assert cfg.risk_per_trade == 0.01
    assert cfg.target_rr == 2.0
    assert cfg.minimum_rr == 1.5
    assert cfg.sl_atr_buffer == 0.10


def test_trade_config_custom_valid():
    cfg = TradeConfig(
        risk_per_trade=0.02,
        target_rr=3.0,
        minimum_rr=1.0,
        sl_atr_buffer=0.0,
    )
    assert cfg.risk_per_trade == 0.02
    assert cfg.sl_atr_buffer == 0.0


@pytest.mark.parametrize("bad", [0.0, -0.01, 1.0, 1.5])
def test_trade_config_invalid_risk_per_trade(bad: float):
    with pytest.raises(ValueError, match="risk_per_trade"):
        TradeConfig(risk_per_trade=bad)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_trade_config_invalid_target_rr(bad: float):
    with pytest.raises(ValueError, match="target_rr"):
        TradeConfig(target_rr=bad)


@pytest.mark.parametrize("bad", [0.0, -0.5])
def test_trade_config_invalid_minimum_rr(bad: float):
    with pytest.raises(ValueError, match="minimum_rr"):
        TradeConfig(minimum_rr=bad)


def test_trade_config_invalid_sl_atr_buffer():
    with pytest.raises(ValueError, match="sl_atr_buffer"):
        TradeConfig(sl_atr_buffer=-0.01)


def test_trade_config_immutable():
    cfg = TradeConfig()
    with pytest.raises(AttributeError):
        cfg.risk_per_trade = 0.05  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RiskContext
# ---------------------------------------------------------------------------


def test_risk_context_valid():
    ctx = RiskContext(equity=10_000.0)
    assert ctx.equity == 10_000.0


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_risk_context_invalid_equity(bad: float):
    with pytest.raises(ValueError, match="equity"):
        RiskContext(equity=bad)


def test_risk_context_immutable():
    ctx = RiskContext(equity=1000.0)
    with pytest.raises(AttributeError):
        ctx.equity = 2000.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TradeCandidate / result immutability
# ---------------------------------------------------------------------------


def test_trade_candidate_immutable():
    cand = _candidate_from()
    with pytest.raises(AttributeError):
        cand.entry_price = 0.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        cand.position_size = 999.0  # type: ignore[misc]


def test_trade_candidate_has_no_execution_fields():
    cand = _candidate_from()
    for forbidden in (
        "order_id",
        "filled",
        "fill_price",
        "exit_price",
        "pnl",
        "status",
        "execution_timestamp",
        "broker_position_id",
    ):
        assert not hasattr(cand, forbidden)


def test_construction_result_accepted_shape():
    cand = _candidate_from()
    result = TradeConstructionResult(accepted=True, trade=cand, rejection_reason=None)
    assert result.accepted is True
    assert result.trade is cand
    assert result.rejection_reason is None


def test_construction_result_rejected_shape():
    result = TradeConstructionResult(
        accepted=False,
        trade=None,
        rejection_reason=RejectionReason.INSUFFICIENT_RR,
    )
    assert result.accepted is False
    assert result.trade is None
    assert result.rejection_reason == RejectionReason.INSUFFICIENT_RR


def test_construction_result_accepted_requires_trade():
    with pytest.raises(ValueError, match="TradeCandidate"):
        TradeConstructionResult(accepted=True, trade=None, rejection_reason=None)


def test_construction_result_rejected_requires_reason():
    with pytest.raises(ValueError, match="RejectionReason"):
        TradeConstructionResult(accepted=False, trade=None, rejection_reason=None)


def test_construction_result_immutable():
    result = TradeConstructionResult(
        accepted=False,
        trade=None,
        rejection_reason=RejectionReason.INVALID_FVG,
    )
    with pytest.raises(AttributeError):
        result.accepted = True  # type: ignore[misc]


def test_rejection_reason_values():
    assert RejectionReason.INVALID_FVG.value == "invalid_fvg"
    assert RejectionReason.INSUFFICIENT_RR.value == "insufficient_rr"
