"""CLI registration for Milestone 6E strategy tournament."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from smb.research.tournament.models import (
    DEFAULT_TOURNAMENT_HORIZON_SECONDS,
    TARGET_R_MULTIPLES,
)


def _validate_frozen_targets(values: list[str] | None) -> tuple[float, ...]:
    """Accept only the exact frozen target set; reject anything else."""
    if values is None:
        return TARGET_R_MULTIPLES
    parsed = tuple(float(x) for x in values)
    if len(parsed) != len(TARGET_R_MULTIPLES) or any(
        abs(a - b) >= 1e-12 for a, b in zip(parsed, TARGET_R_MULTIPLES, strict=True)
    ):
        raise SystemExit(
            "Milestone 6E freezes --targets to exactly "
            f"{' '.join(str(t) for t in TARGET_R_MULTIPLES)}; "
            f"got: {' '.join(values)}"
        )
    return TARGET_R_MULTIPLES


def _validate_frozen_horizon(value: int) -> int:
    if value != DEFAULT_TOURNAMENT_HORIZON_SECONDS:
        raise SystemExit(
            "Milestone 6E freezes --horizon to exactly "
            f"{DEFAULT_TOURNAMENT_HORIZON_SECONDS}; got: {value}"
        )
    return DEFAULT_TOURNAMENT_HORIZON_SECONDS


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
    targets = _validate_frozen_targets(args.targets)
    horizon = _validate_frozen_horizon(int(args.horizon))

    config = TournamentConfig(
        instrument=args.instrument,
        start_epoch=args.start,
        end_epoch=args.end,
        target_r_multiples=targets,
        max_duration_seconds=horizon,
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
        default=DEFAULT_TOURNAMENT_HORIZON_SECONDS,
        help=(
            f"Simulation max duration seconds "
            f"(frozen: {DEFAULT_TOURNAMENT_HORIZON_SECONDS}; other values rejected)"
        ),
    )
    p_tour.add_argument(
        "--targets",
        nargs="*",
        default=None,
        help=(
            "Reward targets in R (frozen: "
            f"{' '.join(str(t) for t in TARGET_R_MULTIPLES)}; "
            "other values rejected)"
        ),
    )
    p_tour.add_argument(
        "--strategies",
        nargs="*",
        default=None,
        help="Strategy ids to run (default: all registered)",
    )
    p_tour.set_defaults(func=_cmd)
