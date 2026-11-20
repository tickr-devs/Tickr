"""Scanner files: what the shadow scanner records and reads back.

In the capture day folder `<data_dir>/<[market_data] capture_dir>/<environment>/<NY date>/`:

- `scanner-events.jsonl` (plain, small): `day-start` (once per day: definition, eligible types,
  universe, splits, previous-close source), `run-start`, stream `connected` / `stream-down` /
  `refused`, `disk-low`, `day-end`. Times are ISO-8601 UTC.
- `scanner-bars-<run>.jsonl.gz`: EVERY event the stream delivered (all tickers, as received),
  one line `{"n": sequence, "r": receipt epoch ms, "e": the event}`; `n` counts on across runs.
- `scanner-lists-<run>.jsonl.gz`: one line per ranked minute (`lists_record`).

`<run>` is the UTC time the process started the day (HHMMSS), so a restart opens new files and
never appends to a gzip member a crash may have cut. The gzip files are flushed with a sync point
after every minute, so a cut file reads back to its last minute (`read_gz_lines`).

The previous-close table (`<data_dir>/<[scanner] state_dir>/prev-closes.json`) carries the
closes through its `through` day; written atomically at each day's end.
"""

from __future__ import annotations

import gzip
import json
import os
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from tickr.scanner.lists import DEFINITION, ListName, Ranking

EVENTS_FILE = "scanner-events.jsonl"
BARS_PREFIX = "scanner-bars-"
LISTS_PREFIX = "scanner-lists-"
GZ_LINES_SUFFIX = ".jsonl.gz"
PREV_FILE = "prev-closes.json"
RUN_FORMAT = "%H%M%S"
DAY_START = "day-start"
RUN_START = "run-start"
DAY_END = "day-end"


class StoreError(Exception):
    """A scanner file could not be written or read."""


def utc_text(at: datetime) -> str:
    if at.tzinfo is None:
        raise StoreError(f"naive datetime {at!r}")
    return at.astimezone(UTC).isoformat()


class GzLines:
    """Append-only gzip JSONL writer; `sync()` makes everything written so far readable even if
    the process dies afterwards."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise StoreError(f"{path.name} exists; a run never appends to an earlier run's file")
        self._file = gzip.open(path, "wb")

    def write(self, record: dict[str, Any]) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")).encode() + b"\n")

    def sync(self) -> None:
        self._file.flush(zlib_mode=zlib.Z_SYNC_FLUSH)

    def close(self) -> None:
        self._file.close()


def append_event(folder: Path, record: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / EVENTS_FILE, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def read_events(folder: Path) -> list[dict[str, Any]]:
    path = folder / EVENTS_FILE
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_gz_lines(path: Path) -> Iterator[dict[str, Any]]:
    """The complete lines of a gzip JSONL file; a file cut by a crash ends at its last complete
    line (a partial last line is dropped)."""
    with gzip.open(path, "rb") as handle:
        pending = b""
        while True:
            try:
                chunk = handle.read(1 << 20)
            except (EOFError, zlib.error, gzip.BadGzipFile):
                return
            if not chunk:
                return
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for line in lines:
                if line.strip():
                    yield json.loads(line)


def run_files(folder: Path, prefix: str) -> list[Path]:
    """A day's files of one kind, oldest run first."""
    return sorted(folder.glob(f"{prefix}*{GZ_LINES_SUFFIX}"))


def run_name(started: datetime) -> str:
    return started.astimezone(UTC).strftime(RUN_FORMAT)


def lists_record(ranking: Ranking, *, day: date, start: datetime, run: str, through: int,
                 computed_at: datetime, outside: int, duplicates: int,
                 prev: str) -> dict[str, Any]:
    """One ranked minute: every list's rows as [ticker, value, bar minute, lag ms]."""
    return {
        "definition": DEFINITION,
        "day": day.isoformat(),
        "minute": ranking.minute,
        "start": utc_text(start),
        "run": run,
        "through": through,
        "computed_at": utc_text(computed_at),
        "applied": ranking.applied,
        "late": ranking.late,
        "outside": outside,
        "duplicates": duplicates,
        "prev": prev,
        "eligible": {name.value: n for name, n in ranking.eligible.items()},
        "lists": {name.value: [[r.ticker, r.value, r.bar_minute, r.lag_ms] for r in rows]
                  for name, rows in ranking.lists.items()},
    }


def list_names(record: dict[str, Any]) -> list[ListName]:
    return [ListName(name) for name in record["lists"]]


@dataclass(frozen=True)
class PrevTable:
    through: date
    closes: dict[str, float]


def load_prev(path: Path) -> PrevTable | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text("utf-8"))
        return PrevTable(date.fromisoformat(raw["through"]),
                         {str(t): float(c) for t, c in raw["closes"].items()})
    except (ValueError, KeyError, TypeError) as exc:
        raise StoreError(f"{path.name} unreadable: {type(exc).__name__}") from exc


def save_prev(path: Path, table: PrevTable) -> None:
    """Write the table to a temporary file, fsync, then rename into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    body = {"definition": DEFINITION, "through": table.through.isoformat(),
            "closes": dict(sorted(table.closes.items()))}
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(body, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
