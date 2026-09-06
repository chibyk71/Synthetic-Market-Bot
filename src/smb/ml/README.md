# Milestone 3B — ML Dataset + Model

## Purpose

Build a research-safe supervised-learning pipeline that answers:

> Given that the mechanical strategy produced this setup, how likely is the
> desired simulation outcome?

The model is a **filter/gate**, not a signal generator. The 2A strategy remains
the sole source of candidates.

## Pipeline

```
StrategySignal  ──(signal-time only)──►  features
TradeSimulationResult  ──(future)──►  target
        ↓
   MLObservation / MLDataset
        ↓
   ChronologicalSplit (train → validation → test)
        ↓
   RandomForestClassifier (seeded, no HPO)
        ↓
   EvaluationReport + joblib ModelArtifact
```

## Observation identity

`(instrument, signal_epoch, direction)` — duplicates raise; no silent overwrite.

## Feature schema (`3b.1`)

All features are extracted from `StrategySignal` fields available at
`signal_epoch`. **Never** use exit prices, exit times, realized R, TP/SL hits,
MFE, MAE, future candles, or aggregate validation stats as features.

| Feature | Meaning |
|---------|---------|
| `direction` | +1 LONG, −1 SHORT |
| `instrument_v75` / `instrument_step100` | deterministic one-hot slots |
| `sweep_depth` | \|sweep extreme − swept level\| |
| `msb_bars_after_sweep` | bars between sweep and MSB |
| `displacement_body_range_ratio` | body / range |
| `displacement_body_atr_ratio` | body / ATR |
| `atr` | ATR at displacement |
| `fvg_size` | fair-value gap size |
| `fvg_size_atr_ratio` | size/ATR (0 if missing) |
| `fvg_size_atr_missing` | 1 if ratio unavailable |
| `m15_bias` | +1 bullish, −1 bearish, 0 neutral/missing |
| `m15_bias_missing` | 1 if bias was None |
| `m15_recent_range` | M15 recent high−low (0 if missing) |
| `m15_range_missing` | missing flag |
| `signal_vs_m15_mid` | (FVG mid − M15 mid) / range |
| `signal_vs_m15_missing` | missing flag |
| `hour_of_day` | UTC hour 0–23 from `signal_epoch` |

Missing numeric values use explicit missing indicators (0 + flag), not silent
imputation with arbitrary constants beyond the documented policy.

## Target definition

**Default policy** `TargetPolicy.FILLED_TP_POSITIVE`:

- Include only **filled** simulations (`TP`, `SL`, `TIMEOUT`).
- `target = 1` iff `outcome == TP`.
- `target = 0` for `SL` or `TIMEOUT`.
- **`NO_FILL` is excluded** from the supervised matrix (row still kept in the
  full dataset with `target=None` for audit).

Alternative: `INCLUDE_NO_FILL_AS_NEGATIVE` maps NO_FILL → 0.

The original `SimulationOutcome` is always stored on each `MLObservation`.

## Chronological split

No random shuffle. Labeled rows are ordered by `signal_epoch` (then instrument,
direction). Default proportions: 60% train / 20% validation / 20% test.
Epoch-boundary splits are also supported. Test always contains later
observations than train.

## Model

- `sklearn.ensemble.RandomForestClassifier`
- `random_state=42`, `n_jobs=1`, fixed `n_estimators` / `max_depth`
- No hyperparameter optimization, no ensembles of models, no neural nets

## Persistence

`save_model` / `load_model` store via joblib:

- estimator
- `ModelArtifact` (schema, target policy, epoch boundaries, seed, class counts)
- feature name order and schema version

Bare estimator-only pickles are not used.

## Leakage controls

1. Features come only from `StrategySignal` (pre-built at signal time).
2. Simulation exit/MFE/MAE fields are never passed into `extract_features`.
3. Dataset is sorted by signal time **before** split; no global shuffle.
4. Trainer fits only on train indices.
5. Regression tests assert that mutating future exit price/time/MFE/MAE does
   not change the feature vector.

## Out of scope (3B)

Walk-forward (3C), live inference, ML signal generation, execution, Telegram,
health gates, HPO, SMOTE, neural networks.
