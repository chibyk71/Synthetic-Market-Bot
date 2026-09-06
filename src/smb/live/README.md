# Live market data (4A) + Live strategy & simulation (4B)

## Pipeline

```
Deriv live ticks
    → 4A normalize / ordering / M1·M15 candles
    → LiveMarketDataService.events()
    → 4B LiveStrategyRunner
    → StrategyEngine (2A) on FINALIZED candles only
    → TradeConstructor (2B)
    → LiveSimulationSession (2C semantics)
    → research records
```

**4B does NOT place real or demo trades.**

## Event flow

| Event | Action |
|-------|--------|
| `LiveTick` | Advance open simulations; never alone triggers strategy |
| `CandleEventKind.UPDATE` | Forming candle — ignored for strategy decisions |
| `CandleEventKind.FINALIZED` M15 | `StrategyEngine.on_m15` (context only) |
| `CandleEventKind.FINALIZED` M1 | `StrategyEngine.on_m1` → optional signals |

No lookahead: strategy only sees completed candles.

At an M15 boundary, finalized events are ordered so **M15 FINALIZED is
delivered before M1 FINALIZED** when they share the same `end_epoch`. That
lets the strategy decision at `T` use M15 context with `end_epoch <= T`
without inspecting a forming candle.

M15 context at an M1 decision uses only M15 candles with
`end_epoch <= decision_epoch` (enforced inside the existing strategy engine).

## Signal → risk → simulation

1. `StrategySignal` emitted → record `SIGNAL_GENERATED`
2. Identity `(instrument, signal_epoch, direction)` deduplicated (bounded deque)
3. `TradeConstructor.construct` → accept or `SIGNAL_REJECTED`
4. On accept → `TRADE_OPENED` + `LiveSimulationSession`
5. Subsequent ticks drive the session until TP / SL / TIMEOUT / NO_FILL → `TRADE_CLOSED`

Concurrency: `LiveRunnerConfig.max_open_simulations` (default 1). Extra
accepted signals beyond the limit are recorded as rejected with metadata.

## Simulation semantics

`LiveSimulationSession` applies the same rules as `SimulationEngine.simulate`:

- ticks with `epoch <= signal_epoch` ignored
- horizon = `signal_epoch + max_duration_seconds` (default 900s)
- touch entry; same-tick SL over TP
- TIMEOUT exit_time = horizon_end

## Reconnect

4A owns reconnect / resubscribe. 4B keeps open simulation sessions across
temporary disconnects. Dedup prevents re-processing the same finalized signal
after a reconnect replay of the last candle.

## Shutdown

```
stop requested
  → stop accepting new signals
  → cancel runner task
  → optional finalize open sims (default on)
  → market.stop()
```

## Local run with fake stream

```python
import asyncio
from smb.live import (
    FakeTickTransport,
    LiveMarketDataService,
    LiveStrategyRunner,
    LiveRunnerConfig,
    make_fake_symbol,
)

async def main():
    transport = FakeTickTransport()
    market = LiveMarketDataService(
        "Volatility 75 (1s) Index",
        transport=transport,
        symbol_resolver=lambda n: make_fake_symbol(n, "1HZ75V"),
    )
    runner = LiveStrategyRunner(market, config=LiveRunnerConfig())
    await runner.start()
    await runner.stop()
    for rec in runner.records:
        print(rec)

asyncio.run(main())
```

## One-week live research

After merge, run the runner against the real Deriv transport for ~7 days,
persist `LiveResearchRecord` stream (sink callback), then compare signal
frequency, rejection rate, TP/SL/TIMEOUT, and expectancy vs historical.

## Boundaries

**In scope:** orchestration, dedup, bounded state, research records, tests.

**Out of scope:** real/demo execution, broker orders, Telegram, new strategy
rules, ML inference gate, optimization, portfolio management.
