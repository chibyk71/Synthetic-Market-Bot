# Milestone 5F restore package

Recovered from local known-good commits on
`feature/milestone-5f-baseline-diagnostic-research`
(HEAD recovery commit `6da8ce9`).

## Contents (paths relative to repo root)

- `src/smb/research/diagnostic_core.py`
- `src/smb/research/diagnostic_analysis.py`
- `src/smb/research/__main__.py`  (includes `run-baseline-diagnostics`)
- `src/smb/research/__init__.py`
- `tests/test_diagnostic_analysis.py`
- `results/campaigns/milestone-5f/diagnostic.json`
- `results/campaigns/milestone-5f/report.md`

## Restore and push

```bash
cd /path/to/Synthetic-Market-Bot
git fetch origin
git checkout feature/milestone-5f-baseline-diagnostic-research
git pull origin feature/milestone-5f-baseline-diagnostic-research

# Extract this archive at the repo root (overwrites the placeholder files)
tar -xvf milestone-5f-restore.tar

git status
git add \
  src/smb/research/diagnostic_core.py \
  src/smb/research/diagnostic_analysis.py \
  src/smb/research/__main__.py \
  src/smb/research/__init__.py \
  tests/test_diagnostic_analysis.py \
  results/campaigns/milestone-5f/

git commit -m "fix(research): restore complete Milestone 5F implementation"
git push origin feature/milestone-5f-baseline-diagnostic-research
```

## Verify

```bash
pytest tests/test_diagnostic_analysis.py -q
python -m smb.research --help   # should list run-baseline-diagnostics
```

Do not change strategy, trade construction, risk, or simulation.
