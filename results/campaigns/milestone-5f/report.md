# Milestone 5F — Structured Baseline Diagnostic Research

## 1. Executive summary

- **source:** campaign_execution
- **instruments:** volatility_75_1s, step_index
- **aggregate signals:** 148
- **accepted / filled:** 148 / 81
- **TP / SL / TIMEOUT / NO_FILL:** 6 / 27 / 48 / 67
- **win rate (filled):** 0.07407407407407407
- **average realized R:** -0.45454545454545453
- **total realized R:** -15.0
- **sample class:** preliminary

### Top observed failure modes (descriptive)
- **#1 NO_FILL:** frequency=67 share=0.4527027027027027 r_impact=0.0 — Accepted candidates that never filled; opportunity cost, zero realized R.
- **#2 TIMEOUT:** frequency=48 share=0.32432432432432434 r_impact=None — Frequent unfinished trades at horizon; MFE/MAE descriptive only — do not treat positive MFE as a missed win.
- **#3 SL:** frequency=27 share=0.18243243243243243 r_impact=-27.0 — Stop-loss exits; primary source of negative realized R when present.
- **#4 insufficient_TP_realization:** frequency=6 share=0.04054054054054054 r_impact=12.0 — Low TP count relative to filled trades reduces positive R contribution.

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
  }
}
```

## 4. Data / sample coverage

- aggregate signals: 148
- aggregate accepted: 148
- aggregate filled: 81
- aggregate sample class: preliminary

### Per instrument
- **volatility_75_1s:** signals=89 accepted=89 filled=47 ticks=2973000 class=preliminary
- **step_index:** signals=59 accepted=59 filled=34 ticks=2078000 class=small

## 5. Overall outcome distribution

Denominators are explicit:
- `fill_rate = filled / accepted`
- `win_rate_filled = TP / filled`
- `win_rate_all_accepted = TP / accepted`

- total_signals: 148
- accepted: 148
- filled (TP+SL+TIMEOUT): 81
- TP / SL / TIMEOUT / NO_FILL: 6 / 27 / 48 / 67
- fill_rate: 0.5472972972972973
- win_rate_filled: 0.07407407407407407
- win_rate_all_accepted: 0.04054054054054054

## 6. Per-instrument comparison

**Warning:** instrument behavior may differ; do not pool blindly.

### volatility_75_1s
- campaign_id: `milestone-5f-volatility_75_1s`
- ticks: 2973000
- period UTC: None → None
- signals / accepted / filled: 89 / 89 / 47
- TP / SL / TIMEOUT / NO_FILL: 5 / 16 / 26 / 42
- fill_rate: 0.5280898876404494
- win_rate_filled: 0.10638297872340426
- average R / total R: -0.2857142857142857 / -6.0
- avg MAE / MFE: 16.00851063829798 / 16.375000000000078
- avg duration (s): 511.4255319148936

### step_index
- campaign_id: `milestone-5f-step_index`
- ticks: 2078000
- period UTC: None → None
- signals / accepted / filled: 59 / 59 / 34
- TP / SL / TIMEOUT / NO_FILL: 1 / 11 / 22 / 25
- fill_rate: 0.576271186440678
- win_rate_filled: 0.029411764705882353
- average R / total R: -0.75 / -9.0
- avg MAE / MFE: 1.5897058823529733 / 1.4661764705882996
- avg duration (s): 523.7647058823529

## 7. R distribution

Realized R follows existing simulation semantics (typically TP/SL only; TIMEOUT has no exit_price).

- count (with realized_r): 33
- total R: -15.0
- average / median R: -0.45454545454545453 / -1.0
- min / max R: -1.0 / 2.0
- std R: 1.1750241777009605
- positive / negative / zero: 6 / 27 / 0

### By outcome
- **sl:** {'count': 27, 'total_r': -27.0, 'average_r': -1.0, 'median_r': -1.0}
- **tp:** {'count': 6, 'total_r': 12.0, 'average_r': 2.0, 'median_r': 2.0}
- **timeout:** {'count': 48, 'total_r': None, 'average_r': None, 'median_r': None, 'note': 'TIMEOUT has no realized_r under current simulation semantics'}

## 8. MAE / MFE analysis

- aggregate MAE: {'count': 81, 'mean': 9.95617283950625, 'median': 3.6500000000005457, 'minimum': 0.0, 'maximum': 42.64500000000044, 'p25': 1.699999999999818, 'p75': 16.80499999999938, 'p90': 26.460000000000036}
- aggregate MFE: {'count': 81, 'mean': 10.116975308642047, 'median': 3.3299999999999272, 'minimum': 0.0, 'maximum': 63.399999999999636, 'p25': 1.25, 'p75': 14.175000000001091, 'p90': 29.55500000000029}

### By outcome (aggregate)
- MAE by outcome: {'tp': {'count': 6, 'mean': 5.585000000000036, 'median': 5.350000000000364, 'minimum': 0.1499999999996362, 'maximum': 13.43499999999949, 'p25': 1.7437499999998636, 'p75': 7.850000000000591, 'p90': 10.837500000000091}, 'sl': {'count': 27, 'mean': 15.487407407407503, 'median': 16.80499999999938, 'minimum': 0.6500000000005457, 'maximum': 42.64500000000044, 'p25': 2.899999999999636, 'p75': 25.117499999999836, 'p90': 30.04200000000019}, 'timeout': {'count': 48, 'mean': 7.3912500000000705, 'median': 2.432499999999436, 'minimum': 0.0, 'maximum': 37.284999999999854, 'p25': 1.1000000000001364, 'p75': 11.501250000000027, 'p90': 20.339000000000237}}
- MFE by outcome: {'tp': {'count': 6, 'mean': 31.15666666666675, 'median': 27.672500000000127, 'minimum': 4.75, 'maximum': 63.399999999999636, 'p25': 22.52375000000029, 'p75': 38.89625000000024, 'p90': 52.70499999999993}, 'sl': {'count': 27, 'mean': 3.483518518518529, 'median': 1.3499999999994543, 'minimum': 0.0, 'maximum': 14.175000000001091, 'p25': 0.2699999999999818, 'p75': 5.364999999999782, 'p90': 10.439999999999966}, 'timeout': {'count': 48, 'mean': 11.218333333333439, 'median': 4.125, 'minimum': 0.1000000000003638, 'maximum': 53.13500000000022, 'p25': 1.5875000000003183, 'p75': 17.494999999999436, 'p90': 31.251000000000477}}

TIMEOUT MFE is descriptive only — positive MFE does **not** imply the trade should have been a win.

## 9. Duration analysis

- aggregate: {'count': 81, 'mean': 516.604938271605, 'median': 563.0, 'minimum': 4.0, 'maximum': 894.0, 'near_horizon_count': 7, 'horizon_seconds': 900}

TIMEOUT trades near the simulation horizon (900s) indicate unfinished paths, not a mandate to change the horizon in this milestone.

## 10. Direction analysis

- **long:** signals=76 filled=42 TP/SL/TO/NF=1/14/27/34 WR=0.023809523809523808 avgR=-0.8 totalR=-12.0 class=small
  - warning: long: n=76 is small (30–79); treat findings as hypotheses only.
- **short:** signals=72 filled=39 TP/SL/TO/NF=5/13/21/33 WR=0.1282051282051282 avgR=-0.16666666666666666 totalR=-3.0 class=small
  - warning: short: n=72 is small (30–79); treat findings as hypotheses only.

## 11. M15 context analysis

Uses existing `m15_context.directional_bias` only — no new classifications.

- **bearish:** signals=69 filled=37 TP/SL/TO/NF=4/10/23/32 WR=0.10810810810810811 avgR=-0.14285714285714285 totalR=-2.0 class=small
  - warning: bearish: n=69 is small (30–79); treat findings as hypotheses only.
- **bullish:** signals=79 filled=44 TP/SL/TO/NF=2/17/25/35 WR=0.045454545454545456 avgR=-0.6842105263157895 totalR=-13.0 class=small
  - warning: bullish: n=79 is small (30–79); treat findings as hypotheses only.

## 12. Signal characteristic analysis

Only fields already recorded by StrategyEngine / TradeCandidate are summarized.

```json
{
  "aggregate": {
    "available": true,
    "displacement_body_atr_ratio": {
      "count": 148,
      "mean": 1.1269211306194646,
      "median": 1.0547945205478646,
      "min": 0.802083333332867,
      "max": 1.8320468839337036
    },
    "displacement_body_range_ratio": {
      "count": 148,
      "mean": 0.8592114562358558,
      "median": 0.8666666666667879,
      "min": 0.6041156295933373,
      "max": 1.0
    },
    "fvg_size": {
      "count": 148,
      "mean": 3.6420945945945804,
      "median": 1.7350000000001273,
      "min": 0.0999999999994543,
      "max": 19.200000000000728
    },
    "fvg_size_atr_ratio": {
      "count": 148,
      "mean": 0.5705478098407145,
      "median": 0.5325281877183403,
      "min": 0.024013722126907666,
      "max": 1.9012345679010987
    },
    "msb_bars_after_sweep": {
      "count": 148,
      "mean": 1.945945945945946,
      "median": 2.0
    },
    "entry_to_stop_distance": {
      "count": 148,
      "mean": 14.452666988416953,
      "median": 14.662107142857622,
      "min": 0.5714285714284415,
      "max": 48.90192857142847
    },
    "entry_to_target_distance": {
      "count": 148,
      "mean": 28.905333976833905,
      "median": 29.324214285715243,
      "min": 1.142857142856883,
      "max": 97.80385714285694
    },
    "risk_reward": {
      "count": 148,
      "mean": 2.0,
      "median": 2.0,
      "min": 2.0,
      "max": 2.0
    },
    "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
  },
  "per_instrument": {
    "volatility_75_1s": {
      "available": true,
      "displacement_body_atr_ratio": {
        "count": 89,
        "mean": 1.1485153234087657,
        "median": 1.0750281531531394,
        "min": 0.8075997248968364,
        "max": 1.8320468839337036
      },
      "displacement_body_range_ratio": {
        "count": 89,
        "mean": 0.8614433991891828,
        "median": 0.8642638036810175,
        "min": 0.6041156295933373,
        "max": 1.0
      },
      "fvg_size": {
        "count": 89,
        "mean": 5.539662921348353,
        "median": 4.480000000000473,
        "min": 0.23999999999978172,
        "max": 19.200000000000728
      },
      "fvg_size_atr_ratio": {
        "count": 89,
        "mean": 0.509924372686902,
        "median": 0.41839916839920127,
        "min": 0.024013722126907666,
        "max": 1.867229296963271
      },
      "msb_bars_after_sweep": {
        "count": 89,
        "mean": 1.9662921348314606,
        "median": 2.0
      },
      "entry_to_stop_distance": {
        "count": 89,
        "mean": 22.479675762439737,
        "median": 22.247142857143444,
        "min": 5.7324285714285,
        "max": 48.90192857142847
      },
      "entry_to_target_distance": {
        "count": 89,
        "mean": 44.95935152487947,
        "median": 44.49428571428689,
        "min": 11.464857142857,
        "max": 97.80385714285694
      },
      "risk_reward": {
        "count": 89,
        "mean": 2.0,
        "median": 2.0,
        "min": 2.0,
        "max": 2.0
      },
      "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
    },
    "step_index": {
      "available": true,
      "displacement_body_atr_ratio": {
        "count": 59,
        "mean": 1.0943468398017056,
        "median": 1.028248587570723,
        "min": 0.802083333332867,
        "max": 1.738562091503643
      },
      "displacement_body_range_ratio": {
        "count": 59,
        "mean": 0.8558446270350747,
        "median": 0.8666666666667879,
        "min": 0.642857142857282,
        "max": 1.0
      },
      "fvg_size": {
        "count": 59,
        "mean": 0.7796610169490601,
        "median": 0.6999999999998181,
        "min": 0.0999999999994543,
        "max": 2.199999999999818
      },
      "fvg_size_atr_ratio": {
        "count": 59,
        "mean": 0.6619967235134149,
        "median": 0.6473988439307422,
        "min": 0.08433734939713752,
        "max": 1.9012345679010987
      },
      "msb_bars_after_sweep": {
        "count": 59,
        "mean": 1.9152542372881356,
        "median": 2.0
      },
      "entry_to_stop_distance": {
        "count": 59,
        "mean": 2.3441283292978383,
        "median": 2.3314285714277503,
        "min": 0.5714285714284415,
        "max": 4.880000000000109
      },
      "entry_to_target_distance": {
        "count": 59,
        "mean": 4.688256658595677,
        "median": 4.662857142855501,
        "min": 1.142857142856883,
        "max": 9.760000000000218
      },
      "risk_reward": {
        "count": 59,
        "mean": 2.0,
        "median": 2.0,
        "min": 2.0,
        "max": 2.0
      },
      "limitation": "Only fields already present on StrategySignal / TradeCandidate are summarized; no new strategy logic was added."
    }
  }
}
```

## 13. Failure-mode analysis

Ranking considers frequency and R impact separately. A mode can be frequent with little realized-R impact (e.g. NO_FILL) or less frequent with large impact (SL).

1. **NO_FILL** — freq=67, share=0.4527027027027027, r_impact=0.0, instruments=['volatility_75_1s', 'step_index']
   - Accepted candidates that never filled; opportunity cost, zero realized R.
2. **TIMEOUT** — freq=48, share=0.32432432432432434, r_impact=None, instruments=['volatility_75_1s', 'step_index']
   - Frequent unfinished trades at horizon; MFE/MAE descriptive only — do not treat positive MFE as a missed win.
3. **SL** — freq=27, share=0.18243243243243243, r_impact=-27.0, instruments=['volatility_75_1s', 'step_index']
   - Stop-loss exits; primary source of negative realized R when present.
4. **insufficient_TP_realization** — freq=6, share=0.04054054054054054, r_impact=12.0, instruments=['volatility_75_1s', 'step_index']
   - Low TP count relative to filled trades reduces positive R contribution.

## 14. Sample-size warnings

Thresholds (research interpretation only):
- <30: very_small
- 30–79: small
- 80–199: preliminary
- ≥200: stronger

- aggregate: n=148 is preliminary/usable (80–199); still not definitive.
- volatility_75_1s overall: n=89 is preliminary/usable (80–199); still not definitive.
- long: n=46 is small (30–79); treat findings as hypotheses only.
- short: n=43 is small (30–79); treat findings as hypotheses only.
- bearish: n=45 is small (30–79); treat findings as hypotheses only.
- bullish: n=44 is small (30–79); treat findings as hypotheses only.
- step_index overall: n=59 is small (30–79); treat findings as hypotheses only.
- long: n=30 is small (30–79); treat findings as hypotheses only.
- short: n=29 is very small (<30); insufficient for directional or causal conclusions.
- bearish: n=24 is very small (<30); insufficient for directional or causal conclusions.
- bullish: n=35 is small (30–79); treat findings as hypotheses only.
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

Continue research-only investigation of the frozen baseline. Do not deploy live/demo and do not modify strategy parameters based on this diagnostic alone. Hypothesis for later testing (not implementation): entry-zone / fill conditions contributing to NO_FILL rate. Observed average realized R is negative in this sample; treat as evidence of baseline weakness under current data, not a mandate to curve-fit. Any promising subgroup should be recorded as a research hypothesis only.

---
*Generated at 2026-09-15T17:04:43.151741+00:00 · analysis_version=milestone-5f-v1*