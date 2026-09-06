#!/usr/bin/env python3
"""Live probe: historical ticks for configured instruments + pagination study.

Requires Internet access. Run from the project root:

    python scripts/probe_history.py

Saves a small CSV sample under data/raw/probe/ (gitignored).
"""

from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from smb.deriv.client import DEFAULT_WS_URL, DerivClient  # noqa: E402
from smb.deriv.history import (  # noqa: E402
    MAX_TICKS_PER_REQUEST,
    compute_tick_stats,
    fetch_ticks,
    fetch_ticks_paginated,
    flatten_pages,
)
from smb.deriv.symbols import load_active_symbols, resolve_symbol  # noqa: E402


def _load_settings() -> dict:
    path = _ROOT / "config" / "settings.toml"
    with path.open("rb") as f:
        return tomllib.load(f)


def _fmt_ts(dt) -> str:
    if dt is None:
        return "n/a"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _print_stats(title: str, symbol: str, stats, requested_pages: int, requested_count: int) -> None:
    print("=" * 64)
    print(title)
    print("=" * 64)
    print(f"  Resolved symbol ID:     {symbol}")
    print(f"  Requested pages\u00d7count:  {requested_pages} \u00d7 {requested_count}")
    print(f"  Returned tick count:    {stats.count}")
    print(f"  Earliest timestamp:     {_fmt_ts(stats.earliest)}")
    print(f"  Latest timestamp:       {_fmt_ts(stats.latest)}")
    if stats.duration_seconds is not None:
        print(f"  Elapsed period:         {stats.duration_seconds:.0f}s "
              f"({stats.duration_seconds/3600:.2f} h)")
    print(f"  Min tick interval:      {stats.min_interval}")
    print(f"  Max tick interval:      {stats.max_interval}")
    print(f"  Median tick interval:   {stats.median_interval}")
    if stats.ticks_per_second is not None:
        print(f"  Approx ticks/second:    {stats.ticks_per_second:.4f}")
    print(f"  Min price:              {stats.min_price}")
    print(f"  Max price:              {stats.max_price}")
    print(f"  Price precision (dec):  {stats.price_precision}")
    print(f"  Duplicate epochs:       {stats.duplicate_epochs}")
    print(f"  Non-monotonic pairs:    {stats.non_monotonic_pairs}")
    print()


def _estimate_six_months(stats) -> None:
    if not stats.ticks_per_second or stats.ticks_per_second <= 0:
        print("  (insufficient data for six-month estimate)")
        return
    tps = stats.ticks_per_second
    per_day = tps * 86400
    per_month = per_day * 30.4375
    per_6m = per_month * 6
    bytes_per_tick = 32
    size_6m_mb = (per_6m * bytes_per_tick) / (1024 * 1024)
    print("  --- Six-month feasibility (EXTRAPOLATED from measured rate) ---")
    print(f"  Measured ticks/s:       {tps:.4f}")
    print(f"  Est. ticks/day:         {per_day:,.0f}")
    print(f"  Est. ticks/month:       {per_month:,.0f}")
    print(f"  Est. ticks/6 months:    {per_6m:,.0f}")
    print(f"  Est. raw size (CSV):    ~{size_6m_mb:,.0f} MB")
    print()


def _save_sample(symbol: str, ticks, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol}_sample.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "timestamp_utc", "price"])
        for t in ticks[:200]:
            w.writerow([t.epoch, t.timestamp.isoformat(), t.price])
    return path


async def main() -> int:
    settings = _load_settings()
    instruments = settings.get("instruments", {})
    deriv_cfg = settings.get("deriv", {})
    url = deriv_cfg.get("websocket_url", DEFAULT_WS_URL)
    timeout = float(deriv_cfg.get("request_timeout_seconds", 30.0))

    targets = {
        key: cfg["name"]
        for key, cfg in instruments.items()
        if isinstance(cfg, dict) and "name" in cfg
    }
    if not targets:
        print("No instruments configured", file=sys.stderr)
        return 1

    pages = 3
    count = MAX_TICKS_PER_REQUEST

    print("Connecting to Deriv...")
    client = DerivClient(url=url, timeout=timeout)
    sample_dir = _ROOT / "data" / "raw" / "probe"
    try:
        await client.connect()
        print("Connected.\n")

        symbols = await load_active_symbols(client, detail="full")
        print(f"Active symbols retrieved: {len(symbols)}\n")

        print("-" * 64)
        print("Pagination experiment (Volatility 75 (1s) Index)")
        print("-" * 64)
        v75_info = resolve_symbol("Volatility 75 (1s) Index", symbols)
        page1 = await fetch_ticks(client, v75_info.symbol, count=5000, end="latest")
        print(f"  Requested count=5000 \u2192 received {page1.count} "
              f"(API max appears to be {MAX_TICKS_PER_REQUEST})")
        page_a = await fetch_ticks(client, v75_info.symbol, count=1000, end="latest")
        end_b = page_a.earliest.epoch - 1 if page_a.earliest else "latest"
        page_b = await fetch_ticks(client, v75_info.symbol, count=1000, end=end_b)
        overlap = set()
        if page_a.ticks and page_b.ticks:
            overlap = {t.epoch for t in page_a.ticks} & {t.epoch for t in page_b.ticks}
        print(f"  Page A (latest): {page_a.count} ticks "
              f"{page_a.earliest.epoch if page_a.earliest else 'n/a'}.."
              f"{page_a.latest.epoch if page_a.latest else 'n/a'}")
        print(f"  Page B (end={end_b}): {page_b.count} ticks "
              f"{page_b.earliest.epoch if page_b.earliest else 'n/a'}.."
              f"{page_b.latest.epoch if page_b.latest else 'n/a'}")
        print(f"  Overlap epochs: {len(overlap)}")
        print(f"  Chronological (A): "
              f"{list(page_a.ticks) == sorted(page_a.ticks, key=lambda t: t.epoch)}")
        print("  Safe cursor advance: end = earliest_epoch - 1")
        print()

        for _key, display_name in targets.items():
            info = resolve_symbol(display_name, symbols)
            pages_list = await fetch_ticks_paginated(
                client, info.symbol, pages=pages, count_per_page=count
            )
            ticks = flatten_pages(pages_list)
            stats = compute_tick_stats(ticks)
            _print_stats(display_name, info.symbol, stats, pages, count)
            _estimate_six_months(stats)
            sample_path = _save_sample(info.symbol, ticks, sample_dir)
            print(f"  Sample saved: {sample_path.relative_to(_ROOT)} "
                  f"({min(200, len(ticks))} rows)\n")

    finally:
        await client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
