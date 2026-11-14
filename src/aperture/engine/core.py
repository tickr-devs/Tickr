"""Trade engine: entry -> protective stop -> exit -> closed, driven by broker Detail.

Every send and cancel is written to the store BEFORE the broker call and its outcome after
it (commit-before-effect). Nothing is ever resent automatically. State advances only from
broker reads; `step()` is one pass and is safe to call repeatedly or after a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aperture.broker.base import (
    Broker,
    BrokerError,
    OrderNotFound,
    RateLimited,
    Refused,
)
from aperture.config import Config, GateResult
from aperture.store import Store, now_utc
from aperture.types import (
    OrderRequest,
    OrderState,
    OrderType,
    Side,
    TimeInForce,
    TradingSession,
)

ZERO = Decimal(0)


class Leg(StrEnum):
    ENTRY = "entry"
    STOP = "stop"
    EXIT = "exit"


class SendState(StrEnum):
    INTENT = "intent"  # written, not yet attempted
    ATTEMPTED = "attempted"  # about to call the broker
    ACKNOWLEDGED = "acknowledged"
    REFUSED = "refused"  # broker or local budget refused: the order was NOT created
    UNKNOWN = "unknown"  # may or may not exist: never resend, resolve by Detail
    ABSENT = "absent"  # resolved: never created


class TradeState(StrEnum):
    OPENING = "opening"  # entry working, nothing filled yet
    OPEN = "open"  # some quantity filled; protection maintained
    EXITING = "exiting"  # teardown: cancel entry and stops, then close what is held
    CLOSED = "closed"


class Fence(StrEnum):
    """Reasons that block NEW entries. Never block exits, cancels or protection."""

    STARTUP_RECONCILE = "startup-reconcile"
    BROKER_READ = "broker-read-failed"
    UNKNOWN_ORDER = "unknown-order"
    STATUS_UNKNOWN = "status-unknown"
    UNPROTECTED = "unprotected"
    STOP_REJECTED = "stop-rejected"
    EXIT_REFUSED = "exit-refused"
    CANCEL_UNKNOWN = "cancel-unknown"
    FOREIGN_POSITION = "foreign-position"
    FOREIGN_ORDER = "foreign-order"
    QUANTITY_MISMATCH = "quantity-mismatch"


# Fences that need the operator to look before entries resume; all others clear themselves
# when their condition is gone.
OPERATOR_FENCES = frozenset({Fence.STOP_REJECTED, Fence.EXIT_REFUSED})

_LEG_LETTER = {Leg.ENTRY: "e", Leg.STOP: "s", Leg.EXIT: "x"}
_LIVE_SEND = (SendState.INTENT, SendState.ATTEMPTED, SendState.ACKNOWLEDGED, SendState.UNKNOWN)


class EntryRefused(Exception):
    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


@dataclass(frozen=True)
class OrderRow:
    client_order_id: str
    trade_id: str
    leg: Leg
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    send_state: SendState
    broker_state: OrderState | None
    filled_quantity: Decimal
    cancel_state: str | None
    absent_reads: int

    @property
    def live(self) -> bool:
        """Could still create exposure or fill."""
        if self.send_state not in _LIVE_SEND:
            return False
        return self.broker_state is None or not self.broker_state.terminal

    @property
    def open_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True)
class TradeRow:
    trade_id: str
    symbol: str
    state: TradeState
    entry_quantity: Decimal
    entry_limit_price: Decimal
    stop_price: Decimal
    planned_loss_usd: Decimal
    exit_requested: str | None
    stop_failures: int


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


def _order(row: Any) -> OrderRow:
    return OrderRow(
        client_order_id=row["client_order_id"], trade_id=row["trade_id"], leg=Leg(row["leg"]),
        symbol=row["symbol"], side=Side(row["side"]), order_type=OrderType(row["order_type"]),
        quantity=Decimal(row["quantity"]), limit_price=_dec(row["limit_price"]),
        stop_price=_dec(row["stop_price"]), send_state=SendState(row["send_state"]),
        broker_state=None if row["broker_state"] is None else OrderState(row["broker_state"]),
        filled_quantity=Decimal(row["filled_quantity"]), cancel_state=row["cancel_state"],
        absent_reads=row["absent_reads"],
    )


def _trade(row: Any) -> TradeRow:
    return TradeRow(
        trade_id=row["trade_id"], symbol=row["symbol"], state=TradeState(row["state"]),
        entry_quantity=Decimal(row["entry_quantity"]),
        entry_limit_price=Decimal(row["entry_limit_price"]),
        stop_price=Decimal(row["stop_price"]), planned_loss_usd=Decimal(row["planned_loss_usd"]),
        exit_requested=row["exit_requested"], stop_failures=row["stop_failures"],
    )


class Engine:
    def __init__(self, store: Store, broker: Broker, config: Config, run_id: str,
                 gate: Callable[[], GateResult]) -> None:
        self.store = store
        self.broker = broker
        self.config = config
        self.run_id = run_id
        self.gate = gate

    # --- fences ----------------------------------------------------------------------------

    def fences(self) -> dict[str, str]:
        return {r["name"]: r["cause"] for r in self.store.query("SELECT name, cause FROM fences")}

    def _set_fence(self, fence: Fence, key: str, cause: str) -> None:
        name = f"{fence}:{key}" if key else str(fence)
        with self.store.transaction():
            if self.store.execute("INSERT OR IGNORE INTO fences(name, cause, set_at) "
                                  "VALUES (?, ?, ?)", (name, cause, now_utc())):
                self.store.append("fence-set", {"cause": cause}, key=name, run_id=self.run_id)

    def _fence_row(self, fence: Fence, key: str, cause: str) -> None:
        """Set a fence inside an already open transaction."""
        name = f"{fence}:{key}"
        if self.store.execute("INSERT OR IGNORE INTO fences(name, cause, set_at) "
                              "VALUES (?, ?, ?)", (name, cause, now_utc())):
            self.store.append("fence-set", {"cause": cause}, key=name, run_id=self.run_id)

    def _clear_fence(self, fence: Fence, key: str = "") -> None:
        name = f"{fence}:{key}" if key else str(fence)
        with self.store.transaction():
            if self.store.execute("DELETE FROM fences WHERE name = ?", (name,)):
                self.store.append("fence-cleared", {}, key=name, run_id=self.run_id)

    def clear_operator_fence(self, name: str) -> bool:
        """Operator command: clear a fence that does not clear itself."""
        if name.split(":", 1)[0] not in OPERATOR_FENCES:
            return False
        with self.store.transaction():
            removed = self.store.execute("DELETE FROM fences WHERE name = ?", (name,))
            if removed:
                self.store.append("fence-cleared", {"by": "operator"}, key=name,
                                  run_id=self.run_id)
        return bool(removed)

    # --- reads -----------------------------------------------------------------------------

    def trades(self, *, open_only: bool = True) -> list[TradeRow]:
        sql = "SELECT * FROM trades"
        if open_only:
            sql += f" WHERE state != '{TradeState.CLOSED}'"
        return [_trade(r) for r in self.store.query(sql + " ORDER BY opened_at")]

    def trade(self, trade_id: str) -> TradeRow:
        return _trade(self.store.query("SELECT * FROM trades WHERE trade_id = ?", (trade_id,))[0])

    def orders(self, trade_id: str | None = None) -> list[OrderRow]:
        if trade_id is None:
            rows = self.store.query("SELECT * FROM orders ORDER BY rowid")
        else:
            rows = self.store.query("SELECT * FROM orders WHERE trade_id = ? ORDER BY rowid",
                                    (trade_id,))
        return [_order(r) for r in rows]

    def order(self, client_order_id: str) -> OrderRow:
        return _order(self.store.query("SELECT * FROM orders WHERE client_order_id = ?",
                                       (client_order_id,))[0])

    # --- entry -----------------------------------------------------------------------------

    def open_trade(self, symbol: str, quantity: Decimal, limit_price: Decimal,
                   stop_price: Decimal, planned_loss_usd: Decimal) -> str:
        """Record and send a LIMIT BUY entry. Refuses (EntryRefused) instead of guessing."""
        gate = self.gate()
        if not gate.open:
            raise EntryRefused(f"trading gate closed: {gate.cause}")
        fences = self.fences()
        if fences:
            raise EntryRefused("entries fenced: " + ", ".join(sorted(fences)))
        if len(self.trades()) >= self.config.risk.max_open_positions:
            raise EntryRefused("open position limit reached")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            raise EntryRefused("quantity must be a positive whole number")
        if not ZERO < stop_price < limit_price:
            raise EntryRefused("stop must be above zero and below the entry limit")

        with self.store.transaction():
            trade_id = f"t{self.store.next_counter('trade')}"
            self.store.execute(
                "INSERT INTO trades(trade_id, run_id, symbol, state, entry_quantity, "
                "entry_limit_price, stop_price, planned_loss_usd, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, self.run_id, symbol, TradeState.OPENING, str(quantity),
                 str(limit_price), str(stop_price), str(planned_loss_usd), now_utc()))
            self.store.append("trade-opened", {
                "symbol": symbol, "quantity": quantity, "limit_price": limit_price,
                "stop_price": stop_price, "planned_loss_usd": planned_loss_usd},
                key=trade_id, run_id=self.run_id)
            cid = self._write_intent(trade_id, Leg.ENTRY, symbol, Side.BUY, OrderType.LIMIT,
                                     quantity, limit_price=limit_price)
        self._send(cid)
        return trade_id

    def request_exit(self, trade_id: str, cause: str) -> None:
        with self.store.transaction():
            changed = self.store.execute(
                "UPDATE trades SET exit_requested = ? WHERE trade_id = ? AND state != ? "
                "AND exit_requested IS NULL", (cause, trade_id, TradeState.CLOSED))
            if changed:
                self.store.append("exit-requested", {"cause": cause}, key=trade_id,
                                  run_id=self.run_id)

    # --- sending ---------------------------------------------------------------------------

    def _client_order_id(self, trade_id: str, leg: Leg) -> str:
        n = self.store.next_counter(f"orders:{trade_id}")
        cid = (f"{self.config.orders.client_order_id_prefix}{self.run_id[:8]}-"
               f"{trade_id}-{_LEG_LETTER[leg]}{n}")
        if len(cid) > self.config.orders.client_order_id_max_length:
            raise ValueError(f"client_order_id too long: {cid}")
        return cid

    def _write_intent(self, trade_id: str, leg: Leg, symbol: str, side: Side,
                      order_type: OrderType, quantity: Decimal, *,
                      limit_price: Decimal | None = None,
                      stop_price: Decimal | None = None) -> str:
        cid = self._client_order_id(trade_id, leg)
        self.store.execute(
            "INSERT INTO orders(client_order_id, trade_id, leg, symbol, side, order_type, "
            "quantity, limit_price, stop_price, send_state, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, trade_id, leg, symbol, side, order_type, str(quantity),
             None if limit_price is None else str(limit_price),
             None if stop_price is None else str(stop_price), SendState.INTENT, now_utc()))
        self.store.append("order-intent", {
            "trade_id": trade_id, "leg": leg, "symbol": symbol, "side": side,
            "order_type": order_type, "quantity": quantity, "limit_price": limit_price,
            "stop_price": stop_price}, key=cid, run_id=self.run_id)
        return cid

    def _set_send_state(self, cid: str, state: SendState, body: dict[str, Any],
                        *, refusal: str | None = None, broker_order_id: str | None = None) -> None:
        self.store.execute(
            "UPDATE orders SET send_state = ?, refusal = COALESCE(?, refusal), "
            "broker_order_id = COALESCE(?, broker_order_id), updated_at = ? "
            "WHERE client_order_id = ?",
            (state, refusal, broker_order_id, now_utc(), cid))
        self.store.append(f"order-{state}", body, key=cid, run_id=self.run_id)

    def _send(self, cid: str) -> None:
        row = self.order(cid)
        request = OrderRequest(
            client_order_id=cid, symbol=row.symbol, side=row.side, order_type=row.order_type,
            quantity=row.quantity, time_in_force=TimeInForce.DAY,
            session=TradingSession.CORE, limit_price=row.limit_price,
            stop_price=row.stop_price)
        with self.store.transaction():
            self._set_send_state(cid, SendState.ATTEMPTED, {})
        try:
            ack = self.broker.place(request)
        except (Refused, RateLimited) as exc:
            code = exc.code if isinstance(exc, Refused) else "rate-limited"
            with self.store.transaction():
                self._set_send_state(cid, SendState.REFUSED, {"code": code}, refusal=code)
                self._count_refusal(row)
            return
        except Exception as exc:  # OutcomeUnknown and anything unexpected: may exist
            with self.store.transaction():
                self._set_send_state(cid, SendState.UNKNOWN, {"error": type(exc).__name__})
            self._set_fence(Fence.UNKNOWN_ORDER, cid, "send outcome unknown")
            return
        with self.store.transaction():
            self._set_send_state(cid, SendState.ACKNOWLEDGED, {},
                                 broker_order_id=ack.broker_order_id)

    def _count_refusal(self, row: OrderRow) -> None:
        """Inside a transaction: a refused or rejected stop or exit counts against the trade."""
        if row.leg is Leg.STOP:
            self.store.execute("UPDATE trades SET stop_failures = stop_failures + 1 "
                               "WHERE trade_id = ?", (row.trade_id,))
        elif row.leg is Leg.EXIT:
            self.store.execute(
                "UPDATE trades SET state = ?, exit_requested = NULL WHERE trade_id = ?",
                (TradeState.OPEN, row.trade_id))
            self.store.append("exit-refused", {"client_order_id": row.client_order_id},
                              key=row.trade_id, run_id=self.run_id)
            self._fence_row(Fence.EXIT_REFUSED, row.trade_id,
                            "closing order refused; stop re-placed, operator must look")

    def _cancel(self, row: OrderRow) -> None:
        if row.cancel_state is not None:
            return  # one cancel per order, never resent
        cid = row.client_order_id
        with self.store.transaction():
            self.store.execute("UPDATE orders SET cancel_state = 'requested', updated_at = ? "
                               "WHERE client_order_id = ?", (now_utc(), cid))
            self.store.append("cancel-intent", {}, key=cid, run_id=self.run_id)
        try:
            self.broker.cancel(cid)
            outcome = "sent"
        except (Refused, OrderNotFound):
            outcome = "refused"
        except Exception:  # noqa: BLE001 - any transport failure: outcome unknown
            outcome = "unknown"
        with self.store.transaction():
            self.store.execute("UPDATE orders SET cancel_state = ?, updated_at = ? "
                               "WHERE client_order_id = ?", (outcome, now_utc(), cid))
            self.store.append(f"cancel-{outcome}", {}, key=cid, run_id=self.run_id)
        if outcome == "unknown":
            self._set_fence(Fence.CANCEL_UNKNOWN, cid, "cancel outcome unknown")

    # --- observation -----------------------------------------------------------------------

    def _refresh(self, row: OrderRow) -> None:
        cid = row.client_order_id
        try:
            detail = self.broker.order_detail(cid)
        except OrderNotFound:
            if row.send_state is SendState.UNKNOWN:
                reads = row.absent_reads + 1
                with self.store.transaction():
                    self.store.execute("UPDATE orders SET absent_reads = ? "
                                       "WHERE client_order_id = ?", (reads, cid))
                    if reads >= self.config.orders.unknown_absent_reads:
                        self._set_send_state(cid, SendState.ABSENT, {"reads": reads})
                        self._count_refusal(row)
                if reads >= self.config.orders.unknown_absent_reads:
                    self._clear_fence(Fence.UNKNOWN_ORDER, cid)
            return
        except BrokerError:
            return  # read failure: keep the last known state, try again next step
        changed = (detail.state != row.broker_state
                   or detail.filled_quantity != row.filled_quantity)
        with self.store.transaction():
            if row.send_state is SendState.UNKNOWN:
                self._set_send_state(cid, SendState.ACKNOWLEDGED, {"resolved_by": "detail"})
            if changed:
                self.store.execute(
                    "UPDATE orders SET broker_state = ?, filled_quantity = ?, "
                    "average_fill_price = ?, updated_at = ? WHERE client_order_id = ?",
                    (detail.state, str(detail.filled_quantity),
                     None if detail.average_fill_price is None
                     else str(detail.average_fill_price), now_utc(), cid))
                self.store.append("order-observed", {
                    "state": detail.state, "raw_status": detail.raw_status,
                    "filled_quantity": detail.filled_quantity,
                    "average_fill_price": detail.average_fill_price}, key=cid,
                    run_id=self.run_id)
                if detail.state is OrderState.REJECTED:
                    self._count_refusal(row)
        if row.send_state is SendState.UNKNOWN:
            self._clear_fence(Fence.UNKNOWN_ORDER, cid)
        if detail.state is OrderState.UNKNOWN:
            self._set_fence(Fence.STATUS_UNKNOWN, cid, f"unmapped status {detail.raw_status!r}")
        else:
            self._clear_fence(Fence.STATUS_UNKNOWN, cid)
        if detail.state.terminal and row.cancel_state == "unknown":
            self._clear_fence(Fence.CANCEL_UNKNOWN, cid)

    # --- the loop body ---------------------------------------------------------------------

    def step(self) -> None:
        """One pass: read positions, observe live orders, advance every open trade."""
        try:
            positions = self.broker.positions()
        except BrokerError as exc:
            self._set_fence(Fence.BROKER_READ, "", f"positions read failed: {type(exc).__name__}")
            return
        self._clear_fence(Fence.BROKER_READ)
        held = {p.symbol: p.quantity for p in positions}

        for row in self.orders():
            if row.send_state in (SendState.ACKNOWLEDGED, SendState.UNKNOWN) and (
                    row.broker_state is None or not row.broker_state.terminal):
                self._refresh(row)

        open_symbols = set()
        for trade in self.trades():
            open_symbols.add(trade.symbol)
            self._advance(trade, held.get(trade.symbol, ZERO))

        for symbol, quantity in held.items():
            if symbol not in open_symbols and quantity != 0:
                self._set_fence(Fence.FOREIGN_POSITION, symbol,
                                "position not opened by this bot")
        for name in self.fences():
            fence, _, symbol = name.partition(":")
            if fence == Fence.FOREIGN_POSITION and held.get(symbol, ZERO) == 0:
                self._clear_fence(Fence.FOREIGN_POSITION, symbol)

    def _advance(self, trade: TradeRow, held: Decimal) -> None:
        orders = self.orders(trade.trade_id)
        entry = next(o for o in orders if o.leg is Leg.ENTRY)
        stops = [o for o in orders if o.leg is Leg.STOP]
        exits = [o for o in orders if o.leg is Leg.EXIT]
        sold = sum((o.filled_quantity for o in stops + exits), ZERO)
        # Quantity we own according to broker Detail (fills only come from Detail).
        expected = entry.filled_quantity - sold
        live = [o for o in orders if o.live]

        if trade.state is TradeState.OPENING and entry.filled_quantity > 0:
            self._set_trade_state(trade, TradeState.OPEN)
        if trade.exit_requested and trade.state is not TradeState.EXITING:
            self._set_trade_state(trade, TradeState.EXITING)
            trade = self.trade(trade.trade_id)

        if not live and expected == 0 and held == 0:
            cause = trade.exit_requested or (
                "stopped-out" if any(o.filled_quantity > 0 for o in stops)
                else "entry-not-filled" if entry.filled_quantity == 0 else "closed")
            self._close(trade, cause)
            return

        if trade.state is TradeState.EXITING:
            self._exit_step(trade, entry, stops, exits, held, expected)
            return

        # Mismatch only once nothing is in flight; the broker quantity wins.
        if not live and held != expected:
            self._set_fence(Fence.QUANTITY_MISMATCH, trade.trade_id,
                            f"broker holds {held}, Detail says {expected}")
        else:
            self._clear_fence(Fence.QUANTITY_MISMATCH, trade.trade_id)
        self._protect(trade, stops, max(expected, held))

    def _protect(self, trade: TradeRow, stops: list[OrderRow], target: Decimal) -> None:
        live_stops = [s for s in stops if s.live]
        covered = sum((s.open_quantity for s in live_stops), ZERO)
        verified = sum((s.open_quantity for s in live_stops
                        if s.send_state is SendState.ACKNOWLEDGED
                        and s.broker_state in (OrderState.ACCEPTED,
                                               OrderState.PARTIALLY_FILLED)), ZERO)
        if target > 0 and verified < target:
            self._set_fence(Fence.UNPROTECTED, trade.trade_id,
                            f"verified stop covers {verified} of {target}")
        else:
            self._clear_fence(Fence.UNPROTECTED, trade.trade_id)
        if covered >= target:
            return
        if trade.stop_failures > self.config.orders.stop_retry_attempts:
            if f"{Fence.EXIT_REFUSED}:{trade.trade_id}" in self.fences():
                return  # exit already refused once: wait for the operator, do not loop
            self._set_fence(Fence.STOP_REJECTED, trade.trade_id,
                            "protective stop refused after retries; exiting")
            self.request_exit(trade.trade_id, "stop-rejected")
            return
        with self.store.transaction():
            cid = self._write_intent(trade.trade_id, Leg.STOP, trade.symbol, Side.SELL,
                                     OrderType.STOP_LOSS, target - covered,
                                     stop_price=trade.stop_price)
        self._send(cid)

    def _exit_step(self, trade: TradeRow, entry: OrderRow, stops: list[OrderRow],
                   exits: list[OrderRow], held: Decimal, expected: Decimal) -> None:
        if entry.live:
            self._cancel(entry)
        for stop in stops:
            if stop.live:
                self._cancel(stop)
        # Wait until the entry and every stop are terminal (confirmed by Detail).
        if entry.live or any(s.live for s in stops) or any(e.live for e in exits):
            return
        if held != expected:
            # Positions can lag Detail; never sell on a quantity the two sources disagree on.
            self._set_fence(Fence.QUANTITY_MISMATCH, trade.trade_id,
                            f"broker holds {held}, Detail says {expected}")
            return
        if held > 0:
            with self.store.transaction():
                cid = self._write_intent(trade.trade_id, Leg.EXIT, trade.symbol, Side.SELL,
                                         OrderType.MARKET, held)
            self._send(cid)

    def _set_trade_state(self, trade: TradeRow, state: TradeState) -> None:
        with self.store.transaction():
            self.store.execute("UPDATE trades SET state = ? WHERE trade_id = ?",
                               (state, trade.trade_id))
            self.store.append("trade-state", {"from": trade.state, "to": state},
                              key=trade.trade_id, run_id=self.run_id)

    def _close(self, trade: TradeRow, cause: str) -> None:
        with self.store.transaction():
            self.store.execute(
                "UPDATE trades SET state = ?, closed_at = ?, close_cause = ? WHERE trade_id = ?",
                (TradeState.CLOSED, now_utc(), cause, trade.trade_id))
            self.store.append("trade-closed", {"cause": cause}, key=trade.trade_id,
                              run_id=self.run_id)
        self._clear_fence(Fence.UNPROTECTED, trade.trade_id)
        self._clear_fence(Fence.QUANTITY_MISMATCH, trade.trade_id)

    # --- startup ---------------------------------------------------------------------------

    def recover(self) -> None:
        """Call once at startup before anything else. Fences entries until reconciled."""
        self._set_fence(Fence.STARTUP_RECONCILE, "", "startup reconciliation pending")
        for row in self.orders():
            if row.send_state is SendState.INTENT:
                # The attempt was never written, so the broker was never called.
                with self.store.transaction():
                    self._set_send_state(row.client_order_id, SendState.ABSENT,
                                         {"recovered": "never attempted"})
                    self._count_refusal(row)
            elif row.send_state is SendState.ATTEMPTED:
                with self.store.transaction():
                    self._set_send_state(row.client_order_id, SendState.UNKNOWN,
                                         {"recovered": "crashed during send"})
                self._set_fence(Fence.UNKNOWN_ORDER, row.client_order_id,
                                "crashed during send")
            if row.cancel_state == "requested":
                with self.store.transaction():
                    self.store.execute("UPDATE orders SET cancel_state = 'unknown' "
                                       "WHERE client_order_id = ?", (row.client_order_id,))
                    self.store.append("cancel-unknown", {"recovered": "crashed during cancel"},
                                      key=row.client_order_id, run_id=self.run_id)
                self._set_fence(Fence.CANCEL_UNKNOWN, row.client_order_id,
                                "crashed during cancel")
        if self.reconcile():
            self._clear_fence(Fence.STARTUP_RECONCILE)

    def reconcile(self) -> bool:
        """Compare broker open orders with our records; returns False if the read failed."""
        try:
            open_orders = self.broker.open_orders()
        except BrokerError as exc:
            self._set_fence(Fence.BROKER_READ, "", f"open orders read failed: {type(exc).__name__}")
            return False
        known = {o.client_order_id for o in self.orders()}
        broker_ids = set()
        for order in open_orders:
            broker_ids.add(order.client_order_id)
            if order.client_order_id not in known:
                self._set_fence(Fence.FOREIGN_ORDER, order.client_order_id,
                                "open order not placed by this bot")
        for name in self.fences():
            fence, _, cid = name.partition(":")
            if fence == Fence.FOREIGN_ORDER and cid not in broker_ids:
                self._clear_fence(Fence.FOREIGN_ORDER, cid)
        self.step()
        return Fence.BROKER_READ not in self.fences()
