from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from aperture import config
from aperture.broker.fake import FakeBroker
from aperture.calendar import NEW_YORK
from aperture.config import GateResult
from aperture.engine.core import Engine, Fence
from aperture.runtime import service
from aperture.runtime.alerts import Alerter, AlertKind
from aperture.runtime.watch import ALERT, BACKUP, BACKUP_FAILED, Watch
from aperture.store import Store

ROOT = Path(__file__).resolve().parents[2]
CFG = config.load(ROOT / "config" / "bot.toml")
MONDAY_11 = datetime(2026, 11, 16, 11, 0, tzinfo=NEW_YORK)
AFTER = float(CFG.alerts.fence_alert_after_seconds)


class Recorder:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []

    def notify(self, title: str, text: str) -> None:
        self.shown.append((title, text))


class Rig:
    def __init__(self, tmp_path: Path, broker: FakeBroker | None = None) -> None:
        self.cfg = replace(CFG, root=tmp_path)
        self.broker = broker or FakeBroker()
        self.store = Store.open(tmp_path / "db.sqlite3", busy_timeout_seconds=0)
        self.clock = [1_000.0]
        self.engine = Engine(self.store, self.broker, self.cfg, "watch001",
                             lambda: GateResult(True, "t"), clock=lambda: self.clock[0])
        self.now = MONDAY_11
        self.notifier = Recorder()
        self.watch = Watch(self.engine, self.cfg,
                           Alerter(self.cfg.alerts, "Aperture test", self.notifier,
                                   lambda: self.now),
                           lambda: self.now)

    def fence(self, fence: Fence, key: str) -> None:
        with self.store.transaction():
            self.engine._fence_row(fence, key, "test")

    def kinds(self) -> list[str]:
        return [r.body["kind"] for r in self.store.journal(ALERT)]


def test_operator_fence_alerts_at_once_and_only_once(tmp_path):
    rig = Rig(tmp_path)
    rig.fence(Fence.STOP_REJECTED, "t1")
    rig.watch.check()
    rig.watch.check()
    assert rig.kinds() == [AlertKind.STOP_REJECTED]
    assert rig.notifier.shown == [("Aperture test: stop-rejected", "stop-rejected:t1: test")]


def test_self_clearing_fence_alerts_only_when_it_lasts(tmp_path):
    rig = Rig(tmp_path)
    rig.fence(Fence.BROKER_READ, "")
    rig.clock[0] += AFTER - 1
    rig.watch.check()
    assert rig.kinds() == []
    rig.clock[0] += 1
    rig.watch.check()
    assert rig.kinds() == [AlertKind.BROKER_READS_FAILING]


def test_a_fence_set_again_after_clearing_alerts_again(tmp_path):
    rig = Rig(tmp_path)
    rig.fence(Fence.EXIT_REFUSED, "t1")
    rig.watch.check()
    rig.engine.clear_operator_fence("exit-refused:t1")
    rig.watch.check()
    rig.now += timedelta(seconds=float(CFG.alerts.dedupe_window_seconds))
    rig.fence(Fence.EXIT_REFUSED, "t1")
    rig.watch.check()
    assert rig.kinds() == [AlertKind.EXIT_REFUSED, AlertKind.EXIT_REFUSED]


def test_stream_lost_exit_alerts_once(tmp_path):
    rig = Rig(tmp_path)
    t = rig.engine.open_trade("ABC", D(1), D(10), D(9), D(1))
    rig.engine.request_exit(t, "stream-lost")
    rig.watch.check()
    rig.watch.check()
    assert rig.kinds() == [AlertKind.STREAM_LOST_WITH_POSITION]


def test_daily_backup_is_verified_and_only_the_latest_is_kept(tmp_path):
    rig = Rig(tmp_path)
    folder = tmp_path / CFG.run.data_dir / CFG.run.backups_dir
    folder.mkdir(parents=True)
    (folder / "aperture-2026-11-13.sqlite3").write_bytes(b"old")
    rig.watch.backup_daily()
    assert sorted(p.name for p in folder.iterdir()) == ["aperture-2026-11-16.sqlite3"]
    rig.watch.backup_daily()  # same day: nothing more
    assert len(rig.store.journal(BACKUP)) == 1
    rig.now += timedelta(days=1)
    rig.watch.backup_daily()
    assert sorted(p.name for p in folder.iterdir()) == ["aperture-2026-11-17.sqlite3"]


def test_backup_failure_is_journaled_and_keeps_the_old_one(tmp_path):
    rig = Rig(tmp_path)
    folder = tmp_path / CFG.run.data_dir / CFG.run.backups_dir
    folder.mkdir(parents=True)
    old = folder / "aperture-2026-11-13.sqlite3"
    old.write_bytes(b"old")

    def broken(target):
        raise OSError("disk full")

    rig.store.backup = broken
    rig.watch.backup_daily()
    assert old.exists()
    (row,) = rig.store.journal(BACKUP_FAILED)
    assert row.body["error"] == "OSError"


class NeverCancels(FakeBroker):
    def cancel(self, cid):
        pass


def test_drain_deadline_alerts(tmp_path):
    rig = Rig(tmp_path, broker=NeverCancels())
    rig.engine.open_trade("ABC", D(1), D(10), D(9), D(1))
    paths = service.ServicePaths(tmp_path / "status.json", tmp_path / "in", tmp_path / "out")
    bot = service.Service(rig.engine, now=lambda: rig.now, calendar=None,
                          strategy=service.strategy_id(CFG), paths=paths, watch=rig.watch)
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    assert not bot.run(should_stop=lambda: True, sleep=sleep, monotonic=lambda: clock[0])
    assert AlertKind.BOT_DID_NOT_STOP in rig.kinds()
