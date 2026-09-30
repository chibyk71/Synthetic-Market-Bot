"""CLI registration for Milestone 6E strategy tournament."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_run_strategy_tournament(
    args: argparse.Namespace,
    *,
    load_settings,
    data_root_fn,
    strategy_from_settings,
) -> int:
    """Milestone 6E: short-horizon strategy tournament (research-only)."""
    from smb.research.tournament.models import TournamentConfig
    from smb.research.tournament.report import write_json, write_markdown
    from smb.research.tournament.runner import StrategyTournamentRunner

    settings = load_settings()
    data_root = Path(args.data_root) if args.data_root else data_root_fn(settings)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    strategy_ids = tuple(args.strategies) if args.strategies else None
    targets = tuple(float(x) for x in args.targets) if args.targets else (0.30, 0.40, 0.50)

    config = TournamentConfig(
        instrument=args.instrument,
        start_epoch=args.start,
        end_epoch=args.end,
        target_r_multiples=targets,
        max_duration_seconds=int(args.horizon),
        strategy_ids=strategy_ids,
    )
    runner = StrategyTournamentRunner(
        data_root,
        config,
        strategy_config=strategy_from_settings(settings),
    )
    result = runner.run()
    json_path = output / "tournament_results.json"
    md_path = output / "tournament_report.md"
    write_json(result, json_path)
    write_markdown(result, md_path)
    print(
        f"tournament complete: strategies={len(result.strategy_definitions)} "
        f"rows={len(result.rows)}",
        file=sys.stderr,
    )
    print(f"artifacts: {json_path} , {md_path}", file=sys.stderr)
    return 0


def add_tournament_parser(
    sub: argparse._SubParsersAction,
    *,
    load_settings,
    data_root_fn,
    strategy_from_settings,
) -> None:
    """Register run-strategy-tournament subcommand on the research CLI."""

    def _cmd(args: argparse.Namespace) -> int:
        return cmd_run_strategy_tournament(
            args,
            load_settings=load_settings,
            data_root_fn=data_root_fn,
            strategy_from_settings=strategy_from_settings,
        )

    p_tour = sub.add_parser(
        "run-strategy-tournament",
        help=(
            "Milestone 6E: short-horizon strategy tournament "
            "(ICT control + momentum/mean-reversion/breakout/impulse/vol-regime)"
        ),
    )
    p_tour.add_argument(
        "--output",
        default="results/campaigns/milestone-6e",
        help="Output directory for JSON and Markdown artifacts",
    )
    p_tour.add_argument(
        "--instrument",
        default="volatility_75_1s",
        help="Instrument key (default: volatility_75_1s)",
    )
    p_tour.add_argument("--start", type=int, default=None, help="start_epoch inclusive")
    p_tour.add_argument("--end", type=int, default=None, help="end_epoch exclusive")
    p_tour.add_argument("--data-root", default=None, help="Override data root")
    p_tour.add_argument(
        "--horizon",
        type=int,
        default=300,
        help="Simulation max duration seconds (default 300 scalping horizon)",
    )
    p_tour.add_argument(
        "--targets",
        nargs="*",
        default=None,
        help="Reward targets in R (default: 0.30 0.40 0.50)",
    )
    p_tour.add_argument(
        "--strategies",
        nargs="*",
        default=None,
        help="Strategy ids to run (default: all registered)",
    )
    p_tour.set_defaults(func=_cmd)
