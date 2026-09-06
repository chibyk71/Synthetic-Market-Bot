"""Milestone 3C — expanding-window walk-forward validation.

Orchestrates repeated train → test cycles over a chronological
:class:`~smb.ml.models.MLDataset` using the existing 3B model and evaluation
APIs. Does not change features, targets, strategy, or simulation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sklearn.ensemble import RandomForestClassifier

from smb.ml.evaluation import evaluate_predictions
from smb.ml.models import EvaluationReport, MLDataset, MLObservation
from smb.ml.trainer import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_N_ESTIMATORS,
    DEFAULT_RANDOM_SEED,
    predict,
)


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Deterministic expanding-window fold configuration.

    Sizes are counts of **labeled** observations (``target is not None``).

    The 3B RandomForest hyperparameters are **not** configurable here; every
    fold uses the fixed baseline (seed 42, 50 trees, max_depth 6, n_jobs=1).

    Attributes:
        initial_train_size: Labeled rows in the first training window.
        test_size: Labeled rows in each test window.
        step_size: How far the test window advances between folds.
            Defaults to ``test_size`` (non-overlapping tests). Must be
            ``>= test_size`` so test windows never overlap.
        minimum_train_size: Reject folds whose training window is smaller.
        minimum_test_size: Reject folds whose test window is smaller.
    """

    initial_train_size: int
    test_size: int
    step_size: int | None = None
    minimum_train_size: int = 1
    minimum_test_size: int = 1

    def __post_init__(self) -> None:
        if self.initial_train_size < 1:
            raise ValueError("initial_train_size must be >= 1")
        if self.test_size < 1:
            raise ValueError("test_size must be >= 1")
        step = self.test_size if self.step_size is None else self.step_size
        if step < 1:
            raise ValueError("step_size must be >= 1")
        if step < self.test_size:
            raise ValueError(
                f"step_size ({step}) must be >= test_size ({self.test_size}) "
                "to prevent overlapping test windows"
            )
        if self.minimum_train_size < 1:
            raise ValueError("minimum_train_size must be >= 1")
        if self.minimum_test_size < 1:
            raise ValueError("minimum_test_size must be >= 1")
        if self.initial_train_size < self.minimum_train_size:
            raise ValueError("initial_train_size must be >= minimum_train_size")
        if self.test_size < self.minimum_test_size:
            raise ValueError("test_size must be >= minimum_test_size")
        object.__setattr__(self, "step_size", step)


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One expanding-window fold over labeled observation indices."""

    fold_index: int
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    train_start_epoch: int | None
    train_end_epoch: int | None
    test_start_epoch: int | None
    test_end_epoch: int | None

    @property
    def train_count(self) -> int:
        return len(self.train_indices)

    @property
    def test_count(self) -> int:
        return len(self.test_indices)

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise ValueError("fold_index must be >= 0")
        if not self.train_indices:
            raise ValueError("train_indices must be non-empty")
        if not self.test_indices:
            raise ValueError("test_indices must be non-empty")
        if set(self.train_indices) & set(self.test_indices):
            raise ValueError("train and test indices must be disjoint")


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    """Immutable per-fold out-of-sample result."""

    fold: WalkForwardFold
    train_count: int
    test_count: int
    labeled_train_count: int
    labeled_test_count: int
    positive_train_count: int
    negative_train_count: int
    positive_test_count: int
    negative_test_count: int
    predictions_count: int
    evaluation: EvaluationReport | None
    y_true: tuple[int, ...]
    y_pred: tuple[int, ...]
    y_score: tuple[float, ...]
    evaluable: bool
    skip_reason: str | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is not None:
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """Aggregate expanding-window walk-forward validation result."""

    fold_results: tuple[WalkForwardFoldResult, ...]
    total_folds: int
    total_test_observations: int
    total_labeled_test_observations: int
    aggregate_evaluation: EvaluationReport | None
    config: WalkForwardConfig
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is not None:
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _identity(obs: MLObservation) -> tuple[str, int, str]:
    return (obs.instrument, obs.signal_epoch, str(obs.direction))


def _assert_labeled_chronological(labeled: tuple[MLObservation, ...]) -> None:
    for i in range(1, len(labeled)):
        prev, cur = labeled[i - 1], labeled[i]
        if cur.signal_epoch < prev.signal_epoch:
            raise ValueError(
                "labeled observations must be sorted by signal_epoch ascending"
            )


def generate_folds(
    dataset: MLDataset,
    config: WalkForwardConfig,
) -> tuple[WalkForwardFold, ...]:
    """Generate expanding-window folds over labeled observations."""
    labeled = dataset.labeled
    n = len(labeled)
    min_required = config.initial_train_size + config.test_size
    if n < min_required:
        raise ValueError(
            f"insufficient labeled observations: need at least "
            f"initial_train_size + test_size = {min_required}, got {n}"
        )
    _assert_labeled_chronological(labeled)
    step = config.step_size if config.step_size is not None else config.test_size
    folds: list[WalkForwardFold] = []
    fold_index = 0
    test_start = config.initial_train_size
    while test_start < n:
        test_end = min(test_start + config.test_size, n)
        train_end = test_start
        train_indices = tuple(range(0, train_end))
        test_indices = tuple(range(test_start, test_end))
        if len(train_indices) < config.minimum_train_size:
            break
        if len(test_indices) < config.minimum_test_size:
            break
        train_epochs = [labeled[i].signal_epoch for i in train_indices]
        test_epochs = [labeled[i].signal_epoch for i in test_indices]
        if max(train_epochs) >= min(test_epochs):
            raise ValueError(
                f"fold {fold_index}: train/test epoch overlap or inversion: "
                f"max(train)={max(train_epochs)} min(test)={min(test_epochs)}."
            )
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_indices=train_indices,
                test_indices=test_indices,
                train_start_epoch=train_epochs[0],
                train_end_epoch=train_epochs[-1],
                test_start_epoch=test_epochs[0],
                test_end_epoch=test_epochs[-1],
            )
        )
        fold_index += 1
        test_start += step
    if not folds:
        raise ValueError(
            "no valid walk-forward folds for the given configuration and dataset"
        )
    return tuple(folds)


def _matrix_from_labeled(
    labeled: tuple[MLObservation, ...], indices: tuple[int, ...]
) -> tuple[list[list[float]], list[int]]:
    X = [list(labeled[i].features) for i in indices]
    y = [int(labeled[i].target) for i in indices]  # type: ignore[arg-type]
    return X, y


def _fit_fold_model(
    X_train: list[list[float]],
    y_train: list[int],
    config: WalkForwardConfig,
) -> RandomForestClassifier | None:
    if not X_train or not y_train:
        return None
    if len(set(y_train)) < 1:
        return None
    # Fixed 3B baseline — not configurable via WalkForwardConfig
    clf = RandomForestClassifier(
        n_estimators=DEFAULT_N_ESTIMATORS,
        max_depth=DEFAULT_MAX_DEPTH,
        random_state=DEFAULT_RANDOM_SEED,
        n_jobs=1,
    )
    clf.fit(X_train, y_train)
    return clf


def _run_fold(
    dataset: MLDataset,
    fold: WalkForwardFold,
    config: WalkForwardConfig,
) -> WalkForwardFoldResult:
    labeled = dataset.labeled
    X_train, y_train = _matrix_from_labeled(labeled, fold.train_indices)
    X_test, y_test = _matrix_from_labeled(labeled, fold.test_indices)
    pos_tr = sum(1 for v in y_train if v == 1)
    neg_tr = len(y_train) - pos_tr
    pos_te = sum(1 for v in y_test if v == 1)
    neg_te = len(y_test) - pos_te
    train_epochs = [labeled[i].signal_epoch for i in fold.train_indices]
    test_epochs = [labeled[i].signal_epoch for i in fold.test_indices]
    if max(train_epochs) >= min(test_epochs):
        raise ValueError(
            f"fold {fold.fold_index}: leakage — max(train_epoch) >= min(test_epoch)"
        )
    clf = _fit_fold_model(X_train, y_train, config)
    if clf is None:
        return WalkForwardFoldResult(
            fold=fold,
            train_count=fold.train_count,
            test_count=fold.test_count,
            labeled_train_count=len(y_train),
            labeled_test_count=len(y_test),
            positive_train_count=pos_tr,
            negative_train_count=neg_tr,
            positive_test_count=pos_te,
            negative_test_count=neg_te,
            predictions_count=0,
            evaluation=None,
            y_true=(),
            y_pred=(),
            y_score=(),
            evaluable=False,
            skip_reason="empty_or_invalid_training_labels",
        )
    n_classes = len(set(y_train))
    if n_classes < 2:
        only = y_train[0]
        y_pred = [only] * len(y_test)
        y_score = [1.0 if only == 1 else 0.0] * len(y_test)
        report = evaluate_predictions(
            y_test, y_pred, y_score=y_score, partition=f"fold_{fold.fold_index}"
        )
        return WalkForwardFoldResult(
            fold=fold,
            train_count=fold.train_count,
            test_count=fold.test_count,
            labeled_train_count=len(y_train),
            labeled_test_count=len(y_test),
            positive_train_count=pos_tr,
            negative_train_count=neg_tr,
            positive_test_count=pos_te,
            negative_test_count=neg_te,
            predictions_count=len(y_pred),
            evaluation=report,
            y_true=tuple(y_test),
            y_pred=tuple(y_pred),
            y_score=tuple(y_score),
            evaluable=True,
            skip_reason="one_class_training",
            metadata={"train_classes": n_classes},
        )
    y_pred, y_score = predict(clf, X_test)
    report = evaluate_predictions(
        y_test, y_pred, y_score=y_score, partition=f"fold_{fold.fold_index}"
    )
    return WalkForwardFoldResult(
        fold=fold,
        train_count=fold.train_count,
        test_count=fold.test_count,
        labeled_train_count=len(y_train),
        labeled_test_count=len(y_test),
        positive_train_count=pos_tr,
        negative_train_count=neg_tr,
        positive_test_count=pos_te,
        negative_test_count=neg_te,
        predictions_count=len(y_pred),
        evaluation=report,
        y_true=tuple(y_test),
        y_pred=tuple(y_pred),
        y_score=tuple(y_score),
        evaluable=True,
        skip_reason=None,
    )


def run_walk_forward_validation(
    dataset: MLDataset,
    config: WalkForwardConfig,
) -> WalkForwardResult:
    """Run expanding-window walk-forward validation on a 3B dataset."""
    labeled = dataset.labeled
    folds = generate_folds(dataset, config)
    seen_test: set[tuple[str, int, str]] = set()
    fold_results: list[WalkForwardFoldResult] = []
    for fold in folds:
        for i in fold.test_indices:
            key = _identity(labeled[i])
            if key in seen_test:
                raise ValueError(
                    f"test observation {key} appears in more than one fold"
                )
            seen_test.add(key)
        fold_results.append(_run_fold(dataset, fold, config))
    all_true: list[int] = []
    all_pred: list[int] = []
    all_score: list[float] = []
    total_test = 0
    total_labeled_test = 0
    for fr in fold_results:
        total_test += fr.test_count
        total_labeled_test += fr.labeled_test_count
        if fr.evaluable and fr.y_true:
            all_true.extend(fr.y_true)
            all_pred.extend(fr.y_pred)
            all_score.extend(fr.y_score)
    aggregate: EvaluationReport | None
    if all_true:
        aggregate = evaluate_predictions(
            all_true, all_pred, y_score=all_score, partition="walk_forward_oos"
        )
    else:
        aggregate = None
    return WalkForwardResult(
        fold_results=tuple(fold_results),
        total_folds=len(fold_results),
        total_test_observations=total_test,
        total_labeled_test_observations=total_labeled_test,
        aggregate_evaluation=aggregate,
        config=config,
        metadata={
            "target_policy": str(dataset.target_policy),
            "schema_version": dataset.schema.version,
            "n_labeled": len(labeled),
        },
    )


def default_walk_forward_config(n_labeled: int) -> WalkForwardConfig:
    """Deterministic default sizes from labeled count (≈60% train, 10% test)."""
    if n_labeled < 10:
        raise ValueError(
            f"need at least 10 labeled observations for default config, got {n_labeled}"
        )
    initial_train = max(1, int(n_labeled * 0.60))
    test_size = max(1, int(n_labeled * 0.10))
    if initial_train + test_size > n_labeled:
        initial_train = max(1, n_labeled - test_size)
    return WalkForwardConfig(
        initial_train_size=initial_train,
        test_size=test_size,
        step_size=test_size,
    )
