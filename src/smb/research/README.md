# Research package

Observer-only research layer for Synthetic Market Bot.

## Modules

| Module | Role |
|--------|------|
| `experiment` | Historical harness: ticks → strategy → trade → simulation → metrics |
| `campaign` | Milestone **5A** campaign runner: experiment + persistent artifacts |
| `metrics` / `models` | MAE/MFE path metrics over simulated trades |
| `baseline` | Milestone **3B** baseline analysis report |
| `diagnostic` | Milestone **3C** baseline **diagnostic** research |
| `stats` | Deterministic type-7 percentiles (stdlib only) |

## Milestone 5A — Historical research campaign runner

**Purpose:** one deterministic orchestration layer that runs the full historical
pipeline and persists a self-contained campaign artifact set.

**Not in scope:** strategy changes, filters, optimization, walk-forward, live/demo.

### Artifacts (per campaign)

```
<output_dir>/
  ├── manifest.json      # identity + configs + git commit (if available)
  ├── summary.json       # aggregate counts / R / drawdown
  ├── trades.parquet     # per-trade research rows
  ├── diagnostics.json   # 3C diagnostic report (when experiment runs)
  └── report.md          # human-readable summary
```

### CLI

```bash
python -m smb.research run-campaign \
  --instrument volatility_75_1s \
  --output results/campaigns/v75_2024H1 \
  --start 1704067200 \
  --end 1719792000
```

Programmatic API:

```python
from smb.research import CampaignConfig, CampaignRunner, run_campaign

results = run_campaign(
    "data",
    instrument="volatility_75_1s",
    output_dir="results/campaigns/example",
)
```

Simulation still requires tick data; the campaign composes
`HistoricalResearchExperiment` (tick stream → candles → strategy → …).

## Milestone 3C — Baseline diagnostic research

**Purpose:** explain characteristics of an existing baseline experiment (e.g. −5R)
without changing strategy, trade construction, or simulation semantics.

**Not in scope:** parameter optimization, filters, ML, look-ahead labels, live execution.

### Semantics preserved

- `filled = TP + SL + TIMEOUT`
- Realized R from **TP and SL only** (TIMEOUT has no exit price by design)
- `NO_FILL` is not a losing trade; `TIMEOUT` is not marked to market
- MAE/MFE only for filled trades

### Sample-size labels

Heuristic only (not p-values):

| n | status |
|---|--------|
| < 5 | `insufficient_sample` |
| 5–14 | `descriptive_only` |
| ≥ 15 | `usable_for_hypothesis` |

### Chronological / leakage boundary

Diagnostics summarize the **completed** baseline result set. They do not shuffle
trades, invent future-based labels, or normalize using future information.

### CLI (experiment + analysis)

```bash
# Concise experiment summary (unchanged)
python -m smb.research run --instrument volatility_75_1s

# 3B analysis
python -m smb.research run --instrument volatility_75_1s --analysis

# 3C diagnostic
python -m smb.research run --instrument volatility_75_1s --diagnostic
python -m smb.research run --instrument volatility_75_1s --diagnostic-json artifacts/diagnostic_v75.json
```

The baseline remains the control/reference for later hypothesis-testing milestones.
