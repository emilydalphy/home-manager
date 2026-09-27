"""
Emily's week of 2026-09-27, replayed end to end with the model stubbed.

The integration reviewer's replay (plan-walk integration, 2026-09-27) of
the week she walked on her phone: Sunday 27 September 2026 at 3:53pm in
Toronto, a Sunday-to-Friday week, two adults. Counts 3 dinners · 1
breakfast · 2 lunches · 2 snacks a day, 2 snack dishes. Last week (approved)
was Overnight oats every morning. This week's answers: mood "Something
new", the Burgers chip, Wednesday tagged Leftovers, Friday short on time,
weekday lunches prepped on Sunday and Tuesday for Mon–Thu and Friday's
lunch "leftovers from dinner". The note: "I have corn that I need to use so
suggest a dish with that. And I want to make a Japanese curry heavy on
veggies today to use up stuff in the fridge and have leftovers for it."

The model (stubbed) answers the way it did that day: the curry on Sunday
LUNCH with Sunday dinner open, Corn pancakes on Monday lunch, Wednesday
dinner open, Greek chicken claiming the Burgers chip, Beef bowls three
meals running at the end of the week, Overnight oats again every morning
and six different snacks.

Every assertion is one thing Emily's review said the draft must be. The
swap picker is stubbed: a pick asked for the Burgers chip is Smash burgers,
anything else is the next quick dish on a list.
"""
from __future__ import annotations

import datetime
import json

import pytest

from conftest import household_pin
from app import agent, tools
from app.db import get_conn
from app.tools import leftovers
from app.tools import swap_in_place as sip

SUNDAY = "2026-09-27"
PERIOD = [(datetime.date(2026, 9, 27) + datetime.timedelta(days=i)).isoformat() for i in range(6)]
LAST_MON = "2026-09-21"
LAST = [(datetime.date(2026, 9, 21) + datetime.timedelta(days=i)).isoformat() for i in range(6)]

NOTE = ("I have corn that I need to use so suggest a dish with that. And I want to make a Japanese curry "
        "heavy on veggies today to use up stuff in the fridge and have leftovers for it.")
CURRY_WORDS = "I want to make a Japanese curry heavy on veggies today"


def _e(date, slot, meal, minutes=30, **extra):
    d = {"date": date, "slot": slot, "meal_name": meal, "is_new_recipe": True,
         "ingredients": [{"item": f"{meal} base", "qty": "1", "category": "pantry"}],
         "instructions": [f"Cook the {meal.lower()}.", "Serve."],
         "reasoning": f"{meal} because it fits the week", "food_groups": ["protein", "vegetable", "carb"],
         "prep_time_minutes": minutes // 2, "cook_time_minutes": minutes - minutes // 2}
    d.update(extra)
    return d


def _open(date, slot):
    return {"date": date, "slot": slot, "meal_name": "", "is_new_recipe": False, "reasoning": "",
            "slot_state": "open", "open_reason": "I'd rather ask than guess."}


def _rows(plan_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.id, mpe.date, mpe.slot, mpe.slot_state, mpe.derived_from_json, "
        "       COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.weekly_plan_id = ? AND mpe.component_category IS NULL ORDER BY mpe.date, mpe.id",
        (plan_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture
def picker(monkeypatch):
    calls = []
    names = iter(["Shakshuka", "Quick Veggie Frittata", "Ten-Minute Fried Rice", "Speedy Bean Tostadas",
                  "Fast Pesto Gnocchi", "Rapid Egg Curry", "Snappy Tuna Melts", "Miso Soba", "Pea Risotto",
                  "Hummus Plate", "Yogurt Parfait", "Trail Mix", "Congee", "Granola"])

    def pick(context):
        calls.append(context)
        if context.get("must_be_cuisine"):
            name, cuisine, minutes = "Smash burgers", "American", 25
        else:
            name, cuisine, minutes = next(names), None, 15
        out = {"meal_name": name, "reason": f"{name} because",
               "ingredients": [{"item": f"{name} base", "qty": "1", "category": "pantry"}],
               "instructions": ["Cook it.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
               "prep_time_minutes": 5, "cook_time_minutes": minutes - 5}
        if cuisine:
            out["cuisine"] = cuisine
        return out

    monkeypatch.setattr(sip, "_pick_replacement", pick)
    return calls


def _emilys_week(monkeypatch, frozen_today):
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.set_household_meal_preferences(dinners_per_week=3, breakfasts_per_week=1, lunches_per_week=2,
                                         snacks_per_day=2, mark_complete=False)
    tools.edit_preference("snack_dishes_per_week", 2)

    # Last week, approved: Overnight oats every morning.
    frozen_today(household_pin(9, 0, on=datetime.date(2026, 9, 20)))
    last = []
    for i, d in enumerate(LAST):
        last += [_e(d, "breakfast", "Overnight oats", 10), _e(d, "lunch", ["Greek salad", "Lentil soup"][i % 2], 15),
                 _e(d, "dinner", ["Bean chili", "Beef tacos", "Salmon", "Kofte", "Pad thai", "Shrimp"][i], 30),
                 _e(d, "snack", "Apple", 2)]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: last)
    lp = agent.generate_weekly_plan(LAST_MON, day_count=6, period_start=LAST_MON)["weekly_plan_id"]
    conn = get_conn()
    conn.execute("UPDATE weekly_plans SET status = 'approved' WHERE id = ?", (lp,))
    conn.commit()
    conn.close()

    # This week: Sunday 27 September 2026, 3:53pm.
    frozen_today(household_pin(15, 53, on=datetime.date(2026, 9, 27)))
    tools.save_week_intake(
        SUNDAY, day_count=6, freeform=NOTE, moods=["Something new"], cuisines=["Burgers"],
        night_tags={PERIOD[3]: ["left"], PERIOD[5]: ["rush"]},
        weekday_lunches={"prep_days": ["sunday", "tuesday"], "days": [
            {"date": PERIOD[1], "kind": "prepped"}, {"date": PERIOD[2], "kind": "prepped"},
            {"date": PERIOD[3], "kind": "prepped"}, {"date": PERIOD[4], "kind": "prepped"},
            {"date": PERIOD[5], "kind": "leftovers"}]},
    )
    days = []
    snacks = ["Apple slices", "Cheese stick", "Hummus and carrots", "Yogurt cup", "Trail mix", "Rice cakes"]
    for i, d in enumerate(PERIOD):
        days.append(_e(d, "breakfast", "Overnight oats", 10))
        days.append(_e(d, "snack", snacks[i], 3))
        days.append(_e(d, "snack", snacks[(i + 1) % 6], 3))
    days += [
        _e(PERIOD[0], "lunch", "Japanese Vegetable Curry", 50, derived_from={"freeform": CURRY_WORDS}),
        _open(PERIOD[0], "dinner"),
        _e(PERIOD[1], "lunch", "Corn pancakes", 25, derived_from={"inventory": ["corn"]}),
        _e(PERIOD[1], "dinner", "Sheet-pan sausage and peppers", 35),
        _e(PERIOD[2], "lunch", "Chicken salad", 30),
        _e(PERIOD[2], "dinner", "Greek chicken", 40, cuisine="Greek", derived_from={"inputs": ["cuisines:burgers"]}),
        _e(PERIOD[3], "lunch", "Chicken salad", 30),
        _open(PERIOD[3], "dinner"),
        _e(PERIOD[4], "lunch", "Chicken salad", 30),
        _e(PERIOD[4], "dinner", "Beef bowls", 30),
        _e(PERIOD[5], "lunch", "Beef bowls", 0, derived_from={"links_to": f"{PERIOD[4]}:dinner"}),
        _e(PERIOD[5], "dinner", "Beef bowls", 0, derived_from={"links_to": f"{PERIOD[4]}:dinner"}),
    ]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]


def week_table(plan_id) -> str:
    """The finished week, one line a meal — for a failing assert's message."""
    chains = tools.plan_leftover_chains(plan_id)
    lines = []
    for r in _rows(plan_id):
        link = chains["leftovers"].get(r["id"])
        src = f"  <- {link['source']['date'][5:]} {link['source']['slot']}" if link else ""
        lines.append(f"{r['date'][5:]} {r['slot']:9} {r['slot_state']:13} {r['meal']}{src}")
    return "\n".join(lines)


@pytest.mark.real_time_of_day
def test_emilys_week_comes_out_the_way_she_asked(monkeypatch, frozen_today, picker):
    plan_id = _emilys_week(monkeypatch, frozen_today)
    rows = _rows(plan_id)
    chains = tools.plan_leftover_chains(plan_id)
    menu = tools.get_week_menu(plan_id)
    opener = menu["draft_opener"]
    table = week_table(plan_id) + "\n--\n" + "\n".join(opener)

    def at(date, slot):
        return [r for r in rows if r["date"] == date and r["slot"] == slot]

    planned = [r for r in rows if r["slot_state"] == "planned" and r["meal"]]

    # 1. No meal that had gone by is planned: Sunday's breakfast and lunch.
    for slot in ("breakfast", "lunch"):
        assert [r["slot_state"] for r in at(SUNDAY, slot)] == ["planned_empty"], table
    # 2. The curry is Sunday's dinner, and it feeds later meals.
    sunday = at(SUNDAY, "dinner")[0]
    assert sunday["meal"] == "Japanese Vegetable Curry", table
    assert sunday["id"] in chains["sources"], table
    # 3. No open meal anywhere, dinners included.
    assert [r for r in rows if r["slot_state"] == "open"] == [], table
    # 4. Wednesday, tagged Leftovers, reheats an earlier cook.
    assert at(PERIOD[3], "dinner")[0]["id"] in chains["leftovers"], table
    # 5. No dish on more than two meals in a row.
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == [], table
    # 6. No dish cooked fresh on two days running.
    cooks = {}
    for r in planned:
        derived = json.loads(r["derived_from_json"] or "{}")
        if r["id"] in chains["leftovers"] or leftovers.frozen_portion_on(derived) or r["slot"] == "snack":
            continue
        cooks.setdefault(leftovers.dish_identity(r["meal"]), []).append(datetime.date.fromisoformat(r["date"]))
    for dish, dates in cooks.items():
        dates.sort()
        assert all((b - a).days > 1 for a, b in zip(dates, dates[1:])), f"{dish} cooked on {dates}\n{table}"
    # 7. At most two lunch dishes (a lunch that is a dinner's leftovers is not one).
    lunch_dishes = {
        leftovers.dish_identity(chains["leftovers"][r["id"]]["source"]["meal"] if r["id"] in chains["leftovers"]
                                else r["meal"])
        for r in planned if r["slot"] == "lunch"
        and not (r["id"] in chains["leftovers"] and chains["leftovers"][r["id"]]["source"]["slot"] == "dinner")
    }
    assert len(lunch_dishes) <= 2, f"{lunch_dishes}\n{table}"
    # 8. Two snack dishes.
    assert len({r["meal"].strip().lower() for r in planned if r["slot"] == "snack"}) == 2, table
    # 9. A burger, or the draft says there's none.
    assert any("burger" in r["meal"].lower() for r in planned) or any("burger" in l.lower() for l in opener), table
    # 10. The corn is in a dish, or the draft says it couldn't be.
    corn_dish = any("corn" in r["meal"].lower() or "corn" in (json.loads(r["derived_from_json"] or "{}").get("must_use") or [])
                    for r in planned)
    assert corn_dish or any("corn" in l.lower() for l in opener), table
    # 11. Breakfast is not last week's (mood "Something new", and the two-week window).
    assert not any(r["meal"] == "Overnight oats" for r in planned if r["slot"] == "breakfast"), table
    # 12. No row says a reason — only schedule facts (decision C: the
    # rows are rendered by shell.js's own row-line functions, as the Plan
    # tab draws them, and no reason or "as asked" fact reaches the page).
    _no_reason_lines(menu, table)


def _no_reason_lines(menu, table):
    import shutil
    from pathlib import Path
    if shutil.which("node") is None:
        pytest.skip("node is needed to run the row renderers")
    import nodeharness
    from test_week_seven_tiles import _extract

    shell = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")
    rows = [(day["date"], slot, day[slot]) for day in menu["days"] for slot in ("breakfast", "lunch", "dinner")
            if (day.get(slot) or {}).get("state") == "planned"]
    script = (_extract("escapeHtml", shell) + "\n" + _extract("wkRowMetaLine", shell) + "\n"
              + _extract("wkRowMetaHtml", shell) + "\n"
              + f"var rows = {json.dumps([r for _d, _s, r in rows])};\n"
              + "console.log(JSON.stringify(rows.map(function (e) { "
              + "return wkRowMetaHtml(e, wkRowMetaLine(e, e.meta || '')); })));")
    rendered = json.loads(nodeharness.run_node(script).stdout)
    for (date, slot, row), html in zip(rows, rendered):
        for said in (row.get("reason"), row.get("asked")):
            if said and said != row.get("schedule_note"):
                assert said.rstrip(".") not in html, f"{date} {slot} says {said!r}: {html}\n{table}"


# ---------- the fixes behind it, one at a time ----------

def _bare(rows):
    plan_id = tools.create_weekly_plan(SUNDAY, content_start_date=SUNDAY, day_count=6)["weekly_plan_id"]
    for date, slot, meal, derived in rows:
        tools.plan_meal(date, meal, slot=slot, weekly_plan_id=plan_id, derived_from=derived or {})
    return plan_id


def test_every_request_that_didnt_land_is_said_in_one_line():
    """1. "No burgers fit" used to hide behind "I couldn't fit the corn"."""
    from app.tools import draft_opener
    report = {"unmet": [{"words": "I have corn", "ingredient": "corn"}, {"words": "Burgers", "cuisine": "Burgers"}]}
    assert draft_opener._line_two([], report, None) == "I couldn’t fit the corn or a burger in this week."
    report["unmet"].append({"words": "Mexican", "cuisine": "Mexican"})
    assert draft_opener._line_two([], report, None) == \
        "I couldn’t fit the corn, a burger or a Mexican dish in this week."
    assert draft_opener._line_two([], {"unmet": report["unmet"][1:2]}, None) == "No burgers fit this week."


def test_a_batch_lunch_never_overwrites_the_dish_placed_for_their_corn():
    """2a. Sunday's prep feeds Monday: the curry reheats on Monday's lunch,
    and the Corn pancakes that were there move to Monday's dinner."""
    from app.tools import weekday_lunches
    plan_id = _bare([
        (PERIOD[0], "dinner", "Japanese Vegetable Curry", None),
        (PERIOD[1], "lunch", "Corn pancakes", {"inventory": ["corn"]}),
        (PERIOD[1], "dinner", "Tacos", None),
    ])
    intake = {"freeform": "I have corn that I need to use.",
              "weekday_lunches": {"prep_days": ["sunday"], "days": [{"date": PERIOD[1], "kind": "prepped"}]}}

    weekday_lunches.apply_to_plan(plan_id, intake)

    rows = {(r["date"], r["slot"]): r for r in _rows(plan_id)}
    assert rows[(PERIOD[1], "lunch")]["meal"] == "Japanese Vegetable Curry"
    assert rows[(PERIOD[1], "dinner")]["meal"] == "Corn pancakes"
    assert json.loads(rows[(PERIOD[1], "dinner")]["derived_from_json"])["freeform"]


def test_the_corn_can_land_on_a_chained_week_by_replacing_a_whole_chain():
    """2b. Every dinner is one end of a chain: the cook and the meal eating
    it take the corn dish together, and stay a chain."""
    from app.tools import typed_requests
    plan_id = _bare([
        (PERIOD[1], "dinner", "Beef bowls", None),
        (PERIOD[2], "lunch", "Beef bowls", {"links_to": f"{PERIOD[1]}:dinner"}),
    ])
    tools.repair_leftover_chains(plan_id)

    def corn(context):
        return {"meal_name": "Corn chowder", "reason": "uses the corn",
                "ingredients": [{"item": "corn", "qty": "2 cups", "category": "produce"}],
                "instructions": ["Simmer the corn.", "Serve."], "food_groups": ["protein", "vegetable", "carb"],
                "prep_time_minutes": 10, "cook_time_minutes": 20}

    out = typed_requests.use_requested_ingredients(
        plan_id, [{"ingredient": "corn", "words": "I have corn"}], {}, picker=corn)

    assert out["repicked"] == ["corn"]
    rows = {(r["date"], r["slot"]): r for r in _rows(plan_id)}
    assert rows[(PERIOD[1], "dinner")]["meal"] == "Corn chowder"
    assert rows[(PERIOD[2], "lunch")]["meal"] == "Corn chowder"
    chains = tools.plan_leftover_chains(plan_id)
    assert chains["leftovers"][rows[(PERIOD[2], "lunch")]["id"]]["source"]["date"] == PERIOD[1]


def test_a_prepped_batch_cook_or_a_tagged_leftovers_source_is_not_replaced_whole():
    """2b's limits: never a prepped-lunch batch's cook, never the pot a
    Leftovers night they tagged is eating."""
    from app.tools import typed_requests
    plan_id = _bare([
        (PERIOD[1], "dinner", "Beef bowls", None),
        (PERIOD[3], "dinner", "Beef bowls", {"links_to": f"{PERIOD[1]}:dinner", "tags": ["left"],
                                            "constraint": "leftovers_night"}),
        (PERIOD[2], "lunch", "Chicken salad", {"prep_date": PERIOD[2], "constraint": "weekday_lunches"}),
        (PERIOD[3], "lunch", "Chicken salad", {"links_to": f"{PERIOD[2]}:lunch", "cook_ahead": True}),
    ])
    tools.repair_leftover_chains(plan_id)
    entries = typed_requests._load_entries(plan_id)
    chains = tools.plan_leftover_chains(plan_id)
    assert len(chains["sources"]) == 2, "both are real chains"
    assert typed_requests._slot_to_repick(entries, chains) is None


@pytest.mark.real_time_of_day
def test_snacks_stay_while_a_meal_of_the_day_is_ahead(monkeypatch, frozen_today, picker):
    """4. Snacks have no clock: at 3:53pm Sunday keeps its snacks (dinner is
    ahead); at 11:30pm, every meal gone, it has none."""
    days = [_e(d, s, f"{s.title()} {i}") for i, d in enumerate(PERIOD) for s in ("breakfast", "lunch", "dinner")]
    days += [_e(d, "snack", f"Snack {i}", 2) for i, d in enumerate(PERIOD)]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)

    frozen_today(household_pin(15, 53, on=datetime.date(2026, 9, 27)))
    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]
    assert [r for r in _rows(plan_id) if r["date"] == SUNDAY and r["slot"] == "snack"]

    frozen_today(household_pin(23, 30, on=datetime.date(2026, 9, 27)))
    plan_id = agent.generate_weekly_plan(SUNDAY, day_count=6, period_start=SUNDAY)["weekly_plan_id"]
    assert [r for r in _rows(plan_id) if r["date"] == SUNDAY and r["slot"] == "snack"] == []
    assert [r for r in _rows(plan_id) if r["date"] == PERIOD[1] and r["slot"] == "snack"]
