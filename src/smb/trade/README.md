# Milestone 2B — Trade Construction + Risk

Transforms a completed **2A** `StrategySignal` into an immutable **`TradeCandidate`**
specification, or an explicit rejection reason.

```
StrategySignal
      ↓
TradeConstructor.construct(signal, risk_context)
      ↓
TradeConstructionResult
   ├── accepted → TradeCandidate
   └── rejected → RejectionReason
```

## What this is

A **planned trade specification** derived only from:

- the completed 2A signal
- `TradeConfig` (risk %, target/minimum RR, SL ATR buffer)
- `RiskContext` (equity)

## What this is not

- No fills, order IDs, or execution status
- No candle-by-candle simulation or P&L (Milestone **2C**)
- No leverage, margin, broker multipliers, or contract specs
- No market-data access after signal time (no lookahead)

## Construction rules

| Element | Rule |
|--------|------|
| **Entry** | FVG midpoint: `(gap_low + gap_high) / 2` |
| **Entry zone** | Preserved as `entry_zone_low` / `entry_zone_high` |
| **Stop (LONG)** | `swept_level - ATR × sl_atr_buffer` |
| **Stop (SHORT)** | `swept_level + ATR × sl_atr_buffer` |
| **ATR source** | `signal.displacement.atr` (available at signal time) |
| **Take-profit** | Fixed R-multiple: `entry ± risk_distance × target_rr` |
| **Minimum RR** | Reject if `risk_reward < minimum_rr` |
| **Risk amount** | `equity × risk_per_trade` |
| **Position size** | Generic research units: `risk_amount / risk_distance` |

Position size is **not** a broker lot size. It is a unit quantity such that a
full stop-out costs approximately `risk_amount` in account currency, assuming
1 unit moves 1 price unit. Instrument contract multipliers are out of scope
for 2B.

## Causality

`TradeConstructor` accepts **no** market-data provider. Construction cannot
inspect future candles, determine whether entry was touched, or compute
excursion / P&L.

## Configuration

```toml
[trade]
risk_per_trade = 0.01
target_rr = 2.0
minimum_rr = 1.5
sl_atr_buffer = 0.10
```

## Package layout

```
src/smb/trade/
  __init__.py
  models.py        # TradeConfig, RiskContext, TradeCandidate, result, reasons
  constructor.py   # TradeConstructor
  README.md
```
