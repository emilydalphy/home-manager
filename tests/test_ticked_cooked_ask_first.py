"""
Moving a cook onto a night already ticked cooked ASKS FIRST.

Loop Board, 2026-09-24 ("Stepping a dish down, or taking a night off,
silently deletes a fed night that was already ticked cooked"). Both doors
that move a cook onto the first night it was feeding — the Review
stepper's "−" (weekly_plan.drop_dish_from_day) and "Not tonight — we're
going out" (tonight.tonight_night_off) — go through
weekly_plan.move_cook_onto_fed_night, which deletes the leftovers row on
the night the cook lands on. The cooked_status refusal guarded the row
being DROPPED, never the row LANDED ON, so a ticked reheat vanished
without a word.

Emily's decision, 2026-09-24, option B — ask first: without the
household's yes (`confirm_cooked`) nothing is written and the answer is
`needs_confirmation` with the question to put; with it, the move goes
ahead exactly as before, and Undo still puts the ticked row back.

These drive the real functions on a real database. Every test is a CATCH
(red on main, where the unconfirmed call deleted the ticked row, or where
the name/flag/route field did not exist) unless its docstring says GUARD.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import allergen_gate
from app.tools import tonight as _tonight
from app.tools import weekly_plan as _wp

REPO = Path(__file__).resolve().parents[1]
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


# ------------------------------------------------------------- the "−"

TODAY = household_today()
WEEK_START = (TODAY - datetime.timedelta(days=1)).isoformat()


def D(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _weekday(day: str) -> str:
    return datetime.date.fromisoformat(day).strftime("%A")


def _ids(day: str, slot: str) -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? ORDER BY id",
        (tools.household_id(), day, slot)).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _row(entry_id: int) -> dict | None:
    conn = get_conn()
    r = conn.execute(
        "SELECT * FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, tools.household_id())).fetchone()
    conn.close()
    return dict(r) if r else None


def _dump():
    conn = get_conn()
    out = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()]
        for t in ("meal_plan_entries", "prep_tasks", "meal_plan_grocery_links", "grocery_items", "inventory_items")
    }
    conn.close()
    return out


def _tick(entry_id: int) -> None:
    out = tools.check_off_meal(entry_id)
    assert _row(entry_id)["cooked_status"] == "done", out


class _TickAtTheLock:
    """A connection that, the moment it is asked for BEGIN IMMEDIATE, lets
    'the other phone' tick `entry_id` on a connection of its own first —
    so the tick lands AFTER any read taken before the lock and BEFORE any
    read taken under it. A check that is not read under the lock, on the
    locked connection, misses it."""

    def __init__(self, conn, entry_id):
        self._conn, self._entry_id = conn, entry_id

    def execute(self, sql, *args):
        if sql.strip().upper() == "BEGIN IMMEDIATE" and self._entry_id is not None:
            other = get_conn()
            other.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (self._entry_id,))
            other.commit()
            other.close()
            self._entry_id = None
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _tick_at_the_lock(monkeypatch, module, entry_id):
    real = module.get_conn
    armed = {"on": True}

    def fake():
        conn = real()
        return _TickAtTheLock(conn, entry_id if armed["on"] else None)

    monkeypatch.setattr(module, "get_conn", fake)
    return armed


def _chili_chain(fed: list[tuple[str, str]]) -> tuple[int, int]:
    """Beef Chili cooked on D(0) dinner, double for `fed`, confirmed the
    way the app's validator confirms one. Returns (plan, source)."""
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
    tools.add_recipe("Beef Chili", ingredients=[{"item": "Black beans", "qty": "1 can", "category": "pantry"}],
                     instructions=["Simmer."], prep_time_minutes=10, cook_time_minutes=20)
    plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(D(0), "Beef Chili", slot="dinner", weekly_plan_id=plan)
    source = _ids(D(0), "dinner")[-1]
    reheats = []
    for day, slot in fed:
        tools.plan_meal(day, "Beef Chili", slot=slot, weekly_plan_id=plan)
        reheats.append(_ids(day, slot)[-1])
    conn = get_conn()
    for rid in reheats:
        conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                     (json.dumps({"links_to": f"{D(0)}:dinner"}), rid))
    conn.execute("UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ?",
                 (json.dumps({"make_double_for": [f"{d}:{s}" for d, s in fed]}), source))
    conn.commit()
    conn.close()
    assert source in tools.plan_leftover_chains(plan)["sources"]
    return plan, source


class TestTheStepperAsksFirst:
    def test_a_ticked_fed_night_is_asked_about_and_nothing_is_written(self):
        """CATCH — the reported bug. On main this answered `dropped` and the
        ticked lunch row was simply gone."""
        plan, source = _chili_chain([(D(1), "lunch"), (D(2), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick(reheat)
        before = _dump()

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "needs_confirmation"
        assert out["reason"] == _wp.COOKED_FED_NIGHT
        assert out["cooked_date"] == D(1) and out["cooked_slot"] == "lunch"
        assert out["message"] == f"{_weekday(D(1))}’s lunch is already marked cooked. Move the Beef Chili there anyway?"
        assert out["confirm_label"] == "Move it"
        assert _dump() == before, "nothing written until they say yes"
        assert _row(reheat)["cooked_status"] == "done"

    def test_a_yes_goes_ahead_and_undo_puts_the_tick_back(self):
        """CATCH (on main the flag does not exist). With confirm_cooked the
        move is exactly what it always was; Undo restores the ticked row."""
        plan, source = _chili_chain([(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick(reheat)

        out = tools.drop_dish_from_day(plan, source, confirm_cooked=True)

        assert out["status"] == "dropped" and out["moved_to"] == D(1)
        assert _row(reheat) is None
        back = tools.drop_dish_undo(plan, out["undo_entry_id"])
        assert back["status"] == "restored"
        assert _row(reheat)["cooked_status"] == "done"

    def test_an_unticked_fed_night_is_not_asked_about(self):
        """GUARD — green on main. The question is only for a tick; the
        ordinary self-solving "−" keeps its one tap."""
        plan, source = _chili_chain([(D(1), "lunch")])

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "dropped"

    def test_a_tick_made_as_the_lock_is_taken_is_seen_under_it(self, monkeypatch):
        """CATCH. The check is read under BEGIN IMMEDIATE, on the locked
        connection: the other phone ticks the reheat at the very moment the
        lock is asked for, and the "−" still asks rather than deleting it.
        A check moved above the BEGIN, or onto another connection, fails."""
        plan, source = _chili_chain([(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick_at_the_lock(monkeypatch, _wp, reheat)

        out = tools.drop_dish_from_day(plan, source)

        assert out["status"] == "needs_confirmation"
        assert _row(reheat)["cooked_status"] == "done"

    def test_a_yes_to_one_night_is_not_a_yes_to_another(self):
        """CATCH. The screen sends back the night it asked about. If the
        week moved and a DIFFERENT ticked night is now the one the cook
        lands on, that is asked about in turn, never deleted on the old yes.
        Chat's plain True is the model relaying a yes it asked for in words."""
        plan, source = _chili_chain([(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick(reheat)

        stale = tools.drop_dish_from_day(plan, source, confirm_cooked=f"{D(3)}:lunch")
        assert stale["status"] == "needs_confirmation"
        assert stale["confirm_night"] == f"{D(1)}:lunch"
        assert _row(reheat) is not None

        right = tools.drop_dish_from_day(plan, source, confirm_cooked=stale["confirm_night"])
        assert right["status"] == "dropped"
        assert _row(reheat) is None

    def test_a_changed_chain_still_says_changed_before_it_asks(self):
        """GUARD. DROP_DISH_CHANGED still comes first: a chain broken since
        the decision is refused as changed, whether or not a tick exists."""
        plan, source = _chili_chain([(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick(reheat)
        other = {"entry_id": 999999, "date": D(1), "slot": "lunch"}

        out = _wp._drop_by_cooking_on_the_fed_night(plan, source, D(0), "dinner", "Beef Chili", other, "x")

        assert out["status"] == "refused" and out["message"] == _wp.DROP_DISH_CHANGED

    def test_the_route_asks_then_goes_ahead_on_yes(self, signed_in):
        """CATCH — the route passes confirm_cooked through."""
        plan, source = _chili_chain([(D(1), "lunch")])
        reheat = _ids(D(1), "lunch")[0]
        _tick(reheat)

        first = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day", json={"entry_id": source})
        assert first.status_code == 200 and first.json()["status"] == "needs_confirmation"
        assert _row(reheat) is not None

        yes = signed_in.post(f"/api/week/{WEEK_START}/drop-dish-day",
                             json={"entry_id": source, "confirm_cooked": True})
        assert yes.status_code == 200 and yes.json()["status"] == "dropped"
        assert _row(reheat) is None

    def test_the_allergen_sweep_never_counts_a_question_as_an_opened_slot(self, monkeypatch):
        """CATCH. The sweep is not a person and never answers yes; a
        needs_confirmation leaves the week as it was, so it is not a slot
        opened. On main the sweep counted anything but `refused` as opened."""
        tools.add_member("Emily")
        tools.set_member_dietary_restrictions("Emily", ["pineapple allergy"])
        tools.add_recipe("Fruit Cup", ingredients=[{"item": "pineapple chunks", "qty": "1 bag"}])
        plan = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
        tools.plan_meal(D(1), "Fruit Cup", slot="snack", weekly_plan_id=plan)
        monkeypatch.setattr(
            allergen_gate._weekly_plan, "drop_dish_from_day",
            lambda *a, **kw: {"status": "needs_confirmation", "message": "asked"},
        )
        pineapple = {
            "meal_name": "More Pineapple", "is_new_recipe": True, "reason": "x",
            "ingredients": [{"item": "Pineapple", "qty": "1", "category": "produce"}],
            "instructions": ["Cut."], "food_groups": ["fruit"], "cuisine": "Any",
            "main_protein": "none", "prep_time_minutes": 5, "cook_time_minutes": 0, "default_servings": 2,
        }

        out = allergen_gate.sweep_plan(plan, picker=lambda ctx: pineapple)

        assert out["slots_opened"] == 0


# ---------------------------------------------------------- the night off

def _monday() -> datetime.date:
    return TODAY - datetime.timedelta(days=TODAY.weekday())


WEEK = _monday().isoformat()
DAYS = tools._week_dates(WEEK)
MON, TUE, WED, THU, FRI, SAT, SUN = DAYS
TONIGHT = WED
AFTERNOON = datetime.datetime.fromisoformat(f"{TONIGHT}T15:50:00")
CHICKEN = "Seared Garlic Chicken Thighs"


def _full_week_chicken_feeds_friday() -> int:
    for name in ("Emily", "Julia", "Rae"):
        tools.add_member(name)
    plan = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    for day in DAYS:
        if day == FRI:
            tools.plan_meal(day, CHICKEN, slot="dinner", weekly_plan_id=plan,
                            derived_from={"links_to": f"{TONIGHT}:dinner"})
            continue
        dish = CHICKEN if day == TONIGHT else f"Filler {day}"
        tools.add_recipe(dish, ingredients=[{"item": f"Main for {dish}", "qty": "1 lb", "category": "meat"}],
                         prep_time_minutes=10, cook_time_minutes=25, default_servings=3)
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan)
    tools.repair_leftover_chains(plan)
    tools.approve_weekly_plan(plan)
    return plan


class TestTheNightOffAsksFirst:
    def test_a_ticked_fed_night_is_asked_about_and_nothing_is_written(self):
        """CATCH — the same deletion through the other door. On main this
        answered `night_off` and Friday's ticked row was gone."""
        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick(friday)
        before = _dump()

        out = _tonight.tonight_night_off(now=AFTERNOON)

        assert out["status"] == "needs_confirmation"
        assert out["kind"] == "cook_on_fed"
        assert out["message"] == f"Friday’s already marked cooked. Move the {CHICKEN} there anyway?"
        assert out["said"] == out["message"], "chat says the question, not a toast"
        assert out["can_undo"] is False
        assert _dump() == before
        assert _row(friday)["cooked_status"] == "done"

    def test_a_yes_goes_ahead_and_undo_puts_the_tick_back(self):
        """CATCH (flag absent on main)."""
        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick(friday)

        out = _tonight.tonight_night_off(now=AFTERNOON, confirm_cooked=True)

        assert out["status"] == "night_off" and out["kind"] == "cook_on_fed"
        assert _row(friday) is None
        back = _tonight.tonight_night_off_undo(day=TONIGHT)
        assert back["status"] == "restored"
        assert _row(friday)["cooked_status"] == "done"

    def test_a_tick_made_as_the_lock_is_taken_is_seen_under_it(self, monkeypatch):
        """CATCH. The same race through this door: Friday is ticked at the
        moment the night off asks for its lock, and it is still asked about."""
        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick_at_the_lock(monkeypatch, _tonight, friday)

        out = _tonight.tonight_night_off(now=AFTERNOON)

        assert out["status"] == "needs_confirmation"
        assert _row(friday)["cooked_status"] == "done"

    def test_the_sheets_yes_names_friday_and_goes_through(self):
        """CATCH. What the sheet sends back — the night it showed — is a yes."""
        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick(friday)
        asked = _tonight.tonight_night_off(now=AFTERNOON)
        assert asked["confirm_night"] == f"{FRI}:dinner"

        out = _tonight.tonight_night_off(now=AFTERNOON, confirm_cooked=asked["confirm_night"])

        assert out["status"] == "night_off"
        assert _row(friday) is None

    def test_an_unticked_fed_night_is_not_asked_about(self):
        """GUARD — green on main."""
        _full_week_chicken_feeds_friday()

        out = _tonight.tonight_night_off(now=AFTERNOON)

        assert out["status"] == "night_off" and out["kind"] == "cook_on_fed"

    def test_the_route_asks_then_goes_ahead_on_yes(self, signed_in):
        """CATCH."""
        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick(friday)

        first = signed_in.post("/api/today/tonight/night-off", json={"date": TONIGHT})
        assert first.status_code == 200 and first.json()["status"] == "needs_confirmation"
        assert _row(friday) is not None

        yes = signed_in.post("/api/today/tonight/night-off", json={"date": TONIGHT, "confirm_cooked": True})
        assert yes.status_code == 200 and yes.json()["status"] == "night_off"
        assert _row(friday) is None

    def test_the_chat_tool_takes_the_flag_and_is_told_to_ask(self):
        """CATCH. The tool schema offers confirm_cooked, the description
        says to ask on needs_confirmation, and a yes passed as the model
        would pass it goes through the real function."""
        tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "take_the_night_off")
        assert "confirm_cooked" in tool["input_schema"]["properties"]
        assert "needs_confirmation" in tool["description"]
        assert "confirm_cooked" not in tool["input_schema"].get("required", [])

        _full_week_chicken_feeds_friday()
        friday = _ids(FRI, "dinner")[0]
        _tick(friday)
        fn = agent.TOOL_FUNCTIONS["take_the_night_off"]
        assert fn(day=TONIGHT)["status"] == "needs_confirmation"
        assert _row(friday) is not None
        assert fn(day=TONIGHT, confirm_cooked=True)["status"] == "night_off"

    def test_the_sheet_asks_on_the_row_and_sends_the_yes(self):
        """CATCH. The night-off sheet handles needs_confirmation by turning
        its own row into the question, and only that second tap sends
        confirm_cooked — the night it asked about.

        Source markers, because there is no JS harness in this repo; the
        server half of the contract is pinned by the behaviour tests above.
        The branch is read in order: the question is handled BEFORE the
        sheet closes, and the first tap sends no yes."""
        fn = SHELL_JS[SHELL_JS.index("async function runTonightNightOff(panel, confirmCooked)"):]
        fn = fn[:fn.index("\n  }\n")]
        assert "confirm_cooked: confirmCooked || false" in fn
        asks = fn.index("out.status === 'needs_confirmation'")
        assert fn.index("showNightOffConfirm(panel, out)") > asks
        assert asks < fn.index("closeTonightSheet()"), "asked before the sheet closes"
        assert "nightOff.addEventListener('click', function () { runTonightNightOff(panel); });" in SHELL_JS
        assert "runTonightNightOff(panel, out.confirm_night || false)" in SHELL_JS
