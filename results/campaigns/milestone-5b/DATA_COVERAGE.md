# Data coverage for Milestone 5B campaigns

Source: Deriv public ticks_history via `python -m smb.data ingest` into `/tmp/smb-data`
(local workspace had no pre-existing Parquet history under `data/`).

| Instrument | Semantic key | Ticks | Earliest UTC | Latest UTC | Duplicates | Non-monotonic |
| --- | --- | --- | --- | --- | --- | --- |
| Volatility 75 (1s) | `volatility_75_1s` | 255000 | 2026-09-10T13:30:43Z | 2026-09-13T12:20:49Z | 0 | 0 |
| Step Index 100 | `step_index` | 225000 | 2026-09-10T21:51:05Z | 2026-09-13T12:21:11Z | 0 | 0 |

Approx. span: ~2.5–3 days of 1-second ticks per instrument.

**Sample-size note:** Despite ~250k ticks, the baseline strategy produced only single-digit
signals per instrument. This remains **small-sample evidence**, not campaign-scale.
Expanding history is a research prerequisite before strategy changes.
