"""Protection tightened while the bot runs (backlog S5, runtime/liveprotection.py): a stricter
value in config/bot.toml takes effect on the next entry; a looser value, an invalid file or a
change of any other key is ignored for the run with a journal row and an alert; a file being
written is never read half old, half new."""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from baseline import write_baseline

from tickr import config
from tickr.api import contract as api
from tickr.api.settingsapply import edited
from tickr.runtime import service
from tickr.runtime.alerts import AlertKind
from tickr.runtime.liveprotection import (
    ADOPTED,
    IGNORED,
    Adoption,
    Ignored,
    IgnoredCause,
    LiveProtection,
    protection_specs,
)

from .test_entries import CALENDAR, CFG, NOW, ROOT
from .test_several_positions import Desk

pytestmark = pytest.mark.skipif(not config.trading_gate(CFG).open,
                                reason="the configured trading gate is closed")

SLOTS = "risk.max_open_positions"
CEILING = "risk.max_daily_loss_usd"


class Alerts:
    def __init__(self) -> None:
        self.sent: list[tuple[AlertKind, str]] = []

    def alert(self, kind: AlertKind, text: str) -> None:
        self.sent.append((kind, text))


class Evaluator:
    def __init__(self, desk) -> None:
        self.desk = desk


def write(path: Path, *changes: tuple[str, str]) -> None:
    text = path.read_text("utf-8")
    for key, value in changes:
        text = edited(text, key, value)
    api.write_atomic(path, text)


class Rig:
    """A bot that started on a config file with three position slots."""

    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "config" / "bot.toml"
        self.path.parent.mkdir()
        write_baseline(self.path)
        write(self.path, (SLOTS, "3"))
        self.cfg = config.load(self.path, ROOT)
        self.desk = Desk(tmp_path, self.cfg)
        self.alerts = Alerts()
        self.protection = LiveProtection(self.cfg, self.path)
        self.service = service.Service(
            self.desk.engine, now=lambda: NOW, calendar=CALENDAR,
            strategy=service.strategy_id(self.cfg),
            paths=service.ServicePaths(tmp_path / "status.json", tmp_path / "inbox",
                                       tmp_path / "processed"),
            evaluator=Evaluator(self.desk.desk), watch=self.alerts, protection=self.protection)

    def pass_(self) -> None:
        """The loop's safe point, before strategy work."""
        self.service.check_protection()

    def rows(self, kind: str) -> list[dict]:
        return [r.body for r in self.desk.store.journal(kind)]

    def in_force(self) -> dict[str, str]:
        return {p.key: p.value for p in self.service.status().protection_in_force}


def test_live_protection_is_the_at_once_risk_settings():
    keys = {s.key for s in protection_specs()}
    assert SLOTS in keys and CEILING in keys
    assert "risk.entry_snapshot_interval_seconds" not in keys  # applies when flat
    assert all(k.startswith("risk.") for k in keys)


def test_a_tightening_mid_session_takes_effect_on_the_next_entry(tmp_path):
    rig = Rig(tmp_path)
    assert rig.desk.enter("AAA").admission == "taken"
    rig.desk.fill("AAA")
    (stop,) = rig.desk.stops("AAA")
    write(rig.path, (SLOTS, "1"))
    rig.pass_()
    (row,) = rig.rows(ADOPTED)
    assert row["changes"] == [{"key": SLOTS, "before": "3", "after": "1"}]
    assert row["digest_before"] == rig.cfg.digest != row["digest_after"]
    record = rig.desk.enter("BBB")
    assert record.admission == "refused"
    assert record.refusal_cause.startswith("POSITION_LIMIT_REACHED: 1 open position(s), limit 1")
    # The open trade keeps the protection it was opened with: its stop is untouched.
    assert rig.desk.stops("AAA") == [stop]
    status = rig.service.status()
    adopted = {p.key: p for p in status.protection_in_force}[SLOTS]
    assert (adopted.value, adopted.adopted_at) == ("1", NOW)
    assert {p.key: p.adopted_at for p in status.protection_in_force}[CEILING] is None
    assert rig.alerts.sent == []
    rig.pass_()  # the same file again: nothing new
    assert len(rig.rows(ADOPTED)) == 1


def test_a_loosening_mid_session_is_ignored_until_the_next_session(tmp_path):
    rig = Rig(tmp_path)
    write(rig.path, (SLOTS, "4"))
    rig.pass_()
    assert rig.rows(ADOPTED) == []
    (row,) = rig.rows(IGNORED)
    assert row["cause"] == "loosens" and f"{SLOTS} 3 -> 4" in row["detail"]
    assert [k for k, _ in rig.alerts.sent] == [AlertKind.PROTECTION_CHANGE_IGNORED]
    assert rig.in_force()[SLOTS] == "3"
    for symbol in ("AAA", "BBB", "CCC"):
        assert rig.desk.enter(symbol).admission == "taken"
    assert rig.desk.enter("DDD").refusal_cause.startswith("POSITION_LIMIT_REACHED")
    rig.pass_()  # ignored once, not alerted again every pass
    assert len(rig.alerts.sent) == 1
    # The next session starts from the file.
    assert LiveProtection(config.load(rig.path, ROOT), rig.path).risk.max_open_positions == 4


def test_a_tightening_beside_a_loosening_is_never_half_applied(tmp_path):
    rig = Rig(tmp_path)
    write(rig.path, (SLOTS, "2"), (CEILING, "301"))
    rig.pass_()
    assert rig.rows(ADOPTED) == [] and rig.rows(IGNORED)[0]["cause"] == "loosens"
    assert rig.in_force()[SLOTS] == "3" and rig.in_force()[CEILING] == "300"


def test_an_invalid_file_is_ignored_with_an_alert_and_a_later_valid_one_is_adopted(tmp_path):
    rig = Rig(tmp_path)
    good = rig.path.read_text("utf-8")
    api.write_atomic(rig.path, edited(good, SLOTS, "0"))  # config.load refuses 0 slots
    rig.pass_()
    (row,) = rig.rows(IGNORED)
    assert row["cause"] == "invalid" and "max_open_positions" in row["detail"]
    assert [k for k, _ in rig.alerts.sent] == [AlertKind.PROTECTION_CHANGE_IGNORED]
    assert rig.in_force()[SLOTS] == "3"
    api.write_atomic(rig.path, good.replace("[risk]", "[risk", 1))  # does not parse
    rig.pass_()
    assert [r["cause"] for r in rig.rows(IGNORED)] == ["invalid", "invalid"]
    api.write_atomic(rig.path, edited(good, SLOTS, "2"))
    rig.pass_()
    assert rig.in_force()[SLOTS] == "2"


def test_a_change_outside_live_protection_is_ignored_whole(tmp_path):
    rig = Rig(tmp_path)
    write(rig.path, (SLOTS, "2"), ("risk.entry_snapshot_interval_seconds", "7"))
    rig.pass_()
    (row,) = rig.rows(IGNORED)
    assert row["cause"] == "other_keys"
    assert "risk.entry_snapshot_interval_seconds" in row["detail"]
    assert rig.in_force()[SLOTS] == "3"


def test_a_tightening_after_a_change_that_waits_for_a_restart_is_adopted(tmp_path):
    """The settings API answers restart_needed only for the when-flat change; the tightening
    applied after it in the same run must then take effect without a restart."""
    rig = Rig(tmp_path)
    snapshot = "risk.entry_snapshot_interval_seconds"
    write(rig.path, (snapshot, "7"))
    rig.pass_()
    assert [r["cause"] for r in rig.rows(IGNORED)] == ["other_keys"]
    write(rig.path, (SLOTS, "1"))
    rig.pass_()
    (row,) = rig.rows(ADOPTED)
    assert row["changes"] == [{"key": SLOTS, "before": "3", "after": "1"}]
    assert len(rig.rows(IGNORED)) == 1 and len(rig.alerts.sent) == 1
    # The waiting change is still not in force: it applies at the next start.
    assert rig.protection.config.risk.entry_snapshot_interval_seconds == \
        rig.cfg.risk.entry_snapshot_interval_seconds
    assert rig.desk.enter("AAA").admission == "taken"
    assert rig.desk.enter("BBB").refusal_cause.startswith("POSITION_LIMIT_REACHED")


def test_an_unreadable_file_is_reported_once_per_stretch(tmp_path):
    rig = Rig(tmp_path)
    rig.path.unlink()
    rig.pass_()
    rig.pass_()
    assert [r["cause"] for r in rig.rows(IGNORED)] == ["unreadable"]
    assert len(rig.alerts.sent) == 1


def test_a_file_caught_half_written_is_invalid_never_a_torn_mix(tmp_path):
    rig = Rig(tmp_path)
    write(rig.path, (CEILING, "250"))
    full = rig.path.read_bytes()
    reads = iter((full[: len(full) // 2], full))
    protection = LiveProtection(rig.cfg, rig.path, read=lambda _: next(reads))
    first = protection.check(NOW)
    assert isinstance(first, Ignored) and first.cause is IgnoredCause.INVALID
    assert protection.risk == rig.cfg.risk
    second = protection.check(NOW + timedelta(seconds=1))
    assert isinstance(second, Adoption)
    assert second.changes == ((CEILING, D(300), D(250)),)


def test_no_torn_read_while_the_settings_api_writes_the_file(tmp_path):
    """Each value the writer leaves is complete: the bot sees the old file or the new one."""
    rig = Rig(tmp_path)
    protection = LiveProtection(rig.cfg, rig.path)
    done = threading.Event()

    def writer() -> None:
        for ceiling in range(299, 199, -1):
            write(rig.path, (CEILING, str(ceiling)))
        done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    found = []
    while not done.is_set():
        found.append(protection.check(NOW))
    thread.join()
    found.append(protection.check(NOW))
    assert not [f for f in found if isinstance(f, Ignored)]
    assert protection.risk.max_daily_loss_usd == D(200)


def test_the_loop_adopts_a_tightening_each_pass(tmp_path):
    rig = Rig(tmp_path)
    rig.service.watch = None  # the loop's watch upkeep is not under test
    write(rig.path, (SLOTS, "2"))
    left = [1]

    def should_stop() -> bool:
        left[0] -= 1
        return left[0] < 0

    assert rig.service.run(should_stop=should_stop, sleep=lambda _: None,
                           monotonic=lambda: 0.0)
    assert [r["changes"][0]["after"] for r in rig.rows(ADOPTED)] == ["2"]
    status = api.read_status(tmp_path / "status.json")
    assert {p.key: p.value for p in status.protection_in_force}[SLOTS] == "2"
