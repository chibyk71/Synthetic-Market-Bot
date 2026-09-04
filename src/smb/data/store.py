"""Parquet-backed tick store (source of truth for historical datasets).

Layout
------
Under ``root``::

    {root}/
      ticks/
        instrument={key}/
          year={YYYY}/
            month={MM}/
              part-*.parquet

Rows: instrument, epoch, price, source_order.

``source_order`` is a monotonically increasing ingestion sequence used so
dataset-level validation can detect non-monotonic *source* ordering
(epoch going backward relative to write/ingest order). Query results for
time ranges remain chronological (ORDER BY epoch).

Writes are page/partition-oriented. Callers should prefer ``write_page``
so a multi-month stream is never held in memory.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from smb.data.models import StoredTick

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("epoch", pa.int64()),
        ("price", pa.float64()),
        ("source_order", pa.int64()),
    ]
)

_STREAM_FLUSH_THRESHOLD = 5_000


class ParquetTickStore:
    """Durable Parquet dataset for historical ticks."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.ticks_dir = self.root / "ticks"
        self.ticks_dir.mkdir(parents=True, exist_ok=True)
        self._source_order_counter: int | None = None

    def write_ticks(
        self,
        ticks: Iterable[StoredTick],
        *,
        dedupe: bool = True,
    ) -> int:
        """Append ticks with bounded per-partition buffers."""
        buffers: dict[tuple[str, int, int], list[StoredTick]] = {}
        written = 0

        def flush_key(key: tuple[str, int, int]) -> int:
            group = buffers.pop(key, [])
            if not group:
                return 0
            return self._write_partition(key[0], key[1], key[2], group, dedupe=dedupe)

        for tick in ticks:
            if not tick.instrument or tick.epoch < 0:
                continue
            y, m = _year_month(tick.epoch)
            key = (tick.instrument, y, m)
            buf = buffers.setdefault(key, [])
            buf.append(tick)
            if len(buf) >= _STREAM_FLUSH_THRESHOLD:
                written += flush_key(key)

        for key in list(buffers):
            written += flush_key(key)
        return written

    def write_page(
        self,
        ticks: Sequence[StoredTick],
        *,
        dedupe: bool = True,
    ) -> int:
        """Write a single page/batch (preferred ingestion path)."""
        if not ticks:
            return 0
        return self.write_ticks(ticks, dedupe=dedupe)

    def read_ticks(
        self,
        instrument: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> Iterator[StoredTick]:
        """Stream ticks via DuckDB (does not materialise whole partitions).

        Range semantics (half-open when both bounds set)::

            start_epoch <= epoch < end_epoch

        Results are chronological (ORDER BY epoch, price).

        Prefer :class:`~smb.data.repository.TickRepository` for application
        code; this method is a thin DuckDB-backed store helper.
        """
        instrument_dir = self.ticks_dir / f"instrument={instrument}"
        if not instrument_dir.exists():
            return

        pattern = str(instrument_dir / "**" / "*.parquet")
        clauses = ["instrument = ?"]
        params: list[object] = [instrument]
        if start_epoch is not None:
            clauses.append("epoch >= ?")
            params.append(start_epoch)
        if end_epoch is not None:
            clauses.append("epoch < ?")
            params.append(end_epoch)
        where = " AND ".join(clauses)

        sql = f"""
            SELECT instrument, epoch, price
            FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
            WHERE {where}
            ORDER BY epoch ASC, price ASC
        """
        con = duckdb.connect()
        try:
            result = con.execute(sql, [pattern, *params])
            while True:
                row = result.fetchone()
                if row is None:
                    break
                yield StoredTick(
                    instrument=str(row[0]),
                    epoch=int(row[1]),
                    price=float(row[2]),
                )
        except duckdb.Error as exc:
            from smb.data.repository import StorageError

            raise StorageError(
                f"Failed to read ticks for instrument={instrument!r}: {exc}"
            ) from exc
        finally:
            con.close()

    def list_instruments(self) -> list[str]:
        if not self.ticks_dir.exists():
            return []
        names: list[str] = []
        for p in sorted(self.ticks_dir.iterdir()):
            if p.is_dir() and p.name.startswith("instrument="):
                names.append(p.name.split("=", 1)[1])
        return names

    def partition_paths(self, instrument: str) -> list[Path]:
        instrument_dir = self.ticks_dir / f"instrument={instrument}"
        if not instrument_dir.exists():
            return []
        return sorted(instrument_dir.rglob("*.parquet"))

    def inspect(self) -> dict[str, Any]:
        from smb.data.repository import TickRepository

        repo = TickRepository(self)
        info: dict[str, Any] = {"root": str(self.root), "instruments": {}}
        for instrument in self.list_instruments():
            info["instruments"][instrument] = repo.coverage(instrument)
        return info

    def next_source_order(self, count: int) -> int:
        """Allocate ``count`` consecutive source_order values; return the first."""
        if self._source_order_counter is None:
            self._source_order_counter = self._max_source_order() + 1
        start = self._source_order_counter
        self._source_order_counter += count
        return start

    def _max_source_order(self) -> int:
        """Max source_order across the whole dataset (0 if empty)."""
        if not self.ticks_dir.exists():
            return 0
        paths = list(self.ticks_dir.rglob("*.parquet"))
        if not paths:
            return 0
        pattern = str(self.ticks_dir / "**" / "*.parquet")
        con = duckdb.connect()
        try:
            row = con.execute(
                """
                SELECT COALESCE(MAX(source_order), 0)
                FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
                """,
                [pattern],
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except duckdb.Error:
            return 0
        finally:
            con.close()

    def _write_partition(
        self,
        instrument: str,
        year: int,
        month: int,
        group: list[StoredTick],
        *,
        dedupe: bool,
    ) -> int:
        path = self._partition_path(instrument, year, month)
        path.parent.mkdir(parents=True, exist_ok=True)

        if dedupe:
            seen: set[tuple[int, float]] = set()
            unique: list[StoredTick] = []
            for t in group:
                key = (t.epoch, t.price)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(t)
            group = unique

        if dedupe and path.exists():
            existing = self._load_keys(path)
            group = [t for t in group if (t.epoch, t.price) not in existing]
        if not group:
            return 0

        start_order = self.next_source_order(len(group))
        orders = list(range(start_order, start_order + len(group)))
        table = _ticks_to_table(group, orders)

        if path.exists():
            old = pq.read_table(path)
            old = _ensure_source_order_column(old)
            table = pa.concat_tables([old.cast(_SCHEMA), table.cast(_SCHEMA)])
        pq.write_table(table, path, compression="zstd")
        logger.debug(
            "Wrote %s ticks to %s",
            len(group),
            path.relative_to(self.root),
        )
        return len(group)

    def _partition_path(self, instrument: str, year: int, month: int) -> Path:
        return (
            self.ticks_dir
            / f"instrument={instrument}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "part-000.parquet"
        )

    def _load_keys(self, path: Path) -> set[tuple[int, float]]:
        table = pq.read_table(path, columns=["epoch", "price"])
        epochs = table.column("epoch").to_pylist()
        prices = table.column("price").to_pylist()
        return set(zip(epochs, prices, strict=True))


def _year_month(epoch: int) -> tuple[int, int]:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.year, dt.month


def _ticks_to_table(ticks: Sequence[StoredTick], orders: Sequence[int]) -> pa.Table:
    return pa.table(
        {
            "instrument": [t.instrument for t in ticks],
            "epoch": [t.epoch for t in ticks],
            "price": [t.price for t in ticks],
            "source_order": list(orders),
        },
        schema=_SCHEMA,
    )


def _ensure_source_order_column(table: pa.Table) -> pa.Table:
    if "source_order" in table.column_names:
        return table
    n = table.num_rows
    return table.append_column("source_order", pa.array(range(n), type=pa.int64()))
