"""Regression: multi-page historical tick source_order chronology."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from smb.data.ingest import ingest_instrument
from smb.data.models import StoredTick
from smb.data.repository import TickRepository
from smb.data.store import ParquetTickStore
from smb.data.validation import validate_ticks
from smb.deriv.history import HistoryPage, Tick


def _st(instrument: str, epoch: int, price: float) -> StoredTick:
    return StoredTick(instrument=instrument, epoch=epoch, price=price)


def _tick(epoch: int, price: float) -> Tick:
    return Tick(
        timestamp=datetime.fromtimestamp(epoch, tz=UTC),
        price=price,
        epoch=epoch,
    )


@pytest.fixture
def store(tmp_path: Path) -> ParquetTickStore:
    return ParquetTickStore(tmp_path / "dataset")


@pytest.mark.asyncio
async def test_ingest_multipage_chronological_source_order(store: ParquetTickStore):
    """Newest→oldest API pages must still yield ascending source_order epochs.

    Page layout (Deriv-style: each page chronological, pages newest first)::

        page1: 103, 104, 105
        page2: 100, 101, 102
        page3:  97,  98,  99

    After ingest, ORDER BY source_order must be 97..105 and coverage must
    report non_monotonic_count == 0.
    """
    pages = [
        HistoryPage(
            symbol="STP",
            ticks=(_tick(103, 1.0), _tick(104, 1.1), _tick(105, 1.2)),
            pip_size=0.01,
        ),
        HistoryPage(
            symbol="STP",
            ticks=(_tick(100, 0.7), _tick(101, 0.8), _tick(102, 0.9)),
            pip_size=0.01,
        ),
        HistoryPage(
            symbol="STP",
            ticks=(_tick(97, 0.4), _tick(98, 0.5), _tick(99, 0.6)),
            pip_size=0.01,
        ),
    ]

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        fake_info = MagicMock()
        fake_info.symbol = "STP"

        async def fake_load(client, detail="full"):
            return [fake_info]

        def fake_resolve(name, symbols):
            return fake_info

        call_count = {"n": 0}

        async def fake_fetch(client, symbol, *, count, end, start=1):
            idx = call_count["n"]
            call_count["n"] += 1
            return pages[idx]

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)

        result = await ingest_instrument(
            client,
            store,
            instrument="step",
            display_name="Step Index 100",
            pages=3,
            count_per_page=3,
        )

    assert result.pages_fetched == 3
    assert result.ticks_written == 9

    import duckdb

    pattern = str(store.ticks_dir / "instrument=step" / "**" / "*.parquet")
    con = duckdb.connect()
    rows = con.execute(
        """
        SELECT epoch, source_order
        FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
        ORDER BY source_order ASC
        """,
        [pattern],
    ).fetchall()
    con.close()
    epochs = [r[0] for r in rows]
    orders = [r[1] for r in rows]
    assert epochs == list(range(97, 106))
    assert orders == list(range(9))
    assert epochs == sorted(epochs)

    cov = TickRepository(store).coverage("step")
    assert cov["tick_count"] == 9
    assert cov["duplicate_count"] == 0
    assert cov["non_monotonic_count"] == 0


@pytest.mark.asyncio
async def test_ingest_incremental_remains_chronological(store: ParquetTickStore):
    """Two ingest passes (newer pages, then older) stay monotonic after reindex."""
    newer = HistoryPage(
        symbol="V",
        ticks=(_tick(200, 2.0), _tick(201, 2.1), _tick(202, 2.2)),
        pip_size=0.01,
    )
    older = HistoryPage(
        symbol="V",
        ticks=(_tick(100, 1.0), _tick(101, 1.1), _tick(102, 1.2)),
        pip_size=0.01,
    )

    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        fake_info = MagicMock()
        fake_info.symbol = "V"

        async def fake_load(client, detail="full"):
            return [fake_info]

        def fake_resolve(name, symbols):
            return fake_info

        queues = {"pages": [newer]}

        async def fake_fetch(client, symbol, *, count, end, start=1):
            return queues["pages"].pop(0)

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)

        await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="Volatility 75 (1s) Index",
            pages=1,
            count_per_page=3,
        )

        queues["pages"] = [older]
        await ingest_instrument(
            client,
            store,
            instrument="vol",
            display_name="Volatility 75 (1s) Index",
            pages=1,
            count_per_page=3,
        )

    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 6
    assert cov["duplicate_count"] == 0
    assert cov["non_monotonic_count"] == 0

    import duckdb

    pattern = str(store.ticks_dir / "instrument=vol" / "**" / "*.parquet")
    con = duckdb.connect()
    epochs = [
        r[0]
        for r in con.execute(
            """
            SELECT epoch FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
            ORDER BY source_order ASC
            """,
            [pattern],
        ).fetchall()
    ]
    con.close()
    assert epochs == [100, 101, 102, 200, 201, 202]


@pytest.mark.asyncio
async def test_ingest_dedupes_overlapping_pages(store: ParquetTickStore):
    page = HistoryPage(
        symbol="V",
        ticks=(_tick(10, 1.0), _tick(11, 1.1), _tick(12, 1.2)),
        pip_size=0.01,
    )
    client = AsyncMock()
    with pytest.MonkeyPatch.context() as mp:
        from smb.data import ingest as ingest_mod

        fake_info = MagicMock()
        fake_info.symbol = "V"

        async def fake_load(client, detail="full"):
            return [fake_info]

        def fake_resolve(name, symbols):
            return fake_info

        async def fake_fetch(client, symbol, *, count, end, start=1):
            return page

        mp.setattr(ingest_mod, "load_active_symbols", fake_load)
        mp.setattr(ingest_mod, "resolve_symbol", fake_resolve)
        mp.setattr(ingest_mod, "fetch_ticks", fake_fetch)

        r1 = await ingest_instrument(
            client, store, instrument="vol", display_name="X", pages=1, count_per_page=3
        )
        r2 = await ingest_instrument(
            client, store, instrument="vol", display_name="X", pages=1, count_per_page=3
        )

    assert r1.ticks_written == 3
    assert r2.ticks_written == 0
    cov = TickRepository(store).coverage("vol")
    assert cov["tick_count"] == 3
    assert cov["duplicate_count"] == 0
    assert cov["non_monotonic_count"] == 0


def test_raw_write_still_detects_non_monotonic_source_order(store: ParquetTickStore):
    """Validator/coverage must still see deliberate non-monotonic source_order."""
    store.write_ticks(
        [
            _st("x", 100, 1.0),
            _st("x", 99, 1.0),
            _st("x", 101, 1.0),
        ]
    )
    cov = TickRepository(store).coverage("x")
    assert cov["non_monotonic_count"] == 1
    report = validate_ticks(
        [
            _st("x", 100, 1.0),
            _st("x", 99, 1.0),
            _st("x", 101, 1.0),
        ]
    )
    assert report.non_monotonic_count == 1
    assert report.valid is False
