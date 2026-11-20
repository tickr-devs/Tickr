"""The live shadow scanner: one process (its own launchd job), one trading day after another.

For each trading day of the calendar it wakes `[scanner] start_lead_minutes` before day_start,
fixes the day's inputs once (`day-start`: eligible tickers and splits from Massive's reference
API, the previous closes), opens the `AM.*` stream and, `cut_delay_seconds` after each grid
minute ends, ranks that minute (`lists.DayLists.rank`) and records it. Every stream event is
recorded raw with its receipt time. After the last minute it writes the previous-close table
for the next day.

Restart: a new run re-reads the day's `day-start` (which holds the previous closes it uses, so a
day can be replayed without the table) and every earlier run's bars, so its lists
include them; the minutes whose cut passed while no run was up are not ranked (a gap the
`run-start` event shows). Stream loss: recorded, reconnected after `reconnect_seconds`; minutes
are still ranked with the bars received.

Observer only: no broker, no trading database, no journal, no alert. A failure here can never
change what the bot does. The previous closes are refused (change lists not built, the volume
list still is) when neither the table nor the record of the previous trading day has them;
`python -m tickr.scanner seed` fills the table from flat files.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from tickr.calendar import CalendarCause, CalendarRefused, SessionCalendar, ny_date_of
from tickr.config import Config, ScannerConfig
from tickr.scanner.lists import (
    DEFINITION,
    MS_PER_SECOND,
    Bar,
    Clock,
    DayLists,
    Split,
    apply_splits,
)
from tickr.scanner.massive import AGGREGATE_EVENT, EVENT_KEY, STATUS_EVENT, Feed, FeedError
from tickr.scanner.store import (
    BARS_PREFIX,
    DAY_END,
    DAY_START,
    GZ_LINES_SUFFIX,
    LISTS_PREFIX,
    PREV_FILE,
    RUN_START,
    GzLines,
    PrevTable,
    StoreError,
    append_event,
    lists_record,
    load_prev,
    read_events,
    read_gz_lines,
    run_files,
    run_name,
    save_prev,
    utc_text,
)

BYTES_PER_MB = 1 << 20  # unit factor
PREV_OK = "ok"
PREV_MISSING_TABLE = "prev-closes-missing"  # no table: run `python -m tickr.scanner seed`
PREV_MISSING_DAY = "prev-closes-missing-day"  # a trading day in between has no recorded bars
PREV_UNREADABLE = "prev-closes-unreadable"


def clock_of(cfg: ScannerConfig) -> Clock:
    def parse(key: str) -> time:
        return time.fromisoformat(getattr(cfg, key))

    return Clock(parse("day_start"), parse("regular_start"), parse("regular_end"),
                 parse("day_end"))


def day_folder(config: Config, day: date) -> Path:
    return (config.root / config.run.data_dir / config.market_data.capture_dir
            / config.environment_name / day.isoformat())


def prev_path(config: Config) -> Path:
    return config.root / config.run.data_dir / config.scanner.state_dir / PREV_FILE


def bar_of(event: dict[str, Any], receipt_ms: int | None) -> Bar | None:
    """A stream AM event (or a bars-file event) as a list bar; None when a field is unusable."""
    ticker, start, close, volume = (event.get(k) for k in ("sym", "s", "c", "v"))
    if (not isinstance(ticker, str) or isinstance(start, bool) or not isinstance(start, int)
            or not all(isinstance(x, int | float) and not isinstance(x, bool)
                       for x in (close, volume))):
        return None
    return Bar(ticker, start, float(close), float(volume), receipt_ms)


def day_start_record(folder: Path) -> dict[str, Any] | None:
    for record in read_events(folder):
        if record.get("event") == DAY_START:
            return record
    return None


def splits_of(record: dict[str, Any]) -> list[Split]:
    return [Split(t, float(f), float(to)) for t, f, to in record["splits"]]


def replay_bars(lists: DayLists, folder: Path, through: int | None = None) -> int:
    """Add the day's recorded bars (every run, in sequence order) to `lists`, up to sequence
    `through` when given; returns the last sequence number read (0 when none)."""
    last = 0
    for path in run_files(folder, BARS_PREFIX):
        for line in read_gz_lines(path):
            n = int(line["n"])
            if through is not None and n > through:
                return last
            last = n
            if line["e"].get(EVENT_KEY) == AGGREGATE_EVENT:
                bar = bar_of(line["e"], int(line["r"]))
                if bar is not None:
                    lists.add(bar)
    return last


def recorded_closes(clock: Clock, folder: Path, day: date) -> dict[str, float] | None:
    """The previous-close table after a recorded day: the closes the day started from (its
    `day-start`) updated with the closes of its recorded bars; None when the day has no record
    or started without previous closes."""
    start = day_start_record(folder)
    if start is None or start.get("prev_closes") is None:
        return None
    lists = DayLists(clock, day, frozenset(start["universe"]), None, 1)
    replay_bars(lists, folder)
    return {**start["prev_closes"], **lists.day_closes()}


def previous_trading_day(calendar: SessionCalendar, day: date) -> date:
    """The latest trading day before `day` (CalendarRefused when the calendar cannot say)."""
    past = day - timedelta(days=1)
    while True:
        try:
            calendar.day(past)
            return past
        except CalendarRefused as refused:
            if refused.cause not in (CalendarCause.WEEKEND, CalendarCause.HOLIDAY):
                raise
        past -= timedelta(days=1)


def previous_closes(config: Config, calendar: SessionCalendar, day: date,
                    splits: list[Split]) -> tuple[dict[str, float] | None, str, date | None]:
    """Today's previous closes with today's splits applied, the cause, and the trading day they
    run through. From the table when it runs through the previous trading day, else rebuilt from
    that day's record (a day whose end the scanner missed); refused otherwise."""
    try:
        table = load_prev(prev_path(config))
        last = previous_trading_day(calendar, day)
    except StoreError:
        return None, PREV_UNREADABLE, None
    except CalendarRefused:
        return None, PREV_MISSING_DAY, None
    if table is not None and table.through == last:
        return apply_splits(table.closes, splits), PREV_OK, last
    rebuilt = recorded_closes(clock_of(config.scanner), day_folder(config, last), last)
    if rebuilt is not None:
        return apply_splits(rebuilt, splits), PREV_OK, last
    if table is None:
        return None, PREV_MISSING_TABLE, None
    return None, PREV_MISSING_DAY, table.through


@dataclass
class Context:
    """What a run needs from outside, injected so tests drive it with a fake clock and feed."""

    config: Config
    calendar: SessionCalendar
    reference: Any  # massive.MassiveReference or a fake with eligible_tickers / splits
    feed: Callable[[], Feed]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    stop: Callable[[], bool]
    free_mb: Callable[[Path], float] = lambda p: shutil.disk_usage(p).free / BYTES_PER_MB


def _event(folder: Path, name: str, at: datetime, **fields: Any) -> None:
    append_event(folder, {"event": name, "at": utc_text(at), **fields})


def fix_day_start(ctx: Context, day: date, folder: Path) -> dict[str, Any] | None:
    """The day's inputs: read once from the record when a run already fixed them, else from
    Massive (retried every `reference_retry_seconds`, recorded as `reference-failed`); None when
    the day ended or the process is stopping first."""
    found = day_start_record(folder)
    if found is not None:
        return found
    cfg = ctx.config.scanner
    end = clock_of(cfg).start(day, clock_of(cfg).minutes)
    while not ctx.stop() and ctx.now() < end:
        try:
            types = ctx.reference.eligible_tickers(cfg.eligible_types)
            splits = ctx.reference.splits(day)
        except FeedError as exc:
            _event(folder, "reference-failed", ctx.now(), cause=str(exc))
            ctx.sleep(float(cfg.reference_retry_seconds))
            continue
        prev, cause, through = previous_closes(ctx.config, ctx.calendar, day, splits)
        record = {"event": DAY_START, "at": utc_text(ctx.now()), "day": day.isoformat(),
                  "definition": DEFINITION, "eligible_types": list(cfg.eligible_types),
                  "universe": sorted(types),
                  "types": {k: sorted(t for t, v in types.items() if v == k)
                            for k in cfg.eligible_types},
                  "splits": [[s.ticker, s.split_from, s.split_to] for s in splits],
                  "prev": cause, "prev_through": through and through.isoformat(),
                  "prev_closes": prev}
        append_event(folder, record)
        return record
    return None


class DayRun:
    """One run of one trading day (see the module docstring)."""

    def __init__(self, ctx: Context, day: date) -> None:
        self.ctx, self.day = ctx, day
        self.cfg = ctx.config.scanner
        self.clock = clock_of(self.cfg)
        self.folder = day_folder(ctx.config, day)
        self.feed: Feed | None = None
        self.retry_at: datetime | None = None
        self.recording = True

    def cut(self, minute: int) -> datetime:
        return self.clock.start(self.day, minute + 1) + timedelta(
            seconds=float(self.cfg.cut_delay_seconds))

    def run(self) -> bool:
        """True when the day ran to its last minute (the previous-close table is written)."""
        start = fix_day_start(self.ctx, self.day, self.folder)
        if start is None:
            return False
        prev, cause = start["prev_closes"], start["prev"]
        self.lists = DayLists(self.clock, self.day, frozenset(start["universe"]), prev,
                              self.cfg.list_depth)
        self.prev_cause = cause
        self.seq = replay_bars(self.lists, self.folder)
        started = self.ctx.now()
        self.run_id = run_name(started)
        self.next = 0
        while self.next < self.clock.minutes and self.cut(self.next) < started:
            self.next += 1
        _event(self.folder, RUN_START, started, run=self.run_id, through=self.seq,
               first_minute=self.next, prev=cause)
        self.bars = GzLines(self.folder / f"{BARS_PREFIX}{self.run_id}{GZ_LINES_SUFFIX}")
        self.records = GzLines(self.folder / f"{LISTS_PREFIX}{self.run_id}{GZ_LINES_SUFFIX}")
        try:
            self._loop()
        finally:
            if self.feed is not None:
                self.feed.close()
            self.bars.close()
            self.records.close()
        if self.next < self.clock.minutes:
            return False
        self._day_end(prev)
        return True

    def _loop(self) -> None:
        while self.next < self.clock.minutes and not self.ctx.stop():
            now = self.ctx.now()
            due = self.cut(self.next)
            if now >= due:
                self._rank(now)
                continue
            if self.feed is None:
                if self.retry_at is not None and now < self.retry_at:
                    self.ctx.sleep((min(self.retry_at, due) - now).total_seconds())
                    continue
                self._open(now)
                continue
            try:
                events = self.feed.receive((due - now).total_seconds())
            except FeedError as exc:
                self._down(str(exc))
                continue
            self._take(events)

    def _open(self, now: datetime) -> None:
        feed = self.ctx.feed()
        try:
            feed.open()
        except FeedError as exc:
            self._down(str(exc), refused=True)
            return
        self.feed = feed
        _event(self.folder, "connected", self.ctx.now(), run=self.run_id)

    def _down(self, cause: str, refused: bool = False) -> None:
        if self.feed is not None:
            self.feed.close()
        self.feed = None
        now = self.ctx.now()
        self.retry_at = now + timedelta(seconds=float(self.cfg.reconnect_seconds))
        _event(self.folder, "refused" if refused else "stream-down", now, run=self.run_id,
               cause=cause)

    def _take(self, events: list[dict[str, Any]]) -> None:
        receipt = int(self.ctx.now().timestamp() * MS_PER_SECOND)
        for event in events:
            self.seq += 1
            if self.recording:
                self.bars.write({"n": self.seq, "r": receipt, "e": event})
            kind = event.get(EVENT_KEY)
            if kind == AGGREGATE_EVENT:
                bar = bar_of(event, receipt)
                if bar is not None:
                    self.lists.add(bar)
            elif kind == STATUS_EVENT:
                _event(self.folder, "status", self.ctx.now(), run=self.run_id,
                       status=str(event.get("status")))

    def _rank(self, now: datetime) -> None:
        ranking = self.lists.rank(self.next)
        self.records.write(lists_record(
            ranking, day=self.day, start=self.clock.start(self.day, self.next), run=self.run_id,
            through=self.seq, computed_at=now, outside=self.lists.outside,
            duplicates=self.lists.duplicates, prev=self.prev_cause))
        self.records.sync()
        if self.recording:
            self.bars.sync()
            if self.ctx.free_mb(self.folder) < self.cfg.min_free_disk_mb:
                self.recording = False
                _event(self.folder, "disk-low", now, run=self.run_id, through=self.seq)
        self.next += 1

    def _day_end(self, prev: dict[str, float] | None) -> None:
        closes = self.lists.day_closes()
        written = prev is not None
        if prev is not None:
            save_prev(prev_path(self.ctx.config), PrevTable(self.day, {**prev, **closes}))
        _event(self.folder, DAY_END, self.ctx.now(), run=self.run_id, prev_written=written,
               closes=len(closes), bars=self.seq, recording=self.recording)


def next_day(calendar: SessionCalendar, clock: Clock, now: datetime) -> date:
    """The trading day whose grid has not ended at `now` (today or a later one)."""
    day = ny_date_of(now)
    while True:
        try:
            calendar.day(day)
            if now < clock.start(day, clock.minutes):
                return day
        except CalendarRefused as refused:
            if refused.cause not in (CalendarCause.WEEKEND, CalendarCause.HOLIDAY):
                raise
        day += timedelta(days=1)


def serve(ctx: Context) -> None:
    """Run day after day until `ctx.stop()`. A calendar that cannot answer is waited out
    (`idle_check_seconds`)."""
    cfg = ctx.config.scanner
    clock = clock_of(cfg)
    lead = timedelta(minutes=cfg.start_lead_minutes)
    idle = float(cfg.idle_check_seconds)
    while not ctx.stop():
        now = ctx.now()
        try:
            day = next_day(ctx.calendar, clock, now)
        except CalendarRefused:
            ctx.sleep(idle)
            continue
        wake = clock.start(day, 0) - lead
        if now < wake:
            ctx.sleep(min(idle, (wake - now).total_seconds()))
            continue
        DayRun(ctx, day).run()
