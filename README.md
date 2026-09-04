# Synthetic Market Bot

Research-first automated trading system for **Deriv Synthetic Indices**.

This repository currently implements only the foundation required to talk to
Deriv's public market-data WebSocket and to discover trading instruments at
runtime.

## Current milestone (1A)

```
Deriv WebSocket
      ↓
API client
      ↓
active_symbols
      ↓
instrument discovery
```

No trading strategy, execution, risk engine, simulation, ML, or Telegram
integration is present in this milestone.

### Target instruments (discovered dynamically)

| Config key            | Semantic name (from settings.toml)   |
|-----------------------|--------------------------------------|
| `volatility_75_1s`    | Volatility 75 (1s) Index             |
| `step_index`          | Step Index 100                       |

Symbol IDs such as `1HZ75V` or `stpRNG` are **never hard-coded** as
authoritative. They are resolved from the live `active_symbols` response.

> **Note on Step Index:** The current Deriv `active_symbols` payload lists
> Step Index variants as `Step Index 100`, `Step Index 200`, … rather than a
> single name `"Step Index"`. Configuration uses the exact display name
> returned by the API.

## API verification

Official documentation consulted (September 2026):

- [Active Symbols](https://developers.deriv.com/llms/active-symbols.md)
- [Market Data overview](https://developers.deriv.com/docs/data)
- [Public WebSocket](https://developers.deriv.com/docs/options/websocket/)
- [API comparison – active_symbols](https://developers.deriv.com/comparison/active-symbols)

### Verified behaviour

| Item | Value |
|------|-------|
| Public WebSocket endpoint | `wss://api.derivws.com/trading/v1/options/ws/public` |
| Authentication | **None** required for `active_symbols` |
| Request | `{"active_symbols": "full"}` or `"brief"` |
| Response symbol field | `underlying_symbol` (replaces legacy `symbol`) |
| Response name field | `underlying_symbol_name` (replaces legacy `display_name`) |
| Pip field | `pip_size` (replaces legacy `pip`) |

Legacy parameters such as `product_type` and `landing_company_short` are no
longer accepted by the current API and are not sent.

## Project layout

```
synthetic-market-bot/
├── pyproject.toml
├── README.md
├── .gitignore
├── config/
│   └── settings.toml          # semantic instrument names + WS URL
├── src/
│   └── smb/
│       ├── __init__.py
│       └── deriv/
│           ├── __init__.py
│           ├── client.py      # DerivClient + DerivAPIError
│           └── symbols.py     # SymbolInfo, resolve_symbol, …
├── scripts/
│   └── probe_symbols.py       # live discovery probe
└── tests/
    ├── test_client.py
    └── test_symbols.py
```

## Setup

Requires **Python 3.12+**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Run tests

```bash
pytest
```

All unit tests are offline; they use fixtures / mocks and do **not** require
Internet access.

## Live API probe

```bash
python scripts/probe_symbols.py
```

This script connects to Deriv, requests `active_symbols`, resolves the
configured instruments, and prints verified metadata. **Internet access is
required.**

Example output shape:

```
Connecting to Deriv...
Connected.

Active symbols retrieved: 89

============================================================
Volatility 75 (1s) Index
============================================================

Name:                    Volatility 75 (1s) Index
Underlying symbol:       1HZ75V
Market:                  synthetic_index
…
```

## Configuration

Instrument names live in `config/settings.toml`:

```toml
[instruments.volatility_75_1s]
name = "Volatility 75 (1s) Index"

[instruments.step_index]
name = "Step Index 100"

[deriv]
websocket_url = "wss://api.derivws.com/trading/v1/options/ws/public"
request_timeout_seconds = 15.0
```

No API token is required for public market-data endpoints.

## Known limitations (intentionally deferred)

- Reconnection / heartbeat framework
- Tick streaming and historical data
- Market-data interface abstraction used by later layers
- Strategy, risk, execution, simulation, ML, Telegram

These belong to subsequent milestones.
