# Milestone 3B — ML Dataset + Model

## Purpose

Build a research-safe supervised-learning pipeline. The model is a **filter/gate**, not a signal generator.

## Feature schema (`3b.1`)

18 fixed-order signal-time features. Never use exit prices, MFE/MAE, or future candles as features.

## Target policy

Default `FILLED_TP_POSITIVE`: TP→1, SL/TIMEOUT→0, NO_FILL excluded.

## Model

`RandomForestClassifier`, `random_state=42`, `n_estimators=50`, `max_depth=6`, `n_jobs=1`.

---

# Milestone 3C — Walk-forward Validation

## What it is

Expanding-window walk-forward validation repeatedly trains the **3B** baseline model on historical labeled observations and evaluates it on later, unseen observations. Each fold uses a **fresh** model.

```
Fold 1:  TRAIN [0 .. t0)     TEST [t0 .. t0+s)
Fold 2:  TRAIN [0 .. t0+s)   TEST [t0+s .. t0+2s)
Fold 3:  TRAIN [0 .. t0+2s)  TEST [t0+2s .. t0+3s)
```

## Why it is required

Random splits can leak future regimes into training. Walk-forward enforces:

```
max(train signal_epoch) < min(test signal_epoch)
```

## Leakage rules

- No future observations in training
- No random shuffle / KFold
- No test data used for fitting
- 3B feature schema and target policy unchanged
- NO_FILL excluded from supervised matrices
- Fresh RandomForest per fold
- Aggregate metrics from **combined** OOS predictions (not mean of fold metrics)

## API

```python
from smb.ml import WalkForwardConfig, run_walk_forward_validation

config = WalkForwardConfig(initial_train_size=60, test_size=10, step_size=10)
result = run_walk_forward_validation(dataset, config)
print(result.aggregate_evaluation)
```

## Limitations

3C measures out-of-sample **classification** performance. It does not prove live profitability or invent PnL.

## Out of scope

XGBoost, HPO, SMOTE, live inference, execution, strategy/risk/simulation changes.
