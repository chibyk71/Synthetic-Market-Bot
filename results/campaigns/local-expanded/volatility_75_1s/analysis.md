# Campaign-scale baseline report: volatility_75_1s

## 1. Executive summary
- **instrument:** volatility_75_1s
- **campaign_id:** `milestone-5d-volatility_75_1s`
- **period (UTC):** None → None
- **ticks processed:** 2973000
- **signals:** 89
- **accepted / filled:** 89 / 47
- **win rate (among filled):** 0.10638297872340426
- **average R:** -0.2857142857142857
- **total R:** -6.0
- **profit factor:** 0.625
- **max drawdown (R):** 8.0
- **sample scale:** borderline
- **interpretation:** **BASELINE WEAK**

### Interpretation notes
- borderline sample (n_signals=89); larger than original ~80-signal baseline but below campaign-scale threshold 200
- Negative average R (-0.286) and total R -6.00 across n_signals=89.
- Profit factor 0.625 is well below 1.

## 2. Data coverage
- ticks: 2973000
- M1 candles: 49551
- M15 candles: 3304
- start_epoch: None
- end_epoch: None

## 3. Baseline strategy results
- wins / losses / timeouts / no-fill: 5 / 16 / 26 / 42
- average MAE / MFE: 16.00851063829798 / 16.375000000000078
- median MAE / MFE: 14.449999999999818 / 12.534999999998945
- average / median duration (s): 511.4255319148936 / 523.0

## 4. Long vs short
- **long:** signals=46 filled=25 WR=0.04 avgR=-0.6666666666666666 totalR=-6.0 PF=0.25 maxDD=8.0
- **short:** signals=43 filled=22 WR=0.18181818181818182 avgR=0.0 totalR=0.0 PF=1.0 maxDD=5.0

## 5. Existing M15 context (causal at signal time)
- **bearish:** signals=45 filled=22 WR=0.13636363636363635 avgR=0.0 totalR=0.0 PF=1.0
- **bullish:** signals=44 filled=25 WR=0.08 avgR=-0.5 totalR=-6.0 PF=0.4

## 6. Volatility / ATR context
- No separate volatility-bucket classification is exposed as a first-class causal signal field beyond existing ATR ratios inside setup geometry. Bucketed low/normal/high volatility segmentation is **unavailable** without inventing thresholds (out of scope for 5B).

## 7. MAE / MFE (post-entry research only)
- average MAE: 16.00851063829798
- median MAE: 14.449999999999818
- average MFE: 16.375000000000078
- median MFE: 12.534999999998945
- MAE/MFE are not used for signal generation or trade selection.

## 8. Rejections and no-fills
- rejected candidates: 0
- no-fill among accepted: 42
- no rejection reasons recorded (or zero rejections).
- outcome counts:
  - no_fill: 42
  - sl: 16
  - timeout: 26
  - tp: 5

## 9. Duration
- average duration (s): 511.4255319148936
- median duration (s): 523.0
- timeout count: 26
- Intended research horizon remains the simulation max_duration (default 900s / 15m). Compare median duration and timeout proportion against that design assumption.

## 10. Failure modes (observational)
- SL hits outnumber TP hits among filled trades.
- Timeouts are material (26/47 filled); many trades neither hit TP nor SL within the horizon.
- No-fill rate is material (42/89 accepted); usable frequency is reduced before performance is measured.

## 11. Research interpretation
- Label: **BASELINE WEAK**
- borderline sample (n_signals=89); larger than original ~80-signal baseline but below campaign-scale threshold 200
- Negative average R (-0.286) and total R -6.00 across n_signals=89.
- Profit factor 0.625 is well below 1.

## 12. Recommendation
No strategy change is made in this milestone. Use the interpretation label and segment tables to choose the *next* research question (e.g. more data, direction diagnostic depth, timeout geometry, or context-conditioned analysis) — not to optimize parameters here.

## Appendix — Milestone 3B baseline detail

Baseline strategy analysis
  instrument:        volatility_75_1s
  start_epoch:       None
  end_epoch:         None
  total_signals:     89
  total_accepted:    89
  total_rejected:    0
  total_filled:      47  (TP+SL+TIMEOUT)

Notes:
  - win_rate denominator = filled = TP + SL + TIMEOUT (NO_FILL excluded).
  - realized R includes TP and SL only; TIMEOUT has no exit_price by design.
  - MAE/MFE distributions exclude NO_FILL.
  - Time buckets use UTC derived from signal_epoch.
  - Time buckets with fewer than 5 signals are marked sparse.
  - Timeout MFE-in-R thresholds are descriptive only; TIMEOUT is not marked to market.

By direction (fill_rate = filled/accepted; rates among filled):
  [long]
    signals=46 accepted=46 rejected=0 no_fill=21 filled=25
    tp=1 sl=8 timeout=16 fill_rate=0.5434782608695652 tp_rate_filled=0.04 sl_rate_filled=0.32 timeout_rate_filled=0.64
    total_r=-6.0 average_r=-0.6666666666666666 avg_mae=16.235200000000113 avg_mfe=17.6746000000001 avg_duration=589.64
  [short]
    signals=43 accepted=43 rejected=0 no_fill=21 filled=22
    tp=4 sl=8 timeout=10 fill_rate=0.5116279069767442 tp_rate_filled=0.18181818181818182 sl_rate_filled=0.36363636363636365 timeout_rate_filled=0.45454545454545453
    total_r=0.0 average_r=0.0 avg_mae=15.750909090909193 avg_mfe=14.89818181818187 avg_duration=422.54545454545456

Outcomes (pct_of_all_signals = count/signals; pct_of_filled = count/filled, None for NO_FILL):
  tp: count=5 pct_of_all_signals=0.056179775280898875 pct_of_filled=0.10638297872340426
  sl: count=16 pct_of_all_signals=0.1797752808988764 pct_of_filled=0.3404255319148936
  timeout: count=26 pct_of_all_signals=0.29213483146067415 pct_of_filled=0.5531914893617021
  no_fill: count=42 pct_of_all_signals=0.47191011235955055 pct_of_filled=None

Filled-trade distributions (NO_FILL excluded):
  MAE: n=47 min=0.43000000000029104 median=14.449999999999818 mean=16.00851063829798 p75=24.470000000000255 p90=30.04200000000019 max=42.64500000000044
  MFE: n=47 min=0.0 median=12.534999999998945 mean=16.375000000000078 p75=25.582499999999982 p90=34.747000000000114 max=63.399999999999636
  duration_s: n=47 min=55.0 median=523.0 mean=511.4255319148936 p75=767.0 p90=840.4 max=894.0

Excursions by filled outcome:
  [tp] n=5
      MAE: n=5 min=0.9849999999996726 median=6.680000000000291 mean=6.672000000000116 p75=8.240000000000691 p90=11.35699999999997 max=13.43499999999949
      MFE: n=5 min=21.4350000000004 median=29.55500000000029 mean=36.4380000000001 p75=42.01000000000022 p90=54.84399999999986 max=63.399999999999636
      duration_s: n=5 min=175.0 median=506.0 mean=506.0 p75=718.0 p90=741.4 max=757.0
  [sl] n=16
      MAE: n=16 min=11.714999999999236 median=24.377500000000055 mean=24.49437499999999 p75=29.327500000000327 p90=30.587500000000546 max=42.64500000000044
      MFE: n=16 min=0.0 median=3.6900000000005093 mean=5.269062500000075 p75=8.940000000000055 p90=11.352499999999964 max=14.175000000001091
      duration_s: n=16 min=55.0 median=342.5 mean=360.25 p75=563.75 p90=711.5 max=777.0
  [timeout] n=26
      MAE: n=26 min=0.43000000000029104 median=11.372500000000855 mean=12.581923076923257 p75=17.502500000000282 p90=25.940000000000055 max=37.284999999999854
      MFE: n=26 min=2.850000000000364 median=16.547499999999673 mean=19.35115384615392 p75=25.83625000000029 p90=35.065000000000055 max=53.13500000000022
      duration_s: n=26 min=68.0 median=657.0 mean=605.5 p75=829.75 p90=877.5 max=894.0

Timeout descriptive analysis (not realized P&L):
  count=26
  mfe: avg=19.35115384615392 median=16.547499999999673 p75=25.83625000000029 p90=35.065000000000055 max=53.13500000000022
  mae: avg=12.581923076923257 median=11.372500000000855
  mfe_in_R thresholds (sample=26): >=0.5R:16 >=1.0R:10 >=1.5R:2 >=2.0R:0

No-fill analysis:
  count=42 pct_of_all_signals=0.47191011235955055 long=21 short=21
  long_pct_of_no_fill=0.5 short_pct_of_no_fill=0.5
  long_pct_of_long_signals=0.45652173913043476 short_pct_of_short_signals=0.4883720930232558
  avg_risk_distance=20.67320068027203 avg_entry_zone_width=5.54595238095241 avg_rr=2.0

Setup groups (existing signal attributes only):
  attribute=direction
    value=long: signals=46 filled=25 tp=1 sl=8 timeout=16 no_fill=21 total_r=-6.0 average_r=-0.6666666666666666
    value=short: signals=43 filled=22 tp=4 sl=8 timeout=10 no_fill=21 total_r=0.0 average_r=0.0
  attribute=m15_bias
    value=bearish: signals=45 filled=22 tp=3 sl=6 timeout=13 no_fill=23 total_r=0.0 average_r=0.0
    value=bullish: signals=44 filled=25 tp=2 sl=10 timeout=13 no_fill=19 total_r=-6.0 average_r=-0.5
  attribute=sweep_direction
    value=long: signals=46 filled=25 tp=1 sl=8 timeout=16 no_fill=21 total_r=-6.0 average_r=-0.6666666666666666
    value=short: signals=43 filled=22 tp=4 sl=8 timeout=10 no_fill=21 total_r=0.0 average_r=0.0
  attribute=msb_direction
    value=long: signals=46 filled=25 tp=1 sl=8 timeout=16 no_fill=21 total_r=-6.0 average_r=-0.6666666666666666
    value=short: signals=43 filled=22 tp=4 sl=8 timeout=10 no_fill=21 total_r=0.0 average_r=0.0

Time buckets UTC (sparse if signals < 5):
  By hour:
    hour=00: signals=6 fills=2 tp=0 sl=0 timeout=2 no_fill=4 total_r=None
    hour=01: signals=4 fills=3 tp=1 sl=2 timeout=0 no_fill=1 total_r=0.0 SPARSE
    hour=02: signals=5 fills=3 tp=0 sl=0 timeout=3 no_fill=2 total_r=None
    hour=03: signals=5 fills=5 tp=1 sl=3 timeout=1 no_fill=0 total_r=-1.0
    hour=04: signals=4 fills=2 tp=1 sl=0 timeout=1 no_fill=2 total_r=2.0 SPARSE
    hour=05: signals=5 fills=2 tp=0 sl=1 timeout=1 no_fill=3 total_r=-1.0
    hour=06: signals=5 fills=2 tp=0 sl=1 timeout=1 no_fill=3 total_r=-1.0
    hour=07: signals=2 fills=2 tp=0 sl=1 timeout=1 no_fill=0 total_r=-1.0 SPARSE
    hour=08: signals=5 fills=4 tp=1 sl=1 timeout=2 no_fill=1 total_r=1.0
    hour=09: signals=4 fills=3 tp=1 sl=0 timeout=2 no_fill=1 total_r=2.0 SPARSE
    hour=10: signals=2 fills=1 tp=0 sl=0 timeout=1 no_fill=1 total_r=None SPARSE
    hour=11: signals=4 fills=1 tp=0 sl=0 timeout=1 no_fill=3 total_r=None SPARSE
    hour=12: signals=7 fills=4 tp=0 sl=2 timeout=2 no_fill=3 total_r=-2.0
    hour=13: signals=2 fills=1 tp=0 sl=0 timeout=1 no_fill=1 total_r=None SPARSE
    hour=14: signals=2 fills=1 tp=0 sl=1 timeout=0 no_fill=1 total_r=-1.0 SPARSE
    hour=15: signals=3 fills=1 tp=0 sl=1 timeout=0 no_fill=2 total_r=-1.0 SPARSE
    hour=16: signals=1 fills=1 tp=0 sl=0 timeout=1 no_fill=0 total_r=None SPARSE
    hour=17: signals=5 fills=1 tp=0 sl=0 timeout=1 no_fill=4 total_r=None
    hour=18: signals=2 fills=1 tp=0 sl=0 timeout=1 no_fill=1 total_r=None SPARSE
    hour=19: signals=1 fills=1 tp=0 sl=1 timeout=0 no_fill=0 total_r=-1.0 SPARSE
    hour=20: signals=4 fills=3 tp=0 sl=1 timeout=2 no_fill=1 total_r=-1.0 SPARSE
    hour=21: signals=3 fills=1 tp=0 sl=0 timeout=1 no_fill=2 total_r=None SPARSE
    hour=22: signals=5 fills=2 tp=0 sl=1 timeout=1 no_fill=3 total_r=-1.0
    hour=23: signals=3 fills=0 tp=0 sl=0 timeout=0 no_fill=3 total_r=None SPARSE
  By day-of-week:
    Monday: signals=16 fills=6 tp=0 sl=4 timeout=2 no_fill=10 total_r=-4.0
    Tuesday: signals=15 fills=7 tp=1 sl=1 timeout=5 no_fill=8 total_r=1.0
    Wednesday: signals=13 fills=3 tp=1 sl=1 timeout=1 no_fill=10 total_r=1.0
    Thursday: signals=9 fills=7 tp=1 sl=3 timeout=3 no_fill=2 total_r=-1.0
    Friday: signals=8 fills=6 tp=1 sl=1 timeout=4 no_fill=2 total_r=1.0
    Saturday: signals=16 fills=10 tp=1 sl=4 timeout=5 no_fill=6 total_r=-2.0
    Sunday: signals=12 fills=8 tp=0 sl=2 timeout=6 no_fill=4 total_r=-2.0

Geometry records: 89 accepted candidates