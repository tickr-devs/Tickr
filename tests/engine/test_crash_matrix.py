"""Kill the process at every broker call (before and after it) and restart.

Invariants after recovery: nothing is sent twice; the run ends either closed and flat, or
holding a position whose broker stop is working with entries fenced.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal as D
from pathlib import Path

import pytest

from aperture import config
from aperture.broker.fake import FakeBroker
from aperture.config import GateResult
from aperture.engine.core import Engine, Leg, TradeState
from aperture.store import Store
from aperture.types import OrderState

CFG = config.load(Path(__file__).resolve().parents[2] / "config" / "bot.toml")
OPEN = GateResult(True, "test")


class Crash(BaseException):
    pass


class CrashingBroker(FakeBroker):
    def __init__(self, at: int, after: bool) -> None:
        super().__init__()
        self.calls = 0
        self.at = at
        self.after = after

    def _maybe(self, fn, *args):
        self.calls += 1
        if self.calls == self.at and not self.after:
            raise Crash()
        result = fn(*args)
        if self.calls == self.at:
            raise Crash()
        return result

    def place(self, request):
        return self._maybe(super().place, request)

    def cancel(self, client_order_id):
        return self._maybe(super().cancel, client_order_id)


def drive(engine: Engine, broker: FakeBroker, steps: int = 30) -> None:
    """Simulate the market: entry and exit orders fill fully; exit once protected."""
    for _ in range(steps):
        for trade in engine.trades():
            for order in engine.orders(trade.trade_id):
                if order.leg in (Leg.ENTRY, Leg.EXIT) and order.client_order_id in broker.orders:
                    detail = broker.order_detail(order.client_order_id)
                    if not detail.state.terminal:
                        broker.fill(order.client_order_id,
                                    detail.quantity - detail.filled_quantity, D(10))
            stops = [o for o in engine.orders(trade.trade_id) if o.leg is Leg.STOP]
            if trade.state is TradeState.OPEN and any(
                    s.broker_state is OrderState.ACCEPTED for s in stops):
                engine.request_exit(trade.trade_id, "test-exit")
        engine.step()


@pytest.mark.parametrize("after", [False, True], ids=["before-call", "after-call"])
@pytest.mark.parametrize("at", [1, 2, 3, 4])
def test_crash_anywhere_then_restart(tmp_path, at, after):
    store = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
    broker = CrashingBroker(at, after)
    engine = Engine(store, broker, CFG, "run1aaaa", lambda: OPEN)
    try:
        engine.open_trade("ABC", D(1), D(10), D("9.80"), D(1))
        drive(engine, broker)
        crashed = False
    except Crash:
        crashed = True
    assert crashed

    healthy = FakeBroker(orders=broker.orders, holdings=broker.holdings, sent=broker.sent)
    restarted = Engine(store, healthy, CFG, "run2bbbb", lambda: OPEN)
    restarted.recover()
    drive(restarted, healthy)

    counts = Counter(r.client_order_id for r in healthy.sent)
    assert all(n == 1 for n in counts.values()), counts
    trades = restarted.trades(open_only=False)
    held = {p.symbol: p.quantity for p in healthy.positions()}
    if all(t.state is TradeState.CLOSED for t in trades):
        assert held == {}
    else:
        # Still holding: must be protected at the broker and entries fenced.
        working = sum((o.quantity - o.filled_quantity for o in healthy.open_orders()
                       if o.order_type == "STOP_LOSS"), D(0))
        assert working >= held.get("ABC", D(0)) > 0
        assert restarted.fences()
    store.close()
