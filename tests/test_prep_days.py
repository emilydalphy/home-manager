"""
Prep days, and the sessions they hold.

Emily, 2026-09-04 and again 2026-09-08: "I like to do some prep on Sunday
to make the week easier, make some things fresh during the week, and then
do another prep Wednesday/Thursday depending on the week."

Two slices are covered here and one is deliberately absent:

  A — the QUESTION. A seventh household rhythm fact (`prep_days`), asked
      once in onboarding, editable on What we know, and correctable in
      chat through `set_prep_days` — including the one-off
      ("I can't prep this Sunday"), which is a flag on the PLAN and must
      never edit the standing answer.
  B — the prep SESSIONS on the Cook tab: what a prep day actually holds,
      GATHERED from work already on the plan (a cook-ahead batch whose
      source night is that day, a fridge move already dated there) plus
      the one new row type, prep_cut.

  C — the planner. Generation is untouched by this ticket, on purpose; no
      test here asserts anything about what a week gets planned.

The load-bearing claims, since a "gathering" is easy to quietly turn into
a generator: nothing here re-dates a defrost row, nothing here creates a
chain, and a session is empty (not fabricated) whenever the household has
not answered, has answered a day this period does not contain, or has
skipped prep for this one week.
"""
import datetime

import pytest

from app import db, households, tools
from app.tools._shared import DEFAULT_HOUSEHOLD_ID


def _monday() -> datetime.date:
    today = datetime.date.today()
    return today - datetime.timedelta(days=today.weekday())


def _day(offset: int) -> str:
    return (_monday() + datetime.timedelta(days=offset)).isoformat()


WEEK = _monday().isoformat()
MON, TUE, WED, THU, FRI, SAT = (_day(i) for i in range(6))
# The Sunday BEFORE this Monday — a prep week starts there (Emily's "some
# prep on Sunday to make the week easier"), so a Sunday-start period is
# what actually covers Mon/Tue/Wed.
SUN = _day(-1)


def _household(*names):
    for n in names or ("Alex", "Sam", "Rae"):
        tools.add_member(n)


def _eggs():
    tools.add_recipe(
        "Egg White Bites",
        ingredients=[{"item": "egg whites", "qty": "1 cup"}],
        default_servings=3,
        prep_time_minutes=10,
        cook_time_minutes=25,
    )


def _bowls():
    tools.add_recipe(
        "Chicken Bowls",
        ingredients=[
            {"item": "romaine", "qty": "1 head", "category": "produce"},
            {"item": "cherry tomatoes", "qty": "1 pint", "category": "produce"},
            {"item": "chicken thighs", "qty": "2 lb", "category": "meat/seafood"},
        ],
        default_servings=3,
    )


def _skip_flag(plan_id):
    conn = db.get_conn()
    row = conn.execute("SELECT skip_prep_this_week FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return row["skip_prep_this_week"]


# ---------- Slice A: the rhythm fact ----------

def test_prep_days_round_trip_through_the_rhythm_read():
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}, {"weekday": "wednesday"}])

    rhythm = tools.get_household_rhythm()

    assert rhythm["prep_days"] == [
        {"weekday": "sunday", "minutes": 60, "note": None},
        {"weekday": "wednesday", "minutes": None, "note": None},
    ]


def test_the_summary_says_it_the_way_a_person_would():
    """Emily's own example sentence, verbatim — if this copy is reworded,
    update it here in the same commit rather than deleting the test."""
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}, {"weekday": "wednesday"}])

    assert tools.get_household_rhythm()["prep_days_summary"] == "Preps on Sunday (about an hour) and Wednesday."


def test_an_unanswered_question_says_nothing_at_all():
    """Not "no prep days" — an unset rhythm fact reads as unset, the same
    rule planning_anchor_label follows."""
    rhythm = tools.get_household_rhythm()

    assert rhythm["prep_days"] == []
    assert rhythm["prep_days_summary"] == ""


def test_saying_you_do_not_prep_clears_the_answer():
    tools.set_prep_days([{"weekday": "sunday"}])

    tools.set_prep_days([])

    assert tools.get_household_rhythm()["prep_days"] == []
    assert tools.get_household_rhythm()["prep_days_summary"] == ""


def test_a_correction_replaces_rather_than_appends():
    tools.set_prep_days([{"weekday": "sunday"}])

    tools.set_prep_days([{"weekday": "saturday", "minutes": 30}])

    assert tools.get_household_rhythm()["prep_days"] == [
        {"weekday": "saturday", "minutes": 30, "note": None},
    ]
    assert tools.get_household_rhythm()["prep_days_summary"] == "Preps on Saturday (about half an hour)."


def test_more_than_two_prep_days_is_refused():
    with pytest.raises(ValueError):
        tools.set_prep_days([{"weekday": "sunday"}, {"weekday": "wednesday"}, {"weekday": "friday"}])


def test_a_weekday_that_is_not_a_weekday_is_refused():
    with pytest.raises(ValueError):
        tools.set_prep_days([{"weekday": "someday"}])


# ---------- Slice A: the routes and the agent's way in ----------

def test_the_onboarding_route_saves_prep_days(signed_in):
    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={"dinner_window": "6_8", "prep_days": [{"weekday": "sunday", "minutes": 60}]},
    )

    assert res.status_code == 200
    assert res.json()["prep_days"] == [{"weekday": "sunday", "minutes": 60, "note": None}]
    assert tools.get_household_rhythm()["prep_days"][0]["weekday"] == "sunday"


def test_a_rhythm_post_that_never_mentions_prep_days_leaves_them_alone(signed_in):
    """The route takes a partial body — What we know saves one fact per
    tap — so an absent key must not read as "we don't prep ahead"."""
    tools.set_prep_days([{"weekday": "sunday"}])

    signed_in.post("/api/onboarding/rhythm", json={"leftovers_stance": "love_them"})

    assert tools.get_household_rhythm()["prep_days"] == [{"weekday": "sunday", "minutes": None, "note": None}]


def test_what_we_know_reads_prep_days_back(signed_in):
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}, {"weekday": "wednesday"}])

    prefs = signed_in.get("/api/facts?category=rhythm").json()["preferences"]

    assert [d["weekday"] for d in prefs["prep_days"]] == ["sunday", "wednesday"]
    assert prefs["prep_days_summary"] == "Preps on Sunday (about an hour) and Wednesday."


def test_the_agent_can_set_prep_days():
    """"We prep on Saturdays now" — the same write onboarding makes, which
    is what makes a chat correction and an onboarding answer one fact."""
    from app import agent

    assert "set_prep_days" in agent.TOOL_FUNCTIONS
    agent.TOOL_FUNCTIONS["set_prep_days"](days=[{"weekday": "saturday", "minutes": 120}])

    assert tools.get_household_rhythm()["prep_days_summary"] == "Preps on Saturday (a longer stretch)."


def test_the_agent_tool_is_declared_with_the_this_week_only_argument():
    from app import agent

    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "set_prep_days")

    assert "this_week_only" in tool["input_schema"]["properties"]
    assert tool["input_schema"]["properties"]["days"]["items"]["properties"]["weekday"]["enum"][0] == "monday"


def test_a_one_off_skip_never_edits_the_standing_answer():
    """"I can't prep this Sunday" is true of this week, not of the
    household — so it flips a flag on the plan and leaves the fact."""
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}])
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]

    result = tools.set_prep_days(days=[], this_week_only=True)

    assert result["skip_prep_this_week"] is True
    assert result["weekly_plan_id"] == plan_id
    assert _skip_flag(plan_id) == 1
    assert tools.get_household_rhythm()["prep_days"] == [{"weekday": "sunday", "minutes": 60, "note": None}]


def test_a_one_off_can_be_taken_back():
    tools.set_prep_days([{"weekday": "sunday"}])
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.set_prep_days(days=[], this_week_only=True)

    tools.set_prep_days(days=[{"weekday": "sunday"}], this_week_only=True)

    assert _skip_flag(plan_id) == 0


def test_the_skip_migration_is_idempotent():
    """init_db runs on every startup; running it again must neither fail
    nor reset a flag a household already set."""
    tools.set_prep_days([{"weekday": "sunday"}])
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    tools.set_prep_days(days=[], this_week_only=True)

    db.init_db()
    db.init_db()

    assert _skip_flag(plan_id) == 1


# ---------- Slice B: the sessions ----------

def _sunday_start_plan():
    """A plan whose period starts on Sunday, so Sunday is a real day of it
    rather than the Monday week's last day."""
    plan_id = tools.create_weekly_plan(SUN)["weekly_plan_id"]
    return plan_id


def test_no_prep_days_means_no_sessions():
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]

    assert tools.prep_sessions_for_plan(plan_id) == []


def test_a_prep_day_outside_the_period_is_not_a_session():
    """A three-day plan that never reaches Sunday has no Sunday prep, and
    inventing one would be the app describing a day that isn't there."""
    tools.set_prep_days([{"weekday": "sunday"}])
    plan_id = tools.create_weekly_plan(WEEK)["weekly_plan_id"]
    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET content_start_date = ?, day_count = 3 WHERE id = ?", (MON, plan_id))
    conn.commit()
    conn.close()

    assert tools.prep_sessions_for_plan(plan_id) == []


def _emilys_sunday():
    """The reported shape: a batch cooked Sunday for three mornings, a
    fridge move already dated Sunday, and a raw component to cut."""
    _household()
    _eggs()
    _bowls()
    plan_id = _sunday_start_plan()
    ids = {}
    for d in (SUN, MON, TUE):
        ids[d] = tools.plan_meal(d, "Egg White Bites", slot="breakfast", weekly_plan_id=plan_id)["entry_id"]
    ids["bowls"] = tools.plan_meal(WED, "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.set_cook_ahead(ids[SUN], [ids[MON], ids[TUE]])
    # A defrost-shaped fridge move, written the way defrost.py writes one
    # (its own row, its own date) — this test never asks this module to
    # date it, only to notice it lands on the prep day.
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id, quantity) "
        "VALUES (?, ?, ?, 'Move the chicken thighs to the fridge', 'Chicken Bowls', 'pending', 'defrost', ?, '2 lb')",
        (DEFAULT_HOUSEHOLD_ID, plan_id, SUN, ids["bowls"]),
    )
    conn.commit()
    conn.close()
    tools.add_prep_cut(plan_id, SUN, "Cut up romaine", [ids["bowls"]])
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}])
    return plan_id, ids


def test_a_sunday_prep_gathers_all_three_kinds_of_work():
    plan_id, ids = _emilys_sunday()

    sessions = tools.prep_sessions_for_plan(plan_id)

    assert len(sessions) == 1
    session = sessions[0]
    assert session["date"] == SUN
    assert session["weekday"] == "Sunday"
    assert session["items_total"] == 3
    assert session["items_done"] == 0
    assert sorted(i["kind"] for i in session["items"]) == ["cook_ahead", "fridge_move", "prep_cut"]
    batch = next(i for i in session["items"] if i["kind"] == "cook_ahead")
    assert batch["title"] == "Egg White Bites for 3 mornings"
    assert batch["entry_id"] == ids[SUN]


def test_covers_is_computed_from_what_the_items_actually_feed():
    """Not "the prep day plus a window" — the chain's own target days and
    the meals the cut and the move are for."""
    plan_id, _ids = _emilys_sunday()

    session = tools.prep_sessions_for_plan(plan_id)[0]

    assert session["covers"] == [SUN, MON, TUE, WED]


def test_the_households_own_answer_beats_the_arithmetic():
    plan_id, _ids = _emilys_sunday()

    assert tools.prep_sessions_for_plan(plan_id)[0]["total_minutes_estimate"] == 60


def test_without_a_stated_length_the_items_are_added_up():
    """35 minutes of recipe (10 prep + 25 cook) + 10 for the cut + 2 for
    the fridge move."""
    plan_id, _ids = _emilys_sunday()
    tools.set_prep_days([{"weekday": "sunday"}])

    session = tools.prep_sessions_for_plan(plan_id)[0]

    assert session["minutes_planned"] is None
    assert session["total_minutes_estimate"] == 47


def test_the_done_count_moves_when_an_item_is_checked():
    plan_id, _ids = _emilys_sunday()
    cut = next(i for i in tools.prep_sessions_for_plan(plan_id)[0]["items"] if i["kind"] == "prep_cut")

    tools.check_off_prep_step(cut["prep_task_id"], "done")

    session = tools.prep_sessions_for_plan(plan_id)[0]
    assert session["items_done"] == 1
    assert session["items_total"] == 3
    assert next(i for i in session["items"] if i["kind"] == "prep_cut")["done"] is True


def test_a_batch_is_done_once_it_has_been_cooked():
    """The cook-ahead item has no box of its own — cooking it IS checking
    it off, so its state is the entry's cooked_status and never a second
    copy of that fact."""
    plan_id, ids = _emilys_sunday()

    tools.check_off_meal(ids[SUN], "done")

    batch = next(i for i in tools.prep_sessions_for_plan(plan_id)[0]["items"] if i["kind"] == "cook_ahead")
    assert batch["done"] is True
    assert batch["prep_task_id"] is None


def test_the_defrost_row_is_listed_where_defrost_dated_it():
    """The whole point of gathering rather than generating: this module
    must never move a food-safety date to suit a prep day."""
    plan_id, _ids = _emilys_sunday()
    before = [(t["id"], t["task_date"]) for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]

    tools.prep_sessions_for_plan(plan_id)

    after = [(t["id"], t["task_date"]) for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "defrost"]
    assert before == after


def test_skipping_prep_this_week_hides_that_plans_sessions_only():
    plan_id, _ids = _emilys_sunday()
    other = tools.create_weekly_plan((_monday() + datetime.timedelta(days=7)).isoformat())["weekly_plan_id"]
    conn = db.get_conn()
    conn.execute("UPDATE weekly_plans SET content_start_date = ?, day_count = 7 WHERE id = ?",
                 ((_monday() + datetime.timedelta(days=7)).isoformat(), other))
    conn.commit()
    conn.close()
    tools.add_prep_cut(other, (_monday() + datetime.timedelta(days=13)).isoformat(), "Cut up peppers", [])

    tools.set_skip_prep_this_week(True, plan_id)

    assert tools.prep_sessions_for_plan(plan_id) == []
    assert len(tools.prep_sessions_for_plan(other)) == 1


# ---------- Slice B: prep-cuts ----------

def test_a_prep_cut_is_a_prep_task_row_with_its_own_type():
    _household()
    _bowls()
    plan_id = _sunday_start_plan()
    entry_id = tools.plan_meal(WED, "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]

    tools.add_prep_cut(plan_id, SUN, "Cut up romaine", [entry_id])

    task = [t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "prep_cut"][0]
    assert task["task_date"] == SUN
    assert task["description"] == "Cut up romaine"
    assert task["related_meal"] == "Chicken Bowls"
    assert task["meal_plan_entry_id"] == entry_id
    assert task["status"] == "pending"


def test_adding_the_same_cut_twice_does_not_duplicate_or_untick_it():
    _household()
    _bowls()
    plan_id = _sunday_start_plan()
    entry_id = tools.plan_meal(WED, "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    first = tools.add_prep_cut(plan_id, SUN, "Cut up romaine", [entry_id])
    tools.check_off_prep_step(first["tasks"][0]["prep_task_id"], "done")

    again = tools.add_prep_cut(plan_id, SUN, "Cut up romaine", [entry_id])

    assert again["added"] == 0
    tasks = [t for t in tools.get_prep_schedule(plan_id) if t["task_type"] == "prep_cut"]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"


def test_the_prep_cut_route_answers_with_the_refreshed_cook_view(signed_in):
    _household()
    _bowls()
    plan_id = _sunday_start_plan()
    entry_id = tools.plan_meal(WED, "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)["entry_id"]
    tools.set_prep_days([{"weekday": "sunday"}])

    res = signed_in.post("/api/prep-cut", json={
        "prep_date": SUN, "description": "Cut up romaine", "entry_ids": [entry_id], "weekly_plan_id": plan_id,
    })

    assert res.status_code == 200
    body = res.json()
    assert body["weekly_plan_id"] == plan_id
    assert body["prep_sessions"][0]["items"][0]["title"] == "Cut up romaine"


def test_the_sessions_route_serves_the_week(signed_in):
    plan_id, _ids = _emilys_sunday()

    res = signed_in.get(f"/api/week/{SUN}/prep-sessions")

    assert res.status_code == 200
    assert res.json()["weekly_plan_id"] == plan_id
    assert res.json()["sessions"][0]["items_total"] == 3


def test_the_sessions_route_404s_for_a_week_with_no_plan(signed_in):
    assert signed_in.get("/api/week/2099-01-04/prep-sessions").status_code == 404


# ---------- The cooker payload ----------

def test_the_cook_view_carries_the_sessions_and_whether_days_were_ever_set():
    plan_id, _ids = _emilys_sunday()

    view = tools.get_cooker_view(plan_id)

    assert view["prep_days_set"] is True
    assert len(view["prep_sessions"]) == 1
    # Additive: nothing already on this payload changed shape.
    assert view["prep_total"] == len(view["prep_tasks"])


def test_a_household_that_never_answered_is_flagged_as_such():
    _household()
    _bowls()
    plan_id = _sunday_start_plan()
    tools.plan_meal(WED, "Chicken Bowls", slot="dinner", weekly_plan_id=plan_id)

    view = tools.get_cooker_view(plan_id)

    assert view["prep_days_set"] is False
    assert view["prep_sessions"] == []


# ---------- Household scoping ----------

def test_prep_days_and_sessions_stay_inside_one_household():
    beta = households.create_household("The Beta Testers", "beta-prep-passphrase")
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}])
    plan_id, _ids = _emilys_sunday()

    with tools.use_household(beta):
        assert tools.get_household_rhythm()["prep_days"] == []
        assert tools.prep_sessions_for_plan(plan_id) == []
        assert tools.has_prep_days() is False
        with pytest.raises(ValueError):
            tools.add_prep_cut(plan_id, SUN, "Cut up romaine", [])

    assert len(tools.prep_sessions_for_plan(plan_id)) == 1


# ---------- The Cook view's own source (no JS harness in this repo) ----------
# Source-level assertions, the same workaround test_cooker_today.py and
# test_frontend_restored_2026_09_08.py use: shell.js cannot be exercised
# from pytest, and a marker that is present but mis-wired is still a much
# better failure mode than a marker that is gone. Where a string below is
# user-facing copy it is asserted verbatim — reword it here in the same
# commit if the copy changes, rather than deleting the test.

import pathlib  # noqa: E402

SHELL_JS = (pathlib.Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")


def test_the_cook_view_renders_the_sessions_card_from_the_payload():
    """
    UPDATED 2026-09-08 (flows-4-kitchen-and-preferences), deliberately: the
    Cook OVERVIEW this used to describe is the Kitchen root now, and the
    "Prep schedule" rail the sessions card used to sit above (cookPrepHtml)
    came out with it — a fridge move or a prep task due today is a line on
    Today's timeline, and the rows that belong to the meal being cooked are
    on the focused screen. The sessions card itself moved unchanged, and it
    still sits between what is cooking today and the rest of the week,
    which is what this assertion was really pinning.
    """
    assert "function cookPrepSessionsHtml(data)" in SHELL_JS
    assert "data.prep_sessions || []" in SHELL_JS
    assert (
        "kitchenCookingTodayHtml(rows) +\n      cookPrepSessionsHtml(data) +\n"
        "      cookRestOfWeekHtml(" in SHELL_JS
    )


def test_the_quiet_offer_when_no_prep_days_are_set():
    assert "Prep ahead? " in SHELL_JS
    assert "Tell Pomona which days you prep" in SHELL_JS
    # It only shows for a household that never answered — see
    # prep_days_set on the cooker payload.
    assert "if (data.prep_days_set) return '';" in SHELL_JS


def test_the_session_focus_screen_exists_and_can_be_left():
    assert "function cookSessionHtml(data, session, meals)" in SHELL_JS
    assert "data-cook=\"exit-session\"" in SHELL_JS
    assert "if (what === 'session') return cookEnterSession(" in SHELL_JS


def test_prep_cuts_are_not_shown_twice_on_the_cook_screen():
    """They belong to their session; every other prep list leaves them out.

    UPDATED 2026-09-08 (flows-4-kitchen-and-preferences): the exclusion used
    to live in the Cook overview's prep rail. That rail is gone, so the same
    line now lives in cookFocusPrepTasks — the focused screen's own list,
    which is the one that could otherwise repeat a prep-cut the session
    already holds (a prep_cut row carries the entry it feeds).
    """
    assert "return t.task_type !== 'prep_cut';" in SHELL_JS
    assert "function cookFocusPrepTasks(" in SHELL_JS


def test_the_cook_view_never_touches_todays_panel():
    """The Today panel was being rebuilt on another branch while this one
    was written — this branch's Cook work must not have reached into it."""
    assert "function buildTodayPanel" in SHELL_JS  # still there, untouched
    assert "prep_sessions" not in SHELL_JS.split("function buildTodayPanel")[1].split("\n  function ")[0]


def test_a_prep_cut_naming_only_stray_entries_is_dropped_not_filed_unattached():
    """Every id was stray (the plan moved under the screen): drop the tick,
    never file an unattached cut nobody asked for."""
    _household()
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}])
    plan_id, _ids = _emilys_sunday()
    res = tools.add_prep_cut(plan_id, SUN, "Cut up kale", [999999])
    assert res["added"] is False and res.get("dropped") is True
    items = [i for s_ in tools.prep_sessions_for_plan(plan_id) for i in s_["items"]]
    assert not any("kale" in (i.get("title") or i.get("description") or "").lower() for i in items)


def test_skipping_prep_on_someone_elses_plan_reports_no_change():
    """The UPDATE is household-scoped; the return must not claim a write."""
    _household()
    beta = households.create_household("The Beta Testers", "beta-prep-passphrase")
    tools.set_prep_days([{"weekday": "sunday", "minutes": 60}])
    plan_id, _ids = _emilys_sunday()
    with tools.use_household(beta):
        res = tools.set_skip_prep_this_week(True, weekly_plan_id=plan_id)
    assert res["changed"] is False and res["skip_prep_this_week"] is None
    assert len(tools.prep_sessions_for_plan(plan_id)) == 1
