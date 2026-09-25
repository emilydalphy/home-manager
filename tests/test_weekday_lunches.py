"""
Plan a week, step 3: weekday lunches — how many prepped, leftovers, or
cooked that day.

Loop Board card (Emily, 2026-09-25, mockup A1 "Say how many of each"):
step 3 becomes "Weekday lunches" with the number of weekday lunches in the
period ("5 lunches, Monday to Friday."), three − / + rows that add up to it
(Made on a prep day · Leftovers from dinner · Cooked that day, "20 minutes
or less"), "Which day will you cook?" chips when a lunch is cooked that
day, prep-day chips when one is prepped (starting from the household's
prep days), the on-the-go days as "Taking it with you", and a day-by-day
list where a tap changes a day. It opens filled in from last week's
answers. Planning follows: prepped lunches are one cook per prep day eaten
within three days, a leftovers lunch is the dinner before it cooked
bigger, a lunch cooked that day is 20 minutes or less.

Every test here fails on main (7983d7f): there is no weekday_lunches
column, answer, carry-over, lunch_kind, planning pass or screen.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import nodeharness
from app import agent, tools
from app.db import get_conn
from app.tools import leftovers, time_caps, weekday_lunches

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
AGENT = (REPO / "app" / "agent.py").read_text(encoding="utf-8").replace("\\\n", "")

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the page's own functions"
)


def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _plus(iso: str, n: int) -> str:
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=n)).isoformat()


# ==========================================================================
# 1. The stored answer
# ==========================================================================

class TestTheStoredAnswer:
    def test_saved_in_one_shape_with_counts_prep_days_and_the_dinner_it_reheats(self):
        mon = _monday()
        saved = tools.save_week_intake(mon, weekday_lunches={
            "prep_days": ["Wednesday", "sunday"],
            "days": [
                {"date": _plus(mon, 4), "kind": "cooked"},
                {"date": mon, "kind": "prepped"},
                {"date": _plus(mon, 1), "kind": "prepped"},
                {"date": _plus(mon, 2), "kind": "leftovers"},
                {"date": _plus(mon, 3), "kind": "prepped"},
            ],
        })
        wl = saved["weekday_lunches"]
        assert wl == {
            "counts": {"prepped": 3, "leftovers": 1, "cooked": 1},
            "prep_days": ["sunday", "wednesday"],
            "days": [
                {"date": mon, "weekday": "monday", "kind": "prepped", "prep_day": "sunday"},
                {"date": _plus(mon, 1), "weekday": "tuesday", "kind": "prepped", "prep_day": "sunday"},
                {"date": _plus(mon, 2), "weekday": "wednesday", "kind": "leftovers", "from_dinner": _plus(mon, 1)},
                {"date": _plus(mon, 3), "weekday": "thursday", "kind": "prepped", "prep_day": "wednesday"},
                {"date": _plus(mon, 4), "weekday": "friday", "kind": "cooked"},
            ],
        }
        assert tools.get_week_intake(mon)["weekday_lunches"] == wl

    def test_not_answered_is_an_empty_object_and_other_saves_keep_it(self):
        mon = _monday()
        assert tools.save_week_intake(mon, moods=["Comfort food"])["weekday_lunches"] == {}
        tools.save_week_intake(mon, weekday_lunches={"days": [{"date": mon, "kind": "cooked"}]})
        kept = tools.save_week_intake(mon, moods=["Something warm"])
        assert kept["weekday_lunches"]["days"] == [{"date": mon, "weekday": "monday", "kind": "cooked"}]
        # No prepped lunch: no prep days carried, whatever was sent.
        assert kept["weekday_lunches"]["prep_days"] == []
        assert tools.save_week_intake(mon, weekday_lunches={})["weekday_lunches"] == {}

    def test_a_day_later_left_out_of_the_plan_drops_its_lunch_and_a_leftovers_lunch_after_it(self):
        mon = _monday()
        tools.save_week_intake(mon, weekday_lunches={"days": [
            {"date": _plus(mon, 1), "kind": "cooked"},
            {"date": _plus(mon, 2), "kind": "leftovers"},
            {"date": _plus(mon, 3), "kind": "cooked"},
        ]})
        saved = tools.save_week_intake(mon, skipped_days=[_plus(mon, 1)])
        assert [d["date"] for d in saved["weekday_lunches"]["days"]] == [_plus(mon, 3)]

    @pytest.mark.parametrize("answer, why", [
        ({"days": [{"date": "SAT", "kind": "cooked"}]}, "date"),
        ({"days": [{"date": "+5", "kind": "cooked"}]}, "weekend"),
        ({"days": [{"date": "+14", "kind": "cooked"}]}, "period"),
        ({"days": [{"date": "+0", "kind": "leftovers"}]}, "dinner before"),
        ({"days": [{"date": "+1", "kind": "prepped"}]}, "prep day"),
        ({"days": [{"date": "+1", "kind": "fresh"}]}, "kind"),
        ({"days": [{"date": "+1", "kind": "cooked"}, {"date": "+1", "kind": "cooked"}]}, "twice"),
        ({"days": [{"date": "+1", "kind": "prepped"}], "prep_days": ["someday"]}, "day of the week"),
    ])
    def test_what_the_screen_would_never_send_is_refused(self, answer, why):
        mon = _monday()
        days = []
        for d in answer["days"]:
            raw = d["date"]
            iso = raw if not raw.startswith("+") else _plus(mon, int(raw[1:]))
            days.append(dict(d, date=iso))
        with pytest.raises(ValueError):
            tools.save_week_intake(mon, weekday_lunches=dict(answer, days=days))

    def test_the_route_takes_it_and_says_no_plainly(self, signed_in):
        client = signed_in
        mon = _monday()
        ok = client.post(f"/api/week/{mon}/intake", json={
            "weekday_lunches": {"days": [{"date": _plus(mon, 1), "kind": "cooked"}]},
        })
        assert ok.status_code == 200
        assert ok.json()["weekday_lunches"]["counts"] == {"prepped": 0, "leftovers": 0, "cooked": 1}
        bad = client.post(f"/api/week/{mon}/intake", json={
            "weekday_lunches": {"days": [{"date": mon, "kind": "leftovers"}]},
        })
        assert bad.status_code == 400
        assert "dinner before it isn’t planned" in bad.json()["detail"]

    def test_the_prep_day_is_the_nearest_one_on_or_before_the_lunch(self):
        assert weekday_lunches.prep_day_for("2026-09-28", ["sunday"]) == ("sunday", 1)        # Mon
        assert weekday_lunches.prep_day_for("2026-10-01", ["sunday"]) == ("sunday", 4)        # Thu
        assert weekday_lunches.prep_day_for("2026-10-01", ["sunday", "wednesday"]) == ("wednesday", 1)
        assert weekday_lunches.prep_day_for("2026-09-30", ["wednesday"]) == ("wednesday", 0)  # same day
        assert weekday_lunches.prep_day_for("2026-09-28", []) is None


# ==========================================================================
# 2. Next week opens on this week's answer
# ==========================================================================

class TestCarryOver:
    def test_last_weeks_lunches_travel_by_weekday_with_the_on_the_go_days(self):
        last, this = _monday(1), _monday(2)
        tools.save_week_intake(last, packed_lunch_days=[_plus(last, 1), _plus(last, 5)], weekday_lunches={
            "prep_days": ["sunday"],
            "days": [
                {"date": last, "kind": "prepped"},
                {"date": _plus(last, 1), "kind": "prepped"},
                {"date": _plus(last, 2), "kind": "leftovers"},
                {"date": _plus(last, 3), "kind": "prepped"},
                {"date": _plus(last, 4), "kind": "cooked"},
            ],
        })
        carried = tools.get_week_intake_prefill(this)["last_intake"]["weekday_lunches"]
        assert carried == {
            "counts": {"prepped": 3, "leftovers": 1, "cooked": 1},
            "prep_days": ["sunday"],
            "kinds": {"monday": "prepped", "tuesday": "prepped", "wednesday": "leftovers",
                      "thursday": "prepped", "friday": "cooked"},
            # Weekday lunches only: Saturday's packed lunch isn't this screen's.
            "on_the_go": ["tuesday"],
        }

    def test_the_prep_day_chips_start_from_the_households_prep_days(self):
        tools.set_prep_days([{"weekday": "wednesday"}, {"weekday": "sunday"}])
        assert tools.get_week_intake_prefill(_monday())["rhythm_prep_days"] == ["sunday", "wednesday"]

    def test_re_planning_opens_on_this_weeks_own_answer(self):
        mon = _monday()
        tools.save_week_intake(mon, weekday_lunches={"days": [{"date": _plus(mon, 2), "kind": "cooked"}]})
        intake = tools.get_week_intake_prefill(mon)["intake"]
        assert intake["weekday_lunches"]["days"][0]["date"] == _plus(mon, 2)


# ==========================================================================
# 3. The time caps follow the answer
# ==========================================================================

class TestTimeCaps:
    MEMORY = {"rhythm": {"prep_days": [{"weekday": "wednesday"}]}}

    def test_a_lunch_cooked_that_day_is_twenty_minutes_even_on_a_prep_day(self):
        wednesday = "2026-09-30"
        assert time_caps.minutes_cap(wednesday, "lunch", [], self.MEMORY) is None
        assert time_caps.minutes_cap(wednesday, "lunch", [], self.MEMORY, lunch_kind="cooked") == 20

    def test_prepped_and_leftovers_lunches_have_no_cap(self):
        tuesday = "2026-09-29"
        assert time_caps.minutes_cap(tuesday, "lunch", [], {}) == 20
        assert time_caps.minutes_cap(tuesday, "lunch", [], {}, lunch_kind="prepped") is None
        assert time_caps.minutes_cap(tuesday, "lunch", [], {}, lunch_kind="leftovers") is None
        # A reheat is still a reheat, whatever was said.
        assert time_caps.minutes_cap(tuesday, "lunch", [], {}, is_leftovers=True, lunch_kind="cooked") is None

    def test_the_generator_reads_the_kind_off_the_intake(self):
        intake = {"night_tags": {}, "weekday_lunches": {"days": [
            {"date": "2026-09-30", "kind": "cooked"}, {"date": "2026-09-29", "kind": "prepped"}]}}
        assert agent._meal_minutes_cap("2026-09-30", "lunch", intake, self.MEMORY) == 20
        assert agent._meal_minutes_cap("2026-09-29", "lunch", intake, {}) is None
        # Dinner is untouched by a lunch answer.
        assert agent._meal_minutes_cap("2026-09-30", "dinner", intake, {}) is None

    def test_the_swap_and_the_quality_check_read_it_too(self):
        src = (REPO / "app" / "tools" / "swap_in_place.py").read_text(encoding="utf-8")
        assert "_minutes_cap(entry, tags, memory, _lunch_kind(intake, entry))" in src
        assert "_minutes_cap(e, tags_by_date.get(e[\"date\"]) or [], memory, _lunch_kind(intake, e))" in src
        quality = (REPO / "app" / "tools" / "plan_quality.py").read_text(encoding="utf-8")
        assert '"lunch_kinds": _weekday_lunches.kinds_by_date(intake_ctx),' in quality
        assert "lunch_kind=kinds.get(entry[\"date\"])" in quality


# ==========================================================================
# 4. Planning follows the answer
# ==========================================================================

def _slot(date, slot, name, minutes=30):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} stuff", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}


def _week_of(dates, lunches, dinners):
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", "Oats"))
        out.append(_slot(d, "lunch", lunches[i]))
        out.append(_slot(d, "dinner", dinners[i]))
    return out


def _rows(plan_id: int, slot: str) -> dict[str, dict]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT mpe.id, mpe.date, mpe.slot_state, mpe.derived_from_json, mpe.reasoning,
               COALESCE(r.name, mpe.freeform_meal) AS meal
        FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id
        WHERE mpe.weekly_plan_id = ? AND mpe.slot = ? AND mpe.component_category IS NULL
        ORDER BY mpe.date, mpe.id
        """,
        (plan_id, slot),
    ).fetchall()
    conn.close()
    return {r["date"]: dict(r, derived=json.loads(r["derived_from_json"] or "{}")) for r in rows}


@pytest.fixture
def two_adults():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


@pytest.fixture
def seen_context(monkeypatch):
    seen = {}

    def _stub(days):
        def _generate(ctx):
            seen["ctx"] = ctx
            return days
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", _generate)
    return seen, _stub


def test_the_week_is_planned_as_the_household_said(two_adults, seen_context):
    seen, stub = seen_context
    # A standing lunch count of two would fold the five lunches into two
    # dishes; the week's own answer outranks it.
    tools.set_household_meal_preferences(lunches_per_week=2)
    mon = _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={
        "prep_days": ["sunday", "wednesday"],
        "days": [
            {"date": dates[0], "kind": "prepped"},
            {"date": dates[1], "kind": "prepped"},
            {"date": dates[2], "kind": "leftovers"},
            {"date": dates[3], "kind": "prepped"},
            {"date": dates[4], "kind": "cooked"},
        ],
    })
    stub(_week_of(
        dates,
        lunches=["Chili", "Soup", "Salad", "Curry", "Sandwich", "Pita", "Toastie"],
        dinners=["Tacos", "Roast Chicken", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"],
    ))

    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]

    # The model was told, in the context and in the prompt.
    assert seen["ctx"]["intake"]["weekday_lunches"]["counts"] == {"prepped": 3, "leftovers": 1, "cooked": 1}
    lunch = _rows(plan_id, "lunch")
    dinner = _rows(plan_id, "dinner")
    chains = tools.plan_leftover_chains(plan_id)

    # Prepped Sunday: Monday and Tuesday are one Chili, cooked on Monday's
    # entry and made ahead for Tuesday. The cook says when to cook it.
    assert lunch[dates[0]]["meal"] == "Chili" and lunch[dates[1]]["meal"] == "Chili"
    assert chains["leftovers"][lunch[dates[1]]["id"]]["source"]["entry_id"] == lunch[dates[0]]["id"]
    assert lunch[dates[1]]["derived"].get("cook_ahead") is True
    assert lunch[dates[0]]["derived"]["prep_day"] == "sunday"
    assert lunch[dates[0]]["reasoning"] == "Cook this Sunday for Monday and Tuesday’s lunches."

    # Wednesday is Tuesday's dinner, reheated, and that dinner cooks for it.
    assert lunch[dates[2]]["meal"] == "Roast Chicken"
    assert chains["leftovers"][lunch[dates[2]]["id"]]["source"]["entry_id"] == dinner[dates[1]]["id"]
    assert f"{dates[2]}:lunch" in dinner[dates[1]]["derived"]["make_double_for"]

    # Prepped Wednesday: Thursday is its own batch.
    assert lunch[dates[3]]["meal"] == "Curry"
    assert lunch[dates[3]]["derived"]["prep_day"] == "wednesday"
    assert lunch[dates[3]]["id"] not in chains["leftovers"]

    # Cooked that day: Friday is left a fresh cook — the count didn't fold it.
    assert lunch[dates[4]]["meal"] == "Sandwich"
    assert lunch[dates[4]]["id"] not in chains["leftovers"]
    assert tools.audit_plan_slots(plan_id)["complete"] is True


def test_a_prepped_lunch_past_three_days_eats_a_frozen_portion(two_adults, seen_context):
    _seen, stub = seen_context
    mon = _monday()
    dates = tools._week_dates(mon)
    tools.save_week_intake(mon, weekday_lunches={
        "prep_days": ["sunday"],
        "days": [{"date": d, "kind": "prepped"} for d in dates[:4]],
    })
    stub(_week_of(dates, lunches=["Chili", "Soup", "Salad", "Curry", "Wrap", "Pita", "Toastie"],
                  dinners=["Tacos", "Roast", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]))

    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]

    lunch = _rows(plan_id, "lunch")
    chains = tools.plan_leftover_chains(plan_id)
    assert [lunch[d]["meal"] for d in dates[:3]] == ["Chili"] * 3
    # Thursday is four days after Sunday.
    assert lunch[dates[3]]["meal"] == "Leftovers from the freezer — Monday’s Chili"
    assert lunch[dates[3]]["derived"][leftovers.FROM_FREEZER_KEY]["dish"] == "Chili"
    # One portion per person at the table, frozen on the cook.
    assert chains["freezer"].get(lunch[dates[0]]["id"]) == 2


def test_no_answer_plans_exactly_as_before(two_adults, seen_context):
    seen, stub = seen_context
    mon = _monday()
    dates = tools._week_dates(mon)
    stub(_week_of(dates, lunches=["Chili", "Soup", "Salad", "Curry", "Wrap", "Pita", "Toastie"],
                  dinners=["Tacos", "Roast", "Pasta", "Stir-fry", "Pizza", "Burgers", "Stew"]))
    plan_id = agent.generate_weekly_plan(mon)["weekly_plan_id"]
    assert [r["meal"] for r in _rows(plan_id, "lunch").values()] == \
        ["Chili", "Soup", "Salad", "Curry", "Wrap", "Pita", "Toastie"]
    assert weekday_lunches.apply_to_plan(plan_id, {"weekday_lunches": {}}) == \
        {"leftovers": [], "prepped": [], "frozen": [], "skipped": []}


def test_the_prompt_says_what_each_kind_means():
    assert "`intake.weekday_lunches.days`, when present, is how the household said each Monday-Friday lunch gets made" in AGENT
    for kind in ("`prepped` — made ahead on the prep day", "`leftovers` — that lunch is the dinner of the evening before",
                 "`cooked` — cooked fresh that day, {lunch_max} minutes of prep+cook at most"):
        assert kind in AGENT, kind
    assert '"weekday_lunches": intake.get("weekday_lunches") or {},' in AGENT
    finish = AGENT[AGENT.index("def _finish_week_slots("):AGENT.index("_complete_plates_pass(plan_id, household_memory, intake)")]
    assert finish.index("tools.repair_leftover_chains(plan_id)") < finish.index("_weekday_lunches.apply_to_plan(plan_id, intake)") \
        < finish.index("_meal_variety.repick_repeats(")
    assert 'if slot == "lunch" and lunches_answered:' in finish


# ==========================================================================
# 5. The screen
# ==========================================================================

def _extract(name: str, source: str = PAGE) -> str:
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, j = 0, i
    while True:
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return source[start:j + 1]


def _var(name: str) -> str:
    m = re.search(rf"  var {name} = [\s\S]*?;\n", PAGE)
    assert m, name
    return m.group(0)


def _section(qid: str) -> str:
    start = PAGE.index(f'<section id="{qid}"')
    return PAGE[start:PAGE.index("</section>", start)]


_FUNCS = ("isoLocal", "addDaysIso", "isoWeekday", "titleDay", "isWeekdayIso", "prevIso", "prepDayFor",
          "lunchDates", "leftoversOk", "lunchCounts", "lunchLeaving", "lunchStep", "lunchCycle",
          "ensurePrepReach", "lunchPrefill", "lunchLine", "lunchesSentence", "joinWords", "sentence",
          "lunchUsingLine")


def _node(script: str) -> object:
    prelude = (
        _var("LUNCH_KINDS") + _var("LUNCH_KEEP_DAYS") + _var("PREP_WEEKDAYS")
        + "\n".join(_extract(f) for f in _FUNCS) + "\n"
    )
    res = nodeharness.run_node(prelude + f"console.log(JSON.stringify({script}));", timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip().splitlines()[-1])


# Mon 28 Sep – Fri 2 Oct 2026, planned Monday to Sunday.
WEEK = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
PERIOD = WEEK + ["2026-10-03", "2026-10-04"]
OK = f"function (d) {{ return leftoversOk(d, {json.dumps(PERIOD)}, null); }}"


class TestTheScreen:
    def test_the_step_is_weekday_lunches_with_the_three_rows_and_the_groups(self):
        q3 = _section("q3")
        assert "<h1>Weekday lunches</h1>" in q3
        assert '<p class="step-sub" id="lunch-sub"></p>' in q3
        order = ['id="lunch-counts"', 'id="lunch-cook-group" hidden', 'id="lunch-prep-group" hidden',
                 "Taking it with you", 'id="lunch-week"', "Tap a day to change it."]
        positions = [q3.index(x) for x in order]
        assert positions == sorted(positions)
        assert "<p class=\"eyebrow group-eyebrow\">Which day will you cook?</p>" in q3
        assert "<p class=\"eyebrow group-eyebrow\">Prep day</p>" in q3
        copy = _var("LUNCH_COPY")
        for line in ("prepped: 'Made on a prep day'", "leftovers: 'Leftovers from dinner'",
                     "cooked: 'Cooked that day'", "cookedSub: '20 minutes or less'"):
            assert line in copy, line
        # The groups show only when they have something to say.
        paint = _extract("paintLunchStep")
        assert "$('lunch-cook-group').hidden = counts.cooked < 1;" in paint
        assert "$('lunch-prep-group').hidden = counts.prepped < 1;" in paint

    def test_every_control_is_44px_and_colours_go_through_tokens(self):
        css = PAGE[PAGE.index("/* ---------- Weekday lunches (step 3"):PAGE.index("/* The typed answer (step 5)")]
        assert "min-height: 44px" in css            # each day row is a button
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)
        assert "var(--hairline)" in css and "var(--surface)" in css
        # The steppers are the day sheet's own 44px ones.
        assert '<span class="stepper" role="group"' in _extract("renderLunchStep")

    def test_step_three_saves_the_answer_and_a_period_without_weekday_lunches_skips_it(self):
        save = _extract("saveStep")
        assert "saveIntake({ packed_lunch_days: answers.packed_lunch_days, weekday_lunches: lunchPayload() })" in save
        assert "return n === 3 && !!data && currentLunchDates().length === 0;" in _extract("skipsStep")
        assert "if (skipsStep(to)) to += 1;" in _extract("advance")
        assert "if (skipsStep(to)) to -= 1;" in _extract("goBack")
        assert "if (lunch.ready) payload.weekday_lunches = lunchPayload();" in _extract("leavePayload")

    @_needs_node
    def test_the_line_under_the_question(self):
        assert _node(f"lunchesSentence({json.dumps(WEEK)})") == "5 lunches, Monday to Friday."
        assert _node(f"lunchesSentence({json.dumps(WEEK[2:])})") == "3 lunches, Wednesday to Friday."
        assert _node("lunchesSentence(['2026-10-02'])") == "1 lunch, Friday."
        assert _node(f"lunchesSentence({json.dumps([WEEK[0], WEEK[1], WEEK[3], WEEK[4]])})") == \
            "4 lunches: Monday, Tuesday, Thursday and Friday."

    @_needs_node
    def test_only_weekdays_and_only_days_somebody_is_home_for_lunch(self):
        days = json.dumps([{"date": d} for d in PERIOD])
        assert _node(f"lunchDates({days}, null)") == WEEK
        assert _node(f"lunchDates({days}, function (d, s) {{ return d === '2026-09-30' && s === 'lunch'; }})") == \
            [WEEK[0], WEEK[1], WEEK[3], WEEK[4]]

    @_needs_node
    def test_the_counts_always_add_up_and_each_tap_moves_one_day(self):
        kinds = {d: "prepped" for d in WEEK}
        # + cooked takes the LAST prepped day: Friday.
        after = _node(f"lunchStep({json.dumps(kinds)}, {json.dumps(WEEK)}, {OK}, 'cooked', 1)")
        assert after == dict(kinds, **{WEEK[4]: "cooked"})
        # + leftovers takes the last prepped day that has a dinner before it.
        after2 = _node(f"lunchStep({json.dumps(after)}, {json.dumps(WEEK)}, {OK}, 'leftovers', 1)")
        assert after2 == dict(after, **{WEEK[3]: "leftovers"})
        # Monday can't be leftovers in a week that starts Monday.
        mon_only = {WEEK[0]: "prepped"}
        assert _node(f"lunchStep({json.dumps(mon_only)}, {json.dumps(WEEK[:1])}, {OK}, 'leftovers', 1)") is None
        # − on cooked gives the day back; with no leftovers in the week it
        # goes to leftovers only when there's nothing prepped to join.
        back = _node(f"lunchStep({json.dumps(after)}, {json.dumps(WEEK)}, {OK}, 'cooked', -1)")
        assert back == kinds
        # A − with nothing to take is a no-op.
        assert _node(f"lunchStep({json.dumps(kinds)}, {json.dumps(WEEK)}, {OK}, 'cooked', -1)") is None
        for state in (after, after2, back):
            counts = _node(f"lunchCounts({json.dumps(state)}, {json.dumps(WEEK)})")
            assert sum(counts.values()) == 5

    @_needs_node
    def test_a_tap_on_a_day_cycles_it(self):
        assert _node("lunchCycle('prepped', true)") == "leftovers"
        assert _node("lunchCycle('prepped', false)") == "cooked"
        assert _node("lunchCycle('leftovers', true)") == "cooked"
        assert _node("lunchCycle('cooked', true)") == "prepped"

    @_needs_node
    def test_a_prepped_lunch_out_of_reach_brings_in_a_midweek_prep_day(self):
        kinds = {WEEK[0]: "prepped", WEEK[1]: "prepped", WEEK[2]: "leftovers", WEEK[3]: "prepped", WEEK[4]: "cooked"}
        assert _node(f"ensurePrepReach(['sunday'], {json.dumps(kinds)}, {json.dumps(WEEK)}, 'sunday')") == \
            ["sunday", "wednesday"]
        # Nothing prepped, nothing added; nothing chosen yet, the fallback first.
        assert _node(f"ensurePrepReach([], {json.dumps({d: 'cooked' for d in WEEK})}, {json.dumps(WEEK)}, 'sunday')") == []
        assert _node(f"ensurePrepReach([], {json.dumps({WEEK[0]: 'prepped'})}, {json.dumps(WEEK[:1])}, 'saturday')") == \
            ["saturday"]

    @_needs_node
    def test_the_prep_day_rule_is_the_servers(self):
        for lunch, days in (("2026-09-28", ["sunday"]), ("2026-10-01", ["sunday"]),
                            ("2026-10-01", ["sunday", "wednesday"]), ("2026-09-30", ["wednesday"])):
            server = weekday_lunches.prep_day_for(lunch, days)
            assert _node(f"prepDayFor('{lunch}', {json.dumps(days)})") == {"day": server[0], "back": server[1]}

    @_needs_node
    def test_the_days_say_what_each_one_gets(self):
        assert _node(f"lunchLine('{WEEK[0]}', 'prepped', ['sunday'])") == "Prepped Sunday"
        assert _node(f"lunchLine('{WEEK[3]}', 'prepped', ['sunday'])") == "Prepped Sunday, from the freezer"
        assert _node(f"lunchLine('{WEEK[2]}', 'leftovers', [])") == "Leftovers from Tuesday’s dinner"
        assert _node(f"lunchLine('{WEEK[4]}', 'cooked', [])") == "Cooked that day"

    @_needs_node
    def test_it_opens_on_last_weeks_answer_by_weekday(self):
        carry = {"prep_days": ["sunday"], "kinds": {"monday": "leftovers", "tuesday": "prepped",
                                                    "wednesday": "leftovers", "friday": "cooked"}}
        got = _node(f"lunchPrefill({json.dumps(WEEK)}, {json.dumps(carry)}, {OK}, ['wednesday'])")
        assert got["prepDays"] == ["sunday"]
        assert got["kinds"] == {
            WEEK[0]: "prepped",      # leftovers last week, but no Sunday dinner in this period
            WEEK[1]: "prepped",
            WEEK[2]: "leftovers",
            WEEK[3]: "leftovers",    # not answered last week: last week's most common kind
            WEEK[4]: "cooked",
        }

    @_needs_node
    def test_a_first_week_opens_on_the_households_prep_days_or_cooks_each_day(self):
        got = _node(f"lunchPrefill({json.dumps(WEEK)}, null, {OK}, ['sunday'])")
        assert got == {"kinds": {d: "prepped" for d in WEEK}, "prepDays": ["sunday"]}
        got = _node(f"lunchPrefill({json.dumps(WEEK)}, null, {OK}, [])")
        assert got == {"kinds": {d: "cooked" for d in WEEK}, "prepDays": []}

    @_needs_node
    def test_the_building_screen_says_it_in_one_line(self):
        # Wednesday's chip is on, but no lunch comes from it.
        wl = {"prep_days": ["sunday", "wednesday"], "days": [
            {"date": WEEK[0], "weekday": "monday", "kind": "prepped", "prep_day": "sunday"},
            {"date": WEEK[1], "weekday": "tuesday", "kind": "prepped", "prep_day": "sunday"},
            {"date": WEEK[2], "weekday": "wednesday", "kind": "leftovers"},
            {"date": WEEK[3], "weekday": "thursday", "kind": "prepped", "prep_day": "sunday"},
            {"date": WEEK[4], "weekday": "friday", "kind": "cooked"},
        ]}
        assert _node(f"lunchUsingLine({json.dumps(wl)})") == \
            "3 lunches prepped Sunday, 1 from dinner and 1 cooked Friday."
        assert _node("lunchUsingLine({})") == ""
