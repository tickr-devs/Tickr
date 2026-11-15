"""Daily report: observer strategies against the active one over the same day. SIMULATED R.

Input: one day's decision records (active and observers, as written by observers.DecisionLog
or the bot) and the captured bars of that day (plus earlier days, harmless).

Per strategy identity (name + version + role):
- decisions by verdict, ENTERs and, for the active one, how many admission took or refused;
- every ENTER simulated through the replay engine exactly like `whatif` (docs/research.md),
  taken or not, so all identities are measured the same way. Per symbol, an ENTER that arrives
  while an earlier simulated trade of the same identity is still open is `skipped_overlap`
  (one position per symbol, as live). R per trade; USD only for ENTERs with a recorded whole
  quantity (gross: slippage in, commissions out);
- every ENTER as simulated (`ObserverReport.enters`, read by GET /observers for its trades;
  not written to the report files);
- for observers, a bar-by-bar diff against the active decisions (aperture.research.compare)
  and how many ENTER bars it shares with the active strategy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from aperture.calendar import TIMEZONE_NAME
from aperture.replay.clock import ClockFor
from aperture.replay.engine import SimulatedTrade
from aperture.replay.settings import ReplaySettings
from aperture.research.compare import diff
from aperture.research.errors import ResearchError
from aperture.research.records import Admission, DecisionRecord
from aperture.research.whatif import WhatIfOutcome, simulate_one
from aperture.strategy.base import Role, Verdict
from aperture.types import Bar

LABEL = "simulated"
ONE = Decimal(1)


@dataclass
class IdentityReport:
    strategy: str
    version: str
    role: str
    decisions: int = 0
    by_verdict: dict[str, int] = field(default_factory=dict)
    enters: int = 0
    enters_taken: int = 0
    enters_refused: int = 0
    simulated: dict[str, int] = field(default_factory=dict)
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_r: Decimal = Decimal(0)
    average_r: Decimal | None = None
    total_usd: Decimal = Decimal(0)
    usd_trades: int = 0
    vs_active: dict[str, int] | None = None
    shared_enter_bars: int | None = None


SKIPPED_OVERLAP = "skipped_overlap"


@dataclass(frozen=True)
class SimulatedEnter:
    """One ENTER of an identity as the report simulated it. `outcome`: a WhatIfOutcome value
    or SKIPPED_OVERLAP; `trade` only when filled; `result_usd` only with a recorded whole
    quantity (without one the trade is simulated for one share and counts in R only)."""

    record: DecisionRecord
    outcome: str
    cause: str
    trade: SimulatedTrade | None
    result_usd: Decimal | None


Identity = tuple[str, str, str]  # (strategy, version, role)


@dataclass(frozen=True)
class ObserverReport:
    active: str | None
    identities: tuple[IdentityReport, ...]
    # Every ENTER of each identity, in simulation order (symbol, then bar start).
    enters: dict[Identity, tuple[SimulatedEnter, ...]] = field(default_factory=dict)


def _whole(quantity: Decimal | None) -> bool:
    return quantity is not None and quantity >= 1 and quantity == quantity.to_integral_value()


def _simulate(
    row: IdentityReport,
    enters: Sequence[DecisionRecord],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: ReplaySettings,
    clock_for: ClockFor,
) -> tuple[SimulatedEnter, ...]:
    out: list[SimulatedEnter] = []
    outcomes = {o.value: 0 for o in WhatIfOutcome} | {SKIPPED_OVERLAP: 0}
    busy_until: dict[str, datetime] = {}
    r_values: list[Decimal] = []
    for record in sorted(enters, key=lambda r: (r.symbol, r.bar_start)):
        if record.bar_start < busy_until.get(record.symbol, record.bar_start):
            outcomes[SKIPPED_OVERLAP] += 1
            out.append(SimulatedEnter(record, SKIPPED_OVERLAP, "an earlier simulated trade of "
                                      f"{record.symbol} was still open", None, None))
            continue
        bars = bars_by_symbol.get(record.symbol)
        if not bars:
            outcomes[WhatIfOutcome.NOT_SIMULATED.value] += 1
            out.append(SimulatedEnter(record, WhatIfOutcome.NOT_SIMULATED.value,
                                      "no captured bars for the symbol", None, None))
            continue
        quantity = record.decision.quantity
        has_usd = _whole(quantity)
        result = simulate_one(record, bars, settings, clock_for, quantity if has_usd else ONE)
        outcomes[result.outcome.value] += 1
        trade = result.trade
        usd = result.result_usd if has_usd and trade is not None else None
        out.append(SimulatedEnter(record, result.outcome.value, result.cause, trade, usd))
        if trade is None:
            continue
        busy_until[record.symbol] = trade.exit_time
        r_values.append(trade.r_multiple)
        if usd is not None:
            row.total_usd += usd
            row.usd_trades += 1
    row.simulated = outcomes
    row.trades = len(r_values)
    row.wins = sum(1 for r in r_values if r > 0)
    row.losses = sum(1 for r in r_values if r < 0)
    row.total_r = sum(r_values, Decimal(0))
    row.average_r = row.total_r / len(r_values) if r_values else None
    return tuple(out)


def report(
    records: Sequence[DecisionRecord],
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    settings: ReplaySettings,
    clock_for: ClockFor,
) -> ObserverReport:
    groups: dict[tuple[str, str, str], list[DecisionRecord]] = {}
    for record in records:
        key = (record.decision.strategy, record.decision.version, record.role.value)
        groups.setdefault(key, []).append(record)
    actives = [k for k in groups if k[2] == Role.ACTIVE.value]
    if len(actives) > 1:
        names = ", ".join(f"{n} v{v}" for n, v, _ in actives)
        raise ResearchError(f"more than one active strategy in the records: {names}")
    active_key = actives[0] if actives else None
    zone = ZoneInfo(TIMEZONE_NAME)
    rows = []
    simulated: dict[Identity, tuple[SimulatedEnter, ...]] = {}
    for key in sorted(groups, key=lambda k: (k != active_key, k)):
        group = groups[key]
        row = IdentityReport(*key)
        row.decisions = len(group)
        for record in group:
            verdict = record.decision.verdict.value
            row.by_verdict[verdict] = row.by_verdict.get(verdict, 0) + 1
        row.by_verdict = dict(sorted(row.by_verdict.items()))
        enters = [r for r in group if r.decision.verdict is Verdict.ENTER]
        row.enters = len(enters)
        row.enters_taken = sum(1 for r in enters if r.admission is Admission.TAKEN)
        row.enters_refused = sum(1 for r in enters if r.admission is Admission.REFUSED)
        simulated[key] = _simulate(row, enters, bars_by_symbol, settings, clock_for)
        if active_key is not None and key != active_key:
            active = groups[active_key]
            row.vs_active = dict(diff(active, group, zone).counts)
            active_enters = {
                (r.symbol, r.bar_start) for r in active if r.decision.verdict is Verdict.ENTER
            }
            row.shared_enter_bars = len({(r.symbol, r.bar_start) for r in enters} & active_enters)
        rows.append(row)
    active_name = f"{active_key[0]} v{active_key[1]}" if active_key else None
    return ObserverReport(active_name, tuple(rows), simulated)
