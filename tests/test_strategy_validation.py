"""Unit tests for Milestone 3A StrategyValidationCalculator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from smb.deriv.history import Tick
from smb.research import ResearchMetricsCalculator, TradeResearchMetrics
from smb.simulation import (
    ExitReason,
    SimulationConfig,
    SimulationEngine,
    SimulationOutcome,
    TradeSimulationResult,
)
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
from smb.validation import (
    OutcomeCounts,
    StrategyValidationCalculator,
    StrategyValidationReport,
)

# ---------------------------------------------------------------------------
# Helpers — minimal candidate / direct result factories
# ---------------------------------------------------------------------------


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


def _minimal_signal(
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "vol75",
    signal_epoch: int = 1000,
) -> StrategySignal:
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
    fvg = FairValueGap(
        direction=direction,
        gap_low=115.0 if direction == Direction.LONG else 83.0,
        gap_high=117.0 if direction == Direction.LONG else 85.0,
        size=2.0,
        size_atr_ratio=0.4,
        candle1_start_epoch=480,
        candle2_start_epoch=540,
        candle3_start_epoch=600,
        candle3_end_epoch=660,
    )
    m15 = M15Context(None, None, None, None, None, None)
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


def _candidate(
    *,
    direction: Direction = Direction.LONG,
    signal_epoch: int = 1000,
    entry_price: float = 100.0,
    stop_loss: float = 95.0,
    take_profit: float = 110.0,
    instrument: str = "vol75",
) -> TradeCandidate:
    if direction == Direction.SHORT and stop_loss == 95.0 and take_profit == 110.0:
        stop_loss, take_profit = 105.0, 90.0
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    signal = _minimal_signal(
        direction=direction, instrument=instrument, signal_epoch=signal_epoch
    )
    return TradeCandidate(
        instrument=instrument,
        direction=direction,
        signal_epoch=signal_epoch,
        entry_price=entry_price,
        entry_zone_low=entry_price - 0.5,
        entry_zone_high=entry_price + 0.5,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk,
        reward_distance=reward,
        risk_reward=reward / risk if risk else 0.0,
        risk_percent=0.01,
        risk_amount=10.0,
        position_size=10.0 / risk if risk else 0.0,
        source_signal=signal,
    )


def _sim(
    outcome: SimulationOutcome,
    *,
    direction: Direction = Direction.LONG,
    instrument: str = "vol75",
    signal_epoch: int = 1000,
    entry_time: int | None = 1001,
    entry_price: float | None = 100.0,
    exit_time: int | None = 1010,
    exit_price: float | None = None,
    duration_seconds: int | None = 9,
) -> TradeSimulationResult:
    candidate = _candidate(
        direction=direction,
        instrument=instrument,
        signal_epoch=signal_epoch,
        entry_price=entry_price if entry_price is not None else 100.0,
    )
    if outcome == SimulationOutcome.NO_FILL:
        return TradeSimulationResult(
            instrument=instrument,
            direction=direction,
            signal_epoch=signal_epoch,
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
    if outcome == SimulationOutcome.TP:
        exit_reason = ExitReason.TP
        if exit_price is None:
            exit_price = candidate.take_profit
    elif outcome == SimulationOutcome.SL:
        exit_reason = ExitReason.SL
        if exit_price is None:
            exit_price = candidate.stop_loss
    else:
        exit_reason = ExitReason.TIMEOUT
        exit_price = None
    return TradeSimulationResult(
        instrument=instrument,
        direction=direction,
        signal_epoch=signal_epoch,
        outcome=outcome,
        filled=True,
        entry_time=entry_time,
        entry_price=entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        duration_seconds=duration_seconds,
        candidate=candidate,
    )


def _metrics_for(
    sim: TradeSimulationResult,
    *,
    mfe: float | None = None,
    mae: float | None = None,
) -> TradeResearchMetrics:
    if not sim.filled:
        return TradeResearchMetrics(
            instrument=sim.instrument,
            direction=sim.direction,
            signal_epoch=sim.signal_epoch,
            outcome=sim.outcome,
            filled=False,
            entry_time=None,
            entry_price=None,
            exit_time=None,
            exit_price=None,
            mfe=None,
            mae=None,
            mfe_time=None,
            mae_time=None,
            observation_start=None,
            observation_end=None,
            simulation=sim,
        )
    assert sim.entry_time is not None and sim.entry_price is not None
    assert sim.exit_time is not None
    mfe_v = 0.0 if mfe is None else mfe
    mae_v = 0.0 if mae is None else mae
    return TradeResearchMetrics(
        instrument=sim.instrument,
        direction=sim.direction,
        signal_epoch=sim.signal_epoch,
        outcome=sim.outcome,
        filled=True,
        entry_time=sim.entry_time,
        entry_price=sim.entry_price,
        exit_time=sim.exit_time,
        exit_price=sim.exit_price,
        mfe=mfe_v,
        mae=mae_v,
        mfe_time=sim.entry_time,
        mae_time=sim.entry_time,
        observation_start=sim.entry_time,
        observation_end=sim.exit_time,
        simulation=sim,
    )


def _calc() -> StrategyValidationCalculator:
    return StrategyValidationCalculator()


# ---------------------------------------------------------------------------
# Empty / all NO_FILL / pure outcomes
# ---------------------------------------------------------------------------


def test_empty_dataset():
    report = _calc().validate([])
    assert isinstance(report, StrategyValidationReport)
    c = report.overall.counts
    assert c == OutcomeCounts(
        total=0, filled=0, no_fill=0, wins=0, losses=0, timeouts=0
    )
    assert report.overall.rates.fill_rate is None
    assert report.overall.rates.win_rate is None
    assert report.overall.excursions.sample_size == 0
    assert report.overall.excursions.avg_mfe is None
    assert report.overall.duration.avg_duration_seconds is None
    assert report.by_direction == {}
    assert report.by_instrument == {}
    assert report.by_outcome[SimulationOutcome.NO_FILL.value] == 0


def test_all_no_fill():
    sims = [
        _sim(SimulationOutcome.NO_FILL, signal_epoch=1000),
        _sim(SimulationOutcome.NO_FILL, signal_epoch=2000),
    ]
    mets = [_metrics_for(s) for s in sims]
    report = _calc().validate(sims, mets)
    assert report.overall.counts.total == 2
    assert report.overall.counts.filled == 0
    assert report.overall.counts.no_fill == 2
    assert report.overall.rates.fill_rate == pytest.approx(0.0)
    assert report.overall.rates.win_rate is None
    assert report.overall.excursions.avg_mfe is None


def test_all_wins():
    sims = [
        _sim(SimulationOutcome.TP, duration_seconds=5),
        _sim(SimulationOutcome.TP, signal_epoch=2000, duration_seconds=15),
    ]
    mets = [
        _metrics_for(sims[0], mfe=10.0, mae=1.0),
        _metrics_for(sims[1], mfe=20.0, mae=2.0),
    ]
    report = _calc().validate(sims, mets)
    assert report.overall.counts.wins == 2
    assert report.overall.counts.losses == 0
    assert report.overall.rates.win_rate == pytest.approx(1.0)
    assert report.overall.rates.loss_rate == pytest.approx(0.0)
    assert report.overall.excursions.avg_mfe == pytest.approx(15.0)
    assert report.overall.excursions.median_mfe == pytest.approx(15.0)
    assert report.overall.duration.avg_duration_seconds == pytest.approx(10.0)


def test_all_losses():
    sims = [
        _sim(SimulationOutcome.SL, duration_seconds=3),
        _sim(SimulationOutcome.SL, signal_epoch=3000, duration_seconds=7),
    ]
    mets = [
        _metrics_for(sims[0], mfe=0.5, mae=5.0),
        _metrics_for(sims[1], mfe=1.5, mae=4.0),
    ]
    report = _calc().validate(sims, mets)
    assert report.overall.counts.losses == 2
    assert report.overall.rates.loss_rate == pytest.approx(1.0)
    assert report.overall.rates.win_rate == pytest.approx(0.0)
    assert report.overall.excursions.avg_mae == pytest.approx(4.5)


def test_mixed_tp_sl_timeout():
    sims = [
        _sim(SimulationOutcome.TP, signal_epoch=1000),
        _sim(SimulationOutcome.SL, signal_epoch=2000),
        _sim(SimulationOutcome.TIMEOUT, signal_epoch=3000, exit_time=3900),
        _sim(SimulationOutcome.NO_FILL, signal_epoch=4000),
    ]
    mets = [
        _metrics_for(sims[0], mfe=8.0, mae=1.0),
        _metrics_for(sims[1], mfe=0.0, mae=5.0),
        _metrics_for(sims[2], mfe=3.0, mae=2.0),
        _metrics_for(sims[3]),
    ]
    report = _calc().validate(sims, mets)
    c = report.overall.counts
    assert c.total == 4
    assert c.filled == 3
    assert c.no_fill == 1
    assert c.wins == 1
    assert c.losses == 1
    assert c.timeouts == 1
    assert report.overall.rates.fill_rate == pytest.approx(0.75)
    assert report.overall.rates.win_rate == pytest.approx(1 / 3)
    assert report.overall.rates.loss_rate == pytest.approx(1 / 3)
    assert report.overall.rates.timeout_rate == pytest.approx(1 / 3)
    assert report.by_outcome[SimulationOutcome.TP.value] == 1
    assert report.by_outcome[SimulationOutcome.TIMEOUT.value] == 1


# ---------------------------------------------------------------------------
# LONG / SHORT and instruments
# ---------------------------------------------------------------------------


def test_long_short_separation():
    sims = [
        _sim(SimulationOutcome.TP, direction=Direction.LONG, signal_epoch=1),
        _sim(SimulationOutcome.SL, direction=Direction.LONG, signal_epoch=2),
        _sim(SimulationOutcome.TP, direction=Direction.SHORT, signal_epoch=3),
    ]
    report = _calc().validate(sims)
    assert report.by_direction[Direction.LONG.value].counts.total == 2
    assert report.by_direction[Direction.LONG.value].counts.wins == 1
    assert report.by_direction[Direction.LONG.value].counts.losses == 1
    assert report.by_direction[Direction.SHORT.value].counts.total == 1
    assert report.by_direction[Direction.SHORT.value].counts.wins == 1


def test_multiple_instruments():
    sims = [
        _sim(SimulationOutcome.TP, instrument="vol75", signal_epoch=1),
        _sim(SimulationOutcome.NO_FILL, instrument="vol75", signal_epoch=2),
        _sim(SimulationOutcome.SL, instrument="step100", signal_epoch=3),
    ]
    report = _calc().validate(sims)
    assert set(report.by_instrument) == {"step100", "vol75"}
    assert report.by_instrument["vol75"].counts.total == 2
    assert report.by_instrument["vol75"].counts.filled == 1
    assert report.by_instrument["step100"].counts.losses == 1


# ---------------------------------------------------------------------------
# Rates / aggregates
# ---------------------------------------------------------------------------


def test_fill_rate_calculation():
    sims = [
        _sim(SimulationOutcome.TP, signal_epoch=1),
        _sim(SimulationOutcome.NO_FILL, signal_epoch=2),
        _sim(SimulationOutcome.NO_FILL, signal_epoch=3),
        _sim(SimulationOutcome.SL, signal_epoch=4),
    ]
    report = _calc().validate(sims)
    assert report.overall.rates.fill_rate == pytest.approx(0.5)


def test_mfe_mae_aggregation():
    sims = [
        _sim(SimulationOutcome.TP, signal_epoch=1),
        _sim(SimulationOutcome.SL, signal_epoch=2),
        _sim(SimulationOutcome.TIMEOUT, signal_epoch=3, exit_time=1900),
    ]
    mets = [
        _metrics_for(sims[0], mfe=10.0, mae=2.0),
        _metrics_for(sims[1], mfe=1.0, mae=8.0),
        _metrics_for(sims[2], mfe=4.0, mae=3.0),
    ]
    report = _calc().validate(sims, mets)
    e = report.overall.excursions
    assert e.sample_size == 3
    assert e.avg_mfe == pytest.approx((10 + 1 + 4) / 3)
    assert e.avg_mae == pytest.approx((2 + 8 + 3) / 3)
    assert e.median_mfe == pytest.approx(4.0)
    assert e.median_mae == pytest.approx(3.0)


def test_duration_aggregation():
    sims = [
        _sim(SimulationOutcome.TP, duration_seconds=10),
        _sim(SimulationOutcome.SL, signal_epoch=2000, duration_seconds=20),
        _sim(SimulationOutcome.NO_FILL, signal_epoch=3000),
    ]
    report = _calc().validate(sims)
    assert report.overall.duration.sample_size == 2
    assert report.overall.duration.avg_duration_seconds == pytest.approx(15.0)


def test_undefined_statistics_are_none():
    """No filled trades → win/loss/timeout rates and excursions are None."""
    sims = [_sim(SimulationOutcome.NO_FILL)]
    report = _calc().validate(sims, [_metrics_for(sims[0])])
    assert report.overall.rates.win_rate is None
    assert report.overall.rates.loss_rate is None
    assert report.overall.rates.timeout_rate is None
    assert report.overall.excursions.avg_mfe is None
    assert report.overall.duration.avg_duration_seconds is None


def test_metrics_without_series_leaves_excursions_empty():
    sims = [_sim(SimulationOutcome.TP)]
    report = _calc().validate(sims, metrics=None)
    assert report.overall.counts.wins == 1
    assert report.overall.excursions.sample_size == 0
    assert report.overall.excursions.avg_mfe is None


# ---------------------------------------------------------------------------
# Invalid input / consistency
# ---------------------------------------------------------------------------


def test_inconsistent_metrics_length_raises():
    sims = [_sim(SimulationOutcome.TP), _sim(SimulationOutcome.SL, signal_epoch=2)]
    mets = [_metrics_for(sims[0])]
    with pytest.raises(ValueError, match="length"):
        _calc().validate(sims, mets)


def test_inconsistent_metrics_identity_raises():
    sim_a = _sim(SimulationOutcome.TP, signal_epoch=1)
    sim_b = _sim(SimulationOutcome.SL, signal_epoch=2)
    bad = _metrics_for(sim_b)
    with pytest.raises(ValueError, match="inconsistent"):
        _calc().validate([sim_a], [bad])


def test_no_nan_infinity_in_rates():
    sims = [_sim(SimulationOutcome.NO_FILL) for _ in range(5)]
    report = _calc().validate(sims)
    for rate in (
        report.overall.rates.fill_rate,
        report.overall.rates.win_rate,
        report.overall.rates.loss_rate,
        report.overall.rates.timeout_rate,
    ):
        if rate is not None:
            assert rate == rate  # not NaN
            assert abs(rate) != float("inf")


def test_report_mappings_immutable():
    report = _calc().validate([_sim(SimulationOutcome.TP)])
    with pytest.raises(TypeError):
        report.by_direction["long"] = report.overall  # type: ignore[index]


# ---------------------------------------------------------------------------
# Integration: does not alter 2C/2D outcomes
# ---------------------------------------------------------------------------


def test_validation_consumes_2c_2d_without_changing_outcomes():
    """End-to-end: 2C → 2D → 3A; simulation/metrics objects unchanged."""
    c = _candidate(direction=Direction.LONG, entry_price=100.0, take_profit=110.0)
    ticks = [
        _tick(1001, 100.0),
        _tick(1002, 103.0),
        _tick(1003, 98.0),
        _tick(1004, 110.0),
    ]
    sim = SimulationEngine(SimulationConfig(max_duration_seconds=900)).simulate(
        c, ticks
    )
    met = ResearchMetricsCalculator().calculate(sim, ticks)
    before_outcome = sim.outcome
    before_mfe = met.mfe
    report = _calc().validate([sim], [met])
    assert sim.outcome is before_outcome
    assert met.mfe == before_mfe
    assert report.overall.counts.wins == 1
    assert report.overall.excursions.avg_mfe == pytest.approx(before_mfe)
    assert report.overall.rates.fill_rate == pytest.approx(1.0)
