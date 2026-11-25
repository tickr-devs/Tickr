"""Replay parity: the bot's feature path replayed on one open historical day, minute by minute,
against the training's own feature table of that day.

The tool for "parity on a real minute": no live feed, only the day's files. It is run by the
owner (Mac or AWS) on one open day:

    python -m tickr.model.parity --day 2024-03-06 --minutes 04:30 09:31 09:45 12:00 15:30 19:59 \
        --bars <minute_aggs folder> --types <types.csv or -> --trades <trades_v1 day file> \
        --quotes <quotes_v1 day file> --reference <prepared reference folder> \
        --benzinga <Benzinga folder> --fl <float facts csv> --table <training feature table of
        the day, parquet> --out <report folder> [--marker] [--rtol R] [--atol A]

INPUTS. `--bars`: a folder of minute_aggs day files `YYYY-MM-DD.csv.gz` holding the day and the 20
trading sessions before it (the files the training's builder opens). `--types`: the training's
types file (`-` = no type filter); its md5 is reported beside the training's pin. `--trades` /
`--quotes`: the day's Massive flat files (csv or csv.gz, header row). `--reference`,
`--benzinga`, `--fl`: the training-format inputs of `tickr.model.features.full` (input
contract there); the splits are read from `<reference>/splits.jsonl`, which must match its
manifest line, with the training's parser. `--table`: the feature table the training wrote for
the day (`l1feat.py` output: date, sym, m, sess and the 191 features).

DATES. Before any file is opened, the day must pass the pinned training rules (`l1labels.needed`
on the bar folder's NAMES only): a sealed day (2026-07-01 .. 2026-08-14, or after the research
archive's last day, the HARD_LAST of the pinned l1labels.py, not the live replacement), a day
off the calendar, the embargo after the sealed window, history reaching into the sealed window
or before the archive, or a missing bar file refuses with the training's own cause. Sealed data
is never read.

REPLAY, for each chosen minute m (HH:MM, 04:00 .. 19:59), as the bot would see it at the end of m:
1. bars: the day's bars with window_start <= m, the DayState from the 20 sessions before
   (`features.history`, splits applied) -> `features.minute_rows`: the rows (symbols) of m;
2. ticks: trades and quotes of those symbols with sip_timestamp before the end of m -> the
   frozen converter v11 through `ticks.tick_rows` -> the tick table of the day so far (the
   converter partitions every column by symbol, so keeping only the row symbols is exact);
3. `full.minute_rows_full` with that tick table and the reference, Benzinga and fl inputs.

MARKER QUOTE (`--marker`, a proposal for Codex, findings item 4; the report then holds both
variants). v11 weighs a quote update until the next update of the stock, capped at the end of
its minute; the stock's latest update so far has no next one yet and weighs 0, while in the
training's whole-day file it weighs up to its minute end whenever the stock quotes again that
day. The marker variant appends, before converting, ONE synthetic quote per symbol that has a
quote in the prefix, stamped exactly at the start of minute m+1 (sip_timestamp = end of m in
ns), and drops every tick row after m. Its fields, exactly:
- ticker: the symbol; sip_timestamp: the start of minute m+1 (ns);
- sequence_number: the sequence number of the symbol's latest quote in the prefix + 1 (the
  latest = largest (sip_timestamp, sequence_number), v11's order);
- ask_price, ask_size, bid_price, bid_size: those of that latest quote (MARKER_VALUES), so the
  marker repeats the standing quote and v11's validity test (bid > 0, ask > bid, both sizes
  > 0) gives it the standing quote's state; conditions and indicators: empty (no halt flag).
What v11 reads of it for minutes <= m is its stamp alone, through `lead(ts)` (the weight d of
the update before it); every value of the marker lands in the row of minute m+1, which is
dropped (and `l1feat.cut_inputs` drops it again). Trades are untouched (the as-of join takes
quotes strictly before a trade; no trade of the prefix is after the marker). So minute m and
every earlier minute get exactly the training's values for every stock that quotes again later
that day; a stock that never quotes again keeps the training's 0 weight in the training table
and gets its minute-end weight here: that difference is what the report shows.

REPORT (`<out>/parity-<day>.json` and `.txt`; an existing report refuses). Per variant, minute
and column, over the symbols in both tables: exact value mismatches (both values present),
missing-pattern differences apart (missing only in the reference / only in the replay), the
largest absolute and relative difference (|a - b| / max(|a|, |b|)) and the symbol where the
absolute one is largest; per symbol, the number of columns that differ; symbols in one table
only. The 65 bar-group columns (bars, lists, market, cross, hist) must be exact (`bar_groups_
exact`); for the others the numbers are reported and no pass threshold is set here (Codex sets
the tolerances). `--rtol` / `--atol` (numpy `isclose`, either alone counts with the other 0)
add the count of cells beyond that tolerance; neither has a default. Values are compared as the
table stores them (float32 features, int m and sess). Note: v11 with several engine threads
(TICK_THREADS) is not bit-deterministic (last-bit differences, up to about 4e-16 relative), and
the training's tick files carry that noise; set TICK_THREADS=1 for a bit-stable replay side.
"""

from __future__ import annotations

import argparse
import contextlib
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

from tickr.model import features
from tickr.model.errors import ModelRefused
from tickr.model.features import full, ticks

# The groups computed from the minute bars alone: the live path and the training share every
# input there, so any difference is a defect, never noise.
BAR_GROUPS = features.LIVE_GROUPS
VARIANTS = ("plain", "marker")
# The marker's quote values (module docstring): SQL over the symbol's latest quote of the prefix.
MARKER_VALUES = {"ask_price": "ask_price", "ask_size": "ask_size", "bid_price": "bid_price",
                 "bid_size": "bid_size", "conditions": "NULL", "indicators": "NULL"}
_CSV = "read_csv(?, header=true, all_varchar=true, delim=',', quote='\"', escape='\"')"


@dataclass(frozen=True)
class ReplayInputs:
    """The day's files (see the module docstring); `types` None = no type filter."""

    bars: Path
    types: Path | None
    trades: Path
    quotes: Path
    reference: Path
    benzinga: Path
    fl_facts: Path
    table: Path


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


def reference_table(path: Path, day: str) -> Any:
    """The training's feature table of `day`: one row per (sym, m), every feature present."""
    import duckdb

    con = duckdb.connect()
    try:
        frame = con.execute("SELECT * FROM read_parquet(?)", [str(path)]).df()
    except duckdb.Error as exc:
        raise ModelRefused("replay_table_unreadable", type(exc).__name__) from exc
    finally:
        con.close()
    missing = [c for c in ["date", "sym", *full.full_features()] if c not in frame.columns]
    if missing:
        raise ModelRefused("replay_table_columns", ", ".join(missing))
    if (frame["date"].astype(str) != day).any():
        raise ModelRefused("replay_table_wrong_day", day)
    if frame.duplicated(["sym", "m"]).any():
        raise ModelRefused("replay_table_key_duplicate", day)
    return frame


def _ns_end(day: str, minute: int) -> int:
    """SIP ns of the end of table minute `minute` (from 04:00 New York)."""
    return ticks._bounds(day, minute + features.DAY_START_MINUTE)[1]


def extract_events(inputs: ReplayInputs, symbols: set[str], before: int, work: Path) -> None:
    """One pass over each day file: the converter's columns of the events of `symbols` stamped
    before `before` (ns), kept as the file's text in work/trades.parquet, work/quotes.parquet."""
    import duckdb
    import pandas as pd

    w = ticks.converter()
    con = duckdb.connect()
    try:
        con.register("syms", pd.DataFrame({"sym": sorted(symbols)}, dtype=object))
        for name, src, cols in (("trades", inputs.trades, w.TCOLS),
                                ("quotes", inputs.quotes, w.QCOLS)):
            out = work / f"{name}.parquet"
            con.execute(f"COPY (SELECT {', '.join(cols)} FROM {_CSV} WHERE ticker IN (SELECT sym "
                        f"FROM syms) AND CAST(sip_timestamp AS BIGINT) < {int(before)}) TO "
                        f"'{out}' (FORMAT parquet)", [str(src)])
    except duckdb.Error as exc:
        raise ModelRefused("tick_file_unreadable", type(exc).__name__) from exc
    finally:
        con.close()


def minute_ticks(work: Path, day: str, minute: int, marker: bool) -> Any:
    """The tick table of `day` as the bot holds it at the end of table minute `minute`: the
    events of work/*.parquet before that end (with the marker quotes when `marker`) through
    the frozen converter; rows after the minute are dropped."""
    import duckdb

    w = ticks.converter()
    end = _ns_end(day, minute)
    through = minute + features.DAY_START_MINUTE
    t, q = work / "t.csv", work / "q.csv"
    pick = f"CAST(sip_timestamp AS BIGINT) < {end}"
    quotes = f"SELECT {', '.join(w.QCOLS)} FROM read_parquet('{work / 'quotes.parquet'}') " \
             f"WHERE {pick}"
    if marker:
        made = {"ticker": "ticker", "sip_timestamp": f"'{end}'",
                "sequence_number": "CAST(CAST(sequence_number AS BIGINT) + 1 AS VARCHAR)",
                **MARKER_VALUES}
        latest = (f"SELECT * FROM ({quotes}) QUALIFY row_number() OVER (PARTITION BY ticker "
                  "ORDER BY CAST(sip_timestamp AS BIGINT) DESC, "
                  "CAST(sequence_number AS BIGINT) DESC) = 1")
        quotes = (f"{quotes} UNION ALL SELECT "
                  f"{', '.join(f'{made[c]} AS {c}' for c in w.QCOLS)} FROM ({latest})")
        through += 1  # the marker stands at the start of the next minute
    con = duckdb.connect()
    try:
        con.execute(f"COPY (SELECT {', '.join(w.TCOLS)} FROM read_parquet("
                    f"'{work / 'trades.parquet'}') WHERE {pick}) TO '{t}' (HEADER, DELIMITER ',')")
        con.execute(f"COPY ({quotes}) TO '{q}' (HEADER, DELIMITER ',')")
    finally:
        con.close()
    table = ticks.tick_rows(t, q, day, through)
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


def _num(x: float) -> float | str:
    return x if math.isfinite(x) else str(x)


def compare(got: Any, want: Any, groups: dict[str, list[str]],
            tolerance: tuple[float, float] | None) -> dict[str, Any]:
    """Every feature column of `got` (replay) against `want` (training), joined on sym."""
    import numpy as np

    a = got.set_index("sym")
    b = want.set_index("sym")
    common = sorted(set(a.index) & set(b.index))
    a, b = a.loc[common], b.loc[common]
    syms = np.array(common, dtype=object)
    out: dict[str, Any] = {
        "rows_reference": len(want), "rows_replay": len(got), "rows_compared": len(common),
        "only_in_reference": sorted(set(want.sym) - set(got.sym)),
        "only_in_replay": sorted(set(got.sym) - set(want.sym)),
        "groups": {}, "columns": {}, "symbols": {}}
    per_symbol = np.zeros(len(common), np.int64)
    for group, cols in groups.items():
        g = {"columns": len(cols), "columns_differ": 0, "value_mismatches": 0,
             "nan_pattern": 0, "max_abs": 0.0, "max_rel": 0.0}
        for c in cols:
            x = a[c].to_numpy(np.float64)
            y = b[c].to_numpy(np.float64)
            nx, ny = np.isnan(x), np.isnan(y)
            both = ~nx & ~ny
            bad = both & (x != y)
            pattern = nx != ny
            if not (bad.any() or pattern.any()):
                continue
            with np.errstate(invalid="ignore", over="ignore"):
                diff = np.where(bad, np.abs(x - y), 0.0)
                scale = np.maximum(np.abs(x), np.abs(y))
                rel = np.where(bad, diff / np.where(scale > 0, scale, 1.0), 0.0)
            # an infinite value against another value: an infinite difference, both ways
            diff = np.where(np.isnan(diff) | np.isinf(diff), math.inf, diff)
            rel = np.where(np.isinf(diff), math.inf, rel)
            col = {"group": group, "value_mismatches": int(bad.sum()),
                   "nan_only_reference": int((ny & ~nx).sum()),
                   "nan_only_replay": int((nx & ~ny).sum()),
                   "max_abs": float(diff.max()) if len(diff) else 0.0,
                   "max_rel": float(np.nanmax(rel)) if len(rel) else 0.0,
                   "worst_symbol": str(syms[int(diff.argmax())]) if bad.any()
                   else str(syms[int(pattern.argmax())])}
            if tolerance is not None:
                close = np.isclose(x, y, rtol=tolerance[0], atol=tolerance[1])
                col["beyond_tolerance"] = int((both & ~close).sum())
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
    out["bar_groups_exact"] = (not out["only_in_reference"] and not out["only_in_replay"]
                               and all(out["groups"][g]["columns_differ"] == 0
                                       for g in BAR_GROUPS))
    return out


def replay(day: str, minutes: Sequence[str], inputs: ReplayInputs, *, marker: bool,
           work: Path, tolerance: tuple[float, float] | None = None) -> dict[str, Any]:
    """The parity report of `day` at `minutes` (HH:MM): see the module docstring."""
    from tickr.model import massive

    need = day_files(day, inputs.bars)  # before any file is opened
    chosen = table_minutes(minutes)
    code = full.full_code()
    groups = full.full_groups()
    want = reference_table(inputs.table, day)
    types, types_md5 = _types(inputs.types)
    splits = _splits(inputs.reference)
    frames = [(d, massive.read_day_file((inputs.bars / f"{d}.csv.gz").read_bytes(), d))
              for d in need]
    state = features.history(frames[:-1], splits, day, types)
    bars = frames[-1][1]
    start = _ns_end(day, -1)

    def bars_through(minute: int) -> Any:
        return bars[bars.window_start <= start + minute * features.NS_PER_MINUTE]

    rows = {m: features.minute_rows(bars_through(m), day, m, state, types) for m in chosen}
    symbols = set().union(*(set(r.sym) for r in rows.values()))
    extract_events(inputs, symbols, _ns_end(day, chosen[-1]), work)
    report: dict[str, Any] = {
        "day": day, "minutes": [hhmm(m) for m in chosen], "history": need[:-1],
        "inputs": {k: str(v) for k, v in inputs.__dict__.items()},
        "types_md5": types_md5, "types_is_training_pin": types_md5 == code.labels.TYPES_MD5,
        "tick_threads": os.environ.get("TICK_THREADS"),
        "tolerance": None if tolerance is None else {"rtol": tolerance[0],
                                                     "atol": tolerance[1]},
        "bar_groups": list(BAR_GROUPS), "variants": {}}
    for variant in VARIANTS if marker else VARIANTS[:1]:
        per: dict[str, Any] = {}
        for m in chosen:
            tick_file = write_tick_file(minute_ticks(work, day, m, variant == "marker"),
                                        work / f"tk-{variant}-{m}.parquet")
            given = full.FullInputs(tick_file, inputs.reference, inputs.benzinga,
                                    inputs.fl_facts)
            got = full.minute_rows_full(bars_through(m), day, m, state, types, given)
            per[hhmm(m)] = {"minute": m, **compare(got, want[want.m == m], groups, tolerance)}
        report["variants"][variant] = per
    return report


def text(report: dict[str, Any]) -> str:
    """The short text form of a report."""
    lines = [f"replay parity {report['day']}: minutes {' '.join(report['minutes'])}; "
             f"variants {', '.join(report['variants'])}; TICK_THREADS "
             f"{report['tick_threads'] or 'unset'}; types md5 {report['types_md5'] or 'none'} "
             f"(training pin: {'yes' if report['types_is_training_pin'] else 'no'})"]
    for variant, per in report["variants"].items():
        for hm, r in per.items():
            lines.append(
                f"{variant} {hm}: rows reference {r['rows_reference']} replay "
                f"{r['rows_replay']} compared {r['rows_compared']}; only in reference "
                f"{len(r['only_in_reference'])}, only in replay {len(r['only_in_replay'])}; "
                f"bar groups exact: {'yes' if r['bar_groups_exact'] else 'NO'}")
            for group, g in r["groups"].items():
                if g["columns_differ"]:
                    lines.append(f"  {group}: {g['columns_differ']} of {g['columns']} columns "
                                 f"differ, {g['value_mismatches']} value cells, "
                                 f"{g['nan_pattern']} missing-pattern cells, max abs "
                                 f"{g['max_abs']}, max rel {g['max_rel']}")
            for name, c in r["columns"].items():
                extra = (f", beyond tolerance {c['beyond_tolerance']}"
                         if "beyond_tolerance" in c else "")
                lines.append(f"    {name}: {c['value_mismatches']} values, missing only in "
                             f"reference {c['nan_only_reference']} / replay "
                             f"{c['nan_only_replay']}, max abs {c['max_abs']}, max rel "
                             f"{c['max_rel']}, worst {c['worst_symbol']}{extra}")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tickr.model.parity",
                                description="Replay one open day minute by minute through the "
                                            "bot's feature path and compare with the "
                                            "training's feature table.")
    p.add_argument("--day", required=True, help="YYYY-MM-DD, an open (non-sealed) day")
    p.add_argument("--minutes", required=True, nargs="+", help="HH:MM inside 04:00 .. 19:59")
    p.add_argument("--bars", required=True, type=Path, help="minute_aggs folder")
    p.add_argument("--types", required=True, help="the training's types file, or - for none")
    for name in ("trades", "quotes", "reference", "benzinga", "fl", "table", "out"):
        p.add_argument(f"--{name}", required=True, type=Path)
    p.add_argument("--marker", action="store_true", help="also run the marker-quote variant")
    p.add_argument("--rtol", type=float, help="relative tolerance to count cells beyond")
    p.add_argument("--atol", type=float, help="absolute tolerance to count cells beyond")
    p.add_argument("--work", type=Path, help="scratch folder (default: a new one in --out)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inputs = ReplayInputs(args.bars, None if args.types == "-" else Path(args.types),
                          args.trades, args.quotes, args.reference, args.benzinga, args.fl,
                          args.table)
    tolerance = None
    if args.rtol is not None or args.atol is not None:
        tolerance = (args.rtol or 0.0, args.atol or 0.0)
    js, tx = args.out / f"parity-{args.day}.json", args.out / f"parity-{args.day}.txt"
    try:
        for f in (js, tx):
            if f.exists():
                raise ModelRefused("output_exists", str(f))
        args.out.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="parity-work-", dir=args.work or args.out))
        try:
            report = replay(args.day, args.minutes, inputs, marker=args.marker, work=work,
                            tolerance=tolerance)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    except ModelRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    js.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", "utf-8")
    tx.write_text(text(report), "utf-8")
    print(tx.read_text("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
