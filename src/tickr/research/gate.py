"""R23 Step 0: the numeric part of the gate before real money (docs/promotion.md).

Input CSV, one row per closed trade of one model version on its sealed period (the caller picks
the rows; the tool cannot tell which months the model saw). It follows the trades shape of the
`sizing` report (`day`, `session`, `result_pct`) and adds `cost_pct`. Header required, other
columns (such as `outcome` and `p_<name>`) ignored:
  day         trading day, YYYY-MM-DD
  session     `regular` or `extended`
  result_pct  the trade's net result AFTER costs, percent of its notional (signed)
  cost_pct    the costs already taken off result_pct, percent of its notional (0 or more)

The repository has no fee model (the replay and the backtest are gross), so the gate adds no cost
of its own: whoever produced the results applied them, and `cost_pct` states how much, so a gross
file cannot pass unseen. Results are scaled to the two money lines of docs/promotion.md: main
session (`regular` rows) at the position cap, all sessions (every row) at the all-sessions size.

Three numeric criteria, each checked on both lines; a criterion passes only when both pass:
  net        net after costs above 0 on the whole period;
  months     net above 0 in at least K of the last M calendar months (ending with the month of the
             last trade; a month without trades on the line has net 0, not in plus);
  drop_best  net still above 0 after the best X percent of trades by result are removed (the
             count is rounded up, so any X above 0 removes at least one trade).
The other R23 items are not numbers: they are listed as checked by hand and never passed here.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from tickr.research.errors import ResearchError

COLUMNS = ("day", "session", "result_pct", "cost_pct")
SESSIONS = ("regular", "extended")
PERCENT = Decimal(100)  # unit factor: percent to fraction
MONTHS_PER_YEAR = 12  # calendar constant
ZERO = Decimal(0)
MANUAL = ("every feature available live (R33 parity)", "stop chosen", "decision frequency chosen")


class GateRefused(ResearchError):
    """The gate cannot be computed. `cause` is one of the class constants."""

    EMPTY_PERIOD = "empty_period"
    MISSING_COLUMN = "missing_column"
    NOT_FINITE = "not_finite"
    BAD_VALUE = "bad_value"
    TOO_FEW_MONTHS = "too_few_months"

    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(f"gate refused ({cause}): {detail}")
        self.cause = cause


@dataclass(frozen=True)
class Trade:
    day: date
    session: str
    result_pct: Decimal
    cost_pct: Decimal


@dataclass(frozen=True)
class Month:
    month: str  # YYYY-MM
    trades: int
    net: Decimal
    in_plus: bool


@dataclass(frozen=True)
class Line:
    name: str
    position_usd: Decimal
    sessions: str  # "regular" or "all" or "all (regular only: no extended trades)"
    trades: int
    days: int
    days_in_plus: int
    earned: Decimal
    lost: Decimal
    net: Decimal
    costs: Decimal
    months: tuple[Month, ...]
    months_in_plus: int
    months_needed: int
    dropped: int
    net_without_best: Decimal

    @property
    def passed(self) -> dict[str, bool]:
        return {"net": self.net > 0, "months": self.months_in_plus >= self.months_needed,
                "drop_best": self.net_without_best > 0}


@dataclass(frozen=True)
class Gate:
    first_day: date
    last_day: date
    window: tuple[str, ...]
    lines: tuple[Line, ...]
    criteria: dict[str, bool]  # each numeric criterion, passed on both lines

    @property
    def numeric_passed(self) -> bool:
        return all(self.criteria.values())


def _number(path: Path, n: int, column: str, raw: str | None) -> Decimal:
    try:
        value = Decimal((raw or "").strip())
    except InvalidOperation as exc:
        raise GateRefused(GateRefused.BAD_VALUE, f"{path}:{n}: {column} is not a number") from exc
    if not value.is_finite():
        raise GateRefused(GateRefused.NOT_FINITE, f"{path}:{n}: {column} is not finite")
    return value


def load_trades(path: Path) -> list[Trade]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise GateRefused(GateRefused.MISSING_COLUMN, f"{path}: {', '.join(missing)}")
        out = []
        for n, row in enumerate(reader, start=2):
            try:
                day = date.fromisoformat((row["day"] or "").strip())
            except ValueError as exc:
                raise GateRefused(GateRefused.BAD_VALUE, f"{path}:{n}: day is not YYYY-MM-DD") \
                    from exc
            if row["session"] not in SESSIONS:
                raise GateRefused(GateRefused.BAD_VALUE,
                                  f"{path}:{n}: session must be one of {', '.join(SESSIONS)}")
            result = _number(path, n, "result_pct", row["result_pct"])
            cost = _number(path, n, "cost_pct", row["cost_pct"])
            if cost < 0:
                raise GateRefused(GateRefused.BAD_VALUE, f"{path}:{n}: cost_pct is below 0")
            out.append(Trade(day, row["session"], result, cost))
    if not out:
        raise GateRefused(GateRefused.EMPTY_PERIOD, f"{path}: no trades")
    return out


def _month_index(day: date) -> int:
    return day.year * MONTHS_PER_YEAR + day.month - 1


def _month_name(index: int) -> str:
    year, month = divmod(index, MONTHS_PER_YEAR)
    return f"{year:04d}-{month + 1:02d}"


def _line(name: str, sessions: str, usd: Decimal, trades: list[Trade], window: list[int],
          positive_months: int, drop_percent: Decimal) -> Line:
    if not trades:
        raise GateRefused(GateRefused.EMPTY_PERIOD, f"no trades for the {name} line")
    results = [(t.day, usd * t.result_pct / PERCENT) for t in trades]
    per_day: dict[date, Decimal] = defaultdict(Decimal)
    per_month: dict[int, list[Decimal]] = defaultdict(list)
    for day, value in results:
        per_day[day] += value
        per_month[_month_index(day)].append(value)
    earned = sum((v for _, v in results if v > 0), ZERO)
    lost = -sum((v for _, v in results if v < 0), ZERO)
    months = tuple(Month(_month_name(m), len(per_month[m]), sum(per_month[m], ZERO),
                         sum(per_month[m], ZERO) > 0) for m in window)
    dropped = math.ceil(len(results) * drop_percent / PERCENT)
    best = sorted((v for _, v in results), reverse=True)[:dropped]
    return Line(
        name=name, position_usd=usd, sessions=sessions, trades=len(results), days=len(per_day),
        days_in_plus=sum(1 for v in per_day.values() if v > 0), earned=earned, lost=lost,
        net=earned - lost, costs=sum((usd * t.cost_pct / PERCENT for t in trades), ZERO),
        months=months, months_in_plus=sum(1 for m in months if m.in_plus),
        months_needed=positive_months, dropped=dropped,
        net_without_best=earned - lost - sum(best, ZERO),
    )


def gate(trades: list[Trade], cap_usd: Decimal, all_sessions_usd: Decimal, positive_months: int,
         window_months: int, drop_percent: Decimal) -> Gate:
    if not trades:
        raise GateRefused(GateRefused.EMPTY_PERIOD, "no trades")
    for usd in (cap_usd, all_sessions_usd):
        if not usd.is_finite():
            raise GateRefused(GateRefused.NOT_FINITE, f"money line size {usd} is not finite")
        if usd <= 0:
            raise GateRefused(GateRefused.BAD_VALUE, f"money line size {usd} is not above 0")
    first = min(t.day for t in trades)
    last = max(t.day for t in trades)
    span = _month_index(last) - _month_index(first) + 1
    if span < window_months:
        raise GateRefused(GateRefused.TOO_FEW_MONTHS,
                          f"{first} to {last} spans {span} calendar months, the gate needs "
                          f"{window_months}")
    window = list(range(_month_index(last) - window_months + 1, _month_index(last) + 1))
    extended = any(t.session != "regular" for t in trades)
    lines = (
        _line("main_session", "regular", cap_usd, [t for t in trades if t.session == "regular"],
              window, positive_months, drop_percent),
        _line("all_sessions", "all" if extended else "all (regular only: no extended trades)",
              all_sessions_usd, trades, window, positive_months, drop_percent),
    )
    criteria = {name: all(line.passed[name] for line in lines) for name in lines[0].passed}
    return Gate(first, last, tuple(_month_name(m) for m in window), lines, criteria)
