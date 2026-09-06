# Milestone 4A — Live Market Data

Reliable live ticks and M1/M15 candles from Deriv public WebSocket data.

## Pipeline

```
Deriv WebSocket → normalize → ordering gate → M1/M15 candles → LiveMarketState
```

4B will consume `LiveMarketState` and `LiveMarketDataService.events()`.

## Boundaries

**In scope:** connection lifecycle, symbol discovery, tick normalization,
duplicate/stale handling, live candle UPDATE/FINALIZED events, reconnect with
bounded backoff, stream status.

**Out of scope:** strategy, risk, simulation, ML, health gates, demo/real
execution, order placement.

## Tick ordering policy

| Condition | Action |
|-----------|--------|
| `epoch < last` | drop (stale) |
| `epoch == last` and same price | drop (duplicate) |
| `epoch == last` and new price | accept (same-second update) |
| `epoch > last` | accept |

Timestamps are never fabricated.

## Candles

Bucket: `start = (epoch // T) * T`. Tick at exact next boundary opens the new
candle. Events:

- `CandleEventKind.UPDATE` — open candle OHLC changed (`finalized=False`)
- `CandleEventKind.FINALIZED` — period ended; snapshot is immutable

M1 and M15 are built from the **same** accepted tick stream.

## Reconnect

Disconnect → exponential backoff (0.5…8s, max attempts) → reconnect →
resubscribe once per symbol → resume. No duplicate subscriptions.

## Testing

All tests use `FakeTickTransport` and injected `symbol_resolver`. No live
Deriv connection is required.
