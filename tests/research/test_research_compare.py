from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from research_helpers import (
    CLOCK,
    HERE,
    NY,
    Breakout,
    flat,
    record,
    replay_settings,
    series,
)

from tickr import config
from tickr.replay.__main__ import clock_of
from tickr.research.compare import DiffKind, decide, diff, select
from tickr.research.errors import ResearchError
from tickr.research.records import Admission
from tickr.risk.admission import AdmissionLimits
from tickr.risk.simulated import SimulationRules
from tickr.strategy.base import Role, Verdict

BARS = series("10:00", [
    flat("10.00"),
    ("10.00", "10.20", "10.00", "10.03"),   # breaks 10.00 by 0.03
    ("10.03", "10.30", "10.03", "10.30"),   # breaks 10.20 by 0.10
    flat("10.30"),
    flat("10.30"),
])


def run(version="1", edge="0"):
    return decide(lambda: Breakout(version, edge), {"ABC": BARS}, replay_settings(), CLOCK)


def test_decide_records_every_decision_as_observer():
    records = run()
    assert [r.decision.verdict for r in records] == [
        Verdict.IGNORE, Verdict.ENTER, Verdict.HOLD, Verdict.HOLD, Verdict.HOLD]
    assert [r.bar_start for r in records] == [b.start for b in BARS]
    assert all(r.role is Role.OBSERVER and r.problem() is None for r in records)
    assert records[1].admission is Admission.NOT_EVALUATED
    assert records[0].admission is Admission.NOT_APPLICABLE


def test_decide_under_the_platform_rules_records_their_refusals():
    """Backlog T12: an ENTER the live rules refuse is recorded `refused` with its cause, and the
    strategy is never given that position."""
    cfg = config.load(HERE.parent.parent / "config" / "bot.toml")
    # The breakout asks for 10 shares with a 0.20 USD stop distance: 2 USD planned loss.
    tight = SimulationRules(replace(AdmissionLimits.from_config(cfg),
                                    planned_loss_usd=Decimal(1)))
    # The first bar's low sits under its high, so the stop is below the entry window.
    bars = series("10:00", [("10.00", "10.00", "9.90", "10.00"),
                            ("10.00", "10.20", "10.00", "10.03"),
                            ("10.03", "10.30", "10.03", "10.30"), flat("10.30")])
    free = decide(Breakout, {"ABC": bars}, replay_settings(), clock_of(cfg),
                  SimulationRules.from_config(cfg))
    assert [r.decision.verdict for r in free] == [
        Verdict.IGNORE, Verdict.ENTER, Verdict.HOLD, Verdict.HOLD]
    assert free[1].admission is Admission.NOT_EVALUATED
    refused = decide(Breakout, {"ABC": bars}, replay_settings(), clock_of(cfg), tight)
    assert [r.decision.verdict for r in refused] == [
        Verdict.IGNORE, Verdict.ENTER, Verdict.ENTER, Verdict.IGNORE]
    assert [(r.admission, r.refusal_cause) for r in refused[1:3]] == [
        (Admission.REFUSED, "PLANNED_LOSS_OVER_LIMIT")] * 2
    assert all(r.problem() is None for r in refused)


def test_diff_of_two_versions_over_the_same_day():
    result = diff(run("1", "0"), run("2", "0.05"), NY)
    assert (result.baseline, result.candidate) == ("breakout-test v1", "breakout-test v2")
    kinds = {(r.bar_start, r.kind) for r in result.rows}
    # v2 skips the 0.03 break, then enters on the 0.10 break instead of holding.
    assert (BARS[1].start, DiffKind.VERDICT_CHANGED) in kinds
    assert (BARS[2].start, DiffKind.VERDICT_CHANGED) in kinds
    row = next(r for r in result.rows if r.bar_start == BARS[1].start)
    assert row.candidate.decision.decisive.rule == "close_above_high"
    assert result.counts[DiffKind.VERDICT_CHANGED] == 2
    assert result.counts[DiffKind.SAME] == 2  # HOLD == HOLD on the last two bars


def test_levels_changed_lists_fields():
    base = [record("10:00", high="10.10", stop="9.80", low="10.00")]
    cand = [record("10:00", high="10.20", stop="9.80", low="10.00", version="2")]
    (row,) = diff(base, cand, NY).rows
    assert row.kind is DiffKind.LEVELS_CHANGED and row.changed == ("entry_high",)


def test_last_record_per_bar_wins_and_is_counted():
    base = [record("10:00", Verdict.WATCH), record("10:00", high="10.1", stop="9.9")]
    cand = [record("10:00", high="10.1", stop="9.9", version="2")]
    (row,) = diff(base, cand, NY).rows
    assert row.kind is DiffKind.SAME
    assert (row.baseline_count, row.candidate_count) == (2, 1)


def test_scope_is_the_baseline_days_and_ignore_vs_absent_is_skipped():
    other_day = date(2026, 11, 9)
    base = [record("10:00", Verdict.WATCH), record("10:01", Verdict.IGNORE)]
    cand = [
        record("10:05", Verdict.WATCH, day=other_day, version="2"),   # history day: out
        record("10:02", Verdict.IGNORE, version="2"),                  # ignore vs absent: out
        record("10:03", Verdict.WATCH, version="2"),                   # absent vs WATCH: in
    ]
    rows = diff(base, cand, NY).rows
    assert [(r.bar_start.astimezone(NY).strftime("%H:%M"), r.kind) for r in rows] == [
        ("10:00", DiffKind.VERDICT_CHANGED), ("10:03", DiffKind.VERDICT_CHANGED)]
    assert rows[0].candidate is None and rows[1].baseline is None


def test_mixed_strategies_are_refused_until_filtered():
    base = [record("10:00", Verdict.WATCH), record("10:00", Verdict.WATCH, strategy="other")]
    with pytest.raises(ResearchError, match="exactly one strategy version"):
        diff(base, base, NY)
    only = select(base, "s", "1")
    assert len(only) == 1 and diff(only, only, NY).counts[DiffKind.SAME] == 1
    assert select(base, "s", None) == only
