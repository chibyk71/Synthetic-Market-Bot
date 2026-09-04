# Synthetic Market Bot

Research-first automated trading system for **Deriv Synthetic Indices**.

## Current milestones

| Milestone | Status | Scope |
|-----------|--------|-------|
| **1A** | Done | Deriv public WebSocket client + `active_symbols` discovery |
| **1B** | Done | Historical ticks (`ticks_history`) + pagination probe |
| **1C** | Done | Historical replay + deterministic M1/M5/M15 candles |
| **1D** | Done | Parquet dataset + DuckDB query + ingestion/validation |

No trading strategy, execution, risk engine, simulation, ML, or Telegram
integration is present.

### Target instruments (discovered dynamically)

| Config key            | Semantic name                    |
|-----------------------|----------------------------------|
| `volatility_75_1s`    | Volatility 75 (1s) Index         |
| `step_index`          | Step Index 100                   |

## Historical storage (Milestone 1D)

```
Deriv ticks_history
        ↓
Incremental ingestion
        ↓
Parquet dataset (source of truth)
        ↓
DuckDB query layer
        ↓
HistoricalReplay → M1/M5/M15 candles
```

### Layout

```
data/ticks/
  instrument={key}/
    year={YYYY}/
      month={MM}/
        part-000.parquet
```

Partitioning by instrument → year → month keeps ~15M-tick, multi-instrument
time-range queries efficient on a local machine without a data lake.

Rows: `instrument` (string), `epoch` (int64), `price` (float64).

### Duplicate policy

Identity: `(instrument, epoch, price)`. Re-ingesting the same range with
`dedupe=True` writes **zero** additional rows (deterministic).

### Query semantics

```python
repo.get_ticks(instrument, start_epoch=..., end_epoch=...)
# half-open: start_epoch <= epoch < end_epoch
# chronological ORDER BY epoch, price
```

### Developer CLI

```bash
python -m smb.data ingest --pages 3
python -m smb.data validate
python -m smb.data stats
```

Large datasets live under `data/` and are **gitignored** — download once,
validate, query repeatedly without hitting Deriv.

## Historical replay & candles (Milestone 1C)

Pipeline: HistoricalReplay (source-order) → CandleBuilder → M1/M5/M15.

UTC half-open buckets `[T, T+N)`. Gaps produce no candles. Out-of-order
ticks raise `OutOfOrderTickError`. `tick_count` is not traded volume.

## Project layout

```
src/smb/deriv/     # client, symbols, history
src/smb/market/    # replay, candles
src/smb/data/      # store, repository, ingest, validation
```

## Setup

```bash
pip install -e ".[dev]"
pytest
```

## Architecture boundary

```
Deriv API → history → Parquet + DuckDB → HistoricalReplay → CandleBuilder
```

Strategy must never import `duckdb`, `pyarrow`, or `DerivClient` directly.

## Known limitations

- No live subscriptions, strategy, indicators, risk, execution, ML, Telegram
- Six-month bulk download is incremental by design; not committed to Git
