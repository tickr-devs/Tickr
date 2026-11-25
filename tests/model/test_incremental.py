"""The incremental table (`features.incremental.DayTable`) against the training's way: for every
tested minute, the rows of `full.table_rows` (the pinned day2 on the whole market's day so far)
and of the incremental path are the same frame, bit for bit (check_exact), on randomized markets
larger than the list depth (see model_market)."""

from __future__ import annotations

import model_market as synthetic
import pandas as pd
import pytest

from tickr.model import features
from tickr.model.errors import ModelRefused
from tickr.model.features import full, incremental

TICKERS = 650  # more than the lists' depth (500): the lists churn
HISTORY_DAYS = 6  # the table code needs at least 5 earlier days (MINH) for the hist group
DAYS = [d.date().isoformat() for d in pd.bdate_range(end="2026-11-18", periods=HISTORY_DAYS + 1)]
# Closed minutes (from 04:00): pre-market, the regular open and its first lists, mid-day, a
# minute right before a session's last row minute, after a flatten (no rows), post-market.
MINUTES = (45, 335, 336, 480, 690, 708, 709, 721, 800, 953)


@pytest.fixture(scope="module", params=[11, 12])
def world(request):
    days = synthetic.market(DAYS, TICKERS, request.param)
    today, bars = days[-1]
    state = features.history(days[:-1], {}, today, None)
    want = {m: full.table_rows(synthetic.cut(bars, today, m), today, m, state, None)
            for m in MINUTES}
    return today, bars, state, want


def _same(got, want):
    if want is None or not len(want):
        assert got is None or not len(got)
        return 0
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True),
                                  check_exact=True)
    return len(want)


@pytest.mark.parametrize(("chunk", "workers"), [(None, 1), (97, 2)])
def test_every_minute_equals_the_whole_market_prefix(world, chunk, workers):
    """Part 2 in one chunk or in several (in worker processes too): the same rows."""
    today, bars, state, want = world
    table = incremental.DayTable(today, state, None, workers)
    rows = 0
    try:
        for m in MINUTES:
            rows += _same(table.rows(synthetic.cut(bars, today, m), m, chunk), want[m])
    finally:
        table.close()
    assert rows > 0 and table.rebuilds == 0


def test_minutes_one_by_one_and_a_types_filter(world):
    today, bars, state, _ = world
    types = set(bars.ticker.unique()[::3]) | {"SPY"}
    table = incremental.DayTable(today, state, types)
    for m in range(331, 337):
        cut = synthetic.cut(bars, today, m)
        _same(table.rows(cut, m), full.table_rows(cut, today, m, state, types))


def test_a_changed_or_late_consumed_bar_starts_the_market_part_again(world):
    """A corrected bar of a consumed minute, then a bar that arrives after its minute was
    consumed: the rows are those of the bars handed in, as with the whole-day call."""
    today, bars, state, _ = world
    table = incremental.DayTable(today, state, None)
    table.rows(synthetic.cut(bars, today, 340), 340)
    cut = synthetic.cut(bars, today, 341).copy()
    early = cut.index[cut.window_start == cut.window_start.min()][0]
    cut.loc[early, "close"] *= 1.01
    _same(table.rows(cut, 341), full.table_rows(cut, today, 341, state, None))
    assert table.rebuilds == 1
    late = synthetic.cut(bars, today, 343)
    first = late.index[late.window_start == late.window_start.max()][0]
    table.rows(late.drop(index=first), 343)  # the corrected close is back: started again
    assert table.rebuilds == 2
    cut = synthetic.cut(bars, today, 344)
    _same(table.rows(cut, 344), full.table_rows(cut, today, 344, state, None))
    assert table.rebuilds == 3


def test_minutes_in_order_and_no_bar_after_the_minute(world):
    today, bars, state, _ = world
    table = incremental.DayTable(today, state, None)
    with pytest.raises(ModelRefused) as e:
        table.rows(synthetic.cut(bars, today, 341), 340)
    assert e.value.cause == "bar_after_minute"
    table.rows(synthetic.cut(bars, today, 340), 340)
    with pytest.raises(ModelRefused) as e:
        table.rows(synthetic.cut(bars, today, 340), 340)
    assert e.value.cause == "minute_order"
