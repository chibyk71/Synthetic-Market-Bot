"""Domain models for Milestone 3B ML dataset and model artifacts.

Observations are built from signal-time information only. Targets and
audit fields may use post-signal simulation outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from smb.simulation.models import SimulationOutcome
from smb.strategy.models import Direction

SCHEMA_VERSION = "3b.1"


class TargetPolicy(StrEnum):
    """How binary labels are derived from simulation outcomes.

    ``FILLED_TP_POSITIVE`` (default):
        Include only filled simulations.
        target=1 when outcome is TP; target=0 when outcome is SL or TIMEOUT.
        NO_FILL rows are excluded from the supervised matrix.

    ``INCLUDE_NO_FILL_AS_NEGATIVE``:
        Include all outcomes. TP → 1; everything else (SL, TIMEOUT, NO_FILL) → 0.
    """

    FILLED_TP_POSITIVE = "filled_tp_positive"
    INCLUDE_NO_FILL_AS_NEGATIVE = "include_no_fill_as_negative"


def _require_finite(value: float, name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a real number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True, slots=True)
class FeatureSchema:
    """Deterministic, versionable feature column ordering.

    ``names`` is the exact column order used for training and inference.
    """

    version: str
    names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("FeatureSchema.names must be non-empty")
        if len(set(self.names)) != len(self.names):
            raise ValueError("FeatureSchema.names must be unique")


# Fixed schema for Milestone 3B baseline. Order is part of the contract.
FEATURE_NAMES: tuple[str, ...] = (
    "direction",  # +1 LONG, -1 SHORT
    "instrument_v75",  # 1 if Volatility 75 (1s)-like, else 0 (one-hot slot)
    "instrument_step100",  # 1 if Step Index 100-like, else 0
    "sweep_depth",  # abs(sweep extreme - swept_level)
    "msb_bars_after_sweep",
    "displacement_body_range_ratio",
    "displacement_body_atr_ratio",
    "atr",
    "fvg_size",
    "fvg_size_atr_ratio",  # 0.0 when unavailable; paired with missing flag
    "fvg_size_atr_missing",  # 1 if size_atr_ratio was None
    "m15_bias",  # +1 bullish, -1 bearish, 0 neutral/missing
    "m15_bias_missing",  # 1 if directional_bias was None
    "m15_recent_range",  # high-low; 0 when unavailable
    "m15_range_missing",
    "signal_vs_m15_mid",  # (signal_ref - mid) / range; 0 when unavailable
    "signal_vs_m15_missing",
    "hour_of_day",  # 0-23 from signal_epoch UTC
)

DEFAULT_FEATURE_SCHEMA = FeatureSchema(version=SCHEMA_VERSION, names=FEATURE_NAMES)


@dataclass(frozen=True, slots=True)
class MLObservation:
    """One supervised-learning row: signal-time features + future target.

    Identity is ``(instrument, signal_epoch, direction)``. Duplicate identities
    must be handled by the dataset builder (reject or explicit policy).
    """

    instrument: str
    signal_epoch: int
    direction: Direction

    # Fixed-order feature vector matching FeatureSchema.names
    features: tuple[float, ...]

    # Binary target under the chosen policy (None if row excluded by policy)
    target: int | None

    # Audit: original simulation outcome (always present when observation built)
    outcome: SimulationOutcome
    filled: bool

    # Optional research audit (post-signal; never used as features)
    mfe: float | None = None
    mae: float | None = None

    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if len(self.features) != len(FEATURE_NAMES):
            raise ValueError(
                f"features length {len(self.features)} != schema {len(FEATURE_NAMES)}"
            )
        for i, v in enumerate(self.features):
            _require_finite(v, f"features[{i}]")
        if self.target is not None and self.target not in (0, 1):
            raise ValueError("target must be 0, 1, or None")
        if self.signal_epoch < 0:
            raise ValueError("signal_epoch must be >= 0")


@dataclass(frozen=True, slots=True)
class MLDataset:
    """Chronologically ordered supervised dataset.

    Rows are sorted by ``signal_epoch`` ascending (then instrument, direction).
    """

    schema: FeatureSchema
    observations: tuple[MLObservation, ...]
    target_policy: TargetPolicy
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.schema.names != FEATURE_NAMES:
            # Allow only the locked 3B schema in this milestone
            if self.schema.version != SCHEMA_VERSION:
                pass  # future versions may differ; 3B locks current
        for obs in self.observations:
            if len(obs.features) != len(self.schema.names):
                raise ValueError("observation feature width mismatch")

    @property
    def n_samples(self) -> int:
        return len(self.observations)

    @property
    def labeled(self) -> tuple[MLObservation, ...]:
        return tuple(o for o in self.observations if o.target is not None)

    def feature_matrix(self) -> list[list[float]]:
        """Return X as list-of-lists in schema order (labeled rows only)."""
        return [list(o.features) for o in self.labeled]

    def labels(self) -> list[int]:
        return [int(o.target) for o in self.labeled]  # type: ignore[arg-type]

    def class_counts(self) -> dict[str, int]:
        pos = sum(1 for o in self.labeled if o.target == 1)
        neg = sum(1 for o in self.labeled if o.target == 0)
        return {
            "labeled": len(self.labeled),
            "positive": pos,
            "negative": neg,
            "unlabeled": self.n_samples - len(self.labeled),
        }


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    """Train / validation / test index partitions (into labeled rows).

    Indices refer to the ordered labeled observation list.
    Partitions are contiguous in time: train then validation then test.
    """

    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    train_end_epoch: int | None
    validation_end_epoch: int | None
    test_end_epoch: int | None

    def __post_init__(self) -> None:
        all_idx = (
            set(self.train_indices)
            | set(self.validation_indices)
            | set(self.test_indices)
        )
        if len(all_idx) != (
            len(self.train_indices)
            + len(self.validation_indices)
            + len(self.test_indices)
        ):
            raise ValueError("split partitions must be disjoint")


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Deterministic metrics for one partition. Undefined metrics are None."""

    partition: str
    n_samples: int
    n_positive: int
    n_negative: int
    accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    roc_auc: float | None
    confusion_matrix: tuple[tuple[int, int], tuple[int, int]] | None
    # [[tn, fp], [fn, tp]] when both classes present


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Serializable model package with schema and split metadata.

    Metadata and class_counts are plain dicts so the artifact remains
    joblib/pickle compatible (MappingProxyType is not picklable).
    """

    model_type: str
    random_seed: int
    schema: FeatureSchema
    target_policy: TargetPolicy
    train_end_epoch: int | None
    validation_end_epoch: int | None
    test_end_epoch: int | None
    class_counts_train: dict[str, int]
    # estimator is held outside frozen dataclass for joblib dump convenience
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "class_counts_train", dict(self.class_counts_train))
