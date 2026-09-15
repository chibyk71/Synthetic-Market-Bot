# Milestone 5D - Expanded Historical Baseline Rerun

## Integrity statement

**Strategy changes between 5B and 5D: none.**

This report is an observational rerun of the published 5B baseline
configuration over expanded historical tick coverage.
No StrategyEngine, TradeConstructor, risk, fill, timeout, or NO_FILL
semantics were modified.

**Metrics source:** `campaign_execution` (CampaignRunner -> CampaignBaselineAnalyzer).

### Configuration identity

- strategy: `{'swing_x': 2, 'msb_window_bars': 3, 'displacement_body_range_ratio': 0.6, 'displacement_body_atr_ratio': 0.8, 'atr_period': 14}`
- trade: `{'risk_per_trade': 0.01, 'target_rr': 2.0, 'minimum_rr': 1.5, 'sl_atr_buffer': 0.1}`
- simulation: `{'max_duration_seconds': 900}`

## Research notes

- Milestone 5D is an unchanged baseline rerun over expanded historical coverage.
- Strategy, trade construction, risk, and simulation semantics were not modified.
- 5D metrics were produced by CampaignRunner -> CampaignBaselineAnalyzer (not hard-coded constants).
- Aggregate candidate signals: 5B=15 -> 5D=148.
- Aggregate total R across instruments (5D): -15.0.
- Instrument behavior may differ; do not pool blindly.

## Per-instrument comparison (5B -> 5D)

### volatility_75_1s

- strategy_unchanged: **True**
- 5B coverage: ~2.5-3 days (2026-09-10 -> 2026-09-13 UTC)
- 5D coverage: None -> None

| Metric | 5B | 5D | Change |
| --- | --- | --- | --- |
| ticks | 255000 | 2973000 | +2718000 |
| signals | 7 | 89 | +82 |
| accepted | 7 | 89 | +82 |
| filled | 4 | 47 | +43 |
| wins | 0 | 5 | +5 |
| losses | 2 | 16 | +14 |
| timeouts | 2 | 26 | +24 |
| no_fills | 3 | 42 | +39 |
| win_rate | 0.0000 | 0.1064 | +0.1064 |
| average_r | -1.0000 | -0.2857 | +0.7143 |
| total_r | -2.0000 | -6.0000 | -4.0000 |
| average_mae | 6.3412 | 16.0085 | +9.6673 |
| average_mfe | 14.5013 | 16.3750 | +1.8737 |
| average_duration_seconds | 383.0000 | 511.4255 | +128.4255 |

- 5D interpretation: **BASELINE WEAK**
- 5D sample_scale: borderline

### step_index

- strategy_unchanged: **True**
- 5B coverage: ~2.5-3 days (2026-09-10 -> 2026-09-13 UTC)
- 5D coverage: None -> None

| Metric | 5B | 5D | Change |
| --- | --- | --- | --- |
| ticks | 225000 | 2078000 | +1853000 |
| signals | 8 | 59 | +51 |
| accepted | 8 | 59 | +51 |
| filled | 6 | 34 | +28 |
| wins | 1 | 1 | +0 |
| losses | 3 | 11 | +8 |
| timeouts | 2 | 22 | +20 |
| no_fills | 2 | 25 | +23 |
| win_rate | 0.1667 | 0.0294 | -0.1373 |
| average_r | -0.2500 | -0.7500 | -0.5000 |
| total_r | -1.0000 | -9.0000 | -8.0000 |
| average_mae | 1.3500 | 1.5897 | +0.2397 |
| average_mfe | 1.2833 | 1.4662 | +0.1829 |
| average_duration_seconds | 408.0000 | 523.7647 | +115.7647 |

- 5D interpretation: **BASELINE INCONCLUSIVE**
- 5D sample_scale: small_sample

## Recommendation

Sample has grown; proceed to structured diagnostic analysis of outcome drivers (timeout vs SL vs no-fill, direction/context) without changing entry logic yet.

---

*Do not claim readiness for live/demo trading. Next milestone is decided*
*after reviewing this evidence.*
