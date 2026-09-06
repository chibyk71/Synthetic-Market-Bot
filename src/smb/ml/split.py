"""Chronological train / validation / test splitting.

No random shuffle. Partitions are contiguous in signal_epoch order.
"""

from __future__ import annotations

from smb.ml.models import ChronologicalSplit, MLDataset


def chronological_split(
    dataset: MLDataset,
    *,
    train_ratio: float = 0.60,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
) -> ChronologicalSplit:
    """Split labeled observations by chronological proportions.

    Ratios must sum to 1.0 (within 1e-9). Empty partitions are allowed when
    the labeled set is too small; callers should check lengths.
    """
    if abs(train_ratio + validation_ratio + test_ratio - 1.0) > 1e-9:
        raise ValueError("train_ratio + validation_ratio + test_ratio must equal 1.0")
    if min(train_ratio, validation_ratio, test_ratio) < 0.0:
        raise ValueError("ratios must be >= 0")

    labeled = dataset.labeled
    n = len(labeled)
    if n == 0:
        return ChronologicalSplit(
            train_indices=(),
            validation_indices=(),
            test_indices=(),
            train_end_epoch=None,
            validation_end_epoch=None,
            test_end_epoch=None,
        )

    # Floor boundaries to keep test as the latest remainder
    n_train = int(n * train_ratio)
    n_val = int(n * validation_ratio)
    # remainder goes to test so proportions are respected as closely as possible

    train_idx = tuple(range(0, n_train))
    val_idx = tuple(range(n_train, n_train + n_val))
    test_idx = tuple(range(n_train + n_val, n))

    def _end_epoch(indices: tuple[int, ...]) -> int | None:
        if not indices:
            return None
        return labeled[indices[-1]].signal_epoch

    return ChronologicalSplit(
        train_indices=train_idx,
        validation_indices=val_idx,
        test_indices=test_idx,
        train_end_epoch=_end_epoch(train_idx),
        validation_end_epoch=_end_epoch(val_idx),
        test_end_epoch=_end_epoch(test_idx),
    )


def chronological_split_by_epochs(
    dataset: MLDataset,
    *,
    train_end_epoch: int,
    validation_end_epoch: int,
) -> ChronologicalSplit:
    """Split by explicit epoch boundaries (inclusive upper bounds).

    train: signal_epoch <= train_end_epoch
    validation: train_end_epoch < signal_epoch <= validation_end_epoch
    test: signal_epoch > validation_end_epoch
    """
    if validation_end_epoch < train_end_epoch:
        raise ValueError("validation_end_epoch must be >= train_end_epoch")

    labeled = dataset.labeled
    train_idx: list[int] = []
    val_idx: list[int] = []
    test_idx: list[int] = []

    for i, obs in enumerate(labeled):
        if obs.signal_epoch <= train_end_epoch:
            train_idx.append(i)
        elif obs.signal_epoch <= validation_end_epoch:
            val_idx.append(i)
        else:
            test_idx.append(i)

    def _end(indices: list[int]) -> int | None:
        return labeled[indices[-1]].signal_epoch if indices else None

    return ChronologicalSplit(
        train_indices=tuple(train_idx),
        validation_indices=tuple(val_idx),
        test_indices=tuple(test_idx),
        train_end_epoch=_end(train_idx),
        validation_end_epoch=_end(val_idx),
        test_end_epoch=_end(test_idx),
    )
