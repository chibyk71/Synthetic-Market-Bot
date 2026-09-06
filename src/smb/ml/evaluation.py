"""Deterministic evaluation metrics for Milestone 3B baseline models."""

from __future__ import annotations

from collections.abc import Sequence

from smb.ml.models import EvaluationReport


def _safe_div(num: float, den: float) -> float | None:
    if den == 0.0:
        return None
    return num / den


def evaluate_predictions(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    *,
    y_score: Sequence[float] | None = None,
    partition: str = "eval",
) -> EvaluationReport:
    """Compute classification metrics. Undefined metrics are ``None`` (never NaN)."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred length mismatch")
    n = len(y_true)
    if n == 0:
        return EvaluationReport(
            partition=partition,
            n_samples=0,
            n_positive=0,
            n_negative=0,
            accuracy=None,
            precision=None,
            recall=None,
            f1=None,
            roc_auc=None,
            confusion_matrix=None,
        )

    y_true_l = [int(v) for v in y_true]
    y_pred_l = [int(v) for v in y_pred]
    n_pos = sum(1 for v in y_true_l if v == 1)
    n_neg = n - n_pos

    tp = sum(1 for t, p in zip(y_true_l, y_pred_l, strict=True) if t == 1 and p == 1)
    tn = sum(1 for t, p in zip(y_true_l, y_pred_l, strict=True) if t == 0 and p == 0)
    fp = sum(1 for t, p in zip(y_true_l, y_pred_l, strict=True) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true_l, y_pred_l, strict=True) if t == 1 and p == 0)

    accuracy = (tp + tn) / n
    precision = _safe_div(float(tp), float(tp + fp))
    recall = _safe_div(float(tp), float(tp + fn))
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0.0:
        f1 = None
    else:
        f1 = 2.0 * precision * recall / (precision + recall)

    roc_auc: float | None = None
    if y_score is not None and n_pos > 0 and n_neg > 0 and len(y_score) == n:
        try:
            from sklearn.metrics import roc_auc_score

            roc_auc = float(roc_auc_score(y_true_l, list(y_score)))
        except Exception:
            roc_auc = None

    cm = ((tn, fp), (fn, tp))

    return EvaluationReport(
        partition=partition,
        n_samples=n,
        n_positive=n_pos,
        n_negative=n_neg,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
        roc_auc=roc_auc,
        confusion_matrix=cm,
    )
