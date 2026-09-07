"""Historical research harness: end-to-end orchestration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick
from smb.research.experiment import (
    ExperimentConfig,
    ExperimentError,
    HistoricalResearchExperiment,
    run_experiment,
)


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _write_ticks(store: ParquetTickStore, instrument: str, ticks: list[tuple[int, float]]) -> None:
    store.write_ticks([_st(instrument, e, p) for e, p in ticks])
    store.reindex_source_order(instrument)


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "dataset")


def _flat_series(start: int, n: int, price: float = 100.0) -> list[tuple[int, float]]:
    """n consecutive 1s ticks at constant price (forms M1/M15 with body=0)."""
    return [(start + i, price) for i in range(n)]


def test_invalid_range_raises(store: ParquetTickStore):
    _write_ticks(store, "vol", [(100, 1.0), (101, 1.1)])
    repo = TickRepository(store)
    with pytest.raises((ExperimentError, ValueError), match="start_epoch"):
        HistoricalResearchExperiment(
            repo,
            config=ExperimentConfig(instrument="vol", start_epoch=200, end_epoch=100),
        ).run()


def test_missing_instrument_raises(store: ParquetTickStore):
    _write_ticks(store, "vol", [(100, 1.0), (101, 1.1)])
    repo = TickRepository(store)
    with pytest.raises(ExperimentError, match="not found"):
        HistoricalResearchExperiment(
            repo, config=ExperimentConfig(instrument="missing")
        ).run()


def test_empty_range_raises(store: ParquetTickStore):
    _write_ticks(store, "vol", [(100, 1.0), (101, 1.1), (102, 1.2)])
    repo = TickRepository(store)
    with pytest.raises(ExperimentError, match="no ticks"):
        HistoricalResearchExperiment(
            repo,
            config=ExperimentConfig(instrument="vol", start_epoch=500, end_epoch=600),
        ).run()


def test_non_monotonic_dataset_refused(store: ParquetTickStore):
    store.write_ticks(
        [_st("vol", 100, 1.0), _st("vol", 99, 1.0), _st("vol", 101, 1.0)],
        dedupe=False,
    )
    repo = TickRepository(store)
    assert repo.coverage("vol")["non_monotonic_count"] >= 1
    with pytest.raises(ExperimentError, match="non-monotonic"):
        HistoricalResearchExperiment(
            repo, config=ExperimentConfig(instrument="vol")
        ).run()


def test_basic_pipeline_runs(store: ParquetTickStore):
    """Enough ticks for several M1 and at least one M15; may yield 0 signals."""
    ticks = _flat_series(1_700_000_000, 40 * 60, price=100.0)
    _write_ticks(store, "vol", ticks)
    result = HistoricalResearchExperiment(
        TickRepository(store),
        config=ExperimentConfig(instrument="vol"),
    ).run()
    assert result.summary.ticks_processed == len(ticks)
    assert result.summary.m1_candles >= 30
    assert result.summary.m15_candles >= 1
    assert result.summary.signals >= 0
    assert result.summary.candidates_accepted + result.summary.candidates_rejected == (
        result.summary.signals
    )


def test_determinism(store: ParquetTickStore):
    ticks = _flat_series(1_700_000_000, 20 * 60, price=50.0)
    ticks = [(e, 50.0 + (i % 17) * 0.01) for i, (e, _) in enumerate(ticks)]
    _write_ticks(store, "vol", ticks)
    cfg = ExperimentConfig(instrument="vol")
    repo = TickRepository(store)
    a = HistoricalResearchExperiment(repo, config=cfg).run()
    b = HistoricalResearchExperiment(repo, config=cfg).run()
    assert a.summary == b.summary
    assert len(a.rows) == len(b.rows)
    assert [r.signal_epoch for r in a.rows] == [r.signal_epoch for r in b.rows]
    assert [r.outcome for r in a.rows] == [r.outcome for r in b.rows]


def test_m15_before_m1_at_shared_boundary():
    """Unit: shared end_epoch ordering helper matches 4B (M15 then M1)."""
    from smb.market.candles import Candle
    from smb.research.experiment import _order_finalized_pair

    end = 900
    m1 = Candle(
        timeframe="M1",
        start_epoch=end - 60,
        end_epoch=end,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        tick_count=1,
        finalized=True,
    )
    m15 = Candle(
        timeframe="M15",
        start_epoch=end - 900,
        end_epoch=end,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        tick_count=1,
        finalized=True,
    )
    ordered = _order_finalized_pair(m1, m15)
    assert [t for t, _ in ordered] == ["M15", "M1"]


def test_run_experiment_convenience(store: ParquetTickStore, tmp_path: Path):
    ticks = _flat_series(1_700_000_000, 16 * 60, price=10.0)
    _write_ticks(store, "step", ticks)
    result = run_experiment(store.root, instrument="step")
    assert result.summary.ticks_processed == len(ticks)
    assert result.summary.instrument == "step"


def test_cli_run_help():
    from smb.research.__main__ import main

    with pytest.raises(SystemExit) as ei:
        main(["run", "--help"])
    assert ei.value.code == 0


def test_simulation_streams_without_materializing_full_window(store: ParquetTickStore):
    """Harness must stream simulation ticks; stream call count stays small."""
    ticks = [(1_700_000_000 + i, 100.0 + (i % 10) * 0.01) for i in range(2_000)]
    _write_ticks(store, "vol", ticks)
    repo = TickRepository(store)

    class _Wrap(TickRepository):
        def __init__(self, inner: TickRepository) -> None:
            super().__init__(inner.store)
            self.inner = inner
            self.stream_calls = 0

        def as_tick_stream(self, instrument, *, start_epoch=None, end_epoch=None):
            self.stream_calls += 1
            gen = self.inner.as_tick_stream(
                instrument, start_epoch=start_epoch, end_epoch=end_epoch
            )

            def _guarded():
                yield from gen

            return _guarded()

    wrap = _Wrap(repo)
    result = HistoricalResearchExperiment(
        wrap, config=ExperimentConfig(instrument="vol")
    ).run()
    assert wrap.stream_calls <= 2
    assert result.summary.ticks_processed == 2000


def test_explicit_end_epoch_limits_simulation_ticks(store: ParquetTickStore):
    """Simulation must not consume ticks with epoch >= experiment end_epoch."""
    start = 1_700_000_000
    ticks = [(start + i, 100.0) for i in range(3_000)]
    _write_ticks(store, "vol", ticks)
    end = start + 1_500
    repo = TickRepository(store)

    class _Spy(TickRepository):
        def __init__(self, inner: TickRepository) -> None:
            super().__init__(inner.store)
            self.inner = inner
            self.sim_epochs: list[int] = []
            self.calls = 0

        def as_tick_stream(self, instrument, *, start_epoch=None, end_epoch=None):
            self.calls += 1
            for t in self.inner.as_tick_stream(
                instrument, start_epoch=start_epoch, end_epoch=end_epoch
            ):
                if self.calls >= 2:
                    self.sim_epochs.append(t.epoch)
                yield t

    spy = _Spy(repo)
    result = HistoricalResearchExperiment(
        spy,
        config=ExperimentConfig(instrument="vol", start_epoch=start, end_epoch=end),
    ).run()
    assert result.summary.ticks_processed == 1500
    if spy.sim_epochs:
        assert max(spy.sim_epochs) < end


def test_long_stream_orchestration_does_not_require_full_list(store: ParquetTickStore):
    """Larger synthetic series still completes via streaming."""
    start = 1_700_000_000
    n = 12_000
    ticks = [(start + i, 80.0 + (i % 50) * 0.02) for i in range(n)]
    _write_ticks(store, "vol", ticks)
    result = HistoricalResearchExperiment(
        TickRepository(store),
        config=ExperimentConfig(instrument="vol"),
    ).run()
    assert result.summary.ticks_processed == n
    assert result.summary.m1_candles >= 100
