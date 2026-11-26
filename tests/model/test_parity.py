"""Replay parity harness: on a synthetic day whose reference table the training's own code built
from the same inputs, the replay reports zero difference (marker variant) and only the
last-quote difference without the marker; a perturbed input gives a located difference; sealed
days are refused before any file is read."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys

import model_bars as bars_maker
import model_inputs as inputs
import model_ticks as tick_maker
import numpy as np
import pandas as pd
import pytest

from tickr.model import parity
from tickr.model.errors import ModelRefused
from tickr.model.features import ticks

DAY = "2024-03-06"  # a day of the open archive with 20 calendar sessions before it
SYMBOLS = bars_maker.TICKERS
NEWS_MINUTES = [320, 338, 339, 340, 352, 360, 371, 384, 386, 400]
TICK_SYMBOLS, TRADES, QUOTES = 8, 400, 700  # a small synthetic tick day: events per symbol
QUIET = "T001"  # no quote update 11:55..12:01 New York: its 11:54 update is the latest at 12:00
NO_BARS = "ZZZZ"  # ticks but no bars: never a table row, absent from the replay's events
MINUTES = ["04:30", "09:45", "12:00", "15:30"]  # minutes with table rows on this day
TICK_GROUPS = {"tk", "tk2"}


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    """One converter thread on both sides: the comparison is then bit-exact."""
    monkeypatch.setenv("TICK_THREADS", "1")


def _tick_names(frame):
    names = {f"S{i:04d}": f"T{i:03d}" for i in range(TICK_SYMBOLS - 3)}
    names[f"S{TICK_SYMBOLS - 3:04d}"] = NO_BARS
    return frame.assign(ticker=frame.ticker.map(lambda t: names.get(t, t)))


def _training_table(root, given, tick_file):
    """The training's way: its history and whole-day table from the bar folder (wm2_l1f.day2 on
    files via l1labels.bar_load), its whole-day tick file, l1feat.assemble and l1feat.write."""
    code = inputs.research()
    labels, w = code.labels, code.w
    need = labels.needed(labels.calendar(), labels.bar_names(str(given.bars))[0], DAY)
    body = (given.reference / "splits.jsonl").read_bytes()
    sp = labels.Splits(labels.splits_parse(body), str(given.reference / "splits.jsonl"),
                       labels.md5b(body))
    saved = w.load
    w.load = labels.bar_load
    try:
        (prev, mprev, hist), _ = labels.history(str(given.bars), None, need, w.day2, sp)
        t = w.day2(str(given.bars / f"{DAY}.csv.gz"), prev, None, hist, mprev, want=True)[0]
    finally:
        w.load = saved
    t = t.reset_index(drop=True)
    pc = np.array([prev.get(s, np.nan) for s in t.sym.values], np.float64)
    work = root / "work"
    work.mkdir()
    with contextlib.redirect_stdout(io.StringIO()):
        o, rec = code.feat.assemble(t, pc, DAY, str(tick_file), str(given.reference),
                                    str(given.benzinga), str(given.fl_facts), code.settings,
                                    str(work), w, code.ref, code.tk)
        code.feat.write(o, rec, str(root / "table"), DAY)
    return root / "table" / f"{DAY}.parquet"


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    root = tmp_path_factory.mktemp("parity")
    labels = inputs.research().labels
    cal = labels.calendar()
    i = cal.index(DAY)
    folder = root / "bars"
    folder.mkdir()
    closes: dict[str, float] = {}
    for k, d in enumerate(cal[i - labels.HIST:i + 1]):
        frame = bars_maker.day_bars(d, k, closes, d == DAY)
        frame.to_csv(folder / f"{d}.csv.gz", index=False, compression="gzip")
    trades, quotes = tick_maker.day_events(DAY, TICK_SYMBOLS, TRADES, QUOTES)
    trades, quotes = _tick_names(trades), _tick_names(quotes)
    gap = ((quotes.ticker == QUIET)
           & (quotes.sip_timestamp >= tick_maker.stamp(DAY, "11:55:00"))
           & (quotes.sip_timestamp < tick_maker.stamp(DAY, "12:01:00")))
    quotes = quotes[~gap].reset_index(drop=True)
    tf, qf = root / "trades.csv.gz", root / "quotes.csv.gz"
    ticks.write_flat_file(trades, tf, tick_maker.TRADE_FILE_COLUMNS)
    ticks.write_flat_file(quotes, qf, tick_maker.QUOTE_FILE_COLUMNS)
    whole = root / "tk-whole.parquet"  # the training's run: the converter's own local mode
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TICK_THREADS", "1")
        subprocess.run([sys.executable, str(ticks.PINNED_DIR / ticks.CONVERTER_FILE), "local",
                        str(tf), str(qf), str(whole)], check=True, capture_output=True)
    given = parity.ReplayInputs(
        bars=folder, types=None, trades=tf, quotes=qf,
        reference=inputs.reference(root / "ref", DAY, SYMBOLS, NEWS_MINUTES),
        benzinga=inputs.benzinga(root / "bz", DAY, SYMBOLS, NEWS_MINUTES, "2023-01-02"),
        fl_facts=inputs.fl_facts(root / "fl.csv", DAY, SYMBOLS), table=root / "none")
    table = _training_table(root, given, whole)
    given = parity.ReplayInputs(**{**given.__dict__, "table": table})
    return given, trades, quotes, whole


def _args(given, out, *extra):
    return ["--day", DAY, "--minutes", *MINUTES, "--bars", str(given.bars), "--types", "-",
            "--trades", str(given.trades), "--quotes", str(given.quotes), "--reference",
            str(given.reference), "--benzinga", str(given.benzinga), "--fl",
            str(given.fl_facts), "--table", str(given.table), "--out", str(out), *extra]


def test_same_inputs_give_zero_difference_with_the_marker(day, tmp_path, capsys):
    given = day[0]
    assert parity.main(_args(given, tmp_path, "--marker", "--rtol", "1e-6")) == 0
    report = json.loads((tmp_path / f"parity-{DAY}.json").read_text())
    assert (tmp_path / f"parity-{DAY}.txt").read_text() == capsys.readouterr().out
    assert report["minutes"] == MINUTES and set(report["variants"]) == {"plain", "marker"}
    for hm, r in report["variants"]["marker"].items():
        assert r["rows_compared"] == r["rows_reference"] == r["rows_replay"] > 0, hm
        assert r["columns"] == {} and r["symbols"] == {}, hm
        assert r["bar_groups_exact"], hm
    # without the marker: the bars are still exact; only the tick groups differ (the latest
    # quote of each stock weighs 0 until its next update)
    differ = set()
    for hm, r in report["variants"]["plain"].items():
        assert r["bar_groups_exact"] and not r["only_in_reference"], hm
        assert {c["group"] for c in r["columns"].values()} <= TICK_GROUPS, hm
        differ |= set(r["columns"])
    assert differ
    assert report["variants"]["plain"]["12:00"]["symbols"].get(QUIET)
    assert parity.main(_args(given, tmp_path)) == 2  # never written over


def test_a_perturbed_trade_gives_a_located_difference(day, tmp_path):
    given, trades, _, _ = day
    sym = "T003"
    lo, hi = tick_maker.stamp(DAY, "09:59:00"), tick_maker.stamp(DAY, "10:00:00")
    hit = ((trades.ticker == sym) & (trades.sip_timestamp >= lo) & (trades.sip_timestamp < hi)
           & (trades.conditions == "") & (trades.correction == 0))
    assert hit.any()
    changed = trades.copy()
    changed.loc[hit, "price"] = (changed.loc[hit, "price"] * 1.05).round(4)
    tf = tmp_path / "trades.csv.gz"
    ticks.write_flat_file(changed, tf, tick_maker.TRADE_FILE_COLUMNS)
    work = tmp_path / "work"
    work.mkdir()
    report = parity.replay(DAY, ["10:00"], parity.ReplayInputs(**{**given.__dict__,
                                                                 "trades": tf}),
                           marker=True, work=work)
    r = report["variants"]["marker"]["10:00"]
    assert r["bar_groups_exact"] and r["columns"]
    assert {c["group"] for c in r["columns"].values()} <= TICK_GROUPS
    assert max(r["symbols"], key=r["symbols"].get) == sym
    assert all(c["max_abs"] != 0 or c["nan_only_reference"] + c["nan_only_replay"]
               for c in r["columns"].values())


def _whole_day(given):
    t = ticks.tick_rows(given.trades, given.quotes, DAY, 24 * 60 - 1,
                        groups=ticks.default_groups())
    return t[t.sym != NO_BARS]


def test_the_marker_makes_the_last_quote_minute_the_whole_day_value(day, tmp_path, monkeypatch):
    given = day[0]
    whole = _whole_day(given).reset_index(drop=True)
    minute = parity.table_minutes(["12:00"])[0]
    through = minute + 240
    symbols = set(whole.sym)
    parity.extract_events(given, symbols, parity._ns_end(DAY, minute), tmp_path)
    want = whole[whole.m <= through].reset_index(drop=True)
    stale = (want.sym == QUIET) & (want.m == 11 * 60 + 54)
    assert stale.sum() == 1
    marked = parity.minute_ticks(tmp_path, DAY, minute, marker=True)
    pd.testing.assert_frame_equal(marked, want, check_exact=True)
    plain = parity.minute_ticks(tmp_path, DAY, minute, marker=False)
    cols = ["qspr", "qbs", "qas", "qimb", "qbad", "qbd", "qad"]
    a, b = plain.loc[stale, cols].to_numpy(), want.loc[stale, cols].to_numpy()
    assert not np.array_equal(a, b, equal_nan=True)
    # only the marker's stamp matters: other prices and sizes give the same rows up to m
    monkeypatch.setattr(parity, "MARKER_VALUES", {**parity.MARKER_VALUES, "bid_price": "'0'",
                                                  "ask_price": "'0'", "bid_size": "'0'",
                                                  "ask_size": "'0'", "conditions": "'1'"})
    pd.testing.assert_frame_equal(parity.minute_ticks(tmp_path, DAY, minute, marker=True), want,
                                  check_exact=True)


@pytest.mark.parametrize(("when", "cause"), [
    ("2026-07-15", "sealed_day"), ("2026-11-20", "sealed_day"),
    ("2026-08-20", "history_in_sealed_window"), ("2026-09-14", "history_in_sealed_window"),
    ("2024-03-09", "not_a_trading_day"), (DAY, "bar_file_missing")])
def test_sealed_and_unbuildable_days_are_refused_before_any_read(tmp_path, when, cause):
    given = parity.ReplayInputs(*(tmp_path / "missing" for _ in range(8)))
    given = parity.ReplayInputs(**{**given.__dict__, "bars": tmp_path})
    with pytest.raises(ModelRefused) as exc:
        parity.replay(when, MINUTES, given, marker=False, work=tmp_path)
    assert exc.value.cause == "replay_day_refused" and f"REFUSED: {cause}" in exc.value.detail


def test_a_table_of_another_day_and_bad_minutes_are_refused(day, tmp_path):
    given = day[0]
    import duckdb

    other = tmp_path / "other.parquet"
    duckdb.sql(f"COPY (SELECT * REPLACE ('2024-03-05' AS date) FROM read_parquet("
               f"'{given.table}')) TO '{other}' (FORMAT parquet)")
    with pytest.raises(ModelRefused) as exc:
        parity.reference_table(other, DAY)
    assert exc.value.cause == "replay_table_wrong_day"
    for bad in (["20:00"], ["9:31"], ["09:31", "09:31"]):
        with pytest.raises(ModelRefused) as exc:
            parity.table_minutes(bad)
        assert exc.value.cause == "replay_minute_invalid"
