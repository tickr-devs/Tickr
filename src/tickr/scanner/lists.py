"""The scanner lists: ONE definition, shared by the live shadow scanner and research.

Definition `massive-lists-v1` (from research's wm.py, 2026-11-20; research's versioned builder
adopts the explicit tie-break):

- Minute grid: minute m counts from `[scanner] day_start` (04:00 New York) on a FIXED clock;
  the regular part is [regular_start, regular_end) (09:30-16:00), the post part from
  regular_end to day_end (20:00). Half days use the same clock (a known quirk kept so live and
  research agree; on a half day "regular" includes 13:00-16:00).
- Universe: the day's eligible tickers (`[scanner] eligible_types`, Massive reference types CS and
  ADRC). Bars of other tickers never enter a list.
- A ticker is visible from its first bar of the day. Its close is carried forward from its
  latest bar (by bar minute, not arrival).
- gainers / losers: the `depth` highest / lowest by carried close / previous close - 1, among
  tickers with a bar today and a previous close above 0.
- volume: the `depth` largest by cumulative volume since day_start (sum of the bars' volumes),
  above 0; no previous close needed.
- post-gainers / post-losers (minutes from regular_end on): the same by carried close / today's
  last close in the regular part - 1, among tickers with a bar in the post part so far and a
  regular close above 0.
- Ties: the ticker ascending as second key (the only identity in both the stream and the flat
  files; stable within a day).
- Previous close: the ticker's last close in the regular part of the latest day it traded, else
  its last close that day (`day_closes`); on a split's execution day multiplied by
  split_from / split_to before the day starts (`apply_splits`).

The list for minute m uses only bars whose minute is at or before m: a bar is applied at the
first ranking of a minute at or after its own (`DayLists.rank`), so a bar that arrives after its
minute was ranked is counted `late` and used from the next ranking on. Values are floats (as in
research, so ties and ranks are the same numbers); nothing here reads a clock or a file.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from tickr.calendar import NEW_YORK

DEFINITION = "massive-lists-v1"
MS_PER_SECOND = 1000  # unit factor
SECONDS_PER_MINUTE = 60  # unit factor
MS_PER_MINUTE = MS_PER_SECOND * SECONDS_PER_MINUTE


class ListName(StrEnum):
    GAINERS = "gainers"
    LOSERS = "losers"
    VOLUME = "volume"
    POST_GAINERS = "post-gainers"
    POST_LOSERS = "post-losers"


class ListsError(ValueError):
    """A clock or bar the definition cannot use."""


@dataclass(frozen=True)
class Clock:
    """The fixed minute grid of a day (New York wall clock)."""

    day_start: time
    regular_start: time
    regular_end: time
    day_end: time

    def __post_init__(self) -> None:
        if not self.day_start < self.regular_start < self.regular_end < self.day_end:
            raise ListsError("scanner clock must be day_start < regular_start < regular_end "
                             "< day_end")

    @staticmethod
    def _minutes(at: time) -> int:
        return at.hour * SECONDS_PER_MINUTE + at.minute

    def _offset(self, at: time) -> int:
        return self._minutes(at) - self._minutes(self.day_start)

    @property
    def minutes(self) -> int:
        """Minutes in the grid (960 for 04:00-20:00)."""
        return self._offset(self.day_end)

    @property
    def regular_first(self) -> int:
        return self._offset(self.regular_start)

    @property
    def post_first(self) -> int:
        return self._offset(self.regular_end)

    def start(self, day: date, minute: int) -> datetime:
        """Aware start of grid minute `minute` of `day`."""
        first = datetime.combine(day, self.day_start, tzinfo=NEW_YORK)
        return first + timedelta(minutes=minute)

    def minute_of(self, day: date, start_ms: int) -> int | None:
        """Grid minute of a bar starting at `start_ms` (epoch ms), or None when the bar is not
        on `day`'s grid (another date, before day_start, from day_end on)."""
        first_ms = int(self.start(day, 0).timestamp() * MS_PER_SECOND)
        offset = start_ms - first_ms
        if offset < 0 or offset % MS_PER_MINUTE:
            return None
        minute = offset // MS_PER_MINUTE
        return minute if minute < self.minutes else None


@dataclass(frozen=True)
class Bar:
    """One minute bar as used by the lists (the stream's AM event or a flat-file row)."""

    ticker: str
    start_ms: int  # epoch ms of the bar's start
    close: float
    volume: float
    receipt_ms: int | None = None  # epoch ms the scanner received it (None: from an archive)


@dataclass(frozen=True)
class Row:
    """One ranked ticker: the value it ranks by, the minute of the latest bar the value used and
    how late that bar arrived after its end (ms; None from an archive)."""

    ticker: str
    value: float
    bar_minute: int
    lag_ms: int | None


@dataclass
class _Ticker:
    close: float = 0.0
    minute: int = -1  # minute of the bar `close` came from; -1 before the first bar
    lag_ms: int | None = None
    volume: float = 0.0
    regular_close: float = 0.0
    regular_minute: int = -1
    post: bool = False
    seen: bytearray = field(default_factory=bytearray)  # one byte per grid minute: 1 = applied


@dataclass(frozen=True)
class Ranking:
    """The lists of one minute and what went into them."""

    minute: int
    lists: dict[ListName, tuple[Row, ...]]
    applied: int  # bars applied at this ranking
    late: int  # of those, bars of an earlier, already ranked minute
    eligible: dict[ListName, int]  # tickers that had a value for each list


@dataclass
class DayLists:
    """The day's state for the lists. `add` takes every received bar; `rank(m)` applies the
    waiting bars of minutes up to m and returns the lists of minute m."""

    clock: Clock
    day: date
    universe: frozenset[str]
    prev_closes: Mapping[str, float] | None  # None: no previous closes (change lists refused)
    depth: int
    _tickers: dict[str, _Ticker] = field(default_factory=dict)
    _waiting: dict[int, list[Bar]] = field(default_factory=dict)
    ranked: int = -1  # the latest minute ranked
    outside: int = 0  # bars not on the day's grid or not in the universe (never in a list)
    duplicates: int = 0  # bars for a ticker and minute already applied

    def add(self, bar: Bar) -> bool:
        """Queue a bar; False when it can never enter a list (not in the universe or not on
        the grid)."""
        minute = self.clock.minute_of(self.day, bar.start_ms)
        if minute is None or bar.ticker not in self.universe:
            self.outside += 1
            return False
        self._waiting.setdefault(minute, []).append(bar)
        return True

    def _apply(self, minute: int, bar: Bar) -> bool:
        state = self._tickers.get(bar.ticker)
        if state is None:
            state = self._tickers[bar.ticker] = _Ticker(seen=bytearray(self.clock.minutes))
        if state.seen[minute]:
            self.duplicates += 1
            return False
        state.seen[minute] = 1
        state.volume += bar.volume
        if minute > state.minute:
            state.close = bar.close
            state.minute = minute
            state.lag_ms = (None if bar.receipt_ms is None
                            else bar.receipt_ms - bar.start_ms - MS_PER_MINUTE)
        if self.clock.regular_first <= minute < self.clock.post_first:
            if minute > state.regular_minute:
                state.regular_close = bar.close
                state.regular_minute = minute
        elif minute >= self.clock.post_first:
            state.post = True
        return True

    def rank(self, minute: int) -> Ranking:
        if not self.ranked < minute < self.clock.minutes:
            raise ListsError(f"minute {minute} after {self.ranked} is not the next on the grid")
        applied = late = 0
        for waiting in sorted(m for m in self._waiting if m <= minute):
            for bar in self._waiting.pop(waiting):
                if self._apply(waiting, bar):
                    applied += 1
                    late += waiting <= self.ranked
        self.ranked = minute
        lists, eligible = self._lists(minute)
        return Ranking(minute, lists, applied, late, eligible)

    def _row(self, ticker: str, value: float) -> Row:
        state = self._tickers[ticker]
        return Row(ticker, value, state.minute, state.lag_ms)

    def _top(self, values: dict[str, float], highest: bool) -> tuple[Row, ...]:
        sign = -1.0 if highest else 1.0
        order = sorted(values, key=lambda t: (sign * values[t], t))[:self.depth]
        return tuple(self._row(t, values[t]) for t in order)

    def _lists(self, minute: int) -> tuple[dict[ListName, tuple[Row, ...]], dict[ListName, int]]:
        change: dict[str, float] = {}
        if self.prev_closes is not None:
            for ticker, state in self._tickers.items():
                prev = self.prev_closes.get(ticker)
                if prev is not None and prev > 0:
                    change[ticker] = state.close / prev - 1
        volume = {t: s.volume for t, s in self._tickers.items() if s.volume > 0}
        lists = {ListName.VOLUME: self._top(volume, True)}
        eligible = {ListName.VOLUME: len(volume)}
        if self.prev_closes is not None:
            lists[ListName.GAINERS] = self._top(change, True)
            lists[ListName.LOSERS] = self._top(change, False)
            eligible[ListName.GAINERS] = eligible[ListName.LOSERS] = len(change)
        if minute >= self.clock.post_first:
            post = {t: s.close / s.regular_close - 1 for t, s in self._tickers.items()
                    if s.post and s.regular_close > 0}
            lists[ListName.POST_GAINERS] = self._top(post, True)
            lists[ListName.POST_LOSERS] = self._top(post, False)
            eligible[ListName.POST_GAINERS] = eligible[ListName.POST_LOSERS] = len(post)
        return lists, eligible

    def day_closes(self) -> dict[str, float]:
        """Each traded ticker's previous close for the next day: its last regular close, else
        its last close of the day (research's `lastc`). Bars still waiting are applied first."""
        for waiting in sorted(self._waiting):
            for bar in self._waiting.pop(waiting):
                self._apply(waiting, bar)
        return {t: (s.regular_close if s.regular_minute >= 0 else s.close)
                for t, s in self._tickers.items() if s.minute >= 0}


@dataclass(frozen=True)
class Split:
    ticker: str
    split_from: float
    split_to: float


def apply_splits(prev: Mapping[str, float], splits: Iterable[Split]) -> dict[str, float]:
    """Previous closes for a split's execution day: each split ticker's close times
    split_from / split_to (research's order of operations); other tickers unchanged."""
    out = dict(prev)
    for split in splits:
        if split.split_to <= 0 or split.split_from <= 0:
            raise ListsError(f"split of {split.ticker} has a ratio at or below 0")
        if split.ticker in out:
            out[split.ticker] *= split.split_from / split.split_to
    return out
