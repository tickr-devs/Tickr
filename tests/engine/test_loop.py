from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from aperture import config
from aperture.broker.fake import FakeBroker
from aperture.config import GateResult
from aperture.engine.core import Engine, Leg
from aperture.engine.loop import run
from aperture.store import Store

CFG = config.load(Path(__file__).resolve().parents[2] / "config" / "bot.toml")


def test_loop_protects_and_reconciles(tmp_path):
    store = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
    broker = FakeBroker()
    engine = Engine(store, broker, CFG, "runloop1", lambda: GateResult(True, "t"))
    trade = engine.open_trade("ABC", D(1), D(10), D(9), D(1))
    broker.fill(engine.orders(trade)[0].client_order_id, D(1), D(10))
    clock = [0.0]
    ticks = []

    def sleep(seconds: float) -> None:
        clock[0] += seconds
        ticks.append(seconds)

    run(engine, should_stop=lambda: len(ticks) >= 5, sleep=sleep, monotonic=lambda: clock[0])
    stops = [o for o in engine.orders(trade) if o.leg is Leg.STOP]
    assert len(stops) == 1 and stops[0].broker_state == "ACCEPTED"
    assert engine.fences() == {}
    assert ticks == [float(CFG.orders.detail_poll_seconds)] * 5
    store.close()
