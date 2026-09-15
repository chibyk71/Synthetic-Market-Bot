# Campaign-scale baseline report: step_index

## 1. Executive summary
- **instrument:** step_index
- **campaign_id:** `milestone-5d-step_index`
- **period (UTC):** None → None
- **ticks processed:** 2078000
- **signals:** 59
- **accepted / filled:** 59 / 34
- **win rate (among filled):** 0.029411764705882353
- **average R:** -0.75
- **total R:** -9.0
- **profit factor:** 0.18181818181818182
- **max drawdown (R):** 9.0
- **sample scale:** small_sample
- **interpretation:** **BASELINE INCONCLUSIVE**

### Interpretation notes
- small-sample evidence (n_signals=59 < 80); do not over-interpret
- Signal count remains in the small-sample regime; treat any profitability conclusion as inconclusive.

## 2. Data coverage
- ticks: 2078000
- M1 candles: 34633
- M15 candles: 2309
- start_epoch: None
- end_epoch: None

## 3. Baseline strategy results
- wins / losses / timeouts / no-fill: 1 / 11 / 22 / 25
- average MAE / MFE: 1.5897058823529733 / 1.4661764705882996
- median MAE / MFE: 1.6749999999997272 / 1.2999999999997272
- average / median duration (s): 523.7647058823529 / 570.5

## 4. Long vs short
- **long:** signals=30 filled=17 WR=0.0 avgR=-1.0 totalR=-6.0 PF=0.0 maxDD=6.0
- **short:** signals=29 filled=17 WR=0.058823529411764705 avgR=-0.5 totalR=-3.0 PF=0.4 maxDD=4.0

## 5. Existing M15 context (causal at signal time)
- **bearish:** signals=24 filled=15 WR=0.06666666666666667 avgR=-0.4 totalR=-2.0 PF=0.5
- **bullish:** signals=35 filled=19 WR=0.0 avgR=-1.0 totalR=-7.0 PF=0.0

## 6. Volatility / ATR context
- No separate volatility-bucket classification is exposed as a first-class causal signal field beyond existing ATR ratios inside setup geometry. Bucketed low/normal/high volatility segmentation is **unavailable** without inventing thresholds (out of scope for 5B).

## 7. MAE / MFE (post-entry research only)
- average MAE: 1.5897058823529733
- median MAE: 1.6749999999997272
- average MFE: 1.4661764705882996
- median MFE: 1.2999999999997272
- MAE/MFE are not used for signal generation or trade selection.

## 8. Rejections and no-fills
- rejected candidates: 0
- no-fill among accepted: 25
- no rejection reasons recorded (or zero rejections).
- outcome counts:
  - no_fill: 25
  - sl: 11
  - timeout: 22
  - tp: 1

## 9. Duration
- average duration (s): 523.7647058823529
- median duration (s): 570.5
- timeout count: 22
- Intended research horizon remains the simulation max_duration (default 900s / 15m). Compare median duration and timeout proportion against that design assumption.

## 10. Failure modes (observational)
- SL hits outnumber TP hits among filled trades.
- Timeouts are material (22/34 filled); many trades neither hit TP nor SL within the horizon.
- No-fill rate is material (25/59 accepted); usable frequency is reduced before performance is measured.

## 11. Research interpretation
- Label: **BASELINE INCONCLUSIVE**
- small-sample evidence (n_signals=59 < 80); do not over-interpret
- Signal count remains in the small-sample regime; treat any profitability conclusion as inconclusive.

## 12. Recommendation
No strategy change is made in this milestone. Use the interpretation label and segment tables to choose the *next* research question (e.g. more data, direction diagnostic depth, timeout geometry, or context-conditioned analysis) — not to optimize parameters here.

## Appendix — Milestone 3B baseline detail

Baseline strategy analysis
  instrument:        step_index
  start_epoch:       None
  end_epoch:         None
  total_signals:     59
  total_accepted:    59
  total_rejected:    0
  total_filled:      34  (TP+SL+TIMEOUT)

Notes:
  - win_rate denominator = filled = TP + SL + TIMEOUT (NO_FILL excluded).
  - realized R includes TP and SL only; TIMEOUT has no exit_price by design.
  - MAE/MFE distributions exclude NO_FILL.
  - Time buckets use UTC derived from signal_epoch.
  - Time buckets with fewer than 5 signals are marked sparse.
  - Timeout MFE-in-R thresholds are descriptive only; TIMEOUT is not marked to market.

By direction (fill_rate = filled/accepted; rates among filled):
  [long]
    signals=30 accepted=30 rejected=0 no_fill=13 filled=17
    tp=0 sl=6 timeout=11 fill_rate=0.5666666666666667 tp_rate_filled=0.0 sl_rate_filled=0.35294117647058826 timeout_rate_filled=0.6470588235294118
    total_r=-6.0 average_r=-1.0 avg_mae=1.6205882352942889 avg_mfe=1.320588235294214 avg_duration=506.2352941176471
  [short]
    signals=29 accepted=29 rejected=0 no_fill=12 filled=17
    tp=1 sl=5 timeout=11 fill_rate=0.5862068965517241 tp_rate_filled=0.058823529411764705 sl_rate_filled=0.29411764705882354 timeout_rate_filled=0.6470588235294118
    total_r=-3.0 average_r=-0.5 avg_mae=1.5588235294116577 avg_mfe=1.611764705882385 avg_duration=541.2941176470588

Outcomes (pct_of_all_signals = count/signals; pct_of_filled = count/filled, None for NO_FILL):
  tp: count=1 pct_of_all_signals=0.01694915254237288 pct_of_filled=0.029411764705882353
  sl: count=11 pct_of_all_signals=0.1864406779661017 pct_of_filled=0.3235294117647059
  timeout: count=22 pct_of_all_signals=0.3728813559322034 pct_of_filled=0.6470588235294118
  no_fill: count=25 pct_of_all_signals=0.423728813559322 pct_of_filled=None

Filled-trade distributions (NO_FILL excluded):
  MAE: n=34 min=0.0 median=1.6749999999997272 mean=1.5897058823529733 p75=2.274999999999636 p90=2.9999999999997273 max=3.6500000000005457
  MFE: n=34 min=0.0 median=1.2999999999997272 mean=1.4661764705882996 p75=2.250000000000682 p90=2.7550000000002908 max=4.75
  duration_s: n=34 min=4.0 median=570.5 mean=523.7647058823529 p75=736.75 p90=817.4 max=876.0

Excursions by filled outcome:
  [tp] n=1
      MAE: n=1 min=0.1499999999996362 median=0.1499999999996362 mean=0.1499999999996362 p75=0.1499999999996362 p90=0.1499999999996362 max=0.1499999999996362
      MFE: n=1 min=4.75 median=4.75 mean=4.75 p75=4.75 p90=4.75 max=4.75
      duration_s: n=1 min=414.0 median=414.0 mean=414.0 p75=414.0 p90=414.0 max=414.0
  [sl] n=11
      MAE: n=11 min=0.6500000000005457 median=2.1500000000005457 mean=2.3863636363638845 p75=3.0000000000004547 p90=3.300000000000182 max=3.6500000000005457
      MFE: n=11 min=0.0 median=0.25 mean=0.8863636363635536 p75=0.9249999999992724 p90=2.6500000000005457 max=3.8499999999994543
      duration_s: n=11 min=66.0 median=241.0 mean=349.6363636363636 p75=603.0 p90=736.0 max=795.0
  [timeout] n=22
      MAE: n=22 min=0.0 median=1.199999999999818 mean=1.2568181818181239 p75=1.8875000000000455 p90=2.2999999999992724 max=2.9999999999990905
      MFE: n=22 min=0.1000000000003638 median=1.525000000000091 mean=1.6068181818183225 p75=2.250000000000682 p90=2.6500000000004547 max=4.200000000000728
      duration_s: n=22 min=4.0 median=617.0 mean=615.8181818181819 p75=772.0 p90=854.6 max=876.0

Timeout descriptive analysis (not realized P&L):
  count=22
  mfe: avg=1.6068181818183225 median=1.525000000000091 p75=2.250000000000682 p90=2.6500000000004547 max=4.200000000000728
  mae: avg=1.2568181818181239 median=1.199999999999818
  mfe_in_R thresholds (sample=22): >=0.5R:14 >=1.0R:7 >=1.5R:3 >=2.0R:0

No-fill analysis:
  count=25 pct_of_all_signals=0.423728813559322 long=13 short=12
  long_pct_of_no_fill=0.52 short_pct_of_no_fill=0.48
  long_pct_of_long_signals=0.43333333333333335 short_pct_of_short_signals=0.41379310344827586
  avg_risk_distance=2.3483999999999288 avg_entry_zone_width=0.9239999999999782 avg_rr=2.0

Setup groups (existing signal attributes only):
  attribute=direction
    value=long: signals=30 filled=17 tp=0 sl=6 timeout=11 no_fill=13 total_r=-6.0 average_r=-1.0
    value=short: signals=29 filled=17 tp=1 sl=5 timeout=11 no_fill=12 total_r=-3.0 average_r=-0.5
  attribute=m15_bias
    value=bearish: signals=24 filled=15 tp=1 sl=4 timeout=10 no_fill=9 total_r=-2.0 average_r=-0.4
    value=bullish: signals=35 filled=19 tp=0 sl=7 timeout=12 no_fill=16 total_r=-7.0 average_r=-1.0
  attribute=sweep_direction
    value=long: signals=30 filled=17 tp=0 sl=6 timeout=11 no_fill=13 total_r=-6.0 average_r=-1.0
    value=short: signals=29 filled=17 tp=1 sl=5 timeout=11 no_fill=12 total_r=-3.0 average_r=-0.5
  attribute=msb_direction
    value=long: signals=30 filled=17 tp=0 sl=6 timeout=11 no_fill=13 total_r=-6.0 average_r=-1.0
    value=short: signals=29 filled=17 tp=1 sl=5 timeout=11 no_fill=12 total_r=-3.0 average_r=-0.5

Time buckets UTC (sparse if signals < 5):
  By hour:
    hour=00: signals=2 fills=0 tp=0 sl=0 timeout=0 no_fill=2 total_r=None SPARSE
    hour=01: signals=4 fills=2 tp=0 sl=1 timeout=1 no_fill=2 total_r=-1.0 SPARSE
    hour=02: signals=4 fills=4 tp=0 sl=1 timeout=3 no_fill=0 total_r=-1.0 SPARSE
    hour=03: signals=2 fills=2 tp=0 sl=0 timeout=2 no_fill=0 total_r=None SPARSE
    hour=04: signals=3 fills=2 tp=0 sl=0 timeout=2 no_fill=1 total_r=None SPARSE
    hour=05: signals=2 fills=2 tp=0 sl=1 timeout=1 no_fill=0 total_r=-1.0 SPARSE
    hour=06: signals=2 fills=1 tp=0 sl=0 timeout=1 no_fill=1 total_r=None SPARSE
    hour=07: signals=2 fills=2 tp=0 sl=1 timeout=1 no_fill=0 total_r=-1.0 SPARSE
    hour=08: signals=4 fills=2 tp=0 sl=0 timeout=2 no_fill=2 total_r=None SPARSE
    hour=09: signals=3 fills=1 tp=0 sl=0 timeout=1 no_fill=2 total_r=None SPARSE
    hour=10: signals=3 fills=1 tp=0 sl=1 timeout=0 no_fill=2 total_r=-1.0 SPARSE
    hour=11: signals=3 fills=3 tp=0 sl=2 timeout=1 no_fill=0 total_r=-2.0 SPARSE
    hour=12: signals=3 fills=1 tp=0 sl=0 timeout=1 no_fill=2 total_r=None SPARSE
    hour=13: signals=4 fills=1 tp=0 sl=0 timeout=1 no_fill=3 total_r=None SPARSE
    hour=14: signals=1 fills=0 tp=0 sl=0 timeout=0 no_fill=1 total_r=None SPARSE
    hour=15: signals=3 fills=1 tp=0 sl=0 timeout=1 no_fill=2 total_r=None SPARSE
    hour=16: signals=2 fills=1 tp=0 sl=1 timeout=0 no_fill=1 total_r=-1.0 SPARSE
    hour=18: signals=2 fills=1 tp=0 sl=1 timeout=0 no_fill=1 total_r=-1.0 SPARSE
    hour=19: signals=4 fills=3 tp=0 sl=1 timeout=2 no_fill=1 total_r=-1.0 SPARSE
    hour=20: signals=1 fills=1 tp=0 sl=1 timeout=0 no_fill=0 total_r=-1.0 SPARSE
    hour=21: signals=2 fills=1 tp=1 sl=0 timeout=0 no_fill=1 total_r=2.0 SPARSE
    hour=22: signals=1 fills=0 tp=0 sl=0 timeout=0 no_fill=1 total_r=None SPARSE
    hour=23: signals=2 fills=2 tp=0 sl=0 timeout=2 no_fill=0 total_r=None SPARSE
  By day-of-week:
    Monday: signals=5 fills=1 tp=0 sl=1 timeout=0 no_fill=4 total_r=-1.0
    Tuesday: signals=7 fills=1 tp=0 sl=1 timeout=0 no_fill=6 total_r=-1.0
    Wednesday: signals=8 fills=6 tp=0 sl=0 timeout=6 no_fill=2 total_r=None
    Thursday: signals=6 fills=6 tp=1 sl=1 timeout=4 no_fill=0 total_r=1.0
    Friday: signals=15 fills=10 tp=0 sl=3 timeout=7 no_fill=5 total_r=-3.0
    Saturday: signals=13 fills=6 tp=0 sl=2 timeout=4 no_fill=7 total_r=-2.0
    Sunday: signals=5 fills=4 tp=0 sl=3 timeout=1 no_fill=1 total_r=-3.0

Geometry records: 59 accepted candidates