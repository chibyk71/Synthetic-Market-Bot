# Synthetic Market Bot

Research-first automated trading system for **Deriv Synthetic Indices**.

The system is designed to **observe, simulate, and evaluate** strategy behaviour on
synthetic indices. It does **not** place real or demo orders.

---

## Project status

**Milestones 1A-4B are complete** in code. That means:

| Capability | Status |
|------------|--------|
| Historical tick ingest + Parquet storage | Implemented |
| Historical replay + M1/M5/M15 candles | Implemented |
| Strategy engine (sweep -> MSB -> displacement -> FVG) | Implemented |
| Trade construction + risk sizing | Implemented |
| Tick-level trade simulation | Implemented |
| MAE/MFE research metrics | Implemented |
| Strategy validation aggregates | Implemented |
| ML dataset + baseline model + walk-forward | Implemented |
| Live market data (public WebSocket) | Implemented |
| Live strategy + **simulation** | Implemented |
| Live ML inference / gating | **Not** implemented |
| Demo order execution | **Not** implemented |
| Real-money order execution | **Not** implemented |

**Important distinction**

- **Research infrastructure exists** -- libraries, storage, live observation path.
- **The empirical research campaign has not been completed by this repository.**
  There is no checked-in proof that the strategy is profitable, stable, or ready for demo.

Do not treat "4B merged" as "strategy validated."

---

## Milestone roadmap

| Milestone | Status | Purpose |
|-----------|--------|---------|
| 1A | Complete | Deriv public WebSocket client + `active_symbols` discovery |
| 1B | Complete | Historical ticks (`ticks_history`) + pagination |
| 1C | Complete | Historical replay + deterministic M1/M5/M15 candles |
| 1D | Complete | Tick Parquet dataset + DuckDB-oriented storage/validation |
| 1E | Complete | Historical candle Parquet dataset (build/query) |
| 2A | Complete | Strategy engine |
| 2B | Complete | Trade construction + risk |
| 2C | Complete | Tick-level simulation |
| 2D | Complete | MAE/MFE research metrics |
| 3A | Complete | Strategy validation report |
| 3B | Complete | ML dataset + baseline model |
| 3C | Complete | Walk-forward validation |
| 4A | Complete | Live market data |
| 4B | Complete | Live strategy + simulation (no execution) |
| **Historical research campaign** | **Next** | Run real empirical evaluation on historical data |
| Live observation campaign | Pending | ~1 week live simulation / recording |
| Demo execution | Future | Not implemented |
| Real execution | Future | Not implemented |

---

## Architecture

### Historical research path

```
Deriv ticks_history
        |
Parquet tick store (data/)
        |
HistoricalReplay / CandleBuilder  (or candle Parquet from 1E)
        |
StrategyEngine (2A)
        |
TradeConstructor + RiskContext (2B)
        |
SimulationEngine (2C)
        |
ResearchMetricsCalculator (2D)
        |
StrategyValidationCalculator (3A)
        |
ML dataset / baseline model (3B)
        |
Walk-forward validation (3C)
```

### Live observation path (4A + 4B)

```
Deriv public WebSocket
        |
normalize + ordering / dedup
        |
M1 + M15 live candles (UPDATE / FINALIZED)
        |
order_candle_events (M15 FINALIZED before M1 at shared boundary)
        |
LiveStrategyRunner
        |
StrategyEngine -> TradeConstructor -> LiveSimulationSession
        |
LiveResearchRecord (SIGNAL_* / TRADE_*)
```

**4B is simulation and observation only.**
No broker orders, no demo account trading, no Telegram alerts.

---

## Strategy contract (2A -> 2B -> 2C)

Decision flow (unchanged by 4B):

```
M15 context
    |
M1 liquidity sweep
    |
M1 market structure break
    |
displacement
    |
FVG
    |
StrategySignal
    |
TradeConstructor (+ RiskContext)
    |
TradeCandidate or rejection
    |
SimulationEngine / LiveSimulationSession
```

### Causality rules

- Strategy evaluates **finalized** candles only.
- Forming candles (`UPDATE`) must **not** trigger decisions.
- At a shared M15/M1 boundary, **M15 FINALIZED is delivered before M1 FINALIZED**
  so the M1 decision at `T` can use M15 context with `end_epoch <= T`
  (see `order_candle_events` in `src/smb/live/candle_feed.py`).
- No future candle or future tick information.
- Simulation only uses ticks with `epoch > signal_epoch`, up to
  `signal_epoch + max_duration_seconds` (default **900** seconds).
- Strategy, risk, and simulation remain separate modules -- 4B only composes them.

Defaults live in `config/settings.toml` (`[strategy]`, `[trade]`) and mirror
`StrategyConfig` / `TradeConfig`.

---

## Target instruments

Configured by **semantic name**, resolved at runtime via Deriv `active_symbols`
(never hard-code symbol IDs as the source of truth):

| Config key (`config/settings.toml`) | Display name |
|-------------------------------------|--------------|
| `volatility_75_1s` | Volatility 75 (1s) Index |
| `step_index` | Step Index 100 |

---

## What to do **now**

The next phase is **not** another coding milestone. Run research:

1. Verify environment (`pip install -e ".[dev]"`, `pytest`, `ruff check .`).
2. Obtain / ingest historical Deriv tick data.
3. Validate storage and build candles.
4. Run strategy -> risk -> simulation **programmatically** on historical data.
5. Compute research metrics (MAE/MFE).
6. Build strategy validation reports.
7. Build ML dataset / train baseline / walk-forward.
8. Inspect results against the checklist below.
9. Decide PASS / FAIL / INVESTIGATE on historical gates.
10. Only then run ~1 week **live simulation** (4A+4B).
11. Compare live vs historical behaviour.
12. Only after live health gates consider a **future** demo-execution milestone.

---

## Setup

```bash
# Python 3.12+
pip install -e ".[dev]"

# Quality gates (from project root)
pytest
ruff check .
```

Public market-data WebSocket requires **no API token** for the endpoints used by
this repo (see [Environment](#environment--safety)).

---

## Historical data operations (existing CLI)

Data root defaults to `data/` (`config/settings.toml` -> `[data] root`).
Large datasets are **gitignored**.

### Ingest ticks from Deriv

```bash
python -m smb.data ingest --pages 3
python -m smb.data ingest --instrument volatility_75_1s --pages 10
```

### Validate / stats

```bash
python -m smb.data validate
python -m smb.data stats
python -m smb.data validate --instrument volatility_75_1s
```

### Build candle dataset from stored ticks

```bash
python -m smb.data build-candles
python -m smb.data build-candles --instrument volatility_75_1s --timeframe M1
python -m smb.data candle-stats
```

Layout (under `data/`):

```
data/
  ticks/...          # Parquet tick partitions (ParquetTickStore)
  candles/
    instrument={key}/
      timeframe={M1|M5|M15}/
        year={YYYY}/month={MM}/...
```

### Probes (network required)

```bash
python scripts/probe_symbols.py
python scripts/probe_history.py
```

---

## Historical research runbook (components)

### Missing piece: no single end-to-end campaign command

There is **no** `python -m smb.research run-campaign` (or equivalent) that wires
strategy -> risk -> simulation -> metrics -> validation -> ML in one CLI.

What **exists**:

| Layer | Package / entry | How to use |
|-------|-----------------|------------|
| Data CLI | `python -m smb.data ...` | ingest / validate / stats / build-candles |
| Replay | `smb.market.HistoricalReplay` | feed ticks in order |
| Candles | `smb.market` builders / `ParquetCandleStore.iter_candles` | M1/M15 series |
| Strategy | `smb.strategy.StrategyEngine` | `on_m15` / `on_m1` or `process` |
| Risk | `smb.trade.TradeConstructor` | `construct(signal, RiskContext)` |
| Simulation | `smb.simulation.SimulationEngine` | `simulate(candidate, ticks)` |
| MAE/MFE | `smb.research.ResearchMetricsCalculator` | `calculate(simulation, ticks)` |
| Validation | `smb.validation.StrategyValidationCalculator` | `validate(simulations, metrics)` |
| ML | `smb.ml` | `build_dataset`, `train_baseline`, `run_walk_forward_validation` |
| Live | `smb.live` | `LiveMarketDataService`, `LiveStrategyRunner` |

A dedicated **historical research runner** (CLI or script) is a reasonable
**future task**, not part of this documentation-only update.

### Illustrative API composition (not a shipped CLI)

```python
from smb.strategy import StrategyEngine, StrategyConfig
from smb.trade import TradeConstructor, RiskContext, TradeConfig
from smb.simulation import SimulationEngine, SimulationConfig
from smb.research import ResearchMetricsCalculator
from smb.validation import StrategyValidationCalculator

# 1) Load finalized M15 then M1 candles in time order
#    (ParquetCandleStore.iter_candles or CandleBuilder over HistoricalReplay).
# 2) eng = StrategyEngine(instrument, StrategyConfig(...))
#    for each finalized M15: eng.on_m15(c)
#    for each finalized M1: signals += eng.on_m1(c)
# 3) constructor = TradeConstructor(TradeConfig(...))
#    risk = RiskContext(equity=10_000.0)
#    results = [constructor.construct(s, risk) for s in signals]
# 4) sim = SimulationEngine(SimulationConfig(max_duration_seconds=900))
#    for each accepted TradeCandidate, pass ticks with epoch > signal_epoch
#    outcome = sim.simulate(candidate, tick_window)
# 5) metrics = ResearchMetricsCalculator().calculate(outcome, tick_window)
# 6) report = StrategyValidationCalculator().validate(all_outcomes, all_metrics)
```

ML (after simulation + metrics align to observations):

```python
from smb.ml import (
    build_dataset,
    run_walk_forward_validation,
    default_walk_forward_config,
)

# dataset = build_dataset(...)   # see src/smb/ml/README.md
# result = run_walk_forward_validation(dataset, default_walk_forward_config())
```

See unit tests under `tests/test_strategy_*.py`, `tests/test_simulation_engine.py`,
`tests/test_research_metrics.py`, `tests/test_strategy_validation.py`,
`tests/test_ml_*.py` for concrete fixtures.

---

## Historical research checklist

### Strategy performance

- Total candidate signals
- Accepted vs risk-rejected
- Filled trades vs `NO_FILL`
- Counts/rates: `TP`, `SL`, `TIMEOUT`, `NO_FILL`
- Win rate (among filled; define consistently)
- Average R / expectancy / cumulative R
- Drawdown characteristics
- Trade duration distribution

### MAE / MFE (post-trade research only)

- Distributions overall and by outcome
- Whether TP/SL geometry looks sensible vs excursions
- **Do not** feed MAE/MFE back as signal-time features merely because they exist

### Segmentation (where data allows)

- Instrument, direction, hour/session, duration

### Validation and ML

- Chronological splits and walk-forward folds
- Sample sizes, class balance
- OOS classification metrics
- Stability across folds

ML is a **filter / gating research** component, not a standalone strategy generator.

---

## Research acceptance framework

Do **not** invent fixed profitability thresholds before seeing natural distributions.

```
Historical data quality
        |
Sufficient sample size
        |
Positive / stable expectancy (define for your risk policy)
        |
Acceptable drawdown (policy-dependent)
        |
Reasonable trade frequency
        |
MAE/MFE behaviour coherent with exits
        |
Walk-forward stability
        |
ML adds genuine OOS value (if used)
        |
No obvious leakage / lookahead
        |
PASS / FAIL / INVESTIGATE
```

Numerical cutoffs should be set after inspecting empirical behaviour and risk objectives.

---

## Live simulation runbook (4A + 4B)

```
Deriv live ticks
    -> LiveMarketDataService
    -> M1/M15 FINALIZED (ordered)
    -> LiveStrategyRunner
    -> StrategyEngine -> TradeConstructor -> LiveSimulationSession
    -> LiveResearchRecord
```

### Properties

- **No** broker order is placed.
- Open simulations survive temporary reconnects.
- Duplicate finalized signals are deduplicated by `(instrument, signal_epoch, direction)`.
- Shared-boundary ordering: M15 before M1.
- Same core exit semantics as 2C (`TP` / `SL` / `TIMEOUT` / `NO_FILL`).
- Optional `record_sink` on `LiveStrategyRunner` for persistence.

### Local / fake stream

```python
import asyncio
from smb.live import (
    FakeTickTransport,
    LiveMarketDataService,
    LiveStrategyRunner,
    LiveRunnerConfig,
    make_fake_symbol,
)

async def main():
    transport = FakeTickTransport()
    market = LiveMarketDataService(
        "Volatility 75 (1s) Index",
        transport=transport,
        symbol_resolver=lambda n: make_fake_symbol(n, "1HZ75V"),
    )
    runner = LiveStrategyRunner(market, config=LiveRunnerConfig())
    await runner.start()
    await runner.stop()
    for rec in runner.records:
        print(rec)

asyncio.run(main())
```

### Live campaign intent

Run live **simulation** for on the order of **one week**, record signals and
outcomes, compare to historical expectations. Validation only -- not execution.

Details: [`src/smb/live/README.md`](src/smb/live/README.md).

---

## Environment and safety

### `.env.example`

```
# No credentials required for public active_symbols / public tick stream.
# DERIV_APP_ID=
# DERIV_API_TOKEN=
```

### Actual configuration

| Source | Purpose |
|--------|---------|
| `config/settings.toml` | Instruments, Deriv WS URL, data root, strategy/trade defaults |
| `DerivClient` `DEFAULT_WS_URL` | `wss://api.derivws.com/trading/v1/options/ws/public` (public, no auth) |

**4B is simulation-only.**
Do not add trading credentials or execution configuration unless a future
**execution** milestone explicitly requires them.

---

## Testing

```bash
pytest
ruff check .
```

Configured in `pyproject.toml` (`testpaths = ["tests"]`, `pythonpath = ["src"]`).

| Area | Representative tests |
|------|----------------------|
| Client / symbols / history | `test_client`, `test_symbols`, `test_history` |
| Replay / candles / storage | `test_replay`, `test_candles`, `test_storage`, `test_candle_store` |
| Strategy | `test_strategy_*` |
| Trade / risk | `test_trade_*` |
| Simulation / metrics / validation | `test_simulation_engine`, `test_research_metrics`, `test_strategy_validation` |
| ML / walk-forward | `test_ml_dataset_model`, `test_ml_walk_forward` |
| Live 4A / 4B | `test_live_market_data`, `test_live_strategy_simulation`, `test_live_candle_event_order` |

Confirm pass counts on your machine after install; this README does not hard-code a number.

---

## Package layout

```
src/smb/
  deriv/        # client, symbols, history
  market/       # replay, candles
  data/         # tick/candle stores, ingest, CLI (python -m smb.data)
  strategy/     # 2A engine
  trade/        # 2B construction + risk
  simulation/   # 2C engine
  research/     # 2D MAE/MFE
  validation/   # 3A aggregates
  ml/           # 3B/3C dataset, model, walk-forward
  live/         # 4A market data + 4B strategy/simulation runner
scripts/        # probe_symbols, probe_history
config/settings.toml
tests/
```

---

## Architecture boundary

Strategy and research decision code should not import `duckdb`, `pyarrow`, or
`DerivClient` directly. Data access stays behind repositories/stores; live data
behind `LiveMarketDataService`.

---

## Known limitations

- No end-to-end historical **campaign** CLI -- compose APIs (or add a runner later).
- `StrategyEngine` retains growing M1/M15 lists (pre-existing 2A); long live runs
  may need a bounded-history follow-up.
- ML walk-forward measures **classification** OOS quality, not live PnL.
- Position size in 2B is research units, not broker lots.
- No Telegram, portfolio manager, or optimizer.
