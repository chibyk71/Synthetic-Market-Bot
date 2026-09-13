"""Milestone 5D — expanded historical baseline tests.

Prove 5D metrics come from CampaignBaselineAnalysis (campaign execution),
not hard-coded prior results.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.research.campaign_baseline import run_baseline_campaign
from smb.research.expanded_baseline import (
    BASELINE_5B_REFERENCE,
    MILESTONE_5D_ID,
    SIMULATION_CONFIG_IDENTITY,
    STRATEGY_CONFIG_IDENTITY,
    TRADE_CONFIG_IDENTITY,
    analysis_to_5d_metrics,
    build_expanded_baseline_report,
    format_expanded_baseline_report,
    run_expanded_baseline,
    write_expanded_baseline_artifacts,
)


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _write_ticks(store: ParquetTickStore, instrument: str, pairs: list[tuple[int, float]]) -> None:
    store.write_ticks([_st(instrument, e, p) for e, p in pairs])
    store.reindex_source_order(instrument)


def _series(start: int, n: int, base: float = 100.0) -> list[tuple[int, float]]:
    return [(start + i, base + (i % 19) * 0.03) for i in range(n)]


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "data")


def test_5b_reference_is_comparison_only():
    assert BASELINE_5B_REFERENCE["volatility_75_1s"]["signals"] == 7
    assert BASELINE_5B_REFERENCE["step_index"]["signals"] == 8


def test_analysis_to_5d_metrics_from_live_campaign(store: ParquetTickStore, tmp_path: Path):
    ticks = _series(1_700_000_000, 40 * 60)
    _write_ticks(store, "vol", ticks)
    _results, analysis = run_baseline_campaign(
        store.root,
        instrument="vol",
        output_dir=tmp_path / "camp",
        campaign_id="live_vol",
    )
    metrics = analysis_to_5d_metrics(analysis)
    assert metrics["campaign_id"] == "live_vol"
    assert metrics["ticks"] == analysis.ticks_processed
    assert metrics["signals"] == analysis.overall.signals
    assert metrics["accepted"] == analysis.overall.accepted
    assert metrics["filled"] == analysis.overall.filled
    assert metrics["total_r"] == analysis.overall.total_r
    assert metrics["average_r"] == analysis.overall.average_r
    assert metrics["average_mae"] == analysis.overall.average_mae
    assert metrics["average_mfe"] == analysis.overall.average_mfe
    assert metrics["interpretation"] == analysis.interpretation


def test_report_metrics_match_supplied_analysis(store: ParquetTickStore, tmp_path: Path):
    ticks = _series(1_700_000_000, 50 * 60)
    _write_ticks(store, "vol_a", ticks)
    _write_ticks(store, "vol_b", ticks[: 30 * 60])

    _, a1 = run_baseline_campaign(
        store.root, instrument="vol_a", output_dir=tmp_path / "a", campaign_id="a"
    )
    _, a2 = run_baseline_campaign(
        store.root, instrument="vol_b", output_dir=tmp_path / "b", campaign_id="b"
    )

    report = build_expanded_baseline_report([a1, a2])
    assert report.source == "campaign_execution"
    assert report.strategy_unchanged is True
    assert report.milestone == MILESTONE_5D_ID
    assert report.instruments == ("vol_a", "vol_b")

    by_inst = {c.instrument: c for c in report.comparisons}
    assert by_inst["vol_a"].expanded_5d["signals"] == a1.overall.signals
    assert by_inst["vol_a"].expanded_5d["ticks"] == a1.ticks_processed
    assert by_inst["vol_b"].expanded_5d["signals"] == a2.overall.signals
    assert by_inst["vol_b"].expanded_5d["ticks"] == a2.ticks_processed
    assert a1.ticks_processed != a2.ticks_processed
    assert by_inst["vol_a"].expanded_5d["ticks"] != by_inst["vol_b"].expanded_5d["ticks"]


def test_run_expanded_baseline_executes_pipeline(store: ParquetTickStore, tmp_path: Path):
    ticks = _series(1_700_000_000, 45 * 60)
    _write_ticks(store, "inst_x", ticks)
    _write_ticks(store, "inst_y", ticks)

    out = tmp_path / "milestone-5d"
    report, analyses = run_expanded_baseline(
        store.root,
        instruments=["inst_x", "inst_y"],
        output_dir=out,
    )
    assert len(analyses) == 2
    assert report.source == "campaign_execution"
    assert (out / "comparison.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "inst_x" / "analysis.json").is_file()
    assert (out / "inst_y" / "manifest.json").is_file()

    payload = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    assert payload["source"] == "campaign_execution"
    assert payload["strategy_unchanged"] is True
    for analysis, comp in zip(analyses, payload["comparisons"], strict=True):
        assert comp["instrument"] == analysis.instrument
        assert comp["expanded_5d"]["signals"] == analysis.overall.signals
        assert comp["expanded_5d"]["ticks"] == analysis.ticks_processed


def test_build_report_rejects_empty_analyses():
    with pytest.raises(ValueError, match="non-empty"):
        build_expanded_baseline_report([])


def test_config_identity_unchanged():
    assert STRATEGY_CONFIG_IDENTITY["atr_period"] == 14
    assert TRADE_CONFIG_IDENTITY["target_rr"] == 2.0
    assert SIMULATION_CONFIG_IDENTITY["max_duration_seconds"] == 900


def test_format_report_states_campaign_source(store: ParquetTickStore, tmp_path: Path):
    ticks = _series(1_700_000_000, 30 * 60)
    _write_ticks(store, "vol", ticks)
    _, analysis = run_baseline_campaign(
        store.root, instrument="vol", output_dir=tmp_path / "c", campaign_id="c"
    )
    report = build_expanded_baseline_report([analysis])
    text = format_expanded_baseline_report(report)
    assert "Strategy changes between 5B and 5D: none" in text
    assert "campaign_execution" in text


def test_write_artifacts_roundtrip(store: ParquetTickStore, tmp_path: Path):
    ticks = _series(1_700_000_000, 30 * 60)
    _write_ticks(store, "vol", ticks)
    _, analysis = run_baseline_campaign(
        store.root, instrument="vol", output_dir=tmp_path / "c", campaign_id="c"
    )
    report = build_expanded_baseline_report([analysis])
    paths = write_expanded_baseline_artifacts(report, tmp_path / "out")
    data = json.loads(paths["comparison_json"].read_text(encoding="utf-8"))
    assert data["source"] == "campaign_execution"
    assert data["comparisons"][0]["expanded_5d"]["signals"] == analysis.overall.signals
