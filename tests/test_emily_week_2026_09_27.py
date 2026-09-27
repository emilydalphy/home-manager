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


# ---------- review of d9257c6: the integration reviewer's probes ----------

def _next_monday():
    from conftest import household_today
    t = household_today()
    return (t - datetime.timedelta(days=t.weekday()) + datetime.timedelta(days=7)).isoformat()


def _probe_week(dates, lunches, dinners, lunch_extra=None, dinner_extra=None, lunch_minutes=None):
    days = []
    for i, d in enumerate(dates):
        days.append(_e(d, "breakfast", "Oats", 10))
        days.append(_e(d, "lunch", lunches[i], (lunch_minutes or {}).get(i, 20), **((lunch_extra or {}).get(i) or {})))
        days.append(_e(d, "dinner", dinners[i], 30, **((dinner_extra or {}).get(i) or {})))
    return days


_PROBE_LUNCHES = ["Chicken salad", "Corn chowder", "Chicken salad", "Chicken salad", "Lentil soup", "Tuna melt", "Cobb salad"]
_CORN = {1: {"derived_from": {"inventory": ["corn"]}}}


def _by_slot(plan_id):
    return {(r["date"], r["slot"]): r for r in _rows(plan_id) if r["slot"] in ("lunch", "dinner")}


def test_a_moved_request_dish_goes_where_it_fits_the_time(monkeypatch, picker):
    """REVIEW 2 (probe_rush_cap). A 60-minute corn chowder displaced from
    Tuesday's prepped lunch skips Tuesday's rush dinner and lands on the
    first dinner it fits."""
    tools.set_household_meal_preferences(lunches_per_week=2, mark_complete=False)
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="I have corn I need to use.", night_tags={dates[1]: ["rush"]},
                           weekday_lunches={"prep_days": ["sunday"], "days": [
                               {"date": dates[i], "kind": "prepped"} for i in range(4)]})
    dinners = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _probe_week(
        dates, _PROBE_LUNCHES, dinners, lunch_extra=_CORN, lunch_minutes={1: 60}))

    rows = _by_slot(agent.generate_weekly_plan(week)["weekly_plan_id"])

    assert rows[(dates[1], "dinner")]["meal"] != "Corn chowder", "not onto the 30-minute rush night"
    assert rows[(dates[2], "dinner")]["meal"] == "Corn chowder"


def test_a_moved_request_dish_never_takes_a_holiday_dish_night(monkeypatch, picker):
    """REVIEW 1 (probe_holiday_dish). The pie they're bringing on Tuesday
    stays; the corn chowder goes to the next ordinary dinner."""
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, freeform="I have corn I need to use.",
                           weekday_lunches={"prep_days": ["sunday"], "days": [
                               {"date": dates[i], "kind": "prepped"} for i in range(4)]})
    dinners = ["Pumpkin pie" if i == 1 else f"Dinner {i}" for i in range(7)]
    holiday = {1: {"derived_from": {"holiday": "Thanksgiving", "holiday_dish": True, "constraint": "bring_a_dish"}}}
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _probe_week(
        dates, _PROBE_LUNCHES, dinners, lunch_extra=_CORN, dinner_extra=holiday))

    rows = _by_slot(agent.generate_weekly_plan(week)["weekly_plan_id"])

    assert rows[(dates[1], "dinner")]["meal"] == "Pumpkin pie"
    assert rows[(dates[2], "dinner")]["meal"] == "Corn chowder"


def test_dinners_that_feed_lunches_still_fold_to_the_count(monkeypatch, picker):
    """REVIEW 3 (probe_leftover_lunch_sources_vs_count). Dinners = 2, every
    weekday lunch Tue–Fri is last night's leftovers, the model sends seven
    dinners: two dinner dishes are cooked, and every one of those lunches
    still reheats a dinner."""
    tools.set_household_meal_preferences(dinners_per_week=2, lunches_per_week=2, mark_complete=False)
    week = _next_monday()
    dates = tools._week_dates(week)
    tools.save_week_intake(week, weekday_lunches={"days": [
        {"date": dates[i], "kind": "leftovers"} for i in range(1, 5)]})
    dinners = ["Lamb stew", "Miso cod", "Bibimbap", "Ratatouille", "Jerk chicken", "Pierogi", "Dal"]
    lunches = ["Tuna melt" if i in (0, 5, 6) else dinners[i - 1] for i in range(7)]
    links = {i: {"derived_from": {"links_to": f"{dates[i - 1]}:dinner"}} for i in range(1, 5)}
    monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: _probe_week(dates, lunches, dinners, lunch_extra=links))

    plan_id = agent.generate_weekly_plan(week)["weekly_plan_id"]

    chains = tools.plan_leftover_chains(plan_id)
    rows = _by_slot(plan_id)
    cooks = {leftovers.dish_identity(r["meal"]) for (d, s), r in rows.items() if s == "dinner"
             and r["slot_state"] == "planned" and r["id"] not in chains["leftovers"]
             and not leftovers.frozen_portion_on(json.loads(r["derived_from_json"] or "{}"))}
    assert len(cooks) == 2, (cooks, week_table(plan_id))
    for i in range(1, 5):
        link = chains["leftovers"].get(rows[(dates[i], "lunch")]["id"])
        assert link and link["source"]["slot"] == "dinner", week_table(plan_id)
    assert leftovers.long_runs(leftovers.run_keys(plan_id)) == []


def test_a_request_lunch_kept_over_a_prepped_batch_is_said():
    """REVIEW 4. Nowhere to move the corn lunch (Monday's only dinner is
    the holiday dish they're bringing): the lunch stays, and one plain
    line says it isn't from Sunday's prep."""
    from app.tools import draft_opener, weekday_lunches
    plan_id = _bare([
        (PERIOD[0], "dinner", "Japanese Vegetable Curry", None),
        (PERIOD[1], "lunch", "Corn pancakes", {"inventory": ["corn"]}),
        (PERIOD[1], "dinner", "Pumpkin pie", {"holiday": "Thanksgiving", "holiday_dish": True}),
    ])
    intake = {"freeform": "I have corn that I need to use.",
              "weekday_lunches": {"prep_days": ["sunday"], "days": [{"date": PERIOD[1], "kind": "prepped"}]}}

    out = weekday_lunches.apply_to_plan(plan_id, intake)

    line = "Monday’s lunch stays Corn pancakes, as you asked — it isn’t from Sunday’s prep."
    assert out["said"] == [line]
    assert _by_slot(plan_id)[(PERIOD[1], "lunch")]["meal"] == "Corn pancakes"
    tools.record_plan_requests(plan_id, {"said_lines": out["said"]})
    menu = tools.get_week_menu(plan_id)
    assert line in menu["draft_opener"]


def test_a_leftovers_lunch_with_nothing_to_reheat_is_said():
    """REVIEW 3's fallback: the evening before is empty, so Friday's lunch
    can't be last night's leftovers — said in one plain line."""
    from app.tools import weekday_lunches, weekly_plan
    plan_id = _bare([
        (PERIOD[5], "lunch", "Toastie", None),
    ])
    weekly_plan.plan_slot_empty(weekly_plan_id=plan_id, meal_date=PERIOD[4], slot="dinner",
                                reason="You’re out.", derived_from={"constraint": "nobody_home"})
    intake = {"weekday_lunches": {"days": [{"date": PERIOD[5], "kind": "leftovers"}]}}

    out = weekday_lunches.repoint_leftover_lunches(plan_id, intake)

    assert out["said"] == ["Friday’s lunch isn’t last night’s leftovers — nothing cooked before it could stretch that far."]
