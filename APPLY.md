# PR #31 — Apply plain sources (replace zlib loaders)

Remote HEAD still uses zlib chunk loaders for:
- `src/smb/research/entry_edge_study.py`
- `src/smb/research/__main__.py`
- `tests/test_entry_edge_study.py`

This archive contains the **plain, complete** sources that fix all review blockers.

## Files

| Archive path | Target in repo |
|---|---|
| `src/smb/research/entry_edge_study.py` | same |
| `src/smb/research/__main__.py` | same |
| `src/smb/research/__init__.py` | same (exports) |
| `tests/test_entry_edge_study.py` | same |

## Apply (from repo root on branch `feature/milestone-6c-entry-edge-study`)

```bash
# 1. Extract
tar -xzf pr31-plain-sources.tar.gz

# 2. Copy plain sources over the loaders
cp src/smb/research/entry_edge_study.py <repo>/src/smb/research/entry_edge_study.py
cp src/smb/research/__main__.py       <repo>/src/smb/research/__main__.py
cp src/smb/research/__init__.py       <repo>/src/smb/research/__init__.py
cp tests/test_entry_edge_study.py     <repo>/tests/test_entry_edge_study.py

# 3. Remove transport chunk files (no longer needed)
rm ./src/smb/research/_ee_chunk_*.py
rm ./src/smb/research/_main_chunk_*.py
rm ./tests/_ee_test_chunk_*.py
rm ./src/smb/research/.6c_restore_canary.txt

# 4. Validate
cd <repo>
pytest -q tests/test_entry_edge_study.py
ruff check src/smb/research/entry_edge_study.py src/smb/research/__main__.py tests/test_entry_edge_study.py
python -m smb.research run-entry-edge-study --help
```

## What these sources fix

- **Required `--baseline-cohort`** — frozen 6B keys loaded independently (no self-comparison)
- **`--write-baseline-cohort`** utility to export keys
- **Tick-driven diagnostics** via `TickRepository` + `compute_post_entry_diagnostics`
  - `early_excursions_by_key` and `mtm_by_key` populated from historical ticks
  - Boundaries: entry_time ≤ epoch ≤ entry_time+t (excursion); signal_epoch+H (MTM)
- **`normalize_direction`** — fails loud on unexpected values (no silent SHORT)
- **`realized_r` accounting** for timeout scenarios (no flat ±1R)
- **53 dedicated tests** covering tick fixtures, boundaries, missing coverage, cohort roundtrip, CLI pipeline

## CLI contract after apply

```
python -m smb.research run-entry-edge-study \
  --baseline-cohort path/to/frozen_6b_keys.json \
  --data-root data \
  --output results/entry-edge \
  [--instruments volatility_75_1s step_index] \
  [--start ...] [--end ...]
```
