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

Partitioning by instrument then year/month keeps time-range queries
efficient for ~15M ticks per instrument without a distributed lake.

Each row: instrument (string), epoch (int64), price (float64).

Writes are page/partition-oriented: callers should pass one page (or a
modest batch) at a time so the full multi-month stream is never held in
memory. Within each write, duplicates are removed both against the
incoming batch and against existing rows on disk.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from smb.data.models import StoredTick

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("epoch", pa.int64()),
        ("price", pa.float64()),
    ]
)

# Soft upper bound on how many ticks we buffer per partition key while
# streaming an iterable. Prevents unbounded growth if a caller passes a
# very large generator without page boundaries.
_STREAM_FLUSH_THRESHOLD = 5_000


class ParquetTickStore:
    """Durable Parquet dataset for historical ticks."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.ticks_dir = self.root / "ticks"
        self.ticks_dir.mkdir(parents=True, exist_ok=True)

    def write_ticks(
        self,
        ticks: Iterable[StoredTick],
        *,
        dedupe: bool = True,
    ) -> int:
        """Append ticks in streaming fashion.

        Ticks are grouped by (instrument, year, month). When a partition
        buffer reaches :data:`_STREAM_FLUSH_THRESHOLD` rows, or when the
        iterable ends, that partition is flushed to disk. This keeps peak
        memory proportional to one partition batch, not the full dataset.

        Deduplication (when ``dedupe=True``):
        1. Within the incoming batch (first occurrence wins).
        2. Against existing rows already on disk for that partition.
        """
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
        """Write a single page/batch of ticks (preferred ingestion path).

        Deduplicates within the page and against existing partition data.
        """
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
        """Yield ticks for ``instrument`` in chronological order.

        Range semantics (half-open when both bounds set)::

            start_epoch <= epoch < end_epoch
        """
        instrument_dir = self.ticks_dir / f"instrument={instrument}"
        if not instrument_dir.exists():
            return

        paths = sorted(instrument_dir.rglob("*.parquet"))
        for path in paths:
            table = pq.read_table(
                path,
                columns=["instrument", "epoch", "price"],
                schema=_SCHEMA,
            )
            epochs = table.column("epoch").to_pylist()
            prices = table.column("price").to_pylist()
            instruments = table.column("instrument").to_pylist()
            rows = list(zip(instruments, epochs, prices, strict=True))
            rows.sort(key=lambda r: (r[1], r[2]))
            for inst, epoch, price in rows:
                if start_epoch is not None and epoch < start_epoch:
                    continue
                if end_epoch is not None and epoch >= end_epoch:
                    continue
                yield StoredTick(instrument=inst, epoch=int(epoch), price=float(price))

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
        """Lightweight coverage summary via DuckDB (no full Python materialisation)."""
        from smb.data.repository import TickRepository

        repo = TickRepository(self)
        info: dict[str, Any] = {"root": str(self.root), "instruments": {}}
        for instrument in self.list_instruments():
            info["instruments"][instrument] = repo.coverage(instrument)
        return info

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

        # 1) Dedupe within incoming batch (first occurrence wins).
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

        # 2) Dedupe against existing on-disk rows.
        if dedupe and path.exists():
            existing = self._load_keys(path)
            group = [t for t in group if (t.epoch, t.price) not in existing]
        if not group:
            return 0

        table = _ticks_to_table(group)
        if path.exists():
            old = pq.read_table(path, schema=_SCHEMA)
            table = pa.concat_tables([old, table])
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


def _ticks_to_table(ticks: Sequence[StoredTick]) -> pa.Table:
    return pa.table(
        {
            "instrument": [t.instrument for t in ticks],
            "epoch": [t.epoch for t in ticks],
            "price": [t.price for t in ticks],
        },
        schema=_SCHEMA,
    )
