"""Massive trades and quotes: stream and REST backfill into flat-file rows; no network
(a stand-in socket and a stand-in HTTP opener)."""

from __future__ import annotations

import dataclasses
import json
import threading
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from tickr import config
from tickr.model import ticks
from tickr.model.errors import ModelRefused

ROOT = Path(__file__).resolve().parents[2]
CFG = config.load(ROOT / "config" / "bot.toml")
KEY = "api-canary-1"
DAY = "2026-11-18"
START, END = ticks.day_bounds_ns(DAY)
MS = 10**6
T0 = START + (9 * 60 + 30) * 60 * 10**9  # 09:30 New York


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(CFG.massive.api_key_variable, KEY)


def with_mode(mode: str) -> config.Config:
    return dataclasses.replace(
        CFG, massive=dataclasses.replace(CFG.massive, tick_subscription=mode))


def trade(sym="AAPL", q=100, t=T0 // MS, **extra):
    msg = {"ev": "T", "sym": sym, "x": 4, "i": "7", "z": 3, "p": 114.125, "s": 100,
           "c": [12, 37], "t": t, "pt": t - 1, "q": q}
    msg.update(extra)
    return msg


def quote(sym="AAPL", q=200, t=T0 // MS, **extra):
    msg = {"ev": "Q", "sym": sym, "bx": 4, "bp": 114.1, "bs": 100, "ax": 7, "ap": 114.2,
           "as": 160, "c": 1, "i": [604], "t": t, "pt": t - 1, "q": q, "z": 3}
    msg.update(extra)
    return msg


AUTH = {"ev": "status", "status": "auth_success"}


class FakeSocket:
    def __init__(self, batches, drop=False):
        self.incoming = [json.dumps(b) for b in batches]
        self.sent = []
        self.drop = drop
        self.stop = None
        self.on_empty = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout):
        if self.incoming:
            return self.incoming.pop(0)
        if self.on_empty is not None:
            hook, self.on_empty = self.on_empty, None
            hook()
            raise TimeoutError
        if self.drop:
            raise ConnectionError("closed")
        self.stop.set()
        raise TimeoutError


def make_stream(sockets, cfg=CFG):
    stop = threading.Event()
    store = ticks.TickStore(DAY)
    made = []
    times = iter(range(1, 10**6))

    def connect(url, max_size):
        assert url == f"wss://{cfg.massive.stream_host}{cfg.massive.stream_path}"
        assert max_size == cfg.massive.stream_max_message_bytes
        sock = sockets.pop(0)
        sock.stop = stop
        made.append(sock)
        return sock

    stream = ticks.TickStream(cfg, store, connect, clock=lambda: next(times))
    return stream, stop, store, made


def test_stream_messages_become_flat_file_rows_with_receipt_times(creds):
    sock = FakeSocket([[AUTH], [trade(trfi=202, q=101), quote()], trade(q=102)])
    stream, stop, store, made = make_stream([sock])
    stream.add("AAPL")
    stream.session(stop)
    assert made[0].sent == [{"action": "auth", "params": KEY},
                            {"action": "subscribe", "params": "T.AAPL,Q.AAPL"}]
    t = store.frame("trades")
    assert list(t.columns) == list(ticks.TRADE_COLUMNS) + ["recv_ns", "source"]
    first = t.iloc[0].to_dict()
    assert first == {"ticker": "AAPL", "conditions": "12,37", "correction": None,
                     "exchange": 4, "price": 114.125, "size": 100, "sip_timestamp": T0,
                     "sequence_number": 101, "trf_id": 202, "participant_timestamp": T0 - MS,
                     "recv_ns": 2, "source": "stream"}
    assert t.iloc[1]["trf_id"] is None and t.iloc[1]["recv_ns"] == 3  # absent, never filled
    assert type(t.iloc[0]["participant_timestamp"]) is int  # exact nanoseconds, no float
    q = store.frame("quotes").iloc[0].to_dict()
    assert q == {"ticker": "AAPL", "ask_price": 114.2, "ask_size": 160, "bid_price": 114.1,
                 "bid_size": 100, "sip_timestamp": T0, "sequence_number": 200,
                 "conditions": "1", "indicators": "604", "recv_ns": 2, "source": "stream"}


def test_an_empty_quote_side_stays_empty():
    row = ticks.stream_row("quotes", {k: v for k, v in quote().items()
                                      if k not in ("ap", "as", "ax", "i")})
    assert row["ask_price"] is None and row["ask_size"] is None and row["indicators"] is None


@pytest.mark.parametrize("kind,msg,key", [("trades", trade(), "q"), ("trades", trade(), "p"),
                                          ("trades", trade(), "t"), ("quotes", quote(), "q"),
                                          ("quotes", quote(), "sym")])
def test_a_missing_required_field_is_refused(kind, msg, key):
    msg = {k: v for k, v in msg.items() if k != key}
    with pytest.raises(ModelRefused) as exc:
        ticks.stream_row(kind, msg)
    assert exc.value.cause == "tick_unreadable" and key in exc.value.detail


def test_a_wrongly_typed_field_is_refused():
    with pytest.raises(ModelRefused):
        ticks.stream_row("trades", trade(p="114.1"))
    with pytest.raises(ModelRefused):
        ticks.stream_row("quotes", quote(i=["604"]))


def test_symbols_joining_during_the_day_are_subscribed_on_the_open_connection(creds):
    sock = FakeSocket([[AUTH]])
    stream, stop, store, _ = make_stream([sock])
    assert stream.add("AAPL") is True
    sock.on_empty = lambda: (stream.add("MSFT"), stream.add("AAPL"))
    stream.session(stop)
    assert sock.sent[1:] == [{"action": "subscribe", "params": "T.AAPL,Q.AAPL"},
                             {"action": "subscribe", "params": "T.MSFT,Q.MSFT"}]
    assert stream.symbols == {"AAPL", "MSFT"}


def test_no_subscribe_before_login_and_ticks_before_login_are_refused(creds):
    stream, stop, _, made = make_stream([FakeSocket([[trade()]])])
    stream.add("AAPL")
    with pytest.raises(ModelRefused) as exc:
        stream.session(stop)
    assert exc.value.cause == "tick_unreadable"
    assert made[0].sent == [{"action": "auth", "params": KEY}]


def test_the_full_market_path_subscribes_every_symbol(creds):
    sock = FakeSocket([[AUTH], [trade(sym="ZZZ")]])
    stream, stop, store, _ = make_stream([sock], with_mode("market"))
    stream.session(stop)
    assert sock.sent[1] == {"action": "subscribe", "params": "T.*,Q.*"}
    stream.add("AAPL")  # nothing more to send: every symbol already arrives
    assert len(sock.sent) == 2 and list(store.frame("trades").ticker) == ["ZZZ"]


def test_a_refused_login_stops_the_stream(creds):
    stream, stop, _, _ = make_stream([FakeSocket([[{"ev": "status", "status": "auth_failed"}]])])
    with pytest.raises(ModelRefused) as exc:
        stream.run(stop)
    assert exc.value.cause == "stream_refused"


@pytest.mark.parametrize("mode,keys", [("symbols", ["AAPL", "MSFT"]), ("market", ["*"])])
def test_a_drop_is_a_gap_of_every_subscribed_symbol_and_the_next_login_resubscribes(
        creds, monkeypatch, mode, keys):
    monkeypatch.setattr(threading.Event, "wait", lambda self, t=None: False)
    first = FakeSocket([[AUTH], [trade()]], drop=True)
    second = FakeSocket([[AUTH], [trade(q=101)]])
    stream, stop, store, made = make_stream([first, second], with_mode(mode))
    stream.add("AAPL")
    stream.add("MSFT")
    stream.run(stop)
    assert len(made) == 2
    assert sorted(store.gaps) == keys
    down, up, cause = store.gaps[keys[0]][0]
    assert down < up and cause == "ConnectionError"
    assert second.sent[1] == first.sent[1]  # the same subscription again
    assert len(store.frame("trades")) == 2


def test_a_tick_of_another_day_is_counted_not_kept(creds):
    sock = FakeSocket([[AUTH], [trade(t=END // MS)]])
    stream, stop, store, _ = make_stream([sock])
    stream.add("AAPL")
    stream.session(stop)
    assert store.other_day == 1 and store.frame("trades").empty


# --- backfill --------------------------------------------------------------------------------


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class Wire:
    """Stand-in for urlopen: a list of answers per kind, popped in order."""

    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        kind = urllib.parse.urlsplit(request.full_url).path.split("/")[2]
        answer = self.answers[kind].pop(0)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "x", {}, None)
        return Response(json.dumps(answer).encode())


def rest_trade(seq, ts, **extra):
    row = {"conditions": [12, 37], "exchange": 4, "id": str(seq), "participant_timestamp": ts - 5,
           "price": 114.125, "sequence_number": seq, "sip_timestamp": ts, "size": 100,
           "tape": 3}
    row.update(extra)
    return row


def rest_quote(seq, ts, **extra):
    row = {"ask_exchange": 7, "ask_price": 114.2, "ask_size": 160, "bid_exchange": 4,
           "bid_price": 114.1, "bid_size": 100, "conditions": [1], "sequence_number": seq,
           "sip_timestamp": ts, "tape": 3}
    row.update(extra)
    return row


NEXT = f"https://{CFG.massive.api_host}/v3/trades/AAPL?cursor=abc"


def test_backfill_pages_both_kinds_and_dedupes_the_overlap_with_the_stream(creds):
    store = ticks.TickStore(DAY)
    # the stream already holds trade 101 (millisecond time) and quote 200
    store.add("trades", [ticks.stream_row("trades", trade(q=101))], 50, ticks.STREAM)
    store.add("quotes", [ticks.stream_row("quotes", quote(q=200))], 50, ticks.STREAM)
    wire = Wire({
        "trades": [{"status": "OK", "results": [rest_trade(100, T0 - 7, correction=1)],
                    "next_url": NEXT},
                   {"status": "OK", "results": [rest_trade(101, T0 + 123, trf_id=202)]}],
        "quotes": [{"status": "OK", "results": [rest_quote(199, T0 - 9),
                                                rest_quote(200, T0 + 456)]}]})
    times = iter([70, 80, 90])
    back = ticks.TickBackfill(CFG, store, wire, clock=lambda: next(times))
    assert back.backfill("AAPL", START, T0 + MS) == {"trades": 1, "quotes": 1}
    first = urllib.parse.urlsplit(wire.requests[0].full_url)
    assert first.path == "/v3/trades/AAPL"
    assert urllib.parse.parse_qs(first.query) == {
        "timestamp.gte": [str(START)], "timestamp.lt": [str(T0 + MS)], "order": ["asc"],
        "sort": ["timestamp"], "limit": [str(CFG.massive.tick_page_limit)]}
    assert wire.requests[1].full_url == NEXT
    assert all(r.get_header("Authorization") == "Bearer " + KEY for r in wire.requests)
    t = store.frame("trades")
    assert list(t.sequence_number) == [100, 101]  # one row per (ticker, sequence_number)
    kept = t.iloc[1].to_dict()
    # the REST copy wins (nanosecond time, trf id), the first receipt stays
    assert kept["sip_timestamp"] == T0 + 123 and kept["trf_id"] == 202
    assert kept["source"] == "rest" and kept["recv_ns"] == 50
    assert t.iloc[0]["correction"] == 1 and t.iloc[0]["recv_ns"] == 70
    q = store.frame("quotes")
    assert list(q.sequence_number) == [199, 200] and q.iloc[1]["recv_ns"] == 50
    assert store.repeats == 2 and store.conflicts == 0
    # the stream repeating a row the backfill holds keeps the REST copy
    store.add("trades", [ticks.stream_row("trades", trade(q=101))], 99, ticks.STREAM)
    assert store.frame("trades").iloc[1]["source"] == "rest" and store.repeats == 3


def test_a_repeat_with_other_content_is_counted_as_a_conflict(creds):
    store = ticks.TickStore(DAY)
    store.add("trades", [ticks.stream_row("trades", trade(q=101))], 50, ticks.STREAM)
    wire = Wire({"trades": [{"status": "OK", "results": [rest_trade(101, T0, size=99)]}],
                 "quotes": [{"status": "OK", "results": []}]})
    ticks.TickBackfill(CFG, store, wire, clock=lambda: 60).backfill("AAPL", START, END)
    assert store.conflicts == 1 and store.frame("trades").iloc[0]["size"] == 99


@pytest.mark.parametrize("answers,cause", [
    ({"trades": [500]}, "http_status"),
    ({"trades": [{"status": "ERROR", "results": []}]}, "backfill_unreadable"),
    ({"trades": [{"status": "OK", "results": [rest_trade(1, END)]}]}, "backfill_unreadable"),
    ({"trades": [{"status": "OK", "results": [{"sip_timestamp": T0, "sequence_number": 1}]}]},
     "tick_unreadable"),
    ({"trades": [{"status": "OK", "results": [],
                  "next_url": "https://elsewhere.example/v3/trades/AAPL"}]},
     "backfill_unreadable"),
    ({"trades": [{"status": "OK", "results": []}],
      "quotes": [{"status": "OK", "results": [rest_quote(1, T0)],
                  "next_url": NEXT.replace("trades", "quotes")}, 404]}, "not_found"),
])
def test_a_failed_backfill_refuses_once_and_keeps_nothing(creds, answers, cause):
    store = ticks.TickStore(DAY)
    wire = Wire(answers)
    with pytest.raises(ModelRefused) as exc:
        ticks.TickBackfill(CFG, store, wire, clock=lambda: 1).backfill("AAPL", START, END)
    assert exc.value.cause == cause
    assert store.frame("trades").empty and store.frame("quotes").empty
    assert all(not v for v in wire.answers.values())  # every request sent once, none retried


def test_too_many_pages_refuse_the_backfill(creds):
    cfg = dataclasses.replace(CFG, massive=dataclasses.replace(CFG.massive, tick_max_pages=2))
    page = {"status": "OK", "results": [], "next_url": NEXT}
    wire = Wire({"trades": [page, page]})
    with pytest.raises(ModelRefused) as exc:
        ticks.TickBackfill(cfg, ticks.TickStore(DAY), wire).backfill("AAPL", START, END)
    assert exc.value.cause == "backfill_incomplete"


@pytest.mark.parametrize("args", [("AAPL", END, START), ("", START, END),
                                  ("AAPL", True, END)])
def test_a_bad_range_is_refused_before_any_request(creds, args):
    wire = Wire({})
    with pytest.raises(ModelRefused) as exc:
        ticks.TickBackfill(CFG, ticks.TickStore(DAY), wire).backfill(*args)
    assert exc.value.cause == "backfill_range" and wire.requests == []


def test_the_day_bounds_are_new_york_midnights():
    start, end = ticks.day_bounds_ns("2026-03-08")  # the spring change: a 23-hour day
    assert (end - start) == 23 * 3600 * 10**9


def test_unknown_subscription_mode_is_refused_by_the_config(tmp_path):
    text = (ROOT / "config" / "bot.toml").read_text("utf-8").replace(
        'tick_subscription = "symbols"', 'tick_subscription = "some"')
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "bot.toml").write_text(text, "utf-8")
    with pytest.raises(config.ConfigError):
        config.load(tmp_path / "config" / "bot.toml")
