"""The bot-watched stop outside the regular session (design v2.1 section 3, owner decision
2026-11-16 "broker, otherwise the bot"): Webull takes no stop in the pre-market or the
post-market (F7), so the bot checks each extended trade's stop price itself on every pass.

`watch` is one check of one trade: pure, no I/O. It answers HOLD (nothing to do), BREACH
(request the exit) or BLIND (fence entries, alert, exit from the last usable bid). The caller
owns the state between checks and supplies `feed_alive`: whether the market data feed itself is
proven alive (the liveness signal probe Q1 picks), so a thin book that has not changed is not
mistaken for a dead feed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tickr.config import SessionConfig
from tickr.risk.timers import WindowKind
from tickr.types import Quote


class Watch(StrEnum):
    HOLD = "hold"
    BREACH = "breach"
    BLIND = "blind"


@dataclass(frozen=True)
class WatchLimits:
    max_quote_age_seconds: Decimal
    max_spread_ratio: Decimal
    confirm_checks: int
    max_gap_seconds: Decimal
    blind_seconds: Decimal


def watch_limits(kind: WindowKind, session: SessionConfig) -> WatchLimits:
    """The watch limits of an extended window; the regular window has a broker stop."""
    if kind is WindowKind.PRE:
        age, spread = session.premarket_max_quote_age_seconds, session.premarket_max_spread_ratio
    elif kind is WindowKind.POST:
        age, spread = (session.postmarket_max_quote_age_seconds,
                       session.postmarket_max_spread_ratio)
    else:
        raise ValueError(f"no bot-watched stop in the {kind} window: it has a broker stop")
    return WatchLimits(age, spread, session.extended_stop_confirm_quotes,
                       session.extended_watch_max_gap_seconds, session.extended_blind_seconds)


@dataclass(frozen=True)
class WatchState:
    """What one trade's watch remembers between checks. Times are monotonic seconds, except
    `last_usable_time`, the provider time of the last usable book."""

    started: float
    last_check: float | None = None
    last_usable: float | None = None
    last_usable_bid: Decimal | None = None
    last_usable_time: datetime | None = None
    breach_checks: int = 0


@dataclass(frozen=True)
class WatchResult:
    verdict: Watch
    cause: str
    state: WatchState


def book_refusal(quote: Quote | None, now: datetime, lim: WatchLimits,
                 feed_alive: bool) -> str | None:
    """Why `quote` is not a usable book, or None. Usable: both sides present and not crossed,
    spread within the window's ceiling, and fresh by provider time unless the feed is proven
    alive (then an old quote is an unchanged book, not a dead one)."""
    if quote is None:
        return "no quote"
    if quote.bid <= 0 or quote.ask <= 0:
        return f"one-sided book (bid {quote.bid}, ask {quote.ask})"
    if quote.ask < quote.bid:
        return f"crossed book (bid {quote.bid}, ask {quote.ask})"
    spread = (quote.ask - quote.bid) / ((quote.ask + quote.bid) / 2)
    if spread > lim.max_spread_ratio:
        return f"spread {spread:.4f} > {lim.max_spread_ratio}"
    age = Decimal(str((now - quote.provider_time).total_seconds()))
    if age > lim.max_quote_age_seconds and not feed_alive:
        return f"quote {age} s old > {lim.max_quote_age_seconds} s and the feed is not alive"
    return None


def watch(stop_price: Decimal, quote: Quote | None, now: datetime, mono: float,
          state: WatchState, lim: WatchLimits, *, feed_alive: bool) -> WatchResult:
    """One check of one extended trade's stop at monotonic time `mono` (wall time `now`)."""
    gap = None if state.last_check is None else mono - state.last_check
    state = replace(state, last_check=mono)
    if gap is not None and gap > lim.max_gap_seconds:
        return WatchResult(Watch.BLIND, f"watch gap {gap:.1f} s > {lim.max_gap_seconds} s",
                           replace(state, breach_checks=0))
    refusal = book_refusal(quote, now, lim, feed_alive)
    if refusal is not None:
        state = replace(state, breach_checks=0)
        since = mono - (state.started if state.last_usable is None else state.last_usable)
        if since > lim.blind_seconds:
            return WatchResult(Watch.BLIND, f"no usable book for {since:.1f} s: {refusal}", state)
        return WatchResult(Watch.HOLD, refusal, state)
    assert quote is not None
    state = replace(state, last_usable=mono, last_usable_bid=quote.bid,
                    last_usable_time=quote.provider_time)
    if quote.bid > stop_price:
        return WatchResult(Watch.HOLD, f"bid {quote.bid} > stop {stop_price}",
                           replace(state, breach_checks=0))
    count = state.breach_checks + 1
    state = replace(state, breach_checks=count)
    if count >= lim.confirm_checks:
        return WatchResult(Watch.BREACH, f"bid {quote.bid} <= stop {stop_price} on {count} "
                           f"checks", state)
    return WatchResult(Watch.HOLD, f"bid {quote.bid} <= stop {stop_price}, {count} of "
                       f"{lim.confirm_checks} checks", state)
