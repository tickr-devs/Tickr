from __future__ import annotations

from collections import Counter
from decimal import Decimal as D
from pathlib import Path

import pytest

from aperture import config
from aperture.broker.fake import FakeBroker
from aperture.config import GateResult
from aperture.engine.core import Engine, EntryRefused, Fence, Leg, SendState, TradeState
from aperture.store import Store
from aperture.types import OrderState, OrderType

ROOT = Path(__file__).resolve().parents[2]
CFG = config.load(ROOT / "config" / "bot.toml")
OPEN = GateResult(True, "test")


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
    yield s
    s.close()


def make(store, broker, gate=OPEN) -> Engine:
    run = store.start_run(environment="test", config_digest="d", strategy="s",
                          strategy_version="1", code_version="x")
    return Engine(store, broker, CFG, run, lambda: gate)


def enter(engine, qty="2") -> str:
    return engine.open_trade("ABC", D(qty), D("10.00"), D("9.80"), D("1"))


def legs(engine, trade_id, leg):
    return [o for o in engine.orders(trade_id) if o.leg is leg]


def assert_single_sends(broker):
    counts = Counter(r.client_order_id for r in broker.sent)
    assert max(counts.values()) == 1, counts


def test_happy_path(store):
    b = FakeBroker()
    e = make(store, b)
    t = enter(e)
    (entry,) = legs(e, t, Leg.ENTRY)
    assert entry.send_state is SendState.ACKNOWLEDGED
    e.step()
    assert e.trade(t).state is TradeState.OPENING
    assert legs(e, t, Leg.STOP) == []

    b.fill(entry.client_order_id, D(2), D("9.99"))
    e.step()  # observes fill, places stop for 2
    (stop,) = legs(e, t, Leg.STOP)
    assert stop.quantity == D(2) and stop.order_type is OrderType.STOP_LOSS
    assert stop.stop_price == D("9.80")
    assert any(n.startswith(Fence.UNPROTECTED) for n in e.fences())
    e.step()  # stop seen working at the broker
    assert not any(n.startswith(Fence.UNPROTECTED) for n in e.fences())
    assert e.trade(t).state is TradeState.OPEN

    e.request_exit(t, "operator-flatten")
    e.step()  # cancels the stop
    assert legs(e, t, Leg.STOP)[0].cancel_state == "sent"
    assert legs(e, t, Leg.EXIT) == []
    e.step()  # stop CANCELLED confirmed -> market exit for held quantity
    (exit_,) = legs(e, t, Leg.EXIT)
    assert exit_.quantity == D(2) and exit_.order_type is OrderType.MARKET
    b.fill(exit_.client_order_id, D(2), D("10.10"))
    e.step()
    e.step()
    trade = e.trade(t)
    assert trade.state is TradeState.CLOSED
    assert e.fences() == {}
    assert b.positions() == []
    assert_single_sends(b)
    kinds = [r.kind for r in store.journal()]
    assert kinds.index("order-intent") < kinds.index("order-attempted")
    assert kinds.index("order-attempted") < kinds.index("order-acknowledged")


def test_partial_fills_are_each_protected(store):
    b = FakeBroker()
    e = make(store, b)
    t = enter(e, "3")
    cid = legs(e, t, Leg.ENTRY)[0].client_order_id
    b.fill(cid, D(1), D(10))
    e.step()
    assert [s.quantity for s in legs(e, t, Leg.STOP)] == [D(1)]
    b.fill(cid, D(2), D(10))
    e.step()
    assert [s.quantity for s in legs(e, t, Leg.STOP)] == [D(1), D(2)]
    e.step()
    assert not any(n.startswith(Fence.UNPROTECTED) for n in e.fences())


def test_stop_fill_closes_trade(store):
    b = FakeBroker()
    e = make(store, b)
    t = enter(e, "1")
    b.fill(legs(e, t, Leg.ENTRY)[0].client_order_id, D(1), D(10))
    e.step()
    b.fill(legs(e, t, Leg.STOP)[0].client_order_id, D(1), D("9.79"))
    e.step()
    e.step()
    assert e.trade(t).state is TradeState.CLOSED
    assert store.query("SELECT close_cause FROM trades")[0][0] == "stopped-out"


def test_stop_refused_then_exit(store):
    b = FakeBroker()
    e = make(store, b)
    t = enter(e, "1")
    b.fill(legs(e, t, Leg.ENTRY)[0].client_order_id, D(1), D(10))
    b.script(*["refuse"] * (CFG.orders.stop_retry_attempts + 1))
    for _ in range(CFG.orders.stop_retry_attempts + 1):
        e.step()
    assert len(legs(e, t, Leg.STOP)) == CFG.orders.stop_retry_attempts + 1
    e.step()  # retries exhausted -> exit requested, entries fenced
    assert any(n.startswith(Fence.STOP_REJECTED) for n in e.fences())
    e.step()
    (exit_,) = legs(e, t, Leg.EXIT)
    b.fill(exit_.client_order_id, D(1), D(10))
    e.step()
    e.step()
    assert e.trade(t).state is TradeState.CLOSED
    with pytest.raises(EntryRefused, match="stop-rejected"):
        enter(e)
    (name,) = e.fences()
    assert e.clear_operator_fence(name)
    enter(e)


def test_unknown_outcome_resolved_by_detail_never_resent(store):
    b = FakeBroker()
    b.script("unknown")  # reached the broker, reply lost
    e = make(store, b)
    t = enter(e)
    entry = legs(e, t, Leg.ENTRY)[0]
    assert entry.send_state is SendState.UNKNOWN
    assert any(n.startswith(Fence.UNKNOWN_ORDER) for n in e.fences())
    e.step()
    assert legs(e, t, Leg.ENTRY)[0].send_state is SendState.ACKNOWLEDGED
    assert e.fences() == {}
    assert_single_sends(b)


def test_unknown_outcome_absent_after_reads(store):
    b = FakeBroker()
    b.script("unknown-lost")
    e = make(store, b)
    t = enter(e)
    for _ in range(CFG.orders.unknown_absent_reads):
        e.step()
    assert legs(e, t, Leg.ENTRY)[0].send_state is SendState.ABSENT
    e.step()
    assert e.trade(t).state is TradeState.CLOSED
    assert e.fences() == {}
    assert len(b.sent) == 1


def test_refused_entry_closes_trade(store):
    b = FakeBroker()
    b.script("refuse")
    e = make(store, b)
    t = enter(e)
    e.step()
    assert e.trade(t).state is TradeState.CLOSED


class Crash(BaseException):
    pass


class CrashAfterSend(FakeBroker):
    """The request reaches the broker, then the process dies before recording the outcome."""

    def place(self, request):
        super().place(request)
        raise Crash()


def test_crash_during_send_recovers_without_resend(store):
    b = CrashAfterSend()
    e = make(store, b)
    with pytest.raises(Crash):
        enter(e)
    assert e.orders()[0].send_state is SendState.ATTEMPTED

    healthy = FakeBroker(orders=b.orders, holdings=b.holdings, sent=b.sent)
    restarted = Engine(store, healthy, CFG, "run2", lambda: OPEN)
    restarted.recover()
    assert restarted.orders()[0].send_state is SendState.ACKNOWLEDGED
    assert restarted.fences() == {}
    assert len(healthy.sent) == 1
    healthy.fill(restarted.orders()[0].client_order_id, D(2), D(10))
    restarted.step()
    assert legs(restarted, restarted.trades()[0].trade_id, Leg.STOP)


def test_intent_without_attempt_is_never_sent(store):
    b = FakeBroker()
    e = make(store, b)
    with store.transaction():
        store.execute(
            "INSERT INTO trades(trade_id, run_id, symbol, state, entry_quantity, "
            "entry_limit_price, stop_price, planned_loss_usd, opened_at) "
            "VALUES ('t9', 'r', 'ABC', 'opening', '1', '10', '9', '1', 'now')")
        e._write_intent("t9", Leg.ENTRY, "ABC", __import__("aperture.types").types.Side.BUY,
                        OrderType.LIMIT, D(1), limit_price=D(10))
    e.recover()
    assert e.orders()[0].send_state is SendState.ABSENT
    assert b.sent == []
    assert e.trade("t9").state is TradeState.CLOSED


def test_foreign_position_and_order_fence_entries(store):
    b = FakeBroker()
    b.add_foreign_position("XYZ", D(5), D(3))
    from aperture.types import OrderRequest, Side, TimeInForce, TradingSession
    b.place(OrderRequest("manual-1", "XYZ", Side.SELL, OrderType.LIMIT, D(5), TimeInForce.DAY,
                         TradingSession.CORE, limit_price=D(4)))
    e = make(store, b)
    e.recover()
    names = set(e.fences())
    assert f"{Fence.FOREIGN_POSITION}:XYZ" in names
    assert f"{Fence.FOREIGN_ORDER}:manual-1" in names
    assert f"{Fence.STARTUP_RECONCILE}" not in names
    with pytest.raises(EntryRefused):
        enter(e)
    b.cancel("manual-1")
    del b.holdings["XYZ"]
    e.reconcile()
    assert e.fences() == {}


def test_gate_closed_refuses_entry(store):
    e = make(store, FakeBroker(), gate=GateResult(False, "disabled"))
    with pytest.raises(EntryRefused, match="gate"):
        enter(e)


def test_exit_waits_when_broker_quantity_disagrees(store):
    b = FakeBroker()
    e = make(store, b)
    t = enter(e, "2")
    b.fill(legs(e, t, Leg.ENTRY)[0].client_order_id, D(2), D(10))
    e.step()
    e.step()
    e.request_exit(t, "test")
    b.holdings["ABC"] = type(b.holdings["ABC"])("ABC", D(1), D(10))  # someone sold 1
    e.step()
    e.step()
    e.step()
    assert legs(e, t, Leg.EXIT) == []
    assert any(n.startswith(Fence.QUANTITY_MISMATCH) for n in e.fences())


def test_cancel_unknown_keeps_stop_and_sends_nothing(store):
    class CancelLost(FakeBroker):
        def cancel(self, client_order_id):
            from aperture.broker.base import OutcomeUnknown
            raise OutcomeUnknown("lost")

    b = CancelLost()
    e = make(store, b)
    t = enter(e, "1")
    b.fill(legs(e, t, Leg.ENTRY)[0].client_order_id, D(1), D(10))
    e.step()
    e.step()
    e.request_exit(t, "test")
    e.step()
    e.step()
    e.step()
    stop = legs(e, t, Leg.STOP)[0]
    assert stop.cancel_state == "unknown"
    assert b.order_detail(stop.client_order_id).state is OrderState.ACCEPTED
    assert legs(e, t, Leg.EXIT) == []
    assert any(n.startswith(Fence.CANCEL_UNKNOWN) for n in e.fences())
    assert_single_sends(b)
