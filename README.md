# Synthetic Market Bot

Research-first automated trading system for **Deriv Synthetic Indices**.

## Current milestones

| Milestone | Status | Scope |
|-----------|--------|-------|
| **1A** | Done | Deriv public WebSocket client + `active_symbols` discovery |
| **1B** | Done | Historical ticks (`ticks_history`) + pagination probe |

No trading strategy, execution, risk engine, simulation, ML, or Telegram
integration is present.

### Target instruments (discovered dynamically)

| Config key            | Semantic name                    |
|-----------------------|----------------------------------|
| `volatility_75_1s`    | Volatility 75 (1s) Index         |
| `step_index`          | Step Index 100                   |

Symbol IDs such as `1HZ75V` or `stpRNG` are **never hard-coded** as
authoritative. They are resolved from the live `active_symbols` response.

## API verification

Official documentation consulted (September 2026):

- [Active Symbols](https://developers.deriv.com/llms/active-symbols.md)
- [Ticks History](https://developers.deriv.com/llms/ticks-history.md)
- [Market Data overview](https://developers.deriv.com/docs/data)
- [Public WebSocket](https://developers.deriv.com/docs/options/websocket/)
- [API comparison – ticks_history](https://developers.deriv.com/comparison/ticks-history)

### Verified behaviour

| Item | Value |
|------|-------|
| Public WebSocket endpoint | `wss://api.derivws.com/trading/v1/options/ws/public` |
| Authentication | **None** required for public market data |
| `active_symbols` request | `{"active_symbols": "full"}` or `"brief"` |
| Symbol field | `underlying_symbol` |
| Name field | `underlying_symbol_name` |
| `ticks_history` request | `ticks_history`, `end`, `count`, `style: "ticks"`, optional `start` |
| History response | `history.prices[]` + `history.times[]` (epoch seconds), `pip_size` |
| Max ticks / request | **1000** (higher `count` is silently truncated) |
| Pagination | Set `end` to `earliest_epoch - 1` for the previous page; no overlap |

## Measured historical characteristics (Milestone 1B probe)

Both instruments emit **exactly 1 tick per second** over the sampled window
(3 × 1000 ticks ≈ 50 minutes):

| Metric | Volatility 75 (1s) | Step Index 100 |
|--------|--------------------|----------------|
| Resolved ID | `1HZ75V` | `stpRNG` |
| Ticks returned (3 pages) | 3000 | 3000 |
| Tick interval (min/med/max) | 1 / 1 / 1 s | 1 / 1 / 1 s |
| Approx ticks/s | 1.0000 | 1.0000 |
| Price precision | 2 decimals | 1 decimal |
| Duplicate / non-monotonic | 0 / 0 | 0 / 0 |

### Pagination findings

- Practical maximum per request: **1000 ticks**.
- Data is returned chronological (oldest → newest within a page).
- Adjacent pages with `end = earliest - 1` have **zero overlap** and a
  1-second gap for these 1s indices.
- Safe cursor: always advance with `end = page.earliest.epoch - 1`.

### Six-month feasibility (extrapolated from measured 1 tick/s)

| Estimate | Value |
|----------|-------|
| Ticks / day | 86 400 |
| Ticks / month | ≈ 2.63 M |
| Ticks / 6 months | ≈ 15.8 M |
| Approx raw CSV size (6 months, one symbol) | ~480 MB |

These are **extrapolations**, not measured downloads. A full six-month
archive is intentionally deferred.

## Project layout

```
synthetic-market-bot/
├── pyproject.toml
├── README.md
├── .gitignore
├── config/
│   └── settings.toml
├── src/smb/deriv/
│   ├── client.py      # DerivClient + req_id routing
│   ├── symbols.py     # SymbolInfo, resolve_symbol
│   └── history.py     # Tick, fetch_ticks, pagination
├── scripts/
│   ├── probe_symbols.py
│   └── probe_history.py
├── data/raw/probe/    # small CSV samples (gitignored)
└── tests/
```

## Setup

Requires **Python 3.12+**.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run tests

```bash
pytest
```

All unit tests are offline.

## Live probes (Internet required)

```bash
python scripts/probe_symbols.py
python scripts/probe_history.py
```

## Configuration

```toml
[instruments.volatility_75_1s]
name = "Volatility 75 (1s) Index"

[instruments.step_index]
name = "Step Index 100"

[deriv]
websocket_url = "wss://api.derivws.com/trading/v1/options/ws/public"
request_timeout_seconds = 15.0
```

## Architecture boundary

```
Deriv API
    ↓
DerivClient (req_id-routed)
    ↓
Historical Tick API (history.py)
    ↓
Normalized Tick
```

Later milestones will add Historical Replay → Candle Builder → Strategy.
Do not couple history retrieval to strategy logic.

## Known limitations (intentionally deferred)

- Reconnection / heartbeat framework
- Live tick subscriptions
- Full Market Data interface abstraction
- Candle builder, strategy, risk, execution, simulation, ML, Telegram
- Bulk multi-month historical archive
