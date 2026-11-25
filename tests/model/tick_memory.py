"""Peak memory and time of the tick converter with one stock group and with its default, on the
same synthetic day, each conversion in its own process (codex-108 point 2: allow one group only
after a serial equality and a peak-memory check). Nothing here is a limit: it records numbers.

    python tests/model/tick_memory.py SYMBOLS TRADES QUOTES

writes the synthetic day (model_ticks.day_events, TRADES / QUOTES events per symbol on average)
to a temporary folder, converts it with the converter's default group count and with one group,
and prints one JSON line per run (peak resident memory of the process before and after the
conversion, seconds, rows) and whether the two tables are equal, exactly.
"""

from __future__ import annotations

import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DAY = "2026-11-18"
LAST_MINUTE = 24 * 60 - 1
# ru_maxrss is in kibibytes on Linux and in bytes on macOS.
RSS_BYTES = 1 if sys.platform == "darwin" else 1024
MIB = 1024 * 1024


def _peak_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * RSS_BYTES / MIB


def _convert(trades: str, quotes: str, groups: int, out: str) -> dict:
    """In this process: the table of the files with `groups`, written to `out` (pickle)."""
    from tickr.model.features import ticks

    ticks.converter()
    before = _peak_mib()
    start = time.perf_counter()
    table = ticks.tick_rows(trades, quotes, DAY, LAST_MINUTE, groups=groups)
    seconds = time.perf_counter() - start
    table.to_pickle(out)
    return {"groups": groups, "peak_rss_mib_before": round(before, 1),
            "peak_rss_mib": round(_peak_mib(), 1), "seconds": round(seconds, 2),
            "rows": len(table)}


def measure(trades: Path, quotes: Path, groups: int, out: Path) -> dict:
    """One conversion in a fresh process; its record."""
    done = subprocess.run([sys.executable, __file__, "convert", str(trades), str(quotes),
                           str(groups), str(out)], check=True, capture_output=True, text=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def compare(folder: Path, trades: Path, quotes: Path) -> tuple[list[dict], bool]:
    """Both group counts on the same files: the two records and whether the tables are equal,
    exactly."""
    import pandas as pd

    from tickr.model.features import ticks

    records, tables = [], []
    for groups in (ticks.default_groups(), ticks.ONE_GROUP):
        out = folder / f"tk-{groups}.pkl"
        records.append(measure(trades, quotes, groups, out))
        tables.append(pd.read_pickle(out))
    try:
        pd.testing.assert_frame_equal(tables[0], tables[1], check_exact=True)
    except AssertionError:
        return records, False
    return records, True


def main(argv: list[str]) -> None:
    if argv[:1] == ["convert"]:
        trades, quotes, groups, out = argv[1:]
        print(json.dumps(_convert(trades, quotes, int(groups), out)))
        return
    import model_ticks as synthetic

    from tickr.model.features import ticks

    symbols, trades_n, quotes_n = (int(a) for a in argv)
    with tempfile.TemporaryDirectory(prefix="tickr-tick-memory-") as tmp:
        folder = Path(tmp)
        trades, quotes = synthetic.day_events(DAY, symbols, trades_n, quotes_n)
        t, q = folder / "trades.csv.gz", folder / "quotes.csv.gz"
        ticks.write_flat_file(trades, t, synthetic.TRADE_FILE_COLUMNS)
        ticks.write_flat_file(quotes, q, synthetic.QUOTE_FILE_COLUMNS)
        print(json.dumps({"symbols": symbols, "trades": len(trades), "quotes": len(quotes),
                          "file_mib": round((t.stat().st_size + q.stat().st_size) / MIB, 1)}))
        records, equal = compare(folder, t, q)
        for record in records:
            print(json.dumps(record))
        print(json.dumps({"equal_exactly": equal}))


if __name__ == "__main__":
    main(sys.argv[1:])
