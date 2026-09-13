"""Milestone 5D — expanded historical baseline comparison tests."""

from __future__ import annotations

import json
from pathlib import Path

from smb.research.expanded_baseline import (
    BASELINE_5B_REFERENCE,
    EXPANDED_5C_METRICS,
    MILESTONE_5D_ID,
    SIMULATION_CONFIG_IDENTITY,
    STRATEGY_CONFIG_IDENTITY,
    TRADE_CONFIG_IDENTITY,
    build_documented_5d_report,
    build_expanded_baseline_report,
    compare_instrument,
    format_expanded_baseline_report,
    write_expanded_baseline_artifacts,
)


def test_5b_reference_instruments():
    assert "volatility_75_1s" in BASELINE_5B_REFERENCE
    assert "step_index" in BASELINE_5B_REFERENCE
    assert BASELINE_5B_REFERENCE["volatility_75_1s"]["signals"] == 7
    assert BASELINE_5B_REFERENCE["step_index"]["signals"] == 8


def test_compare_instrument_preserves_strategy_flag():
    metrics = dict(EXPANDED_5C_METRICS["volatility_75_1s"])
    comp = compare_instrument("volatility_75_1s", metrics)
    assert comp.strategy_unchanged is True
    assert comp.instrument == "volatility_75_1s"
    sig = next(d for d in comp.deltas if d.metric == "signals")
    assert sig.baseline_5b == 7
    assert sig.expanded_5d == 10
    assert sig.change == 3


def test_documented_5d_report_identity():
    report = build_documented_5d_report()
    assert report.milestone == MILESTONE_5D_ID
    assert report.strategy_unchanged is True
    assert report.strategy_config_identity == STRATEGY_CONFIG_IDENTITY
    assert report.trade_config_identity == TRADE_CONFIG_IDENTITY
    assert report.simulation_config_identity == SIMULATION_CONFIG_IDENTITY
    assert set(report.instruments) == {"volatility_75_1s", "step_index"}
    assert len(report.comparisons) == 2


def test_signal_count_increases_but_remains_small():
    report = build_documented_5d_report()
    by_inst = {c.instrument: c for c in report.comparisons}
    v75 = by_inst["volatility_75_1s"]
    step = by_inst["step_index"]
    assert v75.expanded_5d["signals"] > v75.baseline_5b["signals"]
    assert step.expanded_5d["signals"] >= step.baseline_5b["signals"]
    assert v75.expanded_5d["signals"] < 80
    assert step.expanded_5d["signals"] < 80


def test_total_r_persists_negative_or_flat():
    report = build_documented_5d_report()
    for comp in report.comparisons:
        total = comp.expanded_5d.get("total_r")
        assert total is not None
        assert total <= 0


def test_format_report_states_strategy_unchanged():
    report = build_documented_5d_report()
    text = format_expanded_baseline_report(report)
    assert "Strategy changes between 5B and 5D: none" in text
    assert "volatility_75_1s" in text
    assert "| Metric | 5B | 5D | Change |" in text


def test_write_artifacts(tmp_path: Path):
    report = build_documented_5d_report()
    paths = write_expanded_baseline_artifacts(report, tmp_path / "milestone-5d")
    assert paths["comparison_json"].is_file()
    assert paths["report_md"].is_file()
    data = json.loads(paths["comparison_json"].read_text(encoding="utf-8"))
    assert data["milestone"] == MILESTONE_5D_ID
    assert data["strategy_unchanged"] is True


def test_build_from_custom_metrics():
    custom = {
        "volatility_75_1s": {
            **EXPANDED_5C_METRICS["volatility_75_1s"],
            "signals": 12,
            "total_r": -3.0,
        }
    }
    report = build_expanded_baseline_report(expanded_metrics_by_instrument=custom)
    assert report.instruments == ("volatility_75_1s",)
    assert report.comparisons[0].expanded_5d["signals"] == 12


def test_serialization_roundtrip_keys():
    report = build_documented_5d_report()
    d = report.to_dict()
    assert set(d.keys()) >= {
        "milestone",
        "strategy_unchanged",
        "strategy_config_identity",
        "trade_config_identity",
        "simulation_config_identity",
        "instruments",
        "comparisons",
        "research_notes",
        "recommendation",
    }
    for c in d["comparisons"]:
        assert c["strategy_unchanged"] is True
