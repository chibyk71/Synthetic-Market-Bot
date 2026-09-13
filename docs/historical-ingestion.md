# Historical tick ingestion (Milestone 5C)

## Purpose

Resumable, idempotent acquisition of Deriv 1s synthetic tick history into the
Parquet store so research campaigns can reach meaningful sample sizes.

## CLI

```bash
python -m smb.data ingest --instrument volatility_75_1s --pages 50
python -m smb.data ingest --instrument step_index --pages 50 --retries 5
python -m smb.data ingest --instrument volatility_75_1s --end latest --start 1788000000
python -m smb.data validate
python -m smb.data stats
```

### Options

| Flag | Meaning |
| --- | --- |
| `--instrument` | Config key (`volatility_75_1s`, `step_index`) |
| `--pages` | Max API pages (≤1000 ticks each) |
| `--count-per-page` | Request size (capped at 1000) |
| `--end` | Cursor: `latest` or epoch; omit to resume from oldest−1 |
| `--start` | Optional inclusive lower epoch; stop when reached |
| `--retries` | Transient failure retries (exponential backoff) |
| `--no-manifest` | Skip writing `ingest_manifests/*.json` |

## Resume semantics

1. Omit `--end` → if data exists, cursor = `oldest_epoch - 1` (extend backward).
2. Each page is persisted before the next request (bounded memory).
3. Overlapping boundary ticks are deduplicated by `(epoch, price)`.
4. After writes, `reindex_source_order` restores chronological `source_order`.
5. Interrupted runs: re-run the same command; already-stored ticks are skipped.

## Manifests

Each run writes JSON under `{data_root}/ingest_manifests/` with:

- requested vs resolved end
- start bound
- pages / ticks received / persisted / duplicates skipped
- retries and errors
- coverage before and after

## Integrity

`python -m smb.data validate` reports duplicate and non-monotonic counts.
Never fabricate ticks to fill gaps — Deriv history can be irregular.

## Rate limits

The public API rate-limits `ticks_history`. Prefer moderate `--pages` with
`--retries` and multi-session schedules over single multi-hour pulls.
