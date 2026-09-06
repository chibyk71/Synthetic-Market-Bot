"""Strategy validation calculator (Milestone 3A).

Aggregates completed 2C :class:`~smb.simulation.models.TradeSimulationResult`
and optional 2D :class:`~smb.research.models.TradeResearchMetrics` into an
immutable :class:`~smb.validation.models.StrategyValidationReport`.

Does not re-run simulation, alter outcomes, or introduce future information.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence

from smb.research.models import TradeResearchMetrics
from smb.simulation.models import SimulationOutcome, TradeSimulationResult
from smb.validation.models import (
    CohortStats,
    DurationStats,
    ExcursionStats,
    OutcomeCounts,
    RateStats,
    StrategyValidationReport,
)


class StrategyValidationCalculator:
    """Deterministic aggregation of historical simulation / research results."""

    def validate(
        self,
        simulations: Sequence[TradeSimulationResult],
        metrics: Sequence[TradeResearchMetrics] | None = None,
    ) -> StrategyValidationReport:
        """Build a :class:`StrategyValidationReport`.

        Parameters
        ----------
        simulations:
            Completed 2C results (candidates, including NO_FILL).
        metrics:
            Optional parallel 2D metrics, same length and order as
            ``simulations``. When provided, each item must be consistent with
            the corresponding simulation (instrument, direction, signal_epoch,
            outcome, filled). Excursion aggregates use this series.
        """
        if metrics is not None:
            if len(metrics) != len(simulations):
                raise ValueError(
                    "metrics length must match simulations length "
                    f"({len(metrics)} != {len(simulations)})"
                )
            for i, (sim, met) in enumerate(zip(simulations, metrics, strict=True)):
                self._assert_pair_consistent(sim, met, index=i)

        overall = self._cohort(simulations, metrics)
        by_direction = self._by_direction(simulations, metrics)
        by_instrument = self._by_instrument(simulations, metrics)
        by_outcome = self._outcome_histogram(simulations)

        return StrategyValidationReport(
            overall=overall,
            by_direction=by_direction,
            by_instrument=by_instrument,
            by_outcome=by_outcome,
        )

    @staticmethod
    def _assert_pair_consistent(
        sim: TradeSimulationResult,
        met: TradeResearchMetrics,
        *,
        index: int,
    ) -> None:
        if (
            met.instrument != sim.instrument
            or met.direction != sim.direction
            or met.signal_epoch != sim.signal_epoch
            or met.outcome != sim.outcome
            or met.filled != sim.filled
        ):
            raise ValueError(
                f"metrics[{index}] is inconsistent with simulations[{index}]"
            )
        # Prefer identity when the metrics object embeds the same result
        if met.simulation is not sim and (
            met.simulation.outcome != sim.outcome
            or met.simulation.signal_epoch != sim.signal_epoch
        ):
            raise ValueError(
                f"metrics[{index}].simulation does not match simulations[{index}]"
            )

    def _by_direction(
        self,
        simulations: Sequence[TradeSimulationResult],
        metrics: Sequence[TradeResearchMetrics] | None,
    ) -> dict[str, CohortStats]:
        buckets: dict[str, list[int]] = defaultdict(list)
        for i, sim in enumerate(simulations):
            buckets[sim.direction.value].append(i)
        return {
            key: self._cohort_at(simulations, metrics, indices)
            for key, indices in sorted(buckets.items())
        }

    def _by_instrument(
        self,
        simulations: Sequence[TradeSimulationResult],
        metrics: Sequence[TradeResearchMetrics] | None,
    ) -> dict[str, CohortStats]:
        buckets: dict[str, list[int]] = defaultdict(list)
        for i, sim in enumerate(simulations):
            buckets[sim.instrument].append(i)
        return {
            key: self._cohort_at(simulations, metrics, indices)
            for key, indices in sorted(buckets.items())
        }

    @staticmethod
    def _outcome_histogram(
        simulations: Sequence[TradeSimulationResult],
    ) -> dict[str, int]:
        counts = {o.value: 0 for o in SimulationOutcome}
        for sim in simulations:
            counts[sim.outcome.value] += 1
        return counts

    def _cohort(
        self,
        simulations: Sequence[TradeSimulationResult],
        metrics: Sequence[TradeResearchMetrics] | None,
    ) -> CohortStats:
        return self._cohort_at(
            simulations, metrics, list(range(len(simulations)))
        )

    def _cohort_at(
        self,
        simulations: Sequence[TradeSimulationResult],
        metrics: Sequence[TradeResearchMetrics] | None,
        indices: Sequence[int],
    ) -> CohortStats:
        subset = [simulations[i] for i in indices]
        counts = self._counts(subset)
        rates = self._rates(counts)
        if metrics is None:
            excursions = ExcursionStats(
                avg_mfe=None,
                avg_mae=None,
                median_mfe=None,
                median_mae=None,
                sample_size=0,
            )
        else:
            met_subset = [metrics[i] for i in indices]
            excursions = self._excursions(met_subset)
        duration = self._duration(subset)
        return CohortStats(
            counts=counts,
            rates=rates,
            excursions=excursions,
            duration=duration,
        )

    @staticmethod
    def _counts(simulations: Sequence[TradeSimulationResult]) -> OutcomeCounts:
        total = len(simulations)
        filled = 0
        no_fill = 0
        wins = 0
        losses = 0
        timeouts = 0
        for sim in simulations:
            if sim.outcome == SimulationOutcome.NO_FILL or not sim.filled:
                no_fill += 1
                continue
            filled += 1
            if sim.outcome == SimulationOutcome.TP:
                wins += 1
            elif sim.outcome == SimulationOutcome.SL:
                losses += 1
            elif sim.outcome == SimulationOutcome.TIMEOUT:
                timeouts += 1
            else:
                raise ValueError(f"unexpected filled outcome: {sim.outcome}")
        return OutcomeCounts(
            total=total,
            filled=filled,
            no_fill=no_fill,
            wins=wins,
            losses=losses,
            timeouts=timeouts,
        )

    @staticmethod
    def _rates(counts: OutcomeCounts) -> RateStats:
        fill_rate = (
            counts.filled / counts.total if counts.total > 0 else None
        )
        if counts.filled > 0:
            win_rate = counts.wins / counts.filled
            loss_rate = counts.losses / counts.filled
            timeout_rate = counts.timeouts / counts.filled
        else:
            win_rate = None
            loss_rate = None
            timeout_rate = None
        return RateStats(
            fill_rate=fill_rate,
            win_rate=win_rate,
            loss_rate=loss_rate,
            timeout_rate=timeout_rate,
        )

    @staticmethod
    def _excursions(metrics: Sequence[TradeResearchMetrics]) -> ExcursionStats:
        mfes: list[float] = []
        maes: list[float] = []
        for met in metrics:
            if not met.filled:
                continue
            if met.mfe is None or met.mae is None:
                raise ValueError("filled metrics must define mfe and mae")
            mfes.append(met.mfe)
            maes.append(met.mae)
        n = len(mfes)
        if n == 0:
            return ExcursionStats(
                avg_mfe=None,
                avg_mae=None,
                median_mfe=None,
                median_mae=None,
                sample_size=0,
            )
        return ExcursionStats(
            avg_mfe=statistics.fmean(mfes),
            avg_mae=statistics.fmean(maes),
            median_mfe=float(statistics.median(mfes)),
            median_mae=float(statistics.median(maes)),
            sample_size=n,
        )

    @staticmethod
    def _duration(simulations: Sequence[TradeSimulationResult]) -> DurationStats:
        durations: list[float] = []
        for sim in simulations:
            if not sim.filled:
                continue
            if sim.duration_seconds is None:
                continue
            durations.append(float(sim.duration_seconds))
        if not durations:
            return DurationStats(avg_duration_seconds=None, sample_size=0)
        return DurationStats(
            avg_duration_seconds=statistics.fmean(durations),
            sample_size=len(durations),
        )
