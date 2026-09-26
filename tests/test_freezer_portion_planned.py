"""
Freezer portions from a night off get planned into a later week (with a
defrost reminder).

Loop Board card, Emily 2026-09-26: "As someone who put a cooked portion in
the freezer, I want Pomona to plan it into a busy night soon and remind me
to thaw it, so that it gets eaten instead of forgotten."

The gap, measured on a throwaway database against `main` (6f6a5b3) before
anything was changed — a night off on a cooked dinner, then the next week
drafted with the model stubbed:

  freezer rows      [(1, 'Kofte (cooked)', '3 servings', 'night_off')]
  next week dinners Lamb stew, Miso cod, Bibimbap, Ratatouille,
                    Jerk chicken, Laksa, Gumbo      <- no portion anywhere
  next week's prep  []                              <- no fridge move

and on the branch, same seed, the Wednesday of that week tagged rush:

  next week dinners ... Leftovers from the freezer — Kofte on the Wednesday
  next week's prep  defrost 2026-09-29 "Move the Kofte to the fridge —
                    for Wednesday's dinner."

Every test says in its own docstring whether it is a CATCH (red against
main's app/) or a GUARD (green there — a promise this change could have
broken and didn't). The generation tests stub the model at
agent.generate_weekly_plan_llm, as tests/test_bring_over.py does.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import defrost as _defrost
from app.tools import freezer_portions as _fp
from app.tools import leftovers as _leftovers
from app.tools import meal_variety as _meal_variety
from app.tools import moves as _moves
from app.tools import tonight as _tonight
from app.tools import weekly_plan as _weekly_plan


def _monday(offset_weeks: int = 0) -> str:
    """The HOUSEHOLD's Monday, never the process's — the week the app's own
    screens are in (see conftest.household_today)."""
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


THIS = ["Bean chili", "Kofte", "Salmon traybake", "Burgers", "Pad thai", "Pierogi", "Dal"]
NEXT = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Laksa", "Gumbo"]


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def household():
    for name in ("Emily", "Vineeth", "Reid"):
        tools.add_member(name)


@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def _stub(days):
        def fake(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
        return seen
    return _stub


def _slot(date: str, slot: str, name: str, **extra) -> dict:
    d = {
        "date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
        "ingredients": [{"item": f"{name} base", "qty": "1 lb", "category": "pantry"}],
        "instructions": [f"Cook the {name.lower()} over medium heat.", "Serve."],
        "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
        "prep_time_minutes": 10, "cook_time_minutes": 15,
    }
    d.update(extra)
    return d


def _week(week: str, dinners: list[str], tag: str = "Grain") -> list[dict]:
    out = []
    for i, date in enumerate(tools._week_dates(week)):
        out.append(_slot(date, "breakfast", f"{tag} oats"))
        out.append(_slot(date, "snack", f"{tag} apple"))
        out.append(_slot(date, "lunch", f"{tag} salad {i}"))
        out.append(_slot(date, "dinner", dinners[i]))
    return out


def _entries(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.*, COALESCE(r.name, mpe.freeform_meal) AS meal FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id WHERE mpe.weekly_plan_id = ? ORDER BY date, id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dinner(plan_id: int, date: str) -> dict:
    return next(e for e in _entries(plan_id) if e["date"] == date and e["slot"] == "dinner")


def _dinners(plan_id: int) -> dict[str, str]:
    return {e["date"]: e["meal"] for e in _entries(plan_id) if e["slot"] == "dinner"}


def _prep(plan_id: int) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM prep_tasks WHERE weekly_plan_id = ? ORDER BY task_date, id",
                        (plan_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _freezer() -> list[dict]:
    return [i for i in tools.get_inventory() if i.get("location") == "freezer"]


def _a_frozen_portion(dish: str = "Kofte", servings: int = 3, frozen_on: str | None = None,
                      day_index: int = 1) -> int:
    """
    A portion in the freezer, through the door a household actually uses: a
    week, a dinner ticked cooked, then "Not tonight — we're going out",
    which is tonight.tonight_night_off's `freeze_cooked` shape. The row is
    never hand-inserted — the whole point is that this is the row a night
    off writes.
    """
    week = _monday(-1)
    dates = tools._week_dates(week)
    plan = _weekly_plan.get_plan_id_for_week(week) or tools.create_weekly_plan(week)["weekly_plan_id"]
    if not any(r["name"] == dish for r in tools.list_recipes()):
        tools.add_recipe(dish, ingredients=[{"item": f"{dish} base", "qty": "1 lb", "category": "pantry"}],
                         prep_time_minutes=10, cook_time_minutes=30, default_servings=servings + 1)
    tools.plan_meal(dates[day_index], dish, slot="dinner", weekly_plan_id=plan)
    conn = get_conn()
    entry = _dinner(plan, dates[day_index])
    conn.execute("UPDATE meal_plan_entries SET cooked_status = 'done' WHERE id = ?", (entry["id"],))
    conn.commit()
    conn.close()
    out = _tonight.tonight_night_off(day=dates[day_index])
    assert out["status"] == "night_off" and out["kind"] == "freeze_cooked", out
    row = _freezer()[-1]
    if frozen_on is not None:
        conn = get_conn()
        conn.execute("UPDATE inventory_items SET created_at = ? WHERE id = ?",
                     (f"{frozen_on} 12:00:00", row["id"]))
        conn.commit()
        conn.close()
    return row["id"]


def _draft_next_week(stub_model, *, rush: str | None = None, dinners=None, week=None,
                     intake_kwargs=None) -> dict:
    # NEXT week, so every night of it is ahead of today whatever weekday the
    # suite runs on — the defrost screens only ever look forward, and a
    # Saturday run against this week's Wednesday reads an empty schedule.
    week = week or _monday(1)
    kwargs = {"moods": ["Comfort food"]}
    if rush:
        kwargs["night_tags"] = {rush: ["rush"]}
    kwargs.update(intake_kwargs or {})
    tools.save_week_intake(week, **kwargs)
    seen = stub_model(_week(week, dinners or NEXT, tag="Bean"))
    plan = agent.generate_weekly_plan(week)
    return {"plan": plan, "plan_id": plan["weekly_plan_id"], "ctx": seen.get("ctx"), "week": week}


# ==========================================================================
# 1. The portions waiting
# ==========================================================================

class TestWhatIsWaiting:
    def test_a_night_offs_portion_is_waiting_to_be_planned(self, household):
        """CATCH — nothing on main asks this question at all."""
        item_id = _a_frozen_portion("Kofte", servings=3)
        waiting = _fp.portions_to_plan()
        assert [(p["inventory_item_id"], p["dish"], p["quantity"]) for p in waiting] == [
            (item_id, "Kofte", "3 servings")
        ]

    def test_a_freezer_row_the_app_did_not_freeze_is_not_a_portion(self, household):
        """CATCH. The `source` a night off stamps is what says a row is one
        of ours — a bag of peas somebody tracked is not a cooked portion,
        however it is named."""
        tools.update_inventory("Peas (cooked)", "add", quantity="2 bags", location="freezer")
        assert _fp.portions_to_plan() == []

    def test_a_portion_a_live_plan_already_stands_a_night_on_is_not_offered_again(
        self, household, stub_model
    ):
        """CATCH — this is "planned once". The entry claiming the portion is
        what keeps every later week off it."""
        _a_frozen_portion("Kofte")
        out = _draft_next_week(stub_model, rush=tools._week_dates(_monday(1))[2])
        assert any("from the freezer" in m for m in _dinners(out["plan_id"]).values())
        # A week after that, with the portion still in the freezer.
        later = _monday(2)
        assert _fp.portions_to_plan(tools._week_dates(later)) == []

    def test_a_claim_inside_the_period_being_drafted_is_not_a_claim(self, household, stub_model):
        """CATCH. retire_overlapping_plans runs at the END of generation, so
        the outgoing draft is still 'draft' when choose_nights asks — read as
        a claim, re-drafting the same week would lose the portion for good."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        _draft_next_week(stub_model, rush=dates[2], week=week)
        assert [p["dish"] for p in _fp.portions_to_plan(dates)] == ["Kofte"]

    def test_a_retired_plans_claim_does_not_hold_the_portion(self, household, stub_model):
        """CATCH. A retired week was replaced, so nothing on it is going to
        be eaten and the portion is free again."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        out = _draft_next_week(stub_model, rush=tools._week_dates(week)[2], week=week)
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (out["plan_id"],))
        conn.commit()
        conn.close()
        assert [p["dish"] for p in _fp.portions_to_plan(tools._week_dates(_monday(2)))] == ["Kofte"]

    def test_a_night_that_went_by_without_a_tick_is_not_a_claim(self, household, stub_model):
        """CATCH, and the judgment call worth reading: eating the portion
        deletes the row, so a PAST claim can only be a night nobody ticked —
        and by the app's own records that portion is still frozen and still
        unplanned. Never mentioning it again is the bug this module exists
        to fix, so it goes back in the pool."""
        _a_frozen_portion("Kofte")
        week = _monday(-1)
        dates = tools._week_dates(week)
        # A plan whose nights have all gone by, with the portion on one of
        # them, never ticked. Written through the pass's own door.
        plan = _weekly_plan.get_plan_id_for_week(week)
        _fp.apply_to_plan(plan, [{
            "inventory_item_id": _freezer()[0]["id"], "dish": "Kofte",
            "quantity": "3 servings", "on": dates[4],
        }])
        assert _dinner(plan, dates[4])["meal"] == "Leftovers from the freezer — Kofte"
        assert [p["dish"] for p in _fp.portions_to_plan(tools._week_dates(_monday(1)))] == ["Kofte"]

    def test_the_name_a_portion_is_stored_under_round_trips(self):
        """GUARD on the one place the name is defined (tonight.
        FROZEN_COOKED_SUFFIX). Two copies of that suffix — one writing the
        row, one reading the dish back — is the same fact stated twice, and
        the two drift the first time anybody rewords one.

        NOTHING PINS THE SINGLE DEFINITION, and that is said here rather
        than claimed otherwise: the mutation that hard-codes " (cooked)" in
        the reader reddens zero tests (measured), because today the two
        literals agree and the behaviour is identical. What this test does
        pin is the behaviour on both sides of the round trip, including the
        case a household's own capitalisation produces. The one-definition
        rule is a comment at the constant, not a test."""
        assert _tonight.frozen_portion_dish(f"Kofte{_tonight.FROZEN_COOKED_SUFFIX}") == "Kofte"
        assert _tonight.frozen_portion_dish("Kofte (COOKED)") == "Kofte"
        assert _tonight.frozen_portion_dish("Chicken thighs") == ""


# ==========================================================================
# 2. Which night
# ==========================================================================

def _choose(dates, portions=None, **kwargs):
    portions = portions or [{"inventory_item_id": 1, "dish": "Kofte", "quantity": "3 servings"}]
    return _fp.choose_nights(portions, dates, kwargs.pop("intake", None), **kwargs)


class TestWhichNight:
    def test_the_short_on_time_night_comes_first(self):
        """CATCH — Emily's card: "plan it into a busy night soon". The cap is
        the app's own idea of busy (time_caps), and an EARLIER night with no
        cap must not win."""
        dates = tools._week_dates(_monday(0))
        caps = {dates[4]: 30}
        placed, missed = _choose(dates, cap_for=lambda d, slot: caps.get(d))
        assert [p["on"] for p in placed] == [dates[4]]
        assert missed == []

    def test_the_tightest_of_two_busy_nights_wins(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        caps = {dates[1]: 30, dates[5]: 20}
        placed, _ = _choose(dates, cap_for=lambda d, slot: caps.get(d))
        assert [p["on"] for p in placed] == [dates[5]]

    def test_with_no_busy_night_it_is_the_earliest(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        placed, _ = _choose(dates, cap_for=lambda d, slot: None)
        assert [p["on"] for p in placed] == [dates[0]]

    def test_a_cap_never_rules_a_night_out(self):
        """CATCH, and the one place this pass differs from bring_over's: a
        portion is reheated, so a 15-minute night fits it. Every night here
        has a cap tighter than any cook, and one is still chosen."""
        dates = tools._week_dates(_monday(0))
        placed, missed = _choose(dates, cap_for=lambda d, slot: 5)
        assert len(placed) == 1 and missed == []

    def test_a_night_nobody_is_home_is_never_offered(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        placed, _ = _choose(
            dates, cap_for=lambda d, slot: 30 if d == dates[3] else None,
            slot_needs={"away_slots": [{"date": dates[3], "slot": "dinner"}]},
        )
        assert [p["on"] for p in placed] == [dates[0]]

    def test_an_out_night_a_left_night_and_a_skipped_day_are_never_offered(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        placed, _ = _choose(
            dates,
            intake={"night_tags": {dates[0]: ["out"], dates[1]: ["left"]}, "skipped_days": [dates[2]]},
        )
        assert [p["on"] for p in placed] == [dates[3]]

    def test_a_holiday_they_are_out_for_is_never_offered(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        placed, _ = _choose(dates, holidays=[{"date": dates[0], "answer": {"answer": "out"}}])
        assert [p["on"] for p in placed] == [dates[1]]

    def test_a_night_a_brought_over_meal_took_is_never_offered(self):
        """CATCH — bring_over.choose_nights runs first and a night cannot
        hold both."""
        dates = tools._week_dates(_monday(0))
        placed, _ = _choose(dates, taken={(dates[0], "dinner")})
        assert [p["on"] for p in placed] == [dates[1]]

    def test_only_one_portion_a_week(self):
        """CATCH on MAX_PORTIONS_PER_WEEK — a second reheat night nobody
        asked for is the app taking the week over."""
        dates = tools._week_dates(_monday(0))
        portions = [
            {"inventory_item_id": 1, "dish": "Kofte", "quantity": "3 servings"},
            {"inventory_item_id": 2, "dish": "Chili", "quantity": "2 servings"},
        ]
        placed, missed = _choose(dates, portions=portions)
        assert [p["dish"] for p in placed] == ["Kofte"]
        assert [m["dish"] for m in missed] == ["Chili"]

    def test_a_household_that_asked_for_no_dinners_gets_no_portion(self):
        """CATCH."""
        dates = tools._week_dates(_monday(0))
        placed, missed = _choose(dates, zero_slots={"dinner"})
        assert placed == [] and missed == []

    def test_a_week_with_nowhere_to_put_it_leaves_it_frozen(self):
        """CATCH — a portion with no night is left in the freezer, never
        forced onto a night the household's own answer rules out."""
        dates = tools._week_dates(_monday(0))
        placed, missed = _choose(dates, intake={"skipped_days": list(dates)})
        assert placed == [] and [m["dish"] for m in missed] == ["Kofte"]


# ==========================================================================
# 3. The week it writes
# ==========================================================================

class TestTheDraft:
    def test_the_portion_lands_on_the_busy_night_as_a_reheat(self, household, stub_model):
        """CATCH — the headline of the card, end to end through generation."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        assert _dinners(out["plan_id"])[dates[2]] == "Leftovers from the freezer — Kofte"
        entry = _dinner(out["plan_id"], dates[2])
        assert entry["slot_state"] == "planned"
        assert entry["recipe_id"] is None, "a portion is a freeform row, so nothing is bought for it"
        portion = _fp.portion_on(entry["derived_from_json"])
        assert portion and portion["dish"] == "Kofte"
        assert _leftovers.frozen_portion_on(entry["derived_from_json"]) == "Kofte"

    def test_nothing_is_bought_for_it(self, household, stub_model):
        """CATCH. The night the portion took had a dinner on it in the
        model's own answer; approving the week must buy that dinner's
        ingredients for nobody."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        tools.approve_weekly_plan(out["plan_id"])
        names = {(i["item"] or "").lower() for i in tools.list_grocery_list(status="all")}
        assert "bibimbap base" not in names, "the replaced dinner's ingredients"
        assert not any("kofte" in n for n in names), "and nothing for the portion either"
        entry = _dinner(out["plan_id"], dates[2])
        conn = get_conn()
        links = conn.execute("SELECT COUNT(*) AS n FROM meal_plan_grocery_links WHERE meal_plan_entry_id = ?",
                             (entry["id"],)).fetchone()["n"]
        conn.close()
        assert links == 0

    def test_it_is_a_meal_but_not_a_cook(self, household, stub_model):
        """CATCH. weekly_plan._is_cook reads the slot's `source`, and
        build_slot's leftovers regex is what puts it there — so the week's
        own "4 cooks" cannot count a portion as one."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        menu = tools.get_week_menu(out["plan_id"])
        day = next(d for d in menu["days"] if d["date"] == dates[2])
        assert day["dinner"]["source"] == "leftovers"
        assert day["dinner"]["meta"] == "reheat"
        assert _weekly_plan._is_cook(day["dinner"]) is False

    def test_the_model_is_told_the_slot_is_taken(self, household, stub_model):
        """CATCH. Telling is not preventing — the write below does that —
        but a model planning around a slot it knows about writes a better
        week than one whose dinner is quietly replaced."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        told = (out["ctx"].get("intake") or {}).get("frozen_portions")
        assert told == [{"date": dates[2], "slot": "dinner",
                         "meal": "Leftovers from the freezer — Kofte"}]

    def test_whatever_the_model_sent_for_that_night_is_replaced(self, household, stub_model):
        """CATCH — the pass writes after generation, so a model that ignored
        the bullet above cannot keep the night."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        rows = [e for e in _entries(out["plan_id"]) if e["date"] == dates[2] and e["slot"] == "dinner"]
        assert len(rows) == 1, "one row on the slot, not the model's beside ours"
        assert rows[0]["meal"] == "Leftovers from the freezer — Kofte"

    def test_the_no_repeat_and_count_passes_leave_it_standing(self, household, stub_model):
        """CATCH — meal_variety.theirs. Food already cooked and paid for is
        exactly what the no-repeat rule would otherwise swap away."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        entry = _dinner(out["plan_id"], dates[2])
        derived = json.loads(entry["derived_from_json"] or "{}")
        assert _meal_variety.theirs(derived) is True
        assert _meal_variety.theirs_by_hand({"nights": [entry]}) is True

    def test_it_reads_as_a_reheat_on_today_and_never_as_cook_this(self, household, stub_model):
        """CATCH. Measured on main for the in-week freezer night this shape
        is borrowed from: 'Leftovers from the freezer — Monday's Bean chili ·
        Cook this', which is the one thing a reheat must never say."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        card = next(m for m in _cooker.get_cooker_view(out["plan_id"])["meals"]
                    if m["date"] == dates[2] and m["slot"] == "dinner")
        assert card["is_leftovers"] is True
        assert card["has_full_recipe"] is False
        assert card["ingredients"] == [] and card["instructions"] == []
        moves = [m for m in _moves.moves_for_day(dates[2]) if m.get("slot") == "dinner"]
        assert [m["kind"] for m in moves] == ["reheat"]
        assert moves[0]["action"]["label"] != "Cook this"
        assert "from the freezer" in moves[0]["detail"]

    def test_a_component_household_is_left_alone(self, household, stub_model, monkeypatch):
        """GUARD. A component plan's rows are parts, not nights, so there is
        nowhere to put a portion — same carve-out bring_over takes."""
        _a_frozen_portion("Kofte")
        tools.set_planning_mode("component_based")
        week = _monday(0)
        monkeypatch.setattr(agent, "generate_component_plan_llm", lambda ctx: [])
        tools.save_week_intake(week, moods=["Comfort food"])
        with pytest.raises(Exception):
            agent.generate_weekly_plan(week)
        assert [p["dish"] for p in _fp.portions_to_plan()] == ["Kofte"]


class TestAHouseholdWithNoPortions:
    """The households this change must not reach — which is nearly all of
    them: one that has never taken a night off has nothing in the freezer
    this pass can see."""

    def test_a_week_with_no_portion_is_the_week_it_always_was(self, household, stub_model):
        """GUARD. Every dinner the model sent, no extra defrost row, no
        attention item, and the Cook view unchanged."""
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        assert list(_dinners(out["plan_id"]).values()) == NEXT
        assert [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"] == []
        assert tools.get_attention_items() == []
        card = next(m for m in _cooker.get_cooker_view(out["plan_id"])["meals"]
                    if m["date"] == dates[2] and m["slot"] == "dinner")
        assert card["is_leftovers"] is False and card["has_full_recipe"] is True

    def test_the_defrost_sync_asks_about_portions_once_and_stops(self, household, stub_model):
        """GUARD on the LIKE that narrows both new reads. A plan with no
        portion must not cost a query per card or per meal — pinned by
        counting, since the cost of getting this wrong is invisible."""
        week = _monday(1)
        out = _draft_next_week(stub_model, rush=tools._week_dates(week)[2], week=week)
        plan = tools.get_weekly_plan(out["plan_id"])
        seen = []
        real = _fp.get_conn

        def counting():
            seen.append(1)
            return real()

        _fp.get_conn = counting
        try:
            assert _fp.defrost_candidates(plan, "6_8") == []
        finally:
            _fp.get_conn = real
        assert len(seen) == 1, "one connection, whatever the week holds"


# ==========================================================================
# 4. The fridge move
# ==========================================================================

class TestTheFridgeMove:
    def test_the_move_is_booked_the_night_before(self, household, stub_model):
        """CATCH — criterion 2. Nothing on main books one: defrost matches a
        recipe's INGREDIENTS against the freezer by name, and a cooked
        portion is nobody's ingredient (measured: prep_tasks == [])."""
        item_id = _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        rows = [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"]
        assert len(rows) == 1, rows
        assert rows[0]["task_date"] == dates[1], "the night before"
        assert rows[0]["inventory_item_id"] == item_id
        assert rows[0]["description"] == "Move the Kofte to the fridge — for Wednesday's dinner."
        assert rows[0]["status"] == "pending"
        assert rows[0]["quantity"] == "3 servings"

    def test_the_lead_is_a_cooked_portions_not_a_raw_roasts(self, household, stub_model):
        """CATCH. defrost.lead_hours_for_item keys on words for RAW cuts of
        family-pack size, so "Roast Chicken (cooked)" reads as a 72-hour
        whole roast there. A few servings in a container is one night."""
        assert _defrost.lead_hours_for_item("Roast Chicken (cooked)")[0] == 72.0
        assert _fp.PORTION_LEAD_HOURS == 24.0
        _a_frozen_portion("Roast Chicken")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[3], week=week)
        rows = [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"]
        assert [r["task_date"] for r in rows] == [dates[2]]

    def test_the_move_is_read_back_by_the_defrost_screens(self, household, stub_model):
        """CATCH. A booked move nothing shows is not a reminder."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        _draft_next_week(stub_model, rush=dates[2], week=week)
        schedule = tools.get_defrost_schedule(days=14)
        assert [(s["task_date"], s["description"]) for s in schedule] == [
            (dates[1], "Move the Kofte to the fridge — for Wednesday's dinner.")
        ]

    def test_swapping_the_night_away_sweeps_the_move(self, household, stub_model):
        """CATCH — the reason the move goes through sync_defrost_tasks rather
        than being written here: a reminder to move something for a night
        nobody is eating it on is worse than none."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        assert len([p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"]) == 1
        tools.add_recipe("Ramen", ingredients=[{"item": "Noodles", "qty": "1 pack", "category": "pantry"}],
                         prep_time_minutes=5, cook_time_minutes=10, default_servings=4)
        tools.swap_meal_in_plan(out["plan_id"], dates[2], "Ramen", slot="dinner")
        tools.sync_defrost_tasks(out["plan_id"])
        assert [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"] == []

    def test_a_resync_keeps_a_move_already_ticked(self, household, stub_model):
        """GUARD, inherited from sync_defrost_tasks and worth pinning here:
        regenerating a schedule must not un-defrost something already done."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        row = [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"][0]
        tools.check_off_prep_step(row["id"], "done")
        tools.sync_defrost_tasks(out["plan_id"])
        after = [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"]
        assert [(p["id"], p["status"]) for p in after] == [(row["id"], "done")]

    def test_a_portion_no_longer_in_the_freezer_books_no_move(self, household, stub_model):
        """CATCH."""
        item_id = _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        conn = get_conn()
        conn.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
        conn.commit()
        conn.close()
        tools.sync_defrost_tasks(out["plan_id"])
        assert [p for p in _prep(out["plan_id"]) if p["task_type"] == "defrost"] == []


# ==========================================================================
# 5. Eaten
# ==========================================================================

class TestEaten:
    def test_ticking_the_night_takes_the_portion_out_of_the_freezer(self, household, stub_model):
        """CATCH — how "used up" is recorded. Nothing on main could: the
        night has no recipe, so deplete_inventory_for_meal returned at its
        recipe_id guard."""
        item_id = _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        entry = _dinner(out["plan_id"], dates[2])
        assert item_id in {i["id"] for i in _freezer()}
        result = tools.check_off_meal(entry["id"], "done")
        assert item_id not in {i["id"] for i in _freezer()}
        assert [d["ingredient"] for d in result["inventory_depleted"]] == ["Kofte"]

    def test_a_re_tick_cannot_take_a_second_portion(self, household, stub_model):
        """GUARD, and the reason this rides inside deplete_inventory_for_meal
        rather than beside it: the tick's own claim already stops a second
        depletion. Pinned by a second portion of the same dish, which a
        by-name reversal would have eaten."""
        _a_frozen_portion("Kofte", day_index=1)
        second = _a_frozen_portion("Kofte", day_index=3)
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        entry = _dinner(out["plan_id"], dates[2])
        tools.check_off_meal(entry["id"], "done")
        tools.check_off_meal(entry["id"], "pending")
        tools.check_off_meal(entry["id"], "done")
        assert [i["id"] for i in _freezer()] == [second]

    def test_an_eaten_portion_is_never_offered_again(self, household, stub_model):
        """CATCH."""
        _a_frozen_portion("Kofte")
        week = _monday(1)
        dates = tools._week_dates(week)
        out = _draft_next_week(stub_model, rush=dates[2], week=week)
        tools.check_off_meal(_dinner(out["plan_id"], dates[2])["id"], "done")
        assert _fp.portions_to_plan() == []
        assert _fp.portions_to_plan(tools._week_dates(_monday(2))) == []

    def test_an_ordinary_meal_still_depletes_its_ingredients(self, household):
        """GUARD — the recipe path through deplete_inventory_for_meal is
        untouched. Pinned by the mutation that returns the portion result
        unconditionally."""
        tools.update_inventory("Lentils", "add", quantity="2 cups", location="pantry")
        week = _monday(1)
        dates = tools._week_dates(week)
        plan = tools.create_weekly_plan(week)["weekly_plan_id"]
        tools.add_recipe("Dal", ingredients=[{"item": "Lentils", "qty": "1 cup", "category": "pantry"}],
                         prep_time_minutes=5, cook_time_minutes=20, default_servings=3)
        tools.plan_meal(dates[0], "Dal", slot="dinner", weekly_plan_id=plan)
        result = tools.check_off_meal(_dinner(plan, dates[0])["id"], "done")
        assert [d["ingredient"] for d in result["inventory_depleted"]] == ["Lentils"]
        assert [i["quantity"] for i in tools.get_inventory() if i["item"] == "Lentils"] == ["1 cup"]


# ==========================================================================
# 6. Four weeks in the freezer
# ==========================================================================

class TestUseSoon:
    def _said(self) -> list[str]:
        return [i["summary"] for i in tools.get_attention_items()
                if i["kind"] == _tonight.USE_SOON_KIND]

    def test_a_portion_nobody_planned_is_said_after_four_weeks(self, household):
        """CATCH — criterion 3."""
        frozen_on = (household_today() - datetime.timedelta(days=_fp.USE_SOON_AFTER_DAYS)).isoformat()
        _a_frozen_portion("Kofte", frozen_on=frozen_on)
        said = self._said()
        assert len(said) == 1, said
        assert "Kofte" in said[0] and "4 weeks" in said[0]

    def test_a_fresher_portion_is_not_mentioned(self, household):
        """CATCH on the threshold. Pinned in the other direction by the
        mutation that sets USE_SOON_AFTER_DAYS to zero."""
        frozen_on = (household_today() - datetime.timedelta(days=_fp.USE_SOON_AFTER_DAYS - 1)).isoformat()
        _a_frozen_portion("Kofte", frozen_on=frozen_on)
        assert self._said() == []

    def test_it_is_said_once_ever_even_after_it_is_answered(self, household):
        """CATCH. add_attention_item's own dedupe would re-queue the moment
        the household answered — and being told a third time about a portion
        you have decided to keep frozen is how a nudge stops being read."""
        frozen_on = (household_today() - datetime.timedelta(days=40)).isoformat()
        _a_frozen_portion("Kofte", frozen_on=frozen_on)
        items = [i for i in tools.get_attention_items() if i["kind"] == _tonight.USE_SOON_KIND]
        assert len(items) == 1
        tools.resolve_attention_item(items[0]["id"], "dismissed")
        assert self._said() == []
        conn = get_conn()
        rows = conn.execute("SELECT COUNT(*) AS n FROM attention_items WHERE kind = ?",
                            (_tonight.USE_SOON_KIND,)).fetchone()["n"]
        conn.close()
        assert rows == 1

    def test_a_portion_already_on_a_week_is_not_nudged_about(self, household, stub_model):
        """CATCH — it is not forgotten; it has a night."""
        frozen_on = (household_today() - datetime.timedelta(days=40)).isoformat()
        _a_frozen_portion("Kofte", frozen_on=frozen_on)
        week = _monday(1)
        dates = tools._week_dates(week)
        _draft_next_week(stub_model, rush=dates[2], week=week)
        conn = get_conn()
        conn.execute("DELETE FROM attention_items")
        conn.commit()
        conn.close()
        assert self._said() == []

    def test_the_nudge_is_not_the_use_by_date(self, household):
        """GUARD, and the assumption worth saying out loud: four weeks is
        how long the app watches a portion go unplanned. How long it KEEPS
        is tonight.FROZEN_COOKED_KEEPS_DAYS, and it is the row's own
        use-by."""
        assert _fp.USE_SOON_AFTER_DAYS == 28
        assert _tonight.FROZEN_COOKED_KEEPS_DAYS == 90
        cooked_on = tools._week_dates(_monday(-1))[1]
        _a_frozen_portion("Kofte")
        assert [i["expiration_date"] for i in _freezer()] == [
            (datetime.date.fromisoformat(cooked_on) + datetime.timedelta(days=90)).isoformat()
        ]
