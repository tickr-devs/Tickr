"""Fleet runner of the tick history (ops/aws/training/jobs/tickrun.py), in its local test mode.

The same claim rules run on files instead of S3 objects (exclusive create, overwrite only of
the object one has seen). The converter is a stub: it needs the day's two raw files, writes the
day's table and adds a line to a ledger, so a day converted twice is visible.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[2] / "ops/aws/training/jobs/tickrun.py"
DAYS = ["2021-11-15", "2021-11-16", "2021-11-17", "2021-11-18", "2021-11-19"]  # Mon..Fri
STUB = """
import os, sys, time
dt, _, wd, out = sys.argv[1:5]
raw = [os.path.join(wd, "%s-%s.csv.gz" % (dt, k)) for k in ("trades", "quotes")]
assert all(os.path.exists(f) for f in raw)
time.sleep(float(os.environ.get("STUB_SLEEP", "0.2")))
os.makedirs(os.path.join(out, dt[:4]), exist_ok=True)
with open(os.path.join(out, "ledger.txt"), "a") as f:
    f.write(dt + "\\n")
open(os.path.join(out, dt[:4], dt + ".parquet"), "w").write(dt)
[os.remove(f) for f in raw]
print("day", dt)
"""
WAIT = 30  # seconds a test waits for a runner at most


def setup(tmp_path, days=DAYS):
    fake = tmp_path / "fake"
    fake.mkdir()
    for d in days:
        for kind in ("trades", "quotes"):
            (fake / f"{d}-{kind}.csv.gz").write_bytes(f"{d} {kind}".encode())
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    (tmp_path / "out" / "claims").mkdir(parents=True)
    return stub


def start(tmp_path, stub, name, **env):
    e = dict(os.environ)
    e.update(
        RUN_FAKE=str(tmp_path / "fake"),
        RUN_FETCH="2",
        RUN_CONV="2",
        RUN_AHEAD="1",
        RUN_FLOOR_GB="0",
        RUN_WAIT_SEC="0.3",
        RUN_CLAIM_MIN="0.02",
        RUN_RAW=str(tmp_path / "raw"),
    )
    e.update(env)
    log = open(tmp_path / f"{name}.log", "w")
    args = [sys.executable, str(RUNNER), str(stub), DAYS[0], DAYS[-1], "0/1"]
    return subprocess.Popen(
        args + [str(tmp_path / name), str(tmp_path / "out")], env=e, stdout=log, stderr=log
    )


def tables(tmp_path):
    return sorted(p.stem for p in (tmp_path / "out").glob("*/*.parquet"))


def ledger(tmp_path):
    f = tmp_path / "out" / "ledger.txt"
    return sorted(f.read_text().split()) if f.exists() else []


def claims(tmp_path):
    return sorted(p.stem for p in (tmp_path / "out" / "claims").iterdir())


def test_two_machines_convert_every_day_once(tmp_path):
    stub = setup(tmp_path)
    a, b = start(tmp_path, stub, "a"), start(tmp_path, stub, "b")
    assert a.wait(WAIT) == 0 and b.wait(WAIT) == 0
    assert ledger(tmp_path) == DAYS  # each day once, by one machine
    assert tables(tmp_path) == DAYS
    assert claims(tmp_path) == []
    assert sorted(p.stem for p in (tmp_path / "rawman").glob("*/*.json")) == DAYS
    for kind in ("trades", "quotes"):
        assert len(list((tmp_path / "raw" / kind).glob("*/*.csv.gz"))) == len(DAYS)


def test_rerun_skips_everything_and_missing_files_are_a_holiday(tmp_path):
    stub = setup(tmp_path, DAYS[:3])  # no files for the last two days
    assert start(tmp_path, stub, "a").wait(WAIT) == 0
    assert start(tmp_path, stub, "a").wait(WAIT) == 0
    assert ledger(tmp_path) == DAYS[:3]


def test_raw_switch_off_keeps_no_raw(tmp_path):
    stub = setup(tmp_path)
    p = start(tmp_path, stub, "a", RUN_RAW="")
    assert p.wait(WAIT) == 0
    assert tables(tmp_path) == DAYS and not (tmp_path / "raw").exists()


def test_tables_done_raw_record_missing_is_archived_only(tmp_path):
    stub = setup(tmp_path)
    assert start(tmp_path, stub, "a", RUN_RAW="").wait(WAIT) == 0
    assert start(tmp_path, stub, "a").wait(WAIT) == 0
    assert ledger(tmp_path) == DAYS  # nothing was converted again
    assert sorted(p.stem for p in (tmp_path / "rawman").glob("*/*.json")) == DAYS


def test_sigterm_stops_fast_and_releases_claims(tmp_path):
    stub = setup(tmp_path)
    p = start(tmp_path, stub, "a", STUB_SLEEP="60")
    deadline = time.time() + WAIT
    while not claims(tmp_path) and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(1)
    p.send_signal(signal.SIGTERM)
    assert p.wait(WAIT) == 3
    assert claims(tmp_path) == [] and tables(tmp_path) == []


def test_days_of_a_dead_machine_are_taken_over(tmp_path):
    stub = setup(tmp_path)
    old, fresh = (tmp_path / "out" / "claims" / f"{d}.json" for d in DAYS[:2])
    old.write_text("dead")
    os.utime(old, (time.time() - 3600,) * 2)
    fresh.write_text("dead")  # goes stale while the runner works on the other days
    assert start(tmp_path, stub, "a").wait(WAIT) == 0
    assert ledger(tmp_path) == DAYS and claims(tmp_path) == []


def test_never_exits_zero_while_another_machine_holds_a_day(tmp_path):
    stub = setup(tmp_path)
    held = tmp_path / "out" / "claims" / f"{DAYS[0]}.json"
    held.write_text("alive")
    p = start(tmp_path, stub, "a", RUN_RAW="")
    t0 = time.time()
    while time.time() - t0 < 4:  # the other machine keeps its claim fresh
        os.utime(held)
        time.sleep(0.2)
    assert p.poll() is None  # own share finished long ago, the runner still waits
    assert ledger(tmp_path) == DAYS[1:]
    # the other machine finishes its day: table first, then the claim goes
    (tmp_path / "out" / "2021" / f"{DAYS[0]}.parquet").write_text("x")
    held.unlink()
    assert p.wait(WAIT) == 0
    assert ledger(tmp_path) == DAYS[1:]


def test_lost_claim_kills_the_converter_and_writes_nothing(tmp_path):
    stub = setup(tmp_path, DAYS[:1])
    p = start(tmp_path, stub, "a", STUB_SLEEP="60", RUN_CLAIM_MIN="0.05")
    mine = tmp_path / "out" / "claims" / f"{DAYS[0]}.json"
    deadline = time.time() + WAIT
    while not mine.exists() and time.time() < deadline:
        time.sleep(0.1)
    time.sleep(1)
    mine.write_text("other")  # another machine took the day over
    t0 = time.time()
    log = tmp_path / "a.log"
    while "CLAIM LOST" not in log.read_text() and time.time() - t0 < WAIT:
        os.utime(mine)
        time.sleep(0.2)
    assert "CLAIM LOST" in log.read_text()
    p.send_signal(signal.SIGTERM)
    assert p.wait(WAIT) == 3
    assert mine.read_text() == "other"  # the other machine's claim is not deleted
    assert tables(tmp_path) == [] and "FAILED" not in log.read_text()
