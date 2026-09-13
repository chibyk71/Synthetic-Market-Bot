"""Milestone 5A — Historical research campaign runner tests."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.research.campaign import (
    CampaignConfig,
    CampaignError,
    CampaignRunner,
    _default_campaign_id,
    _maximum_drawdown_r,
    _profit_factor,
    format_campaign_report,
    run_campaign,
)
from smb.research.__main__ import main as research_main


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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_campaign_config_valid(tmp_path: Path):
    cfg = CampaignConfig(instrument="vol", output_dir=tmp_path / "out")
    assert cfg.instrument == "vol"
    assert cfg.risk_equity == 10_000.0


def test_campaign_config_invalid_range(tmp_path: Path):
    with pytest.raises(ValueError, match="start_epoch must be < end_epoch"):
        CampaignConfig(
            instrument="vol",
            output_dir=tmp_path / "out",
            start_epoch=100,
            end_epoch=100,
        )


def test_campaign_config_empty_instrument(tmp_path: Path):
    with pytest.raises(ValueError, match="instrument"):
        CampaignConfig(instrument="", output_dir=tmp_path / "out")


def test_campaign_config_missing_output():
    with pytest.raises(ValueError, match="output_dir"):
        CampaignConfig(instrument="vol", output_dir="")


def test_campaign_config_bad_equity(tmp_path: Path):
    with pytest.raises(ValueError, match="risk_equity"):
        CampaignConfig(instrument="vol", output_dir=tmp_path / "out", risk_equity=0.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_maximum_drawdown_r():
    assert _maximum_drawdown_r([]) is None
    assert _maximum_drawdown_r([1.0, 1.0, 1.0]) == 0.0
    # +2, -3 → equity 2 then -1; peak 2; dd = 3
    assert _maximum_drawdown_r([2.0, -3.0]) == pytest.approx(3.0)
    assert _maximum_drawdown_r([1.0, -0.5, 0.25]) == pytest.approx(0.5)


def test_profit_factor():
    assert _profit_factor([]) is None
    assert _profit_factor([1.0, 2.0]) == float("inf")
    assert _profit_factor([-1.0]) == 0.0
    assert _profit_factor([2.0, -1.0]) == pytest.approx(2.0)


def test_deterministic_campaign_id(tmp_path: Path):
    """Same research config → same campaign_id (identity, not run metadata)."""
    a = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "a",
        start_epoch=100,
        end_epoch=200,
    )
    b = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "b",  # output_dir is not part of identity
        start_epoch=100,
        end_epoch=200,
    )
    c = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "c",
        start_epoch=100,
        end_epoch=201,  # different range
    )
    assert _default_campaign_id(a) == _default_campaign_id(b)
    assert _default_campaign_id(a) != _default_campaign_id(c)
    assert _default_campaign_id(a).startswith("vol_")
    assert len(_default_campaign_id(a).split("_")[-1]) == 12


# ---------------------------------------------------------------------------
# Error classification vs legitimate empty
# ---------------------------------------------------------------------------


def test_missing_instrument_fails(store: ParquetTickStore, tmp_path: Path):
    """Missing instrument is a genuine error, not an empty campaign."""
    with pytest.raises(CampaignError, match="not found"):
        CampaignRunner(
            TickRepository(store),
            CampaignConfig(
                instrument="DOES_NOT_EXIST",
                output_dir=tmp_path / "fail",
                campaign_id="should_fail",
            ),
        ).run()


def test_no_ticks_in_range_fails(store: ParquetTickStore, tmp_path: Path):
    """Instrument exists but requested range has zero ticks → CampaignError."""
    _write_ticks(store, "vol", _flat_series(1_700_000_000, 60, price=100.0))
    with pytest.raises(CampaignError, match="no ticks"):
        CampaignRunner(
            TickRepository(store),
            CampaignConfig(
                instrument="vol",
                output_dir=tmp_path / "norange",
                start_epoch=1_800_000_000,
                end_epoch=1_800_000_100,
                campaign_id="norange",
            ),
        ).run()


def test_zero_signal_campaign_is_legitimate_empty(store: ParquetTickStore, tmp_path: Path):
    """Valid ticks that produce no strategy signals → successful empty campaign."""
    # Short flat series: ticks exist, candles may form, but signals can be zero.
    ticks = _flat_series(1_700_000_000, 5 * 60, price=100.0)
    _write_ticks(store, "vol", ticks)
    out = tmp_path / "zero_sig"
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="vol", output_dir=out, campaign_id="zero_sig"),
    ).run()
    assert results.summary.ticks_processed == len(ticks)
    assert results.summary.signals == 0
    assert results.summary.empty is True
    assert results.manifest_path.is_file()
    assert results.summary_path.is_file()
    assert results.report_path.is_file()
    assert results.trades_path is not None and results.trades_path.is_file()
    manifest = json.loads(results.manifest_path.read_text(encoding="utf-8"))
    assert manifest["campaign_id"] == "zero_sig"
    assert "diagnostics_status" in manifest
    assert "empty_reason" not in manifest


def test_normal_dataset_pipeline(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 40 * 60, price=100.0)
    ticks = [(e, 100.0 + (i % 23) * 0.02) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "vol", ticks)
    out = tmp_path / "camp_normal"
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="vol", output_dir=out, campaign_id="normal"),
    ).run()
    assert results.summary.ticks_processed == len(ticks)
    assert results.summary.m1_candles >= 30
    assert results.summary.signals >= 0
    assert (
        results.summary.candidates_accepted + results.summary.candidates_rejected
        == results.summary.signals
    )
    assert results.experiment is not None
    assert results.trades_path is not None and results.trades_path.is_file()
    assert results.diagnostics_status in ("complete", "unavailable")
    if results.diagnostics_status == "complete":
        assert results.diagnostics_path is not None and results.diagnostics_path.is_file()
    table = pq.read_table(results.trades_path)
    assert table.num_rows == len(results.experiment.rows)


def test_date_filtering(store: ParquetTickStore, tmp_path: Path):
    start = 1_700_000_000
    ticks = _flat_series(start, 30 * 60, price=50.0)
    _write_ticks(store, "vol", ticks)
    # Restrict to first 10 minutes
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(
            instrument="vol",
            output_dir=tmp_path / "filt",
            start_epoch=start,
            end_epoch=start + 600,
            campaign_id="filt",
        ),
    ).run()
    assert results.summary.ticks_processed == 600
    assert results.summary.start_epoch == start
    assert results.summary.end_epoch == start + 600


def test_chronological_processing_and_determinism(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 25 * 60, price=50.0)
    ticks = [(e, 50.0 + (i % 17) * 0.01) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "vol", ticks)
    repo = TickRepository(store)
    cfg_a = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "a",
        campaign_id="det_a",
    )
    cfg_b = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "b",
        campaign_id="det_b",
    )
    a = CampaignRunner(repo, cfg_a).run()
    b = CampaignRunner(repo, cfg_b).run()
    assert a.summary.ticks_processed == b.summary.ticks_processed
    assert a.summary.signals == b.summary.signals
    assert a.summary.candidates_accepted == b.summary.candidates_accepted
    assert a.summary.outcomes == b.summary.outcomes
    assert a.summary.average_r == b.summary.average_r
    assert a.summary.cumulative_r == b.summary.cumulative_r
    assert a.summary.maximum_drawdown_r == b.summary.maximum_drawdown_r
    if a.experiment and b.experiment:
        assert [r.signal_epoch for r in a.experiment.rows] == [
            r.signal_epoch for r in b.experiment.rows
        ]
        assert [r.outcome for r in a.experiment.rows] == [
            r.outcome for r in b.experiment.rows
        ]


def test_default_campaign_id_stable_across_runs(store: ParquetTickStore, tmp_path: Path):
    """Without explicit campaign_id, identity is deterministic from config."""
    ticks = _flat_series(1_700_000_000, 10 * 60, price=40.0)
    _write_ticks(store, "vol", ticks)
    repo = TickRepository(store)
    cfg1 = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "id1",
        start_epoch=1_700_000_000,
        end_epoch=1_700_000_000 + 600,
    )
    cfg2 = CampaignConfig(
        instrument="vol",
        output_dir=tmp_path / "id2",
        start_epoch=1_700_000_000,
        end_epoch=1_700_000_000 + 600,
    )
    a = CampaignRunner(repo, cfg1).run()
    b = CampaignRunner(repo, cfg2).run()
    assert a.campaign_id == b.campaign_id
    assert a.campaign_id.startswith("vol_")


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def test_manifest_and_summary_persist(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 16 * 60, price=10.0)
    _write_ticks(store, "step", ticks)
    out = tmp_path / "arts"
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="step", output_dir=out, campaign_id="arts_1"),
    ).run()
    manifest = json.loads(results.manifest_path.read_text(encoding="utf-8"))
    assert manifest["campaign_id"] == "arts_1"
    assert manifest["instrument"] == "step"
    assert "configuration" in manifest
    assert "strategy" in manifest["configuration"]
    assert "trade" in manifest["configuration"]
    assert "simulation" in manifest["configuration"]
    assert "git_commit" in manifest
    assert "diagnostics_status" in manifest
    assert manifest["diagnostics_status"] in ("complete", "unavailable")
    # created_at_utc is run metadata only
    assert "created_at_utc" in manifest

    summary = json.loads(results.summary_path.read_text(encoding="utf-8"))
    assert summary["campaign_id"] == "arts_1"
    assert summary["ticks_processed"] == len(ticks)
    assert "maximum_drawdown_r" in summary

    report = results.report_path.read_text(encoding="utf-8")
    assert "Campaign report" in report
    assert "cumulative R" in report


def test_per_trade_parquet_schema(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 20 * 60, price=80.0)
    _write_ticks(store, "vol", ticks)
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="vol", output_dir=tmp_path / "pq", campaign_id="pq"),
    ).run()
    assert results.trades_path is not None
    table = pq.read_table(results.trades_path)
    names = set(table.schema.names)
    for col in (
        "instrument",
        "signal_epoch",
        "direction",
        "accepted",
        "rejection_reason",
        "entry_price",
        "stop_loss",
        "take_profit",
        "outcome",
        "realized_r",
        "mfe",
        "mae",
        "duration_seconds",
    ):
        assert col in names


def test_run_campaign_convenience(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 16 * 60, price=10.0)
    _write_ticks(store, "step", ticks)
    results = run_campaign(
        store.root,
        instrument="step",
        output_dir=tmp_path / "conv",
        campaign_id="conv",
    )
    assert results.summary.ticks_processed == len(ticks)
    assert results.summary.instrument == "step"


def test_format_campaign_report_readable(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 5 * 60, price=1.0)
    _write_ticks(store, "vol", ticks)
    results = CampaignRunner(
        TickRepository(store),
        CampaignConfig(instrument="vol", output_dir=tmp_path / "rep", campaign_id="rep"),
    ).run()
    text = format_campaign_report(results)
    assert "wins (TP)" in text
    assert "maximum drawdown" in text.lower() or "drawdown" in text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_run_campaign(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 16 * 60, price=10.0)
    _write_ticks(store, "vol", ticks)
    out = tmp_path / "cli_out"
    code = research_main(
        [
            "run-campaign",
            "--instrument",
            "vol",
            "--output",
            str(out),
            "--data-root",
            str(store.root),
            "--campaign-id",
            "cli_test",
        ]
    )
    assert code == 0
    assert (out / "manifest.json").is_file()
    assert (out / "summary.json").is_file()
    assert (out / "report.md").is_file()


def test_cli_run_campaign_missing_instrument(tmp_path: Path):
    with pytest.raises(SystemExit):
        research_main(
            [
                "run-campaign",
                "--output",
                str(tmp_path / "x"),
            ]
        )


def test_cli_missing_data_instrument_fails(store: ParquetTickStore, tmp_path: Path):
    """CLI surfaces CampaignError for missing instrument (exit 2)."""
    code = research_main(
        [
            "run-campaign",
            "--instrument",
            "DOES_NOT_EXIST",
            "--output",
            str(tmp_path / "cli_fail"),
            "--data-root",
            str(store.root),
        ]
    )
    assert code == 2
