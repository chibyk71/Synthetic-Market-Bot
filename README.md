# Synthetic Market Bot

Research-first automated trading system for **Deriv Synthetic Indices**.

## Current milestones

| Milestone | Status | Scope |
|-----------|--------|-------|
| **1A** | Done | Deriv public WebSocket client + `active_symbols` discovery |
| **1B** | Done | Historical ticks (`ticks_history`) + pagination probe |
| **1C** | Done | Historical replay + deterministic M1/M5/M15 candles |
| **1D** | Done | Parquet dataset + DuckDB query + ingestion/validation |
| **1E** | Done | Historical candle dataset (build/persist/query M1/M5/M15) |

No trading strategy, execution, risk engine, simulation, ML, or Telegram
integration is present.

### Target instruments (discovered dynamically)

| Config key            | Semantic name                    |
|-----------------------|----------------------------------|
| `volatility_75_1s`    | Volatility 75 (1s) Index         |
| `step_index`          | Step Index 100                   |

## Historical candles (Milestone 1E)

Build deterministic M1/M5/M15 candles from the stored tick dataset and
persist them for efficient research queries without replaying all ticks:

```
TickRepository → HistoricalReplay → MultiTimeframeCandleBuilder
        ↓
Parquet candles → DuckDB range query
```

```bash
python -m smb.data build-candles
python -m smb.data candle-stats
```

Layout:

```
data/candles/
  instrument={key}/
    timeframe={M1|M5|M15}/
      year={YYYY}/month={MM}/part-000.parquet
```

Same OHLC boundary semantics as Milestone 1C. Rebuilds replace candles
with matching `start_epoch` (deterministic, no duplicates).

## Historical storage (Milestone 1D)

```
Deriv ticks_history → incremental ingestion → Parquet ticks → DuckDB
```

```bash
python -m smb.data ingest --pages 3
python -m smb.data validate
python -m smb.data stats
```

Large datasets under `data/` are **gitignored**.

## Historical replay & candles (Milestone 1C)

`HistoricalReplay` → `CandleBuilder` → M1/M5/M15. Half-open UTC buckets
`[T, T+N)`. Gaps produce no candles. Out-of-order ticks raise
`OutOfOrderTickError`. `tick_count` is not traded volume.

## Project layout

```
src/smb/deriv/     # client, symbols, history
src/smb/market/    # replay, candles
src/smb/data/      # tick store, candle store, ingest, validation
```

## Setup

```bash
pip install -e ".[dev]"
pytest
```

## Architecture boundary

```
Deriv → ticks Parquet → candles Parquet → HistoricalReplay / candle queries
```

Strategy must never import `duckdb`, `pyarrow`, or `DerivClient` directly.

## Known limitations

- No live subscriptions, strategy, indicators, risk, execution, ML, Telegram
- Six-month bulk download is incremental; not committed to Git
