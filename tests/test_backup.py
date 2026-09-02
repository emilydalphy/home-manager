"""
The household's data survives more than a redeploy.

A backup that has never been restored is a hope, not a backup — so the
central test here doesn't just check a file appears, it destroys the
database and brings it back from the copy.
"""
import os
import sqlite3
from datetime import datetime, timedelta

import pytest

from app import backup as backup_module
from app import tools
from app.db import DB_PATH, get_conn


@pytest.fixture
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    monkeypatch.setattr(backup_module, "BACKUP_DIR", str(d))
    return d


def test_a_backup_is_a_real_database_you_can_restore_from(backup_dir):
    """
    The test the ticket asked for by name. Take a snapshot, destroy the
    database, restore, and confirm the household's data is actually there.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])
    tools.add_grocery_item("beans")

    snapshot = backup_module.create_backup()
    assert snapshot and os.path.exists(snapshot)

    # Destroy the real thing — not a simulation of loss, actual loss.
    conn = get_conn()
    conn.execute("DELETE FROM recipes")
    conn.execute("DELETE FROM grocery_items")
    conn.commit()
    conn.close()
    assert tools.list_recipes() == []

    # Restore is a file copy. That is the whole procedure, deliberately:
    # a snapshot is an ordinary database file, so recovery needs no
    # special tooling and no one has to remember a command under pressure.
    with open(snapshot, "rb") as src, open(DB_PATH, "wb") as dst:
        dst.write(src.read())

    restored = [r["name"] for r in tools.list_recipes()]
    assert restored == ["Chili"], "the restored database must contain the household's data"
    assert [i["item"] for i in tools.list_grocery_list()] == ["beans"]


def test_the_snapshot_is_consistent_while_the_app_is_writing(backup_dir):
    """
    Why VACUUM INTO and not a file copy: this database runs in the default
    journal mode, where a plain copy taken mid-write can capture a torn
    file that looks fine and won't open when it is finally needed.
    """
    tools.add_recipe("Chili", ingredients=[{"item": "beans", "qty": "1 tin"}])

    writer = get_conn()
    writer.execute("BEGIN")
    writer.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, status) "
        "VALUES (1, 'mid-flight', '1', 'other', 'needed')"
    )
    snapshot = backup_module.create_backup()   # taken with a write open
    writer.rollback()
    writer.close()

    assert snapshot is not None
    copy = sqlite3.connect(snapshot)
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert copy.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 1
    copy.close()


def test_running_twice_in_a_day_replaces_that_days_copy(backup_dir):
    first = backup_module.create_backup()
    second = backup_module.create_backup()
    assert first == second, "one snapshot per day, not one per restart"
    assert len(backup_module.list_backups()) == 1


def test_old_copies_are_pruned_but_recent_ones_are_kept(backup_dir):
    now = datetime(2026, 9, 1)
    for age in (0, 3, 13, 15, 40):
        backup_module.create_backup(when=now - timedelta(days=age))

    removed = backup_module.prune_backups(keep_days=14, now=now)

    kept = {b["taken"] for b in backup_module.list_backups()}
    assert len(removed) == 2, "the 15- and 40-day-old copies should go"
    assert "2026-09-01" in kept and "2026-08-29" in kept
    assert "2026-08-19" in kept, "13 days old is inside the window"
    assert "2026-07-23" not in kept


def test_pruning_never_touches_files_it_did_not_create(backup_dir):
    """
    Deleting files on a schedule deserves a hard boundary. If BACKUP_DIR
    ever points somewhere unexpected, this must not clear it out.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    bystander = backup_dir / "something-else-entirely.db"
    bystander.write_text("not ours")
    (backup_dir / "home_manager-not-a-date.db").write_text("also not ours")

    backup_module.prune_backups(keep_days=0, now=datetime(2030, 1, 1))

    assert bystander.exists(), "a file this module didn't create must be left alone"
    assert (backup_dir / "home_manager-not-a-date.db").exists()


def test_a_failed_backup_reports_itself_instead_of_crashing(backup_dir, monkeypatch):
    """
    A safety net must not become a new way to lose the service — but it
    also must not fail silently, or the household is uncovered and nobody
    knows.
    """
    monkeypatch.setattr(backup_module, "BACKUP_DIR", "/proc/nonexistent-and-unwritable")

    assert backup_module.create_backup() is None
    assert backup_module.run_daily_maintenance()["created"] is None


def test_an_ephemeral_database_is_called_out_loudly(monkeypatch, caplog):
    """
    The volume can't be enforced from the code — the deployment settings
    live in the hosting dashboard. What the code can do is refuse to be
    quiet, so a missing volume shows up on day one rather than on the day
    the data goes missing.
    """
    import logging

    monkeypatch.delenv("DB_PATH", raising=False)
    with caplog.at_level(logging.WARNING, logger="home_manager"):
        assert backup_module.warn_if_database_is_ephemeral() is True
    assert "wiped on every redeploy" in caplog.text

    monkeypatch.setenv("DB_PATH", "/data/home_manager.db")
    assert backup_module.warn_if_database_is_ephemeral() is False
