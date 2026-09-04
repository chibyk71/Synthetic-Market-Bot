"""Developer CLI: python -m smb.data <command>

Commands:
  ingest        Fetch historical ticks and write Parquet
  validate      Scan tick dataset and report integrity
  stats         Print tick coverage statistics
  build-candles Build M1/M5/M15 from stored ticks and persist
  candle-stats  Print candle dataset coverage
"""

from __future__ import annotations

import argparse
import asyncio
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


def cmd_stats(args: argparse.Namespace) -> int:
    from smb.data.repository import TickRepository
    from smb.data.stats import compute_dataset_stats
    from smb.data.store import ParquetTickStore

    settings = _load_settings()
    store = ParquetTickStore(_data_root(settings))
    repo = TickRepository(store)
    stats = compute_dataset_stats(repo)
    if not stats.instruments:
        print("No instruments in dataset.")
        return 0
    for item in stats.instruments:
        print(f"instrument:          {item.instrument}")
        print(f"  tick_count:        {item.tick_count}")
        print(f"  earliest:          {item.earliest_timestamp}")
        print(f"  latest:            {item.latest_timestamp}")
        print(f"  duration_seconds:  {item.duration_seconds}")
        print(f"  min_price:         {item.min_price}")
        print(f"  max_price:         {item.max_price}")
        print(f"  duplicates:        {item.duplicate_count}")
        print(f"  non_monotonic:     {item.non_monotonic_count}")
        print()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from smb.data.repository import StorageError, TickRepository
    from smb.data.stats import compute_dataset_stats
    from smb.data.store import ParquetTickStore

    settings = _load_settings()
    store = ParquetTickStore(_data_root(settings))
    repo = TickRepository(store)
    try:
        stats = compute_dataset_stats(repo)
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 2
    instruments = stats.instruments
    if args.instrument:
        instruments = tuple(
            i for i in instruments if i.instrument == args.instrument
        )
        if not instruments:
            try:
                cov = repo.coverage(args.instrument)
            except StorageError as exc:
                print(f"Storage error: {exc}", file=sys.stderr)
                return 2
            if cov["tick_count"] == 0:
                print(f"No data for instrument: {args.instrument}")
                return 0
    if not instruments:
        print("No instruments to validate.")
        return 0
    exit_code = 0
    for item in instruments:
        ok = item.duplicate_count == 0 and item.non_monotonic_count == 0
        status = "OK" if ok else "FAIL"
        print(
            f"[{status}] {item.instrument}: ticks={item.tick_count} "
            f"dup={item.duplicate_count} non_mono={item.non_monotonic_count}"
        )
        if item.duplicate_count:
            print(f"  - {item.duplicate_count} duplicate tick(s)")
        if item.non_monotonic_count:
            print(f"  - {item.non_monotonic_count} non-monotonic pair(s)")
        if not ok:
            exit_code = 1
    return exit_code


def cmd_ingest(args: argparse.Namespace) -> int:
    from smb.data.ingest import ingest_instrument
    from smb.data.store import ParquetTickStore
    from smb.deriv.client import DEFAULT_WS_URL, DerivClient

    settings = _load_settings()
    instruments = settings.get("instruments", {})
    deriv = settings.get("deriv", {})
    store = ParquetTickStore(_data_root(settings))

    targets: list[tuple[str, str]] = []
    if args.instrument:
        cfg = instruments.get(args.instrument)
        if not cfg or "name" not in cfg:
            print(f"Unknown instrument key: {args.instrument}", file=sys.stderr)
            return 1
        targets.append((args.instrument, cfg["name"]))
    else:
        for key, cfg in instruments.items():
            if isinstance(cfg, dict) and "name" in cfg:
                targets.append((key, cfg["name"]))

    if not targets:
        print("No instruments configured.", file=sys.stderr)
        return 1

    async def _run() -> int:
        url = deriv.get("websocket_url", DEFAULT_WS_URL)
        timeout = float(deriv.get("request_timeout_seconds", 30.0))
        async with DerivClient(url=url, timeout=timeout) as client:
            for key, name in targets:
                result = await ingest_instrument(
                    client,
                    store,
                    instrument=key,
                    display_name=name,
                    pages=args.pages,
                    end=args.end,
                )
                print(
                    f"Ingested {result.instrument} ({result.symbol}): "
                    f"pages={result.pages_fetched} written={result.ticks_written}"
                )
        return 0

    return asyncio.run(_run())


def cmd_build_candles(args: argparse.Namespace) -> int:
    from smb.data.candle_store import ParquetCandleStore, build_candles_from_ticks
    from smb.data.repository import StorageError, TickRepository
    from smb.data.store import ParquetTickStore
    from smb.market.candles import TIMEFRAMES

    settings = _load_settings()
    root = _data_root(settings)
    tick_repo = TickRepository(ParquetTickStore(root))
    candle_store = ParquetCandleStore(root)

    instruments = (
        [args.instrument] if args.instrument else tick_repo.list_instruments()
    )
    if not instruments:
        print("No tick instruments in dataset.", file=sys.stderr)
        return 1

    tfs = None
    if args.timeframe:
        key = args.timeframe.upper()
        if key not in TIMEFRAMES:
            print(f"Unknown timeframe: {args.timeframe}", file=sys.stderr)
            return 1
        tfs = [TIMEFRAMES[key]]

    try:
        for instrument in instruments:
            counts = build_candles_from_ticks(
                tick_repo,
                candle_store,
                instrument=instrument,
                timeframes=tfs,
                start_epoch=args.start,
                end_epoch=args.end,
            )
            parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"{instrument}: wrote {parts}")
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_candle_stats(args: argparse.Namespace) -> int:
    from smb.data.candle_store import ParquetCandleStore
    from smb.data.repository import StorageError

    settings = _load_settings()
    store = ParquetCandleStore(_data_root(settings))
    instruments = (
        [args.instrument] if args.instrument else store.list_instruments()
    )
    if not instruments:
        print("No candle instruments in dataset.")
        return 0
    try:
        for instrument in instruments:
            tfs = store.list_timeframes(instrument)
            if args.timeframe:
                tfs = [args.timeframe.upper()]
            for tf in tfs:
                cov = store.coverage(instrument, tf)
                print(
                    f"{instrument} {tf}: candles={cov['candle_count']} "
                    f"start={cov['earliest_start']} end={cov['latest_start']} "
                    f"low={cov['min_low']} high={cov['max_high']}"
                )
    except StorageError as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smb.data")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Fetch and store historical ticks")
    p_ingest.add_argument("--instrument", help="Config key (default: all)")
    p_ingest.add_argument("--pages", type=int, default=3, help="Pages per instrument")
    p_ingest.add_argument("--end", default="latest", help="End cursor (latest or epoch)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_val = sub.add_parser("validate", help="Validate stored dataset")
    p_val.add_argument("--instrument", help="Limit to one instrument key")
    p_val.set_defaults(func=cmd_validate)

    p_stats = sub.add_parser("stats", help="Print tick dataset statistics")
    p_stats.set_defaults(func=cmd_stats)

    p_bc = sub.add_parser("build-candles", help="Build candles from stored ticks")
    p_bc.add_argument("--instrument", help="Config key (default: all with ticks)")
    p_bc.add_argument("--timeframe", help="M1, M5, or M15 (default: all three)")
    p_bc.add_argument("--start", type=int, default=None, help="start_epoch inclusive")
    p_bc.add_argument("--end", type=int, default=None, help="end_epoch exclusive")
    p_bc.set_defaults(func=cmd_build_candles)

    p_cs = sub.add_parser("candle-stats", help="Print candle dataset coverage")
    p_cs.add_argument("--instrument", help="Limit to one instrument")
    p_cs.add_argument("--timeframe", help="Limit to one timeframe")
    p_cs.set_defaults(func=cmd_candle_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
