"""Readers and error mapping against recorded Webull responses (tests/fixtures/webull)."""

from __future__ import annotations

import copy
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from aperture.broker.base import OrderNotFound, RateLimited, Refused
from aperture.broker.webull import (
    DETAIL_NOT_VISIBLE,
    BrokerReadFailed,
    MalformedResponse,
    Route,
    WireResponse,
    parse_json,
    read_order_detail,
    read_order_rows,
)
from aperture.types import OrderState, OrderType, Side

from .test_webull_broker import OWN, broker, limit_buy

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "webull"
CID = "probe-d13d7316437b4405b9a3a655c0"


def own(account_id: str) -> bool:
    return account_id == OWN


def load(name: str) -> WireResponse:
    raw = json.loads((FIXTURES / name).read_text("utf-8"))
    # Round-trip the body through the adapter's parser: numbers become Decimal.
    return WireResponse(raw["status"], parse_json(json.dumps(raw["body"])))


def recorded_combo() -> dict:
    """The recorded combo row; Detail returns the same shape (combo with `orders[]`)."""
    return copy.deepcopy(load("historical_orders_cancelled_limit.json").body["data"][0])


def test_fixtures_carry_no_account_identity():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text("utf-8")
        assert "account_id" not in text, path.name
        assert "TS" + "JE" not in text, path.name  # prefix of the real Sandbox account


def test_empty_open_orders_and_positions():
    b, t, _ = broker(load("open_orders_empty.json"), load("positions_empty.json"))
    assert b.open_orders() == []
    assert b.positions() == []
    assert [r for r, _ in t.sent] == [Route.OPEN_ORDERS, Route.POSITIONS]


def test_recorded_order_row():
    (order,) = read_order_rows(load("historical_orders_cancelled_limit.json").body, own, "h")
    assert order.client_order_id == CID
    assert (order.symbol, order.side, order.order_type) == ("AAPL", Side.BUY, OrderType.LIMIT)
    assert order.quantity == D(1) and isinstance(order.quantity, D)
    assert order.filled_quantity == D(0)
    assert order.state is OrderState.CANCELLED and order.raw_status == "CANCELLED"
    assert order.average_fill_price is None


def test_detail_from_recorded_shape():
    b, _, _ = broker(WireResponse(200, recorded_combo()))
    detail = b.order_detail(CID)
    assert detail.state is OrderState.CANCELLED


def with_row(**changes) -> dict:
    combo = recorded_combo()
    row = combo["orders"][0]
    for key, value in changes.items():
        if value is None:
            row.pop(key)
        else:
            row[key] = value
    return combo


@pytest.mark.parametrize(
    "changes, state",
    [
        ({"status": "SUBMITTED"}, OrderState.ACCEPTED),
        ({"status": "PARTIAL FILLED", "total_quantity": "2", "filled_quantity": "1",
          "filled_price": "100.00"}, OrderState.PARTIALLY_FILLED),
        ({"status": "FILLED", "filled_quantity": "1", "filled_price": "99.98"}, OrderState.FILLED),
        ({"status": "FAILED"}, OrderState.REJECTED),
        ({"status": "PENDING_CANCEL"}, OrderState.UNKNOWN),  # may still lose to a fill
        ({"status": "FILLED"}, OrderState.UNKNOWN),  # FILLED with 0 filled contradicts
    ],
)
def test_detail_status_variants(changes, state):
    detail = read_order_detail(with_row(**changes), CID, own)
    assert detail.state is state


def test_fill_price_is_decimal():
    detail = read_order_detail(
        with_row(status="FILLED", filled_quantity="1", filled_price="99.9800"), CID, own
    )
    assert detail.average_fill_price == D("99.98")


@pytest.mark.parametrize(
    "changes",
    [
        {"filled_quantity": None},
        {"total_quantity": None},
        {"status": None},
        {"side": "SHORT"},
        {"order_type": "TRAILING_STOP_LOSS"},
        {"filled_quantity": 0.0},
        {"account_id": "SOMEONE-ELSE"},
    ],
)
def test_detail_refuses_missing_or_foreign(changes):
    with pytest.raises(MalformedResponse):
        read_order_detail(with_row(**changes), CID, own)


def test_detail_refuses_another_order():
    with pytest.raises(MalformedResponse):
        read_order_detail(recorded_combo(), "ap-other", own)


def test_detail_empty_orders_is_not_absence():
    combo = recorded_combo()
    combo["orders"] = []
    with pytest.raises(BrokerReadFailed) as exc:
        read_order_detail(combo, CID, own)
    assert exc.value.code == DETAIL_NOT_VISIBLE


def test_recorded_errors_map_to_typed_errors():
    b, t, budget = broker(
        load("error_non_trading_hours.json"),
        load("error_order_not_present.json"),
        load("error_order_not_present.json"),
        load("error_too_many_requests.json"),
    )
    with pytest.raises(Refused) as refused:
        b.place(limit_buy())
    assert refused.value.code == "OPENAPI_CAN_NOT_TRADING_FOR_NON_TRADING_HOURS"
    with pytest.raises(OrderNotFound):
        b.order_detail("ap-404")
    with pytest.raises(OrderNotFound):
        b.cancel("ap-404")
    with pytest.raises(RateLimited):
        b.order_detail("ap-1")
    assert budget.usage(Route.ORDER_DETAIL.path).paused_for_seconds > 0
    assert len(t.sent) == 4
