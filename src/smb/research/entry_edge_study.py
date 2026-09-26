"""Milestone 6C — Entry Edge / Early Excursion Study.

Research-only observational diagnostic of the frozen 6B entry cohort.
Diagnoses whether filled trades show directional edge in the first 1–5 minutes
after fill via early excursion (MFE/MAE) analysis.

This module is research-only. It does not change strategy signals, trade
construction, production execution paths, or the frozen 6B cohort definition.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ENTRY_EDGE_STUDY_VERSION = "6c-entry-edge-1.0"
PRIMARY_ENDPOINT_SECONDS = 180
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 6_031_800
EARLY_WINDOWS_S = (30, 60, 90, 120, 180, 300)
CANONICAL_TIMEOUT_S = 900
EXPLORATORY_TIMEOUT_S = 1800
TARGET_EFFECT_R = 0.2

# PLACEHOLDER_TRUNCATED_FOR_SIZE - need full content
