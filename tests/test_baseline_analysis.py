"""Milestone 3B baseline analysis tests."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from smb.research.baseline import (
    BaselineAnalysisCalculator,
    format_baseline_analysis,
)
from smb.research.experiment import (
    ExperimentConfig,
    ExperimentResult,
    ExperimentSummary,
    TradeExperimentRow,
)
from smb.research.metrics import ResearchMetricsCalculator
from smb.research.stats import distribution, percentile
from smb.simulation.engine import SimulationEngine
from smb.simulation.models import (
    ExitReason,
    SimulationConfig,
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


def _swing(kind: str, price: float, epoch: int) -> SwingPoint:
    return SwingPoint(
        kind=kind,  # type: ignore[arg-type]
        price=price,
        candle_start_epoch=epoch - 60,
        candle_end_epoch=epoch,
        index=0,
        confirmed_at_epoch=epoch + 120,
    )


def _signal(
    *,
    epoch: int,
    direction: Direction = Direction.LONG,
    instrument: str = "vol",
    m15_bias: str | None = "bullish",
) -> StrategySignal:
    swing = _swing("low" if direction == Direction.LONG else "high", 100.0, epoch - 300)
    structure = _swing("high" if direction == Direction.LONG else "low", 105.0, epoch - 600)
    sweep = LiquiditySweep(
        direction,
        swing.price,
        epoch - 180,
        epoch - 120,
        99.0,
        101.0,
        100.5,
        swing,
    )
    msb = MarketStructureBreak(
        direction,
        structure.price,
        epoch - 120,
        epoch - 60,
        106.0 if direction == Direction.LONG else 94.0,
        1,
        structure,
    )
    disp = Displacement(
        direction,
        epoch - 60,
        epoch - 30,
        100.0,
        110.0,
        99.0,
        108.0 if direction == Direction.LONG else 92.0,
        8.0,
        11.0,
        0.72,
        1.1,
        7.0,
    )
    if direction == Direction.LONG:
        fvg = FairValueGap(
            Direction.LONG, 102.0, 104.0, 2.0, 0.3, epoch - 120, epoch - 60, epoch - 30, epoch
        )
    else:
        fvg = FairValueGap(
            Direction.SHORT, 96.0, 98.0, 2.0, 0.3, epoch - 120, epoch - 60, epoch - 30, epoch
        )
    m15 = M15Context(
        epoch - 900,
        epoch - 900 + 900,
        103.0,
        110.0,
        95.0,
        m15_bias,  # type: ignore[arg-type]
    )
    return StrategySignal(
        instrument=instrument,
        direction=direction,
        signal_epoch=epoch,
        timeframe_context="M15+M1",
        sweep=sweep,
        msb=msb,
        displacement=disp,
        fvg=fvg,
        m15_context=m15,
        reference_levels=MappingProxyType({"fvg_low": fvg.gap_low, "fvg_high": fvg.gap_high}),
        metadata=MappingProxyType({}),
    )


def _candidate(signal: StrategySignal, *, risk_distance: float = 10.0) -> TradeCandidate:
    entry = (signal.fvg.gap_low + signal.fvg.gap_high) / 2.0
    if signal.direction == Direction.LONG:
        sl = entry - risk_distance
        tp = entry + 2.0 * risk_distance
    else:
        sl = entry + risk_distance
        tp = entry - 2.0 * risk_distance
    return TradeCandidate(
        instrument=signal.instrument,
        direction=signal.direction,
        signal_epoch=signal.signal_epoch,
        entry_price=entry,
        entry_zone_low=signal.fvg.gap_low,
        entry_zone_high=signal.fvg.gap_high,
        stop_loss=sl,
        take_profit=tp,
        risk_distance=risk_distance,
        reward_distance=2.0 * risk_distance,
        risk_reward=2.0,
        risk_percent=0.01,
        risk_amount=100.0,
        position_size=100.0 / risk_distance,
        source_signal=signal,
    )


def _sim(
    candidate: TradeCandidate,
    outcome: SimulationOutcome,
    *,
    entry_time: int | None = None,
    exit_time: int | None = None,
    entry_price: float | None = None,
    exit_price: float | None = None,
    duration: int | None = None,
) -> TradeSimulationResult:
    filled = outcome != SimulationOutcome.NO_FILL
    if not filled:
        return TradeSimulationResult(
            instrument=candidate.instrument,
            direction=candidate.direction,
            signal_epoch=candidate.signal_epoch,
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
    et = entry_time if entry_time is not None else candidate.signal_epoch + 10
    ep = entry_price if entry_price is not None else candidate.entry_price
    if outcome == SimulationOutcome.TIMEOUT:
        xt = exit_time if exit_time is not None else candidate.signal_epoch + 900
        return TradeSimulationResult(
            instrument=candidate.instrument,
            direction=candidate.direction,
            signal_epoch=candidate.signal_epoch,
            outcome=outcome,
            filled=True,
            entry_time=et,
            entry_price=ep,
            exit_time=xt,
            exit_price=None,
            exit_reason=ExitReason.TIMEOUT,
            duration_seconds=duration if duration is not None else xt - et,
            candidate=candidate,
        )
    if outcome == SimulationOutcome.TP:
        xp = exit_price if exit_price is not None else candidate.take_profit
        reason = ExitReason.TP
    else:
        xp = exit_price if exit_price is not None else candidate.stop_loss
        reason = ExitReason.SL
    xt = exit_time if exit_time is not None else et + 60
    return TradeSimulationResult(
        instrument=candidate.instrument,
        direction=candidate.direction,
        signal_epoch=candidate.signal_epoch,
        outcome=outcome,
        filled=True,
        entry_time=et,
        entry_price=ep,
        exit_time=xt,
        exit_price=xp,
        exit_reason=reason,
        duration_seconds=duration if duration is not None else xt - et,
        candidate=candidate,
    )


def _metrics(sim: TradeSimulationResult, mae: float | None, mfe: float | None):
    from smb.research.models import TradeResearchMetrics

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
        mfe=mfe if mfe is not None else 0.0,
        mae=mae if mae is not None else 0.0,
        mfe_time=sim.entry_time,
        mae_time=sim.entry_time,
        observation_start=sim.entry_time,
        observation_end=sim.exit_time,
        simulation=sim,
    )


def _realized_r(sim: TradeSimulationResult) -> float | None:
    if not sim.filled or sim.entry_price is None or sim.exit_price is None:
        return None
    risk = sim.candidate.risk_distance
    if risk <= 0:
        return None
    if sim.candidate.direction == Direction.LONG:
        return (sim.exit_price - sim.entry_price) / risk
    return (sim.entry_price - sim.exit_price) / risk


def _row(
    outcome: SimulationOutcome,
    *,
    epoch: int = 1_700_000_000,
    direction: Direction = Direction.LONG,
    mae: float | None = 1.0,
    mfe: float | None = 2.0,
    duration: int | None = 100,
    risk_distance: float = 10.0,
    accepted: bool = True,
    m15_bias: str | None = "bullish",
) -> TradeExperimentRow:
    sig = _signal(epoch=epoch, direction=direction, m15_bias=m15_bias)
    if not accepted:
        return TradeExperimentRow(
            instrument=sig.instrument,
            signal_epoch=epoch,
            direction=str(direction),
            accepted=False,
            rejection_reason=None,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            risk_amount=None,
            outcome=None,
            entry_time=None,
            exit_time=None,
            duration_seconds=None,
            realized_r=None,
            mfe=None,
            mae=None,
            signal=sig,
            candidate=None,
            simulation=None,
            metrics=None,
        )
    cand = _candidate(sig, risk_distance=risk_distance)
    sim = _sim(cand, outcome, duration=duration)
    met = _metrics(sim, mae if outcome != SimulationOutcome.NO_FILL else None, mfe if outcome != SimulationOutcome.NO_FILL else None)
    return TradeExperimentRow(
        instrument=sig.instrument,
        signal_epoch=epoch,
        direction=str(direction),
        accepted=True,
        rejection_reason=None,
        entry_price=cand.entry_price,
        stop_loss=cand.stop_loss,
        take_profit=cand.take_profit,
        risk_reward=cand.risk_reward,
        risk_amount=cand.risk_amount,
        outcome=sim.outcome,
        entry_time=sim.entry_time,
        exit_time=sim.exit_time,
        duration_seconds=sim.duration_seconds,
        realized_r=_realized_r(sim),
        mfe=met.mfe,
        mae=met.mae,
        signal=sig,
        candidate=cand,
        simulation=sim,
        metrics=met,
    )


def _result(rows: list[TradeExperimentRow]) -> ExperimentResult:
    accepted = sum(1 for r in rows if r.accepted)
    rejected = sum(1 for r in rows if not r.accepted)
    outcomes = {
        SimulationOutcome.TP.value: 0,
        SimulationOutcome.SL.value: 0,
        SimulationOutcome.TIMEOUT.value: 0,
        SimulationOutcome.NO_FILL.value: 0,
    }
    for r in rows:
        if r.outcome is not None:
            outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
    sims = tuple(r.simulation for r in rows if r.simulation is not None)
    mets = tuple(r.metrics for r in rows if r.metrics is not None)
    summary = ExperimentSummary(
        instrument="vol",
        start_epoch=None,
        end_epoch=None,
        ticks_processed=1000,
        m1_candles=10,
        m15_candles=1,
        signals=len(rows),
        candidates_accepted=accepted,
        candidates_rejected=rejected,
        outcomes=outcomes,
        win_rate=None,
        average_r=None,
        total_r=None,
        average_duration_seconds=None,
        average_mae=None,
        average_mfe=None,
    )
    return ExperimentResult(
        config=ExperimentConfig(instrument="vol"),
        summary=summary,
        rows=tuple(rows),
        simulations=sims,
        metrics=mets,
        validation=None,
    )


def test_percentile_empty():
    assert percentile([], 50.0) is None


def test_percentile_singleton():
    assert percentile([7.0], 90.0) == 7.0


def test_percentile_type7_known():
    data = [1.0, 2.0, 3.0, 4.0]
    assert percentile(data, 0.0) == 1.0
    assert percentile(data, 100.0) == 4.0
    assert percentile(data, 50.0) == pytest.approx(2.5)
    assert percentile(data, 75.0) == pytest.approx(3.25)
    assert percentile(data, 90.0) == pytest.approx(3.7)


def test_percentile_rejects_non_finite():
    with pytest.raises(ValueError):
        percentile([1.0, float("nan")], 50.0)


def test_distribution_empty():
    d = distribution([])
    assert d.count == 0
    assert d.mean is None
    assert d.median is None


def test_distribution_values():
    d = distribution([1.0, 2.0, 3.0, 4.0])
    assert d.count == 4
    assert d.min == 1.0
    assert d.max == 4.0
    assert d.mean == pytest.approx(2.5)
    assert d.median == pytest.approx(2.5)
    assert d.p75 == pytest.approx(3.25)
    assert d.p90 == pytest.approx(3.7)


def test_direction_grouping_long_short():
    rows = [
        _row(SimulationOutcome.TP, epoch=100, direction=Direction.LONG, mae=1, mfe=5),
        _row(SimulationOutcome.SL, epoch=200, direction=Direction.LONG, mae=3, mfe=1),
        _row(SimulationOutcome.NO_FILL, epoch=300, direction=Direction.SHORT),
        _row(SimulationOutcome.TIMEOUT, epoch=400, direction=Direction.SHORT, mae=2, mfe=4),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert set(report.by_direction) == {"long", "short"}
    long = report.by_direction["long"]
    assert long.signals == 2
    assert long.tp == 1
    assert long.sl == 1
    assert long.filled == 2
    assert long.no_fill == 0
    assert long.fill_rate == pytest.approx(1.0)
    assert long.tp_rate_among_filled == pytest.approx(0.5)
    short = report.by_direction["short"]
    assert short.signals == 2
    assert short.no_fill == 1
    assert short.timeout == 1
    assert short.filled == 1
    assert short.fill_rate == pytest.approx(0.5)


def test_outcome_dual_percentages():
    rows = [
        _row(SimulationOutcome.TP, epoch=100),
        _row(SimulationOutcome.SL, epoch=200),
        _row(SimulationOutcome.TIMEOUT, epoch=300),
        _row(SimulationOutcome.NO_FILL, epoch=400),
        _row(SimulationOutcome.NO_FILL, epoch=500),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    by = {o.outcome: o for o in report.outcomes}
    assert by["tp"].count == 1
    assert by["tp"].pct_of_all_signals == pytest.approx(0.2)
    assert by["tp"].pct_of_filled == pytest.approx(1 / 3)
    assert by["no_fill"].count == 2
    assert by["no_fill"].pct_of_all_signals == pytest.approx(0.4)
    assert by["no_fill"].pct_of_filled is None


def test_no_fill_excluded_from_excursion_distributions():
    rows = [
        _row(SimulationOutcome.TP, epoch=100, mae=10.0, mfe=20.0),
        _row(SimulationOutcome.NO_FILL, epoch=200),
        _row(SimulationOutcome.SL, epoch=300, mae=5.0, mfe=1.0),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert report.mae_distribution.count == 2
    assert report.mfe_distribution.count == 2
    assert report.mae_distribution.mean == pytest.approx(7.5)


def test_realized_r_excludes_timeout_and_no_fill():
    rows = [
        _row(SimulationOutcome.TP, epoch=100),
        _row(SimulationOutcome.SL, epoch=200),
        _row(SimulationOutcome.TIMEOUT, epoch=300, mae=1, mfe=5),
        _row(SimulationOutcome.NO_FILL, epoch=400),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    long = report.by_direction["long"]
    assert long.total_r == pytest.approx(1.0)
    assert long.average_r == pytest.approx(0.5)


def test_excursions_by_outcome():
    rows = [
        _row(SimulationOutcome.TP, epoch=100, mae=1.0, mfe=8.0, duration=50),
        _row(SimulationOutcome.SL, epoch=200, mae=9.0, mfe=2.0, duration=30),
        _row(SimulationOutcome.TIMEOUT, epoch=300, mae=3.0, mfe=4.0, duration=900),
        _row(SimulationOutcome.NO_FILL, epoch=400),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    by = {e.outcome: e for e in report.excursions_by_outcome}
    assert by["tp"].count == 1
    assert by["tp"].mfe.mean == pytest.approx(8.0)
    assert by["sl"].mae.mean == pytest.approx(9.0)
    assert by["timeout"].duration.mean == pytest.approx(900.0)
    assert "no_fill" not in by


def test_timeout_mfe_r_thresholds():
    rows = [
        _row(SimulationOutcome.TIMEOUT, epoch=100, mfe=4.0, mae=1.0, risk_distance=10.0),
        _row(SimulationOutcome.TIMEOUT, epoch=200, mfe=6.0, mae=2.0, risk_distance=10.0),
        _row(SimulationOutcome.TIMEOUT, epoch=300, mfe=12.0, mae=3.0, risk_distance=10.0),
        _row(SimulationOutcome.TIMEOUT, epoch=400, mfe=20.0, mae=4.0, risk_distance=10.0),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    t = report.timeout
    assert t.count == 4
    assert t.r_threshold_sample_size == 4
    assert t.mfe_at_least_0_5r == 3
    assert t.mfe_at_least_1_0r == 2
    assert t.mfe_at_least_1_5r == 1
    assert t.mfe_at_least_2_0r == 1
    assert t.max_mfe == pytest.approx(20.0)
    assert t.median_mfe == pytest.approx(9.0)


def test_no_fill_direction_split():
    rows = [
        _row(SimulationOutcome.NO_FILL, epoch=100, direction=Direction.LONG),
        _row(SimulationOutcome.NO_FILL, epoch=200, direction=Direction.LONG),
        _row(SimulationOutcome.NO_FILL, epoch=300, direction=Direction.SHORT),
        _row(SimulationOutcome.TP, epoch=400, direction=Direction.SHORT),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    nf = report.no_fill
    assert nf.count == 3
    assert nf.pct_of_all_signals == pytest.approx(0.75)
    assert nf.long_count == 2
    assert nf.short_count == 1
    assert nf.long_pct_of_no_fill == pytest.approx(2 / 3)
    assert nf.average_risk_distance == pytest.approx(10.0)
    assert nf.average_entry_zone_width == pytest.approx(2.0)


def test_geometry_records_for_accepted():
    rows = [
        _row(SimulationOutcome.TP, epoch=100),
        _row(SimulationOutcome.NO_FILL, epoch=200, accepted=False),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert len(report.geometry) == 1
    g = report.geometry[0]
    assert g.direction == "long"
    assert g.entry_price is not None
    assert g.atr == pytest.approx(7.0)
    assert g.fvg_size == pytest.approx(2.0)
    assert g.m15_bias == "bullish"


def test_setup_groups_m15_bias():
    rows = [
        _row(SimulationOutcome.TP, epoch=100, m15_bias="bullish"),
        _row(SimulationOutcome.SL, epoch=200, m15_bias="bearish"),
        _row(SimulationOutcome.NO_FILL, epoch=300, m15_bias="bullish"),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    m15 = [g for g in report.setup_groups if g.attribute == "m15_bias"]
    by_val = {g.value: g for g in m15}
    assert by_val["bullish"].signals == 2
    assert by_val["bullish"].tp == 1
    assert by_val["bullish"].no_fill == 1
    assert by_val["bearish"].sl == 1


def test_time_buckets_hour_and_dow():
    epoch = 946_684_800
    rows = [
        _row(SimulationOutcome.TP, epoch=epoch),
        _row(SimulationOutcome.SL, epoch=epoch + 3600),
        _row(SimulationOutcome.NO_FILL, epoch=epoch + 7200),
    ]
    report = BaselineAnalysisCalculator(sparse_bucket_threshold=5).analyze(_result(rows))
    hours = {b.bucket: b for b in report.by_hour_utc}
    assert "00" in hours
    assert hours["00"].tp == 1
    assert hours["00"].sparse is True
    assert hours["01"].sl == 1
    assert any(b.bucket == "Saturday" for b in report.by_day_of_week_utc)


def test_empty_signals():
    report = BaselineAnalysisCalculator().analyze(_result([]))
    assert report.total_signals == 0
    assert report.total_filled == 0
    assert report.mae_distribution.count == 0
    assert report.timeout.count == 0
    assert report.no_fill.count == 0
    assert report.geometry == ()
    text = format_baseline_analysis(report)
    assert "total_signals:     0" in text


def test_only_no_fill():
    rows = [_row(SimulationOutcome.NO_FILL, epoch=i * 100) for i in range(3)]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert report.total_filled == 0
    assert report.mae_distribution.count == 0
    assert report.no_fill.count == 3
    by = {o.outcome: o for o in report.outcomes}
    assert by["no_fill"].pct_of_filled is None


def test_only_tp():
    rows = [_row(SimulationOutcome.TP, epoch=100 + i) for i in range(2)]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert report.total_filled == 2
    by = {o.outcome: o for o in report.outcomes}
    assert by["tp"].pct_of_filled == pytest.approx(1.0)
    long = report.by_direction["long"]
    assert long.tp_rate_among_filled == pytest.approx(1.0)
    assert long.total_r == pytest.approx(4.0)


def test_only_sl():
    rows = [_row(SimulationOutcome.SL, epoch=100)]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    long = report.by_direction["long"]
    assert long.sl_rate_among_filled == pytest.approx(1.0)
    assert long.total_r == pytest.approx(-1.0)


def test_only_timeout():
    rows = [_row(SimulationOutcome.TIMEOUT, epoch=100, mfe=15.0, mae=2.0)]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    assert report.timeout.count == 1
    long = report.by_direction["long"]
    assert long.total_r is None
    assert long.timeout_rate_among_filled == pytest.approx(1.0)


def test_serialization_roundtrip_keys():
    rows = [
        _row(SimulationOutcome.TP, epoch=100),
        _row(SimulationOutcome.TIMEOUT, epoch=200, mfe=5.0),
        _row(SimulationOutcome.NO_FILL, epoch=300, direction=Direction.SHORT),
    ]
    report = BaselineAnalysisCalculator().analyze(_result(rows))
    d = report.to_dict()
    assert d["instrument"] == "vol"
    assert "by_direction" in d
    assert "outcomes" in d
    assert "timeout" in d
    assert "no_fill" in d
    assert "geometry" in d
    assert "setup_groups" in d
    assert "by_hour_utc" in d
    assert "notes" in d
    import json

    json.dumps(d)


def test_format_baseline_includes_denominator_labels():
    rows = [_row(SimulationOutcome.TP, epoch=100), _row(SimulationOutcome.NO_FILL, epoch=200)]
    text = format_baseline_analysis(BaselineAnalysisCalculator().analyze(_result(rows)))
    assert "pct_of_all_signals" in text
    assert "pct_of_filled" in text
    assert "TP+SL+TIMEOUT" in text
    assert "Not realized P&L" in text or "not realized" in text.lower()


def test_cli_analysis_flags_in_help():
    from smb.research.__main__ import main

    with pytest.raises(SystemExit) as ei:
        main(["run", "--help"])
    assert ei.value.code == 0
