import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from threading import Barrier

import pytest

from coffee_house.domain.pricing import Selection, calculate_quote, in_stock
from coffee_house.storage.sqlite import (
    AvailabilityStore,
    Change,
    OperationConflict,
    RevisionConflict,
    StorageUnavailable,
    wal_supported,
)


def test_seed_only_new_base_and_restart(store, catalog):
    first = store.snapshot()
    assert first.revision == 0 and first.confirmed
    assert len(first.products) == 72 and len(first.extras) == 3
    assert {k for k, v in first.products.items() if not v} == {"iced_oreo_latte"}
    assert {k for k, v in first.extras.items() if not v} == {"leche_almendras"}
    result = store.save("save-1", "admin-test", 0, [Change("product", "iced_oreo_latte", True),
                                                 Change("extra", "leche_almendras", True)])
    restarted = AvailabilityStore(store.path, catalog)
    restarted.initialize()
    assert restarted.snapshot() == store.snapshot()
    assert restarted.operation("save-1", "admin-test") == result
    assert len(restarted.history()) == 2
    assert restarted.snapshot().products["iced_oreo_latte"]


def test_product_all_sizes_and_extra_independent(store, catalog):
    store.save("availability", "admin-test", 0, [Change("product", "hot_latte", False),
                                               Change("extra", "espresso_extra", False)])
    snapshot = store.snapshot()
    for size in ("mediano", "grande"):
        assert not in_stock(calculate_quote(catalog, Selection("hot_latte", size, ())), snapshot)
        assert in_stock(calculate_quote(catalog, Selection("hot_cappuccino", size, ())), snapshot)
        assert not in_stock(calculate_quote(catalog, Selection("hot_cappuccino", size, ("espresso_extra",))), snapshot)


def test_lost_response_retry_reordered_batch_and_actor(store):
    changes = [Change("product", "hot_latte", False), Change("extra", "espresso_extra", False)]
    result = store.save("lost-response", "admin-test", 0, changes)
    store.save("later-save", "admin-test", 1, [Change("product", "hot_latte", True)])
    assert store.operation("lost-response", "admin-test") == result
    assert store.operation("lost-response", "other-admin") is None
    assert store.save("lost-response", "admin-test", 0, list(reversed(changes))) == result
    assert store.snapshot().revision == 2 and store.snapshot().products["hot_latte"]
    assert len(store.history()) == 3
    for actor, rev, batch in (("other-admin", 0, changes), ("admin-test", 1, changes),
                              ("admin-test", 0, [Change("product", "hot_latte", True)])):
        with pytest.raises(OperationConflict):
            store.save("lost-response", actor, rev, batch)


def test_stale_revision_rolls_back_and_does_not_record_operation(store):
    store.save("first", "admin-test", 0, [Change("product", "hot_latte", False)])
    with pytest.raises(RevisionConflict) as error:
        store.save("stale", "admin-test", 0, [Change("product", "hot_latte", True)])
    assert error.value.current_revision == 1
    assert store.operation("stale", "admin-test") is None
    assert not store.snapshot().products["hot_latte"]
    assert len(store.history()) == 1


def test_failure_mid_batch_rolls_back_state_history_operation_revision(store):
    before = store.snapshot()
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON availability WHEN OLD.identifier='hot_latte' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(StorageUnavailable):
        store.save("failed", "admin-test", 0, [Change("extra", "espresso_extra", False),
                                              Change("product", "hot_latte", False)])
    assert store.snapshot() == before
    assert store.history() == () and store.operation("failed", "admin-test") is None
    with sqlite3.connect(store.path) as db:
        db.execute("DROP TRIGGER fail_second")
    assert store.save("failed", "admin-test", 0, [Change("product", "hot_latte", False)]).revision == 1


def test_history_failure_also_rolls_back_availability(store):
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT,'test history failure'); END")
    with pytest.raises(StorageUnavailable):
        store.save("history-fail", "admin-test", 0, [Change("product", "hot_latte", False)])
    assert store.snapshot().products["hot_latte"] and store.snapshot().revision == 0
    assert store.history() == () and store.operation("history-fail", "admin-test") is None


def test_two_concurrent_writers_one_wins(store, catalog):
    barrier = Barrier(2)

    def write(number):
        other = AvailabilityStore(store.path, catalog)
        barrier.wait(timeout=5)
        try:
            return other.save(f"concurrent-{number}", "admin-test", 0, [Change("product", "hot_latte", False)])
        except RevisionConflict as conflict:
            return conflict

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(write, range(2)))
    assert sum(isinstance(r, RevisionConflict) for r in results) == 1
    assert store.snapshot().revision == 1 and len(store.history()) == 1


def test_concurrent_identical_retries_only_one_operation(store, catalog):
    barrier = Barrier(2)

    def write(_):
        other = AvailabilityStore(store.path, catalog)
        barrier.wait(timeout=5)
        return other.save("same-operation", "admin-test", 0, [Change("product", "hot_latte", False)])

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(write, range(2)))
    assert results[0] == results[1]
    assert len(store.history()) == 1 and store.snapshot().revision == 1


def test_noop_does_not_invent_change_but_records_retry(store):
    result = store.save("noop", "admin-test", 0, [Change("product", "hot_latte", True)])
    assert result.changed == 0 and result.revision == 0
    assert store.history() == ()
    assert store.save("noop", "admin-test", 0, [Change("product", "hot_latte", True)]) == result


def test_history_utc_order_pagination_and_full_retention(store):
    for number in range(5):
        store.save(f"history-{number}", "admin-test", number,
                   [Change("product", "hot_latte", number % 2 == 1)])
    first = store.history(limit=2)
    following = store.history(after_sequence=first[-1].sequence)
    all_rows = first + following
    assert len(all_rows) == 5 and all_rows == store.history()
    for number, row in enumerate(all_rows):
        assert row.revision == number + 1
        assert row.administrator == "admin-test" and row.identifier == "hot_latte"
        assert row.before != row.after
        assert datetime.fromisoformat(row.saved_at).utcoffset().total_seconds() == 0


def test_backup_and_restore_to_new_path(store, catalog, tmp_path):
    result = store.save("backup-op", "admin-test", 0, [Change("product", "hot_latte", False)])
    backup = store.backup(tmp_path / "backups" / "backup.sqlite3")
    copied = AvailabilityStore(backup, catalog)
    restored_path = copied.backup(tmp_path / "restored" / "restored.sqlite3")
    restored = AvailabilityStore(restored_path, catalog)
    restored.initialize()
    assert restored.snapshot() == store.snapshot()
    assert restored.history() == store.history()
    assert restored.operation("backup-op", "admin-test") == result
    store.save("after-backup", "admin-test", 1, [Change("product", "hot_latte", True)])
    assert not restored.snapshot().products["hot_latte"]
    with pytest.raises(FileExistsError):
        store.backup(backup)
    with pytest.raises(FileExistsError):
        store.backup(store.path)


def test_backup_during_writes_consistent(store, catalog, tmp_path):
    def write():
        for number in range(12):
            store.save(f"backup-concurrent-{number}", "admin-test", number,
                       [Change("product", "hot_latte", number % 2 == 1)])

    with ThreadPoolExecutor(max_workers=1) as worker:
        job = worker.submit(write)
        destination = store.backup(tmp_path / "during-writes.sqlite3")
        job.result(timeout=10)
    copy = AvailabilityStore(destination, catalog)
    snapshot = copy.snapshot()
    assert len(copy.history()) == snapshot.revision
    assert snapshot.products["hot_latte"] == (snapshot.revision % 2 == 0)


def test_crash_without_commit_recovers_previous_state(store):
    store.save("committed", "admin-test", 0, [Change("product", "hot_latte", False)])
    code = "import sqlite3,sys,os; db=sqlite3.connect(sys.argv[1]); db.execute('BEGIN IMMEDIATE'); db.execute(\"UPDATE availability SET available=1 WHERE identifier='hot_latte'\"); db.execute('UPDATE metadata SET revision=99'); os._exit(7)"
    result = subprocess.run([sys.executable, "-B", "-c", code, str(store.path)], timeout=10, check=False)
    assert result.returncode == 7
    assert store.snapshot().revision == 1 and not store.snapshot().products["hot_latte"]
    assert len(store.history()) == 1


def test_committed_state_visible_in_new_process(store):
    store.save("restart", "admin-test", 0, [Change("product", "hot_latte", False)])
    code = "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); assert db.execute('SELECT revision FROM metadata').fetchone()[0]==1; assert db.execute(\"SELECT available FROM availability WHERE identifier='hot_latte'\").fetchone()[0]==0; assert db.execute('SELECT count(*) FROM history').fetchone()[0]==1; assert db.execute(\"SELECT count(*) FROM operations WHERE operation_id='restart'\").fetchone()[0]==1"
    subprocess.run([sys.executable, "-B", "-c", code, str(store.path)], timeout=10, check=True)


def test_uncommitted_changes_not_visible(store):
    with sqlite3.connect(store.path) as other:
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE availability SET available=0 WHERE identifier='hot_latte'")
        assert store.snapshot().products["hot_latte"]
        other.rollback()


def test_lock_timeout_does_not_confirm_or_retry(store, catalog):
    impatient = AvailabilityStore(store.path, catalog, busy_timeout_ms=30)
    with sqlite3.connect(store.path) as locker:
        locker.execute("BEGIN IMMEDIATE")
        with pytest.raises(StorageUnavailable):
            impatient.save("locked", "admin-test", 0, [Change("product", "hot_latte", False)])
        locker.rollback()
    assert store.operation("locked", "admin-test") is None
    assert store.snapshot().revision == 0 and store.snapshot().products["hot_latte"]


def test_missing_database_is_not_implicitly_created(tmp_path, catalog):
    path = tmp_path / "missing.sqlite3"
    store = AvailabilityStore(path, catalog)
    for action in (store.snapshot, store.history, lambda: store.operation("unknown", "admin-test"),
                   lambda: store.save("unknown", "admin-test", 0, [Change("product", "hot_latte", False)])):
        with pytest.raises(StorageUnavailable):
            action()
    assert not path.exists()


def test_failed_backup_removes_only_its_new_file(tmp_path, catalog):
    store = AvailabilityStore(tmp_path / "missing.sqlite3", catalog)
    target = tmp_path / "failed-backup.sqlite3"
    with pytest.raises(StorageUnavailable):
        store.backup(target)
    assert not target.exists() and not store.path.exists()


@pytest.mark.parametrize("damage", ["schema", "catalog", "row", "unrelated"])
def test_invalid_existing_base_not_reseeded(store, catalog, damage):
    with sqlite3.connect(store.path) as db:
        if damage == "schema":
            db.execute("PRAGMA user_version=999")
        elif damage == "catalog":
            db.execute("UPDATE metadata SET catalog_identity='different'")
        elif damage == "row":
            db.execute("DELETE FROM availability WHERE identifier='hot_latte'")
        else:
            db.execute("DROP TABLE metadata")
    before = store.path.read_bytes()
    with pytest.raises(StorageUnavailable):
        AvailabilityStore(store.path, catalog).initialize()
    assert store.path.read_bytes() == before
    with pytest.raises(StorageUnavailable):
        store.snapshot()


def test_catalog_changed_requires_review(store, catalog):
    changed = replace(catalog, products={k: v for k, v in catalog.products.items() if k != "hot_latte"})
    with pytest.raises(StorageUnavailable):
        AvailabilityStore(store.path, changed).initialize()


@pytest.mark.parametrize("changes", [[], [Change("product", "missing", False)],
                                     [Change("variant", "hot_latte", False)], [Change("product", "hot_latte", 0)],
                                     [Change("product", "hot_latte", False)] * 2])
def test_invalid_batch_does_not_write(store, changes):
    before = store.snapshot()
    with pytest.raises(ValueError):
        store.save("invalid", "admin-test", 0, changes)
    assert store.snapshot() == before and store.history() == ()


@pytest.mark.parametrize("operation,actor,revision", [("", "admin-test", 0), ("op", "", 0),
                                                     ("op", "admin-test", True), ("op", "admin-test", -1),
                                                     ("op\n", "admin-test", 0)])
def test_invalid_identity_and_revision(store, operation, actor, revision):
    with pytest.raises(ValueError):
        store.save(operation, actor, revision, [Change("product", "hot_latte", False)])


def test_configuration_and_private_files(store, tmp_path):
    config = store.configuration()
    assert config["journal_mode"] == "delete"
    assert config["synchronous"] == 2 and config["foreign_keys"] == 1
    assert config["busy_timeout_ms"] == 1000 and config["schema_version"] == 1
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.path.parent.stat().st_mode & 0o777 == 0o700
        backup = store.backup(tmp_path / "copy.sqlite3")
        assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("version,supported", [((3, 40, 1), False), ((3, 50, 4), False), ((3, 51, 2), False),
                                              ((3, 44, 6), True), ((3, 50, 7), True), ((3, 51, 3), True)])
def test_wal_version_gate(version, supported):
    assert wal_supported(version) is supported


def test_unsupported_wal_refuses_before_creating_file(tmp_path, catalog):
    if wal_supported(sqlite3.sqlite_version_info):
        pytest.skip("La biblioteca instalada acredita corrección WAL-reset.")
    path = tmp_path / "wal.sqlite3"
    with pytest.raises(ValueError, match="WAL bloqueado"):
        AvailabilityStore(path, catalog, journal_mode="WAL")
    assert not path.exists()
