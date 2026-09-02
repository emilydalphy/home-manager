"""
Keeping a copy of the household's data.

Trust is the product's third principle: a home manager who loses the
household's data gets fired. Until now there were no backups at all --
and once the beta starts, it isn't only Emily's data at stake.

Two things protect the database, and they cover different failures:

- The **persistent volume** stops a redeploy wiping it. That's a
  deployment setting, checked in the beta-safety pass, and nothing in this
  file can enforce it -- but `warn_if_database_is_ephemeral` at least
  makes a missing one visible in the logs on day one instead of on the
  day the data goes missing.
- **These snapshots** cover what the volume can't: corruption, a bad
  migration, a code change that writes nonsense, or `reset_household.py`
  pointed at the wrong thing. All of those happily survive onto a
  perfectly healthy volume.

What this deliberately does NOT cover is losing the volume itself, since
the copies live on it. That needs an off-box destination, which is a
separate decision (Emily, 2026-09-01: build the on-volume copy now,
treat off-box as a follow-up).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta

from .db import DB_PATH

logger = logging.getLogger("home_manager")

# Kept alongside the database, so a mounted volume holds both. Overridable
# mainly so tests don't write into the real one.
BACKUP_DIR = os.environ.get("BACKUP_DIR") or os.path.join(os.path.dirname(DB_PATH), "backups")

# Enough to recover from "something has been wrong for a few days" --
# which is the realistic case, since a corruption or a bad migration is
# usually noticed well after it happened, not as it happens.
RETENTION_DAYS = 14

_PREFIX = "home_manager-"
_SUFFIX = ".db"


def backup_path_for(when: datetime | None = None) -> str:
    when = when or datetime.now()
    return os.path.join(BACKUP_DIR, f"{_PREFIX}{when.strftime('%Y-%m-%d')}{_SUFFIX}")


def create_backup(when: datetime | None = None) -> str | None:
    """
    Take a consistent snapshot of the database. Returns its path, or None
    if it couldn't be taken.

    Uses SQLite's own online backup rather than copying the file. That
    distinction is the whole point: this database runs in the default
    journal mode, so a plain file copy taken while a write is in flight
    can capture a torn file that looks perfectly fine and won't open when
    it's finally needed. `VACUUM INTO` produces a consistent snapshot
    without stopping the app, and the result is a normal database file --
    restoring is copying it back, no special tooling.

    Never raises. A backup that fails must not take the app down with it;
    that would turn a safety net into a new way to lose the service. It
    logs loudly instead, which is what the morning check is for.
    """
    when = when or datetime.now()
    target = backup_path_for(when)
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        # Same-day re-run replaces the day's snapshot rather than failing:
        # VACUUM INTO refuses to write over an existing file.
        if os.path.exists(target):
            os.remove(target)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("VACUUM INTO ?", (target,))
        finally:
            conn.close()
        logger.info("Database backup written to %s (%s bytes)", target, os.path.getsize(target))
        return target
    except Exception:
        logger.exception("Database backup FAILED — the household has no copy from today")
        return None


def prune_backups(keep_days: int = RETENTION_DAYS, now: datetime | None = None) -> list[str]:
    """
    Delete snapshots older than the retention window. Returns what it
    removed.

    Only ever touches files matching this module's own naming pattern, so
    pointing BACKUP_DIR somewhere unexpected can't make this delete
    something it didn't create. Never raises, for the same reason as
    create_backup.
    """
    now = now or datetime.now()
    cutoff = (now - timedelta(days=keep_days)).date()
    removed = []
    try:
        if not os.path.isdir(BACKUP_DIR):
            return removed
        for name in os.listdir(BACKUP_DIR):
            if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
                continue
            stamp = name[len(_PREFIX):-len(_SUFFIX)]
            try:
                taken = datetime.strptime(stamp, "%Y-%m-%d").date()
            except ValueError:
                # Not one of ours after all — leave it alone.
                continue
            if taken < cutoff:
                path = os.path.join(BACKUP_DIR, name)
                os.remove(path)
                removed.append(path)
        if removed:
            logger.info("Pruned %d backup(s) older than %d days", len(removed), keep_days)
    except Exception:
        logger.exception("Pruning old backups failed")
    return removed


def list_backups() -> list[dict]:
    """What copies exist right now, newest first — the answer to "am I actually covered?"."""
    out = []
    try:
        if not os.path.isdir(BACKUP_DIR):
            return out
        for name in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if name.startswith(_PREFIX) and name.endswith(_SUFFIX):
                path = os.path.join(BACKUP_DIR, name)
                out.append({
                    "name": name,
                    "path": path,
                    "bytes": os.path.getsize(path),
                    "taken": name[len(_PREFIX):-len(_SUFFIX)],
                })
    except Exception:
        logger.exception("Listing backups failed")
    return out


def warn_if_database_is_ephemeral() -> bool:
    """
    Say out loud, at startup, where the database actually lives. Returns
    True if it looks like it will not survive a redeploy.

    There is no deployment config in the repo -- the volume and DB_PATH
    exist only in the hosting dashboard -- so nothing in the code can
    enforce them. What it can do is refuse to be quiet: without this, a
    missing volume is invisible until the day the data is gone, and by
    then the logs that would have explained it have rotated away.
    """
    configured = bool(os.environ.get("DB_PATH"))
    if configured:
        logger.info("Database: %s (DB_PATH is set)", DB_PATH)
        return False
    logger.warning(
        "Database: %s — DB_PATH is NOT set. On a hosting platform this is the "
        "container's own disk, which is wiped on every redeploy, taking the "
        "household's data with it. Set DB_PATH to a mounted volume "
        "(e.g. /data/home_manager.db). Fine if this is a local machine.",
        DB_PATH,
    )
    return True


def run_daily_maintenance() -> dict:
    """
    One snapshot, then prune. The whole job, in the order that matters:
    take today's copy first, so a failure while pruning can't leave the
    household with fewer copies than it started with.
    """
    created = create_backup()
    pruned = prune_backups()
    return {"created": created, "pruned": pruned, "kept": len(list_backups())}
