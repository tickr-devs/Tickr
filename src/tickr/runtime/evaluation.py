"""Run the active strategy once per completed minute (spec 5) and hand each decision to the
entry desk with exactly the bar and quote the strategy saw.

Per completed minute, in the regular session: the open positions' symbols, then the WATCH
symbols, then the latest discovery candidates. Today's completed regular-session M1 bars come
from `TodayBars` when discovery read the symbol after the minute was over (data/todaybars.py),
else from `MarketData.bars` (only the symbols missing, 20 per request). The prior sessions come
from `HistoryReader`, asked once per minute for all symbols that have bars (positions first; the
symbols already known for the day cost nothing, the new ones go 20 per request), then cached.
Before the open, `HistoryPreload` (runtime/preload.py) loads the screeners' symbols' history.
A history refusal is not an error here: the strategy gets no prior sessions and refuses with
its own cause, and the decision is recorded like any other. A budget refusal, delayed data or
a failed read of bars stops the symbols not read for this minute (journaled); the symbols read
are still evaluated. A history error stops the minute at the first symbol, in order, whose
history it left unknown (journaled). A strategy exception is journaled and that symbol skipped.
As soon as a decision leaves an order
needing the loop (an entry sent, an exit asked; runtime/protection.py), the rest of the minute
waits (journaled with the symbols left and their quotes at that moment), so the fill and its
stop are not kept waiting behind more reads. On the first pass the service runs the strategy
again, that remainder is evaluated on the same bar (bars cut at it), the saved quotes and the
daily loss budget of that minute (never the room left at replay time), for
the record only: an ENTER is recorded `not_evaluated`, nothing is sent or asked, the WATCH set
is untouched. A `evaluation-remainder` row (keyed by the bar) is written before the records,
so a restart never records the remainder twice; a remainder whose bars or history cannot be
read is journaled, never silently dropped. Nothing here ever ends the trading loop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from tickr.broker.base import BrokerError
from tickr.calendar import NEW_YORK, CalendarRefused, SessionCalendar
from tickr.config import Config
from tickr.data.base import MarketData, MarketDataError
from tickr.data.history import HistoryRead, HistoryReader, SessionHistory
from tickr.data.todaybars import TodayBars
from tickr.engine.core import Engine, Leg
from tickr.runtime.entries import EntryDesk, daily_loss_room
from tickr.runtime.market import StreamInbox
from tickr.runtime.preload import HistoryPreload
from tickr.runtime.protection import busy
from tickr.strategy.base import PositionView, Strategy, SymbolContext, Verdict
from tickr.types import Bar, Quote

M1 = timedelta(minutes=1)
ZERO = Decimal(0)
EVALUATION_STOPPED = "evaluation-stopped"
STRATEGY_ERROR = "strategy-error"
HISTORY_REFUSED = "history-refused"
PROTECT_FIRST = "protect-first"
REMAINDER = "evaluation-remainder"
Remainder = tuple[datetime, dict[str, Quote | None], Decimal | None]


def _quote_body(quote: Quote | None) -> dict[str, str] | None:
    if quote is None:
        return None
    return {"bid": str(quote.bid), "ask": str(quote.ask), "bid_size": str(quote.bid_size),
            "ask_size": str(quote.ask_size), "provider_time": quote.provider_time.isoformat(),
            "receipt_time": quote.receipt_time.isoformat()}


def _quote(symbol: str, body: dict[str, str] | None) -> Quote | None:
    if body is None:
        return None
    return Quote(symbol, Decimal(body["bid"]), Decimal(body["ask"]), Decimal(body["bid_size"]),
                 Decimal(body["ask_size"]), datetime.fromisoformat(body["provider_time"]),
                 datetime.fromisoformat(body["receipt_time"]))


def _chunks(items: Sequence[str], size: int) -> list[Sequence[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class Evaluator:
    def __init__(self, engine: Engine, config: Config, source: MarketData, *,
                 strategy: Strategy, desk: EntryDesk, history: HistoryReader,
                 inbox: StreamInbox, calendar: SessionCalendar, today_bars: TodayBars,
                 now: Callable[[], datetime]) -> None:
        self.engine = engine
        self.config = config
        self.source = source
        self.strategy = strategy
        self.desk = desk
        self.history = history
        self.today_bars = today_bars  # shared with discovery
        self.preload = HistoryPreload(engine, config, source, history)
        self.inbox = inbox
        self.calendar = calendar
        self.now = now
        self.watch: set[str] = set()
        self.contexts: list[SymbolContext] = []  # of the latest pass, for observers
        self.evaluated_at: datetime | None = None  # decision time of `contexts`
        self._last_bar: datetime | None = None
        self._history_refused: set[tuple[str, date]] = set()
        self._budget: Decimal | None = None  # daily loss room of the current minute
        self._remainder = self._pending_remainder()

    def _journal(self, kind: str, body: dict[str, object]) -> None:
        with self.engine.store.transaction():
            self.engine.store.append(kind, body, run_id=self.engine.run_id)

    def _positions(self) -> dict[str, PositionView]:
        views = {}
        for trade in self.engine.trades():
            orders = self.engine.orders(trade.trade_id)
            quantity = self.engine.expected_quantity(orders)
            entry = next((o for o in orders if o.leg is Leg.ENTRY), None)
            if quantity > 0 and entry is not None and entry.average_fill_price is not None:
                views[trade.symbol] = PositionView(entry.average_fill_price, quantity,
                                                   trade.stop_price)
        return views

    def _pending_remainder(self) -> Remainder | None:  # bar, saved quotes, loss budget
        """The latest protect-first remainder not yet evaluated (survives restarts)."""
        rows = [r for r in self.engine.store.journal(EVALUATION_STOPPED)
                if r.body.get("stage") == PROTECT_FIRST and r.body.get("remaining")]
        if not rows or self.engine.store.journal(REMAINDER, rows[-1].key):
            return None
        body = rows[-1].body
        budget = body.get("loss_budget_usd")
        return (datetime.fromisoformat(body["bar_start"]),
                {s: _quote(s, body["quotes"].get(s)) for s in body["remaining"]},
                None if budget is None else Decimal(budget))

    def _loss_budget(self, now: datetime) -> Decimal | None:
        """The strategy sizes into this room (owner decision 2026-11-14). None when today's
        money is unknown or the room is below `[risk] min_daily_room_usd`: the strategy then
        sizes normally and admission refuses with its typed cause (DAILY_MONEY_UNKNOWN or
        DAILY_CEILING), so the refusal is recorded once, by the platform."""
        room = daily_loss_room(self.engine, self.config, now)
        if room is None or room < self.config.risk.min_daily_room_usd:
            return None
        return room

    def tick(self, candidates: Sequence[str]) -> None:
        """Once per loop pass; does work only when a new minute has completed or a minute's
        remainder waits (then only that, this pass)."""
        self.contexts = []
        now = self.now()
        self.evaluated_at = now
        if self._remainder is not None:
            self._finish_remainder(now)
            return
        try:
            day = self.calendar.day_at(now)
        except CalendarRefused:
            return
        if now < day.regular_open:
            self.preload.tick(day, now)  # reads only; nothing before the open decides
            return
        if not day.in_regular_session(now):
            return
        bar_start = now.replace(second=0, microsecond=0) - M1
        if bar_start < day.regular_open or bar_start == self._last_bar:
            return
        self._last_bar = bar_start
        positions = self._positions()
        self._budget = self._loss_budget(now)
        ordered = list(dict.fromkeys([*sorted(positions), *sorted(self.watch), *candidates]))
        self._run(ordered, bar_start, day.regular_open, positions, now, late=None)

    def _finish_remainder(self, now: datetime) -> None:
        assert self._remainder is not None
        bar_start, quotes, self._budget = self._remainder  # that minute's room, not today's now
        self._remainder = None
        symbols = list(quotes)
        with self.engine.store.transaction():  # before the records: never twice
            self.engine.store.append(REMAINDER, {"symbols": symbols}, key=bar_start.isoformat(),
                                     run_id=self.engine.run_id)
        try:
            regular_open = self.calendar.day_at(bar_start).regular_open
        except CalendarRefused as exc:
            self._journal(EVALUATION_STOPPED, {"bar_start": bar_start.isoformat(),
                                               "stage": "remainder", "symbols": symbols,
                                               "error": exc.cause})
            return
        self._run(symbols, bar_start, regular_open, self._positions(), now, late=quotes)

    def _run(self, ordered: list[str], bar_start: datetime, regular_open: datetime,
             positions: dict[str, PositionView], now: datetime,
             late: dict[str, Quote | None] | None) -> None:
        """Evaluate `ordered` on the bar starting at `bar_start`. `late`: a remainder, for the
        record only, with the quotes saved when it was put aside."""
        today = bar_start.astimezone(NEW_YORK).date()
        md = self.config.market_data
        stage = "remainder-" if late is not None else ""
        read = self._today_bars(ordered, bar_start, regular_open, late)
        owns: dict[str, list[Bar]] = {}
        for symbol in [s for s in ordered if s in read]:
            own = [b for b in read[symbol]
                   if regular_open <= b.start <= bar_start and b.start + M1 <= now]
            if own:
                owns[symbol] = own
            elif late is not None:
                self._stopped(late, {"bar_start": bar_start.isoformat(),
                                     "stage": "remainder-no-bars"}, [symbol])
        if not owns:
            return
        histories = self.history.prior_sessions(list(owns), today, md.history_sessions)
        symbols = list(owns)
        for i, symbol in enumerate(symbols):
            error, history = self._history(histories, symbol, today)
            if error is not None:
                self._stopped(late, {"bar_start": bar_start.isoformat(),
                                     "stage": stage + "history", "symbol": symbol,
                                     "error": error}, symbols[i:])
                return
            if late is not None:
                self._evaluate(symbol, owns[symbol], history, positions.get(symbol), now,
                               late_quote=late.get(symbol), late=True)
                continue
            self._evaluate(symbol, owns[symbol], history, positions.get(symbol), now)
            cause = busy(self.engine)
            if cause is not None:  # an entry or exit just went out: protect it first
                self._put_aside(bar_start, symbol, cause, symbols[i + 1:])
                return

    def _today_bars(self, ordered: list[str], bar_start: datetime, regular_open: datetime,
                    late: dict[str, Quote | None] | None) -> dict[str, tuple[Bar, ...]]:
        """Today's bars per symbol: discovery's read when it was sent after the minute was
        over, else read here (missing symbols only, in order, 20 per request; each read is kept
        for later minutes and remainders too). A failed read journals the symbols not read and
        stops reading; those read are returned."""
        md = self.config.market_data
        stage = "remainder-" if late is not None else ""
        found: dict[str, tuple[Bar, ...]] = {}
        missing: list[str] = []
        for symbol in ordered:
            kept = self.today_bars.complete(regular_open, symbol, bar_start + M1)
            if kept is None:
                missing.append(symbol)
            else:
                found[symbol] = kept
        for n, chunk in enumerate(_chunks(missing, md.bars_symbols_per_request)):
            sent = self.now()  # before the request (see TodayBars)
            try:
                bars = self.source.bars(list(chunk), md.bars_per_symbol)
            except (MarketDataError, BrokerError) as exc:  # RateLimited, DelayedData included
                unread = set(missing[n * md.bars_symbols_per_request:])
                self._stopped(late, {"bar_start": bar_start.isoformat(),
                                     "stage": stage + "bars", "error": type(exc).__name__},
                              [s for s in ordered if s in unread])
                break
            self.today_bars.put(regular_open, chunk, bars, sent)
            for symbol in chunk:
                found[symbol] = tuple(b for b in bars if b.symbol == symbol)
        return found

    def _stopped(self, late: dict[str, Quote | None] | None, body: dict[str, object],
                 left: list[str]) -> None:
        """Journal a stop; for a remainder, name the symbols that get no record."""
        self._journal(EVALUATION_STOPPED, body if late is None else {**body, "symbols": left})

    def _put_aside(self, bar_start: datetime, symbol: str, cause: str,
                   remaining: list[str]) -> None:
        quotes = {s: self.inbox.latest(s) for s in remaining}
        with self.engine.store.transaction():
            self.engine.store.append(EVALUATION_STOPPED, {
                "bar_start": bar_start.isoformat(), "stage": PROTECT_FIRST, "symbol": symbol,
                "cause": cause, "remaining": remaining,
                "quotes": {s: _quote_body(q) for s, q in quotes.items()},
                "loss_budget_usd": None if self._budget is None else str(self._budget)},
                key=bar_start.isoformat(), run_id=self.engine.run_id)
        if remaining:
            self._remainder = (bar_start, quotes, self._budget)

    def _history(self, read: HistoryRead, symbol: str,
                 today: date) -> tuple[str | None, SessionHistory | None]:
        """(error type, prior sessions) of `symbol` in this minute's history read. History
        None when refused (recorded once a day); an error (budget, delayed data, a failed or
        malformed read) that left it unknown stops this minute: the next minute tries again."""
        found = read.found.get(symbol)
        if found is not None:
            return None, found
        refused = read.refused.get(symbol)
        if refused is not None:
            if (symbol, today) not in self._history_refused:
                self._history_refused.add((symbol, today))
                self._journal(HISTORY_REFUSED, {"symbol": symbol, "cause": refused.cause,
                                                "detail": refused.detail})
            return None, None
        assert read.error is not None  # neither found nor refused: the error stopped it
        return type(read.error).__name__, None

    def _evaluate(self, symbol: str, today_bars: list[Bar], history: SessionHistory | None,
                  position: PositionView | None, now: datetime, *,
                  late_quote: Quote | None = None, late: bool = False) -> None:
        prior = () if history is None else tuple(history.bars_of(d) for d in history.sessions)
        bars = ([] if history is None else list(history.bars)) + today_bars
        quote = late_quote if late else self.inbox.latest(symbol)
        context = SymbolContext(symbol, tuple(bars), prior, quote, position, self._budget)
        try:
            decision = self.strategy.evaluate(context)
        except Exception as exc:  # noqa: BLE001 - a strategy bug never ends the loop
            self._journal(STRATEGY_ERROR, {"symbol": symbol, "error": type(exc).__name__,
                                           "detail": str(exc)})
            return
        if late:
            self.desk.record_late(decision, bar_start=today_bars[-1].start, now=now)
            return
        if decision.verdict is Verdict.WATCH:
            self.watch.add(symbol)
        elif decision.verdict is Verdict.IGNORE:
            self.watch.discard(symbol)
        # Admission compares the quote and the last broker read with the clock, so it gets the
        # time of this decision: the reads above take time, and the tick's own start would make
        # a quote received meanwhile look like the future and an old one look fresh.
        self.desk.on_decision(decision, bar_start=today_bars[-1].start, quote=quote,
                              now=self.now())
        self.contexts.append(context)
