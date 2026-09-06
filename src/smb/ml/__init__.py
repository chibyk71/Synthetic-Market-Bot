"""Milestone 3B — ML dataset construction and baseline classification model.

The ML layer is a **filter/gate** over mechanical strategy candidates.
It does not generate trades. Features use only signal-time information;
targets use future simulation outcomes.

Pipeline::

    StrategySignal (signal-time features)
            ↓
    TradeSimulationResult (future outcome → target)
            ↓
    MLObservation / MLDataset
            ↓
    ChronologicalSplit
            ↓
    RandomForestClassifier (seeded)
            ↓
    EvaluationReport + ModelArtifact
"""

from smb.ml.dataset import build_dataset, build_observation, metrics_index, resolve_target
from smb.ml.evaluation import evaluate_predictions
from smb.ml.features import extract_features, feature_names, feature_schema
from smb.ml.models import (
    SCHEMA_VERSION,
    ChronologicalSplit,
    EvaluationReport,
    FeatureSchema,
    MLDataset,
    MLObservation,
    ModelArtifact,
    TargetPolicy,
)
from smb.ml.split import chronological_split, chronological_split_by_epochs
from smb.ml.trainer import (
    DEFAULT_RANDOM_SEED,
    evaluate_split,
    load_model,
    predict,
    save_model,
    train_baseline,
)

__all__ = [
    "SCHEMA_VERSION",
    "TargetPolicy",
    "FeatureSchema",
    "MLObservation",
    "MLDataset",
    "ChronologicalSplit",
    "EvaluationReport",
    "ModelArtifact",
    "extract_features",
    "feature_schema",
    "feature_names",
    "resolve_target",
    "build_observation",
    "build_dataset",
    "metrics_index",
    "chronological_split",
    "chronological_split_by_epochs",
    "train_baseline",
    "predict",
    "evaluate_split",
    "evaluate_predictions",
    "save_model",
    "load_model",
    "DEFAULT_RANDOM_SEED",
]
