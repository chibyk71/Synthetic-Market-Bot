# Milestone 5C — Expanded Historical Baseline Report

Strategy and simulation unchanged from Milestone 5B.

## Coverage

### volatility_75_1s
- ticks: 330000
- signals: 10
- accepted/filled: see analysis artifacts
- total R: -2.0
- avg R: -1.0
- interpretation: **BASELINE INCONCLUSIVE**

### step_index
- ticks: 248000
- signals: 9
- total R: -1.0
- avg R: -0.25
- interpretation: **BASELINE INCONCLUSIVE**

## Comparison vs original 5B

| Metric | 5B V75 | 5C V75 | 5B Step | 5C Step |
| --- | --- | --- | --- | --- |
| ticks | ~255000 | 330000 | ~225000 | 248000 |
| signals | 7 | 10 | 8 | 9 |
| filled | 4 | 5 | 6 | 7 |
| total R | -2 | -2.0 | -1 | -1.0 |
| interpretation | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |

## Key findings

- Signal frequency remains extremely low (~2 signals/day).
- Expanded coverage still yields single-digit signals per instrument.
- Negative-to-flat total R persists; sample remains too small for strategy conclusions.
- Deriv ticks_history rate limits constrain rapid multi-week acquisition in one session.
- No strategy changes were made.

## Recommended next research milestone

Continue historical coverage expansion (scheduled multi-session ingest with backoff) until
≥80–200 signals per instrument are available, **before** any strategy diagnostic changes.
