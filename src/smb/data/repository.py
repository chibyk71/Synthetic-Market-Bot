"""DuckDB-backed query layer over the Parquet tick dataset.

Callers request ticks by instrument and optional time range without
loading unrelated instruments or the full dataset into Python.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick


class TickRepository:
    """Query historical ticks via DuckDB over Parquet files.

    Range semantics (half-open when both bounds are provided)::

        start_epoch <= epoch < end_epoch

    Results are always chronological (ORDER BY epoch, price).
    """

    def __init__(self, store: ParquetTickStore) -> None:
        self.store = store
        self.root = store.root

    def get_ticks(
        self,
        instrument: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> list[StoredTick]:
        """Return matching ticks as a list (suitable for moderate ranges)."""
        return list(self.iter_ticks(instrument, start_epoch=start_epoch, end_epoch=end_epoch))

    def iter_ticks(
        self,
        instrument: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> Iterator[StoredTick]:
        """Stream matching ticks without loading the full dataset."""
        pattern = str(
            self.root / "ticks" / f"instrument={instrument}" / "**" / "*.parquet"
        )
        if not (self.root / "ticks" / f"instrument={instrument}").exists():
            return

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
        except duckdb.IOException:
            return
        finally:
            con.close()

    def as_tick_stream(
        self,
        instrument: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> Iterator[Tick]:
        """Yield Milestone 1B :class:`Tick` objects for HistoricalReplay."""
        for stored in self.iter_ticks(
            instrument, start_epoch=start_epoch, end_epoch=end_epoch
        ):
            yield stored.to_tick()

    def list_instruments(self) -> list[str]:
        return self.store.list_instruments()
