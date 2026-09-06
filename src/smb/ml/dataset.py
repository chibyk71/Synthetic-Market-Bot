"""Build chronologically ordered ML datasets from simulation results.

Does not re-run simulations or strategy logic. Consumes completed domain
objects and extracts signal-time features only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from smb.ml.features import extract_features, feature_schema
from smb.ml.models import (
    MLDataset,
    MLObservation,
    TargetPolicy,
)
from smb.research.models import TradeResearchMetrics
from smb.simulation.models import SimulationOutcome, TradeSimulationResult
from smb.strategy.models import Direction, StrategySignal


def resolve_target(
    outcome: SimulationOutcome,
    policy: TargetPolicy,
) -> int | None:
    """Map simulation outcome → binary target under ``policy``.

    Returns ``None`` when the row should be excluded from supervised training
    (e.g. NO_FILL under ``FILLED_TP_POSITIVE``).
    """
    if policy is TargetPolicy.FILLED_TP_POSITIVE:
        if outcome is SimulationOutcome.NO_FILL:
            return None
        if outcome is SimulationOutcome.TP:
            return 1
        if outcome in (SimulationOutcome.SL, SimulationOutcome.TIMEOUT):
            return 0
        raise ValueError(f"unhandled outcome: {outcome}")
    if policy is TargetPolicy.INCLUDE_NO_FILL_AS_NEGATIVE:
        if outcome is SimulationOutcome.TP:
            return 1
        return 0
    raise ValueError(f"unknown policy: {policy}")


def _identity_key(
    instrument: str, signal_epoch: int, direction: Direction
) -> tuple[str, int, str]:
    return (instrument, signal_epoch, str(direction))


def build_observation(
    simulation: TradeSimulationResult,
    *,
    metrics: TradeResearchMetrics | None = None,
    policy: TargetPolicy = TargetPolicy.FILLED_TP_POSITIVE,
    signal: StrategySignal | None = None,
) -> MLObservation:
    """Build one MLObservation from a completed simulation result.

    Features are taken from ``simulation.candidate.source_signal`` (or an
    explicit ``signal`` override for tests). MAE/MFE from ``metrics`` are
    stored as audit fields only — never as features.
    """
    src = signal if signal is not None else simulation.candidate.source_signal
    if src.signal_epoch != simulation.signal_epoch:
        raise ValueError("signal_epoch mismatch between signal and simulation")
    if src.direction is not simulation.direction:
        raise ValueError("direction mismatch between signal and simulation")
    if src.instrument != simulation.instrument:
        raise ValueError("instrument mismatch between signal and simulation")

    feats = extract_features(src)
    target = resolve_target(simulation.outcome, policy)

    mfe = mae = None
    if metrics is not None:
        if (
            metrics.signal_epoch != simulation.signal_epoch
            or metrics.instrument != simulation.instrument
            or metrics.direction is not simulation.direction
        ):
            raise ValueError("metrics identity does not match simulation")
        mfe = metrics.mfe
        mae = metrics.mae

    return MLObservation(
        instrument=simulation.instrument,
        signal_epoch=simulation.signal_epoch,
        direction=simulation.direction,
        features=feats,
        target=target,
        outcome=simulation.outcome,
        filled=simulation.filled,
        mfe=mfe,
        mae=mae,
    )


def build_dataset(
    simulations: Sequence[TradeSimulationResult],
    *,
    metrics_by_key: dict[tuple[str, int, str], TradeResearchMetrics] | None = None,
    policy: TargetPolicy = TargetPolicy.FILLED_TP_POSITIVE,
    metadata: dict | None = None,
) -> MLDataset:
    """Construct a chronologically ordered dataset.

    Duplicate identities ``(instrument, signal_epoch, direction)`` raise
    ``ValueError`` (no silent overwrite).
    """
    metrics_by_key = metrics_by_key or {}
    seen: set[tuple[str, int, str]] = set()
    rows: list[MLObservation] = []

    for sim in simulations:
        key = _identity_key(sim.instrument, sim.signal_epoch, sim.direction)
        if key in seen:
            raise ValueError(f"duplicate observation identity: {key}")
        seen.add(key)
        m = metrics_by_key.get(key)
        rows.append(build_observation(sim, metrics=m, policy=policy))

    rows.sort(
        key=lambda o: (o.signal_epoch, o.instrument, str(o.direction))
    )

    meta = {
        "target_policy": str(policy),
        "n_input_simulations": len(simulations),
        **(metadata or {}),
    }
    return MLDataset(
        schema=feature_schema(),
        observations=tuple(rows),
        target_policy=policy,
        metadata=meta,
    )


def metrics_index(
    metrics: Iterable[TradeResearchMetrics],
) -> dict[tuple[str, int, str], TradeResearchMetrics]:
    """Index research metrics by identity key. Duplicate keys raise."""
    out: dict[tuple[str, int, str], TradeResearchMetrics] = {}
    for m in metrics:
        key = _identity_key(m.instrument, m.signal_epoch, m.direction)
        if key in out:
            raise ValueError(f"duplicate metrics identity: {key}")
        out[key] = m
    return out
