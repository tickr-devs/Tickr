"""Per-variant evidence over a date range, and the missed "trade of the day". SIMULATED.

Input: recorded decisions (active and observers, any number of New York days) and the captured
bars of those days. Nothing is sent or written anywhere but the output files.

Each New York session day is measured alone by `observer_report.report`, so every ENTER is
simulated exactly as the daily `observers` report does (whatif fill model, one position per
symbol, an observer's repeated signal skipped). Per variant (strategy, version, role) and over
the days in range:

- decisions, ENTERs, simulated trades, wins and losses;
- earned on winners, lost on losers (zero or below) and net, in gross USD (trades with a
  recorded whole quantity) and in R (every trade); trades without USD are counted;
- days with a trade, days in plus and days in minus by the day's net USD.

The missed move of a day ("trade of the day"): among the symbols with a recorded decision that
day, minus every symbol any variant traded that day (a filled simulated trade, or an active
ENTER admission took), the biggest rise from a low to a later high, in percent of the low. The
low is the lowest low of the bars from the regular open until the last entry time (a move that
starts later could not be entered); the high is the highest high after it, up to the flatten
time. For every variant with records that day: its decisions on that symbol from the regular
open through the peak bar and why none became a trade (first failed check of an IGNORE or
WATCH, admission's refusal cause, or the simulated ENTER's outcome and cause).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from tickr.calendar import TIMEZONE_NAME
from tickr.replay.clock import ClockFor
from tickr.replay.settings import ReplaySettings
from tickr.research import observer_report
from tickr.research.errors import ResearchError
from tickr.research.records import Admission, DecisionRecord
from tickr.strategy.base import Verdict
from tickr.types import Bar

LABEL = "simulated"
HUNDRED = Decimal(100)
ZERO = Decimal(0)
NOT_EVALUATED = "no decision on the symbol before the peak"

Identity = observer_report.Identity


@dataclass
class VariantDay:
    """One variant's simulated result on one session day."""

    day: date
    decisions: int = 0
    enters: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    earned_usd: Decimal = ZERO
    lost_usd: Decimal = ZERO
    earned_r: Decimal = ZERO
    lost_r: Decimal = ZERO
    trades_without_usd: int = 0

    @property
    def net_usd(self) -> Decimal:
        return self.earned_usd + self.lost_usd

    @property
    def net_r(self) -> Decimal:
        return self.earned_r + self.lost_r


@dataclass
class VariantEvidence:
    strategy: str
    version: str
    role: str
    days: list[VariantDay] = field(default_factory=list)

    @property
    def days_with_trades(self) -> int:
        return sum(1 for d in self.days if d.trades)

    @property
    def days_in_plus(self) -> int:
        return sum(1 for d in self.days if d.net_usd > 0)

    @property
    def days_in_minus(self) -> int:
        return sum(1 for d in self.days if d.net_usd < 0)


@dataclass(frozen=True)
class VariantReason:
    """Why one variant did not trade the missed move: reason -> count."""

    strategy: str
    version: str
    role: str
    decisions: int
    reasons: dict[str, int]


@dataclass(frozen=True)
class MissedMove:
    day: date
    symbol: str
    start: datetime
    """Start of the bar holding the move's low."""
    peak: datetime
    """Start of the bar holding the move's high."""
    low: Decimal
    high: Decimal
    move_percent: Decimal
    variants: tuple[VariantReason, ...]


@dataclass(frozen=True)
class VariantsReport:
    first_day: date
    last_day: date
    variants: tuple[VariantEvidence, ...]
    missed: tuple[MissedMove, ...]
    days_without_missed_move: tuple[date, ...]


def _key(record: DecisionRecord) -> Identity:
    return (record.decision.strategy, record.decision.version, record.role.value)


def _rise(bars: Sequence[Bar], start: datetime, last_entry: datetime,
          flatten: datetime) -> tuple[Bar, Bar] | None:
    """(low bar, high bar) of the biggest rise in percent, or None without one."""
    low: Bar | None = None
    best: tuple[Decimal, Bar, Bar] | None = None
    for bar in bars:
        if not start <= bar.start < flatten:
            continue
        if low is not None and bar.high > low.low:
            rise = (bar.high - low.low) / low.low
            if best is None or rise > best[0]:
                best = (rise, low, bar)
        if bar.start < last_entry and (low is None or bar.low < low.low):
            low = bar
    return None if best is None else (best[1], best[2])


def _reasons(records: Sequence[DecisionRecord],
             simulated: Mapping[tuple[str, datetime], observer_report.SimulatedEnter]
             ) -> Counter[str]:
    reasons: Counter[str] = Counter()
    for record in records:
        verdict = record.decision.verdict
        if verdict is Verdict.ENTER:
            if record.admission is Admission.REFUSED:
                reasons[f"ENTER refused: {record.refusal_cause}"] += 1
                continue
            enter = simulated.get((record.symbol, record.bar_start))
            if enter is None:
                raise ResearchError(f"ENTER of {record.symbol} at {record.bar_start} was not "
                                    "simulated")
            reasons[f"ENTER {enter.outcome}: {enter.cause}"] += 1
            continue
        failed = record.decision.decisive
        reasons[f"{verdict.value}: " + (failed.rule if failed else "all checks passed")] += 1
    return reasons


def _day(day: date, records: Sequence[DecisionRecord],
         bars_by_symbol: Mapping[str, Sequence[Bar]], settings: ReplaySettings,
         clock_for: ClockFor, evidence: dict[Identity, VariantEvidence]
         ) -> MissedMove | None:
    result = observer_report.report(records, bars_by_symbol, settings, clock_for)
    by_identity: dict[Identity, list[DecisionRecord]] = {}
    for record in records:
        by_identity.setdefault(_key(record), []).append(record)
    traded = {r.symbol for r in records if r.admission is Admission.TAKEN}
    for row in result.identities:
        key = (row.strategy, row.version, row.role)
        out = VariantDay(day, row.decisions, row.enters)
        for enter in result.enters.get(key, ()):
            trade = enter.trade
            if trade is None:
                continue
            traded.add(enter.record.symbol)
            out.trades += 1
            r = trade.r_multiple
            if r > 0:
                out.wins += 1
                out.earned_r += r
            elif r < 0:
                out.losses += 1
                out.lost_r += r
            usd = enter.result_usd
            if usd is None:
                out.trades_without_usd += 1
            elif usd > 0:
                out.earned_usd += usd
            else:
                out.lost_usd += usd
        evidence.setdefault(key, VariantEvidence(*key)).days.append(out)

    clock = clock_for(day)
    best: tuple[Decimal, str, Bar, Bar] | None = None
    for symbol in sorted({r.symbol for r in records} - traded):
        found = _rise(bars_by_symbol.get(symbol, ()), clock.regular_open, clock.last_entry,
                      clock.flatten)
        if found is None:
            continue
        low, high = found
        percent = (high.high - low.low) / low.low * HUNDRED
        if best is None or percent > best[0]:
            best = (percent, symbol, low, high)
    if best is None:
        return None
    percent, symbol, low, high = best
    variants = []
    for key in sorted(by_identity):
        seen = [r for r in by_identity[key]
                if r.symbol == symbol and clock.regular_open <= r.bar_start <= high.start]
        simulated = {(e.record.symbol, e.record.bar_start): e
                     for e in result.enters.get(key, ())}
        reasons = _reasons(seen, simulated) if seen else Counter({NOT_EVALUATED: 1})
        variants.append(VariantReason(*key, len(seen), dict(reasons.most_common())))
    return MissedMove(day, symbol, low.start, high.start, low.low, high.high, percent,
                      tuple(variants))


def report(
    records: Sequence[DecisionRecord],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: ReplaySettings,
    clock_for: ClockFor,
    first_day: date,
    last_day: date,
) -> VariantsReport:
    if last_day < first_day:
        raise ResearchError(f"--to {last_day} is before --from {first_day}")
    zone = ZoneInfo(TIMEZONE_NAME)
    by_day: dict[date, list[DecisionRecord]] = {}
    for record in records:
        day = record.bar_start.astimezone(zone).date()
        if first_day <= day <= last_day:
            by_day.setdefault(day, []).append(record)
    if not by_day:
        raise ResearchError(f"no recorded decisions from {first_day} to {last_day}")
    evidence: dict[Identity, VariantEvidence] = {}
    missed = []
    quiet = []
    for day in sorted(by_day):
        move = _day(day, by_day[day], bars_by_symbol, settings, clock_for, evidence)
        if move is None:
            quiet.append(day)
        else:
            missed.append(move)
    return VariantsReport(first_day, last_day,
                          tuple(evidence[k] for k in sorted(evidence)), tuple(missed),
                          tuple(quiet))
