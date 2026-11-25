"""One Massive stocks connection for the minute bars (`AM.*`) and the trades and quotes (`T`,
`Q`) (Codex, codex-108 point 10).

Why one connection: Massive counts WebSocket connections per cluster and an Individual plan
holds one on `/stocks`; a second connection there closes the older one with `max_connections`
(knowledge base, "How many Massive WebSocket connections can I use at one time?", read
2026-11-25). So `MinuteStream` and `TickStream` must not both run on such a plan; this class
carries both channel sets on one socket. Massive sets no ticker limit per stocks connection; the
limit is how fast the client reads ("How many tickers can you subscribe to on a single Massive
WebSocket connection?").

- **Acknowledgments.** Massive answers a subscribe with one status object per parameter,
  `{"ev":"status","status":"success","message":"subscribed to: T.AAPL"}`; a parameter without
  one has not taken. Every subscribe request is recorded with its send time and every parameter
  with its acknowledgment receipt, per connection (`acks`). A decision on a channel whose
  parameter is not acknowledged at its cutoff refuses (`stream_not_acknowledged`). Nothing is
  re-sent on a missing acknowledgment: the refusal stays until the next connection.
- **Backpressure.** Massive drops a client that reads too slowly once its server-side buffer
  fills, and advises reading packets into a queue before parsing them. The socket thread only
  stamps the receipt time and queues the raw text; a router thread parses and hands each event
  to its channel's own queue and consumer thread (`AM`, `T`, `Q`), so a slow tick consumer never
  delays the bars. No queue limit is invented: each lane measures its depth and its lag (receipt
  to handed to the consumer) (`stats`). A decision refuses (`stream_backlog`) while a message
  received by its cutoff still waits in the router or its channel's lane, so a snapshot never
  silently misses what had arrived.
- **Disconnects.** Subscriptions do not carry over a reconnect. A drop opens a gap at once on
  every channel it carries: `DayBars.down` for the bars, `TickStore.down` for every tick key.
  The stream waits `[massive] stream_release_pause_seconds` (Massive's documented release
  window), connects, authenticates, re-sends every subscription, and closes each key's gap at
  its new acknowledgment. Each closed gap is queued as a backfill request (`take_backfill`):
  `TickBackfill.backfill` can fill a tick gap of a symbol; no backfill of a whole-market minute
  exists, so a bar gap keeps refusing the day (`bars_gap`), as with `MinuteStream`.

Decision side: `bars_refusal(cutoff)` and `tick_snapshot(cutoff)` + `tick_refusal(snapshot,
symbol)` give one cause or None. A consumer that failed (an unreadable message) refuses its own
channel only (`stream_consumer_failed`); a stream-level refusal (auth, max_connections, an
error status, an unreadable frame) stops the stream as `MinuteStream` does.
"""

from __future__ import annotations

import collections
import json
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from tickr.config import Config
from tickr.model.errors import ModelRefused
from tickr.model.massive import credential
from tickr.model.ticks import (
    CHANNELS,
    KINDS,
    MARKET,
    STREAM,
    TickSnapshot,
    TickStore,
    stream_row,
)

BARS = "AM"
BARS_PARAM = "AM.*"
ACK_PREFIX = "subscribed to: "  # protocol constant of Massive's subscribe acknowledgment
STREAM_REFUSALS = ("auth_failed", "max_connections", "error")


class GapBarSink(Protocol):
    """The bars side of the shared stream (`DayBars`): bars, an open gap, its close, and
    why a decision cannot use them."""

    def add(self, message: Mapping[str, Any], arrived: Any) -> None: ...

    def down(self, down: Any, cause: str) -> None: ...

    def gap(self, down: Any, up: Any, cause: str) -> None: ...

    def refusal(self, cutoff: Any) -> str | None: ...


class Lane:
    """One FIFO of received items handed to one consumer on its own thread, with its depth and
    lag measured. Items enter in receipt order, so `caught_up(cutoff)` proves every item received
    by `cutoff` was handed."""

    def __init__(self, name: str, handle: Callable[[Any, int], None],
                 clock: Callable[[], int]) -> None:
        self.name = name
        self.handle = handle
        self.clock = clock
        self._items: collections.deque[tuple[Any, int]] = collections.deque()
        self._busy: int | None = None  # receipt of the item being handled
        self._closed = False
        self._cond = threading.Condition()
        self.failed: ModelRefused | None = None
        self.handed = 0
        self.max_depth = 0
        self.last_lag_ns: int | None = None
        self.max_lag_ns = 0
        self.thread = threading.Thread(target=self._run, name=f"stream-{name}", daemon=True)

    def put(self, item: Any, recv_ns: int) -> None:
        with self._cond:
            self._items.append((item, recv_ns))
            self.max_depth = max(self.max_depth, len(self._items))
            self._cond.notify_all()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._items and not self._closed:
                    self._cond.wait()
                if self._closed or self.failed is not None:
                    return
                item, recv_ns = self._items.popleft()
                self._busy = recv_ns
            try:
                self.handle(item, recv_ns)
            except ModelRefused as exc:
                failed: ModelRefused | None = exc
            except Exception as exc:  # noqa: BLE001 - a consumer bug refuses its channel, typed
                failed = ModelRefused("stream_consumer_failed", f"{self.name}: "
                                      f"{type(exc).__name__}")
            else:
                failed = None
            with self._cond:
                self._busy = None
                if failed is not None:
                    self.failed = failed
                    self._cond.notify_all()
                    return
                self.handed += 1
                lag = self.clock() - recv_ns
                self.last_lag_ns = lag
                self.max_lag_ns = max(self.max_lag_ns, lag)
                self._cond.notify_all()

    def caught_up(self, cutoff_ns: int) -> bool:
        """True when no item received by `cutoff_ns` waits or is being handled."""
        with self._cond:
            head = self._busy if self._busy is not None else (
                self._items[0][1] if self._items else None)
            return head is None or head > cutoff_ns

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {"depth": len(self._items) + (self._busy is not None),
                    "max_depth": self.max_depth, "handed": self.handed,
                    "last_lag_ns": self.last_lag_ns, "max_lag_ns": self.max_lag_ns,
                    "oldest_waiting_ns": self._busy if self._busy is not None else (
                        self._items[0][1] if self._items else None),
                    "failed": self.failed.cause if self.failed else None}


class SharedStream:
    """Minute bars and trades/quotes on one connection (module text). `connect` is
    `websockets.sync.client.connect` (a stand-in in the tests); `clock` gives wall-clock ns, the
    receipt stamp of every message, gap end and acknowledgment."""

    def __init__(self, cfg: Config, bars: GapBarSink, ticks: TickStore,
                 connect: Callable[..., Any] | None = None,
                 clock: Callable[[], int] = time.time_ns) -> None:
        self.m = cfg.massive
        self.market = self.m.tick_subscription == "market"
        self.bars = bars
        self.ticks = ticks
        self.clock = clock
        if connect is None:
            from websockets.sync.client import connect as ws_connect

            connect = ws_connect
        self.connect = connect
        self.symbols: set[str] = set()
        # param -> per connection [sent_ns, acked_ns or None, dropped_ns or None, connection]
        self.acks: dict[str, list[list[Any]]] = {}
        self.requests: list[tuple[int, int, tuple[str, ...]]] = []  # (connection, sent, params)
        self.statuses: list[tuple[int, str, str]] = []  # (receipt, status, message)
        self.counts: collections.Counter[str] = collections.Counter()
        self._open: dict[str, tuple[int, str]] = {}  # gap key -> (down, cause) until re-acked
        self._backfill: list[tuple[str, str, int, int]] = []  # (channel, key, down, up)
        self._conn = 0  # the number of the latest connection
        self._live: tuple[int, Any] | None = None  # (connection, socket) while it is open
        self._ws: Any = None  # the socket once logged in
        self._lock = threading.RLock()
        self.router = Lane("router", self._route, clock)
        self.lanes = {
            BARS: Lane(BARS, lambda msg, recv: self.bars.add(msg, recv), clock),
            **{CHANNELS[kind]: Lane(CHANNELS[kind], self._tick_handler(kind), clock)
               for kind in KINDS}}

    def _tick_handler(self, kind: str) -> Callable[[Any, int], None]:
        def handle(message: Any, recv_ns: int) -> None:
            self.ticks.add(kind, [stream_row(kind, message)], recv_ns, STREAM)
        return handle

    # --- subscriptions ---------------------------------------------------------------------

    def _tick_keys(self) -> list[str]:
        return [MARKET] if self.market else sorted(self.symbols)

    @staticmethod
    def _tick_params(keys: list[str]) -> list[str]:
        return [f"{CHANNELS[kind]}.{key}" for key in keys for kind in KINDS]

    def _send(self, ws: Any, params: list[str]) -> None:
        """One subscribe request, recorded before it is sent."""
        sent = self.clock()
        self.requests.append((self._conn, sent, tuple(params)))
        for param in params:
            self.acks.setdefault(param, []).append([sent, None, None, self._conn])
        ws.send(json.dumps({"action": "subscribe", "params": ",".join(params)}))

    def add(self, symbol: str) -> bool:
        """Subscribes `symbol`'s trades and quotes now if logged in, else at the next login;
        True when it is new. With the full-market subscription every symbol already arrives."""
        if not isinstance(symbol, str) or not symbol or symbol == MARKET or "," in symbol:
            raise ModelRefused("tick_symbol", repr(symbol))
        with self._lock:
            if symbol in self.symbols:
                return False
            self.symbols.add(symbol)
            if self._ws is not None and not self.market:
                self._send(self._ws, self._tick_params([symbol]))
            return True

    def _login(self, ws: Any) -> None:
        with self._lock:
            self._send(ws, [BARS_PARAM] + self._tick_params(self._tick_keys()))

    def _acked(self, param: str, recv_ns: int) -> None:
        with self._lock:
            entry = next((e for e in reversed(self.acks.get(param, []))
                          if e[3] == self._conn and e[1] is None), None)
            if entry is None:
                self.counts["unexpected_ack"] += 1
                return
            entry[1] = recv_ns
            if param == BARS_PARAM:
                if BARS_PARAM in self._open:
                    down, cause = self._open.pop(BARS_PARAM)
                    self.bars.gap(down, recv_ns, cause)
                    self._backfill.append((BARS, MARKET, down, recv_ns))
                return
            key = param.split(".", 1)[1]
            params = self._tick_params([key])
            if not all(self._acked_at(p, recv_ns) for p in params):
                return  # the key is complete once both its trades and its quotes took
            self.ticks.subscribe([key], recv_ns)
            if key in self._open:
                down, _ = self._open.pop(key)
                self.ticks.up(recv_ns, [key])
                self._backfill.append(("ticks", key, down, recv_ns))

    def _acked_at(self, param: str, cutoff_ns: int) -> bool:
        return any(a is not None and a <= cutoff_ns and (d is None or d > cutoff_ns)
                   for _, a, d, _ in self.acks.get(param, []))

    def _dropped(self, down: int, cause: str) -> None:
        """Every channel of the connection that ended at `down` is gapped until re-acked."""
        with self._lock:
            for entries in self.acks.values():
                for entry in entries:
                    if entry[3] == self._conn and entry[2] is None:
                        entry[2] = down
            keys = self._tick_keys()
            self.bars.down(down, cause)
            self.ticks.down([k for k in keys if k not in self._open], down, cause)
            for key in [BARS_PARAM, *keys]:
                self._open.setdefault(key, (down, cause))

    def take_backfill(self) -> list[tuple[str, str, int, int]]:
        """The closed gaps not taken yet, (channel, key, down_ns, up_ns): channel `AM` (key `*`,
        no backfill exists) or `ticks` (a symbol, or `*` for the full market)."""
        with self._lock:
            out, self._backfill = self._backfill, []
            return out

    # --- the socket and the router -------------------------------------------------------

    def _route(self, item: tuple[int, str], recv_ns: int) -> None:
        conn, raw = item
        try:
            batch = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelRefused("stream_message_unreadable", "a frame that is not JSON") from exc
        for message in batch if isinstance(batch, list) else [batch]:
            if not isinstance(message, dict):
                raise ModelRefused("stream_message_unreadable", "an event that is not an object")
            ev = message.get("ev")
            if ev == "status":
                self._status(conn, message, recv_ns)
            elif ev in self.lanes:
                if not self._current(conn) or self._ws is None:
                    self.counts["stale_or_early_events"] += 1
                    continue
                self.lanes[ev].put(message, recv_ns)
            else:
                self.counts["other_events"] += 1

    def _status(self, conn: int, message: Mapping[str, Any], recv_ns: int) -> None:
        status, text = str(message.get("status")), str(message.get("message"))
        self.statuses.append((recv_ns, status, text))
        if status in STREAM_REFUSALS:
            raise ModelRefused("stream_refused", status)
        if not self._current(conn):
            self.counts["stale_status"] += 1  # of a connection that has ended
        elif status == "auth_success":
            with self._lock:
                self._ws = self._live[1] if self._live else None
                if self._ws is not None:
                    self._login(self._ws)
        elif status == "success" and text.startswith(ACK_PREFIX):
            self._acked(text[len(ACK_PREFIX):], recv_ns)

    def _current(self, conn: int) -> bool:
        with self._lock:
            return self._live is not None and self._live[0] == conn

    def session(self, stop: threading.Event) -> None:
        """One connection: auth, then read until it closes or `stop` is set. The router sends
        the subscriptions on `auth_success` and records their acknowledgments."""
        url = f"wss://{self.m.stream_host}{self.m.stream_path}"
        with self.connect(url, max_size=self.m.stream_max_message_bytes) as ws:
            with self._lock:
                self._conn += 1
                conn = self._conn
                self._live = (conn, ws)
            try:
                ws.send(json.dumps({"action": "auth",
                                    "params": credential(self.m.api_key_variable)}))
                while not stop.is_set():
                    if self.router.failed is not None:
                        raise self.router.failed
                    try:
                        raw = ws.recv(timeout=float(self.m.stream_reconnect_seconds))
                    except TimeoutError:
                        continue
                    self.router.put((conn, raw), self.clock())
            finally:
                with self._lock:
                    self._ws = None
                    self._live = None

    def run(self, stop: threading.Event) -> None:
        """Connect, and connect again after a drop until `stop`; every drop gaps both channel
        sets at once and waits Massive's release pause before the next connection."""
        for lane in (self.router, *self.lanes.values()):
            lane.thread.start()
        try:
            while not stop.is_set():
                cause = "closed"
                try:
                    self.session(stop)
                except ModelRefused:
                    raise
                except Exception as exc:  # noqa: BLE001 - any transport end is a gap
                    cause = type(exc).__name__
                if self.router.failed is not None:
                    raise self.router.failed
                if stop.is_set():
                    return
                self._dropped(self.clock(), cause)
                stop.wait(float(self.m.stream_release_pause_seconds))
        finally:
            for lane in (self.router, *self.lanes.values()):
                lane.close()

    # --- the decision side ---------------------------------------------------------------

    def _channel_refusal(self, channel: str, cutoff_ns: int) -> str | None:
        lane = self.lanes[channel]
        if lane.failed is not None or self.router.failed is not None:
            return "stream_consumer_failed"
        if not (self.router.caught_up(cutoff_ns) and lane.caught_up(cutoff_ns)):
            return "stream_backlog"
        return None

    def bars_refusal(self, cutoff_ns: int) -> str | None:
        """Why the bars received by `cutoff_ns` cannot be decided on (one cause), or None."""
        with self._lock:
            acked = self._acked_at(BARS_PARAM, cutoff_ns)
        return (self._channel_refusal(BARS, cutoff_ns) or self.bars.refusal(cutoff_ns)
                or (None if acked else "stream_not_acknowledged"))

    def tick_snapshot(self, cutoff_ns: int) -> TickSnapshot:
        """The TickStore snapshot at `cutoff_ns`, taken only once every trade and quote received
        by then was handed to the store; else refuses with the lanes' cause."""
        for kind in KINDS:
            cause = self._channel_refusal(CHANNELS[kind], cutoff_ns)
            if cause is not None:
                raise ModelRefused(cause, f"{CHANNELS[kind]} as of {cutoff_ns}")
        return self.ticks.snapshot(cutoff_ns)

    def tick_refusal(self, snapshot: TickSnapshot, symbol: str) -> str | None:
        """Why `symbol`'s ticks in `snapshot` cannot be decided on (one cause), or None."""
        key = MARKET if self.market else symbol
        with self._lock:
            acked = all(self._acked_at(p, snapshot.cutoff_ns) for p in self._tick_params([key]))
        if not acked and not snapshot.gaps.get(key):  # a drop shows as its gap, not this
            return "stream_not_acknowledged"
        return snapshot.refusal(symbol)

    def stats(self) -> dict[str, dict[str, Any]]:
        """Per lane (router, AM, T, Q): depth, max depth, handed, last and max lag in ns, the
        receipt of the oldest waiting message, a failure. Measured, no limit applied."""
        return {lane.name: lane.stats() for lane in (self.router, *self.lanes.values())}
