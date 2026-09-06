"""Baseline model training and artifact persistence for Milestone 3B.

Uses a seeded RandomForestClassifier. No hyperparameter search.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
from sklearn.ensemble import RandomForestClassifier

from smb.ml.evaluation import evaluate_predictions
from smb.ml.models import (
    ChronologicalSplit,
    EvaluationReport,
    FeatureSchema,
    MLDataset,
    ModelArtifact,
)

DEFAULT_RANDOM_SEED = 42
DEFAULT_N_ESTIMATORS = 50
DEFAULT_MAX_DEPTH = 6


def _matrix_from_indices(
    dataset: MLDataset, indices: tuple[int, ...]
) -> tuple[list[list[float]], list[int]]:
    labeled = dataset.labeled
    X = [list(labeled[i].features) for i in indices]
    y = [int(labeled[i].target) for i in indices]  # type: ignore[arg-type]
    return X, y


def train_baseline(
    dataset: MLDataset,
    split: ChronologicalSplit,
    *,
    random_seed: int = DEFAULT_RANDOM_SEED,
    n_estimators: int = DEFAULT_N_ESTIMATORS,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[RandomForestClassifier, ModelArtifact]:
    """Fit a RandomForestClassifier on the train partition only."""
    if not split.train_indices:
        raise ValueError("train partition is empty")

    X_train, y_train = _matrix_from_indices(dataset, split.train_indices)
    if len(set(y_train)) < 1:
        raise ValueError("train labels empty")

    clf = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=random_seed,
        n_jobs=1,  # determinism
    )
    clf.fit(X_train, y_train)

    pos = sum(1 for v in y_train if v == 1)
    neg = len(y_train) - pos
    artifact = ModelArtifact(
        model_type="RandomForestClassifier",
        random_seed=random_seed,
        schema=dataset.schema,
        target_policy=dataset.target_policy,
        train_end_epoch=split.train_end_epoch,
        validation_end_epoch=split.validation_end_epoch,
        test_end_epoch=split.test_end_epoch,
        class_counts_train={"positive": pos, "negative": neg, "n": len(y_train)},
        metadata={
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "schema_version": dataset.schema.version,
        },
    )
    return clf, artifact


def predict(
    clf: RandomForestClassifier,
    X: list[list[float]],
) -> tuple[list[int], list[float]]:
    """Return class predictions and positive-class probabilities."""
    if not X:
        return [], []
    pred = [int(p) for p in clf.predict(X)]
    proba = clf.predict_proba(X)
    # positive class column: find index of class 1
    classes = list(clf.classes_)
    if 1 in classes:
        pos_idx = classes.index(1)
        scores = [float(row[pos_idx]) for row in proba]
    else:
        scores = [0.0] * len(pred)
    return pred, scores


def evaluate_split(
    clf: RandomForestClassifier,
    dataset: MLDataset,
    split: ChronologicalSplit,
    partition: str,
) -> EvaluationReport:
    """Evaluate on one named partition of the chronological split."""
    if partition == "train":
        indices = split.train_indices
    elif partition == "validation":
        indices = split.validation_indices
    elif partition == "test":
        indices = split.test_indices
    else:
        raise ValueError(f"unknown partition: {partition}")

    if not indices:
        return evaluate_predictions([], [], partition=partition)

    X, y = _matrix_from_indices(dataset, indices)
    y_pred, y_score = predict(clf, X)
    return evaluate_predictions(y, y_pred, y_score=y_score, partition=partition)


def save_model(
    path: str | Path,
    clf: RandomForestClassifier,
    artifact: ModelArtifact,
) -> Path:
    """Persist estimator + artifact metadata via joblib."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "estimator": clf,
        "artifact": artifact,
        "feature_names": list(artifact.schema.names),
        "schema_version": artifact.schema.version,
        "target_policy": str(artifact.target_policy),
        "random_seed": artifact.random_seed,
        "model_type": artifact.model_type,
    }
    joblib.dump(payload, path)
    return path


def load_model(path: str | Path) -> tuple[RandomForestClassifier, ModelArtifact, dict[str, Any]]:
    """Load estimator and metadata. Validates schema presence."""
    payload = joblib.load(path)
    if "estimator" not in payload or "artifact" not in payload:
        raise ValueError("model file missing estimator or artifact")
    clf = payload["estimator"]
    artifact: ModelArtifact = payload["artifact"]
    if not isinstance(artifact.schema, FeatureSchema):
        raise ValueError("artifact.schema must be FeatureSchema")
    return clf, artifact, payload
