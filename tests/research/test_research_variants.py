from __future__ import annotations

import csv
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from research_helpers import (
    CLOCK,
    DAY,
    HERE,
    at,
    bar_json,
    flat,
    record,
    replay_settings,
    series,
    write_jsonl,
)

from tickr.research.__main__ import main
from tickr.research.errors import ResearchError
from tickr.research.records import Admission, write_records
from tickr.research.variants import NOT_EVALUATED, report
from tickr.strategy.base import Check, Role, Verdict

ROOT = HERE.parent.parent
NEXT = DAY + timedelta(days=1)
DECISION = ("10.00", "10.05", "9.95", "10.05")
# Day 1: the 10:00 signal fills at 10:01 and hits the 9.80 stop at 10:03 (a loss).
# Day 2: the same signal fills and ends at the last bar's close, 10.60 (a win).
ABC = (
    series("10:00", [DECISION, ("10.05", "10.08", "10.00", "10.06"), flat("10.40"),
                     ("10.00", "10.00", "9.70", "9.75"), flat("9.75")])
    + series("10:00", [DECISION, ("10.05", "10.08", "10.00", "10.06"), flat("10.60"),
                       flat("10.60")], day=NEXT)
)
# Day 1: XYZ rises from 4.90 (10:01) to 5.10 (10:05), 4.08%: smaller than ABC's 9.95 -> 10.40,
# but ABC was traded. The 15:50 low is after the last entry (15:45) and the 15:56 high after
# the flatten (15:55), so neither belongs to a move.
XYZ = (
    series("10:00", [flat("5.00"), ("5.00", "5.00", "4.90", "4.95"), flat("5.00"),
                     flat("5.00"), flat("5.00"), ("5.00", "5.10", "5.00", "5.05"),
                     flat("5.00")], symbol="XYZ")
    + series("15:50", [("5.00", "5.00", "3.00", "3.00"), ("3.00", "5.08", "3.00", "5.00")],
             symbol="XYZ")
    + series("15:56", [("5.00", "9.00", "5.00", "9.00")], symbol="XYZ")
)
BARS = {"ABC": ABC, "XYZ": XYZ}
VOLUME = Check("relative_volume", Decimal("1.2"), ">=", Decimal(2), False)


def enter(clock, **kw):
    kw.setdefault("quantity", "10")
    return record(clock, low="10.00", high="10.10", stop="9.80", **kw)


def observer(clock, verdict=Verdict.ENTER, **kw):
    kw.update(role=Role.OBSERVER)
    kw.setdefault("version", "2")
    if verdict is Verdict.ENTER:
        kw.setdefault("admission", Admission.NOT_EVALUATED)
        return enter(clock, **kw)
    return record(clock, verdict, **kw)


RECORDS = [
    enter("10:00", admission=Admission.TAKEN, cause=None),
    enter("10:00", admission=Admission.TAKEN, cause=None, day=NEXT),
    record("10:02", Verdict.IGNORE, symbol="XYZ", checks=(VOLUME,)),
    record("10:03", Verdict.WATCH, symbol="XYZ"),
    record("10:30", Verdict.IGNORE, symbol="XYZ", checks=(VOLUME,)),  # after the peak
    observer("10:00"),
    # The price never comes back to the 6.00 window: missed.
    record("10:02", symbol="XYZ", role=Role.OBSERVER, version="2",
           admission=Admission.NOT_EVALUATED, low="6.00", high="6.10", stop="5.80",
           quantity="10"),
    observer("10:01", Verdict.IGNORE, version="3", checks=(VOLUME,)),
]


def run(records=RECORDS, first=DAY, last=NEXT):
    return report(records, BARS, replay_settings(), CLOCK, first, last)


def test_evidence_per_variant_over_days():
    result = run()
    active, obs, quiet = result.variants
    assert (active.role, obs.role, quiet.version) == ("active", "observer", "3")
    assert [d.day for d in active.days] == [DAY, NEXT]
    loss, win = active.days
    assert (loss.trades, loss.losses, loss.wins) == (1, 1, 0)
    assert (win.trades, win.wins, win.losses) == (1, 1, 0)
    assert loss.lost_usd < 0 and loss.earned_usd == 0 and loss.net_usd == loss.lost_usd
    assert win.earned_usd > 0 and win.lost_usd == 0 and win.net_r == win.earned_r > 0
    assert (active.days_with_trades, active.days_in_plus, active.days_in_minus) == (2, 1, 1)
    assert loss.decisions == 4 and loss.enters == 1
    (obs_day,) = obs.days
    assert (obs_day.trades, obs_day.losses, obs_day.lost_usd) == (1, 1, loss.lost_usd)
    assert (quiet.days_with_trades, quiet.days_in_plus, quiet.days[0].decisions) == (0, 0, 1)


def test_missed_move_skips_traded_symbols_and_late_lows():
    result = run()
    (move,) = result.missed
    assert result.days_without_missed_move == (NEXT,)  # only ABC that day, and it was traded
    assert (move.day, move.symbol, move.start, move.peak) == (DAY, "XYZ", at("10:01"),
                                                              at("10:05"))
    assert (move.low, move.high) == (Decimal("4.90"), Decimal("5.10"))
    assert move.move_percent == (Decimal("0.20") / Decimal("4.90")) * 100
    reasons = {(r.strategy, r.version, r.role): r for r in move.variants}
    active = reasons[("s", "1", "active")]
    assert active.decisions == 2  # the 10:30 IGNORE is after the peak
    assert active.reasons == {"IGNORE: relative_volume": 1, "WATCH: all checks passed": 1}
    (obs_reason,) = reasons[("s", "2", "observer")].reasons
    assert obs_reason.startswith("ENTER missed: ")
    assert reasons[("s", "3", "observer")].reasons == {NOT_EVALUATED: 1}


def test_refused_enter_reason_is_the_recorded_cause():
    records = [record("10:02", symbol="XYZ", low="6.00", high="6.10", stop="5.80",
                      cause="daily loss ceiling")]
    (move,) = run(records, DAY, DAY).missed
    assert move.variants[0].reasons == {"ENTER refused: daily loss ceiling": 1}


def test_range_filters_days_and_bad_ranges_are_refused():
    result = run(first=NEXT, last=NEXT)
    assert [d.day for v in result.variants for d in v.days] == [NEXT]
    with pytest.raises(ResearchError, match="before --from"):
        run(first=NEXT, last=DAY)
    with pytest.raises(ResearchError, match="no recorded decisions"):
        run(first=NEXT + timedelta(days=1), last=NEXT + timedelta(days=1))


def test_cli_writes_variant_and_missed_move_files(tmp_path, capsys):
    bars = write_jsonl(tmp_path / "bars.jsonl",
                       [bar_json(b) for series_ in BARS.values() for b in series_])
    decisions = tmp_path / "decisions.jsonl"
    write_records(decisions, RECORDS)
    out = tmp_path / "v"
    assert main(["variants", "--bars", str(bars), "--decisions", str(decisions),
                 "--replay-settings", str(HERE / "replay_settings.toml"),
                 "--config", str(ROOT / "config" / "bot.toml"),
                 "--from", DAY.isoformat(), "--to", NEXT.isoformat(),
                 "--out", str(out)]) == 0
    with open(out / "variants.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [(r["label"], r["role"], r["days_in_plus"]) for r in rows] == [
        ("simulated", "active", "1"), ("simulated", "observer", "0"),
        ("simulated", "observer", "0")]
    with open(out / "missed_moves.csv", newline="") as handle:
        missed = list(csv.DictReader(handle))
    assert {r["symbol"] for r in missed} == {"XYZ"} and len(missed) == 3
    assert missed[0]["move_start"].startswith(f"{DAY.isoformat()}T10:01:00-05:00")
    body = json.loads((out / "variants.json").read_text())
    assert body["days_without_missed_move"] == [NEXT.isoformat()]
    with pytest.raises(SystemExit):
        main(["variants", "--bars", str(bars), "--decisions", str(decisions),
              "--from", "2026-13-01", "--to", NEXT.isoformat(), "--out", str(out)])
