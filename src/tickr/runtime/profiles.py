"""Profile recorder: the instrument profile of every screener instrument, captured as read.

The owner wants to know, for later research on shorts, whether each listed stock could be
shorted and on what terms at the time (2026-11-16): the profile row says `shortable`,
`easy_to_borrow`, `marginable` and the margin requirements. Webull states no borrow quantity
(docs/webull-facts.md), so these flags are all there is.

Each screener round (a discovery pass, or the pre-market and post-market recorder) hands its
rows here. Due are:
- every instrument not asked yet that New York day;
- again, once `[market_data] profile_refresh_seconds` have passed since it was last asked, the
  losers and the gainers whose move from the previous close (`price` / `pre_close` of the
  screener row) is at least `profile_refresh_gainer_move_percent`: the stocks whose borrow
  terms may change during the day.

At most `profile_max_requests_per_round` profile requests a round, each leaving
`profile_reserve_requests` of the route free for discovery's security-type lookups. Due
instruments not read (budget or a failed read) are written to `events.jsonl` as
`profiles-skipped` with the cause, and stay due for the next round. A row whose price or
previous close is missing or not a number is never a "big gainer" (no guess), but is still
captured when first listed.

Only reads market data; nothing here decides, places or asks anything of the broker's orders.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from tickr.broker.base import BrokerError, RateLimited
from tickr.calendar import ny_date_of
from tickr.config import Config
from tickr.data.base import MarketData, MarketDataError, ScreenerKind, ScreenerRow
from tickr.data.capture import PROFILES_SKIPPED, CaptureWriter
from tickr.data.discovery import SKIPPED_BUDGET

PERCENT = Decimal(100)
PRICE_FIELD = "price"  # screener row: last price, string (docs/webull-facts.md)
PREVIOUS_CLOSE_FIELD = "pre_close"  # screener row: previous close, string
SYMBOL_SEPARATOR = ","  # the `symbols` of a profiles-skipped event


def move_percent(row: ScreenerRow) -> Decimal | None:
    """The row's move from the previous close in percent, or None when not stated."""
    try:
        price = Decimal(row.fields[PRICE_FIELD])
        previous = Decimal(row.fields[PREVIOUS_CLOSE_FIELD])
    except (KeyError, InvalidOperation):
        return None
    if not previous.is_finite() or not price.is_finite() or previous <= 0:
        return None
    return (price - previous) / previous * PERCENT


class ProfileRecorder:
    """Thread safe: the discovery thread and the loop (outside the regular session) both call
    `record`; one round runs at a time."""

    def __init__(self, config: Config, source: MarketData, capture: CaptureWriter | None,
                 ) -> None:
        md = config.market_data
        self.refresh = timedelta(seconds=float(md.profile_refresh_seconds))
        self.gainer_move = Decimal(md.profile_refresh_gainer_move_percent)
        self.max_requests = md.profile_max_requests_per_round
        self.reserve = md.profile_reserve_requests
        self.chunk = md.profile_symbols_per_request
        self.source = source
        self.capture = capture
        self._lock = threading.Lock()
        self._day: date | None = None
        # instrument_id -> when last asked that day (a row the provider did not return is not
        # asked again before the refresh time either: no request spent every round on it).
        self._asked: dict[str, datetime] = {}

    def due(self, rows: Sequence[ScreenerRow], now: datetime) -> list[str]:
        """The symbols to read this round, first-listed before refreshes, in row order."""
        first: list[str] = []
        again: list[str] = []
        for row in rows:
            last = self._asked.get(row.instrument_id)
            if last is None:
                first.append(row.symbol)
            elif now - last >= self.refresh and self._watched(row):
                again.append(row.symbol)
        return list(dict.fromkeys(first + again))

    def _watched(self, row: ScreenerRow) -> bool:
        if row.kind is ScreenerKind.LOSERS:
            return True
        move = move_percent(row) if row.kind is ScreenerKind.GAINERS else None
        return move is not None and move >= self.gainer_move

    def record(self, rows: Sequence[ScreenerRow], source: MarketData | None,
               now: datetime) -> None:
        """One round for `rows` (the reads go through `source`, or the recorder's own)."""
        if self.capture is None or not rows:
            return
        reader = self.source if source is None else source
        with self._lock:
            today = ny_date_of(now)
            if self._day != today:
                self._day, self._asked = today, {}
            symbols = self.due(rows, now)
            limit = self.max_requests * self.chunk
            asked, later = symbols[:limit], symbols[limit:]
            wanted = {r.instrument_id: r.symbol for r in rows}
            for start in range(0, len(asked), self.chunk):
                chunk = asked[start:start + self.chunk]
                try:
                    got = reader.profiles(chunk, budget_reserve=self.reserve)
                except (RateLimited, MarketDataError, BrokerError) as exc:
                    cause = SKIPPED_BUDGET if isinstance(exc, RateLimited) else type(exc).__name__
                    self._skipped(asked[start:] + later, cause, now)
                    return
                # Only the listed instruments: a stale second row of a symbol is not theirs.
                kept = [p for p in got if wanted.get(p.instrument_id) == p.symbol]
                self.capture.profiles(kept, reader.provider, now)
                for instrument_id, symbol in wanted.items():
                    if symbol in chunk:
                        self._asked[instrument_id] = now
            if later:
                self._skipped(later, SKIPPED_BUDGET, now)

    def _skipped(self, symbols: Sequence[str], cause: str, now: datetime) -> None:
        if self.capture is not None and symbols:
            self.capture.event(PROFILES_SKIPPED, now, symbols=SYMBOL_SEPARATOR.join(symbols),
                               cause=cause)
