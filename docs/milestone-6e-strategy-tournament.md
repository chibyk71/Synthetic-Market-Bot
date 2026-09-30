# Milestone 6E — Short-Horizon Strategy Tournament

**Research-only.** No live execution, no demo execution, no ML, no parameter
optimization, no automatic strategy ranking.

## Purpose

Test whether **alternative short-horizon / scalping hypotheses** contain
measurable edge on the existing historical tick data, treating the ICT strategy
as one **control** among several competing hypotheses.

## Branch

`feature/milestone-6e-strategy-tournament`

## CLI

```bash
python -m smb.research run-strategy-tournament \
  --output results/campaigns/milestone-6e \
  --instrument volatility_75_1s \
  --horizon 300 \
  --targets 0.30 0.40 0.50
```

Optional: `--strategies momentum_continuation mean_reversion`, `--start`, `--end`, `--data-root`.

## Architecture

```
Strategy (A–F)
  → TournamentSignal
  → construct_from_tournament_signal (target_rr cell)
  → TradeCandidate
  → SimulationEngine (existing)
  → ResearchMetricsCalculator (existing)
  → Tournament aggregation (no ranking score)
```

## Strategies (frozen)

| ID | Hypothesis | Key frozen params |
|----|------------|-------------------|
| `ict_control` | ICT sweep→MSB→displacement→FVG (unchanged) | StrategyEngine + TradeConstructor |
| `momentum_continuation` | Strong short-horizon move continues | lookback=5, min_return_atr=1.5, stop=1×ATR |
| `mean_reversion` | Large deviation reverts | lookback=20, min_dev_atr=2.0, stop=1×ATR |
| `range_breakout` | Range break continues | range=10 bars (prior only), buffer=0.25×ATR |
| `impulse_pullback` | Impulse → pullback → continuation | impulse=3 bars ≥1.5×ATR, pullback 30–60% |
| `volatility_regime` | Regime-dependent behavior | ATR rank 50 bars; low→MR, high→momentum, normal→skip |

## Trade construction

- Risk = 1R (structural stop from signal).
- Targets: **0.30R / 0.40R / 0.50R** (tournament cells).
- Horizon default: **300 seconds** (scalping).
- Shared SimulationEngine; identical historical cohort for all strategies.

## Anti-lookahead

Every strategy uses only candles with `end_epoch ≤ signal_epoch`. Range breakout
builds the range from **prior** bars only. Tests cover prefix consistency and
direction correctness.

## Outputs

- `tournament_results.json` — machine-readable summaries + rows
- `tournament_report.md` — human-readable report (no automatic winner)

## Interface note

`TradeCandidate.source_signal` is optionally `None` so non-ICT tournament
strategies can share the simulation pipeline without fabricating ICT nested
objects. ICT construction still always supplies the signal.

## Out of scope

Grid search, genetic algorithms, Bayesian optimization, ML, automatic feature
selection, multi-instrument expansion (Step Index deferred), live/demo.
