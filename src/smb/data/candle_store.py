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

Only **complete** timeframe buckets are written. Build ranges are aligned
to timeframe boundaries; partial edge candles are not persisted.
Rebuilds clear the entire affected ``[range_start, range_end)`` so stale
candles cannot survive when the underlying tick coverage shrinks.
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


def align_build_range(
    timeframe: Timeframe,
    start_epoch: int | None,
    end_epoch: int | None,
) -> tuple[int | None, int | None]:
    """Align a build window to complete timeframe buckets.

    * ``start_epoch`` is rounded **up** to the next bucket start (ceil).
    * ``end_epoch`` is rounded **down** to a bucket start (floor), which is
      also the exclusive end of the previous complete bucket.

    Raises ``ValueError`` if the aligned window is empty (no complete
    bucket fits between the bounds).
    """
    n = timeframe.seconds
    aligned_start = start_epoch
    aligned_end = end_epoch

    if start_epoch is not None:
        aligned_start = ((start_epoch + n - 1) // n) * n

    if end_epoch is not None:
        aligned_end = (end_epoch // n) * n

    if (
        aligned_start is not None
        and aligned_end is not None
        and aligned_start >= aligned_end
    ):
        raise ValueError(
            f"No complete {timeframe.name} bucket in range "
            f"[{start_epoch}, {end_epoch}); "
            f"aligned [{aligned_start}, {aligned_end})"
        )
    return aligned_start, aligned_end


def is_complete_candle(candle: Candle, timeframe: Timeframe) -> bool:
    """Return True if the candle spans a full timeframe bucket.

    Uses the candle's own start/end epochs (must equal one bucket) and
    requires ``tick_count == timeframe.seconds`` for these 1-tick/s
    synthetic indices so edge partials are not treated as canonical.
    """
    if candle.end_epoch - candle.start_epoch != timeframe.seconds:
        return False
    if candle.start_epoch % timeframe.seconds != 0:
        return False
    return candle.tick_count == timeframe.seconds


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
        clear_start: int | None = None,
        clear_end: int | None = None,
    ) -> int:
        """Persist candles for one instrument.

        When ``clear_start`` / ``clear_end`` are provided, **all** existing
        candles for the same instrument+timeframe with
        ``clear_start <= start_epoch < clear_end`` are removed before
        writing. This replaces the entire affected range so vanished
        candles cannot remain as stale rows.

        If clear bounds are omitted and ``candles`` is non-empty, the clear
        range defaults to ``[min(start_epoch), max(end_epoch))`` of the
        incoming set (full span of new candles).
        """
        if not candles and clear_start is None and clear_end is None:
            return 0

        by_tf: dict[str, list[Candle]] = {}
        for c in candles:
            by_tf.setdefault(c.timeframe, []).append(c)

        if not by_tf and (clear_start is not None or clear_end is not None):
            for tf_name in self.list_timeframes(instrument):
                by_tf.setdefault(tf_name, [])

        written = 0
        for timeframe, group in by_tf.items():
            cs = clear_start
            ce = clear_end
            if cs is None and ce is None and group:
                cs = min(c.start_epoch for c in group)
                ce = max(c.end_epoch for c in group)
            written += self._write_timeframe(
                instrument,
                timeframe,
                group,
                clear_start=cs,
                clear_end=ce,
            )
        return written

    def _write_timeframe(
        self,
        instrument: str,
        timeframe: str,
        candles: Sequence[Candle],
        *,
        clear_start: int | None,
        clear_end: int | None,
    ) -> int:
        paths = self._partition_paths(instrument, timeframe)
        affected_years_months: set[tuple[int, int]] = set()

        if clear_start is not None or clear_end is not None:
            for path in paths:
                affected_years_months.add(_path_year_month(path))

        for c in candles:
            affected_years_months.add(_year_month(c.start_epoch))

        new_by_ym: dict[tuple[int, int], list[Candle]] = {}
        for c in candles:
            ym = _year_month(c.start_epoch)
            new_by_ym.setdefault(ym, []).append(c)

        total_new = 0
        all_ym = set(new_by_ym) | affected_years_months

        for year, month in sorted(all_ym):
            path = self._partition_path(instrument, timeframe, year, month)
            existing: list[Candle] = []
            if path.exists():
                existing = list(_read_partition_candles(path))

            if clear_start is not None or clear_end is not None:
                existing = [
                    c
                    for c in existing
                    if not _in_clear_range(c.start_epoch, clear_start, clear_end)
                ]

            incoming = new_by_ym.get((year, month), [])
            if incoming:
                starts = {c.start_epoch for c in incoming}
                existing = [c for c in existing if c.start_epoch not in starts]

            merged = existing + list(incoming)
            if not merged:
                if path.exists():
                    path.unlink()
                continue

            path.parent.mkdir(parents=True, exist_ok=True)
            table = _candles_to_table(instrument, merged)
            table = table.sort_by("start_epoch")
            pq.write_table(table, path, compression="zstd")
            total_new += len(incoming)
            logger.debug(
                "Wrote partition %s %s %04d-%02d (%s new, %s kept)",
                instrument,
                timeframe,
                year,
                month,
                len(incoming),
                len(existing),
            )
        return total_new

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

    def _partition_paths(self, instrument: str, timeframe: str) -> list[Path]:
        tf_dir = (
            self.candles_dir
            / f"instrument={instrument}"
            / f"timeframe={timeframe}"
        )
        if not tf_dir.exists():
            return []
        return sorted(tf_dir.rglob("*.parquet"))


def build_candles_from_ticks(
    tick_repo: TickRepository,
    candle_store: ParquetCandleStore,
    *,
    instrument: str,
    timeframes: Sequence[Timeframe] | None = None,
    start_epoch: int | None = None,
    end_epoch: int | None = None,
) -> dict[str, int]:
    """Build complete candles from stored ticks and persist them.

    Uses HistoricalReplay + MultiTimeframeCandleBuilder so OHLC matches
    the in-memory 1C path. Only **complete** timeframe buckets are
    written; build bounds are aligned per timeframe. The entire aligned
    range is cleared before write so rebuilds cannot leave stale candles.
    """
    tfs = list(timeframes) if timeframes is not None else list(DEFAULT_TIMEFRAMES)

    fetch_start = start_epoch
    fetch_end = end_epoch
    aligned_per_tf: dict[str, tuple[int | None, int | None]] = {}

    for tf in tfs:
        try:
            a_start, a_end = align_build_range(tf, start_epoch, end_epoch)
        except ValueError:
            aligned_per_tf[tf.name] = (None, None)
            continue
        aligned_per_tf[tf.name] = (a_start, a_end)
        if a_start is not None:
            fetch_start = (
                a_start if fetch_start is None else min(fetch_start, a_start)
            )
        if a_end is not None:
            fetch_end = a_end if fetch_end is None else max(fetch_end, a_end)

    tick_stream = tick_repo.as_tick_stream(
        instrument, start_epoch=fetch_start, end_epoch=fetch_end
    )
    replay = HistoricalReplay(tick_stream)
    mt = MultiTimeframeCandleBuilder(tfs)
    results = mt.process(replay)

    counts: dict[str, int] = {}
    for tf in tfs:
        a_start, a_end = aligned_per_tf.get(tf.name, (start_epoch, end_epoch))
        if a_start is None and a_end is None and start_epoch is not None:
            counts[tf.name] = 0
            continue

        raw = results.get(tf.name, [])
        complete = [
            c
            for c in raw
            if is_complete_candle(c, tf)
            and (a_start is None or c.start_epoch >= a_start)
            and (a_end is None or c.end_epoch <= a_end)
        ]

        clear_start = a_start
        clear_end = a_end
        if clear_start is None and complete:
            clear_start = min(c.start_epoch for c in complete)
        if clear_end is None and complete:
            clear_end = max(c.end_epoch for c in complete)

        n = candle_store.write_candles(
            instrument,
            complete,
            clear_start=clear_start,
            clear_end=clear_end,
        )
        counts[tf.name] = n
    return counts


def _year_month(epoch: int) -> tuple[int, int]:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.year, dt.month


def _path_year_month(path: Path) -> tuple[int, int]:
    month = int(path.parent.name.split("=", 1)[1])
    year = int(path.parent.parent.name.split("=", 1)[1])
    return year, month


def _in_clear_range(
    start_epoch: int, clear_start: int | None, clear_end: int | None
) -> bool:
    if clear_start is not None and start_epoch < clear_start:
        return False
    if clear_end is not None and start_epoch >= clear_end:
        return False
    return clear_start is not None or clear_end is not None


def _read_partition_candles(path: Path) -> Iterator[Candle]:
    table = pq.read_table(path, schema=_SCHEMA)
    cols = {name: table.column(name).to_pylist() for name in table.column_names}
    for i in range(table.num_rows):
        yield Candle(
            timeframe=str(cols["timeframe"][i]),
            start_epoch=int(cols["start_epoch"][i]),
            end_epoch=int(cols["end_epoch"][i]),
            open=float(cols["open"][i]),
            high=float(cols["high"][i]),
            low=float(cols["low"][i]),
            close=float(cols["close"][i]),
            tick_count=int(cols["tick_count"][i]),
            finalized=True,
        )


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
