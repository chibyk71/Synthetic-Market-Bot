# Milestone 5F — Structured Baseline Diagnostic Research

## 1. Executive summary

- **source:** synthetic_demo_for_artifact_layout
- **instruments:** volatility_75_1s, step_index
- **aggregate signals:** 20
- **accepted / filled:** 20 / 15
- **TP / SL / TIMEOUT / NO_FILL:** 3 / 7 / 5 / 5
- **win rate (filled):** 0.2
- **average realized R:** -0.1
- **total realized R:** -1.0
- **sample class:** very_small

### Top observed failure modes (descriptive)
- **#1 SL:** frequency=7 share=0.35 r_impact=-7.0 — Stop-loss exits; primary source of negative realized R when present.
- **#2 NO_FILL:** frequency=5 share=0.25 r_impact=0.0 — Accepted candidates that never filled; opportunity cost, zero realized R.
- **#3 TIMEOUT:** frequency=5 share=0.25 r_impact=None — Frequent unfinished trades at horizon; MFE/MAE descriptive only — do not treat positive MFE as a missed win.
- **#4 insufficient_TP_realization:** frequency=3 share=0.15 r_impact=6.0 — Low TP count relative to filled trades reduces positive R contribution.

## 2. Integrity statement

- StrategyEngine, sweep/MSB/displacement/FVG detection, and M15 context: **unchanged**.
- TradeConstructor, entry/SL/TP geometry, RR, ATR buffer, risk-per-trade: **unchanged**.
- Simulation fill / NO_FILL / TP / SL / TIMEOUT semantics and horizon: **unchanged**.
- No filters, parameter sweeps, ML, or optimization were applied.
- All metrics derive from campaign execution data (not hard-coded baselines).

## 3. Configuration identity

```json
{
  "strategy": {
    "swing_x": 2,
    "msb_window_bars": 3,
    "displacement_body_range_ratio": 0.6,
    "displacement_body_atr_ratio": 0.8,
    "atr_period": 14
  },
  "trade": {
    "risk_per_trade": 0.01,
    "target_rr": 2.0,
    "minimum_rr": 1.5,
    "sl_atr_buffer": 0.1
  },
  "simulation": {
    "max_duration_seconds": 900
  },
  "note": "Synthetic demonstration artifacts for Milestone 5F code path; production diagnostics use CampaignRunner execution over real Parquet ticks."
}
```

## 4. Data / sample coverage

- aggregate signals: 20
- aggregate accepted: 20
- aggregate filled: 15
- aggregate sample class: very_small

### Per instrument
- **volatility_75_1s:** signals=12 accepted=12 filled=9 ticks=50000 class=very_small
- **step_index:** signals=8 accepted=8 filled=6 ticks=40000 class=very_small

## 5. Overall outcome distribution

Denominators are explicit:
- `fill_rate = filled / accepted`
- `win_rate_filled = TP / filled`
- `win_rate_all_accepted = TP / accepted`

- total_signals: 20
- accepted: 20
- filled (TP+SL+TIMEOUT): 15
- TP / SL / TIMEOUT / NO_FILL: 3 / 7 / 5 / 5
- fill_rate: 0.75
- win_rate_filled: 0.2
- win_rate_all_accepted: 0.15

## 6. Per-instrument comparison

**Warning:** instrument behavior may differ; do not pool blindly.

### volatility_75_1s
- campaign_id: `milestone-5f-volatility_75_1s`
- ticks: 50000
- period UTC: 2026-09-01T00:00:00+00:00 → 2026-09-15T00:00:00+00:00
- signals / accepted / filled: 12 / 12 / 9
- TP / SL / TIMEOUT / NO_FILL: 2 / 4 / 3 / 3
- fill_rate: 0.75
- win_rate_filled: 0.2222222222222222
- average R / total R: 0.0 / 0.0
- avg MAE / MFE: 11.777777777777779 / 10.88888888888889
- avg duration (s): 366.6666666666667

### step_index
- campaign_id: `milestone-5f-step_index`
- ticks: 40000
- period UTC: 2026-09-01T00:00:00+00:00 → 2026-09-15T00:00:00+00:00
- signals / accepted / filled: 8 / 8 / 6
- TP / SL / TIMEOUT / NO_FILL: 1 / 3 / 2 / 2
- fill_rate: 0.75
- win_rate_filled: 0.16666666666666666
- average R / total R: -0.25 / -1.0
- avg MAE / MFE: 1.3833333333333335 / 0.8666666666666667
- avg duration (s): 363.3333333333333

## 7. R distribution

Realized R follows existing simulation semantics (typically TP/SL only; TIMEOUT has no exit_price).

- count (with realized_r): 10
- total R: -1.0
- average / median R: -0.1 / -1.0
- min / max R: -1.0 / 2.0
- std R: 1.449137674618944
- positive / negative / zero: 3 / 7 / 0

### By outcome
- **sl:** {'count': 7, 'total_r': -7.0, 'average_r': -1.0, 'median_r': -1.0}
- **tp:** {'count': 3, 'total_r': 6.0, 'average_r': 2.0, 'median_r': 2.0}
- **timeout:** {'count': 5, 'total_r': None, 'average_r': None, 'median_r': None, 'note': 'TIMEOUT has no realized_r under current simulation semantics'}

## 8. MAE / MFE analysis

- aggregate MAE: {'count': 15, 'mean': 7.62, 'median': 2.0, 'minimum': 0.5, 'maximum': 18.0, 'p25': 1.8, 'p75': 14.0, 'p90': 18.0}
- aggregate MFE: {'count': 15, 'mean': 6.88, 'median': 3.0, 'minimum': 0.4, 'maximum': 25.0, 'p25': 1.0, 'p75': 12.0, 'p90': 19.799999999999997}

### By outcome (aggregate)
- MAE by outcome: {'tp': {'count': 3, 'mean': 1.5, 'median': 2.0, 'minimum': 0.5, 'maximum': 2.0, 'p25': 1.25, 'p75': 2.0, 'p90': 2.0}, 'sl': {'count': 7, 'mean': 11.057142857142859, 'median': 18.0, 'minimum': 1.8, 'maximum': 18.0, 'p25': 1.8, 'p75': 18.0, 'p90': 18.0}, 'timeout': {'count': 5, 'mean': 6.4799999999999995, 'median': 10.0, 'minimum': 1.2, 'maximum': 10.0, 'p25': 1.2, 'p75': 10.0, 'p90': 10.0}}
- MFE by outcome: {'tp': {'count': 3, 'mean': 17.333333333333332, 'median': 25.0, 'minimum': 2.0, 'maximum': 25.0, 'p25': 13.5, 'p75': 25.0, 'p90': 25.0}, 'sl': {'count': 7, 'mean': 1.8857142857142857, 'median': 3.0, 'minimum': 0.4, 'maximum': 3.0, 'p25': 0.4, 'p75': 3.0, 'p90': 3.0}, 'timeout': {'count': 5, 'mean': 7.6, 'median': 12.0, 'minimum': 1.0, 'maximum': 12.0, 'p25': 1.0, 'p75': 12.0, 'p90': 12.0}}

TIMEOUT MFE is descriptive only — positive MFE does **not** imply the trade should have been a win.

## 9. Duration analysis

- aggregate: {'count': 15, 'mean': 365.3333333333333, 'median': 100.0, 'minimum': 100.0, 'maximum': 900.0, 'near_horizon_count': 5, 'horizon_seconds': 900}

TIMEOUT trades near the simulation horizon (900s) indicate unfinished paths, not a mandate to change the horizon in this milestone.

## 10. Direction analysis

- **LONG:** signals=10 filled=10 TP/SL/TO/NF=3/7/0/0 WR=0.3 avgR=-0.1 totalR=-1.0 class=very_small
  - warning: LONG: n=10 is very small (<30); insufficient for directional or causal conclusions.
- **SHORT:** signals=10 filled=5 TP/SL/TO/NF=0/0/5/5 WR=0.0 avgR=None totalR=None class=very_small
  - warning: SHORT: n=10 is very small (<30); insufficient for directional or causal conclusions.

## 11. M15 context analysis

Uses existing `m15_context.directional_bias` only — no new classifications.

- **bullish:** signals=20 filled=15 TP/SL/TO/NF=3/7/5/5 WR=0.2 avgR=-0.1 totalR=-1.0 class=very_small
  - warning: bullish: n=20 is very small (<30); insufficient for directional or causal conclusions.

## 12. Signal characteristic analysis

Only fields already recorded by StrategyEngine / TradeCandidate are summarized.

```json
{
  "aggregate": {
    "available": true,
    "displacement_body_atr_ratio": {
      "count": 20,
      "mean": 1.1,
      "median": 1.1,
      "min": 1.1,
      "max": 1.1
    },
    "displacement_body_range_ratio": {
      "count": 20,
      "mean": 0.65,
      "median": 0.65,
      "min": 0.65,
      "max": 0.65
    },
    "fvg_size": {
      "count": 20,
      "mean": 3.0,
      "median": 3.0,
      "min": 3.0,
      "max": 3.0
    },
    "fvg_size_atr_ratio": {
      "count": 20,
      "mean": 0.4,
      "median": 0.4,
      "min": 0.4,
      "max": 0.4
    },
    "msb_bars_after_sweep": {
      "count": 20,
      "mean": 1.0,
      "median": 1.0
    },
    "entry_to_stop_distance": {
      "count": 0
    },
    "entry_to_target_distance": {
      "count": 0
    },
    "risk_reward": {
      "count": 0
    },
    "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
  },
  "per_instrument": {
    "volatility_75_1s": {
      "available": true,
      "displacement_body_atr_ratio": {
        "count": 12,
        "mean": 1.1,
        "median": 1.1,
        "min": 1.1,
        "max": 1.1
      },
      "displacement_body_range_ratio": {
        "count": 12,
        "mean": 0.65,
        "median": 0.65,
        "min": 0.65,
        "max": 0.65
      },
      "fvg_size": {
        "count": 12,
        "mean": 3.0,
        "median": 3.0,
        "min": 3.0,
        "max": 3.0
      },
      "fvg_size_atr_ratio": {
        "count": 12,
        "mean": 0.4000000000000001,
        "median": 0.4,
        "min": 0.4,
        "max": 0.4
      },
      "msb_bars_after_sweep": {
        "count": 12,
        "mean": 1.0,
        "median": 1.0
      },
      "entry_to_stop_distance": {
        "count": 0
      },
      "entry_to_target_distance": {
        "count": 0
      },
      "risk_reward": {
        "count": 0
      },
      "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
    },
    "step_index": {
      "available": true,
      "displacement_body_atr_ratio": {
        "count": 8,
        "mean": 1.1,
        "median": 1.1,
        "min": 1.1,
        "max": 1.1
      },
      "displacement_body_range_ratio": {
        "count": 8,
        "mean": 0.65,
        "median": 0.65,
        "min": 0.65,
        "max": 0.65
      },
      "fvg_size": {
        "count": 8,
        "mean": 3.0,
        "median": 3.0,
        "min": 3.0,
        "max": 3.0
      },
      "fvg_size_atr_ratio": {
        "count": 8,
        "mean": 0.4,
        "median": 0.4,
        "min": 0.4,
        "max": 0.4
      },
      "msb_bars_after_sweep": {
        "count": 8,
        "mean": 1.0,
        "median": 1.0
      },
      "entry_to_stop_distance": {
        "count": 0
      },
      "entry_to_target_distance": {
        "count": 0
      },
      "risk_reward": {
        "count": 0
      },
      "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
    }
  }
}
```

## 13. Failure-mode analysis

Ranking considers frequency and R impact separately. A mode can be frequent with little realized-R impact (e.g. NO_FILL) or less frequent with large impact (SL).

1. **SL** — freq=7, share=0.35, r_impact=-7.0, instruments=['volatility_75_1s', 'step_index']
   - Stop-loss exits; primary source of negative realized R when present.
2. **NO_FILL** — freq=5, share=0.25, r_impact=0.0, instruments=['volatility_75_1s', 'step_index']
   - Accepted candidates that never filled; opportunity cost, zero realized R.
3. **TIMEOUT** — freq=5, share=0.25, r_impact=None, instruments=['volatility_75_1s', 'step_index']
   - Frequent unfinished trades at horizon; MFE/MAE descriptive only — do not treat positive MFE as a missed win.
4. **insufficient_TP_realization** — freq=3, share=0.15, r_impact=6.0, instruments=['volatility_75_1s', 'step_index']
   - Low TP count relative to filled trades reduces positive R contribution.

## 14. Sample-size warnings

Thresholds (research interpretation only):
- <30: very_small
- 30–79: small
- 80–199: preliminary
- ≥200: stronger

- aggregate: n=20 is very small (<30); insufficient for directional or causal conclusions.
- volatility_75_1s overall: n=12 is very small (<30); insufficient for directional or causal conclusions.
- LONG: n=6 is very small (<30); insufficient for directional or causal conclusions.
- SHORT: n=6 is very small (<30); insufficient for directional or causal conclusions.
- bullish: n=12 is very small (<30); insufficient for directional or causal conclusions.
- step_index overall: n=8 is very small (<30); insufficient for directional or causal conclusions.
- LONG: n=4 is very small (<30); insufficient for directional or causal conclusions.
- SHORT: n=4 is very small (<30); insufficient for directional or causal conclusions.
- bullish: n=8 is very small (<30); insufficient for directional or causal conclusions.
- Instrument behavior may differ; comparison is side-by-side, not a pooled portfolio.

## 15. Limitations

- Diagnostics are observational only; association is not causation.
- Small subgroups must not be used to declare a configuration profitable or invalid.
- TIMEOUT has no realized exit price under current simulation semantics; MFE on timeouts is descriptive only.
- Instrument behavior may differ substantially; do not pool blindly.
- Strategy, trade construction, and simulation were frozen for this analysis.
- No live or demo execution was performed.

## 16. Research interpretation

Observations above are descriptive. Associations in small samples are hypotheses for further testing, not evidence of causality and not a license to change the frozen baseline.

## 17. Recommendation for next milestone

Continue research-only investigation of the frozen baseline. Do not deploy live/demo and do not modify strategy parameters based on this diagnostic alone. Primary next step: expand historical coverage further to reach preliminary/campaign-scale samples per instrument before any design change. Hypothesis for later testing (not implementation): stop placement and adverse excursion relative to structure under frozen geometry. Observed average realized R is negative in this sample; treat as evidence of baseline weakness under current data, not a mandate to curve-fit. Any promising subgroup should be recorded as a research hypothesis only.

---
*Generated at 2026-09-15T02:54:35.385903+00:00 · analysis_version=milestone-5f-v1*