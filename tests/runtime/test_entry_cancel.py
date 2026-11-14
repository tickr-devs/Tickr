"""A working entry is cancelled when the quote leaves the window, goes stale, or the last-entry
time passes (spec 9); filled shares stay protected and a cancel is never resent blind."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from aperture import config
from aperture.broker.base import OutcomeUnknown, Refused
from aperture.broker.fake import FakeBroker
from aperture.calendar import NEW_YORK, SessionCalendar
from aperture.config import GateResult
from aperture.data.base import FakeMarketData
from aperture.data.health import StreamHealth, StreamState
from aperture.engine.core import Engine, Fence, Leg, TradeState
from aperture.runtime.entry_cancel import EntryCancel, cancel_cause, cancel_stale_entries
from aperture.runtime.market import MarketRuntime, StreamInbox
from aperture.store import Store
from aperture.types import OrderState, OrderType, Quote

ROOT = Path(__file__).resolve().parents[2]
CFG = config.load(ROOT / "config" / "bot.toml")
CALENDAR = SessionCalendar.load((ROOT / CFG.session.calendar_file).parent)
MONDAY_11 = datetime(2026, 11, 16, 11, 0, tzinfo=NEW_YORK)
LAST_ENTRY = CALENDAR.day_at(MONDAY_11).session_times(
    CFG.session.last_entry_minutes_before_close,
    CFG.session.flatten_minutes_before_close).last_entry
POLL = float(CFG.orders.detail_poll_seconds)
SPACING = float(CFG.orders.cancel_retry_spacing_seconds)
MAX_AGE = float(CFG.risk.max_quote_age_seconds)
LIMIT, LOW, STOP = D("10.10"), D("10.00"), D("9.75")


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


class Rig:
    """Engine, real MarketRuntime on fake market data, and the loop order of one pass:
    engine step, market tick, then the entry cancel check."""

    def __init__(self, tmp_path: Path, broker: FakeBroker | None = None,
                 now: datetime = MONDAY_11) -> None:
        self.broker = broker or FakeBroker()
        self.store = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
        self.clock = Clock()
        self.engine = Engine(self.store, self.broker, CFG, "ecan0001",
                             lambda: GateResult(True, "t"), clock=self.clock)
        self.now = now
        self.source = FakeMarketData()
        self.market = MarketRuntime(self.engine, CFG, self.source,
                                    health=StreamHealth.from_config(CFG), inbox=StreamInbox(),
                                    capture=None, calendar=CALENDAR, now=lambda: self.now,
                                    monotonic=lambda: self.clock.now)
        self.market.bind()
        self.source.transport(True, self.now, "up")

    def open(self, qty: str = "3") -> str:
        t = self.engine.open_trade("ABC", D(qty), LIMIT, STOP, D(1), entry_low=LOW)
        self.market.tick()  # the working entry's symbol is subscribed
        self.push()
        return t

    def push(self, ask: str = "10.05", age: float = 0) -> None:
        a = D(ask)
        self.source.push_quote(Quote("ABC", a - D("0.01"), a, D(1), D(1),
                                     self.now - timedelta(seconds=age), self.now))

    def step(self, ask: str | None = "10.05", passes: int = 1) -> int:
        """One loop pass per count: time moves one poll interval, a fresh quote arrives
        (unless `ask` is None), then engine step, market tick, cancel check."""
        decided = 0
        for _ in range(passes):
            self.clock.now += POLL
            self.now += timedelta(seconds=POLL)
            if ask is not None:
                self.push(ask)
            self.engine.step()
            self.market.tick()
            decided += cancel_stale_entries(self.engine, CFG, CALENDAR, self.now,
                                            quote=self.market.quote,
                                            stream=self.market.stream_state)
        return decided

    def entry(self, t: str):
        return next(o for o in self.engine.orders(t) if o.leg is Leg.ENTRY)

    def causes(self) -> list[str]:
        return [r.body["cause"] for r in self.store.journal("entry-cancel-decided")]

    def working_stops(self) -> D:
        return sum((o.quantity - o.filled_quantity for o in self.broker.open_orders()
                    if o.order_type == OrderType.STOP_LOSS), D(0))


def test_entry_rests_while_the_quote_is_fresh_and_inside_the_window(tmp_path):
    rig = Rig(tmp_path)
    t = rig.open()
    assert rig.step(passes=10) == 0
    assert rig.entry(t).live and rig.entry(t).cancel_state is None
    assert rig.source.subscribed == {"ABC"}  # the working entry keeps its quote coming


@pytest.mark.parametrize("ask", ["10.11", "9.99"])
def test_quote_leaving_the_window_cancels_the_entry(tmp_path, ask):
    rig = Rig(tmp_path)
    t = rig.open()
    rig.step()
    assert rig.step(ask=ask) == 1
    assert rig.causes() == [EntryCancel.WINDOW_LEFT]
    assert rig.broker.orders[rig.entry(t).client_order_id].state is OrderState.CANCELLED
    rig.step(passes=3)  # the quote coming back never re-prices or re-sends anything
    assert rig.engine.trade(t).state is TradeState.CLOSED
    assert [r.order_type for r in rig.broker.sent] == [OrderType.LIMIT]


def test_stale_quote_cancels_the_entry(tmp_path):
    rig = Rig(tmp_path)
    t = rig.open()
    passes = int(MAX_AGE // POLL) + 1  # no new quote for longer than max_quote_age_seconds
    assert rig.step(ask=None, passes=passes) == 1
    assert rig.causes() == [EntryCancel.QUOTE_STALE]
    assert rig.entry(t).cancel_state == "sent"


def test_lost_stream_cancels_the_entry_at_once(tmp_path):
    rig = Rig(tmp_path)
    t = rig.open()
    rig.source.transport(False, rig.now, "down")
    rig.step()
    assert rig.causes() == [EntryCancel.QUOTE_STALE]
    assert rig.entry(t).cancel_state == "sent"


def test_last_entry_time_cancels_the_entry(tmp_path):
    rig = Rig(tmp_path, now=LAST_ENTRY - timedelta(seconds=POLL * 2))
    t = rig.open()
    rig.step()
    assert rig.causes() == []
    rig.step(passes=2)
    assert rig.causes() == [EntryCancel.LAST_ENTRY]
    assert rig.entry(t).cancel_state == "sent"


def test_partial_fill_then_window_left_cancels_the_remainder_and_keeps_the_stop(tmp_path):
    rig = Rig(tmp_path)
    t = rig.open("3")
    rig.broker.fill(rig.entry(t).client_order_id, D(1), D("10.05"))
    rig.step(passes=3)
    assert rig.working_stops() == D(1)
    rig.step(ask="10.30")
    assert rig.causes() == [EntryCancel.WINDOW_LEFT]
    rig.step(passes=3)
    entry = rig.broker.orders[rig.entry(t).client_order_id]
    assert (entry.state, entry.filled_quantity) == (OrderState.CANCELLED, D(1))
    assert rig.engine.trade(t).state is TradeState.OPEN
    assert rig.working_stops() == D(1)
    assert not any(n.startswith(Fence.UNPROTECTED) for n in rig.engine.fences())
    assert rig.engine.trade(t).exit_requested is None  # the position itself is not closed


@pytest.mark.parametrize("raises", [False, True])
def test_cancel_racing_a_fill_ends_protected(tmp_path, raises):
    class FillsFirst(FakeBroker):
        calls = 0

        def cancel(self, cid):
            type(self).calls += 1
            order = self.orders[cid]
            self.fill(cid, order.quantity - order.filled_quantity, D("10.05"))
            if raises:
                raise Refused("TERMINAL", "order already filled")

    rig = Rig(tmp_path, broker=FillsFirst())
    t = rig.open("3")
    rig.step(ask="10.30")
    assert rig.causes() == [EntryCancel.WINDOW_LEFT]
    rig.step(passes=4)
    assert FillsFirst.calls == 1
    assert rig.engine.trade(t).state is TradeState.OPEN
    assert rig.working_stops() == D(3)
    assert not any(n.startswith((Fence.UNPROTECTED, Fence.CANCEL_UNKNOWN))
                   for n in rig.engine.fences())


@pytest.mark.parametrize("took", [True, False])
def test_unknown_cancel_outcome_is_not_resent(tmp_path, took):
    calls = []

    class Lost(FakeBroker):
        def cancel(self, cid):
            calls.append(cid)
            if took:
                FakeBroker.cancel(self, cid)
            raise OutcomeUnknown("reply lost")

    rig = Rig(tmp_path, broker=Lost())
    t = rig.open()
    rig.step(ask="10.30")
    assert rig.entry(t).cancel_state == "unknown"
    assert any(n.startswith(Fence.CANCEL_UNKNOWN) for n in rig.engine.fences())
    if took:
        rig.step(ask="10.30", passes=10)
        assert len(calls) == 1
        assert rig.engine.trade(t).state is TradeState.CLOSED
    else:
        # Until a Detail read well after the cancel still shows the order working, nothing
        # is sent again, however many passes see the quote outside the window.
        rig.step(ask="10.30", passes=int(SPACING // POLL) - 1)
        assert len(calls) == 1
        # Then the engine asks again on its own (a new, recorded decision), bounded by
        # cancel_retry_attempts; the runtime never decides twice.
        rig.step(ask="10.30")  # this read is a full spacing after the cancel
        assert len(calls) == 2
        assert rig.causes() == [EntryCancel.WINDOW_LEFT]


def test_decision_survives_a_restart_and_entry_low_is_kept(tmp_path):
    rig = Rig(tmp_path)
    t = rig.open()
    assert rig.engine.trade(t).entry_low == LOW
    rig.engine.cancel_entry(t, EntryCancel.QUOTE_STALE, "test")
    again = Engine(rig.store, rig.broker, CFG, "ecan0002", lambda: GateResult(True, "t"),
                   clock=rig.clock)
    assert again.trade(t).entry_cancel == EntryCancel.QUOTE_STALE
    assert not again.cancel_entry(t, EntryCancel.WINDOW_LEFT, "second")  # one cause, kept


@pytest.mark.parametrize("quote_age, stream, ask, expected", [
    (0, StreamState.FRESH, "10.05", None),
    (0, StreamState.FRESH, "10.10", None),  # the limit itself is inside the window
    (0, StreamState.FRESH, "10.00", None),
    (0, StreamState.QUIET, "10.05", EntryCancel.QUOTE_STALE),
    (0, None, "10.05", EntryCancel.QUOTE_STALE),  # not subscribed
    (MAX_AGE + 1, StreamState.FRESH, "10.05", EntryCancel.QUOTE_STALE),
    (0, StreamState.FRESH, "10.11", EntryCancel.WINDOW_LEFT),
])
def test_cancel_cause(tmp_path, quote_age, stream, ask, expected):
    rig = Rig(tmp_path)
    trade = rig.engine.trade(rig.open())
    a = D(ask)
    q = Quote("ABC", a - D("0.01"), a, D(1), D(1),
              rig.now - timedelta(seconds=quote_age), rig.now)
    found = cancel_cause(trade, quote=q, stream=stream, calendar=CALENDAR, now=rig.now,
                         config=CFG)
    assert (found and found[0]) == expected


def test_no_window_no_calendar_or_no_quote_is_a_cause(tmp_path):
    rig = Rig(tmp_path)
    t = rig.engine.open_trade("ABC", D(1), LIMIT, STOP, D(1))  # no window recorded
    trade = rig.engine.trade(t)
    q = Quote("ABC", D("10.04"), D("10.05"), D(1), D(1), rig.now, rig.now)
    kw = dict(stream=StreamState.FRESH, now=rig.now, config=CFG)
    assert cancel_cause(trade, quote=q, calendar=CALENDAR, **kw)[0] == EntryCancel.WINDOW_LEFT
    assert cancel_cause(trade, quote=q, calendar=None, **kw)[0] == EntryCancel.LAST_ENTRY
    assert cancel_cause(trade, quote=None, calendar=CALENDAR, **kw)[0] == EntryCancel.QUOTE_STALE


@pytest.mark.parametrize("outcome", ["unknown", "unknown-lost"])
def test_entry_with_an_unknown_send_is_cancelled_only_once_it_is_known_to_exist(tmp_path,
                                                                               outcome):
    calls = []

    class Counting(FakeBroker):
        def cancel(self, cid):
            calls.append(cid)
            FakeBroker.cancel(self, cid)

    rig = Rig(tmp_path, broker=Counting())
    rig.broker.script(outcome)
    t = rig.open()
    rig.engine.cancel_entry(t, EntryCancel.QUOTE_STALE, "test")
    assert calls == []  # never cancel an order that may not exist
    rig.step(passes=CFG.orders.unknown_absent_reads + 2)
    assert len(calls) == (1 if outcome == "unknown" else 0)
    assert rig.engine.trade(t).state is TradeState.CLOSED
    assert not any(n.startswith(Fence.CANCEL_UNKNOWN) for n in rig.engine.fences())
