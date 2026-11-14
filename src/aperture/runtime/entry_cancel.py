"""Cancel a working entry when its reason to exist is gone (spec 9): the executable quote left
the decision's price window, the quote went stale or its stream is quiet or lost, or the
session's last-entry time passed. Never re-priced.

Runs on the trading loop after the engine's broker work and the stream's events of the pass.
It only decides; `Engine.cancel_entry` records the decision with its one cause before the
cancel request, cancels only the unfilled remainder (filled shares stay protected exactly as
before) and never touches a stop.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from aperture.calendar import CalendarRefused, SessionCalendar
from aperture.config import Config
from aperture.data.health import StreamState
from aperture.engine.core import Engine, Leg, TradeRow, TradeState
from aperture.risk.timers import deadlines
from aperture.types import Quote


class EntryCancel(StrEnum):
    WINDOW_LEFT = "window-left"
    QUOTE_STALE = "quote-stale"
    LAST_ENTRY = "last-entry"


def cancel_cause(trade: TradeRow, *, quote: Quote | None, stream: StreamState | None,
                 calendar: SessionCalendar | None, now: datetime,
                 config: Config) -> tuple[EntryCancel, str] | None:
    """Why the trade's working entry must be cancelled now, or None while it may rest.
    Whatever cannot be shown to allow the entry is a cause (refuse, never guess)."""
    session = config.session
    if calendar is None:
        return EntryCancel.LAST_ENTRY, "no calendar loaded"
    try:
        times = deadlines(calendar.day_at(now), session.last_entry_minutes_before_close,
                          session.flatten_minutes_before_close)
    except CalendarRefused as exc:
        return EntryCancel.LAST_ENTRY, f"session unknown: {exc.cause}"
    if now >= times.last_entry:
        return EntryCancel.LAST_ENTRY, f"last entry was {times.last_entry.isoformat()}"
    if quote is None:
        return EntryCancel.QUOTE_STALE, "no quote"
    if stream is not StreamState.FRESH:
        return EntryCancel.QUOTE_STALE, f"stream {stream or 'not tracked'}"
    risk = config.risk
    age = Decimal(str((now - quote.provider_time).total_seconds()))
    if -age > risk.max_clock_skew_seconds:
        return EntryCancel.QUOTE_STALE, f"provider time {-age} s ahead of our clock"
    if age > risk.max_quote_age_seconds:
        return EntryCancel.QUOTE_STALE, f"quote {age} s old > {risk.max_quote_age_seconds} s"
    if not Decimal(0) < quote.bid <= quote.ask:
        return EntryCancel.QUOTE_STALE, f"bid {quote.bid} / ask {quote.ask} not executable"
    if trade.entry_low is None:
        return EntryCancel.WINDOW_LEFT, "no price window recorded for this trade"
    if not trade.entry_low <= quote.ask <= trade.entry_limit_price:
        return EntryCancel.WINDOW_LEFT, (f"ask {quote.ask} outside {trade.entry_low}.."
                                         f"{trade.entry_limit_price}")
    return None


def cancel_stale_entries(engine: Engine, config: Config, calendar: SessionCalendar | None,
                         now: datetime, quote: Callable[[str], Quote | None],
                         stream: Callable[[str], StreamState | None]) -> int:
    """One pass over the open trades; returns how many entry cancels were decided. A trade
    already exiting (its teardown cancels the entry) or already decided is left alone."""
    decided = 0
    for trade in engine.trades():
        if trade.entry_cancel or trade.exit_requested or trade.state is TradeState.EXITING:
            continue
        entry = next(o for o in engine.orders(trade.trade_id) if o.leg is Leg.ENTRY)
        if not entry.live:
            continue
        found = cancel_cause(trade, quote=quote(trade.symbol), stream=stream(trade.symbol),
                             calendar=calendar, now=now, config=config)
        if found is not None and engine.cancel_entry(trade.trade_id, *found):
            decided += 1
    return decided
