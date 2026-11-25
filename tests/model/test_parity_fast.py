"""The replay parity harness's incremental engine (`--engine incremental`): one walk over the
day gives the full-prefix engine's tick tables and features exactly, for every variant; the
results journal resumes after a stop and rebuilds the report; a difference between the engines
is reported as its own class. Synthetic days only (test_parity.py's day)."""
# ruff: noqa: F811 - the tests take test_parity.py's fixture `day`, imported above

from __future__ import annotations

import json

import pandas as pd
import pytest
from test_parity import DAY, MINUTES, _args, day  # noqa: F401 - the fixture `day`

from tickr.model import parity, parity_walk
from tickr.model.errors import ModelRefused
from tickr.model.features import ticks

# Several minutes: early (pre-04:00 carry), the open minute by minute, midday (a stale latest
# quote), late; spans of many minutes and of one.
WALK_MINUTES = ["04:00", "04:30", "06:10", "09:30", "09:31", "09:32", "09:33", "09:45", "11:59",
                "12:00", "12:01", "15:30", "15:59", "19:59"]


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    monkeypatch.setenv("TICK_THREADS", "1")


def test_the_walk_gives_the_full_prefix_tick_table_of_every_variant(day, tmp_path):
    given, trades, quotes = day[0], day[1], day[2]
    symbols = set(trades.ticker) | set(quotes.ticker)
    minutes = parity.table_minutes(WALK_MINUTES)
    parity.extract_events(given, symbols, parity._ns_end(DAY, minutes[-1]), tmp_path)
    walk = parity_walk.TickWalk(tmp_path, DAY, plain=True, groups=ticks.default_groups())
    try:
        for m in minutes:
            through = m + 240
            walk.advance(through)
            for variant in parity.VARIANTS:
                want = parity.minute_ticks(tmp_path, DAY, m, variant != "plain" and variant)
                got = walk.table(variant, through)
                pd.testing.assert_frame_equal(got, want, check_exact=True,
                                              obj=f"{variant} {parity.hhmm(m)}")
    finally:
        walk.close()
    assert walk.calls > len(minutes)
    with pytest.raises(ModelRefused) as exc:  # a table only where the walk stands
        walk.table("plain", minutes[0] + 240)
    assert exc.value.cause == "replay_engine_order"


def _run(given, out, *extra):
    assert parity.main(_args(given, out, "--mode", "all", *extra)) == 0
    return json.loads((out / f"parity-{DAY}.json").read_text())


def _results(report):
    """What a run found, without its clocks or engine settings."""
    drop = {"seconds", "engine", "verify_full_prefix", "engine_check"}
    return {k: v for k, v in report.items() if k not in drop}


def test_the_incremental_engine_equals_full_prefix_on_all_191_columns(day, tmp_path):
    given = day[0]
    ref = _run(given, tmp_path / "ref")
    got = _run(given, tmp_path / "inc", "--engine", "incremental", "--verify-full-prefix",
               str(len(MINUTES)), "--workers", "2")
    check = got["engine_check"]
    assert check["minutes"] == MINUTES and check["differing_minutes"] == []
    for hm in MINUTES:
        e = check["per_minute"][hm]
        assert e["bars"]["exact"] and e["bars"]["rows_incremental"] > 0, hm
        assert set(e["variants"]) == set(parity.VARIANTS), hm
        assert all(v["ticks"]["exact"] and "features" not in v for v in e["variants"].values())
    # the same cells against the archive in both engines, value by value: with the marker every
    # minute is exact (all 191 columns equal the archive), without it the differing cells carry
    # the replay's values, which are then equal too
    assert _results(got) == _results(ref)
    assert all(r["exact"] for r in got["variants"]["marker"].values())
    assert any(not r["exact"] for r in got["variants"]["plain"].values())
    cells = [pd.read_csv(p / f"parity-{DAY}-exceptions.csv") for p in (tmp_path / "ref",
                                                                       tmp_path / "inc")]
    pd.testing.assert_frame_equal(*cells)
    assert len(cells[0]) == got["exceptions"] > 0


def test_resume_skips_finished_minutes_and_rebuilds_the_same_report(day, tmp_path,
                                                                    monkeypatch):
    given = day[0]
    first = tmp_path / "first"
    whole = _run(given, first, "--engine", "incremental")
    lines = (first / f"parity-{DAY}.jsonl").read_text().splitlines(keepends=True)
    assert len(lines) == 1 + len(MINUTES)
    # a stop after two minutes, in the middle of writing the third line, the third minute's
    # cells already written
    out = tmp_path / "stopped"
    out.mkdir()
    (out / f"parity-{DAY}.jsonl").write_text("".join(lines[:3]) + lines[3][:40])
    cells = pd.read_csv(first / f"parity-{DAY}-exceptions.csv")
    cells[cells.minute.isin(MINUTES[:3])].to_csv(out / f"parity-{DAY}-exceptions.csv",
                                                 index=False)
    assert parity.main(_args(given, out, "--mode", "all", "--engine", "incremental")) == 2
    run = []
    real = parity._prepare
    monkeypatch.setattr(parity, "_prepare", lambda *a: run.append(a[1]) or real(*a))
    again = _run(given, out, "--engine", "incremental", "--resume")
    assert [parity.hhmm(m) for m in run] == MINUTES[2:]
    assert _results(again) == _results(whole)
    pd.testing.assert_frame_equal(pd.read_csv(out / f"parity-{DAY}-exceptions.csv"), cells)
    # the report again from the journal alone
    js = (out / f"parity-{DAY}.json").read_text()
    for f in (out / f"parity-{DAY}.json", out / f"parity-{DAY}.txt"):
        f.unlink()
    assert parity.main(["--day", DAY, "--out", str(out), "--rebuild-report"]) == 0
    assert (out / f"parity-{DAY}.json").read_text() == js
    # another run's settings never continue this journal
    for f in (out / f"parity-{DAY}.json", out / f"parity-{DAY}.txt"):
        f.unlink()
    (tmp_path / "w").mkdir()
    with pytest.raises(ModelRefused) as exc:
        parity.replay(DAY, MINUTES, given, variants=("marker",), work=tmp_path / "w",
                      engine="incremental", journal=parity.Journal(out, DAY, resume=True))
    assert exc.value.cause == "replay_resume_mismatch" and "variants_run" in exc.value.detail


def test_a_difference_between_the_engines_is_reported(day, tmp_path, monkeypatch):
    given = day[0]
    target = parity.table_minutes(["12:00"])[0] + 240
    real = parity_walk.TickWalk._convert

    def changed(self, *a, **k):
        rows = real(self, *a, **k)
        hit = (rows.m == target) & rows.qspr.notna()
        if hit.any():  # one cell of one stock-minute: in every table from that minute on
            rows = rows.copy()
            rows.loc[rows.index[hit][0], "qspr"] += 1.0
        return rows

    monkeypatch.setattr(parity_walk.TickWalk, "_convert", changed)
    out = tmp_path / "out"
    report = _run(given, out, "--engine", "incremental", "--verify-full-prefix", "4")
    check = report["engine_check"]
    assert check["differing_minutes"] == ["12:00", "15:30"]
    e = check["per_minute"]["12:00"]
    assert e["bars"]["exact"]
    for variant, x in e["variants"].items():
        assert not x["ticks"]["exact"] and x["ticks"]["cells"] == 1, variant
        assert x["ticks"]["columns"] == {"qspr": 1}
        assert "features" in x, variant  # the feature cells it moves, compared too
    assert not e["variants"]["marker"]["features"]["exact"]
    cells = pd.read_csv(out / f"parity-{DAY}-exceptions.csv")
    engine = cells[cells["class"] == "engine"]
    assert set(engine.minute) == {"12:00", "15:30"} and set(engine.table) == {"engine"}
    assert {"engine-ticks", "engine-features"} <= set(engine.group)
    text = (out / f"parity-{DAY}.txt").read_text()
    assert "ENGINE DIFFERENCE at 12:00 15:30" in text
