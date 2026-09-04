#!/usr/bin/env python3
"""Live probe: connect to Deriv, discover configured instruments, print metadata.

Requires Internet access. Run from the project root after installing the package:

    python scripts/probe_symbols.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# Allow running without an editable install when developing.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from smb.deriv.client import DEFAULT_WS_URL, DerivClient  # noqa: E402
from smb.deriv.symbols import load_active_symbols, resolve_symbol  # noqa: E402


def _load_settings() -> dict:
    path = _ROOT / "config" / "settings.toml"
    with path.open("rb") as f:
        return tomllib.load(f)


def _print_instrument(title: str, info) -> None:
    print("=" * 60)
    print(title)
    print("=" * 60)
    print()
    print(f"Name:                    {info.name}")
    print(f"Underlying symbol:       {info.symbol}")
    print(f"Market:                  {info.market}")
    print(f"Submarket:               {info.submarket}")
    print(f"Subgroup:                {info.subgroup}")
    print(f"Pip size:                {info.pip_size}")
    print(f"Symbol type:             {info.underlying_symbol_type}")
    print(f"Exchange is open:        {info.exchange_is_open}")
    print(f"Trading suspended:       {info.is_trading_suspended}")
    print(f"Trade count:             {info.trade_count}")
    print()


async def main() -> int:
    settings = _load_settings()
    instruments = settings.get("instruments", {})
    deriv_cfg = settings.get("deriv", {})
    url = deriv_cfg.get("websocket_url", DEFAULT_WS_URL)
    timeout = float(deriv_cfg.get("request_timeout_seconds", 15.0))

    targets = {
        key: cfg["name"]
        for key, cfg in instruments.items()
        if isinstance(cfg, dict) and "name" in cfg
    }
    if not targets:
        print("No instruments configured in config/settings.toml", file=sys.stderr)
        return 1

    print("Connecting to Deriv...")
    client = DerivClient(url=url, timeout=timeout)
    try:
        await client.connect()
        print("Connected.")
        print()

        symbols = await load_active_symbols(client, detail="full")
        print(f"Active symbols retrieved: {len(symbols)}")
        print()

        for _key, display_name in targets.items():
            info = resolve_symbol(display_name, symbols)
            _print_instrument(display_name, info)

    finally:
        await client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
