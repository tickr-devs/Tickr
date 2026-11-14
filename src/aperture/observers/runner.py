"""The engine hook: evaluate every observer on the context the active strategy just saw.

`ObserverSet.on_evaluation(context, at)` is called once per active evaluation, after it, with
the same SymbolContext (same completed bars, same quote) and the decision time. Each observer
gets that context WITHOUT the position (observers never hold one), and its decision is
recorded with role `observer` (admission `not_evaluated` for ENTER). Nothing is returned to the
trading path except the list of new failures, which the caller journals and alerts on.

An observer is never allowed to hurt trading:
- any exception from its evaluate(), or a decision that breaks the contract (not a Decision,
  wrong name/version tag, wrong symbol), switches that observer off for the rest of the run;
- an evaluation slower than `[observers] max_evaluation_seconds` is still recorded, then the
  observer is switched off;
- a failed write of the decision log switches every observer off.
Each switch-off is one ObserverFailure with one cause. Nothing is retried or guessed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from aperture.observers.loader import LoadedStrategy
from aperture.observers.log import DecisionLog
from aperture.research.records import M1, Admission, DecisionRecord
from aperture.strategy.base import Decision, Role, SymbolContext, Verdict


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


class ObserverSet:
    def __init__(
        self,
        observers: Sequence[LoadedStrategy],
        log: DecisionLog,
        max_evaluation_seconds: Decimal,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.log = log
        self.max_seconds = float(max_evaluation_seconds)
        self.monotonic = monotonic
        self._enabled = list(observers)
        self.failures: list[ObserverFailure] = []

    @property
    def enabled(self) -> tuple[LoadedStrategy, ...]:
        return tuple(self._enabled)

    def _fail(self, observer: LoadedStrategy, at: datetime, cause: str) -> ObserverFailure:
        failure = ObserverFailure(observer.folder, observer.identity, at, cause)
        self.failures.append(failure)
        self._enabled.remove(observer)
        return failure

    def on_evaluation(self, context: SymbolContext, at: datetime) -> tuple[ObserverFailure, ...]:
        """Evaluate and record every enabled observer; return the failures of this call.

        `at` is the active decision's time; it must not be before the last bar completed
        (ValueError: a caller bug, not an observer fault)."""
        if not context.bars:
            raise ValueError("observers need at least one completed bar")
        bar_start = context.bars[-1].start
        if at < bar_start + M1:
            raise ValueError(f"decision time {at.isoformat()} is before the bar completed")
        shadow = replace(context, position=None)
        new: list[ObserverFailure] = []
        for observer in list(self._enabled):
            started = self.monotonic()
            try:
                decision = observer.strategy.evaluate(shadow)
            except Exception as exc:  # noqa: BLE001 - an observer must never stop trading
                new.append(self._fail(observer, at, f"evaluate raised {type(exc).__name__}: {exc}"))
                continue
            elapsed = self.monotonic() - started
            problem = _contract_problem(observer, context.symbol, decision)
            if problem is not None:
                new.append(self._fail(observer, at, problem))
                continue
            assert isinstance(decision, Decision)
            enter = decision.verdict is Verdict.ENTER
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
                for other in list(self._enabled):
                    new.append(self._fail(other, at, f"decision log write failed: {exc}"))
                return tuple(new)
            if elapsed > self.max_seconds:
                new.append(
                    self._fail(observer, at, f"evaluation took {elapsed:.3f} s, "
                                             f"limit {self.max_seconds} s")
                )
        return tuple(new)
