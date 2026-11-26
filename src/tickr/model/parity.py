"""Replay parity: the bot's feature path replayed on one open historical day, minute by minute,
against the training's own feature table of that day (file-to-file parity; actual receipt-time
parity needs a separate authorized capture, codex-108 point 9).

The tool for "parity on a real minute": no live feed, only the day's files. It is run by the
owner (Mac or AWS) on one open day, e.g. the open archive day 2026-11-17 in the research
bucket's layout, copied to a local folder:

    python -m tickr.model.parity --day 2026-11-17 --folder <local copy> --types <types.csv> \
        --reference <prepared reference folder> --benzinga <Benzinga folder> --fl <facts csv> \
        --out <report folder> [--minutes HH:MM ... | --all-minutes] [--mode plain|marker|both|all]
        [--ticks-only] [--rtol R] [--atol A] [--work-dir W] [--config bot.toml]
        [--disk-reserve-gb G] [--memory-reserve-gb G] [--duckdb-memory-gb G]

INPUTS. `--folder` gives the defaults of the research layout (each may be named instead):
`--bars` = <folder>/minute_aggs (minute_aggs day files `YYYY-MM-DD.csv.gz`: the day and the 20
trading sessions before it); `--table` = the one file <folder>/l1/feat/<pin>/tables/YYYY/<day>.
parquet (the training's feature table: date, sym, m, sess and the 191 features); `--tk61` =
<folder>/tk61/YYYY/<day>.parquet (the training's v11 tick table of the whole day, full market);
`--trades` / `--quotes` = <folder>/tickraw-audit/YYYY/<day>-trades.parquet / -quotes.parquet.
`--types`: the training's types file (`-` = no type filter); its md5 is reported beside the
training's pin. `--reference`, `--benzinga`, `--fl`: the training-format inputs of
`tickr.model.features.full` (input contract there); the splits are read from
`<reference>/splits.jsonl`, which must match its manifest line, with the training's parser.

RAW TICKS, two forms (`--tick-form`, default from the file name: .parquet = audit):
- `flat`: Massive's us_stocks_sip day files (csv or csv.gz, header row), every ticker;
- `audit`: the research bucket's audit copy (parquet, every column stored as text, a subset of
  tickers). v11 reads its input as text (`read_csv(..., all_varchar=true)`) and casts each
  column itself, so the audit text is handed over as the text of a flat file: an empty text
  becomes the empty field (missing), as a flat file's empty field reads. Before any use every
  value is checked against what v11 casts: sip_timestamp and sequence_number as BIGINT, the
  prices and sizes as DOUBLE, conditions and indicators as comma-separated codes; a value that
  fails refuses `tick_value_invalid` naming the column (never dropped or repaired).
  The audit copy holds only some tickers (COVERED): the tick groups (tk, tk2) are compared only
  for covered symbols; every other group is compared for all rows. The minute's tick table
  then takes the rows of every uncovered row symbol from `--tk61` (cut at the minute), so the
  kr_* ranks among the minute's rows see the whole cohort; the audit form refuses without it.
Per-symbol tick extraction reads only the union of the symbols that have a row at a chosen
minute (and are covered), with all their events of the day before the last chosen minute's end
(their full required history); v11 partitions every column by symbol, so this is exact.

DATES. Before any file is opened, the day must pass the pinned training rules (`l1labels.needed`
on the bar folder's NAMES only): a sealed day (2026-07-01 .. 2026-08-14, or after the research
archive's last day, the HARD_LAST of the pinned l1labels.py, not the live replacement), a day
off the calendar, the embargo after the sealed window, history reaching into the sealed window
or before the archive, or a missing bar file refuses with the training's own cause. Sealed data
is never read. The open day 2026-11-17 passes.

MINUTES (table minutes, HH:MM 04:00 .. 19:59). `--minutes` names them; `--all-minutes` takes
every minute of the table day; by default: 04:30, 09:45, 12:00 and 15:30 (codex-108 point 9),
plus, over the CANDIDATES (covered tickers in the audit form; otherwise the row symbols of
those four minutes): the minute of each candidate's final quote of the day (when it lies in the
table day), the first bar minute of each candidate that has an event before 04:00 (the earliest
minute it can have a row, read with the pre-04:00 carry), and the regular-session minute (09:30
.. 15:59) with the most candidate events (the peak). A minute without rows is reported empty.

REPLAY, for each chosen minute m, as the bot would see it at the end of m, rebuilt from scratch
(complete market bars and cohort dependencies for every comparison):
1. bars: the day's bars with window_start <= m, the DayState from the 20 sessions before
   (`features.history`, splits applied) -> `features.minute_rows`: the rows (symbols) of m;
2. ticks: the events of the row symbols before the end of m -> the frozen converter v11 through
   `ticks.tick_rows` (TICK_THREADS as set; 1 gives bit-stable rows) -> the tick table so far;
3. `full.minute_rows_full` with that tick table and the reference, Benzinga and fl inputs.

VARIANTS (`--mode`; both = plain and marker, all = the three):
- `plain`: the prefix as it is: the latest quote update of a stock weighs 0 (v11's `d` needs a
  later update, last-quote-minute.md);
- `marker` (codex-108 point 1, the copied boundary quote): before converting, ONE copy of a
  symbol's latest real quote, stamped at the start of minute m+1 (sip_timestamp = end of m in
  ns, sequence_number + 1, prices and sizes copied, conditions and indicators empty), for every
  symbol with a real quote in the prefix. It acts only on v11's weight `d`, capped at the end of
  the quote's own minute: the latest quote of the minute k holding it weighs to the end of k,
  exactly what a marker at the start of k+1, added at the close of k for a symbol that quoted in
  k, gives, and what the live path (tickr.model.live, which appends each minute's rows at its
  close) holds. Its own row (minute m+1) is dropped; no count, lag or trade join reads it (lag
  columns read earlier updates, trades join quotes strictly before them). Compared with the
  archive, only a symbol's FINAL quote of the day can then differ (weight 0 in the archive).
- `marker-minute`: the literal reading of "only for a symbol with a real quote in that minute"
  applied to the prefix: the marker only for symbols with a real quote in minute m; a symbol
  whose latest quote lies in an earlier minute keeps the plain 0 weight there.

DISK AND MEMORY (`tickr.model.headroom`, `[parity]` of --config; the harness runs beside the
live bot): every temporary file lies under `--work-dir` (default --out) in a run folder removed
at the end; each minute's conversion writes the prefix as zstd csv (v11's read_csv reads it as
it reads csv.gz; a third of the plain csv on the synthetic test day, not yet measured on a
real day) into its own folder, removed when the conversion is done, and the minute's tick file
is removed once its features are built. Before each conversion the guard refuses
`memory_insufficient` or `disk_space_insufficient` (estimate = input rows x the bytes per row
measured on this run); the run then stops: the report holds the minutes done and `stopped`
(variant, minute, cause, detail), and the command exits 3. Every duckdb engine runs with
`duckdb_memory_gb` and spills inside the work folder. `resources` gives, per variant and
minute, the process's peak resident bytes so far, free disk and available memory.

REPORT (`<out>/parity-<day>.json`, `.txt` and `-exceptions.csv`; an existing one refuses). The
threshold is EXACT (codex-108 point 2: pending the per-column bound table); every discrepancy
cell is an exception, listed in the csv (variant, minute, table, symbol, column, group, class,
reference, replay, abs, rel, ulp). Per variant and minute:
- keys: row counts, symbols in one table only, whether the common symbols come in the same
  order (`order_equal`), and whether keys and order are exact (`keys_exact`);
- per column and per group, the cells by DISCREPANCY CLASS: `missing` (a missing value on one
  side only, counted per side), `rank` (kr_*, r_*, *_rk: a rank among the minute's rows),
  `flag` (a column whose archive values on the day are all 0 or 1: eligibility, has-a-value),
  `categorical` (sess, sic2, m), `value` (every other float); for value cells the largest
  absolute, relative (|a-b| / max(|a|,|b|)) and float32 ULP difference, and apart those where
  one side is 0 (near zero, where a relative difference says nothing);
- deterministic columns (the bar groups) must be exact: `bar_groups_exact`;
- with `--tk61`: our v11 rows of minute m against the tk61 rows of minute m for the symbols
  whose ticks were converted (61 columns, tn / qn counts included), the same classes;
- final-quote cases: every (symbol, minute) where a converted row symbol's final quote of the
  day lies in m, with the symbol's quote count of the day and its final quote's time (sparse
  and halted symbols are among them: a stock that stops quoting before 16:00 is marked
  `stopped_before_close`); per case whether a feature value (value / flag / categorical
  class), a missing-value flag or the symbol's own rank differs from the archive, and the count
  of OTHER rows of that minute whose rank differs (downstream);
- with two variants or more, a section comparing each variant with `plain` cell by cell.
`--rtol` / `--atol` (numpy `isclose`) add, beside the exact counts, the cells beyond that
tolerance; neither has a default. `--ticks-only` runs only the tick table comparison (needs
`--tk61`), skipping the features. Values are compared as the table stores them (float32
features, int m and sess). v11 with several engine threads (TICK_THREADS) is not
bit-deterministic (last-bit differences); the training's tick files carry that noise.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tickr import config
from tickr.model import features, headroom
from tickr.model.errors import ModelRefused
from tickr.model.features import full, ticks

# The groups computed from the minute bars alone: the live path and the training share every
# input there, so any difference is a defect, never noise.
BAR_GROUPS = features.LIVE_GROUPS
# The groups computed from the tick table: compared only for symbols whose raw ticks are given.
TICK_GROUPS = ("tk", "tk2")
VARIANTS = ("plain", "marker", "marker-minute")
MODES = {"plain": ("plain",), "marker": ("marker",), "both": ("plain", "marker"),
         "all": VARIANTS}
BASE_MINUTES = ("04:30", "09:45", "12:00", "15:30")  # codex-108 point 9
# The marker's quote values (module docstring): SQL over the symbol's latest quote of the prefix.
MARKER_VALUES = {"ask_price": "ask_price", "ask_size": "ask_size", "bid_price": "bid_price",
                 "bid_size": "bid_size", "conditions": "NULL", "indicators": "NULL"}
CATEGORICAL = ("m", "sess", "sic2")
RANK_PREFIXES, RANK_SUFFIX = ("kr_", "r_"), "_rk"
CLASSES = ("missing", "rank", "flag", "categorical", "value")
FORMS = ("flat", "audit")
# What v11 casts each raw column to (its day()): checked on the audit copy before any use.
BIGINT_COLUMNS = ("sip_timestamp", "sequence_number")
DOUBLE_COLUMNS = ("price", "size", "bid_price", "ask_price", "bid_size", "ask_size")
CODE_COLUMNS = ("conditions", "indicators")
CODE_PATTERN = "^[0-9]+(,[0-9]+)*$"
# Refusals of the run's disk and memory guard (tickr.model.headroom): the run stops there and
# the report holds the minutes done and the refusal.
STOP_CAUSES = ("disk_space_insufficient", "memory_insufficient")
DEFAULT_CONFIG = Path("config/bot.toml")  # the bot's config, relative to the repo root
REGULAR = ("09:30", "15:59")  # the regular session, for the peak minute
EXCEPTION_FIELDS = ("variant", "minute", "table", "sym", "column", "group", "class",
                    "reference", "replay", "abs", "rel", "ulp")
_CSV = "read_csv(?, header=true, all_varchar=true, delim=',', quote='\"', escape='\"')"


@dataclass(frozen=True)
class ReplayInputs:
    """The day's files (see the module docstring); `types` None = no type filter; `tk61` None =
    no tick table comparison; `tick_form` flat or audit."""

    bars: Path
    types: Path | None
    trades: Path
    quotes: Path
    reference: Path
    benzinga: Path
    fl_facts: Path
    table: Path
    tk61: Path | None = None
    tick_form: str = "flat"


@contextlib.contextmanager
def _training(cause: str):
    """A refusal of the training's code (SystemExit('REFUSED: ...')) as one typed refusal."""
    try:
        yield
    except SystemExit as exc:
        raise ModelRefused(cause, str(exc)) from None


def archive_last() -> str:
    """HARD_LAST of the pinned l1labels.py: the old text of LIVEPATCH, which `full.live_text`
    checks to occur exactly once in the md5-pinned file."""
    full.full_code()
    old = full.LIVEPATCH[0][1]
    return old.split("'")[1]


def day_files(day: str, bars: Path) -> list[str]:
    """The training's day rule on the bar folder's names: the 20 sessions before `day` and
    `day`, or the training's refusal (cause `replay_day_refused`, the training's text)."""
    code = full.full_code()
    labels = code.labels
    with _training("replay_day_refused"):
        labels.day_ok(day)
        if day > archive_last():
            raise SystemExit(f"REFUSED: sealed_day: {day} is after the research archive")
        if not bars.is_dir():
            raise SystemExit(f"REFUSED: bar_source_not_a_folder: {bars}")
        have = labels.bar_names(str(bars))[0]
        return list(labels.needed(labels.calendar(), have, day))


def layout(root: Path, day: str) -> dict[str, Path | None]:
    """The research bucket's layout of `day` under `root` (module docstring): bars, table,
    tk61, trades, quotes; a file that is not there is None, two feature tables refuse."""
    year = day[:4]
    tables = sorted(root.glob(f"l1/feat/*/tables/{year}/{day}.parquet"))
    if len(tables) > 1:
        raise ModelRefused("replay_layout", f"{len(tables)} feature tables of {day}")

    def there(p: Path) -> Path | None:
        return p if p.exists() else None

    return {"bars": there(root / "minute_aggs"), "table": tables[0] if tables else None,
            "tk61": there(root / "tk61" / year / f"{day}.parquet"),
            "trades": there(root / "tickraw-audit" / year / f"{day}-trades.parquet"),
            "quotes": there(root / "tickraw-audit" / year / f"{day}-quotes.parquet")}


def table_minutes(hhmm: Sequence[str]) -> list[int]:
    """Table minutes (from 04:00) of HH:MM texts, by the training's `cut_minute`."""
    labels = full.full_code().labels
    with _training("replay_minute_invalid"):
        got = [labels.cut_minute(x) for x in hhmm]
    if not got or len(set(got)) != len(got):
        raise ModelRefused("replay_minute_invalid", "minutes must be given once each")
    return sorted(got)


def hhmm(minute: int) -> str:
    total = minute + features.DAY_START_MINUTE
    return f"{total // 60:02d}:{total % 60:02d}"


def _splits(reference: Path) -> dict[str, list[tuple[str, float]]]:
    code = full.full_code()
    man = json.loads((reference / "manifest.json").read_text("utf-8"))
    path = reference / "splits.jsonl"
    body = path.read_bytes()
    if man.get("files", {}).get(path.name) != [len(body), hashlib.md5(body).hexdigest()]:
        raise ModelRefused("replay_input_changed", path.name)
    with _training("replay_input_unreadable"):
        return code.labels.splits_parse(body)


def _types(path: Path | None) -> tuple[set[str] | None, str | None]:
    if path is None:
        return None, None
    labels = full.full_code().labels
    with _training("replay_input_unreadable"):
        got = labels.read_types(str(path))
        return got, labels.md5b(labels.raw(str(path)))


def _read(path: Path, cause: str, sql: str = "SELECT * FROM read_parquet(?)",
          params: Sequence[Any] = ()) -> Any:
    import duckdb

    con = duckdb.connect()
    try:
        return con.execute(sql, [str(path), *params]).df()
    except duckdb.Error as exc:
        raise ModelRefused(cause, type(exc).__name__) from exc
    finally:
        con.close()


def reference_table(path: Path, day: str) -> Any:
    """The training's feature table of `day`: one row per (sym, m), every feature present."""
    frame = _read(path, "replay_table_unreadable")
    missing = [c for c in ["date", "sym", *full.full_features()] if c not in frame.columns]
    if missing:
        raise ModelRefused("replay_table_columns", ", ".join(missing))
    if (frame["date"].astype(str) != day).any():
        raise ModelRefused("replay_table_wrong_day", day)
    if frame.duplicated(["sym", "m"]).any():
        raise ModelRefused("replay_table_key_duplicate", day)
    return frame


def tick_table(path: Path, symbols: set[str], through: int) -> Any:
    """The rows of `symbols` up to minute `through` (of the New York day) of a v11 tick file
    (tk61), checked to hold v11's columns in v11's order."""
    import pandas as pd

    sql = ("SELECT * FROM read_parquet(?) WHERE sym IN (SELECT unnest(?)) AND m <= ? "
           "ORDER BY sym, m")
    frame = _read(path, "replay_tk61_unreadable", sql, [sorted(symbols), through])
    want = ["sym", "m", *_tick_value_columns()]
    if list(frame.columns) != want:
        raise ModelRefused("replay_tk61_columns", "not v11's columns")
    return frame.astype({"sym": object}) if len(frame) else pd.DataFrame(columns=want)


def _tick_value_columns() -> list[str]:
    """v11's 61 value columns in its order (from a run on an empty day: its own schema)."""
    if "_tk" not in _CACHE:
        with tempfile.TemporaryDirectory(prefix="parity-schema-") as tmp:
            t, q = Path(tmp) / "t.csv", Path(tmp) / "q.csv"
            t.write_text(",".join(ticks.trade_columns()) + "\n", "utf-8")
            q.write_text(",".join(ticks.quote_columns()) + "\n", "utf-8")
            _CACHE["_tk"] = [c for c in ticks.tick_rows(t, q, "2024-03-06", 0,
                                                         groups=ticks.default_groups()).columns
                             if c not in ("sym", "m")]
    return list(_CACHE["_tk"])


_CACHE: dict[str, Any] = {}


def _ns_end(day: str, minute: int) -> int:
    """SIP ns of the end of table minute `minute` (from 04:00 New York)."""
    return ticks._bounds(day, minute + features.DAY_START_MINUTE)[1]


# --- raw ticks ------------------------------------------------------------------------------


def _source(inputs: ReplayInputs, kind: str) -> tuple[str, list[str]]:
    """SQL text of one raw file as v11 reads it (text columns, an empty text = missing) and
    the converter's columns of that kind."""
    w = ticks.converter()
    cols = list(w.TCOLS if kind == "trades" else w.QCOLS)
    path = str(inputs.trades if kind == "trades" else inputs.quotes).replace("'", "''")
    if inputs.tick_form == "flat":
        return _CSV.replace("?", f"'{path}'"), cols
    sel = ", ".join(f"NULLIF(CAST({c} AS VARCHAR), '') AS {c}" for c in cols)
    return f"(SELECT {sel} FROM read_parquet('{path}'))", cols


def check_audit(inputs: ReplayInputs) -> None:
    """Every value of the audit copy is what v11 casts (module docstring), or one refusal."""
    import duckdb

    con = duckdb.connect()
    try:
        for kind in ("trades", "quotes"):
            path = inputs.trades if kind == "trades" else inputs.quotes
            have = {r[0] for r in con.execute(
                "SELECT name FROM parquet_schema(?)", [str(path)]).fetchall()}
            src, cols = _source(inputs, kind)
            lost = [c for c in cols if c not in have]
            if lost:
                raise ModelRefused("tick_columns_missing", f"{kind}: {', '.join(lost)}")
            tests = [(c, f"TRY_CAST({c} AS BIGINT) IS NULL") for c in cols if c in BIGINT_COLUMNS]
            tests += [(c, f"TRY_CAST({c} AS DOUBLE) IS NULL") for c in cols
                      if c in DOUBLE_COLUMNS]
            tests += [(c, f"NOT regexp_full_match({c}, '{CODE_PATTERN}')") for c in cols
                      if c in CODE_COLUMNS]
            for c, bad in tests:
                n = con.execute(f"SELECT count(*) FROM {src} WHERE {c} IS NOT NULL AND {bad}"
                                ).fetchone()[0]
                if n:
                    raise ModelRefused("tick_value_invalid", f"{kind}.{c}: {n} values")
            for c in BIGINT_COLUMNS:
                n = con.execute(f"SELECT count(*) FROM {src} WHERE {c} IS NULL").fetchone()[0]
                if n:
                    raise ModelRefused("tick_value_invalid", f"{kind}.{c}: {n} missing")
    except duckdb.Error as exc:
        raise ModelRefused("tick_file_unreadable", type(exc).__name__) from exc
    finally:
        con.close()


def scan_events(inputs: ReplayInputs, day: str, symbols: set[str] | None) -> dict[str, Any]:
    """Per ticker (of `symbols`, None = every ticker): first event, final quote and quote count
    of the day; and the events per table minute of the regular session."""
    import duckdb

    start = _ns_end(day, -1)
    lo, hi = table_minutes(list(REGULAR))
    con = duckdb.connect()
    try:
        keep = ""
        if symbols is not None:
            con.execute("CREATE TEMP TABLE syms AS SELECT unnest(?) AS sym", [sorted(symbols)])
            keep = " WHERE ticker IN (SELECT sym FROM syms)"
        tsrc, _ = _source(inputs, "trades")
        qsrc, _ = _source(inputs, "quotes")
        both = (f"SELECT ticker, CAST(sip_timestamp AS BIGINT) AS ts, 'q' AS k FROM {qsrc}{keep}"
                f" UNION ALL SELECT ticker, CAST(sip_timestamp AS BIGINT), 't' FROM {tsrc}{keep}")
        con.execute(f"CREATE TEMP TABLE ev AS {both}")
        per = con.execute("SELECT ticker, min(ts), max(CASE WHEN k = 'q' THEN ts END), "
                          "count(*) FILTER (WHERE k = 'q') FROM ev GROUP BY 1 ORDER BY 1"
                          ).fetchall()
        minute = f"CAST((ts - {start}) // {features.NS_PER_MINUTE} AS BIGINT)"
        busy = con.execute(f"SELECT {minute} AS m, count(*) FROM ev WHERE {minute} BETWEEN "
                           f"{lo} AND {hi} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 1").fetchall()
    except duckdb.Error as exc:
        raise ModelRefused("tick_file_unreadable", type(exc).__name__) from exc
    finally:
        con.close()
    return {"first": {r[0]: r[1] for r in per},
            "final_quote": {r[0]: r[2] for r in per if r[2] is not None},
            "quotes": {r[0]: r[3] for r in per},
            "peak": busy[0][0] if busy else None}


def covered_tickers(inputs: ReplayInputs) -> set[str] | None:
    """The tickers of the audit copy (None in the flat form: every ticker is there)."""
    if inputs.tick_form == "flat":
        return None
    import duckdb

    con = duckdb.connect()
    try:
        got = set()
        for kind in ("trades", "quotes"):
            src, _ = _source(inputs, kind)
            got |= {r[0] for r in con.execute(f"SELECT DISTINCT ticker FROM {src}").fetchall()}
        return got
    except duckdb.Error as exc:
        raise ModelRefused("tick_file_unreadable", type(exc).__name__) from exc
    finally:
        con.close()


def extract_events(inputs: ReplayInputs, symbols: set[str], before: int, work: Path,
                   room: headroom.Headroom | None = None) -> None:
    """One pass over each day file: the converter's columns of the events of `symbols` stamped
    before `before` (ns), kept as the file's text in work/trades.parquet, work/quotes.parquet.
    With `room`, the engine runs within its limits and refuses to start below its reserves."""
    import duckdb
    import pandas as pd

    if room is not None:
        room.check("extract", 0)
    con = room.connect() if room is not None else duckdb.connect()
    try:
        con.register("syms", pd.DataFrame({"sym": sorted(symbols)}, dtype=object))
        for kind in ("trades", "quotes"):
            src, cols = _source(inputs, kind)
            out = work / f"{kind}.parquet"
            con.execute(f"COPY (SELECT {', '.join(cols)} FROM {src} WHERE ticker IN (SELECT sym "
                        f"FROM syms) AND CAST(sip_timestamp AS BIGINT) < {int(before)}) TO "
                        f"'{out}' (FORMAT parquet)")
    except duckdb.Error as exc:
        raise ModelRefused("tick_file_unreadable", type(exc).__name__) from exc
    finally:
        con.close()


def minute_ticks(room: headroom.Headroom, day: str, minute: int, marker: bool | str) -> Any:
    """The tick table of `day` as the bot holds it at the end of table minute `minute`: the
    events of <work>/*.parquet before that end (with the marker quotes of the variant: True or
    "marker" for every symbol with a quote, "marker-minute" for the symbols with a quote in the
    minute) through the frozen converter; rows after the minute are dropped. The prefix goes to
    the converter as zstd csv in a conversion folder of `room` (checked before it is written,
    removed after; `headroom`)."""
    w = ticks.converter()
    end, begin = _ns_end(day, minute), _ns_end(day, minute - 1)
    through = minute + features.DAY_START_MINUTE
    trades, quotes_file = room.work / "trades.parquet", room.work / "quotes.parquet"
    pick = f"CAST(sip_timestamp AS BIGINT) < {end}"
    if marker:
        through += 1  # the marker stands at the start of the next minute

    def selects(limit: int | None) -> tuple[str, str]:
        cut = "" if limit is None else (" ORDER BY CAST(sip_timestamp AS BIGINT), "
                                        f"CAST(sequence_number AS BIGINT) LIMIT {int(limit)}")
        t = f"SELECT {', '.join(w.TCOLS)} FROM read_parquet('{trades}') WHERE {pick}{cut}"
        q = (f"SELECT * FROM (SELECT {', '.join(w.QCOLS)} FROM read_parquet('{quotes_file}') "
             f"WHERE {pick}{cut})")
        if marker:
            made = {"ticker": "ticker", "sip_timestamp": f"'{end}'",
                    "sequence_number": "CAST(CAST(sequence_number AS BIGINT) + 1 AS VARCHAR)",
                    **MARKER_VALUES}
            latest = (f"SELECT * FROM ({q}) QUALIFY row_number() OVER (PARTITION BY ticker "
                      "ORDER BY CAST(sip_timestamp AS BIGINT) DESC, "
                      "CAST(sequence_number AS BIGINT) DESC) = 1")
            if marker == "marker-minute":
                latest = f"SELECT * FROM ({latest}) WHERE CAST(sip_timestamp AS BIGINT) >= {begin}"
            q = (f"{q} UNION ALL SELECT "
                 f"{', '.join(f'{made[c]} AS {c}' for c in w.QCOLS)} FROM ({latest})")
        return t, q

    def convert(folder: Path, limit: int | None) -> tuple[Any, int]:
        t, q = folder / "t.csv.zst", folder / "q.csv.zst"
        con = room.connect(folder)
        try:
            ts, qs = selects(limit)
            wrote = con.execute(f"COPY ({ts}) TO '{t}' (HEADER, DELIMITER ',')").fetchone()[0]
            wrote += con.execute(f"COPY ({qs}) TO '{q}' (HEADER, DELIMITER ',')").fetchone()[0]
        finally:
            con.close()
        return ticks.tick_rows(t, q, day, through, groups=ticks.default_groups(),
                               tmp_dir=folder, memory=room.duckdb_memory()), int(wrote)

    con = room.connect()
    try:
        rows = con.execute(f"SELECT (SELECT count(*) FROM read_parquet('{trades}') WHERE {pick})"
                           f" + (SELECT count(*) + count(DISTINCT ticker) FROM read_parquet("
                           f"'{quotes_file}') WHERE {pick})").fetchone()[0]
    finally:
        con.close()
    with room.conversion("prefix", int(rows), lambda f, n: convert(f, n)[1]) as folder:
        table = convert(folder, None)[0]
    return table[table.m <= minute + features.DAY_START_MINUTE].reset_index(drop=True)


def write_tick_file(table: Any, path: Path) -> Path:
    """The tick table as the training's tick file (parquet, v11's column types)."""
    import duckdb

    con = duckdb.connect()
    try:
        con.register("tk", table)
        con.execute(f"COPY (SELECT * FROM tk) TO '{path}' (FORMAT parquet)")
    finally:
        con.close()
    return path


# --- comparison -----------------------------------------------------------------------------


def _num(x: float) -> float | str:
    return x if math.isfinite(x) else str(x)


def column_kinds(want: Any, names: Sequence[str]) -> dict[str, str]:
    """The discrepancy class of each column (module docstring): rank by name, categorical by
    name, flag when every archive value of the day is 0 or 1, else value."""
    import numpy as np

    kinds = {}
    for c in names:
        if c.startswith(RANK_PREFIXES) or c.endswith(RANK_SUFFIX):
            kinds[c] = "rank"
        elif c in CATEGORICAL:
            kinds[c] = "categorical"
        else:
            v = want[c].to_numpy(np.float64) if c in want.columns else np.array([np.nan])
            v = v[~np.isnan(v)]
            kinds[c] = "flag" if len(v) and np.isin(v, (0.0, 1.0)).all() else "value"
    return kinds


def _ulps(x: Any, y: Any) -> Any:
    """Distance in float32 units in the last place (the stored type)."""
    import numpy as np

    def ordered(v):
        i = np.asarray(v, np.float32).view(np.int32).astype(np.int64)
        return np.where(i < 0, np.int64(-(2**31)) - i, i)

    with np.errstate(invalid="ignore", over="ignore"):
        return np.abs(ordered(x) - ordered(y))


def compare(got: Any, want: Any, groups: dict[str, list[str]],
            tolerance: tuple[float, float] | None, kinds: dict[str, str] | None = None,
            tick_symbols: set[str] | None = None,
            exceptions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Every feature column of `got` (replay) against `want` (training), joined on sym, exact;
    the tick groups only for `tick_symbols` when given. Each discrepancy cell is appended to
    `exceptions` (sym, column, group, class, values and differences)."""
    import numpy as np

    kinds = kinds or column_kinds(want, [c for cs in groups.values() for c in cs])
    a = got.set_index("sym")
    b = want.set_index("sym")
    common = [s for s in b.index if s in set(a.index)]
    order_equal = [s for s in a.index if s in set(b.index)] == common
    a, b = a.loc[common], b.loc[common]
    syms = np.array(common, dtype=object)
    out: dict[str, Any] = {
        "rows_reference": len(want), "rows_replay": len(got), "rows_compared": len(common),
        "only_in_reference": sorted(set(want.sym) - set(got.sym)),
        "only_in_replay": sorted(set(got.sym) - set(want.sym)), "order_equal": order_equal,
        "groups": {}, "columns": {}, "symbols": {}, "classes": dict.fromkeys(CLASSES, 0),
        "tick_rows_not_compared": 0}
    out["keys_exact"] = (order_equal and not out["only_in_reference"]
                         and not out["only_in_replay"])
    per_symbol = np.zeros(len(common), np.int64)
    covered = np.ones(len(common), bool)
    if tick_symbols is not None:
        covered = np.isin(syms, sorted(tick_symbols))
        out["tick_rows_not_compared"] = int((~covered).sum())
    for group, cols in groups.items():
        g = {"columns": len(cols), "columns_differ": 0, "value_mismatches": 0,
             "nan_pattern": 0, "max_abs": 0.0, "max_rel": 0.0}
        rows = covered if group in TICK_GROUPS else np.ones(len(common), bool)
        for c in cols:
            x = a[c].to_numpy(np.float64)
            y = b[c].to_numpy(np.float64)
            nx, ny = np.isnan(x), np.isnan(y)
            both = ~nx & ~ny & rows
            bad = both & (x != y)
            pattern = (nx != ny) & rows
            if not (bad.any() or pattern.any()):
                continue
            with np.errstate(invalid="ignore", over="ignore"):
                diff = np.where(bad, np.abs(x - y), 0.0)
                scale = np.maximum(np.abs(x), np.abs(y))
                rel = np.where(bad, diff / np.where(scale > 0, scale, 1.0), 0.0)
            # an infinite value against another value: an infinite difference, both ways
            diff = np.where(np.isnan(diff) | np.isinf(diff), math.inf, diff)
            rel = np.where(np.isinf(diff), math.inf, rel)
            ulp = np.where(bad, _ulps(x, y), 0)
            zero = bad & ((x == 0) | (y == 0))
            kind = kinds.get(c, "value")
            col = {"group": group, "class": kind, "value_mismatches": int(bad.sum()),
                   "nan_only_reference": int((ny & ~nx & rows).sum()),
                   "nan_only_replay": int((nx & ~ny & rows).sum()),
                   "max_abs": float(diff.max()) if len(diff) else 0.0,
                   "max_rel": float(np.nanmax(rel)) if len(rel) else 0.0,
                   "max_ulp": int(ulp.max()) if len(ulp) else 0,
                   "one_side_zero": int(zero.sum()),
                   "max_abs_one_side_zero": float(np.where(zero, diff, 0.0).max())
                   if len(diff) else 0.0,
                   "worst_symbol": str(syms[int(diff.argmax())]) if bad.any()
                   else str(syms[int(pattern.argmax())])}
            if tolerance is not None:
                close = np.isclose(x, y, rtol=tolerance[0], atol=tolerance[1])
                col["beyond_tolerance"] = int((both & ~close).sum())
            out["classes"][kind] += int(bad.sum())
            out["classes"]["missing"] += int(pattern.sum())
            if exceptions is not None:
                for i in np.nonzero(bad | pattern)[0]:
                    exceptions.append({
                        "sym": str(syms[i]), "column": c, "group": group,
                        "class": kind if bad[i] else "missing",
                        "reference": float(y[i]), "replay": float(x[i]),
                        "abs": float(diff[i]) if bad[i] else math.nan,
                        "rel": float(rel[i]) if bad[i] else math.nan,
                        "ulp": int(ulp[i]) if bad[i] else -1})
            per_symbol += bad | pattern
            g["columns_differ"] += 1
            g["value_mismatches"] += col["value_mismatches"]
            g["nan_pattern"] += int(pattern.sum())
            g["max_abs"] = max(g["max_abs"], col["max_abs"])
            g["max_rel"] = max(g["max_rel"], col["max_rel"])
            out["columns"][c] = {k: _num(v) if isinstance(v, float) else v
                                 for k, v in col.items()}
        out["groups"][group] = {k: _num(v) if isinstance(v, float) else v for k, v in g.items()}
    out["symbols"] = {str(s): int(n) for s, n in zip(syms, per_symbol, strict=True) if n}
    out["bar_groups_exact"] = (out["keys_exact"]
                               and all(out["groups"][g]["columns_differ"] == 0
                                       for g in BAR_GROUPS if g in out["groups"]))
    out["exact"] = out["keys_exact"] and not out["columns"]
    return out


def final_quote_cases(minute: int, rows: Any, scan: dict[str, Any], ticked: set[str],
                      cells: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    """The final-quote cases of `minute` (module docstring) from the exception cells of that
    minute against the archive."""
    import datetime

    begin, end = _ns_end(day, minute - 1), _ns_end(day, minute)
    close = _ns_end(day, table_minutes(["15:59"])[0])
    finals = sorted(s for s in rows if s in ticked
                    and begin <= scan["final_quote"].get(s, -1) < end)
    out = []
    for s in finals:
        own = [c for c in cells if c["sym"] == s]
        ts = scan["final_quote"][s]
        out.append({
            "sym": s, "minute": hhmm(minute), "quotes_of_day": scan["quotes"].get(s, 0),
            "final_quote": datetime.datetime.fromtimestamp(ts / 1e9, ticks.NEW_YORK)
            .strftime("%H:%M:%S.%f"),
            "stopped_before_close": ts < close,
            "value_changed": any(c["class"] in ("value", "flag", "categorical") for c in own),
            "missing_changed": any(c["class"] == "missing" for c in own),
            "rank_changed": any(c["class"] == "rank" for c in own),
            "other_rows_rank_changed": len({c["sym"] for c in cells
                                            if c["class"] == "rank" and c["sym"] not in finals})})
    return out


def _summary(cases: list[dict[str, Any]]) -> dict[str, int]:
    return {"cases": len(cases),
            "value_changed": sum(c["value_changed"] for c in cases),
            "missing_changed": sum(c["missing_changed"] for c in cases),
            "rank_changed": sum(c["rank_changed"] for c in cases),
            "minutes_with_downstream_rank_change": len({c["minute"] for c in cases
                                                        if c["other_rows_rank_changed"]}),
            "stopped_before_close": sum(c["stopped_before_close"] for c in cases)}


# --- the replay -----------------------------------------------------------------------------


def choose_minutes(day: str, bars: Any, start: int, rows_at: Any, scan_for: Any,
                   covered: set[str] | None) -> tuple[list[int], dict[str, list[str]]]:
    """The default minutes (module docstring) and why each was chosen."""
    labels = full.full_code().labels
    nm = full.full_code().w.NM
    base = table_minutes(list(BASE_MINUTES))
    candidates = covered
    if candidates is None:
        candidates = set().union(*(set(rows_at(m).sym) for m in base))
    scan = scan_for(candidates)
    why: dict[str, list[str]] = {hhmm(m): ["base"] for m in base}

    def add(m: int, reason: str) -> None:
        if 0 <= m < nm:
            why.setdefault(hhmm(m), []).append(reason)

    for s, ts in scan["final_quote"].items():
        add(int((ts - start) // features.NS_PER_MINUTE), f"final quote {s}")
    for s, ts in scan["first"].items():
        if ts < start:
            own = bars[bars.ticker == s].window_start
            if len(own):
                add(int((own.min() - start) // features.NS_PER_MINUTE), f"pre-04:00 carry {s}")
    if scan["peak"] is not None:
        add(int(scan["peak"]), "regular-session peak")
    with _training("replay_minute_invalid"):
        chosen = sorted(labels.cut_minute(x) for x in why)
    return chosen, why


def replay(day: str, minutes: Sequence[str] | None, inputs: ReplayInputs, *,
           variants: Sequence[str] = ("plain",), work: Path,
           tolerance: tuple[float, float] | None = None, all_minutes: bool = False,
           ticks_only: bool = False,
           exceptions: list[dict[str, Any]] | None = None,
           room: headroom.Headroom | None = None) -> dict[str, Any]:
    """The parity report of `day` at `minutes` (HH:MM; None = the default choice): see the
    module docstring. Exception cells are appended to `exceptions` when given. `room` guards
    disk and memory (default: `[parity]` of config/bot.toml on `work`); a minute it refuses
    stops the run, recorded in the report's `stopped`."""
    from tickr.model import massive

    need = day_files(day, inputs.bars)  # before any file is opened
    if inputs.tick_form not in FORMS:
        raise ModelRefused("replay_tick_form", inputs.tick_form)
    if any(v not in VARIANTS for v in variants) or not variants:
        raise ModelRefused("replay_variant", ", ".join(variants))
    covered = covered_tickers(inputs)
    if (covered is not None or ticks_only) and inputs.tk61 is None:
        raise ModelRefused("replay_tk61_missing", "the audit form and --ticks-only need tk61")
    if inputs.tick_form == "audit":
        check_audit(inputs)
    if minutes is not None:
        chosen = table_minutes(minutes)
    code = full.full_code()
    groups = full.full_groups()
    want = None if ticks_only else reference_table(inputs.table, day)
    kinds = None if want is None else column_kinds(want, full.full_features())
    types, types_md5 = _types(inputs.types)
    splits = _splits(inputs.reference) if not ticks_only else {}
    frames = [(d, massive.read_day_file((inputs.bars / f"{d}.csv.gz").read_bytes(), d))
              for d in need]
    state = features.history(frames[:-1], splits, day, types)
    bars = frames[-1][1]
    start = _ns_end(day, -1)

    def bars_through(minute: int) -> Any:
        return bars[bars.window_start <= start + minute * features.NS_PER_MINUTE]

    rows: dict[int, Any] = {}

    def rows_at(m: int) -> Any:
        if m not in rows:
            rows[m] = features.minute_rows(bars_through(m), day, m, state, types)
        return rows[m]

    scans: dict[str, Any] = {}

    def scan_for(symbols: set[str] | None) -> dict[str, Any]:
        scans["scan"] = scan_events(inputs, day, symbols)
        return scans["scan"]

    why: dict[str, list[str]] = {}
    if all_minutes:
        chosen = list(range(code.w.NM))
    elif minutes is None:
        chosen, why = choose_minutes(day, bars, start, rows_at, scan_for, covered)
    cohort = set().union(*(set(rows_at(m).sym) for m in chosen))
    ticked = cohort if covered is None else cohort & covered
    if "scan" not in scans:
        scan_for(ticked)
    if room is None:
        limits = headroom.Limits.from_config(config.load(DEFAULT_CONFIG).parity)
        room = headroom.Headroom(work, limits)
    extract_events(inputs, ticked, _ns_end(day, chosen[-1]), work, room)
    last = chosen[-1] + features.DAY_START_MINUTE
    fill = (tick_table(inputs.tk61, cohort - ticked, last)
            if covered is not None and cohort - ticked else None)
    tk61 = tick_table(inputs.tk61, ticked, last) if inputs.tk61 is not None else None
    report: dict[str, Any] = {
        "day": day, "minutes": [hhmm(m) for m in chosen], "chosen_because": why,
        "history": need[:-1], "inputs": {k: str(v) for k, v in inputs.__dict__.items()},
        "types_md5": types_md5, "types_is_training_pin": types_md5 == code.labels.TYPES_MD5,
        "tick_threads": os.environ.get("TICK_THREADS"), "threshold": "exact",
        "tolerance": None if tolerance is None else {"rtol": tolerance[0],
                                                     "atol": tolerance[1]},
        "tick_form": inputs.tick_form, "covered_tickers": None if covered is None
        else len(covered), "converted_symbols": len(ticked),
        "tick_rows_from_tk61_symbols": len(cohort - ticked) if fill is not None else 0,
        "bar_groups": list(BAR_GROUPS), "column_classes": kinds, "variants": {},
        "ticks": {}, "final_quote": {}, "versus_plain": {}, "resources": {}, "stopped": None,
        "limits": room.limits.__dict__}
    delivered: dict[str, dict[int, Any]] = {}
    for variant in variants:
        per: dict[str, Any] = {}
        tick_per: dict[str, Any] = {}
        cases: list[dict[str, Any]] = []
        delivered[variant] = {}
        report["resources"][variant] = used = {}
        for m in chosen:
            try:
                mine = minute_ticks(room, day, m, False if variant == "plain" else variant)
            except ModelRefused as exc:
                if exc.cause not in STOP_CAUSES:
                    raise
                report["stopped"] = {"variant": variant, "minute": hhmm(m), "cause": exc.cause,
                                     "detail": exc.detail, **room.usage()}
                break
            through = m + features.DAY_START_MINUTE
            if tk61 is not None:
                cells: list[dict[str, Any]] = []
                r = compare(mine[mine.m == through].drop(columns="m"),
                            tk61[tk61.m == through].drop(columns="m"),
                            {"ticks": _tick_value_columns()}, tolerance,
                            dict.fromkeys(_tick_value_columns(), "value"), None, cells)
                tick_per[hhmm(m)] = {"minute": m, **r}
                _collect(exceptions, cells, variant, m, "ticks")
            if ticks_only:
                used[hhmm(m)] = room.usage()
                continue
            table = mine
            if fill is not None:
                table = (_concat(mine, fill[fill.m <= through])
                         .sort_values(["sym", "m"], kind="stable").reset_index(drop=True))
            tick_file = write_tick_file(table, work / f"tk-{variant}-{m}.parquet")
            given = full.FullInputs(tick_file, inputs.reference, inputs.benzinga,
                                    inputs.fl_facts)
            got = full.minute_rows_full(bars_through(m), day, m, state, types, given)
            tick_file.unlink()
            used[hhmm(m)] = room.usage()
            delivered[variant][m] = got
            cells = []
            per[hhmm(m)] = {"minute": m, **compare(got, want[want.m == m], groups, tolerance,
                                                    kinds, covered, cells)}
            _collect(exceptions, cells, variant, m, "features")
            cases += final_quote_cases(m, set(got.sym), scans["scan"], ticked, cells, day)
        if not ticks_only:
            report["variants"][variant] = per
            report["final_quote"][variant] = {"summary": _summary(cases), "cases": cases}
        if tk61 is not None:
            report["ticks"][variant] = tick_per
        if report["stopped"] is not None:
            break
    if not ticks_only and "plain" in delivered:
        for variant in variants:
            if variant == "plain" or variant not in delivered:
                continue
            report["versus_plain"][variant] = {
                hhmm(m): compare(delivered[variant][m], delivered["plain"][m], groups, None,
                                 kinds, covered)["classes"] for m in chosen
                if m in delivered[variant] and m in delivered["plain"]}
    return report


def _concat(a: Any, b: Any) -> Any:
    import pandas as pd

    return pd.concat([a, b.astype(a.dtypes.to_dict())], ignore_index=True) if len(b) else a


def _collect(into: list[dict[str, Any]] | None, cells: list[dict[str, Any]], variant: str,
             minute: int, table: str) -> None:
    if into is not None:
        into.extend({"variant": variant, "minute": hhmm(minute), "table": table, **c}
                    for c in cells)


def text(report: dict[str, Any]) -> str:
    """The short text form of a report."""
    lines = [f"replay parity {report['day']}: minutes {' '.join(report['minutes'])}; "
             f"variants {', '.join(report['variants']) or 'none (ticks only)'}; TICK_THREADS "
             f"{report['tick_threads'] or 'unset'}; types md5 {report['types_md5'] or 'none'} "
             f"(training pin: {'yes' if report['types_is_training_pin'] else 'no'}); tick form "
             f"{report['tick_form']}, converted symbols {report['converted_symbols']}, tick rows "
             f"from tk61 for {report['tick_rows_from_tk61_symbols']}; threshold exact"]
    for variant, per in report["variants"].items():
        for hm, r in per.items():
            lines.append(
                f"{variant} {hm}: rows reference {r['rows_reference']} replay "
                f"{r['rows_replay']} compared {r['rows_compared']}; only in reference "
                f"{len(r['only_in_reference'])}, only in replay {len(r['only_in_replay'])}; "
                f"order equal {'yes' if r['order_equal'] else 'NO'}; bar groups exact: "
                f"{'yes' if r['bar_groups_exact'] else 'NO'}; exact: "
                f"{'yes' if r['exact'] else 'no'}; cells by class {r['classes']}")
            for group, g in r["groups"].items():
                if g["columns_differ"]:
                    lines.append(f"  {group}: {g['columns_differ']} of {g['columns']} columns "
                                 f"differ, {g['value_mismatches']} value cells, "
                                 f"{g['nan_pattern']} missing-pattern cells, max abs "
                                 f"{g['max_abs']}, max rel {g['max_rel']}")
            for name, c in r["columns"].items():
                extra = (f", beyond tolerance {c['beyond_tolerance']}"
                         if "beyond_tolerance" in c else "")
                lines.append(f"    {name} ({c['class']}): {c['value_mismatches']} values, "
                             f"missing only in reference {c['nan_only_reference']} / replay "
                             f"{c['nan_only_replay']}, max abs {c['max_abs']}, max rel "
                             f"{c['max_rel']}, max ulp {c['max_ulp']}, one side zero "
                             f"{c['one_side_zero']}, worst {c['worst_symbol']}{extra}")
        fq = report["final_quote"].get(variant)
        if fq:
            lines.append(f"{variant} final-quote cases: {fq['summary']}")
    for variant, per in report["ticks"].items():
        for hm, r in per.items():
            lines.append(f"ticks {variant} {hm}: rows tk61 {r['rows_reference']} ours "
                         f"{r['rows_replay']}; exact: {'yes' if r['exact'] else 'no'}; "
                         f"cells by class {r['classes']}; columns {sorted(r['columns'])}")
    for variant, per in report["versus_plain"].items():
        lines.append(f"{variant} versus plain, cells by class: "
                     + "; ".join(f"{hm} {c}" for hm, c in per.items()))
    for variant, per in report["resources"].items():
        if per:
            hm, last = max(per.items(), key=lambda x: x[1]["peak_rss_bytes"])
            lines.append(f"{variant} resources: peak resident {last['peak_rss_bytes']} bytes "
                         f"(reached by {hm}), least free disk "
                         f"{min(x['free_disk_bytes'] for x in per.values())} bytes, least "
                         f"available memory "
                         f"{min(x['available_memory_bytes'] for x in per.values())} bytes")
    if report["stopped"] is not None:
        st = report["stopped"]
        lines.append(f"STOPPED at {st['variant']} {st['minute']}: {st['cause']}: "
                     f"{st['detail']}")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tickr.model.parity",
                                description="Replay one open day minute by minute through the "
                                            "bot's feature path and compare with the "
                                            "training's feature table.")
    p.add_argument("--day", required=True, help="YYYY-MM-DD, an open (non-sealed) day")
    p.add_argument("--folder", type=Path, help="a local copy of the research layout")
    p.add_argument("--minutes", nargs="+", help="HH:MM inside 04:00 .. 19:59 (default: see doc)")
    p.add_argument("--all-minutes", action="store_true", help="every minute of the table day")
    p.add_argument("--bars", type=Path, help="minute_aggs folder")
    p.add_argument("--types", required=True, help="the training's types file, or - for none")
    for name in ("trades", "quotes", "reference", "benzinga", "fl", "table", "tk61"):
        p.add_argument(f"--{name}", type=Path)
    p.add_argument("--tick-form", choices=FORMS, help="flat files or the audit parquet copy")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--mode", choices=sorted(MODES), default="both", help="variants to run")
    p.add_argument("--ticks-only", action="store_true", help="only our v11 rows against tk61")
    p.add_argument("--rtol", type=float, help="relative tolerance to count cells beyond")
    p.add_argument("--atol", type=float, help="absolute tolerance to count cells beyond")
    p.add_argument("--work-dir", "--work", dest="work", type=Path,
                   help="folder for every temporary file (a new run folder is made in it and "
                        "removed at the end; conversion folders are removed per minute; "
                        "default: --out)")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                   help="bot.toml whose [parity] guards disk and memory")
    for name, key in (("disk-reserve-gb", "disk_reserve_gb"),
                      ("memory-reserve-gb", "memory_reserve_gb"),
                      ("duckdb-memory-gb", "duckdb_memory_gb")):
        p.add_argument(f"--{name}", type=int, dest=key,
                       help=f"overrides [parity] {key} (see config/bot.toml; the reserves "
                            "protect the live bot's disk and memory)")
    return p


def _limits(args: argparse.Namespace) -> headroom.Limits:
    """[parity] of --config with the command line's overrides."""
    import dataclasses

    p = config.load(args.config).parity
    given = {k: getattr(args, k) for k in ("disk_reserve_gb", "memory_reserve_gb",
                                            "duckdb_memory_gb") if getattr(args, k) is not None}
    if any(v < 1 for v in given.values()):
        raise ModelRefused("replay_limit_invalid", ", ".join(sorted(given)))
    return headroom.Limits.from_config(dataclasses.replace(p, **given))


def _inputs(args: argparse.Namespace) -> ReplayInputs:
    found = layout(args.folder, args.day) if args.folder is not None else {}
    pick = {k: getattr(args, k) or found.get(k) for k in ("bars", "table", "tk61", "trades",
                                                          "quotes")}
    need = ["bars", "trades", "quotes"] + ([] if args.ticks_only else ["table"])
    lost = [k for k in need if pick[k] is None]
    lost += [k for k in ("reference", "benzinga", "fl")
             if getattr(args, k) is None and not args.ticks_only]
    if lost:
        raise ModelRefused("replay_input_missing", ", ".join(lost))
    form = args.tick_form or ("audit" if str(pick["trades"]).endswith(".parquet") else "flat")
    none = Path(os.devnull)
    return ReplayInputs(pick["bars"], None if args.types == "-" else Path(args.types),
                        pick["trades"], pick["quotes"], args.reference or none,
                        args.benzinga or none, args.fl or none, pick["table"] or none,
                        pick["tk61"], form)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    tolerance = None
    if args.rtol is not None or args.atol is not None:
        tolerance = (args.rtol or 0.0, args.atol or 0.0)
    js, tx = args.out / f"parity-{args.day}.json", args.out / f"parity-{args.day}.txt"
    ex = args.out / f"parity-{args.day}-exceptions.csv"
    try:
        if args.minutes and args.all_minutes:
            raise ModelRefused("replay_minute_invalid", "--minutes or --all-minutes, not both")
        for f in (js, tx, ex):
            if f.exists():
                raise ModelRefused("output_exists", str(f))
        inputs = _inputs(args)
        limits = _limits(args)
        args.out.mkdir(parents=True, exist_ok=True)
        if args.work is not None:
            args.work.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="parity-work-", dir=args.work or args.out))
        cells: list[dict[str, Any]] = []
        saved = tempfile.tempdir
        try:
            room = headroom.Headroom(work, limits)
            # the pinned training code's own temporary folders land in the work folder too
            (work / "tmp").mkdir()
            tempfile.tempdir = str(work / "tmp")
            report = replay(args.day, args.minutes, inputs, variants=MODES[args.mode],
                            work=work, tolerance=tolerance, all_minutes=args.all_minutes,
                            ticks_only=args.ticks_only, exceptions=cells, room=room)
        finally:
            tempfile.tempdir = saved
            shutil.rmtree(work, ignore_errors=True)
    except ModelRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    report["exceptions"] = len(cells)
    with ex.open("w", newline="", encoding="utf-8") as f:
        out = csv.DictWriter(f, fieldnames=EXCEPTION_FIELDS)
        out.writeheader()
        out.writerows(cells)
    js.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", "utf-8")
    tx.write_text(text(report), "utf-8")
    print(tx.read_text("utf-8"), end="")
    if report["stopped"] is not None:
        print(f"STOPPED: {report['stopped']['cause']}: {report['stopped']['detail']}",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
