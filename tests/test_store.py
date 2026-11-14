from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from aperture.store import MIGRATIONS, Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "data" / "aperture.sqlite3")
    yield s
    s.close()


def test_open_migrates_and_uses_wal(store):
    assert store.schema_version == len(MIGRATIONS)
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store._db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL


def test_reopen_is_idempotent(tmp_path):
    path = tmp_path / "a.sqlite3"
    Store.open(path).close()
    s = Store.open(path)
    assert s.schema_version == len(MIGRATIONS)
    s.close()


def test_newer_schema_refused(tmp_path):
    path = tmp_path / "a.sqlite3"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store.open(path)


def test_append_requires_transaction(store):
    with pytest.raises(RuntimeError):
        store.append("x", {})


def test_rollback_discards_rows(store):
    with pytest.raises(ValueError), store.transaction():
        store.append("intent", {"price": Decimal("1.23")})
        raise ValueError("boom")
    assert store.journal() == []


def test_journal_is_append_only(store):
    with store.transaction():
        store.append("intent", {"price": Decimal("1.23")}, key="ap-1")
    rows = store.journal(kind="intent", key="ap-1")
    assert rows[0].body == {"price": "1.23"}
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._db.execute("UPDATE journal SET kind = 'x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._db.execute("DELETE FROM journal")


def test_nested_transaction_refused(store):
    with pytest.raises(RuntimeError, match="nested"), store.transaction(), store.transaction():
        pass


def test_runs_recorded(store):
    run_id = store.start_run(environment="env", config_digest="d", strategy="s",
                             strategy_version="1", code_version="abc")
    store.end_run(run_id, "stopped")
    assert store.run_count() == 1
    assert [r.kind for r in store.journal(key=run_id)] == ["run-started", "run-ended"]


def test_backup_is_verified(store, tmp_path):
    with store.transaction():
        store.append("intent", {"n": 1})
    target = tmp_path / "backup" / "copy.sqlite3"
    store.backup(target)
    copy = Store.open(target)
    assert len(copy.journal()) == 1
    copy.close()
    with pytest.raises(FileExistsError):
        store.backup(target)
