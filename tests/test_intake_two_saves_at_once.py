"""
Two adults saving the week's answers at the same moment (defect hunt,
2026-10-22).

save_week_intake read the current revision outside any transaction, so the
second save could read it BEFORE the first one committed and read the
highest revision number AFTER — and then wrote revision+2 cleanly, built on
the stale answers. The first adult's answer vanished from the week and their
row stayed live beside the new one. The UNIQUE index only caught the case
where both reads landed on the same side of the commit.

The interleaving is forced, not hoped for: the second save pauses right
after its read and lets the first one try to finish. Before the fix the
first one finished inside that pause; now it waits for the lock.
"""
import threading

from app import tools
from app.db import get_conn
from app.tools import week_intake as _week_intake

WEEK = "2026-11-02"


def _race(monkeypatch, first: dict, second: dict) -> list:
    paused = threading.Event()
    first_done = threading.Event()
    errors = []
    original = _week_intake._current_intake_row

    def read_then_pause(conn, week_start):
        row = original(conn, week_start)
        if threading.current_thread().name == "second" and not paused.is_set():
            paused.set()
            first_done.wait(timeout=1.0)
        return row

    monkeypatch.setattr(_week_intake, "_current_intake_row", read_then_pause)

    def run_second():
        try:
            tools.save_week_intake(WEEK, **second)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    def run_first():
        paused.wait(timeout=5)
        try:
            tools.save_week_intake(WEEK, **first)
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
        finally:
            first_done.set()

    threads = [threading.Thread(target=run_second, name="second"),
               threading.Thread(target=run_first, name="first")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return errors


def _live_rows() -> int:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM week_intake WHERE household_id = ? AND week_start = ? "
            "AND superseded_at IS NULL",
            (tools.household_id(), WEEK),
        ).fetchone()[0]
    finally:
        conn.close()


def test_two_halves_saved_at_once_both_stand(monkeypatch):
    tools.save_week_intake(WEEK, moods=["Comfort food"])
    errors = _race(monkeypatch, first={"cuisines": ["Thai"]}, second={"freeform": "Use up the corn"})
    assert errors == []
    week = tools.get_week_intake(WEEK)
    assert week["cuisines"] == ["Thai"]
    assert week["freeform"] == "Use up the corn"
    assert week["moods"] == ["Comfort food"]
    assert _live_rows() == 1


def test_the_very_first_two_saves_of_a_week_both_stand(monkeypatch):
    # No revision yet: the stale read is "nobody has started", which used to
    # let the second save land as revision 2 over an empty base.
    errors = _race(
        monkeypatch,
        first={"night_tags": {"2026-11-04": ["rush"]}},
        second={"cuisines": ["Korean"]},
    )
    assert errors == []
    week = tools.get_week_intake(WEEK)
    assert week["night_tags"] == {"2026-11-04": ["rush"]}
    assert week["cuisines"] == ["Korean"]
    assert _live_rows() == 1
