"""The engine hook: evaluate every observer on the context the active strategy just saw.

`ObserverSet.on_evaluation(context, at)` is called once per active evaluation, after it, with
the same SymbolContext and the decision time. It never evaluates anything on the caller's
thread: it freezes the context (position removed: observers never hold one; no loss budget;
only a quote admission would accept) and puts it on a bounded queue, without blocking. A worker
evaluates the observers in order and appends each decision to the decision log with role
`observer` (admission `not_evaluated` for ENTER). So a slow or hung observer can never delay
Detail polls, stops or exits on the trading thread.

The worker runs in a child process the bot owns (`ProcessSpec`), so observer evaluation never
competes with the trading thread for the interpreter lock; tests may run the same worker on a
thread instead. To keep what crosses to the child small, the caller sends per symbol only the
bars it has not sent yet (all of them again when an earlier one changed: a provider revision)
and the prior sessions only when they changed (once a day). The child keeps them, appends its
records to the same decision file with whole-line appends (`DecisionLog`), and reports back
which observers it switched off and which symbols each observer is watching.

An observer is never allowed to hurt trading:
- queue full: that evaluation is dropped and returned as an ObserverFailure (`DROPPED`); the
  observers go on with the next one (the caller counts drops); nothing waits;
- on the worker, any exception from evaluate(), or a decision that breaks the contract (not a
  Decision, wrong name/version tag, wrong symbol), switches that observer off for the run;
- an evaluation slower than `[observers] max_evaluation_seconds` is still recorded, then the
  observer is switched off (a hung one keeps the worker busy; the queue then overflows and
  further evaluations are dropped, still without blocking);
- a failed decision log write switches every observer off;
- a worker process that ends on its own (`alive` false) is reported once as a failure of all
  observers; the caller then stops them.
Worker failures are collected; the caller takes them with `take_failures()` (for example on
each engine step) and journals and alerts. Each failure has one cause; nothing is retried.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import signal
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tickr.observers.loader import LoadedStrategy, load_strategy, parse_modules
from tickr.observers.log import DecisionLog
from tickr.research.records import M1, Admission, DecisionRecord
from tickr.strategy.base import Decision, Role, SymbolContext, Verdict
from tickr.types import Quote

ALL_OBSERVERS = "all observers"
DROPPED = "queue full"
"""Cause prefix of a dropped evaluation (the queue was full); the observers go on."""
_STOP = "stop"
_PARENT_CHECK_SECONDS = 1.0  # protocol: how often an idle child checks its parent is alive


@dataclass(frozen=True)
class ObserverFailure:
    folder: str
    identity: str
    at: datetime
    cause: str


def _contract_problem(observer: LoadedStrategy, symbol: str, decision: object) -> str | None:
    if not isinstance(decision, Decision):
        return f"evaluate returned {type(decision).__name__}, not Decision"
    if not isinstance(decision.verdict, Verdict):
        return f"verdict {decision.verdict!r} is not a Verdict"
    strategy = observer.strategy
    if (decision.strategy, decision.version) != (strategy.name, strategy.version):
        return f"decision tagged {decision.strategy} v{decision.version}"
    if decision.symbol != symbol:
        return f"decision for {decision.symbol} while evaluating {symbol}"
    return None


def _usable_quote(quote: Quote | None, at: datetime, max_age: timedelta,
                  max_skew: timedelta) -> Quote | None:
    """The quote when entry admission would accept its times at `at`: aware provider and
    receipt times, received no later than `at`, provider time at most `max_skew` ahead and at
    most `max_age` old. Otherwise None."""
    if quote is None:
        return None
    times = (quote.provider_time, quote.receipt_time)
    if any(t.tzinfo is None or t.utcoffset() is None for t in times):
        return None
    if quote.receipt_time > at:
        return None
    age = at - quote.provider_time
    return quote if -max_skew <= age <= max_age else None


def freeze(context: SymbolContext, at: datetime, max_quote_age: timedelta,
           max_clock_skew: timedelta) -> SymbolContext:
    """An immutable copy for the worker: tuples of bars, no position, no daily loss budget
    (that room is the active strategy's; an observer sizes as if it traded alone), and no
    quote whose times entry admission would refuse at `at` (stale, from the future, naive)."""
    quote = _usable_quote(context.quote, at, max_quote_age, max_clock_skew)
    return replace(
        context,
        bars=tuple(context.bars),
        prior_sessions=tuple(tuple(session) for session in context.prior_sessions),
        position=None,
        loss_budget_usd=None,
        quote=quote,
    )


@dataclass(frozen=True)
class ProcessSpec:
    """How the child process builds the observers again: the same folders under `root`, the
    same `[observers] strategy_modules`, and the decision log folder."""
    root: Path
    modules: tuple[str, ...]
    decisions: Path


def _same(sent: Sequence[Any], new: Sequence[Any]) -> bool:
    """`new` starts with exactly what was `sent` (the same objects, or equal ones)."""
    return len(new) >= len(sent) and all(a is b or a == b for a, b in zip(sent, new, strict=False))


class _Encoder:
    """Caller side: what of each symbol's bars and prior sessions the worker already has."""

    def __init__(self) -> None:
        self._bars: dict[str, tuple[Any, ...]] = {}
        self._prior: dict[str, tuple[tuple[Any, ...], ...]] = {}

    def message(self, context: SymbolContext, at: datetime) -> tuple[tuple[Any, ...],
                                                                    Callable[[], None]]:
        """(message, commit): commit only once the message was queued."""
        symbol = context.symbol
        bars = tuple(context.bars)
        prior = tuple(tuple(s) for s in context.prior_sessions)
        sent_bars = self._bars.get(symbol)
        sent_prior = self._prior.get(symbol)
        if sent_bars is not None and _same(sent_bars, bars):
            base, tail = len(sent_bars), bars[len(sent_bars):]
        else:
            base, tail = 0, bars
        same_prior = sent_prior is not None and len(sent_prior) == len(prior) and all(
            len(a) == len(b) and _same(a, b) for a, b in zip(sent_prior, prior, strict=True))
        message = ("eval", symbol, at, context.quote, None if same_prior else prior, base, tail)

        def commit() -> None:
            self._bars[symbol] = bars
            self._prior[symbol] = prior

        return message, commit


class _Worker:
    """Worker side: rebuilds each context from what was sent, evaluates every enabled observer,
    writes the records and reports events: ("off", folder, identity, at, cause),
    ("watch", folder, symbol, bar_start), ("unwatch", folder, symbol)."""

    def __init__(self, observers: Sequence[LoadedStrategy], log: DecisionLog,
                 max_seconds: float, monotonic: Callable[[], float]) -> None:
        self.enabled = list(observers)
        self.log = log
        self.max_seconds = max_seconds
        self.monotonic = monotonic
        self._bars: dict[str, list[Any]] = {}
        self._prior: dict[str, tuple[tuple[Any, ...], ...]] = {}

    def run(self, inbox: Any, outbox: Any, parent_alive: Callable[[], bool] | None) -> None:
        while True:
            try:
                item = inbox.get(timeout=_PARENT_CHECK_SECONDS) if parent_alive else inbox.get()
            except queue.Empty:
                if parent_alive is not None and not parent_alive():
                    return
                continue
            if item == _STOP:
                self.log.close()
                outbox.put(("stopped",))
                return
            events: list[tuple[Any, ...]] = []
            try:
                self._handle(item, events)
            except Exception as exc:  # noqa: BLE001 - the worker itself must never die
                at = item[2] if isinstance(item, tuple) and len(item) > 2 else datetime.now(UTC)
                for observer in tuple(self.enabled):
                    self._off(observer, at, f"observer worker failed: {type(exc).__name__}: "
                              f"{exc}", events)
            finally:
                outbox.put(("done", events))

    def _handle(self, item: tuple[Any, ...], events: list[tuple[Any, ...]]) -> None:
        _, symbol, at, quote, prior, base, tail = item
        bars = self._bars.setdefault(symbol, [])
        if base > len(bars):
            raise ValueError(f"{symbol}: {base} bars said to be held, {len(bars)} held")
        del bars[base:]
        bars.extend(tail)
        if prior is not None:
            self._prior[symbol] = prior
        context = SymbolContext(symbol, tuple(bars), self._prior.get(symbol, ()), quote)
        self._evaluate(context, at, events)

    def _off(self, observer: LoadedStrategy, at: datetime, cause: str,
             events: list[tuple[Any, ...]]) -> None:
        self.enabled.remove(observer)
        events.append(("off", observer.folder, observer.identity, at, cause))

    def _evaluate(self, context: SymbolContext, at: datetime,
                  events: list[tuple[Any, ...]]) -> None:
        bar_start = context.bars[-1].start
        for observer in tuple(self.enabled):
            started = self.monotonic()
            try:
                decision = observer.strategy.evaluate(context)
            except Exception as exc:  # an observer must never stop the worker
                self._off(observer, at, f"evaluate raised {type(exc).__name__}: {exc}", events)
                continue
            elapsed = self.monotonic() - started
            problem = _contract_problem(observer, context.symbol, decision)
            if problem is not None:
                self._off(observer, at, problem, events)
                continue
            assert isinstance(decision, Decision)
            enter = decision.verdict is Verdict.ENTER
            if enter or decision.verdict is Verdict.WATCH:
                events.append(("watch", observer.folder, context.symbol, bar_start))
            else:
                events.append(("unwatch", observer.folder, context.symbol))
            record = DecisionRecord(
                time=at,
                bar_start=bar_start,
                role=Role.OBSERVER,
                decision=decision,
                admission=Admission.NOT_EVALUATED if enter else Admission.NOT_APPLICABLE,
            )
            try:
                self.log.append(record)
            except (OSError, ValueError) as exc:
                for other in tuple(self.enabled):
                    self._off(other, at, f"decision log write failed: {exc}", events)
                return
            if elapsed > self.max_seconds:
                self._off(observer, at,
                          f"evaluation took {elapsed:.3f} s, limit {self.max_seconds} s", events)


def _child_main(spec: ProcessSpec, folders: tuple[str, ...], max_seconds: float,
                parent_pid: int, inbox: Any, outbox: Any) -> None:
    """The observer process: build the observers from their folders and serve the queue until
    told to stop or the parent is gone. Ctrl-C in a terminal is the parent's to handle."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    modules = parse_modules(spec.modules)
    loaded = []
    for folder in folders:
        try:
            loaded.append(load_strategy(spec.root, folder, modules))
        except Exception as exc:  # noqa: BLE001 - reported to the parent, never raised there
            outbox.put(("events", [("off", folder, folder, datetime.now(UTC),
                                    f"not loaded in the observer process: "
                                    f"{type(exc).__name__}: {exc}")]))
    worker = _Worker(loaded, DecisionLog(spec.decisions), max_seconds, time.monotonic)
    worker.run(inbox, outbox, lambda: os.getppid() == parent_pid)


class ObserverSet:
    def __init__(
        self,
        observers: Sequence[LoadedStrategy],
        log: DecisionLog,
        max_evaluation_seconds: Decimal,
        queue_size: int,
        max_quote_age_seconds: Decimal,
        max_clock_skew_seconds: Decimal,
        monotonic: Callable[[], float] = time.monotonic,
        *,
        process: ProcessSpec | None = None,
    ) -> None:
        """`process`: run the worker in a child process built from the observers' folders;
        without it the worker is a thread of this process using `observers` as given."""
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        self.log = log
        self.max_seconds = float(max_evaluation_seconds)
        self.max_quote_age = timedelta(seconds=float(max_quote_age_seconds))
        self.max_clock_skew = timedelta(seconds=float(max_clock_skew_seconds))
        self.queue_size = queue_size
        self._enabled = list(observers)
        self._lock = threading.Lock()
        self._failures: list[ObserverFailure] = []
        # observer folder -> symbol -> bar of its latest WATCH/ENTER
        self._watching: dict[str, dict[str, datetime]] = {}
        self._encoder = _Encoder()
        self._sent = 0
        self._done = 0
        self._dead_reported = False
        self._process: Any = None
        self._thread: threading.Thread | None = None
        self._inbox: Any = None
        self._outbox: Any = None
        if not self._enabled:
            return
        if process is None:
            self._inbox, self._outbox = queue.Queue(maxsize=queue_size), queue.Queue()
            worker = _Worker(self._enabled, log, self.max_seconds, monotonic)
            self._thread = threading.Thread(target=worker.run, name="observers", daemon=True,
                                            args=(self._inbox, self._outbox, None))
            self._thread.start()
        else:
            context = multiprocessing.get_context("spawn")
            self._inbox, self._outbox = context.Queue(maxsize=queue_size), context.Queue()
            self._process = context.Process(
                target=_child_main, name="tickr-observers", daemon=True,
                args=(process, tuple(o.folder for o in self._enabled), self.max_seconds,
                      os.getpid(), self._inbox, self._outbox))
            self._process.start()

    @property
    def enabled(self) -> tuple[LoadedStrategy, ...]:
        self._drain()
        with self._lock:
            return tuple(self._enabled)

    @property
    def alive(self) -> bool:
        """False once a worker process ended on its own (never for the thread worker)."""
        return self._process is None or self._process.is_alive()

    # ----------------------------------------------------------- caller (trading) thread

    def on_evaluation(self, context: SymbolContext, at: datetime) -> tuple[ObserverFailure, ...]:
        """Queue one evaluation for every enabled observer; never blocks, never evaluates.

        Returns a failure only when the queue is full (the evaluation is dropped, `DROPPED`).
        `at` before the end of the last bar is a caller bug (ValueError)."""
        if not context.bars:
            raise ValueError("observers need at least one completed bar")
        if at < context.bars[-1].start + M1:
            raise ValueError(f"decision time {at.isoformat()} is before the bar completed")
        if self._inbox is None or not self.enabled or not self.alive:
            return ()
        frozen = freeze(context, at, self.max_quote_age, self.max_clock_skew)
        message, commit = self._encoder.message(frozen, at)
        try:
            self._inbox.put_nowait(message)
        except queue.Full:
            failure = ObserverFailure(
                ALL_OBSERVERS, ALL_OBSERVERS, at,
                f"{DROPPED} ({self.queue_size}): evaluation of {context.symbol} dropped",
            )
            with self._lock:
                self._failures.append(failure)
            return (failure,)
        commit()
        with self._lock:
            self._sent += 1
        return ()

    def take_failures(self) -> tuple[ObserverFailure, ...]:
        """Failures recorded since the last call (worker, overflow and a worker process that
        ended), oldest first."""
        self._drain()
        if not self.alive and not self._dead_reported:
            self._dead_reported = True
            code = self._process.exitcode
            with self._lock:
                self._enabled.clear()
                self._failures.append(ObserverFailure(
                    ALL_OBSERVERS, ALL_OBSERVERS, datetime.now(UTC),
                    f"observer process ended (exit code {code})"))
        with self._lock:
            taken = tuple(self._failures)
            self._failures.clear()
        return taken

    def watching(self, now: datetime, window: timedelta) -> frozenset[str]:
        """Symbols whose latest decision by an enabled observer was WATCH or ENTER on a bar
        that started at most `window` before `now`: the observers' own quote wishes, so the
        platform can subscribe them below the active strategy's. A symbol no longer evaluated,
        or every symbol once evaluation stops (after the close), drops out."""
        self._drain()
        since = now - window
        with self._lock:
            enabled = {o.folder for o in self._enabled}
            return frozenset(s for folder, symbols in self._watching.items()
                             if folder in enabled
                             for s, bar in symbols.items() if bar >= since)

    def wait_idle(self, timeout: float) -> bool:
        """True once every queued evaluation is done (tests, orderly shutdown)."""
        deadline = time.monotonic() + timeout
        while True:
            self._drain()
            with self._lock:
                if self._done >= self._sent:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.alive:
                return False
            try:
                item = self._outbox.get(timeout=min(remaining, _PARENT_CHECK_SECONDS))
            except queue.Empty:
                continue
            self._apply(item)

    def close(self, timeout: float) -> bool:
        """Stop the worker after the queued work; False if it did not finish in time
        (a hung observer: a thread is then abandoned, a process terminated). Closes the log
        when done."""
        if self._inbox is None:
            self.log.close()
            return True
        deadline = time.monotonic() + timeout
        try:
            self._inbox.put(_STOP, timeout=timeout)
        except queue.Full:
            self._abandon()
            return False
        if self._thread is not None:
            self._thread.join(max(deadline - time.monotonic(), 0))
            finished = not self._thread.is_alive()
        else:
            self._process.join(max(deadline - time.monotonic(), 0))
            finished = not self._process.is_alive()
        self._drain()
        if not finished:
            self._abandon()
            return False
        self.log.close()
        return True

    def _abandon(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.terminate()

    def _drain(self) -> None:
        if self._outbox is None:
            return
        while True:
            try:
                item = self._outbox.get_nowait()
            except queue.Empty:
                return
            self._apply(item)

    def _apply(self, item: tuple[Any, ...]) -> None:
        """("done", events) after each queued evaluation, ("events", events) on their own."""
        if item[0] not in ("done", "events"):
            return
        with self._lock:
            if item[0] == "done":
                self._done += 1
            for event in item[1]:
                if event[0] == "off":
                    _, folder, identity, at, cause = event
                    self._enabled = [o for o in self._enabled if o.folder != folder]
                    self._failures.append(ObserverFailure(folder, identity, at, cause))
                elif event[0] == "watch":
                    self._watching.setdefault(event[1], {})[event[2]] = event[3]
                else:
                    self._watching.get(event[1], {}).pop(event[2], None)
