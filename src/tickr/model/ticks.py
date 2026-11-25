"""Massive trades and quotes, live (WebSocket channels `T` and `Q`) and backfilled (REST
`/v3/trades/{ticker}`, `/v3/quotes/{ticker}`), kept as rows of the training's flat-file columns
(`us_stocks_sip/trades_v1`, `quotes_v1`, the columns the frozen converter v11 reads) so the
converter can run on the live day as it runs on the day file.

Field map (the pull request and the project note tq-field-map.md hold the table with the
doc links):

- Stream timestamps are Unix MILLISECONDS (`t`, `pt`); the flat file and REST hold nanoseconds.
  A stream row's `sip_timestamp` is `t * 10**6`, exact in unit and truncated in precision; its
  `source` is `stream` so a reader can tell. A REST row replaces the stream row of the same key.
- The stream sends no `correction`: a stream row has none (the flat file leaves it blank for an
  ordinary print too). Whether the stream relays later cancel / error / correction records as
  plain trades is not documented; `source` keeps such rows apart for that check.
- A field the stream leaves out (`trfi`, `pt`, `bp`/`bs`, `ap`/`as`, `c`, `i`) is kept as None,
  never filled: the flat file writes 0 for an empty quote side and blank for no TRF, and the
  converter coalesces both; the raw quote marks of its side files (not model inputs) do not.

Every row also carries `recv_ns`, the local wall-clock time (ns) its message or page arrived,
so the lag of the feed can be measured before any wait is chosen (owner rule).

Overlap of backfill and stream: one row per key (`ticker`, `sequence_number`) per kind;
Massive documents the sequence number as unique per ticker within a session. On a repeat the
REST copy's fields win (nanosecond timestamps, correction), the earlier `recv_ns` is kept, and
a copy whose price, size or SIP millisecond differs is counted in `conflicts`.

Memory: rows are held in Python tuples in memory for the day. A full-market day of quotes is
far larger than a cohort's; the capacity of either path is not measured yet and no cap is
invented here.

Not decided here: which subscription path runs (`[massive] tick_subscription`, "symbols" or
"market", a measurement decision), and whether this stream may share Massive's one connection
per cluster with `MinuteStream` (both use `stream_host` / `stream_path`).
"""

from __future__ import annotations

import datetime
import json
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
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
COMPARED = {"trades": ("price", "size"), "quotes": ("ask_price", "ask_size", "bid_price",
                                                    "bid_size")}


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


# --- the day's rows -------------------------------------------------------------------------


@dataclass
class TickStore:
    """Today's trades and quotes as flat-file rows, one per (ticker, sequence_number) per kind.
    Thread-safe: the stream thread and a backfill add, the decision side reads."""

    day: str
    rows: dict[str, dict[tuple[str, int], dict[str, Any]]] = field(
        default_factory=lambda: {kind: {} for kind in KINDS})
    # symbol (or MARKET) -> [(down_ns, up_ns, cause)]: rows sent while the stream was down are lost
    gaps: dict[str, list[tuple[int, int, str]]] = field(default_factory=dict)
    other_day: int = 0
    repeats: int = 0  # rows already held (stream / backfill overlap)
    conflicts: int = 0  # repeats whose price, size or SIP millisecond differ
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, kind: str, rows: list[dict[str, Any]], recv_ns: int, source: str) -> int:
        """Adds rows (all or none), returns how many were new; a row of another New York
        day is counted and dropped."""
        start, end = day_bounds_ns(self.day)
        new = 0
        with self._lock:
            held = self.rows[kind]
            for row in rows:
                if not start <= row["sip_timestamp"] < end:
                    self.other_day += 1
                    continue
                key = (row["ticker"], row["sequence_number"])
                row = {**row, "recv_ns": recv_ns, "source": source}
                old = held.get(key)
                if old is None:
                    held[key] = row
                    new += 1
                    continue
                self.repeats += 1
                if self._differs(kind, old, row):
                    self.conflicts += 1
                keep = row if source == REST and old["source"] == STREAM else old
                held[key] = {**keep, "recv_ns": min(old["recv_ns"], recv_ns)}
        return new

    @staticmethod
    def _differs(kind: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        if a["sip_timestamp"] // NS_PER_MS != b["sip_timestamp"] // NS_PER_MS:
            return True
        return any(a[c] != b[c] for c in COMPARED[kind])

    def gap(self, symbols: list[str], down_ns: int, up_ns: int, cause: str) -> None:
        with self._lock:
            for symbol in symbols:
                self.gaps.setdefault(symbol, []).append((down_ns, up_ns, cause))

    def frame(self, kind: str, symbol: str | None = None) -> Any:
        """The rows of one kind (one symbol or all) in SIP time then sequence order, with the
        flat-file columns followed by recv_ns and source."""
        import pandas as pd

        with self._lock:
            rows = [r for (sym, _), r in self.rows[kind].items()
                    if symbol is None or sym == symbol]
        rows.sort(key=lambda r: (r["ticker"], r["sip_timestamp"], r["sequence_number"]))
        cols = list(columns(kind))
        # object columns: an absent field stays None and a nanosecond time an exact int (a
        # float column would turn None into NaN and round a participant timestamp)
        return pd.DataFrame([[r[c] for c in cols] for r in rows], columns=cols, dtype=object)


# --- the live stream ------------------------------------------------------------------------


class TickStream:
    """Trades and quotes from the real-time stream into a TickStore: per symbol (a set that
    grows during the day, `add`) or the full market, by `[massive] tick_subscription`.
    `connect` is `websockets.sync.client.connect` (a stand-in in the tests); `clock` gives
    wall-clock ns, comparable with the SIP timestamps."""

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
            return True

    def _login(self, ws: Any) -> None:
        with self._lock:
            self._ws = ws
            params = (self._params([MARKET]) if self.market
                      else self._params(sorted(self.symbols)))
            if params:
                ws.send(json.dumps({"action": "subscribe", "params": params}))

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
        """Connect, and connect again after a drop until `stop`; every drop is a gap of every
        subscribed symbol (MARKET for the full market)."""
        while not stop.is_set():
            down, cause = None, "closed"
            try:
                self.session(stop)
            except ModelRefused:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport end is a gap, never a crash
                down, cause = self.clock(), type(exc).__name__
            if stop.is_set():
                return
            down = down if down is not None else self.clock()
            stop.wait(float(self.m.stream_reconnect_seconds))
            with self._lock:
                keys = [MARKET] if self.market else sorted(self.symbols)
            self.store.gap(keys, down, self.clock(), cause)


# --- the backfill of a symbol that joins late -----------------------------------------------


class TickBackfill:
    """A symbol's trades and quotes of a time range from Massive's REST, paged to the end, into
    the TickStore. One request per page, never retried; a failure refuses and keeps nothing."""

    def __init__(self, cfg: Config, store: TickStore, opener: Opener = _open,
                 clock: Callable[[], int] = time.time_ns) -> None:
        self.m = cfg.massive
        self.store = store
        self.opener = opener
        self.clock = clock

    def _pages(self, kind: str, symbol: str, from_ns: int,
               to_ns: int) -> list[tuple[list[dict[str, Any]], int]]:
        path = f"/v3/{kind}/{urllib.parse.quote(symbol, safe='')}"
        query = {"timestamp.gte": str(from_ns), "timestamp.lt": str(to_ns), "order": "asc",
                 "sort": "timestamp", "limit": str(self.m.tick_page_limit)}
        url = f"https://{self.m.api_host}{path}?{urllib.parse.urlencode(query)}"
        pages = []
        for _ in range(self.m.tick_max_pages):
            headers = {"Authorization": "Bearer " + credential(self.m.api_key_variable)}
            body = _get(self.opener, url, headers, self.m.http_timeout_seconds)
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
                return pages
            parts = urllib.parse.urlsplit(nxt)
            if parts.scheme != "https" or parts.hostname != self.m.api_host:
                # the next request carries the key: only to the configured host over https
                raise ModelRefused("backfill_unreadable", "next page not on the https api host")
            url = nxt
        raise ModelRefused("backfill_incomplete",
                           f"{path}: more than {self.m.tick_max_pages} pages")

    def backfill(self, symbol: str, from_ns: int, to_ns: int) -> dict[str, int]:
        """Trades and quotes of `symbol` with SIP time in [from_ns, to_ns); returns the new rows
        per kind (rows the stream already holds are repeats, see TickStore.add)."""
        if (not isinstance(symbol, str) or not symbol or isinstance(from_ns, bool)
                or isinstance(to_ns, bool) or not isinstance(from_ns, int)
                or not isinstance(to_ns, int) or from_ns >= to_ns):
            raise ModelRefused("backfill_range", f"{symbol!r} {from_ns!r}..{to_ns!r}")
        fetched = {kind: self._pages(kind, symbol, from_ns, to_ns) for kind in KINDS}
        return {kind: sum(self.store.add(kind, rows, recv_ns, REST)
                          for rows, recv_ns in fetched[kind]) for kind in KINDS}
