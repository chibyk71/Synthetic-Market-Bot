"""Markdown + JSON report writers for the strategy tournament."""

from __future__ import annotations

import json
from pathlib import Path

from smb.research.tournament.models import TournamentResult


def write_json(result: TournamentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["rows"] = [
        {
            "strategy_id": r.strategy_id,
            "target_rr": r.target_rr,
            "instrument": r.instrument,
            "signal_epoch": r.signal_epoch,
            "direction": r.direction,
            "accepted": r.accepted,
            "filled": r.filled,
            "outcome": r.outcome.value if r.outcome else None,
            "entry_price": r.entry_price,
            "stop_loss": r.stop_loss,
            "take_profit": r.take_profit,
            "entry_time": r.entry_time,
            "exit_time": r.exit_time,
            "duration_seconds": r.duration_seconds,
            "realized_r": r.realized_r,
            "gross_r": r.gross_r,
            "cost_r": r.cost_r,
            "net_r": r.net_r,
            "mfe": r.mfe,
            "mae": r.mae,
            "signal_metadata": dict(r.signal_metadata),
        }
        for r in result.rows
    ]
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def write_markdown(result: TournamentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Milestone 6E — Short-Horizon Strategy Tournament Report")
    lines.append("")
    lines.append(f"**Study version:** `{result.study_version}`")
    lines.append("")
    lines.append("## 1. Dataset / cohort description")
    lines.append("")
    for k, v in result.dataset.items():
        lines.append(f"- **{k}:** `{v}`")
    lines.append("")
    lines.append("## 2. Frozen configuration")
    lines.append("")
    cfg = result.config
    lines.append(f"- Instrument: `{cfg.instrument}`")
    lines.append(f"- Target R multiples (frozen): `{list(cfg.target_r_multiples)}`")
    lines.append(f"- Max duration seconds (frozen): `{cfg.max_duration_seconds}`")
    lines.append(f"- Equity: `{cfg.equity}`")
    lines.append(f"- Risk per trade: `{cfg.risk_per_trade}`")
    lines.append(f"- Minimum RR gate: `{cfg.minimum_rr}`")
    lines.append(
        f"- Spread cost R (6C canonical): `{cfg.spread_cost_r}` "
        "(subtracted from gross R for net R on filled trades)"
    )
    lines.append("")
    lines.append("## 3. Strategy definitions")
    lines.append("")
    for sid, defn in result.strategy_definitions.items():
        lines.append(f"### `{sid}`")
        lines.append("")
        hyp = defn.get("hypothesis", "")
        lines.append(f"**Hypothesis:** {hyp}")
        lines.append("")
        lines.append("Frozen parameters:")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(defn.get("frozen_params", {}), indent=2))
        lines.append("```")
        lines.append("")
    lines.append("## 4. Results by strategy × target (gross R)")
    lines.append("")
    lines.append(
        "| strategy | target_rr | signals | accepted | fills | TP | SL | TIMEOUT | "
        "win_rate | total_gross_R | avg_gross_R | median_gross_R | PF_gross |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )

    def fmt(x: float | None) -> str:
        if x is None:
            return "—"
        if x == float("inf"):
            return "inf"
        return f"{x:.4f}"

    for s in result.summaries:
        lines.append(
            f"| `{s.strategy_id}` | {s.target_rr:.2f} | {s.signals} | {s.accepted} | "
            f"{s.fills} | {s.tp} | {s.sl} | {s.timeout} | {fmt(s.win_rate)} | "
            f"{fmt(s.total_gross_r)} | {fmt(s.average_gross_r)} | {fmt(s.median_r)} | "
            f"{fmt(s.profit_factor)} |"
        )
    lines.append("")
    lines.append("## 4b. Cost-adjusted results (net R = gross R − spread_cost_r)")
    lines.append("")
    lines.append(
        "| strategy | target_rr | fills | cost_R | total_net_R | avg_net_R | "
        "median_net_R | PF_net |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for s in result.summaries:
        lines.append(
            f"| `{s.strategy_id}` | {s.target_rr:.2f} | {s.fills} | "
            f"{fmt(s.spread_cost_r)} | {fmt(s.total_net_r)} | "
            f"{fmt(s.average_net_r)} | {fmt(s.median_net_r)} | "
            f"{fmt(s.profit_factor_net)} |"
        )
    lines.append("")
    lines.append("## 5. Results by target (cross-strategy)")
    lines.append("")
    targets = sorted({s.target_rr for s in result.summaries})
    for tr in targets:
        lines.append(f"### Target {tr:.2f}R")
        lines.append("")
        cell = [s for s in result.summaries if s.target_rr == tr]
        for s in cell:
            lines.append(
                f"- `{s.strategy_id}`: fills={s.fills}, TP={s.tp}, SL={s.sl}, "
                f"TIMEOUT={s.timeout}, avg_gross_R={s.average_gross_r}, "
                f"avg_net_R={s.average_net_r}"
            )
        lines.append("")
    lines.append("## 6. MAE / MFE comparison")
    lines.append("")
    lines.append("| strategy | target_rr | mean_MAE | median_MAE | mean_MFE | median_MFE |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for s in result.summaries:
        lines.append(
            f"| `{s.strategy_id}` | {s.target_rr:.2f} | {fmt(s.mean_mae)} | "
            f"{fmt(s.median_mae)} | {fmt(s.mean_mfe)} | {fmt(s.median_mfe)} |"
        )
    lines.append("")
    lines.append("## 7. Duration statistics")
    lines.append("")
    lines.append("| strategy | target_rr | mean_duration_s | median_duration_s |")
    lines.append("|---|---:|---:|---:|")
    for s in result.summaries:
        lines.append(
            f"| `{s.strategy_id}` | {s.target_rr:.2f} | "
            f"{fmt(s.mean_duration_seconds)} | {fmt(s.median_duration_seconds)} |"
        )
    lines.append("")
    lines.append("## 8. Cost treatment")
    lines.append("")
    lines.append(
        "Transaction cost uses the Milestone 6C canonical spread cost model "
        f"(`spread_cost_r = {cfg.spread_cost_r}` R units)."
    )
    lines.append("")
    lines.append("- **gross_R**: structural realized R from SimulationEngine outcomes.")
    lines.append("- **cost_R**: canonical spread cost applied to filled trades only.")
    lines.append("- **net_R**: `gross_R − cost_R` for filled trades.")
    lines.append(
        "- SimulationEngine historical semantics are unchanged; cost is applied "
        "in the tournament aggregation layer only."
    )
    lines.append("")
    lines.append("## 9. Sanity checks")
    lines.append("")
    lines.append("- Identical historical cohort across strategies.")
    lines.append("- Shared SimulationEngine and ResearchMetricsCalculator.")
    lines.append("- No lookahead: signals use only candles at or before signal_epoch.")
    lines.append(
        "- Post-entry tick windows via indexed binary search (O(log N)); "
        "one window per signal reused across all targets."
    )
    lines.append("- No automatic strategy selection or ranking score.")
    lines.append("- Targets and horizon are frozen (0.30/0.40/0.50 R, 300 s).")
    lines.append("")
    lines.append("## 10. Limitations")
    lines.append("")
    lines.append("- Single instrument (`volatility_75_1s`) in this milestone.")
    lines.append("- Frozen parameters only; no optimization.")
    lines.append("- Small sample risk for some strategies.")
    lines.append(
        "- Cost model is the explicit 6C canonical spread cost in R; "
        "no additional slippage assumptions beyond the research framework."
    )
    lines.append("- ICT control may emit few signals on short cohorts.")
    lines.append("")
    lines.append("## 11. No automatic strategy selection")
    lines.append("")
    lines.append(
        "This report is a **factual comparison dataset**. Do not treat any "
        "row as a production recommendation. Second-stage investigation is a "
        "human decision after review."
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
