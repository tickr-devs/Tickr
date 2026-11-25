"""The tick table from the training's own frozen converter: parity with its `local` mode, the
prefix rule (where it holds and where it does not), the refusals."""

from __future__ import annotations

import ast
import subprocess
import sys

import model_ticks as synthetic
import numpy as np
import pandas as pd
import pytest

from tickr.model.errors import ModelRefused
from tickr.model.features import ticks

DAY = "2026-11-18"
SYMBOLS, TRADES, QUOTES = 8, 1500, 2500  # a small synthetic day: events per symbol
QUIET = "S0001"  # a stock with no quote update 11:55..12:01 New York (minutes 715..720)
TIME_WEIGHTED = ["qspr", "qbs", "qas", "qimb", "qbad", "qbd", "qad"]
LAST_MINUTE = 24 * 60 - 1


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    """One engine thread: with several the converter's floating sums are added in a varying
    order, so two runs of the same files differ in the last bit (see the test below)."""
    monkeypatch.setenv("TICK_THREADS", "1")


@pytest.fixture(scope="module")
def events():
    trades, quotes = synthetic.day_events(DAY, SYMBOLS, TRADES, QUOTES)
    gap = ((quotes.ticker == QUIET) & (quotes.sip_timestamp >= synthetic.stamp(DAY, "11:55:00"))
           & (quotes.sip_timestamp < synthetic.stamp(DAY, "12:01:00")))
    return trades, quotes[~gap].reset_index(drop=True)


def files(folder, trades, quotes, suffix=".csv.gz"):
    t, q = folder / f"trades{suffix}", folder / f"quotes{suffix}"
    ticks.write_flat_file(trades, t, synthetic.TRADE_FILE_COLUMNS)
    ticks.write_flat_file(quotes, q, synthetic.QUOTE_FILE_COLUMNS)
    return t, q


def local_mode(folder, t, q):
    """The training's run: the pinned converter's own `local` mode, as a separate process."""
    out = folder / "local.parquet"
    script = ticks.PINNED_DIR / ticks.CONVERTER_FILE
    subprocess.run([sys.executable, str(script), "local", str(t), str(q), str(out)], check=True,
                   capture_output=True)
    import duckdb

    return duckdb.connect().execute("SELECT * FROM read_parquet(?) ORDER BY sym, m",
                                    [str(out)]).df()


def cut(events, hhmmss):
    stamp = synthetic.stamp(DAY, hhmmss)
    return synthetic.before(events[0], stamp), synthetic.before(events[1], stamp)


def minute_of(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def test_the_pinned_converter_is_the_frozen_bytes():
    import hashlib

    data = (ticks.PINNED_DIR / ticks.CONVERTER_FILE).read_bytes()
    assert hashlib.md5(data).hexdigest() == ticks.CONVERTER_MD5
    assert ticks.trade_columns() == ["ticker", "conditions", "correction", "exchange", "price",
                                     "size", "sip_timestamp", "sequence_number", "trf_id"]
    assert set(ticks.trade_columns()) <= set(synthetic.TRADE_FILE_COLUMNS)
    assert set(ticks.quote_columns()) <= set(synthetic.QUOTE_FILE_COLUMNS)


def test_the_bot_path_does_not_import_boto3():
    code = ("import sys; from tickr.model.features import ticks; ticks.converter(); "
            "print('boto3' in sys.modules)")
    done = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True,
                          text=True)
    assert done.stdout.strip() == "False"


# The pinned converter's `main()` and `creds()` read S3 credential values (environment or Secrets
# Manager) for the training's download modes. They are the one place in the repository where
# credential values are read, byte-pinned and unreachable from the bot path: the bot calls only
# `day()` and reads the constants TCOLS and QCOLS. These two tests are that exception (rule 4).
BOT_USES_OF_THE_CONVERTER = {"day", "TCOLS", "QCOLS"}


def test_the_bot_only_uses_day_and_constants_of_the_converter():
    seen = set()
    for path in ticks.PINNED_DIR.parents[3].rglob("*.py"):
        if ticks.PINNED_DIR in path.parents:
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not [n for n in names if n.split(".")[0] in ("boto3", "botocore")], path
            if isinstance(node, ast.Attribute):
                base = node.value
                is_converter = (isinstance(base, ast.Call) and getattr(
                    base.func, "id", getattr(base.func, "attr", None)) == "converter") or (
                    path.name == "ticks.py" and isinstance(base, ast.Name) and base.id == "w")
                if is_converter:
                    seen.add(node.attr)
            if isinstance(node, ast.Constant) and node.value in ("main", "creds"):
                assert path.name != "ticks.py", f"{path}: a name string of a converter entry"
    assert seen == BOT_USES_OF_THE_CONVERTER, seen


def test_tick_rows_runs_with_main_and_creds_raising_and_boto3_blocked(tmp_path, events,
                                                                       monkeypatch):
    t, q = files(tmp_path, *events)
    module = ticks.converter()

    def boom(*_a, **_k):
        raise AssertionError("a credential-reading converter entry point was reached")

    monkeypatch.setattr(module, "main", boom)
    monkeypatch.setattr(module, "creds", boom)
    for name in ("boto3", "botocore"):
        monkeypatch.setitem(sys.modules, name, None)  # `import boto3` now raises ImportError
    assert len(ticks.tick_rows(t, q, DAY, LAST_MINUTE)) > 0


def test_the_table_equals_the_converters_local_mode(tmp_path, events):
    t, q = files(tmp_path, *events)
    got = ticks.tick_rows(t, q, DAY, LAST_MINUTE)
    want = local_mode(tmp_path, t, q)
    assert got.shape[1] == 2 + 61 and len(got) > 0
    assert list(got.columns) == list(want.columns)
    pd.testing.assert_frame_equal(got, want, check_exact=True)


def test_plain_csv_and_a_day_prefix_equal_local_mode(tmp_path, events):
    t, q = files(tmp_path, *cut(events, "09:45:20"), suffix=".csv")
    got = ticks.tick_rows(t, q, DAY, minute_of("09:45"))
    pd.testing.assert_frame_equal(got, local_mode(tmp_path, t, q), check_exact=True)


def test_several_threads_differ_only_in_the_last_bits(tmp_path, events, monkeypatch):
    t, q = files(tmp_path, *events)
    one = ticks.tick_rows(t, q, DAY, LAST_MINUTE)
    monkeypatch.setenv("TICK_THREADS", "4")
    many = ticks.tick_rows(t, q, DAY, LAST_MINUTE)
    pd.testing.assert_frame_equal(one, many, check_exact=False, rtol=1e-12, atol=0)


def test_the_group_count_does_not_change_the_table(tmp_path, events, monkeypatch):
    t, q = files(tmp_path, *events)
    w = ticks.converter()
    default = ticks.tick_rows(t, q, DAY, LAST_MINUTE)
    monkeypatch.setattr(w, "KB", 1)
    pd.testing.assert_frame_equal(ticks.tick_rows(t, q, DAY, LAST_MINUTE), default,
                                  check_exact=True)


@pytest.fixture(scope="module")
def whole_day(tmp_path_factory, events):
    folder = tmp_path_factory.mktemp("whole")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TICK_THREADS", "1")
        return ticks.tick_rows(*files(folder, *events), DAY, LAST_MINUTE)


def rows_at(table, sym, minute):
    return table[(table.sym == sym) & (table.m == minute)].iloc[0]


def test_closed_minutes_equal_the_whole_day_except_a_stocks_last_quote(tmp_path, events,
                                                                        whole_day):
    """Cut at 12:00:30: every minute before 12:00 equals the whole day's, except the quote
    time weights of the minute that holds a stock's latest quote update so far (QUIET: its
    last update before the cut is in 11:54, the next one comes after 12:01)."""
    through = minute_of("12:00")
    got = ticks.tick_rows(*files(tmp_path, *cut(events, "12:00:30")), DAY, through)
    quotes = synthetic.before(events[1], synthetic.stamp(DAY, "12:00:30"))
    last = quotes.groupby("ticker").sip_timestamp.max()
    local = pd.to_datetime(last, unit="ns", utc=True).dt.tz_convert(synthetic.NY)
    stale_minute = dict(zip(last.index, local.dt.hour * 60 + local.dt.minute, strict=True))
    assert stale_minute[QUIET] == minute_of("11:54")
    closed = got[got.m < through].reset_index(drop=True)
    want = whole_day[whole_day.m < through].reset_index(drop=True)
    pd.testing.assert_frame_equal(closed[["sym", "m"]], want[["sym", "m"]])
    stale = np.array([stale_minute.get(s) == m for s, m in zip(closed.sym, closed.m, strict=True)])
    assert stale.sum() >= 1
    steady = [c for c in closed.columns if c not in TIME_WEIGHTED]
    pd.testing.assert_frame_equal(closed[steady], want[steady], check_exact=True)
    pd.testing.assert_frame_equal(closed.loc[~stale, TIME_WEIGHTED],
                                  want.loc[~stale, TIME_WEIGHTED], check_exact=True)
    # where it does NOT hold: the stale minute's last update weighs 0 until a later one arrives
    a, b = rows_at(got, QUIET, minute_of("11:54")), rows_at(whole_day, QUIET, minute_of("11:54"))
    assert any(not (a[c] == b[c] or (pd.isna(a[c]) and pd.isna(b[c]))) for c in TIME_WEIGHTED)
    # ... and the open minute 12:00 holds only its first 30 seconds
    open_got = got[got.m == through].set_index("sym").qn
    open_all = whole_day[whole_day.m == through].set_index("sym").qn.reindex(open_got.index)
    assert len(open_got) > 0 and (open_got < open_all).any()


def test_a_later_quote_makes_the_stale_minute_final(tmp_path, events, whole_day):
    got = ticks.tick_rows(*files(tmp_path, *cut(events, "12:05:00")), DAY, minute_of("12:04"))
    minute = minute_of("11:54")
    pd.testing.assert_series_equal(rows_at(got, QUIET, minute), rows_at(whole_day, QUIET, minute),
                                   check_exact=True, check_names=False)


def test_an_empty_prefix_gives_an_empty_table(tmp_path, events):
    t, q = files(tmp_path, events[0].iloc[:0], events[1].iloc[:0], suffix=".csv")
    got = ticks.tick_rows(t, q, DAY, minute_of("04:00"))
    assert len(got) == 0 and got.shape[1] == 2 + 61


@pytest.mark.parametrize("name", ticks.OUTPUT_VARIABLES)
def test_a_set_output_variable_is_refused(monkeypatch, name):
    monkeypatch.setenv(name, "1")
    with pytest.raises(ModelRefused) as exc:
        ticks.converter()
    assert exc.value.cause == "tick_variable_set" and exc.value.detail == name


def test_an_event_after_the_minute_is_refused(tmp_path, events):
    t, q = files(tmp_path, *cut(events, "10:00:05"), suffix=".csv")
    with pytest.raises(ModelRefused) as exc:
        ticks.tick_rows(t, q, DAY, minute_of("09:59"))
    assert exc.value.cause == "tick_after_minute"


def test_an_event_of_another_day_is_refused(tmp_path, events):
    trades, quotes = cut(events, "10:00:00")
    trades = trades.copy()
    trades.loc[0, "sip_timestamp"] = synthetic.stamp("2026-11-17", "15:00:00")
    t, q = files(tmp_path, trades, quotes, suffix=".csv")
    with pytest.raises(ModelRefused) as exc:
        ticks.tick_rows(t, q, DAY, minute_of("10:00"))
    assert exc.value.cause == "tick_wrong_day"


def test_a_float_code_column_is_refused(tmp_path, events):
    trades = events[0].astype({"trf_id": "float64"})
    with pytest.raises(ModelRefused) as exc:
        ticks.write_flat_file(trades, tmp_path / "t.csv", synthetic.TRADE_FILE_COLUMNS)
    assert exc.value.cause == "tick_value_invalid"


def test_a_missing_column_is_refused(tmp_path, events):
    with pytest.raises(ModelRefused) as exc:
        ticks.write_flat_file(events[0].drop(columns="trf_id"), tmp_path / "t.csv",
                              synthetic.TRADE_FILE_COLUMNS)
    assert exc.value.cause == "tick_columns_missing"
    t, q = files(tmp_path, *cut(events, "10:00:00"), suffix=".csv")
    pd.read_csv(t).drop(columns="trf_id").to_csv(t, index=False)
    with pytest.raises(ModelRefused) as exc:
        ticks.tick_rows(t, q, DAY, minute_of("10:00"))
    assert exc.value.cause == "tick_converter_refused"
