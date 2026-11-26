"""The harness's disk and memory guard: the machine's own readers answer, and a conversion
folder's peak counts the files the converter removes before its end."""

from __future__ import annotations

import os
from decimal import Decimal

from tickr.model import headroom


def test_the_machine_readers_answer(tmp_path):
    assert headroom.available_memory() > 0
    assert headroom.peak_rss() > 0
    assert headroom.free_bytes(tmp_path) > 0


def test_a_file_removed_inside_a_conversion_counts_in_its_peak(tmp_path):
    limits = headroom.Limits(disk_reserve=1, memory_reserve=1, duckdb_memory=headroom.GB,
                             probe_rows=1000, poll_seconds=float(Decimal(3600)))
    room = headroom.Headroom(tmp_path, limits, free=lambda p: 10**15, memory=lambda: 10**15)
    with room.conversion("kind", 100) as folder:
        (folder / "big").write_bytes(b"x" * 5000)
        os.remove(folder / "big")  # no poll comes in an hour: the removal is sampled
        (folder / "small").write_bytes(b"x" * 10)
    assert room.measured["kind"] == (100, 50.0)
    assert not folder.exists()
