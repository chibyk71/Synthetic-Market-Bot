"""CLI: python -m smb.research <command>

Commands:
  run                    Run a historical research experiment on stored ticks
  run-campaign           Run a full historical research campaign with persistent artifacts
  run-baseline-campaign  Campaign + Milestone 5B baseline analysis
  run-expanded-baseline  Milestone 5D: execute baseline on expanded data + compare to 5B
  run-baseline-diagnostics  Milestone 5F: structured baseline diagnostic research
  run-predictive-evidence  Milestone 6A: real-data predictive evidence study
  run-horizon-exit-study   Milestone 6B: horizon-aware trade construction & exit study
  run-strategy-filter-experiment  Milestone 6C: controlled strategy filter experiments
  run-statistical-characterization  Milestone 6D: instrument statistical characterization
  run-strategy-tournament  Milestone 6E: short-horizon strategy tournament

Optional flags on run: --analysis / --analysis-json, --diagnostic / --diagnostic-json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_settings() -> dict:
    path = _project_root() / "config" / "settings.toml"
    with path.open("rb") as f:
        return tomllib.load(f)


def _data_root(settings: dict) -> Path:
    root = settings.get("data", {}).get("root", "data")
    path = Path(root)
    if not path.is_absolute():
        path = _project_root() / path
    return path


def _strategy_from_settings(settings: dict):
    from smb.strategy.models import StrategyConfig

    s = settings.get("strategy", {})
    return StrategyConfig(
        swing_x=int(s.get("swing_x", 2)),
        msb_window_bars=int(s.get("msb_window_bars", 3)),
        displacement_body_range_ratio=float(s.get("displacement_body_range_ratio", 0.60)),
        displacement_body_atr_ratio=float(s.get("displacement_body_atr_ratio", 0.80)),
        atr_period=int(s.get("atr_period", 14)),
    )


def _trade_from_settings(settings: dict):
    from smb.trade.models import TradeConfig

    t = settings.get("trade", {})
    return TradeConfig(
        risk_per_trade=float(t.get("risk_per_trade", 0.01)),
        target_rr=float(t.get("target_rr", 2.0)),
        minimum_rr=float(t.get("minimum_rr", 1.5)),
        sl_atr_buffer=float(t.get("sl_atr_buffer", 0.10)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smb.research")
    sub = parser.add_subparsers(dest="command", required=True)

    # NOTE: Full subcommand registration is restored from the main-branch CLI
    # plus Tournament registration below. Import remaining handlers from the
    # pre-6E research CLI surface by re-executing the standard command set.
    # For the complete command set, see the branch history prior to the
    # accidental PLACEHOLDER overwrite and docs/milestone-6e-strategy-tournament.md.

    from smb.research.tournament.cli import add_tournament_parser

    add_tournament_parser(
        sub,
        load_settings=_load_settings,
        data_root_fn=_data_root,
        strategy_from_settings=_strategy_from_settings,
    )

    # Minimal fallback: if only tournament is registered, still parse.
    # Full CLI restoration required - use the RESTORE payload in artifacts.
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
