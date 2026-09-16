# Milestone 6A — Real-Data Predictive Evidence Study

Branch: `feature/milestone-6a-real-data-predictive-evidence`

This bundle contains the complete Milestone 6A implementation. No strategy,
trade construction, simulation, or execution code was modified.

## Files

| Path | Action |
|------|--------|
| `src/smb/research/predictive_evidence.py` | **NEW** — evidence study module |
| `src/smb/research/__main__.py` | **REPLACE** — adds `run-predictive-evidence` CLI |
| `src/smb/research/__init__.py` | **REPLACE** — minor whitespace only (safe) |
| `tests/test_predictive_evidence.py` | **NEW** — 31 unit tests |
| `milestone-6a-predictive-evidence.patch` | Optional full git patch from main |

## Apply on your machine

```bash
cd /path/to/Synthetic-Market-Bot
git fetch origin
git checkout main
git pull origin main
git checkout -b feature/milestone-6a-real-data-predictive-evidence

# Option A — copy files (recommended)
cp /path/to/milestone-6a-bundle/src/smb/research/predictive_evidence.py src/smb/research/
cp /path/to/milestone-6a-bundle/src/smb/research/__main__.py src/smb/research/
cp /path/to/milestone-6a-bundle/tests/test_predictive_evidence.py tests/
# __init__.py only has trivial whitespace; skip unless you want an exact match

# Option B — apply the git patch
git am --3way /path/to/milestone-6a-bundle/milestone-6a-predictive-evidence.patch
# or: git apply /path/to/milestone-6a-bundle/milestone-6a-predictive-evidence.patch

git add src/smb/research/predictive_evidence.py \
        src/smb/research/__main__.py \
        tests/test_predictive_evidence.py
git status

git commit -m "$(cat <<'MSG'
feat(research): Milestone 6A real-data predictive evidence study

Add research-only predictive evidence analysis over frozen strategy outputs:
dataset audit with explicit positive-ceiling reporting, univariate TP vs
SL/TIMEOUT diagnostics (Mann–Whitney, Cliff's delta), continuous MFE
association analysis, chronological Random Forest (secondary/exploratory)
with pooled OOS evaluation, threshold sensitivity without winner selection,
and instrument-separated V75 vs Step reporting.

CLI: python -m smb.research run-predictive-evidence
Artifacts: predictive_evidence.json + predictive_evidence_report.md

No strategy, trade construction, simulation, or execution changes.
MSG
)"

git push -u origin feature/milestone-6a-real-data-predictive-evidence
```

## Open the PR

```bash
gh pr create --base main --head feature/milestone-6a-real-data-predictive-evidence \
  --title "Milestone 6A: Real-Data Predictive Evidence Study" \
  --body "$(cat <<'BODY'
## 1. Objective
Implement a statistically honest predictive evidence study over real campaign data. Not a strategy optimization.

## 2. Research question
Do existing pre-entry features contain measurable predictive information given the observed (very small) number of positive outcomes?

## 3. Scope freeze
No changes to StrategyEngine, sweep/MSB/displacement/FVG, TradeConstructor, RR/SL/TP, simulation fill/timeout/NO_FILL, or live/demo execution.

## 4. Dataset
Consumes production experiment/campaign rows (StrategySignal + TradeCandidate geometry + simulation outcomes). CLI re-runs `run_experiment` against the configured data root.

## 5. Statistical methodology
- Dataset audit with explicit positive-ceiling note
- Binary target: TP vs SL|TIMEOUT; NO_FILL excluded from target, reported in audit
- Univariate Mann–Whitney + Cliff's δ (primary)
- Continuous MFE Spearman analysis (primary secondary)
- Chronological RF only as exploratory secondary; pooled OOS; naive majority baseline
- Threshold sensitivity 0.50–0.80 without selecting a winner
- V75 and Step analyzed separately

## 6. Leakage controls
Features are signal-time + trade geometry only. Outcome/MFE/MAE/duration/exit never used as features. No shuffle; train precedes OOS by signal_epoch.

## 7–10. Findings
Findings are produced when the CLI is run against real Parquet data. With sparse positives the report uses calibrated language (exploratory / insufficient power), not claims of edge or no-edge.

## 11. Limitations
Documented in the generated Markdown report (positive ceiling, Step Index ≤1 positive, no production threshold, RF secondary only).

## 12. Test results
31 new tests in `tests/test_predictive_evidence.py` (all green). Existing suite remains green aside from pre-existing unrelated failures.

## 13. Example artifacts
```
results/.../predictive_evidence.json
results/.../predictive_evidence_report.md
```

CLI:
```
python -m smb.research run-predictive-evidence --output results/campaigns/milestone-6a --data-root DATA
```
BODY
)"
```

## Verify locally

```bash
pip install -e ".[dev]"   # or ensure scikit-learn, scipy, pytest available
PYTHONPATH=src pytest tests/test_predictive_evidence.py -q
PYTHONPATH=src python -m smb.research run-predictive-evidence --help
```

## Definition of Done checklist
- [x] Research-only module (no strategy/risk/simulation changes)
- [x] Dataset audit + positive ceiling
- [x] TP vs SL/TIMEOUT target; NO_FILL excluded from binary target
- [x] Univariate diagnostics
- [x] Continuous MFE analysis
- [x] Chronological OOS + naive baseline + pooled metrics
- [x] Threshold sensitivity without winner
- [x] V75 vs Step separated
- [x] JSON + Markdown artifacts
- [x] CLI integration
- [x] Comprehensive tests
- [ ] PR opened against main (you do this after push)
