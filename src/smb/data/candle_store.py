"""Parquet storage and DuckDB query for historical OHLC candles.

Layout::

    {root}/
      candles/
        instrument={key}/
          timeframe={M1|M5|M15}/
            year={YYYY}/
              month={MM}/
                part-000.parquet

Candles are built from stored ticks via the existing CandleBuilder and
persisted for efficient range queries without replaying all ticks.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from smb.data.repository import StorageError, TickRepository
from smb.market.candles import (
    TIMEFRAME_M1,
    TIMEFRAME_M5,
    TIMEFRAME_M15,
    Candle,
    MultiTimeframeCandleBuilder,
    Timeframe,
)
from smb.market.replay import HistoricalReplay

logger = logging.getLogger(__name__)

_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("timeframe", pa.string()),
        ("start_epoch", pa.int64()),
        ("end_epoch", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("tick_count", pa.int64()),
    ]
)

DEFAULT_TIMEFRAMES: tuple[Timeframe, ...] = (
    TIMEFRAME_M1,
    TIMEFRAME_M5,
    TIMEFRAME_M15,
)


class ParquetCandleStore:
    """Durable Parquet dataset for OHLC candles."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.candles_dir = self.root / "candles"
        self.candles_dir.mkdir(parents=True, exist_ok=True)

    def write_candles(
        self,
        instrument: str,
        candles: Sequence[Candle],
        *,
        replace_range: bool = True,
    ) -> int:
        """Persist candles for one instrument.

        When ``replace_range`` is True, existing rows whose start_epoch falls
        in the written set's span for the same timeframe are removed first
        so rebuilds are deterministic and free of duplicates.
        """
        if not candles:
            return 0

        by_tf: dict[str, list[Candle]] = {}
        for c in candles:
            by_tf.setdefault(c.timeframe, []).append(c)

        written = 0
        for timeframe, group in by_tf.items():
            written += self._write_timeframe(
                instrument, timeframe, group, replace_range=replace_range
            )
        return written

    def _write_timeframe(
        self,
        instrument: str,
        timeframe: str,
        candles: Sequence[Candle],
        *,
        replace_range: bool,
    ) -> int:
        buckets: dict[tuple[int, int], list[Candle]] = {}
        for c in candles:
            y, m = _year_month(c.start_epoch)
            buckets.setdefault((y, m), []).append(c)

        total = 0
        for (year, month), group in buckets.items():
            path = self._partition_path(instrument, timeframe, year, month)
            path.parent.mkdir(parents=True, exist_ok=True)

            starts = {c.start_epoch for c in group}
            table = _candles_to_table(instrument, group)

            if path.exists():
                old = pq.read_table(path, schema=_SCHEMA)
                if replace_range:
                    mask = [
                        int(ep) not in starts
                        for ep in old.column("start_epoch").to_pylist()
                    ]
                    if any(mask):
                        keep_idx = [i for i, keep in enumerate(mask) if keep]
                        old = old.take(keep_idx)
                        table = pa.concat_tables([old, table])
                else:
                    table = pa.concat_tables([old, table])

            table = table.sort_by("start_epoch")
            pq.write_table(table, path, compression="zstd")
            total += len(group)
            logger.debug(
                "Wrote %s %s candles to %s",
                len(group),
                timeframe,
                path.relative_to(self.root),
            )
        return total

    def iter_candles(
        self,
        instrument: str,
        timeframe: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> Iterator[Candle]:
        """Stream candles via DuckDB.

        Range semantics (half-open when both bounds set)::

            start_epoch <= candle.start_epoch < end_epoch
        """
        tf_dir = (
            self.candles_dir
            / f"instrument={instrument}"
            / f"timeframe={timeframe}"
        )
        if not tf_dir.exists():
            return

        pattern = str(tf_dir / "**" / "*.parquet")
        clauses = ["instrument = ?", "timeframe = ?"]
        params: list[object] = [instrument, timeframe]
        if start_epoch is not None:
            clauses.append("start_epoch >= ?")
            params.append(start_epoch)
        if end_epoch is not None:
            clauses.append("start_epoch < ?")
            params.append(end_epoch)
        where = " AND ".join(clauses)

        sql = f"""
            SELECT instrument, timeframe, start_epoch, end_epoch,
                   open, high, low, close, tick_count
            FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
            WHERE {where}
            ORDER BY start_epoch ASC
        """
        con = duckdb.connect()
        try:
            result = con.execute(sql, [pattern, *params])
            while True:
                row = result.fetchone()
                if row is None:
                    break
                yield Candle(
                    timeframe=str(row[1]),
                    start_epoch=int(row[2]),
                    end_epoch=int(row[3]),
                    open=float(row[4]),
                    high=float(row[5]),
                    low=float(row[6]),
                    close=float(row[7]),
                    tick_count=int(row[8]),
                    finalized=True,
                )
        except duckdb.Error as exc:
            raise StorageError(
                f"Failed to read candles instrument={instrument!r} "
                f"timeframe={timeframe!r}: {exc}"
            ) from exc
        finally:
            con.close()

    def get_candles(
        self,
        instrument: str,
        timeframe: str,
        *,
        start_epoch: int | None = None,
        end_epoch: int | None = None,
    ) -> list[Candle]:
        return list(
            self.iter_candles(
                instrument,
                timeframe,
                start_epoch=start_epoch,
                end_epoch=end_epoch,
            )
        )

    def list_instruments(self) -> list[str]:
        if not self.candles_dir.exists():
            return []
        names: list[str] = []
        for p in sorted(self.candles_dir.iterdir()):
            if p.is_dir() and p.name.startswith("instrument="):
                names.append(p.name.split("=", 1)[1])
        return names

    def list_timeframes(self, instrument: str) -> list[str]:
        inst_dir = self.candles_dir / f"instrument={instrument}"
        if not inst_dir.exists():
            return []
        names: list[str] = []
        for p in sorted(inst_dir.iterdir()):
            if p.is_dir() and p.name.startswith("timeframe="):
                names.append(p.name.split("=", 1)[1])
        return names

    def coverage(self, instrument: str, timeframe: str) -> dict[str, Any]:
        """SQL aggregate coverage without loading all candles into Python."""
        empty = {
            "candle_count": 0,
            "earliest_start": None,
            "latest_start": None,
            "min_low": None,
            "max_high": None,
        }
        tf_dir = (
            self.candles_dir
            / f"instrument={instrument}"
            / f"timeframe={timeframe}"
        )
        if not tf_dir.exists():
            return empty
        pattern = str(tf_dir / "**" / "*.parquet")
        con = duckdb.connect()
        try:
            row = con.execute(
                """
                SELECT COUNT(*), MIN(start_epoch), MAX(start_epoch),
                       MIN(low), MAX(high)
                FROM read_parquet(?, hive_partitioning=1, union_by_name=True)
                WHERE instrument = ? AND timeframe = ?
                """,
                [pattern, instrument, timeframe],
            ).fetchone()
            if row is None or row[0] == 0:
                return empty
            return {
                "candle_count": int(row[0]),
                "earliest_start": int(row[1]),
                "latest_start": int(row[2]),
                "min_low": float(row[3]),
                "max_high": float(row[4]),
            }
        except duckdb.Error as exc:
            raise StorageError(
                f"Failed candle coverage {instrument}/{timeframe}: {exc}"
            ) from exc
        finally:
            con.close()

    def _partition_path(
        self, instrument: str, timeframe: str, year: int, month: int
    ) -> Path:
        return (
            self.candles_dir
            / f"instrument={instrument}"
            / f"timeframe={timeframe}"
            / f"year={year:04d}"
            / f"month={month:02d}"
            / "part-000.parquet"
        )


def build_candles_from_ticks(
    tick_repo: TickRepository,
    candle_store: ParquetCandleStore,
    *,
    instrument: str,
    timeframes: Sequence[Timeframe] | None = None,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
) -> dict[str, int]:
    """Build candles from stored ticks and persist them.

    Uses HistoricalReplay + MultiTimeframeCandleBuilder so results match
    the in-memory 1C path. Streams ticks from DuckDB; materialises only
    the candle result sets (far smaller than tick volume).

    Returns ``{timeframe_name: candles_written}``.
    """
    tfs = list(timeframes) if timeframes is not None else list(DEFAULT_TIMEFRAMES)
    tick_stream = tick_repo.as_tick_stream(
        instrument, start_epoch=start_epoch, end_epoch=end_epoch
    )
    replay = HistoricalReplay(tick_stream)
    mt = MultiTimeframeCandleBuilder(tfs)
    results = mt.process(replay)

    counts: dict[str, int] = {}
    for name, candles in results.items():
        n = candle_store.write_candles(instrument, candles, replace_range=True)
        counts[name] = n
    return counts


def _year_month(epoch: int) -> tuple[int, int]:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.year, dt.month


def _candles_to_table(instrument: str, candles: Sequence[Candle]) -> pa.Table:
    return pa.table(
        {
            "instrument": [instrument] * len(candles),
            "timeframe": [c.timeframe for c in candles],
            "start_epoch": [c.start_epoch for c in candles],
            "end_epoch": [c.end_epoch for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "tick_count": [c.tick_count for c in candles],
        },
        schema=_SCHEMA,
    )
