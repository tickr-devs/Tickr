"""The incremental tick path: one closed minute from a bounded tail (`tail_carry` +
`minute_rows`) gives the same rows as the converter on the whole day so far, minute by minute,
on randomized days; with the marker quote, the training's whole-day rows."""

from __future__ import annotations

import model_ticks as synthetic
import numpy as np
import pandas as pd
import pytest

from tickr.model.errors import ModelRefused
from tickr.model.features import ticks

DAY = "2026-11-18"
NS_PER_MINUTE = 60 * 10**9
TIME_WEIGHTED = ["qspr", "qbs", "qas", "qimb", "qbad", "qbd", "qad"]
# windows of a randomized day: across the regular-session open (Form T counts for prices only
# outside it), mid-day, across the close; minutes of the New York day
FIRST_MINUTES = (9 * 60 + 24, 11 * 60, 15 * 60 + 54)
SPAN = 10  # minutes of each randomized day
CONDITIONS = np.array(["", "", "", "", "", "12", "12", "12,37", "37", "14", "14,37", "15", "16",
                       "38", "10", "22", "5", "2", "25", "8", "53,37"], dtype=object)
CORRECTIONS = np.array([0] * 12 + [1, 7, 8, 10, 11, 12])
QCOND = np.array(["", "", "", "", "", "1", "20"], dtype=object)
QIND = np.array(["", "", "", "", "", "1", "604", "2,604"], dtype=object)


@pytest.fixture(autouse=True)
def one_thread(monkeypatch):
    """One engine thread: the converter is bit-deterministic only with one (test_ticks.py)."""
    monkeypatch.setenv("TICK_THREADS", "1")


def _stamps(rs: np.random.RandomState, t0: int, n: int) -> np.ndarray:
    """n stamps over the window: some on a minute's first or last nanosecond, some shared."""
    ts = t0 + rs.randint(0, SPAN * NS_PER_MINUTE, n).astype(np.int64)
    edge = rs.random_sample(n)
    ts[edge < 0.05] = ts[edge < 0.05] // NS_PER_MINUTE * NS_PER_MINUTE
    ts[(edge >= 0.05) & (edge < 0.1)] = (ts[(edge >= 0.05) & (edge < 0.1)] // NS_PER_MINUTE + 1) \
        * NS_PER_MINUTE - 1
    ts = np.minimum(ts, t0 + SPAN * NS_PER_MINUTE - 1)
    tie = rs.random_sample(n) < 0.15
    ts = np.sort(ts)
    ts[1:][tie[1:]] = ts[:-1][tie[1:]]
    return ts


def _silence(rs: np.random.RandomState, t0: int, ts: np.ndarray) -> np.ndarray:
    """Drop a random stretch of minutes (a halt, a quiet stock): the kept stamps."""
    a = t0 + rs.randint(0, SPAN) * NS_PER_MINUTE
    b = a + rs.randint(1, 5) * NS_PER_MINUTE
    return ts[(ts < a) | (ts >= b)]


def random_day(seed: int) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """A small randomized day in the flat-file layout: several stocks over SPAN minutes with
    halts and quiet stretches, events on minute edges, equal stamps (between quotes, between
    trades and quotes, between trades) told apart by sequence numbers, every correction code,
    dropped and price-class conditions, Form T across the open, zero and empty quote sides,
    locked and crossed quotes, cheap and sub-penny prints, zero prices and sizes, rows in a
    random order. Returns (trades, quotes, first minute)."""
    rs = np.random.RandomState(seed)
    first = int(FIRST_MINUTES[seed % len(FIRST_MINUTES)])
    t0 = synthetic.stamp(DAY, f"{first // 60:02d}:{first % 60:02d}:00")
    tparts, qparts = [], []
    for k in range(rs.randint(3, 7)):
        ticker = f"R{k}"
        qs = _stamps(rs, t0, rs.randint(0, 150))
        if rs.random_sample() < 0.7:
            qs = _silence(rs, t0, qs)
        tt = _stamps(rs, t0, rs.randint(0, 120))
        if len(qs) and len(tt):
            share = rs.random_sample(len(tt)) < 0.15  # a trade on a quote's exact stamp
            tt[share] = rs.choice(qs, share.sum())
            tt = np.sort(tt)
        if rs.random_sample() < 0.5:
            tt = _silence(rs, t0, tt)
        # sequence numbers of the stock: increasing with the stamp, random order within one
        stamps = np.concatenate([qs, tt])
        order = np.lexsort((rs.random_sample(len(stamps)), stamps))
        seq = np.empty(len(stamps), dtype=np.int64)
        seq[order] = 1000 * k + np.arange(len(stamps))
        nq, nt = len(qs), len(tt)
        price0 = rs.choice([0.37, 3.5, 42.0, 410.0])
        tick = 0.0001 if price0 < 1 else 0.01
        mid = price0 * np.exp(np.cumsum(rs.normal(0, 1e-3, max(nq, 1))))[:nq]
        bid = np.round((mid - tick) / tick) * tick
        ask = np.round((mid + tick * rs.randint(1, 3, nq)) / tick) * tick
        bsz = rs.randint(1, 9, nq).astype(float)
        asz = rs.randint(1, 9, nq).astype(float)
        odd = rs.random_sample(nq)
        ask[odd < 0.05] = bid[odd < 0.05]  # locked
        ask[(odd >= 0.05) & (odd < 0.08)] = bid[(odd >= 0.05) & (odd < 0.08)] - tick  # crossed
        zero = (odd >= 0.08) & (odd < 0.12)
        bid[zero], bsz[zero] = 0.0, 0.0  # a side written as 0
        empty = (odd >= 0.12) & (odd < 0.16)
        ask[empty], asz[empty] = np.nan, np.nan  # a side left empty
        qparts.append(pd.DataFrame({
            "ticker": ticker, "ask_exchange": rs.randint(1, 20, nq), "ask_price": ask,
            "ask_size": asz, "bid_exchange": rs.randint(1, 20, nq), "bid_price": bid,
            "bid_size": bsz, "conditions": rs.choice(QCOND, nq), "indicators": rs.choice(QIND, nq),
            "participant_timestamp": qs - 1000, "sequence_number": seq[:nq], "sip_timestamp": qs,
            "tape": 1, "trf_timestamp": pd.array([pd.NA] * nq, dtype="Int64")}))
        px = price0 * np.exp(rs.normal(0, 2e-3, nt))
        off = rs.random_sample(nt) < 0.4
        px = np.where(off, np.round(px, 4), np.round(px / tick) * tick)
        size = np.where(rs.random_sample(nt) < 0.4, rs.randint(1, 100, nt),
                        100 * rs.randint(1, 30, nt)).astype(float)
        bad = rs.random_sample(nt)
        px[bad < 0.02] = 0.0
        size[(bad >= 0.02) & (bad < 0.04)] = 0.0
        trf = pd.array(np.where(off, rs.choice([201, 202, 0], nt), 0), dtype="Int64")
        trf[~off] = pd.NA
        tparts.append(pd.DataFrame({
            "ticker": ticker, "conditions": rs.choice(CONDITIONS, nt),
            "correction": rs.choice(CORRECTIONS, nt), "exchange": np.where(off, 4, 11),
            "id": np.arange(nt), "participant_timestamp": tt - 500, "price": px,
            "sequence_number": seq[nq:], "sip_timestamp": tt, "size": size, "tape": 1,
            "trf_id": trf, "trf_timestamp": pd.array([pd.NA] * nt, dtype="Int64")}))
    trades = pd.concat(tparts, ignore_index=True)
    quotes = pd.concat(qparts, ignore_index=True)
    trades = trades.sample(frac=1, random_state=rs).reset_index(drop=True)
    quotes = quotes.sample(frac=1, random_state=rs).reset_index(drop=True)
    return trades, quotes, first


def same(got, want):
    """Exact equality of two row sets, value for value. The frame types are not compared: the
    engine's frame makes an integer column nullable (Int64) when ANY row of the whole table is
    missing there, so a subset of rows can carry int64 in one table and Int64 in another."""
    got, want = got.reset_index(drop=True), want.reset_index(drop=True)
    assert list(got.columns) == list(want.columns)
    assert len(got) == len(want)
    if len(got):
        pd.testing.assert_frame_equal(got, want, check_exact=True, check_dtype=False)


def files(folder, trades, quotes):
    t, q = folder / "t.csv", folder / "q.csv"
    ticks.write_flat_file(trades, t, synthetic.TRADE_FILE_COLUMNS)
    ticks.write_flat_file(quotes, q, synthetic.QUOTE_FILE_COLUMNS)
    return t, q


def in_minute(frame, minute):
    m = minute_of_stamps(frame.sip_timestamp)
    return frame[m == minute].reset_index(drop=True)


def minute_of_stamps(stamps):
    local = pd.to_datetime(stamps, unit="ns", utc=True).dt.tz_convert(synthetic.NY)
    return local.dt.hour * 60 + local.dt.minute


def end_of(minute):
    return synthetic.stamp(DAY, f"{minute // 60:02d}:{minute % 60:02d}:00") + NS_PER_MINUTE


def walk(folder, trades, quotes, minutes):
    """Minute by minute: the tail's rows (plain and with the marker) and the converter's rows
    on the whole day so far, from the first minute of `minutes` on."""
    carry = (trades.iloc[:0], quotes.iloc[:0])
    for minute in minutes:
        tm, qm = in_minute(trades, minute), in_minute(quotes, minute)
        plain = ticks.minute_rows(carry, tm, qm, DAY, minute)
        marked = ticks.minute_rows(carry, tm, qm, DAY, minute, marker=True)
        stamp = end_of(minute)
        prefix = ticks.tick_rows(*files(folder, synthetic.before(trades, stamp),
                                        synthetic.before(quotes, stamp)), DAY, minute)
        yield minute, plain, marked, prefix[prefix.m == minute].reset_index(drop=True)
        carry = ticks.tail_carry(pd.concat([carry[0], tm]), pd.concat([carry[1], qm]))


@pytest.mark.parametrize("seed", range(6))
def test_the_tail_equals_the_whole_prefix_every_minute(tmp_path, seed):
    trades, quotes, first = random_day(seed)
    whole = ticks.tick_rows(*files(tmp_path, trades, quotes), DAY, first + SPAN)
    minutes = range(first - 1, first + SPAN + 1)  # one empty minute on each side
    rows = 0
    for minute, plain, marked, want in walk(tmp_path, trades, quotes, minutes):
        rows += len(want)
        same(plain, want)
        # marker: the training's whole-day row for every stock that quotes again later today;
        # a stock that never does keeps its last update at weight 0, as without the marker
        later = set(quotes.ticker[quotes.sip_timestamp >= end_of(minute)])
        day_rows = whole[whole.m == minute].reset_index(drop=True)
        again = marked.sym.isin(later)
        same(marked[again], day_rows[day_rows.sym.isin(later)])
        same(plain[~plain.sym.isin(later)], day_rows[~day_rows.sym.isin(later)])
        steady = [c for c in plain.columns if c not in TIME_WEIGHTED]
        same(marked[steady], plain[steady])
    assert rows > 0


@pytest.fixture(scope="module")
def big_day():
    return synthetic.day_events(DAY, 12, 3000, 4000, seed=11)


@pytest.mark.parametrize("hhmm", ["09:29", "12:00", "16:00"])
def test_the_tail_equals_the_whole_prefix_on_a_full_day(tmp_path, big_day, hhmm):
    """A whole day of the common synthetic layout: the carry is built from every event before
    the minute, and stays small (one quote per stock, one trade per stock and key)."""
    trades, quotes = big_day
    first = int(hhmm[:2]) * 60 + int(hhmm[3:])
    start = end_of(first - 1)
    carry = ticks.tail_carry(synthetic.before(trades, start), synthetic.before(quotes, start))
    assert len(carry[1]) == quotes.ticker.nunique()
    # bounded by the keys (stock, conditions, correction; three yes / no flags), not by time
    keys = trades[["ticker", "conditions", "correction"]].drop_duplicates()
    assert len(carry[0]) <= 8 * len(keys) and len(carry[0]) < len(trades) // 50
    seen = 0
    for minute in (first, first + 1):
        tm, qm = in_minute(trades, minute), in_minute(quotes, minute)
        stamp = end_of(minute)
        prefix = ticks.tick_rows(*files(tmp_path, synthetic.before(trades, stamp),
                                        synthetic.before(quotes, stamp)), DAY, minute)
        want = prefix[prefix.m == minute].reset_index(drop=True)
        same(ticks.minute_rows(carry, tm, qm, DAY, minute), want)
        seen += len(want)
        carry = ticks.tail_carry(pd.concat([carry[0], tm]), pd.concat([carry[1], qm]))
    assert seen > 0


def test_without_the_carry_the_rows_differ(tmp_path):
    """The carry is needed: the minute's events alone lose qgap / qofi / tgap / tvar / the
    quote-signed trade columns of the minute's first events."""
    trades, quotes, first = random_day(4)
    differs = 0
    for minute in range(first + 1, first + SPAN):
        tm, qm = in_minute(trades, minute), in_minute(quotes, minute)
        start = end_of(minute - 1)
        carry = ticks.tail_carry(synthetic.before(trades, start), synthetic.before(quotes, start))
        alone = ticks.minute_rows((trades.iloc[:0], quotes.iloc[:0]), tm, qm, DAY, minute)
        full = ticks.minute_rows(carry, tm, qm, DAY, minute)
        differs += not alone.equals(full)
    assert differs > 0


def test_an_event_outside_its_part_is_refused():
    trades, quotes, first = random_day(1)
    tm, qm = in_minute(trades, first + 2), in_minute(quotes, first + 2)
    empty = (trades.iloc[:0], quotes.iloc[:0])
    with pytest.raises(ModelRefused) as exc:
        ticks.minute_rows(empty, tm, qm, DAY, first + 3)
    assert exc.value.cause == "tick_outside_minute"
    with pytest.raises(ModelRefused) as exc:
        ticks.minute_rows((tm, empty[1]), tm, qm, DAY, first + 2)
    assert exc.value.cause == "tick_outside_minute"
    with pytest.raises(ModelRefused) as exc:
        ticks.minute_rows(empty, tm, qm, "2026-11-17", first + 2)
    assert exc.value.cause == "tick_outside_minute"
