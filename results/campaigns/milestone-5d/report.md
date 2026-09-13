# Milestone 5D — Expanded Historical Baseline Rerun

## Integrity statement

**Strategy changes between 5B and 5D: none.**

This report is an observational rerun of the published 5B baseline
configuration over the expanded historical tick coverage from Milestone 5C.
No StrategyEngine, TradeConstructor, risk, fill, timeout, or NO_FILL
semantics were modified.

### Configuration identity

- strategy: `{'swing_x': 2, 'msb_window_bars': 3, 'displacement_body_range_ratio': 0.6, 'displacement_body_atr_ratio': 0.8, 'atr_period': 14}`
- trade: `{'risk_per_trade': 0.01, 'target_rr': 2.0, 'minimum_rr': 1.5, 'sl_atr_buffer': 0.1}`
- simulation: `{'max_duration_seconds': 900}`

## Research notes

- Milestone 5D is an unchanged baseline rerun over expanded historical coverage.
- Strategy, trade construction, risk, and simulation semantics were not modified.
- Aggregate candidate signals: 5B=15 → 5D=19.
- Aggregate total R across instruments (5D): -3.0.
- Sample remains below the 80–200 signal campaign-scale target per instrument.
- Negative / flat total R persists; do not interpret as definitive strategy failure.
- Instrument behavior differs (e.g. V75 MAE vs Step); do not pool blindly.

## Per-instrument comparison (5B → 5D)

### volatility_75_1s

- strategy_unchanged: **True**
- 5B coverage: ~2.5–3 days (2026-09-10 → 2026-09-13 UTC)
- 5D coverage: ~3.8 days; epochs 1788979632 → 1789309638

| Metric | 5B | 5D | Change |
| --- | --- | --- | --- |
| ticks | 255000 | 330000 | +75000 |
| signals | 7 | 10 | +3 |
| accepted | 7 | 10 | +3 |
| filled | 4 | 5 | +1 |
| wins | 0 | 0 | +0 |
| losses | 2 | 2 | +0 |
| timeouts | 2 | 3 | +1 |
| no_fills | 3 | 5 | +2 |
| win_rate | 0.0000 | 0.0000 | +0.0000 |
| average_r | -1.0000 | -1.0000 | +0.0000 |
| total_r | -2.0000 | -2.0000 | +0.0000 |
| average_mae | 6.3412 | None | — |
| average_mfe | 14.5013 | None | — |
| average_duration_seconds | 383.0000 | None | — |

- 5D interpretation: **BASELINE INCONCLUSIVE**
- 5D sample_scale: small_sample

### step_index

- strategy_unchanged: **True**
- 5B coverage: ~2.5–3 days (2026-09-10 → 2026-09-13 UTC)
- 5D coverage: ~2.9 days; epochs 1789061670 → 1789309676

| Metric | 5B | 5D | Change |
| --- | --- | --- | --- |
| ticks | 225000 | 248000 | +23000 |
| signals | 8 | 9 | +1 |
| accepted | 8 | 9 | +1 |
| filled | 6 | 7 | +1 |
| wins | 1 | 1 | +0 |
| losses | 3 | 3 | +0 |
| timeouts | 2 | 3 | +1 |
| no_fills | 2 | 2 | +0 |
| win_rate | 0.1667 | 0.1429 | -0.0238 |
| average_r | -0.2500 | -0.2500 | +0.0000 |
| total_r | -1.0000 | -1.0000 | +0.0000 |
| average_mae | 1.3500 | None | — |
| average_mfe | 1.2833 | None | — |
| average_duration_seconds | 408.0000 | None | — |

- 5D interpretation: **BASELINE INCONCLUSIVE**
- 5D sample_scale: small_sample

## Recommendation

Expanded sample still small. Continue historical coverage expansion (scheduled multi-session ingest) before any strategy diagnostic changes. Do not optimize or add filters based on this sample.

---

*Do not claim readiness for live/demo trading. Next milestone is decided*
*after reviewing this evidence.*
