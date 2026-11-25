"""Minute bars and trades/quotes on one Massive connection (codex-108 point 10): a stand-in
socket that answers each subscribe parameter like Massive, no network."""

from __future__ import annotations

import dataclasses
import itertools
import json
import threading
import time

import pytest
from test_tick_stream import AUTH, CFG, DAY, KEY, MS, START, T0, trade

from tickr.model import ticks
from tickr.model.errors import ModelRefused
from tickr.model.massive import DayBars
from tickr.model.stream import SharedStream

ALL = 10**30  # a cutoff after every receipt of a test
WAIT = 5.0  # seconds a test waits for a stream thread before it fails (test-side only)
CFG0 = dataclasses.replace(CFG, massive=dataclasses.replace(
    CFG.massive, stream_release_pause_seconds=0, tick_subscription="symbols"))


def bar(sym="AAPL", s=T0 // MS):
    return {"ev": "AM", "sym": sym, "v": 100, "o": 1.0, "c": 1.1, "h": 1.2, "l": 0.9, "s": s}


def until(check):
    deadline = time.monotonic() + WAIT
    while not check():
        assert time.monotonic() < deadline, "stream thread did not get there"
        time.sleep(0.001)


class Server:
    """Frames to send, an acknowledgment per subscribed parameter (except `unacked`), and at
    the end either a drop or the stop flag."""

    def __init__(self, frames, drop=False, unacked=()):
        self.incoming = [json.dumps(f) for f in frames]
        self.sent = []
        self.drop = drop
        self.unacked = set(unacked)
        self.stream = None
        self.stop = None
        self.at_end = None  # runs once when the frames are all read and routed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, text):
        msg = json.loads(text)
        self.sent.append(msg)
        if msg["action"] == "subscribe":
            self.incoming.append(json.dumps([
                {"ev": "status", "status": "success", "message": f"subscribed to: {p}"}
                for p in msg["params"].split(",") if p not in self.unacked]))

    def recv(self, timeout):
        if not self.incoming:
            until(lambda: self.stream.router.caught_up(ALL))
        if self.incoming:
            return self.incoming.pop(0)
        if self.at_end is not None:
            hook, self.at_end = self.at_end, None
            hook()
            raise TimeoutError
        until(lambda: all(lane.caught_up(ALL) or lane.failed
                          for lane in self.stream.lanes.values()))
        if self.drop:
            raise ConnectionError("closed")
        self.stop.set()
        raise TimeoutError


@pytest.fixture
def make(monkeypatch):
    monkeypatch.setenv(CFG.massive.api_key_variable, KEY)

    def build(servers, cfg=CFG0, on_connect=None):
        stop = threading.Event()
        bars, store = DayBars(DAY), ticks.TickStore(DAY)
        count = itertools.count(START + 10**12)
        made = []

        def connect(url, max_size):
            assert url == f"wss://{cfg.massive.stream_host}{cfg.massive.stream_path}"
            if on_connect is not None:
                on_connect(len(made))
            server = servers.pop(0)
            server.stream, server.stop = stream, stop
            made.append(server)
            return server

        stream = SharedStream(cfg, bars, store, connect, clock=lambda: next(count))
        return stream, stop, bars, store, made
    return build


def complete(store, symbol, upto=ALL):
    for kind in ticks.KINDS:
        store.window(ticks.Window(symbol, kind, store.start_ns, upto, 0, 1, None))


def test_one_connection_subscribes_both_channel_sets_and_records_each_acknowledgment(make):
    stream, stop, bars, store, made = make([Server([[AUTH], [bar(), trade()]])])
    stream.add("AAPL")
    stream.run(stop)
    assert len(made) == 1
    assert made[0].sent == [{"action": "auth", "params": KEY},
                            {"action": "subscribe", "params": "AM.*,T.AAPL,Q.AAPL"}]
    [(conn, sent, params)] = stream.requests
    assert conn == 1 and params == ("AM.*", "T.AAPL", "Q.AAPL")
    for param in params:
        [[at, acked, dropped, c]] = stream.acks[param]
        assert at == sent and acked > sent and dropped is None and c == 1
    assert store.subscribed == {"AAPL": stream.acks["Q.AAPL"][0][1]}  # when both took
    assert len(bars.rows) == 1 and len(store.versions["trades"]) == 1
    assert stream.bars_refusal(ALL) is None
    complete(store, "AAPL")
    assert stream.tick_refusal(stream.tick_snapshot(ALL), "AAPL") is None
    assert stream.stats()["AM"]["handed"] == 1 and stream.stats()["T"]["max_lag_ns"] > 0


def test_a_symbol_joining_later_is_its_own_acknowledged_request(make):
    server = Server([[AUTH]])
    stream, stop, _, store, _ = make([server])
    server.at_end = lambda: stream.add("MSFT")
    stream.run(stop)
    assert server.sent[1:] == [{"action": "subscribe", "params": "AM.*"},
                               {"action": "subscribe", "params": "T.MSFT,Q.MSFT"}]
    assert all(stream.acks[p][0][1] is not None for p in ("T.MSFT", "Q.MSFT"))
    assert "MSFT" in store.subscribed


@pytest.mark.parametrize("unacked", ["AM.*", "Q.AAPL"])
def test_a_channel_never_acknowledged_is_refused(make, unacked):
    stream, stop, _, store, _ = make([Server([[AUTH]], unacked=[unacked])])
    stream.add("AAPL")
    stream.run(stop)
    assert stream.acks[unacked][0][1] is None
    complete(store, "AAPL")
    bars_cause = stream.bars_refusal(ALL)
    tick_cause = stream.tick_refusal(stream.tick_snapshot(ALL), "AAPL")
    if unacked == "AM.*":
        assert bars_cause == "stream_not_acknowledged" and tick_cause is None
    else:
        assert tick_cause == "stream_not_acknowledged" and bars_cause is None
        assert "AAPL" not in store.subscribed


def test_an_acknowledgment_arriving_after_the_cutoff_does_not_count(make):
    stream, stop, _, _, _ = make([Server([[AUTH]])])
    stream.run(stop)
    acked = stream.acks["AM.*"][0][1]
    assert stream.bars_refusal(acked - 1) == "stream_not_acknowledged"
    assert stream.bars_refusal(acked) is None


def test_a_slow_tick_consumer_never_delays_the_bars_and_is_visible(make, monkeypatch):
    release = threading.Event()
    server = Server([[AUTH], [trade()], [bar()]])
    stream, stop, bars, store, _ = make([server])
    real_add = store.add

    def slow_add(*args, **kwargs):
        assert release.wait(WAIT)
        return real_add(*args, **kwargs)

    monkeypatch.setattr(store, "add", slow_add)
    stream.add("AAPL")
    seen = {}

    def at_end():
        until(lambda: len(bars.rows) == 1)  # the bar went through while the trade waits
        seen["T"] = stream.stats()["T"]
        seen["bars"] = stream.bars_refusal(ALL)
        with pytest.raises(ModelRefused) as exc:
            stream.tick_snapshot(ALL)
        seen["ticks"] = exc.value.cause
        release.set()
        until(lambda: stream.lanes["T"].caught_up(ALL))

    server.at_end = at_end
    stream.run(stop)
    assert seen["T"]["depth"] == 1 and seen["T"]["oldest_waiting_ns"] is not None
    assert seen["bars"] is None and seen["ticks"] == "stream_backlog"
    assert stream.tick_snapshot(ALL).rows["trades"]  # handed once the consumer caught up


def test_a_failed_consumer_refuses_its_own_channel_only(make):
    bad = {"ev": "T", "sym": "AAPL"}  # no price, size, time or sequence
    stream, stop, _, store, _ = make([Server([[AUTH], [bad], [bar()]])])
    stream.add("AAPL")
    stream.run(stop)
    with pytest.raises(ModelRefused) as exc:
        stream.tick_snapshot(ALL)
    assert exc.value.cause == "stream_consumer_failed"
    assert stream.stats()["T"]["failed"] == "tick_unreadable"
    assert stream.bars_refusal(ALL) is None


def test_a_drop_gaps_bars_and_ticks_at_once_and_the_reconnect_resubscribes(make):
    open_state = {}
    holder = {}

    def on_connect(n):
        if n == 1:  # between the drop and the second connection
            s = holder["stream"]
            complete(s.ticks, "AAPL", upto=s.acks["Q.AAPL"][0][1])  # the initial backfill
            snap = s.tick_snapshot(ALL)
            open_state.update(bars=s.bars_refusal(ALL), ticks=s.tick_refusal(snap, "AAPL"),
                              open_bars=s.bars.open_gap, open_ticks=snap.gaps["AAPL"])

    first = Server([[AUTH], [bar(), trade()]], drop=True)
    second = Server([[AUTH], [trade(q=101)]])
    stream, stop, bars, store, made = make([first, second], on_connect=on_connect)
    holder["stream"] = stream
    stream.add("AAPL")
    stream.run(stop)
    assert open_state["bars"] == "bars_gap" and open_state["ticks"] == "tick_gap"
    down, cause = open_state["open_bars"]
    assert cause == "ConnectionError"
    assert open_state["open_ticks"] == ((down, None, "ConnectionError"),)
    # the new connection authenticates and sends every subscription again
    assert second.sent == [{"action": "auth", "params": KEY},
                           {"action": "subscribe", "params": "AM.*,T.AAPL,Q.AAPL"}]
    for param in ("AM.*", "T.AAPL", "Q.AAPL"):
        old, new = stream.acks[param]
        assert old[2] == down and old[3] == 1 and new[3] == 2 and new[1] is not None
    am_up = stream.acks["AM.*"][1][1]
    tick_up = stream.acks["Q.AAPL"][1][1]
    assert bars.gaps == [(down, am_up, "ConnectionError")] and bars.open_gap is None
    assert store.gaps["AAPL"] == [[down, tick_up, "ConnectionError"]]
    assert stream.take_backfill() == [("AM", "*", down, am_up),
                                      ("ticks", "AAPL", down, tick_up)]
    assert stream.take_backfill() == []
    # still refused until a backfill fills the tick gap; the bar gap has no backfill
    assert stream.bars_refusal(ALL) == "bars_gap"
    assert stream.tick_refusal(stream.tick_snapshot(ALL), "AAPL") == "tick_gap"
    for kind in ticks.KINDS:
        store.window(ticks.Window("AAPL", kind, down, tick_up, 0, 1, None))
    assert stream.tick_refusal(stream.tick_snapshot(ALL), "AAPL") is None
    assert len(store.versions["trades"]) == 2


def test_the_reconnect_waits_the_documented_release_pause(make, monkeypatch):
    waits = []
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: waits.append(t) or True)
    cfg = dataclasses.replace(CFG0, massive=dataclasses.replace(
        CFG0.massive, stream_release_pause_seconds=CFG.massive.stream_release_pause_seconds))
    stream, stop, _, _, _ = make([Server([[AUTH]], drop=True), Server([[AUTH]])], cfg)
    stream.run(stop)
    assert [w for w in waits if w is not None] == [float(CFG.massive.stream_release_pause_seconds)]


def test_the_full_market_path_shares_the_connection(make):
    cfg = dataclasses.replace(CFG0, massive=dataclasses.replace(
        CFG0.massive, tick_subscription="market"))
    server = Server([[AUTH], [trade(sym="ZZZ")]], drop=True)
    stream, stop, _, store, _ = make([server, Server([[AUTH]])], cfg)
    stream.run(stop)
    assert server.sent[1] == {"action": "subscribe", "params": "AM.*,T.*,Q.*"}
    assert "*" in store.subscribed and [g[2] for g in store.gaps["*"]] == ["ConnectionError"]
    assert ("ticks", "*") in [r[:2] for r in stream.take_backfill()]


@pytest.mark.parametrize("status", ["auth_failed", "max_connections", "error"])
def test_a_stream_level_refusal_stops_the_stream(make, status):
    stream, stop, _, _, _ = make([Server([[{"ev": "status", "status": status}]])])
    with pytest.raises(ModelRefused) as exc:
        stream.run(stop)
    assert exc.value.cause == "stream_refused" and exc.value.detail == status


def test_events_before_login_are_counted_not_kept(make):
    stream, stop, bars, _, _ = make([Server([[bar()], [AUTH]])])
    stream.run(stop)
    assert not bars.rows and stream.counts["stale_or_early_events"] == 1

