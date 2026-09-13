"""Milestone 5B — campaign-scale baseline analysis tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.research.campaign import CampaignConfig, CampaignRunner
from smb.research.campaign_baseline import (
    CampaignBaselineAnalyzer,
    _median,
    _segment_from_rows,
    compare_instruments,
    format_campaign_baseline_report,
    format_comparison_report,
    run_baseline_campaign,
    write_analysis_artifacts,
)
from smb.research.experiment import TradeExperimentRow
from smb.simulation.models import SimulationOutcome


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _write_ticks(store: ParquetTickStore, instrument: str, pairs: list[tuple[int, float]]) -> None:
    store.write_ticks([_st(instrument, e, p) for e, p in pairs])
    store.reindex_source_order(instrument)


def _flat_series(start: int, n_seconds: int, price: float = 100.0) -> list[tuple[int, float]]:
    return [(start + i, price) for i in range(n_seconds)]


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "data")


def test_median_helper():
    assert _median([]) is None
    assert _median([3.0]) == 3.0
    assert _median([1.0, 3.0]) == 2.0
    assert _median([1.0, 2.0, 3.0]) == 2.0


def test_segment_empty():
    seg = _segment_from_rows("empty", [])
    assert seg.signals == 0
    assert seg.filled == 0
    assert seg.average_r is None
    assert seg.profit_factor is None
    assert "small-sample" in seg.sample_note


def test_segment_realized_r_metrics():
    # Minimal stand-in: only fields used by _segment_from_rows
    from types import SimpleNamespace

    def _row(
        *,
        epoch: int,
        outcome: SimulationOutcome,
        realized_r: float,
        mae: float,
        mfe: float,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            instrument="vol",
            signal_epoch=epoch,
            direction="long",
            accepted=True,
            rejection_reason=None,
            outcome=outcome,
            realized_r=realized_r,
            mae=mae,
            mfe=mfe,
            duration_seconds=10,
            signal=None,
        )

    rows = [
        _row(epoch=10, outcome=SimulationOutcome.TP, realized_r=2.0, mae=0.2, mfe=2.1),
        _row(epoch=30, outcome=SimulationOutcome.SL, realized_r=-1.0, mae=1.0, mfe=0.3),
    ]
    seg = _segment_from_rows("long", rows)  # type: ignore[arg-type]
    assert seg.signals == 2
    assert seg.wins == 1
    assert seg.losses == 1
    assert seg.average_r == pytest.approx(0.5)
    assert seg.total_r == pytest.approx(1.0)
    assert seg.profit_factor == pytest.approx(2.0)
    assert seg.maximum_drawdown_r == pytest.approx(1.0)
    assert seg.median_mae == pytest.approx(0.6)


def test_one_instrument_baseline_pipeline(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 40 * 60, price=100.0)
    ticks = [(e, 100.0 + (i % 19) * 0.03) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "vol", ticks)
    out = tmp_path / "base_vol"
    results, analysis = run_baseline_campaign(
        store.root,
        instrument="vol",
        output_dir=out,
        campaign_id="base_vol",
    )
    assert results.summary.ticks_processed == len(ticks)
    assert analysis.instrument == "vol"
    assert analysis.campaign_id == "base_vol"
    assert (out / "analysis.json").is_file()
    assert (out / "analysis.md").is_file()
    assert (out / "manifest.json").is_file()
    assert analysis.interpretation in {
        "BASELINE PROMISING",
        "BASELINE MIXED",
        "BASELINE WEAK",
        "BASELINE INCONCLUSIVE",
    }
    payload = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert payload["overall"]["signals"] == analysis.overall.signals
    assert "by_direction" in payload
    assert "by_m15_bias" in payload


def test_analyzer_direction_keys(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 30 * 60, price=50.0)
    ticks = [(e, 50.0 + (i % 11) * 0.05) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "step", ticks)
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="step", output_dir=tmp_path / "d", campaign_id="d"),
    ).run()
    analysis = CampaignBaselineAnalyzer().analyze(results)
    # Direction map is well-formed (may be empty if zero signals)
    assert isinstance(analysis.by_direction, dict)
    for key, seg in analysis.by_direction.items():
        assert seg.label == key
        assert seg.signals >= 0


def test_compare_instruments_table(store: ParquetTickStore, tmp_path: Path):
    analyses = []
    for inst, n in (("vol", 20 * 60), ("step", 20 * 60)):
        ticks = _flat_series(1_700_000_000, n, price=80.0)
        _write_ticks(store, inst, ticks)
        _, analysis = run_baseline_campaign(
            store.root,
            instrument=inst,
            output_dir=tmp_path / inst,
            campaign_id=f"cmp_{inst}",
        )
        analyses.append(analysis)
    comparison = compare_instruments(analyses)
    assert comparison.instruments == ("vol", "step")
    text = format_comparison_report(comparison)
    assert "vol" in text and "step" in text
    assert "total_r" in text
    assert "interpretation" in text


def test_format_report_contains_sections(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 16 * 60, price=10.0)
    _write_ticks(store, "vol", ticks)
    _, analysis = run_baseline_campaign(
        store.root,
        instrument="vol",
        output_dir=tmp_path / "rep",
        campaign_id="rep",
    )
    text = format_campaign_baseline_report(analysis, include_baseline_detail=False)
    for section in (
        "Executive summary",
        "Long vs short",
        "M15 context",
        "MAE / MFE",
        "Rejections and no-fills",
        "Research interpretation",
        "Recommendation",
    ):
        assert section in text


def test_write_analysis_artifacts_idempotent(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 12 * 60, price=10.0)
    _write_ticks(store, "vol", ticks)
    results, analysis = run_baseline_campaign(
        store.root,
        instrument="vol",
        output_dir=tmp_path / "idemp",
        campaign_id="idemp",
    )
    paths = write_analysis_artifacts(results, analysis)
    assert paths["analysis_json"].is_file()
    # Second write does not raise
    write_analysis_artifacts(results, analysis)


def test_deterministic_analysis_for_same_campaign(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 25 * 60, price=40.0)
    ticks = [(e, 40.0 + (i % 13) * 0.02) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "vol", ticks)
    a1 = run_baseline_campaign(
        store.root, instrument="vol", output_dir=tmp_path / "a1", campaign_id="det"
    )[1]
    a2 = run_baseline_campaign(
        store.root, instrument="vol", output_dir=tmp_path / "a2", campaign_id="det"
    )[1]
    assert a1.overall.signals == a2.overall.signals
    assert a1.overall.total_r == a2.overall.total_r
    assert a1.overall.average_r == a2.overall.average_r
    assert a1.interpretation == a2.interpretation
