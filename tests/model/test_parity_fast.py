"""The replay parity harness's incremental engine (`--engine incremental`): one walk over the
day gives the full-prefix engine's tick tables and features exactly, for every variant; the
results journal resumes after a stop and rebuilds the report; a difference between the engines
is reported as its own class. Synthetic days only (test_parity.py's day)."""
# ruff: noqa: F811 - the tests take test_parity.py's fixture `day`, imported above

from __future__ import annotations

import json

import pandas as pd
import pytest
from test_parity import DAY, MINUTES, ROOMY, _args, _room, day  # noqa: F401 - fixture `day`

from tickr.model import headroom, parity, parity_walk
from tickr.model.errors import ModelRefused
from tickr.model.features import ticks

# Several minutes: early (pre-04:00 carry), the open minute by minute, midday (a stale latest
# quote), late; spans of many minutes and of one.
WALK_MINUTES = ["04:00", "04:30", "06:10", "09:30", "09:31", "09:32", "09:33", "09:45", "11:59",
                "12:00", "12:01", "15:30", "15:59", "19:59"]


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    monkeypatch.setenv("TICK_THREADS", "1")
    monkeypatch.setattr(headroom, "free_bytes", lambda path: ROOMY)
    monkeypatch.setattr(headroom, "available_memory", lambda: ROOMY)


def test_the_walk_gives_the_full_prefix_tick_table_of_every_variant(day, tmp_path):
    given, trades, quotes = day[0], day[1], day[2]
    symbols = set(trades.ticker) | set(quotes.ticker)
    minutes = parity.table_minutes(WALK_MINUTES)
    parity.extract_events(given, symbols, parity._ns_end(DAY, minutes[-1]), tmp_path)
    room = _room(tmp_path)
    walk = parity_walk.TickWalk(room, DAY, plain=True, groups=ticks.default_groups())
    try:
        for m in minutes:
            through = m + 240
            walk.advance(through)
            for variant in parity.VARIANTS:
                want = parity.minute_ticks(room, DAY, m, variant != "plain" and variant)
                got = walk.table(variant, through)
                pd.testing.assert_frame_equal(got, want, check_exact=True,
                                              obj=f"{variant} {parity.hhmm(m)}")
    finally:
        walk.close()
    assert walk.calls > len(minutes)
    assert {"minute", "span", "prefix"} <= set(room.measured)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("conv-")]
    with pytest.raises(ModelRefused) as exc:  # a table only where the walk stands
        walk.table("plain", minutes[0] + 240)
    assert exc.value.cause == "replay_engine_order"


def _run(given, out, *extra):
    assert parity.main(_args(given, out, "--mode", "all", *extra)) == 0
    return json.loads((out / f"parity-{DAY}.json").read_text())


def _results(report):
    """What a run found, without its clocks or engine settings."""
    drop = {"seconds", "engine", "verify_full_prefix", "engine_check", "resources"}
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


def test_the_walk_stops_at_the_guard_and_a_resume_finishes_the_day(day, tmp_path,
                                                                   monkeypatch, capsys):
    """The incremental engine's conversions go through the same guard: below the disk reserve
    the run stops with a `stop` line in the journal and a report of the minutes done (exit 3);
    with room again, --resume redoes the stopped minute and gives the whole run's results."""
    given = day[0]
    whole = _run(given, tmp_path / "whole", "--engine", "incremental")
    out = tmp_path / "out"
    real = parity_walk.TickWalk._convert
    stop_at = parity.table_minutes([MINUTES[2]])[0] + 240

    def tight(self, carry, t, q, first, through, marker):
        if through >= stop_at:  # from here on the disk is full but for the reserve
            self.room.free = lambda path: self.room.limits.disk_reserve
        return real(self, carry, t, q, first, through, marker)

    monkeypatch.setattr(parity_walk.TickWalk, "_convert", tight)
    args = _args(given, out, "--mode", "all", "--engine", "incremental")
    assert parity.main(args) == 3
    report = json.loads((out / f"parity-{DAY}.json").read_text())
    assert report["stopped"]["cause"] == "disk_space_insufficient"
    assert report["stopped"]["minute"] == MINUTES[2] and report["minutes_done"] == MINUTES[:2]
    stopped = report["stopped"]
    assert stopped["resources"]["free_disk_bytes"] == stopped["limits"]["disk_reserve"]
    assert all(report["resources"][hm]["peak_rss_bytes"] > 0 for hm in MINUTES[:2])
    assert "STOPPED at 12:00: disk_space_insufficient" in capsys.readouterr().out
    kinds = [json.loads(x)["kind"] for x in (out / f"parity-{DAY}.jsonl").read_text().splitlines()]
    assert kinds == ["header", "minute", "minute", "stop"]
    monkeypatch.setattr(parity_walk.TickWalk, "_convert", real)
    for f in (out / f"parity-{DAY}.json", out / f"parity-{DAY}.txt"):
        f.unlink()
    again = _run(given, out, "--engine", "incremental", "--resume")
    assert again["stopped"] is None and again["complete"]
    assert _results(again) == _results(whole)


def test_an_event_file_out_of_order_is_refused(tmp_path):
    """The walk streams the extracted file in file order (extraction sorted it); a step back
    in (stamp, sequence) refuses instead of converting events out of order."""
    import duckdb

    cols = ticks.trade_columns()
    rows = pd.DataFrame({c: ["1"] * 3 for c in cols})
    rows["ticker"] = "AAA"
    rows["sip_timestamp"] = ["30", "20", "40"]
    rows["sequence_number"] = ["1", "2", "3"]
    path = tmp_path / "trades.parquet"
    con = duckdb.connect()
    con.register("r", rows)
    con.execute(f"COPY (SELECT * FROM r) TO '{path}' (FORMAT parquet)")
    con.close()
    events = parity_walk._Events(path, cols, _room(tmp_path))
    with pytest.raises(ModelRefused) as refused:
        events.take(100)
    assert refused.value.cause == "tick_file_unordered"
    events.close()
