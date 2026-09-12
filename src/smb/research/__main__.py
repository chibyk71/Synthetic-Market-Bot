"""CLI: python -m smb.research <command>

Commands:
  run           Run a historical research experiment on stored ticks
  run-campaign  Run a full historical research campaign with persistent artifacts

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


def cmd_run(args: argparse.Namespace) -> int:
    from smb.research.baseline import (
        BaselineAnalysisCalculator,
        format_baseline_analysis,
    )
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

    if args.analysis or args.analysis_json:
        report = BaselineAnalysisCalculator().analyze(result)
        if args.analysis:
            print()
            print(format_baseline_analysis(report))
        if args.analysis_json:
            out_path = Path(args.analysis_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote analysis JSON: {out_path}", file=sys.stderr)

    if args.diagnostic or args.diagnostic_json:
        try:
            from smb.research.diagnostic import (
                BaselineDiagnosticCalculator,
                format_diagnostic_report,
            )
        except ImportError:
            print("Diagnostic module not available", file=sys.stderr)
            return 0
        diag = BaselineDiagnosticCalculator().diagnose(result)
        if args.diagnostic:
            print()
            print(format_diagnostic_report(diag))
        if args.diagnostic_json:
            out_path = Path(args.diagnostic_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(diag.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"Wrote diagnostic JSON: {out_path}", file=sys.stderr)

    return 0


def cmd_run_campaign(args: argparse.Namespace) -> int:
    """Run Milestone 5A campaign: full pipeline + persistent artifacts."""
    from smb.research.campaign import CampaignError, format_campaign_report, run_campaign
    from smb.simulation.models import SimulationConfig

    settings = _load_settings()
    data_root = Path(args.data_root) if args.data_root else _data_root(settings)
    output = Path(args.output)
    if not args.instrument:
        print("error: --instrument is required", file=sys.stderr)
        return 2

    try:
        results = run_campaign(
            data_root,
            instrument=args.instrument,
            output_dir=output,
            start_epoch=args.start,
            end_epoch=args.end,
            strategy=_strategy_from_settings(settings),
            trade=_trade_from_settings(settings),
            simulation=SimulationConfig(max_duration_seconds=args.max_duration),
            risk_equity=args.equity,
            campaign_id=args.campaign_id,
        )
    except CampaignError as exc:
        print(f"Campaign error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — surface unexpected failures
        print(f"Campaign failed: {exc}", file=sys.stderr)
        return 1

    print(format_campaign_report(results))
    print(f"Artifacts written to: {results.output_dir}", file=sys.stderr)
    print(f"  manifest:     {results.manifest_path}", file=sys.stderr)
    print(f"  summary:      {results.summary_path}", file=sys.stderr)
    if results.trades_path:
        print(f"  trades:       {results.trades_path}", file=sys.stderr)
    if results.diagnostics_path:
        print(f"  diagnostics:  {results.diagnostics_path}", file=sys.stderr)
    print(f"  report:       {results.report_path}", file=sys.stderr)
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
    p_run.add_argument(
        "--analysis",
        action="store_true",
        help="Print expanded Milestone 3B baseline analysis after the summary",
    )
    p_run.add_argument(
        "--analysis-json",
        default=None,
        metavar="PATH",
        help="Write serializable baseline analysis JSON to PATH",
    )
    p_run.add_argument(
        "--diagnostic",
        action="store_true",
        help="Print Milestone 3C baseline diagnostic report after the summary",
    )
    p_run.add_argument(
        "--diagnostic-json",
        default=None,
        metavar="PATH",
        help="Write serializable baseline diagnostic JSON to PATH",
    )
    p_run.set_defaults(func=cmd_run)

    p_camp = sub.add_parser(
        "run-campaign",
        help="Run historical research campaign with persistent artifacts (Milestone 5A)",
    )
    p_camp.add_argument("--instrument", required=True, help="Config key / store instrument")
    p_camp.add_argument(
        "--output",
        required=True,
        help="Campaign output directory (manifest, summary, trades, report)",
    )
    p_camp.add_argument("--start", type=int, default=None, help="start_epoch inclusive")
    p_camp.add_argument("--end", type=int, default=None, help="end_epoch exclusive")
    p_camp.add_argument("--data-root", default=None, help="Override data root (default: config)")
    p_camp.add_argument("--equity", type=float, default=10_000.0, help="Risk equity")
    p_camp.add_argument(
        "--max-duration",
        type=int,
        default=900,
        help="Simulation horizon seconds after signal (default 900)",
    )
    p_camp.add_argument(
        "--campaign-id",
        default=None,
        help="Optional explicit campaign id (default: instrument_timestamp_uuid)",
    )
    p_camp.set_defaults(func=cmd_run_campaign)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
