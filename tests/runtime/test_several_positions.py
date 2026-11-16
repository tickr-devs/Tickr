"""Several positions at once (backlog X1), built but off by default (`[risk]
max_open_positions = 1`): each open trade takes one slot, at most one trade per symbol, every
trade has its own broker stop sized on its own fills, and all trades share the daily loss
ceiling through their reservations."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from tickr import config
from tickr.broker.fake import FakeBroker
from tickr.config import GateResult
from tickr.engine.core import Engine, EntryRefused, Fence, Leg, TradeState
from tickr.observers.log import DecisionLog
from tickr.runtime.entries import EntryDesk, slots_in_use
from tickr.store import Store
from tickr.types import OrderType, Quote

from .test_entries import BAR, CALENDAR, CFG, NOW, ROOT, decision

pytestmark = pytest.mark.skipif(not config.trading_gate(CFG).open,
                                reason="the configured trading gate is closed")


def wide(slots: int, **risk: object) -> config.Config:
    return replace(CFG, risk=replace(CFG.risk, max_open_positions=slots, **risk))


def quote(symbol: str, ask: str = "10.05") -> Quote:
    return Quote(symbol, D("10.04"), D(ask), D(100), D(100), NOW - timedelta(seconds=1), NOW)


class Desk:
    def __init__(self, tmp_path, cfg: config.Config) -> None:
        self.broker = FakeBroker()
        self.store = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
        self.clock = [1_000_000.0]
        self.engine = Engine(self.store, self.broker, cfg, "desk0001",
                             lambda: GateResult(True, "t"), clock=lambda: self.clock[0])
        self.engine.recover()
        self.desk = EntryDesk(self.engine, cfg, calendar=CALENDAR,
                              last_broker_read_at=lambda: NOW,
                              log=DecisionLog(tmp_path / "decisions"))

    def enter(self, symbol: str, **overrides: object):
        return self.desk.on_decision(decision(symbol=symbol, **overrides), bar_start=BAR,
                                     quote=quote(symbol), now=NOW)

    def step(self, steps: int = 1) -> None:
        """Advance one Detail poll interval per step, like the loop does."""
        for _ in range(steps):
            self.clock[0] += float(CFG.orders.detail_poll_seconds)
            self.engine.step()

    def trade_of(self, symbol: str):
        return next(t for t in self.engine.trades() if t.symbol == symbol)

    def fill(self, symbol: str, quantity: D = D(10)) -> None:
        entry = self.engine.orders(self.trade_of(symbol).trade_id)[0]
        self.broker.fill(entry.client_order_id, quantity, D("10.05"))
        self.step(3)

    def stops(self, symbol: str):
        return [o for o in self.engine.orders(self.trade_of(symbol).trade_id)
                if o.leg is Leg.STOP]


def test_each_open_trade_takes_one_slot_until_the_limit(tmp_path):
    desk = Desk(tmp_path, wide(3))
    assert [desk.enter(s).admission for s in ("AAA", "BBB", "CCC")] == ["taken"] * 3
    record = desk.enter("DDD")
    assert record.admission == "refused"
    assert record.refusal_cause.startswith("POSITION_LIMIT_REACHED: 3 open position(s), limit 3")
    assert slots_in_use(desk.engine) == 3


def test_a_working_entry_takes_its_slot_and_blocks_only_its_own_symbol(tmp_path):
    desk = Desk(tmp_path, wide(3))
    assert desk.enter("AAA").admission == "taken"  # entry working, nothing filled
    again = desk.enter("AAA")
    assert again.refusal_cause.startswith("OWN_ORDER_WORKING")
    desk.fill("AAA")
    assert desk.enter("AAA").refusal_cause.startswith("OWN_POSITION_OPEN")
    assert desk.enter("BBB").admission == "taken"


def test_the_engine_refuses_a_second_trade_in_a_symbol_whatever_admission_says(tmp_path):
    desk = Desk(tmp_path, wide(3))
    desk.engine.open_trade("AAA", D(2), D("10.00"), D("9.80"), D(1))
    with pytest.raises(EntryRefused, match="a trade in AAA is already open"):
        desk.engine.open_trade("AAA", D(2), D("10.00"), D("9.80"), D(1))
    assert len(desk.engine.trades()) == 1


def test_every_position_has_its_own_stop_sized_on_its_own_fills(tmp_path):
    desk = Desk(tmp_path, wide(3))
    for symbol in ("AAA", "BBB"):
        desk.enter(symbol)
    desk.fill("AAA", D(4))
    desk.fill("BBB", D(10))
    for symbol, quantity in (("AAA", D(4)), ("BBB", D(10))):
        (stop,) = desk.stops(symbol)
        assert (stop.symbol, stop.order_type, stop.quantity) == (symbol, OrderType.STOP_LOSS,
                                                                 quantity)
        assert stop.verified
    assert not any(n.startswith(Fence.UNPROTECTED) for n in desk.engine.fences())


def test_a_stop_out_closes_only_its_trade_and_frees_its_slot(tmp_path):
    desk = Desk(tmp_path, wide(2))
    for symbol in ("AAA", "BBB"):
        desk.enter(symbol)
        desk.fill(symbol)
    assert desk.enter("CCC").refusal_cause.startswith("POSITION_LIMIT_REACHED")
    (stop,) = desk.stops("AAA")
    aaa = desk.trade_of("AAA").trade_id
    desk.broker.fill(stop.client_order_id, D(10), D("9.70"))
    desk.step(3)
    assert desk.engine.trade(aaa).state is TradeState.CLOSED
    (bbb,) = desk.engine.trades()
    assert bbb.symbol == "BBB" and bbb.state is TradeState.OPEN
    assert desk.stops("BBB")[0].live
    assert desk.engine.fences() == {}
    assert desk.enter("CCC").admission == "taken"


def test_reconcile_with_several_positions_agrees_and_flags_only_real_mismatches(tmp_path):
    desk = Desk(tmp_path, wide(3))
    for symbol in ("AAA", "BBB"):
        desk.enter(symbol)
        desk.fill(symbol)
    assert desk.engine.reconcile() and desk.engine.fences() == {}
    desk.broker.add_foreign_position("BBB", D(5), D(10))  # shares bought by hand in BBB
    assert desk.engine.reconcile()
    bbb = desk.trade_of("BBB").trade_id
    assert desk.engine.fences() == {f"{Fence.QUANTITY_MISMATCH}:{bbb}":
                                    "broker holds 15, Detail says 10"}
    # Hard rule 1: a conflict blocks every new entry; both stops stay on the Detail quantity.
    assert desk.enter("CCC").refusal_cause.startswith("UNRESOLVED_EXPOSURE")
    assert [(s.live, s.quantity) for sym in ("AAA", "BBB") for s in desk.stops(sym)] == [
        (True, D(10)), (True, D(10))]


def test_all_trades_share_the_daily_loss_ceiling(tmp_path):
    # 10 x (10.10 - 9.75) = 3.50 reserved per trade; a ceiling of 9 leaves room for two.
    desk = Desk(tmp_path, wide(5, max_daily_loss_usd=D(9), min_daily_room_usd=D(1)))
    assert [desk.enter(s).admission for s in ("AAA", "BBB")] == ["taken", "taken"]
    record = desk.enter("CCC")
    assert record.admission == "refused" and record.refusal_cause.startswith("DAILY_CEILING")


def test_shutdown_and_flatten_cover_every_trade(tmp_path):
    desk = Desk(tmp_path, wide(3))
    for symbol in ("AAA", "BBB"):
        desk.enter(symbol)
        desk.fill(symbol)
    for trade in desk.engine.trades():
        desk.engine.request_exit(trade.trade_id, "session-flatten")
    desk.step(10)  # one Detail read per step: the teardowns take turns
    exits = [o for o in desk.engine.orders() if o.leg is Leg.EXIT]
    assert sorted((o.symbol, o.quantity) for o in exits) == [("AAA", D(10)), ("BBB", D(10))]


@pytest.mark.parametrize("value", [0, CFG.market_data.stream_reserved_symbols + 1])
def test_config_refuses_a_slot_count_outside_1_to_the_reserved_stream_slots(tmp_path, value):
    text = (ROOT / "config" / "bot.toml").read_text("utf-8")
    text = re.sub(r"(?m)^max_open_positions = \d+", f"max_open_positions = {value}", text)
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "bot.toml"
    path.write_text(text, "utf-8")
    with pytest.raises(config.ConfigError, match="max_open_positions"):
        config.load(path, ROOT)


def test_the_shipped_config_keeps_one_slot():
    assert CFG.risk.max_open_positions == 1
