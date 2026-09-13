# Milestone 5C — Data Coverage

## Acquisition method

- Source: Deriv public `ticks_history` (WebSocket)
- Client: `smb.deriv.history.fetch_ticks` via `smb.data.ingest.ingest_instrument`
- Storage: Parquet (`ParquetTickStore`)
- Pagination: newest → oldest, `end = earliest_epoch - 1`, ≤1000 ticks/page
- Dedup: `(epoch, price)` at write time
- Manifests: `ingest_manifests/*.json` under the data root

## Actual coverage (post multi-batch incremental ingest)

| Instrument | Tick count | Approx span | First epoch | Last epoch |
| --- | ---: | --- | ---: | ---: |
| volatility_75_1s | 330,000 | ~3.8 days | 1788979632 | 1789309638 |
| step_index | 248,000 | ~2.9 days | 1789061670 | 1789309676 |

## Integrity

- Duplicate count: 0 (after store dedupe)
- Non-monotonic source_order: 0 (after `reindex_source_order`)
- No synthetic ticks fabricated

## Rate limits

Deriv returned `You have reached the rate limit for ticks_history` during
aggressive multi-hundred-page pulls. Retry/backoff recovers partial progress;
full multi-week depth requires **scheduled multi-session** runs.

## Expanded 5B campaign (unchanged strategy)

| Metric | V75 | Step |
| --- | ---: | ---: |
| Signals | 10 | 9 |
| Filled | 5 | 7 |
| Total R | −2.0 | −1.0 |
| Interpretation | BASELINE INCONCLUSIVE | BASELINE INCONCLUSIVE |

Signal frequency remains ~2–3 signals/day. Sample size is still far below the
80–200 target. **Do not change the strategy yet** — continue coverage expansion.
