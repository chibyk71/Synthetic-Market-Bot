"""Milestone 6D — Instrument Statistical Characterization.

Research-only characterization of temporal structure in synthetic tick
processes. Does **not** modify strategy, trade construction, simulation,
live/demo execution, or production baselines.

Preregistered structures (each instrument independently):
  Gate A — directional dependence (ACF of signed increments)
  Gate B — volatility dependence (ACF of abs / squared increments)
  Gate C — directional transitions and run lengths
  Gate D — overall synthesis

Detection requires BOTH statistical significance AND a frozen
minimum-effect-size floor.

Correct negative language:
  "no measurable structure was detected under the preregistered tests
   and effect-size thresholds."
Correct positive language:
  "a candidate statistical property has been detected and may justify a
   separately preregistered hypothesis study."
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.research.stats import percentile

STUDY_ID = "milestone-6d-statistical-characterization"
STUDY_VERSION = "6d.1"
DEFAULT_SEED = 20260927
ACF_LAGS: tuple[int, ...] = (1, 2, 5, 10, 30, 60, 120, 300, 600)
EXTREME_MOVE_WINDOW = 300
EXTREME_MOVE_THRESHOLD_SIGMA = 3.0
FUTURE_RESPONSE_HORIZONS: tuple[int, ...] = (1, 5, 10, 30, 60, 180, 300)
NULL_SIMULATIONS = 100
SIGNIFICANCE_LEVEL = 0.05
MINIMUM_EFFECT_SIZE_ACF = 0.02
MINIMUM_EFFECT_SIZE_TRANSITION = 0.03
MIN_OBSERVATIONS_FOR_ACF = 50
MIN_TRANSITION_PAIRS = 30
MIN_RUNS_PER_DIRECTION = 10
MIN_EXTREME_EVENTS = 20
MIN_TICKS_FOR_STUDY = 100

INSTRUMENT_V75 = "volatility_75_1s"
INSTRUMENT_STEP = "step_index"
DEFAULT_INSTRUMENTS: tuple[str, ...] = (INSTRUMENT_V75, INSTRUMENT_STEP)

GateState = Literal["DETECTED", "NOT_DETECTED", "INCONCLUSIVE"]
OverallState = Literal[
    "NO_MEASURABLE_STRUCTURE",
    "CANDIDATE_STRUCTURE",
    "NEEDS_MORE_DATA",
    "INVALID_STUDY",
]
RUN_LENGTH_BUCKETS: tuple[int, ...] = (1, 2, 3, 4, 5, 10)

# NOTE: Full module body continues in follow-up commits if size-limited.
# Temporary minimal stub so branch is not left as 'placeholder'.
# See milestone-6d-statistical-characterization.tar.gz for complete source.

raise ImportError(
    "statistical_characterization full source must be applied from "
    "milestone-6d-statistical-characterization.tar.gz — incomplete push"
)
