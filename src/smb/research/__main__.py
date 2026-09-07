"""CLI: python -m smb.research <command>

Commands:
  run    Run a historical research experiment on stored ticks
"""

from __future__ import annotations

import argparse
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


def cmd_run(args: argparse.Namespace) -> int:
    from smb.research.experiment import (
        ExperimentError,
        format_summary,
        run_experiment,
    )
    from smb.simulation.models import SimulationConfig

    settings = _load_settings()
    data_root = Path(args.data_root) if args.data_root else _data_root(settings)
    try:
        result = run_experiment(
            data_root,
            instrument=args.instrument,
            start_epoch=args.start,
            end_epoch=args.end,
            strategy=_strategy_from_settings(settings),
            trade=_trade_from_settings(settings),
            simulation=SimulationConfig(max_duration_seconds=args.max_duration),
            risk_equity=args.equity,
        )
    except ExperimentError as exc:
        print(f"Experiment error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — surface unexpected integration errors
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print(format_summary(result))
    if args.show_rows and result.rows:
        print()
        print("Rows:")
        for row in result.rows:
            print(
                f"  epoch={row.signal_epoch} dir={row.direction} "
                f"accepted={row.accepted} outcome={row.outcome} "
                f"R={row.realized_r} MAE={row.mae} MFE={row.mfe}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smb.research")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run historical research experiment")
    p_run.add_argument("--instrument", required=True, help="Config key / store instrument")
    p_run.add_argument("--start", type=int, default=None, help="start_epoch inclusive")
    p_run.add_argument("--end", type=int, default=None, help="end_epoch exclusive")
    p_run.add_argument("--data-root", default=None, help="Override data root (default: config)")
    p_run.add_argument("--equity", type=float, default=10_000.0, help="Risk equity")
    p_run.add_argument(
        "--max-duration",
        type=int,
        default=900,
        help="Simulation horizon seconds after signal (default 900)",
    )
    p_run.add_argument(
        "--show-rows",
        action="store_true",
        help="Print per-trade research rows",
    )
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
