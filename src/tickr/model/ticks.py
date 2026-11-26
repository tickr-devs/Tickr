"""Massive trades and quotes, live (WebSocket channels `T` and `Q`) and backfilled (REST
`/v3/trades/{ticker}`, `/v3/quotes/{ticker}`), kept as rows of the training's flat-file columns
(`us_stocks_sip/trades_v1`, `quotes_v1`, the columns the frozen converter v11 reads) so the
converter can run on the live day as it runs on the day file.

Field map (the pull request and the project note tq-field-map.md hold the table with the
doc links):

- Stream timestamps are Unix MILLISECONDS (`t`, `pt`); the flat file and REST hold nanoseconds.
  A stream row's `sip_timestamp` is `t * 10**6`, exact in unit and truncated in precision; its
  `source` is `stream` so a reader can tell.
- The stream sends no `correction`: a stream row has none (the flat file leaves it blank for an
  ordinary print too). Whether the stream relays later cancel / error / correction records as
  plain trades is not documented; `source` keeps such rows apart for that check.
- A field the stream leaves out (`trfi`, `pt`, `bp`/`bs`, `ap`/`as`, `c`, `i`) is kept as None,
  never filled: the flat file writes 0 for an empty quote side and blank for no TRF, and the
  converter coalesces both; the raw quote marks of its side files (not model inputs) do not.

Every row also carries `recv_ns`, the local wall-clock time (ns) its message or page arrived,
so the lag of the feed can be measured before any wait is chosen (owner rule).

Versions, not one merged row (Codex, codex-108 point 3): every copy of a print that arrives,
from the stream or from a REST page, is kept as its own version with its own `recv_ns` and
`source`, keyed by (kind, ticker, sequence_number) inside the store of one New York session
date; Massive documents the sequence number as unique per ticker within a session. A version
whose content differs from the version held before it is a typed `Conflict`. A decision reads
an immutable `TickSnapshot` taken at its receipt cutoff: per key the versions received by the
cutoff, of which it uses the latest REST version when one arrived by then (nanosecond time,
`correction`), else the latest stream version, each with its own receipt time. Later arrivals
never change a snapshot already taken.

Initial state (codex-107 point 2): a symbol's day starts at New York midnight, as the day file
does (the converter carries a pre-04:00 quote), so `TickBackfill.initial` fetches from 00:00
America/New_York of the session date. `TickSnapshot.refusal` names why a symbol's ticks are not
proven complete at the cutoff (not subscribed, no complete backfill from midnight, an incomplete
backfill, stream coverage not verified yet, an unfilled stream gap, a conflict); a decision that
needs the symbol refuses with that cause.

Coverage in event time (Codex, codex-109 point 6). A subscription epoch of a key runs from its
(re)subscription to its drop (`TickStore.epochs`, receipt times). What the stream verifiably
covered is told by SIP times only: per symbol and kind, the SIP time of the first and the last
stream event received in each epoch. The stream is taken as complete from an epoch's first
event to its last (to the live edge while the epoch is open), and nothing else is: the SIP
ranges before an epoch's first event, from day start (so current-day events before 04:00 count)
or from the last event of the previous epoch, are holes (`TickSnapshot.recovery`). A hole is
filled only by a complete REST window of that kind that overlaps both ends in SIP time (from at
or before the hole's start, to after its end), so a REST page of a range that a drop cut is
never trusted on the stream's word. A hole whose end is not known yet (no event of the new
epoch) cannot be filled. Local receipt times (a drop, a reconnect, an acknowledgment) are never
compared with SIP times as evidence of coverage. A reset by Massive (for example shortly after
New York midnight) is such a drop: both kinds recover from the last verified event (or day
start) through the first verified event after the reconnect.

Measurement, no thresholds (owner rule): receipt time and lag per symbol, REST requests and
rate-limit answers, late arrivals (a version received after a snapshot whose cutoff already
passed its SIP time) and stream rows that share a SIP millisecond, are counted, not judged.

Memory: rows are held in Python tuples in memory for the day. A full-market day of quotes is
far larger than a cohort's; the capacity of either path is not measured yet and no cap is
invented here.

Not decided here: which subscription path runs (`[massive] tick_subscription`, "symbols" or
"market", a measurement decision). On a plan with one connection per cluster, `MinuteStream`
and `TickStream` would close each other (`max_connections`); `tickr.model.stream.SharedStream`
carries both channel sets on one connection.
"""

from __future__ import annotations

import collections
import datetime
import json
import threading
import time
import types
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tickr.config import Config
from tickr.model.errors import ModelRefused
from tickr.model.massive import NEW_YORK, NS_PER_MS, Opener, _get, _open, credential

# The flat-file columns of trades_v1 the converter v11 reads (its TCOLS, plus
# participant_timestamp of its side files), and of quotes_v1 (its QCOLS).
TRADE_COLUMNS = ("ticker", "conditions", "correction", "exchange", "price", "size",
                 "sip_timestamp", "sequence_number", "trf_id", "participant_timestamp")
QUOTE_COLUMNS = ("ticker", "ask_price", "ask_size", "bid_price", "bid_size", "sip_timestamp",
                 "sequence_number", "conditions", "indicators")
EXTRA_COLUMNS = ("recv_ns", "source")
STREAM, REST = "stream", "rest"
KINDS = ("trades", "quotes")
MARKET = "*"  # the gap key of a full-market subscription
SUBSCRIPTIONS = ("symbols", "market")
CHANNELS = {"trades": "T", "quotes": "Q"}

# Stream key -> flat-file column. `ms` marks a millisecond time put in nanoseconds; `codes` a
# list or single code written as the file's comma-separated text.
TRADE_STREAM = {"sym": "ticker", "c": "conditions", "x": "exchange", "p": "price", "s": "size",
                "t": "sip_timestamp", "q": "sequence_number", "trfi": "trf_id",
                "pt": "participant_timestamp"}
QUOTE_STREAM = {"sym": "ticker", "ap": "ask_price", "as": "ask_size", "bp": "bid_price",
                "bs": "bid_size", "t": "sip_timestamp", "q": "sequence_number",
                "c": "conditions", "i": "indicators"}
STREAM_MS = {"t", "pt"}
STREAM_REQUIRED = {"trades": ("sym", "x", "p", "s", "t", "q"), "quotes": ("sym", "t", "q")}
# REST results carry the flat-file names (no ticker: it is the path's).
REST_REQUIRED = {"trades": ("exchange", "price", "size", "sip_timestamp", "sequence_number"),
                 "quotes": ("sip_timestamp", "sequence_number")}
CODE_COLUMNS = {"conditions", "indicators"}
# Columns a stream and a REST version of one print both carry with one meaning (besides the SIP
# millisecond): a difference is a conflict. Two versions of one source compare every column.
CROSS_COMPARED = {"trades": ("exchange", "price", "size", "conditions", "trf_id"),
                  "quotes": ("ask_price", "ask_size", "bid_price", "bid_size", "indicators")}
# An empty quote side: the stream leaves it out (None), REST and the file write 0.
QUOTE_SIDES = {"ask_price", "ask_size", "bid_price", "bid_size"}
HTTP_TOO_MANY_REQUESTS = "429"  # protocol constant: the status of a rate-limit answer


def columns(kind: str) -> tuple[str, ...]:
    return (TRADE_COLUMNS if kind == "trades" else QUOTE_COLUMNS) + EXTRA_COLUMNS


def day_bounds_ns(day: str) -> tuple[int, int]:
    """New York midnight to the next midnight of `day`, Unix ns: the span of the day file."""
    start = datetime.datetime.fromisoformat(day).replace(tzinfo=NEW_YORK)
    end = (start + datetime.timedelta(days=1)).replace(tzinfo=NEW_YORK)
    return int(start.timestamp()) * 10**9, int(end.timestamp()) * 10**9


def _number(value: Any, what: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ModelRefused("tick_unreadable", what)
    return value


def _codes(value: Any, what: str) -> str | None:
    """A list of codes (or one code) as the flat file's text `12,37`; absent stays None."""
    if value is None:
        return None
    items = value if isinstance(value, list) else [value]
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in items):
        raise ModelRefused("tick_unreadable", what)
    return ",".join(str(v) for v in items)


def stream_row(kind: str, message: Mapping[str, Any]) -> dict[str, Any]:
    """One T or Q message as flat-file columns; a missing required field refuses."""
    for key in STREAM_REQUIRED[kind]:
        if message.get(key) is None:
            raise ModelRefused("tick_unreadable", f"a {CHANNELS[kind]} message without {key}")
    if not isinstance(message["sym"], str) or not message["sym"]:
        raise ModelRefused("tick_unreadable", f"a {CHANNELS[kind]} message without sym")
    row: dict[str, Any] = dict.fromkeys(TRADE_COLUMNS if kind == "trades" else QUOTE_COLUMNS)
    for key, col in (TRADE_STREAM if kind == "trades" else QUOTE_STREAM).items():
        value = message.get(key)
        if value is None or col == "ticker":
            row[col] = value
        elif col in CODE_COLUMNS:
            row[col] = _codes(value, f"{CHANNELS[kind]}.{key}")
        else:
            value = _number(value, f"{CHANNELS[kind]}.{key}")
            row[col] = value * NS_PER_MS if key in STREAM_MS else value
    return row


def rest_row(kind: str, symbol: str, result: Any) -> dict[str, Any]:
    """One REST result as flat-file columns; a missing required field refuses."""
    if not isinstance(result, dict):
        raise ModelRefused("tick_unreadable", f"a {kind} result that is not an object")
    for key in REST_REQUIRED[kind]:
        if result.get(key) is None:
            raise ModelRefused("tick_unreadable", f"a {kind} result without {key}")
    row: dict[str, Any] = {"ticker": symbol}
    for col in (TRADE_COLUMNS if kind == "trades" else QUOTE_COLUMNS)[1:]:
        value = result.get(col)
        if value is None:
            row[col] = None
        elif col in CODE_COLUMNS:
            row[col] = _codes(value, f"{kind}.{col}")
        else:
            row[col] = _number(value, f"{kind}.{col}")
    return row


# --- the day's versions and the decision snapshot --------------------------------------------


@dataclass(frozen=True)
class Conflict:
    """Two versions of one print whose content differs: `fields` names the columns, `held` and
    `later` the (source, recv_ns) of the version held before and of the one that arrived."""

    kind: str
    ticker: str
    sequence_number: int
    fields: tuple[str, ...]
    held: tuple[str, int]
    later: tuple[str, int]


@dataclass(frozen=True)
class Window:
    """One REST backfill of one kind of a symbol over SIP time [from_ns, to_ns): `cause` is None
    only when its final page came without next_url (exhaustion proven), else why it is
    incomplete; `recv_ns` is the receipt of its last answer."""

    symbol: str
    kind: str
    from_ns: int
    to_ns: int
    recv_ns: int
    pages: int
    cause: str | None


def _same(col: str, a: Any, b: Any) -> bool:
    if a == b:
        return True
    if col in QUOTE_SIDES:
        return a in (None, 0) and b in (None, 0)
    return col in CODE_COLUMNS and a in (None, "") and b in (None, "")


class TickStore:
    """Every version of the session date's trades and quotes (see the module text). Thread-safe:
    the stream thread and a backfill add, the decision side takes snapshots."""

    def __init__(self, day: str) -> None:
        self.day = day
        self.start_ns, self.end_ns = day_bounds_ns(day)
        self.cols = {kind: columns(kind) for kind in KINDS}
        # kind -> (ticker, sequence_number) -> versions (column tuples) in arrival order
        self.versions: dict[str, dict[tuple[str, int], list[tuple[Any, ...]]]] = {
            kind: {} for kind in KINDS}
        self.conflicts: list[Conflict] = []
        self.windows: list[Window] = []
        self.subscribed: dict[str, int] = {}  # symbol (or MARKET) -> first subscribe sent, ns
        # symbol (or MARKET) -> [down_ns, up_ns or None while down, cause]: rows sent then are lost
        self.gaps: dict[str, list[list[Any]]] = {}
        # symbol (or MARKET) -> subscription epochs [start_ns, end_ns or None while open] (receipt)
        self.epochs: dict[str, list[list[Any]]] = {}
        # (kind, ticker) -> epoch index -> [first SIP ns, its receipt ns, last SIP ns] of the stream
        self._cover: dict[tuple[str, str], dict[int, list[int]]] = {}
        # measurement counts: other_day, late, same_ms_trades / _quotes / _trade_quote
        self.counts: collections.Counter[str] = collections.Counter()
        self._cutoff = 0  # the latest cutoff a snapshot was taken at
        self._last_ms: dict[tuple[str, str], int] = {}  # (kind, ticker) -> last stream SIP ms
        self._lock = threading.Lock()

    def add(self, kind: str, rows: list[dict[str, Any]], recv_ns: int, source: str) -> int:
        """Keeps every row as a version received at `recv_ns` from `source`; returns how many
        were the first version of their key. A row of another New York day is counted, dropped."""
        cols = self.cols[kind]
        new = 0
        with self._lock:
            held = self.versions[kind]
            for row in rows:
                ts = row["sip_timestamp"]
                if not self.start_ns <= ts < self.end_ns:
                    self.counts["other_day"] += 1
                    continue
                version = tuple({**row, "recv_ns": recv_ns, "source": source}[c] for c in cols)
                key = (row["ticker"], row["sequence_number"])
                if ts < self._cutoff < recv_ns:
                    self.counts["late"] += 1
                if source == STREAM:
                    self._same_ms(kind, row["ticker"], ts // NS_PER_MS)
                    self._covered(kind, row["ticker"], ts, recv_ns)
                versions = held.setdefault(key, [])
                if not versions:
                    new += 1
                else:
                    before = versions[-1]
                    fields = self._differs(kind, before, version)
                    if fields:
                        self.conflicts.append(Conflict(kind, key[0], key[1], fields,
                                                       (before[-1], before[-2]),
                                                       (source, recv_ns)))
                versions.append(version)
        return new

    def _same_ms(self, kind: str, ticker: str, ms: int) -> None:
        other = "quotes" if kind == "trades" else "trades"
        if self._last_ms.get((kind, ticker)) == ms:
            self.counts[f"same_ms_{kind}"] += 1
        if self._last_ms.get((other, ticker)) == ms:
            self.counts["same_ms_trade_quote"] += 1
        self._last_ms[(kind, ticker)] = ms

    def _key(self, ticker: str) -> str | None:
        return ticker if ticker in self.epochs else (MARKET if MARKET in self.epochs else None)

    def _covered(self, kind: str, ticker: str, sip_ns: int, recv_ns: int) -> None:
        """A stream event received inside an epoch of its key extends that epoch's coverage."""
        key = self._key(ticker)
        if key is None:
            return  # received before any subscription took: proves nothing
        epochs = self.epochs[key]
        for idx in range(len(epochs) - 1, -1, -1):
            start, end = epochs[idx]
            if start <= recv_ns and (end is None or recv_ns < end):
                held = self._cover.setdefault((kind, ticker), {})
                if idx in held:
                    held[idx][2] = sip_ns
                else:
                    held[idx] = [sip_ns, recv_ns, sip_ns]
                return

    def _differs(self, kind: str, a: tuple[Any, ...], b: tuple[Any, ...]) -> tuple[str, ...]:
        cols = self.cols[kind]
        va, vb = dict(zip(cols, a, strict=True)), dict(zip(cols, b, strict=True))
        if va["source"] == vb["source"]:
            names = [c for c in cols[:-len(EXTRA_COLUMNS)] if not _same(c, va[c], vb[c])]
        else:
            names = [c for c in CROSS_COMPARED[kind] if not _same(c, va[c], vb[c])]
            if va["sip_timestamp"] // NS_PER_MS != vb["sip_timestamp"] // NS_PER_MS:
                names.append("sip_timestamp")
        return tuple(names)

    def subscribe(self, keys: list[str], at_ns: int) -> None:
        with self._lock:
            for key in keys:
                self.subscribed.setdefault(key, at_ns)
                self.epochs.setdefault(key, [[at_ns, None]])

    def down(self, keys: list[str], down_ns: int, cause: str) -> None:
        """The stream of `keys` dropped at `down_ns`; the gap stays open until `up`."""
        with self._lock:
            for key in keys:
                self.gaps.setdefault(key, []).append([down_ns, None, cause])
                epochs = self.epochs.get(key)
                if epochs and epochs[-1][1] is None:
                    epochs[-1][1] = down_ns

    def up(self, up_ns: int, keys: list[str] | None = None) -> None:
        """The subscription was sent again (or, on the shared stream, acknowledged): the open
        gaps of `keys` (every key when None) close at `up_ns`."""
        with self._lock:
            for key, gaps in self.gaps.items():
                if keys is not None and key not in keys:
                    continue
                for gap in gaps:
                    if gap[1] is None:
                        gap[1] = up_ns
                epochs = self.epochs.get(key)
                if epochs and epochs[-1][1] is not None:
                    epochs.append([up_ns, None])

    def window(self, window: Window) -> None:
        with self._lock:
            self.windows.append(window)

    def snapshot(self, cutoff_ns: int) -> TickSnapshot:
        """What had arrived by `cutoff_ns` (receipt time), frozen for one decision."""
        rows: dict[str, tuple[tuple[Any, ...], ...]] = {}
        last: dict[str, tuple[int, int]] = {}  # ticker -> (last stream recv_ns, its SIP ns)
        with self._lock:
            self._cutoff = max(self._cutoff, cutoff_ns)
            for kind in KINDS:
                ts, seq = (self.cols[kind].index(c) for c in ("sip_timestamp", "sequence_number"))
                chosen = []
                for versions in self.versions[kind].values():
                    seen = [v for v in versions if v[-2] <= cutoff_ns]
                    if not seen:
                        continue
                    chosen.append(max(seen, key=lambda v: (v[-1] == REST, v[-2])))
                    for v in seen:
                        if v[-1] == STREAM and v[-2] >= last.get(v[0], (-1, 0))[0]:
                            last[v[0]] = (v[-2], v[ts])
                chosen.sort(key=lambda v: (v[0], v[ts], v[seq]))
                rows[kind] = tuple(chosen)
            gaps = {key: tuple((d, u if u is not None and u <= cutoff_ns else None, c)
                               for d, u, c in found if d <= cutoff_ns)
                    for key, found in self.gaps.items()}
            epochs = {key: tuple((s, e if e is not None and e <= cutoff_ns else None)
                                 for s, e in found if s <= cutoff_ns)
                      for key, found in self.epochs.items()}
            cover = {}
            for (kind, ticker), held in self._cover.items():
                key = self._key(ticker)
                spans = epochs.get(key, ()) if key is not None else ()
                cover[(kind, ticker)] = tuple(
                    (idx, first, last if spans[idx][1] is not None else None)
                    for idx, (first, first_recv, last) in held.items()
                    if first_recv <= cutoff_ns and idx < len(spans))
            return TickSnapshot(
                day=self.day, start_ns=self.start_ns, cutoff_ns=cutoff_ns,
                rows=types.MappingProxyType(rows),
                conflicts=tuple(c for c in self.conflicts if c.later[1] <= cutoff_ns),
                windows=tuple(w for w in self.windows if w.recv_ns <= cutoff_ns),
                subscribed=types.MappingProxyType(
                    {k: v for k, v in self.subscribed.items() if v <= cutoff_ns}),
                gaps=types.MappingProxyType({k: v for k, v in gaps.items() if v}),
                last=types.MappingProxyType(last),
                epochs=types.MappingProxyType({k: v for k, v in epochs.items() if v}),
                cover=types.MappingProxyType({k: v for k, v in cover.items() if v}))


@dataclass(frozen=True)
class TickSnapshot:
    """The ticks a decision saw: per key the version chosen as of `cutoff_ns` (module text), and
    the per-symbol delivery state at that cutoff. Immutable: later arrivals change the store,
    never a snapshot taken before them."""

    day: str
    start_ns: int
    cutoff_ns: int
    rows: Mapping[str, tuple[tuple[Any, ...], ...]]
    conflicts: tuple[Conflict, ...]
    windows: tuple[Window, ...]
    subscribed: Mapping[str, int]
    gaps: Mapping[str, tuple[tuple[int, int | None, str], ...]]
    last: Mapping[str, tuple[int, int]]
    # key -> epochs (start_ns, end_ns or None while open at the cutoff), receipt times
    epochs: Mapping[str, tuple[tuple[int, int | None], ...]]
    # (kind, ticker) -> (epoch index, first SIP ns, last SIP ns or None while the epoch is open)
    cover: Mapping[tuple[str, str], tuple[tuple[int, int, int | None], ...]]

    def frame(self, kind: str, symbol: str | None = None) -> Any:
        """The rows of one kind (one symbol or all) in ticker, SIP time, sequence order, with the
        flat-file columns followed by recv_ns and source of the chosen version."""
        import pandas as pd

        rows = [r for r in self.rows[kind] if symbol is None or r[0] == symbol]
        # object columns: an absent field stays None and a nanosecond time an exact int
        return pd.DataFrame(rows, columns=list(columns(kind)), dtype=object)

    def lag(self, symbol: str) -> tuple[int, int] | None:
        """(receipt ns, receipt minus SIP ns) of the symbol's latest stream version by the
        cutoff; None when none arrived. Recorded for measurement, no limit applied."""
        if symbol not in self.last:
            return None
        recv, sip = self.last[symbol]
        return recv, recv - sip

    def _key(self, symbol: str) -> str | None:
        return symbol if symbol in self.subscribed else (
            MARKET if MARKET in self.subscribed else None)

    def _holes(self, symbol: str, key: str) -> dict[str, list[tuple[int, int | None, bool]]]:
        """Per kind, the SIP ranges [lo, hi] the stream has not proven (module text), with hi None
        while the next verified event is not known, and True for the day's initial hole."""
        out: dict[str, list[tuple[int, int | None, bool]]] = {}
        for kind in KINDS:
            cover = {idx: (first, last) for idx, first, last in self.cover.get((kind, symbol), ())}
            lo: int | None = self.start_ns
            initial = True
            holes: list[tuple[int, int | None, bool]] = []
            for idx, (_, end) in enumerate(self.epochs.get(key, ())):
                first, last = cover.get(idx, (None, None))
                if first is None:
                    if end is None:
                        break  # the open epoch has no event yet: its hole has no known end
                    initial = False  # a closed epoch that proved nothing
                    continue
                if lo is not None:
                    holes.append((lo, first, initial))
                if end is None:
                    lo = None  # the stream covers from its first event to the live edge
                    break
                lo, initial = last, False
            if lo is not None:
                holes.append((lo, None, initial))
            out[kind] = holes
        return out

    @staticmethod
    def _filled(windows: list[Window], kind: str, lo: int, hi: int | None) -> bool:
        return hi is not None and any(w.kind == kind and w.cause is None and w.from_ns <= lo
                                      and w.to_ns > hi for w in windows)

    def recovery(self, symbol: str) -> tuple[tuple[str, int, int | None], ...]:
        """The SIP ranges (kind, from_ns, through_ns inclusive or None while unknown) that still
        need a complete REST window before the symbol's ticks are proven: backfill each with
        `TickBackfill.backfill(symbol, from_ns, through_ns + 1)` once its end is known."""
        key = self._key(symbol)
        if key is None:
            return ()
        mine = [w for w in self.windows if w.symbol == symbol]
        return tuple((kind, lo, hi) for kind, holes in self._holes(symbol, key).items()
                     for lo, hi, _ in holes if not self._filled(mine, kind, lo, hi))

    def refusal(self, symbol: str) -> str | None:
        """Why the symbol's ticks are not proven complete from midnight to the cutoff (one
        cause), or None. Every hole of the stream's SIP coverage (module text) must be filled by
        a complete window of its kind that overlaps both its ends."""
        key = self._key(symbol)
        if key is None:
            return "tick_not_subscribed"
        mine = [w for w in self.windows if w.symbol == symbol]
        holes = self._holes(symbol, key)
        for kind in KINDS:
            for lo, hi, initial in holes[kind]:
                if not initial or self._filled(mine, kind, lo, hi):
                    continue
                if hi is None and any(w.kind == kind and w.cause is None and w.from_ns <= lo
                                      for w in mine):
                    return "tick_coverage_unverified"  # no stream event proves the live edge
                return ("tick_backfill_incomplete" if any(w.cause for w in mine)
                        else "tick_no_initial_state")
        for kind in KINDS:
            if any(not initial and not self._filled(mine, kind, lo, hi)
                   for lo, hi, initial in holes[kind]):
                return "tick_gap"
        if any(c.ticker == symbol for c in self.conflicts):
            return "tick_conflict"
        return None

    def require(self, symbol: str) -> None:
        cause = self.refusal(symbol)
        if cause is not None:
            raise ModelRefused(cause, f"{symbol} as of {self.cutoff_ns}")


# --- the live stream ------------------------------------------------------------------------


class TickStream:
    """Trades and quotes from the real-time stream into a TickStore: per symbol (a set that
    grows during the day, `add`) or the full market, by `[massive] tick_subscription`.
    `connect` is `websockets.sync.client.connect` (a stand-in in the tests); `clock` gives
    wall-clock ns, comparable with the SIP timestamps. A subscription is recorded when it is
    sent (its acknowledgment is not measured yet)."""

    def __init__(self, cfg: Config, store: TickStore, connect: Callable[..., Any] | None = None,
                 clock: Callable[[], int] = time.time_ns) -> None:
        self.m = cfg.massive
        self.market = self.m.tick_subscription == "market"
        self.store = store
        self.clock = clock
        if connect is None:
            from websockets.sync.client import connect as ws_connect

            connect = ws_connect
        self.connect = connect
        self.symbols: set[str] = set()
        self._ws: Any = None  # the connection while logged in
        self._lock = threading.Lock()

    @staticmethod
    def _params(symbols: list[str]) -> str:
        return ",".join(f"{CHANNELS[k]}.{s}" for s in symbols for k in KINDS)

    def add(self, symbol: str) -> bool:
        """Subscribes `symbol` now if logged in, else at the next login; True when it is new.
        With the full-market subscription every symbol already arrives."""
        if not isinstance(symbol, str) or not symbol or symbol == MARKET or "," in symbol:
            raise ModelRefused("tick_symbol", repr(symbol))
        with self._lock:
            if symbol in self.symbols:
                return False
            self.symbols.add(symbol)
            if self._ws is not None and not self.market:
                self._ws.send(json.dumps({"action": "subscribe",
                                          "params": self._params([symbol])}))
                self.store.subscribe([symbol], self.clock())
            return True

    def _keys(self) -> list[str]:
        return [MARKET] if self.market else sorted(self.symbols)

    def _login(self, ws: Any) -> None:
        with self._lock:
            self._ws = ws
            keys = self._keys()
            if keys:
                ws.send(json.dumps({"action": "subscribe", "params": self._params(keys)}))
                now = self.clock()
                self.store.subscribe(keys, now)
                self.store.up(now)

    def session(self, stop: threading.Event) -> None:
        """One connection: auth, subscribe, read until it closes or `stop` is set."""
        url = f"wss://{self.m.stream_host}{self.m.stream_path}"
        try:
            with self.connect(url, max_size=self.m.stream_max_message_bytes) as ws:
                ws.send(json.dumps({"action": "auth",
                                    "params": credential(self.m.api_key_variable)}))
                while not stop.is_set():
                    try:
                        raw = ws.recv(timeout=float(self.m.stream_reconnect_seconds))
                    except TimeoutError:
                        continue
                    arrived = self.clock()
                    batch = json.loads(raw)
                    for message in batch if isinstance(batch, list) else [batch]:
                        self._read(ws, message, arrived)
        finally:
            with self._lock:
                self._ws = None

    def _read(self, ws: Any, message: Any, arrived: int) -> None:
        if not isinstance(message, dict):
            raise ModelRefused("tick_unreadable", "a stream event that is not an object")
        ev = message.get("ev")
        if ev == "status":
            status = message.get("status")
            if status == "auth_success":
                self._login(ws)
            elif status in ("auth_failed", "max_connections", "error"):
                raise ModelRefused("stream_refused", str(status))
            return
        kind = {"T": "trades", "Q": "quotes"}.get(ev)  # type: ignore[arg-type]
        if kind is None:
            return
        if self._ws is None:
            raise ModelRefused("tick_unreadable", "ticks before auth")
        self.store.add(kind, [stream_row(kind, message)], arrived, STREAM)

    def run(self, stop: threading.Event) -> None:
        """Connect, and connect again after a drop until `stop`; every drop opens a gap of every
        subscribed symbol (MARKET for the full market) that the next subscription closes."""
        while not stop.is_set():
            cause = "closed"
            try:
                self.session(stop)
            except ModelRefused:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport end is a gap, never a crash
                cause = type(exc).__name__
            if stop.is_set():
                return
            with self._lock:
                keys = self._keys()
            self.store.down(keys, self.clock(), cause)
            stop.wait(float(self.m.stream_reconnect_seconds))


# --- the backfill of a symbol that joins late or of a stream gap ---------------------------


class TickBackfill:
    """A symbol's trades and quotes of a SIP time range from Massive's REST, paged to the end,
    into the TickStore as REST versions with each page's receipt time. One request per page,
    never retried. Each kind's outcome is a `Window` kept in the store: complete only when the
    final page came without next_url. Paging has no count limit (none is measured): a page
    without progress (no rows, a last SIP time that does not pass the previous page's, or a
    next_url fetched before) ends the window as incomplete (`backfill_no_progress`) with the
    rows received kept. A failed or unreadable answer refuses, keeps no row of the backfill and
    records the window as incomplete with that cause. `requests` and `rate_limited` count every
    request and every 429 answer (measurement)."""

    def __init__(self, cfg: Config, store: TickStore, opener: Opener = _open,
                 clock: Callable[[], int] = time.time_ns) -> None:
        self.m = cfg.massive
        self.store = store
        self.opener = opener
        self.clock = clock
        self.requests = 0
        self.rate_limited = 0

    def _get(self, url: str) -> bytes:
        self.requests += 1
        headers = {"Authorization": "Bearer " + credential(self.m.api_key_variable)}
        try:
            return _get(self.opener, url, headers, self.m.http_timeout_seconds)
        except ModelRefused as exc:
            if exc.cause == "http_status" and exc.detail.split(" ")[0] == HTTP_TOO_MANY_REQUESTS:
                self.rate_limited += 1
            raise

    def _pages(self, kind: str, symbol: str, from_ns: int, to_ns: int,
               pages: list[tuple[list[dict[str, Any]], int]]) -> str | None:
        """Fetches into `pages`; returns None when exhaustion is proven, else the cause."""
        path = f"/v3/{kind}/{urllib.parse.quote(symbol, safe='')}"
        query = {"timestamp.gte": str(from_ns), "timestamp.lt": str(to_ns), "order": "asc",
                 "sort": "timestamp", "limit": str(self.m.tick_page_limit)}
        url = f"https://{self.m.api_host}{path}?{urllib.parse.urlencode(query)}"
        seen = {url}
        last_ts: int | None = None
        while True:
            body = self._get(url)
            recv_ns = self.clock()
            try:
                page = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ModelRefused("backfill_unreadable", path) from exc
            if (not isinstance(page, dict) or page.get("status") != "OK"
                    or not isinstance(page.get("results", []), list)):
                raise ModelRefused("backfill_unreadable", f"{path}: status "
                                   f"{page.get('status') if isinstance(page, dict) else None}")
            rows = [rest_row(kind, symbol, r) for r in page.get("results", [])]
            if any(not from_ns <= r["sip_timestamp"] < to_ns for r in rows):
                raise ModelRefused("backfill_unreadable", f"{path}: a row outside the range")
            pages.append((rows, recv_ns))
            nxt = page.get("next_url")
            if not nxt:
                return None
            parts = urllib.parse.urlsplit(nxt)
            if parts.scheme != "https" or parts.hostname != self.m.api_host:
                # the next request carries the key: only to the configured host over https
                raise ModelRefused("backfill_unreadable", "next page not on the https api host")
            page_last = max((r["sip_timestamp"] for r in rows), default=None)
            if (page_last is None or (last_ts is not None and page_last <= last_ts)
                    or nxt in seen):
                return "backfill_no_progress"  # a stalled cursor is not proof of the end
            last_ts = page_last
            seen.add(nxt)
            url = nxt

    def backfill(self, symbol: str, from_ns: int, to_ns: int) -> dict[str, Window]:
        """Trades and quotes of `symbol` with SIP time in [from_ns, to_ns); returns each kind's
        window (see TickSnapshot.refusal for how a decision reads it)."""
        if (not isinstance(symbol, str) or not symbol or isinstance(from_ns, bool)
                or isinstance(to_ns, bool) or not isinstance(from_ns, int)
                or not isinstance(to_ns, int) or from_ns >= to_ns):
            raise ModelRefused("backfill_range", f"{symbol!r} {from_ns!r}..{to_ns!r}")
        fetched: dict[str, tuple[list[tuple[list[dict[str, Any]], int]], str | None]] = {}
        for kind in KINDS:
            pages: list[tuple[list[dict[str, Any]], int]] = []
            try:
                fetched[kind] = (pages, self._pages(kind, symbol, from_ns, to_ns, pages))
            except ModelRefused as exc:
                self.store.window(Window(symbol, kind, from_ns, to_ns, self.clock(),
                                         len(pages), exc.cause))
                raise
        windows = {}
        for kind, (pages, cause) in fetched.items():
            for rows, recv_ns in pages:
                self.store.add(kind, rows, recv_ns, REST)
            windows[kind] = Window(symbol, kind, from_ns, to_ns, pages[-1][1], len(pages), cause)
            self.store.window(windows[kind])
        return windows

    def initial(self, symbol: str) -> dict[str, Window]:
        """The symbol's day so far, from 00:00 America/New_York of the session date (the day
        file's start: the converter carries a pre-04:00 quote) to now. Subscribe the symbol
        first: the window counts only once it reaches past the SIP time of the first stream
        event of each kind after the subscription (TickSnapshot.recovery), so no row falls
        between them."""
        return self.backfill(symbol, self.store.start_ns, min(self.clock(), self.store.end_ns))
