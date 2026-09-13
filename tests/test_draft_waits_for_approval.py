"""
A draft leaves the approved week alone until it's approved — Loop Board
2026-09-13 (Emily: "yes, make the draft wait until approval").

Before: generating a draft over an approved week took those days off the
approved week — meals gone, groceries reversed — the moment the draft
existed. Abandon the draft and the week you had approved was already
shorter. Now:

1. Generating the draft changes nothing about the approved week: every
   meal, every grocery line, on every screen that follows the real week
   (Now, Cook, the list). Only the Plan tab leads with the draft.
2. Approving the draft is the takeover: the overlapping days come off the
   approved week, their groceries with them, in the same step that puts
   the draft's groceries on — and anything already bought stays.
3. Walking away — another draft, or the draft expiring — leaves the
   approved week exactly as it was.
4. A draft over another DRAFT is still replaced at once, as before.
"""
import datetime

import pytest

from app import agent, tools
from app.db import get_conn
from app.tools import tonight as _tonight
from app.tools import weekly_plan as _weekly_plan
from tests.test_planning_periods import _dates_on, _full_period, _monday, _plan_row, recipes, stub_model  # noqa: F401


def _needed() -> dict:
    return {i["item"]: i["quantity"] for i in tools.list_grocery_list(status="needed")}


def _approved_week(stub_model, meal="Chili"):
    week = _monday()
    stub_model(_full_period(week, 7, meal=meal))
    plan = agent.generate_weekly_plan(week, day_count=7, period_start=week)
    tools.approve_weekly_plan(plan["weekly_plan_id"], approved_by="Emily")
    return week, plan["weekly_plan_id"]


def _draft_over(stub_model, start, days, meal="Katsu"):
    stub_model(_full_period(start, days, meal=meal))
    return agent.generate_weekly_plan(start, day_count=days, period_start=start, confirm_takeover=True)


class TestTheDraftChangesNothing:
    def test_the_approved_week_keeps_every_meal_and_every_grocery_line(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        before_meals = _dates_on(approved)
        before_list = _needed()
        assert "beans" in before_list

        thursday = tools.period_dates(week, 7)[3]
        draft = _draft_over(stub_model, thursday, 4)

        assert draft["status"] != "needs_confirmation"
        assert draft["took_over"]["shortened_plan_ids"] == []
        assert draft["took_over"]["retired_plan_ids"] == []
        assert _dates_on(approved) == before_meals
        assert tools.plan_period(_plan_row(approved)) == (week, 7)
        assert _plan_row(approved)["status"] == "approved"
        assert _needed() == before_list, "nothing of the draft's reached the list, nothing of the week's left it"

    def test_now_cook_and_the_day_resolver_keep_following_the_approved_week(self, recipes, stub_model, monkeypatch):
        week, approved = _approved_week(stub_model)
        thursday = tools.period_dates(week, 7)[3]
        draft = _draft_over(stub_model, thursday, 4)

        # Every "which plan owns this day" answer is the approved week.
        for day in tools.period_dates(thursday, 4):
            assert tools.get_plan_id_for_date(day) == approved
        conn = get_conn()
        covering = _weekly_plan._live_plan_covering(conn, thursday)
        conn.close()
        assert covering["id"] == approved

        # And "the current plan" — Cook, defrost, prep, the list's stale
        # sweep — on a day both cover.
        class _Thursday(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(thursday)
        monkeypatch.setattr(_weekly_plan, "date", _Thursday)
        conn = get_conn()
        current = _weekly_plan._current_weekly_plan_row(conn)
        conn.close()
        assert current["id"] == approved

    def test_the_plan_tab_leads_with_the_draft(self, recipes, stub_model, monkeypatch):
        week, approved = _approved_week(stub_model)
        thursday = tools.period_dates(week, 7)[3]
        draft = _draft_over(stub_model, thursday, 4)

        class _Thursday(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(thursday)
        monkeypatch.setattr(_weekly_plan, "date", _Thursday)

        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == draft["weekly_plan_id"]
        assert menu["status"] == "draft"
        # ...and says what approving it would replace, in the new tense.
        replaces = menu["replaces"]
        assert replaces is not None
        assert replaces["approved_plan_ids"] == [approved]
        assert replaces["note"].startswith("Once it's approved, I'd replace")
        # A pinned read is left alone.
        assert tools.get_week_menu(approved)["weekly_plan_id"] == approved
        assert tools.get_week_menu(approved)["replaces"] is None

    def test_the_confirm_question_is_still_asked_and_reads_as_a_promise_not_an_act(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        thursday = tools.period_dates(week, 7)[3]
        stub_model(_full_period(thursday, 4, meal="Katsu"))
        asked = agent.generate_weekly_plan(thursday, day_count=4, period_start=thursday)
        assert asked["status"] == "needs_confirmation"
        assert asked["note"].startswith("Once it's approved, I'd replace")
        assert asked["note"].endswith("Go ahead?")


class TestApprovingIsTheTakeover:
    def test_approving_the_draft_moves_the_days_and_their_groceries(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)

        result = tools.approve_weekly_plan(draft["weekly_plan_id"], approved_by="Emily")

        took = result["took_over"]
        assert took["shortened_plan_ids"] == [approved]
        assert took["surrendered_dates"] == days[3:7]
        assert tools.plan_period(_plan_row(approved)) == (week, 3)
        assert _dates_on(approved) == set(days[:3])
        assert _dates_on(draft["weekly_plan_id"]) == set(days[3:7])
        # The list holds three days of Chili and four of Katsu — never both
        # weeks' food for one night.
        needed = _needed()
        assert "panko" in needed
        assert "beans" in needed
        assert took["grocery_trimmed"] or took["grocery_removed"]

    def test_a_line_already_bought_is_left_alone(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        beans = next(i for i in tools.list_grocery_list(status="needed") if i["item"] == "beans")
        tools.mark_grocery_item(beans["id"], "purchased")
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)

        result = tools.approve_weekly_plan(draft["weekly_plan_id"], approved_by="Emily")

        conn = get_conn()
        row = conn.execute("SELECT status FROM grocery_items WHERE id = ?", (beans["id"],)).fetchone()
        conn.close()
        assert row["status"] == "purchased"
        assert "beans" in result["took_over"]["grocery_kept_bought"]

    def test_a_re_approval_takes_nothing_twice(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)
        tools.approve_weekly_plan(draft["weekly_plan_id"], approved_by="Emily")
        again = tools.approve_weekly_plan(draft["weekly_plan_id"], approved_by="Emily")
        assert again["was_already_approved"] is True
        assert tools.plan_period(_plan_row(approved)) == (week, 3)

    def test_approving_opens_exactly_one_connection_still(self, recipes, stub_model, monkeypatch):
        # The takeover now runs inside the approval's transaction; it must
        # read and write on that one connection (test_approve_race.py pins
        # the same for the ingest).
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)

        opened = {"n": 0}
        real = _weekly_plan.get_conn

        def counting():
            opened["n"] += 1
            return real()
        monkeypatch.setattr(_weekly_plan, "get_conn", counting)
        _weekly_plan._settle_weekly_plan_approval(draft["weekly_plan_id"], "Emily", None, [], None)
        assert opened["n"] == 1


class TestWalkingAway:
    def test_another_draft_replaces_the_first_and_the_approved_week_is_still_whole(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        first = _draft_over(stub_model, days[3], 4)
        second = _draft_over(stub_model, days[2], 5, meal="Katsu")

        # Draft over draft: replaced at once, as before.
        assert second["took_over"]["retired_plan_ids"] == [first["weekly_plan_id"]]
        assert _plan_row(first["weekly_plan_id"])["status"] == "retired"
        # Approved week: untouched by either.
        assert tools.plan_period(_plan_row(approved)) == (week, 7)
        assert _dates_on(approved) == set(days)
        assert "beans" in _needed()
        assert "panko" not in _needed()

    def test_an_expired_draft_never_touched_the_approved_week(self, recipes, stub_model, monkeypatch):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)

        after = (datetime.date.fromisoformat(days[6]) + datetime.timedelta(days=1)).isoformat()
        retired = tools.retire_expired_drafts(today=after)
        assert retired == [draft["weekly_plan_id"]]
        assert tools.plan_period(_plan_row(approved)) == (week, 7)
        assert _dates_on(approved) == set(days)
        assert "beans" in _needed()


class TestTonight:
    def test_tonight_reads_the_approved_week_not_the_draft(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        _draft_over(stub_model, days[4], 3)
        # Friday evening, 6pm, on Now: the dinner named is the approved
        # week's Chili, not the draft's Katsu.
        friday_six = datetime.datetime.fromisoformat(days[4] + "T18:00:00")
        out = _tonight.tonight_check(now=friday_six)
        assert out["dinner"] is not None
        assert out["dinner"]["meal"] == "Chili"
