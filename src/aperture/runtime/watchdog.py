"""Watchdog (spec 11): a separate launchd job that outlives a crashed bot.

The protective stop is a DAY order. If the bot dies and is (deliberately) not restarted, nothing
flattens at the flatten time and the position goes overnight without a broker-side stop; the
bot's own day-end check dies with the bot. The watchdog is its own process: it never takes the
bot's single-instance lock, opens the bot's database read-only (SQLite `mode=ro`), journals to
its own file, and sees the broker only through `ReadOnlyBroker`, which has the two reads and
nothing else, so it cannot send a trading request.

On an open market day (half days too: a position after the flatten time is wrong on any day):
- `[runtime] watchdog_after_flatten_seconds` after the flatten time and again
  `watchdog_before_close_seconds` before the regular close, it reads positions and open orders.
  Any position or any open order (ours or not) alerts `watchdog-not-flat`; a failed read alerts
  `watchdog-cannot-check`. Checks whose time passed before the job started collapse into one
  check at once.
- every `watchdog_bot_check_seconds` in the regular session until its last check: if the bot's
  lock file records no live process while the database shows an open trade, it alerts
  `bot-down-with-open-trade`.
Every broker check, every "bot down" finding and every alert is one JSON line on stdout (the
job log) and in `<data_dir>/<watchdog_journal_file>`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from aperture.broker.base import Broker, BrokerError
from aperture.calendar import NEW_YORK, CalendarCause, CalendarRefused, MarketDay, SessionCalendar
from aperture.config import Config
from aperture.engine.core import TradeState
from aperture.lock import recorded_holder
from aperture.runtime.alerts import Alerter, AlertKind
from aperture.store import Store
from aperture.types import OrderDetail, Position

START = "watchdog-start"
CHECK = "watchdog-check"
BOT_DOWN = "watchdog-bot-down"
ALERT = "alert"
JOURNAL_FAILED = "watchdog-journal-failed"
CHANNEL_REFUSED = "watchdog-alert-channel-refused"
# Calendar answers that mean "no session today" (exit without an alert, like preflight).
CLOSED_CAUSES = frozenset({CalendarCause.WEEKEND, CalendarCause.HOLIDAY})


class Reads(Protocol):
    def positions(self) -> list[Position]: ...

    def open_orders(self) -> list[OrderDetail]: ...


class ReadOnlyBroker:
    """The watchdog's only view of the broker: positions and open orders, nothing that sends."""

    __slots__ = ("_broker",)

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def positions(self) -> list[Position]:
        return self._broker.positions()

    def open_orders(self) -> list[OrderDetail]:
        return self._broker.open_orders()


def market_day(config: Config, now: datetime) -> MarketDay:
    """Today's sessions, or CalendarRefused (WEEKEND/HOLIDAY mean no session)."""
    return SessionCalendar.load((config.root / config.session.calendar_file).parent).day_at(now)


def check_times(config: Config, day: MarketDay) -> tuple[datetime, datetime]:
    """The two broker checks of `day` (UTC): after the flatten time, and before the close."""
    flatten = day.before_close(config.session.flatten_minutes_before_close).astimezone(UTC)
    close = day.regular_close.astimezone(UTC)
    first = flatten + timedelta(seconds=config.runtime.watchdog_after_flatten_seconds)
    last = close - timedelta(seconds=config.runtime.watchdog_before_close_seconds)
    if not flatten < first < last < close:
        raise CalendarRefused(CalendarCause.INVALID_OFFSET,
                              f"watchdog checks {first.isoformat()} and {last.isoformat()} are "
                              f"not between flatten {flatten.isoformat()} and the close")
    return first, last


def open_trades(database: Path) -> int:
    """Open trades in the bot's database, read-only; 0 when it was never created."""
    if not database.exists():
        return 0
    store = Store.open_readonly(database)
    try:
        rows = store.query("SELECT COUNT(*) AS n FROM trades WHERE state != ?",
                           (TradeState.CLOSED.value,))
    finally:
        store.close()
    return int(rows[0]["n"])


class Journal:
    """JSON lines to stdout and to the watchdog's own file (never the bot's database)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, record: str, body: dict[str, Any]) -> None:
        line = json.dumps({"record": record, **body}, sort_keys=True, default=str)
        print(line, flush=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            print(json.dumps({"record": JOURNAL_FAILED, "error": type(exc).__name__}),
                  flush=True)


def _ny(instant: datetime) -> str:
    return instant.astimezone(NEW_YORK).strftime("%H:%M:%S ET")


def _cause(exc: Exception) -> str:
    # Adapter errors carry names and causes only; anything else is reported by type alone.
    return f"{type(exc).__name__}: {exc}" if isinstance(exc, BrokerError) else type(exc).__name__


class Watchdog:
    def __init__(self, config: Config, day: MarketDay, *, connect: Callable[[], Reads],
                 alerter: Alerter, journal: Journal, clock: Callable[[], datetime],
                 sleep: Callable[[float], None], lock_path: Path, database: Path) -> None:
        self.day = day
        self.times = check_times(config, day)
        self.interval = float(config.runtime.watchdog_bot_check_seconds)
        self._connect = connect
        self._reads: Reads | None = None
        self._alerter = alerter
        self.journal = journal
        self._clock = clock
        self._sleep = sleep
        self._lock_path = lock_path
        self._database = database
        self.alerted = False

    def alert(self, kind: AlertKind, text: str) -> None:
        self.alerted = True
        self.journal.write(ALERT, self._alerter.alert(kind, text).journal_body())

    def run(self) -> int:
        """Until the last broker check; 1 if anything alerted, else 0."""
        self.journal.write(START, {"day": self.day.ny_date.isoformat(),
                                   "checks": [t.isoformat() for t in self.times]})
        pending = list(self.times)
        while pending:
            now = self._clock()
            if now >= pending[0]:
                pending = [t for t in pending if t > now]
                self.broker_check(now)
                continue
            if self.day.in_regular_session(now):
                self.bot_check(now)
            self._sleep(min(self.interval, (pending[0] - now).total_seconds()))
        return 1 if self.alerted else 0

    def broker_check(self, now: datetime) -> None:
        try:
            if self._reads is None:
                self._reads = self._connect()
            positions = [p for p in self._reads.positions() if p.quantity != 0]
            orders = self._reads.open_orders()
        except Exception as exc:  # noqa: BLE001 - any failure means "unknown", never "flat"
            cause = _cause(exc)
            self.journal.write(CHECK, {"at": now.isoformat(), "broker_read": False,
                                       "cause": cause})
            self.alert(AlertKind.WATCHDOG_CANNOT_CHECK,
                       f"{_ny(now)}: broker read failed ({cause}). Open the Webull app and "
                       "check positions and orders yourself: the stop is DAY")
            return
        left = ([f"position {p.symbol} {p.quantity}" for p in positions]
                + [f"order {o.client_order_id} {o.symbol} {o.side} {o.order_type}"
                   for o in orders])
        self.journal.write(CHECK, {"at": now.isoformat(), "broker_read": True,
                                   "positions": len(positions), "open_orders": len(orders)})
        if left:
            self.alert(AlertKind.WATCHDOG_NOT_FLAT,
                       f"{_ny(now)}: not flat after the flatten time: " + "; ".join(left)
                       + ". Open the Webull app: close it, or place a stop by hand; the stop "
                       f"is DAY and expires at {_ny(self.day.regular_close)}")

    def bot_check(self, now: datetime) -> None:
        if recorded_holder(self._lock_path) is not None:
            return
        try:
            count = open_trades(self._database)
        except sqlite3.Error as exc:
            self.alert(AlertKind.WATCHDOG_CANNOT_CHECK,
                       f"bot not running and its database is unreadable ({type(exc).__name__})")
            return
        if count == 0:
            return
        self.journal.write(BOT_DOWN, {"at": now.isoformat(), "open_trades": count})
        self.alert(AlertKind.BOT_DOWN_WITH_OPEN_TRADE,
                   f"the bot is not running and the database shows {count} open trade(s): "
                   "nothing will flatten it. Open the Webull app: check the stop, close by hand "
                   f"before {_ny(self.day.regular_close)}")
