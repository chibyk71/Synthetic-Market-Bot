"""DuckDB-backed query layer over the Parquet tick dataset.

Callers request ticks by instrument and optional time range without
loading unrelated instruments or the full dataset into Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import duckdb

from smb.data.models import StoredTick
from smb.data.store import ParquetTickStore
from smb.deriv.history import Tick


class StorageError(RuntimeError):
    """Raised when the underlying Parquet/DuckDB dataset cannot be read."""


class TickRepository:
    """Query historical ticks via DuckDB over Parquet files.

    Range semantics (half-open when both bounds are provided)::

        start_epoch <= epoch < end_epoch

    Time-range results are chronological (ORDER BY epoch, price).

    Dataset-level non-monotonic detection uses ``source_order`` (ingestion
    sequence preserved at write time), not sorted epoch order.
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
        return list(
            self.iter_ticks(instrument, start_epoch=start_epoch, end_epoch=end_epoch)
        )

    def iter_ticks(
        self,
        instrument: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> Iterator[StoredTick]:
        """Stream matching ticks (primary application read path)."""
        instrument_dir = self.root / "ticks" / f"instrument={instrument}"
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
            raise StorageError(
                f"Failed to read ticks for instrument={instrument!r}: {exc}"
            ) from exc
        finally:
            con.close()

    def coverage(self, instrument: str) -> dict[str, Any]:
        """Compute coverage stats via SQL without materialising all ticks.

        ``non_monotonic_count`` counts consecutive pairs in **ingestion
        order** (``source_order``) where epoch decreases. Sorting by epoch
        before the check is intentionally avoided.
        """
        instrument_dir = self.root / "ticks" / f"instrument={instrument}"
        empty = {
            "tick_count": 0,
            "earliest_epoch": None,
            "latest_epoch": None,
            "min_price": None,
            "max_price": None,
            "duplicate_count": 0,
            "non_monotonic_count": 0,
        }
        if not instrument_dir.exists():
            return empty

        pattern = str(instrument_dir / "**" / "*.parquet")
        con = duckdb.connect()
        try:
            row = con.execute(
                """
                SELECT
                    COUNT(*) AS tick_count,
                    MIN(epoch) AS earliest_epoch,
                    MAX(epoch) AS latest_epoch,
                    MIN(price) AS min_price,
                    MAX(price) AS max_price,
                    COUNT(*) - COUNT(DISTINCT (epoch, price)) AS duplicate_count
                FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
                WHERE instrument = ?
                """,
                [pattern, instrument],
            ).fetchone()
            if row is None or row[0] == 0:
                return empty

            non_mono = con.execute(
                """
                WITH ordered AS (
                    SELECT epoch,
                           LAG(epoch) OVER (ORDER BY source_order ASC) AS prev_epoch
                    FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
                    WHERE instrument = ?
                )
                SELECT COUNT(*) FROM ordered
                WHERE prev_epoch IS NOT NULL AND epoch < prev_epoch
                """,
                [pattern, instrument],
            ).fetchone()

            return {
                "tick_count": int(row[0]),
                "earliest_epoch": int(row[1]) if row[1] is not None else None,
                "latest_epoch": int(row[2]) if row[2] is not None else None,
                "min_price": float(row[3]) if row[3] is not None else None,
                "max_price": float(row[4]) if row[4] is not None else None,
                "duplicate_count": int(row[5]),
                "non_monotonic_count": int(non_mono[0]) if non_mono else 0,
            }
        except duckdb.Error as exc:
            raise StorageError(
                f"Failed to compute coverage for instrument={instrument!r}: {exc}"
            ) from exc
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
