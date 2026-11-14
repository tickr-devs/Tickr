"""Daily pool of past gainers, chosen from daily bars only (no minute data, no look-ahead into
the run: the run decides per minute when a pool symbol became a gainer, see gate.py).

A (symbol, session) enters the pool when, against the previous session's close,
- the day's HIGH reached `gainer_change_percent` (a symbol on the gainers list at any minute
  had a price at least that far up, so this is a superset of the minute-level rule), and
- the day's dollar volume, typical price x volume, reached bot.toml
  `min_session_dollar_volume_usd` (discovery's floor applies to the session so far, which can
  never exceed the whole day).
Per session at most `max_pool_per_day` symbols, best daily-high change first.

Daily bars are forward-adjusted; a ratio of two adjacent bars of the same series is unaffected.
A symbol without a bar on the previous session is skipped for that day (no previous close).
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from aperture.backtest.errors import BacktestError

POOL_FILE = "pool.csv"
POOL_COLUMNS = ("session_date", "rank", "symbol", "previous_close", "day_high_change_percent",
                "day_dollar_volume_usd")
PERCENT = Decimal(100)
THREE = Decimal(3)


@dataclass(frozen=True)
class PoolEntry:
    session_date: date
    rank: int
    symbol: str
    previous_close: Decimal
    day_high_change_percent: Decimal
    day_dollar_volume_usd: Decimal


def select_pool(rows: Iterable[dict[str, Any]], sessions: Sequence[date],
                change_percent: Decimal, dollar_volume_floor: Decimal,
                max_per_day: int) -> list[PoolEntry]:
    """`sessions` are the window's session dates oldest first; the first one only supplies the
    previous close of the second."""
    by_symbol: dict[str, dict[date, dict[str, Any]]] = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        own = by_symbol.setdefault(row["symbol"], {})
        if day in own:
            raise BacktestError(f"two daily rows for {row['symbol']} on {day}")
        own[day] = row
    pool: list[PoolEntry] = []
    for previous, day in zip(sessions, sessions[1:], strict=False):
        candidates: list[tuple[Decimal, str, Decimal, Decimal]] = []
        for symbol, days in by_symbol.items():
            today, before = days.get(day), days.get(previous)
            if today is None or before is None:
                continue
            previous_close = Decimal(before["close"])
            if previous_close <= 0:
                raise BacktestError(f"{symbol} {previous}: close {previous_close} not positive")
            high, low, close = (Decimal(today[k]) for k in ("high", "low", "close"))
            change = (high / previous_close - 1) * PERCENT
            dollar_volume = (high + low + close) / THREE * Decimal(today["volume"])
            if change >= change_percent and dollar_volume >= dollar_volume_floor:
                candidates.append((change, symbol, previous_close, dollar_volume))
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for rank, (change, symbol, previous_close, dollar_volume) in enumerate(
                candidates[:max_per_day], start=1):
            pool.append(PoolEntry(day, rank, symbol, previous_close, change, dollar_volume))
    return pool


def write_pool(pool: Sequence[PoolEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(POOL_COLUMNS)
        for e in pool:
            writer.writerow([e.session_date.isoformat(), e.rank, e.symbol, e.previous_close,
                             e.day_high_change_percent, e.day_dollar_volume_usd])


def read_pool(path: Path) -> list[PoolEntry]:
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != POOL_COLUMNS:
                raise BacktestError(f"{path}: columns {reader.fieldnames} are not {POOL_COLUMNS}")
            return [PoolEntry(date.fromisoformat(r["session_date"]), int(r["rank"]), r["symbol"],
                              Decimal(r["previous_close"]), Decimal(r["day_high_change_percent"]),
                              Decimal(r["day_dollar_volume_usd"])) for r in reader]
    except OSError as exc:
        raise BacktestError(f"cannot read {path}: {exc}") from exc
