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

Writes are append-only with optional deduplication against existing
keys for the same instrument. The store never loads the full dataset
into memory for range reads.
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
        """Append ticks, optionally skipping known (instrument, epoch, price) keys.

        Returns the number of rows actually written.
        Batches by instrument and by calendar month so large streams do not
        materialise entirely in memory beyond one batch buffer.
        """
        batches: dict[tuple[str, int, int], list[StoredTick]] = {}
        for tick in ticks:
            if not tick.instrument or tick.epoch < 0:
                continue
            y, m = _year_month(tick.epoch)
            key = (tick.instrument, y, m)
            batches.setdefault(key, []).append(tick)

        written = 0
        for (instrument, year, month), group in batches.items():
            path = self._partition_path(instrument, year, month)
            path.parent.mkdir(parents=True, exist_ok=True)

            if dedupe and path.exists():
                existing = self._load_keys(path)
                group = [t for t in group if (t.epoch, t.price) not in existing]
            if not group:
                continue

            table = _ticks_to_table(group)
            if path.exists():
                old = pq.read_table(path, schema=_SCHEMA)
                table = pa.concat_tables([old, table])
            pq.write_table(table, path, compression="zstd")
            written += len(group)
            logger.debug(
                "Wrote %s ticks to %s",
                len(group),
                path.relative_to(self.root),
            )
        return written

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

        If only ``start_epoch`` is set: ``epoch >= start_epoch``.
        If only ``end_epoch`` is set: ``epoch < end_epoch``.
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

    def inspect(self) -> dict[str, Any]:
        """Lightweight dataset summary without full scans of all prices."""
        info: dict[str, Any] = {"root": str(self.root), "instruments": {}}
        for instrument in self.list_instruments():
            count = 0
            min_ep: int | None = None
            max_ep: int | None = None
            for tick in self.read_ticks(instrument):
                count += 1
                if min_ep is None or tick.epoch < min_ep:
                    min_ep = tick.epoch
                if max_ep is None or tick.epoch > max_ep:
                    max_ep = tick.epoch
            info["instruments"][instrument] = {
                "tick_count": count,
                "earliest_epoch": min_ep,
                "latest_epoch": max_ep,
            }
        return info

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
