"""Bar-by-bar replay of a strategy over completed M1 bars. Every result is SIMULATED.

Timeline per symbol and NY session. Bar `i` is processed in three steps:

1. An open simulated position is managed on bar `i`: a pending strategy EXIT or the flatten
   time closes it at the bar's open; otherwise a bar whose low reaches the stop closes it at
   the stop, or at the open when the bar opens through the stop (gap).
2. A pending entry may fill on bar `i` if the bar trades inside the decision's price window.
   The fill price is always the TOP of the window plus slippage, never better. If the same
   bar also reaches the stop, the stop is assumed hit after the fill (worst case).
3. The strategy is evaluated with the last `history_sessions` sessions plus bars `0..i` of
   this session in `bars` (bar `i` is now complete), the same sessions in `prior_sessions`,
   and, if enabled, a synthetic quote around bar `i`'s close stamped at the bar's end. An
   ENTER becomes a pending entry for the following bars; an EXIT is executed on bar `i + 1`.

The strategy never sees bar `i + 1` before its decision on bar `i`, so it cannot look ahead.
Every exit pays adverse slippage. R and percent are per simulated share and do not depend on
quantity.

The symbols of one New York date are replayed together, bar by bar in time order (symbols in
name order within a minute). With `rules` (backlog T12), an ENTER the replay's own timing would
take is then admitted by the live bot's platform rules (`tickr.risk.simulated`): the trading
window, one trade per symbol, the position and working-entry limits, the pause after a loss,
the decision's prices, size and the daily loss ceiling, over that day's simulated entries and
trades of every symbol (USD from the strategy's quantity). A refusal is a `rejected` signal
with the admission cause. Without `rules` no platform limit applies.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tickr.calendar import ny_date_of
from tickr.replay.clock import ClockFor, SessionClock
from tickr.replay.errors import ReplayError, StrategyContractError
from tickr.replay.settings import ReplaySettings
from tickr.risk.causes import AdmissionCause
from tickr.risk.simulated import SimulatedBook, SimulationRules
from tickr.strategy.base import Decision, PositionView, Strategy, SymbolContext, Verdict
from tickr.types import Bar, Quote

LABEL = "simulated"
SIMULATED_QUANTITY = Decimal(1)
M1 = timedelta(minutes=1)


class ExitReason(StrEnum):
    STOP = "stop"
    STRATEGY_EXIT = "strategy_exit"
    FLATTEN = "flatten"
    END_OF_DATA = "end_of_data"


class SignalOutcome(StrEnum):
    FILLED = "filled"
    MISSED = "missed"
    REJECTED = "rejected"
    IGNORED = "ignored"


@dataclass(frozen=True)
class SimulatedTrade:
    strategy: str
    version: str
    symbol: str
    session_date: date
    setup: str | None
    signal_bar_start: datetime
    entry_time: datetime
    entry_price: Decimal
    stop: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: ExitReason
    bars_held: int
    quantity: Decimal | None = None
    """The strategy's `Decision.quantity` (None if it gave none); R and percent ignore it."""
    entry_high: Decimal | None = None
    """Top of the decision's price window (the live limit price, before slippage)."""

    @property
    def pnl_usd(self) -> Decimal | None:
        """(exit - entry) x the strategy's quantity, gross of commissions and fees."""
        if self.quantity is None:
            return None
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def risk_per_share(self) -> Decimal:
        return self.entry_price - self.stop

    @property
    def r_multiple(self) -> Decimal:
        return (self.exit_price - self.entry_price) / self.risk_per_share

    @property
    def return_percent(self) -> Decimal:
        return (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass(frozen=True)
class Signal:
    """One ENTER decision and what the simulation did with it."""

    strategy: str
    version: str
    symbol: str
    session_date: date
    bar_start: datetime
    setup: str | None
    entry_low: Decimal | None
    entry_high: Decimal | None
    stop: Decimal | None
    outcome: SignalOutcome
    cause: str
    admission_cause: AdmissionCause | None = None
    """The live admission cause when the platform rules refused the ENTER (`rules`)."""


@dataclass(frozen=True)
class ReplayResult:
    strategy: str
    version: str
    trades: tuple[SimulatedTrade, ...]
    signals: tuple[Signal, ...]
    verdict_counts: Mapping[str, int]
    sessions: int
    bars: int
    rules: str | None = None
    """The platform rules and config values the result was computed under
    (`SimulationRules.label`); None = none applied."""


@dataclass
class _Pending:
    decision: Decision
    bar_start: datetime
    bars_left: int
    book_key: int | None = None


@dataclass
class _Open:
    decision: Decision
    signal_bar_start: datetime
    entry_time: datetime
    entry_price: Decimal
    entry_index: int
    exit_requested: bool = False
    book_key: int | None = None


def _check_decision(strategy: Strategy, symbol: str, decision: Decision) -> None:
    if not isinstance(decision, Decision):
        raise StrategyContractError(f"evaluate returned {type(decision).__name__}, not Decision")
    if not isinstance(decision.verdict, Verdict):
        raise StrategyContractError(f"verdict {decision.verdict!r} is not a Verdict")
    if (decision.strategy, decision.version) != (strategy.name, strategy.version):
        raise StrategyContractError(
            f"decision tagged {decision.strategy} v{decision.version}, "
            f"strategy is {strategy.name} v{strategy.version}"
        )
    if decision.symbol != symbol:
        raise StrategyContractError(f"decision for {decision.symbol} while replaying {symbol}")


def _entry_problem(decision: Decision) -> str | None:
    """Why an ENTER decision cannot be simulated, or None."""
    if decision.rest_bars is not None:
        # A resting limit below the ask fills only on a pullback; simulating it as a
        # marketable entry would book fills the order could not get.
        return "resting limit entries (rest_bars) are not modelled"
    if decision.scale_out is not None:
        # Replay closes the whole simulated position on any EXIT; a partial sale would be
        # booked as a full one.
        return "scale-out (partial exits) is not modelled"
    if decision.entry_high is None:
        return "ENTER without entry_high"
    if decision.stop is None:
        return "ENTER without stop"
    if decision.entry_low is not None and decision.entry_low > decision.entry_high:
        return "entry_low above entry_high"
    if decision.stop >= decision.entry_high:
        return "stop not below entry_high"
    if decision.stop <= 0:
        return "stop not positive"
    return None


class _SessionReplay:
    def __init__(
        self,
        strategy: Strategy,
        symbol: str,
        day: date,
        bars: Sequence[Bar],
        prior: tuple[tuple[Bar, ...], ...],
        settings: ReplaySettings,
        clock: SessionClock,
        book: SimulatedBook | None = None,
    ) -> None:
        self.strategy = strategy
        self.symbol = symbol
        self.day = day
        self.bars = tuple(bars)
        self.prior = prior
        self.history = tuple(b for session in prior for b in session)
        self.settings = settings
        self.session_clock = clock
        self.pending: _Pending | None = None
        self.open: _Open | None = None
        self.trades: list[SimulatedTrade] = []
        self.signals: list[Signal] = []
        self.verdicts: dict[str, int] = {}
        self.book = book
        self.index = 0

    def _signal(
        self, decision: Decision, bar_start: datetime, outcome: SignalOutcome, cause: str,
        admission_cause: AdmissionCause | None = None,
    ) -> None:
        self.signals.append(
            Signal(
                strategy=decision.strategy,
                version=decision.version,
                symbol=self.symbol,
                session_date=self.day,
                bar_start=bar_start,
                setup=decision.setup,
                entry_low=decision.entry_low,
                entry_high=decision.entry_high,
                stop=decision.stop,
                outcome=outcome,
                cause=cause,
                admission_cause=admission_cause,
            )
        )

    def _close(self, index: int, raw_price: Decimal, at: datetime, reason: ExitReason) -> None:
        position = self.open
        assert position is not None and position.decision.stop is not None
        price = raw_price - self.settings.slippage(raw_price)
        if price <= 0:
            raise ReplayError(
                f"{self.symbol} {at.isoformat()}: simulated exit price {price} is not positive"
            )
        self.trades.append(
            SimulatedTrade(
                strategy=position.decision.strategy,
                version=position.decision.version,
                symbol=self.symbol,
                session_date=self.day,
                setup=position.decision.setup,
                signal_bar_start=position.signal_bar_start,
                entry_time=position.entry_time,
                entry_price=position.entry_price,
                stop=position.decision.stop,
                exit_time=at,
                exit_price=price,
                exit_reason=reason,
                bars_held=index - position.entry_index + 1,
                quantity=position.decision.quantity,
                entry_high=position.decision.entry_high,
            )
        )
        self.open = None
        if self.book is not None and position.book_key is not None:
            assert position.decision.quantity is not None  # admission refuses an ENTER without
            self.book.ended(position.book_key, at,
                            (price - position.entry_price) * position.decision.quantity)

    def _missed(self, pending: _Pending, at: datetime, cause: str) -> None:
        self.pending = None
        if self.book is not None and pending.book_key is not None:
            self.book.ended(pending.book_key, at, Decimal(0))
        self._signal(pending.decision, pending.bar_start, SignalOutcome.MISSED, cause)

    def _manage_position(self, index: int, bar: Bar) -> None:
        position = self.open
        if position is None:
            return
        stop = position.decision.stop
        assert stop is not None
        if position.exit_requested:
            self._close(index, bar.open, bar.start, ExitReason.STRATEGY_EXIT)
        elif bar.start >= self.session_clock.flatten:
            self._close(index, bar.open, bar.start, ExitReason.FLATTEN)
        elif bar.low <= stop:
            self._close(index, min(bar.open, stop), bar.start, ExitReason.STOP)

    def _try_fill(self, index: int, bar: Bar) -> None:
        pending = self.pending
        if pending is None:
            return
        decision = pending.decision
        top = decision.entry_high
        stop = decision.stop
        assert top is not None and stop is not None
        if bar.start >= self.session_clock.last_entry:
            self._missed(pending, bar.start, "last entry time")
            return
        bottom = decision.entry_low if decision.entry_low is not None else bar.low
        if bar.low <= top and bar.high >= bottom:
            self.pending = None
            self.open = _Open(
                decision=decision,
                signal_bar_start=pending.bar_start,
                entry_time=bar.start,
                entry_price=top + self.settings.slippage(top),
                entry_index=index,
                book_key=pending.book_key,
            )
            if self.book is not None and pending.book_key is not None:
                self.book.filled(pending.book_key, bar.start)
            self._signal(decision, pending.bar_start, SignalOutcome.FILLED, "")
            if bar.low <= stop:
                self._close(index, stop, bar.start, ExitReason.STOP)
            return
        pending.bars_left -= 1
        if pending.bars_left == 0:
            self._missed(pending, bar.start + M1, "price outside window")

    def _quote(self, bar: Bar) -> Quote | None:
        """Synthetic quote at the end of the completed bar, or None when disabled."""
        settings = self.settings
        if not settings.synthesize_quote:
            return None
        half = settings.synthetic_spread(bar.close) / 2
        bid, ask = bar.close - half, bar.close + half
        if bid <= 0:
            raise ReplayError(
                f"{self.symbol} {bar.start.isoformat()}: synthetic bid {bid} is not positive"
            )
        end = bar.start + M1
        size = settings.synthetic_quote_size_shares
        return Quote(self.symbol, bid, ask, size, size, end, end)

    def _evaluate(self, index: int, bar: Bar) -> None:
        view = None
        if self.open is not None:
            assert self.open.decision.stop is not None
            view = PositionView(
                entry_price=self.open.entry_price,
                quantity=SIMULATED_QUANTITY,
                stop=self.open.decision.stop,
            )
        context = SymbolContext(
            symbol=self.symbol,
            bars=self.history + self.bars[: index + 1],
            prior_sessions=self.prior,
            quote=self._quote(bar),
            position=view,
            # Replay input is the recorded session itself: its bars are the whole session.
            session_open=self.session_clock.regular_open,
        )
        decision = self.strategy.evaluate(context)
        _check_decision(self.strategy, self.symbol, decision)
        self.verdicts[decision.verdict.value] = self.verdicts.get(decision.verdict.value, 0) + 1
        if decision.verdict is Verdict.EXIT and self.open is not None:
            self.open.exit_requested = True
        elif decision.verdict is Verdict.ENTER:
            self._enter(decision, bar)

    def _enter(self, decision: Decision, bar: Bar) -> None:
        if self.open is not None:
            self._signal(decision, bar.start, SignalOutcome.IGNORED, "position open")
            return
        if self.pending is not None:
            self._signal(decision, bar.start, SignalOutcome.IGNORED, "entry pending")
            return
        refused = self.session_clock.entries_refused
        if refused is not None:
            self._signal(decision, bar.start, SignalOutcome.REJECTED, refused)
            return
        if bar.start < self.session_clock.regular_open:
            self._signal(decision, bar.start, SignalOutcome.REJECTED, "before regular open")
            return
        if bar.start + M1 >= self.session_clock.last_entry:
            self._signal(decision, bar.start, SignalOutcome.REJECTED, "after last entry time")
            return
        problem = _entry_problem(decision)
        if problem is not None:
            self._signal(decision, bar.start, SignalOutcome.REJECTED, problem)
            return
        key = None
        if self.book is not None:
            now = bar.start + M1
            admission = self.book.admit(decision, now, self.session_clock.market_day)
            if not admission.ok:
                assert admission.cause is not None
                self._signal(decision, bar.start, SignalOutcome.REJECTED,
                             f"{admission.cause.value}: {admission.detail}", admission.cause)
                return
            key = self.book.entered(self.symbol, now, admission)
        self.pending = _Pending(decision, bar.start, self.settings.entry_wait_bars, key)

    def step(self) -> None:
        """Process the next bar."""
        index, bar = self.index, self.bars[self.index]
        self._manage_position(index, bar)
        self._try_fill(index, bar)
        self._evaluate(index, bar)
        self.index += 1

    def finish(self) -> None:
        last = len(self.bars) - 1
        if self.open is not None:
            self._close(last, self.bars[last].close, self.bars[last].start, ExitReason.END_OF_DATA)
        if self.pending is not None:
            self._missed(self.pending, self.bars[last].start + M1, "no further bars")

    def run(self) -> None:
        while self.index < len(self.bars):
            self.step()
        self.finish()


def _run_together(runs: Sequence[_SessionReplay]) -> None:
    """Every bar of `runs` (one date, one per symbol) in time order; within a minute, in the
    order of `runs`. Each run's own bars keep their order."""
    queue = [(run.bars[0].start, k) for k, run in enumerate(runs) if run.bars]
    heapq.heapify(queue)
    while queue:
        _, k = heapq.heappop(queue)
        run = runs[k]
        run.step()
        if run.index < len(run.bars):
            heapq.heappush(queue, (run.bars[run.index].start, k))
    for run in runs:
        if run.bars:
            run.finish()


def refused_by_rules(signals: Sequence[Signal]) -> dict[str, int]:
    """How many ENTERs the platform rules refused, by admission cause (name order)."""
    counts: dict[str, int] = {}
    for signal in signals:
        if signal.admission_cause is not None:
            cause = signal.admission_cause.value
            counts[cause] = counts.get(cause, 0) + 1
    return dict(sorted(counts.items()))


def split_sessions(bars: Sequence[Bar]) -> list[tuple[date, tuple[Bar, ...]]]:
    """Group one symbol's bars (oldest first) by New York session date."""
    sessions: list[tuple[date, list[Bar]]] = []
    for bar in bars:
        day = ny_date_of(bar.start)
        if not sessions or sessions[-1][0] != day:
            sessions.append((day, []))
        sessions[-1][1].append(bar)
    return [(day, tuple(day_bars)) for day, day_bars in sessions]


def replay(
    create_strategy: Callable[[], Strategy],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: ReplaySettings,
    clock_for: ClockFor,
    rules: SimulationRules | None = None,
    book: SimulatedBook | None = None,
) -> ReplayResult:
    """Replay every symbol through a fresh strategy instance; `clock_for` gives each day's clock.
    `rules`: the platform rules every ENTER must pass (None: no platform limit). `book`: the
    caller's simulated day to admit against and record in, instead of a fresh one (one date
    only)."""
    strategies: dict[str, Strategy] = {}
    sessions: dict[str, dict[date, tuple[Bar, ...]]] = {}
    name = version = None
    for symbol in sorted(bars_by_symbol):
        strategy = create_strategy()
        if name is None:
            name, version = strategy.name, strategy.version
        elif (strategy.name, strategy.version) != (name, version):
            raise StrategyContractError("strategy factory returned different name/version")
        strategies[symbol] = strategy
        sessions[symbol] = dict(split_sessions(bars_by_symbol[symbol]))
    if name is None or version is None:
        raise ReplayError("no bars to replay")
    trades: dict[str, list[SimulatedTrade]] = {symbol: [] for symbol in strategies}
    signals: dict[str, list[Signal]] = {symbol: [] for symbol in strategies}
    verdicts: dict[str, int] = {}
    prior: dict[str, list[tuple[Bar, ...]]] = {symbol: [] for symbol in strategies}
    session_count = 0
    bar_count = 0
    dates = sorted({day for days in sessions.values() for day in days})
    if book is not None and (rules is None or len(dates) != 1):
        raise ReplayError("a caller's simulated day needs rules and bars of exactly one date")
    for day in dates:
        clock = clock_for(day)
        day_book = book if book is not None else None if rules is None else SimulatedBook(rules)
        runs = [_SessionReplay(strategies[symbol], symbol, day, days[day],
                               tuple(prior[symbol]), settings, clock, day_book)
                for symbol, days in sessions.items() if day in days]
        _run_together(runs)
        for run in runs:
            trades[run.symbol].extend(run.trades)
            signals[run.symbol].extend(run.signals)
            for verdict, count in run.verdicts.items():
                verdicts[verdict] = verdicts.get(verdict, 0) + count
            history = prior[run.symbol]
            history.append(run.bars)
            if len(history) > settings.history_sessions:
                history.pop(0)
            session_count += 1
            bar_count += len(run.bars)
    return ReplayResult(
        strategy=name,
        version=version,
        trades=tuple(t for symbol in strategies for t in trades[symbol]),
        signals=tuple(s for symbol in strategies for s in signals[symbol]),
        verdict_counts=dict(sorted(verdicts.items())),
        sessions=session_count,
        bars=bar_count,
        rules=None if rules is None else rules.label(),
    )
