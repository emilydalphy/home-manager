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


class TestNowAndAwayFollowTheApprovedWeek:
    """Found by the verification pass on the first build: three surfaces
    read meal_plan_entries by date with no plan filter, and picked the
    draft's row (newest) over the approved week's."""

    def _today_over(self, stub_model):
        # An approved week that covers TODAY, and a draft over today too —
        # the case Now, "away" and the chat's upcoming list all have to get
        # right. Generated on the real date so get_needs_you_items (which
        # reads the clock itself) sees them.
        today = datetime.date.today()
        start = (today - datetime.timedelta(days=1)).isoformat()
        stub_model(_full_period(start, 4, meal="Chili"))
        approved = agent.generate_weekly_plan(start, day_count=4, period_start=start)["weekly_plan_id"]
        tools.approve_weekly_plan(approved, approved_by="Emily")
        draft = _draft_over(stub_model, today.isoformat(), 3)["weekly_plan_id"]
        return today.isoformat(), approved, draft

    @staticmethod
    def _open(plan_id, day, reason):
        # plan_slot_open is a raw insert (drop_dish_from_day clears first);
        # do the same, so the slot has one row.
        tools.clear_plan_slot(plan_id, day, "dinner")
        tools.plan_slot_open(plan_id, day, "dinner", reason)

    def test_nows_dinner_card_is_the_approved_weeks_not_the_drafts(self, recipes, stub_model):
        today, approved, draft = self._today_over(stub_model)
        # The approved week's tonight is open; the draft's tonight is planned.
        self._open(approved, today, "Deciding nearer the time.")
        cards = [i for i in tools.get_needs_you_items() if i["type"] == "dinner_open"]
        assert len(cards) == 1 and cards[0]["date"] == today
        assert cards[0]["weekly_plan_id"] == approved

        # And the other way round: an open night on the DRAFT is not
        # tonight's decision.
        tools.resolve_open_slot(approved, today, "dinner", "Chili")
        self._open(draft, today, "Still thinking.")
        assert [i for i in tools.get_needs_you_items() if i["type"] == "dinner_open"] == []

    def test_the_pick_from_now_lands_on_the_approved_week(self, recipes, stub_model, signed_in):
        today, approved, draft = self._today_over(stub_model)
        self._open(approved, today, "Deciding nearer the time.")
        card = next(i for i in tools.get_needs_you_items() if i["type"] == "dinner_open")
        res = signed_in.post(
            f"/api/week/{card['week_start']}/slot",
            json={"date": today, "slot": "dinner", "choice": "Tacos", "weekly_plan_id": card["weekly_plan_id"]},
        )
        assert res.status_code == 200, res.text
        approved_tonight = [m for m in tools.get_weekly_plan(approved)["meals"] if m["date"] == today and m["slot"] == "dinner"]
        draft_tonight = [m for m in tools.get_weekly_plan(draft)["meals"] if m["date"] == today and m["slot"] == "dinner"]
        assert [m["meal"] for m in approved_tonight] == ["Tacos"]
        assert [m["meal"] for m in draft_tonight] == ["Katsu"]

    def test_away_empties_the_approved_week_not_the_draft(self, recipes, stub_model):
        today, approved, draft = self._today_over(stub_model)
        tomorrow = (datetime.date.fromisoformat(today) + datetime.timedelta(days=1)).isoformat()
        tools.set_away_stretch(tomorrow, "breakfast", tomorrow, "dinner", reason="trip")
        approved_rows = [m for m in tools.get_weekly_plan(approved)["meals"] if m["date"] == tomorrow]
        draft_rows = [m for m in tools.get_weekly_plan(draft)["meals"] if m["date"] == tomorrow]
        assert approved_rows and all(m["slot_state"] == "planned_empty" for m in approved_rows)
        assert draft_rows and all(m["slot_state"] == "planned" for m in draft_rows)

    def test_the_chats_upcoming_list_shows_one_plans_meals_per_day(self, recipes, stub_model):
        today, approved, draft = self._today_over(stub_model)
        upcoming = tools.get_meal_plan(days_ahead=3)
        by_day = {}
        for m in upcoming:
            by_day.setdefault(m["date"], set()).add(m["meal"])
        assert by_day[today] == {"Chili"}
        assert all(meals == {"Chili"} for meals in by_day.values())


class TestDroppingADraft:
    """Loop Board 2026-09-13, "Drop this draft puts the approved week back".

    A draft you have decided against had no door out but approving it or
    drafting something else over it, so it stayed the Plan tab's front page
    until its last day had passed. Dropping it is exactly what the expiry
    sweep does — retired, reason 'discarded', meals and answers kept — said
    out loud instead of waited out.
    """

    def test_dropping_a_draft_leaves_the_approved_week_and_the_list_whole(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        before_meals = _dates_on(approved)
        before_list = _needed()
        draft = _draft_over(stub_model, days[3], 4)

        out = tools.discard_draft_plan(draft["weekly_plan_id"])

        assert out["status"] == "retired"
        assert out["was_already_retired"] is False
        row = _plan_row(draft["weekly_plan_id"])
        assert row["status"] == "retired"
        assert row["retired_reason"] == "discarded"
        # Kept on record, not deleted — the same "don't lead with it" an
        # expired draft gets.
        assert _dates_on(draft["weekly_plan_id"]) == set(days[3:7])
        assert tools.plan_period(row) == (days[3], 4)
        # And nothing of the approved week's moved.
        assert tools.plan_period(_plan_row(approved)) == (week, 7)
        assert _dates_on(approved) == before_meals
        assert _plan_row(approved)["status"] == "approved"
        assert _needed() == before_list

    def test_the_toast_can_name_the_week_that_is_still_theirs(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)
        out = tools.discard_draft_plan(draft["weekly_plan_id"])
        assert out["approved_week_label"] == _weekly_plan._format_period_range(week, 7)
        assert out["week_label"] == _weekly_plan._format_period_range(days[3], 4)

    def test_a_lone_draft_drops_to_the_empty_state(self, recipes, stub_model):
        week = _monday()
        stub_model(_full_period(week, 7, meal="Katsu"))
        draft = agent.generate_weekly_plan(week, day_count=7, period_start=week)

        out = tools.discard_draft_plan(draft["weekly_plan_id"])

        # No approved week underneath, so nothing to name.
        assert out["approved_week_label"] is None
        assert tools.get_week_menu()["weekly_plan_id"] is None
        assert _needed() == {}

    def test_the_plan_tab_falls_back_to_the_approved_week(self, recipes, stub_model, monkeypatch):
        week, approved = _approved_week(stub_model)
        thursday = tools.period_dates(week, 7)[3]
        draft = _draft_over(stub_model, thursday, 4)

        class _Thursday(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(thursday)
        monkeypatch.setattr(_weekly_plan, "date", _Thursday)

        assert tools.get_week_menu()["weekly_plan_id"] == draft["weekly_plan_id"]
        tools.discard_draft_plan(draft["weekly_plan_id"])
        menu = tools.get_week_menu()
        assert menu["weekly_plan_id"] == approved
        assert menu["status"] == "approved"

    def test_a_dropped_draft_is_never_the_front_page_again(self, recipes, stub_model, monkeypatch):
        """Every "which plan is this" answer refuses a retired plan already;
        this pins that the discarded reason is no exception to it."""
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)
        draft_id = draft["weekly_plan_id"]
        tools.discard_draft_plan(draft_id)

        class _Thursday(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(days[3])
        monkeypatch.setattr(_weekly_plan, "date", _Thursday)

        conn = get_conn()
        assert _weekly_plan._current_weekly_plan_row(conn)["id"] == approved
        conn.close()
        assert tools.get_plan_id_for_week(days[3]) != draft_id
        assert tools.get_plan_id_for_date(days[4]) == approved
        assert _weekly_plan._pending_draft_over(tools.get_weekly_plan(approved)) is None
        # The expiry sweep has nothing left to do with it — it only reads
        # drafts, so a dropped one can't be retired a second time.
        after = (datetime.date.fromisoformat(days[6]) + datetime.timedelta(days=1)).isoformat()
        assert draft_id not in tools.retire_expired_drafts(today=after)
        assert _plan_row(draft_id)["retired_reason"] == "discarded"

    def test_dropping_an_approved_plan_is_refused(self, recipes, stub_model):
        """A SlotRefused, not a bare ValueError: the sentence is written for
        the household, and the route answers it as a 200 that says no rather
        than as a failure — reachable whenever the other adult approves
        while this screen sits open."""
        week, approved = _approved_week(stub_model)
        with pytest.raises(tools.SlotRefused, match="reopen it or re-plan it"):
            tools.discard_draft_plan(approved)
        assert _plan_row(approved)["status"] == "approved"
        assert "beans" in _needed()

    def test_a_draft_over_two_approved_weeks_names_the_one_they_are_in(self, recipes, stub_model, monkeypatch):
        """A draft can straddle two approved weeks ("Pick my own days"), and
        naming the later one tells a household living in this week that next
        week is still theirs — true, and not the answer to what they asked."""
        week, first = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        nxt = tools.period_dates(week, 14)[7]
        stub_model(_full_period(nxt, 7, meal="Chili"))
        second = agent.generate_weekly_plan(nxt, day_count=7, period_start=nxt, confirm_takeover=True)["weekly_plan_id"]
        tools.approve_weekly_plan(second, approved_by="Emily")
        # Friday of week one through Tuesday of week two.
        draft = _draft_over(stub_model, days[5], 5)

        class _Monday(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(days[0])
        monkeypatch.setattr(_weekly_plan, "date", _Monday)

        out = tools.discard_draft_plan(draft["weekly_plan_id"])
        assert out["approved_week_label"] == _weekly_plan._format_period_range(week, 7)

    def test_a_draft_wholly_ahead_names_the_nearer_week(self, recipes, stub_model, monkeypatch):
        """Nothing covers today, so there is no week they are "in" — the
        earliest of the two is the one they reach first."""
        week, first = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        nxt = tools.period_dates(week, 14)[7]
        stub_model(_full_period(nxt, 7, meal="Chili"))
        second = agent.generate_weekly_plan(nxt, day_count=7, period_start=nxt, confirm_takeover=True)["weekly_plan_id"]
        tools.approve_weekly_plan(second, approved_by="Emily")
        draft = _draft_over(stub_model, days[5], 5)

        before = (datetime.date.fromisoformat(week) - datetime.timedelta(days=3)).isoformat()

        class _Earlier(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date.fromisoformat(before)
        monkeypatch.setattr(_weekly_plan, "date", _Earlier)

        out = tools.discard_draft_plan(draft["weekly_plan_id"])
        assert out["approved_week_label"] == _weekly_plan._format_period_range(week, 7)

    def test_dropping_twice_is_a_no_op(self, recipes, stub_model):
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)
        tools.discard_draft_plan(draft["weekly_plan_id"])
        again = tools.discard_draft_plan(draft["weekly_plan_id"])
        assert again["was_already_retired"] is True
        assert again["status"] == "retired"
        assert _plan_row(draft["weekly_plan_id"])["retired_reason"] == "discarded"
        assert _dates_on(approved) == set(days)

    def test_a_plan_this_household_does_not_have_is_not_droppable(self, recipes, stub_model):
        """The lookup is household-scoped, so an id from somewhere else is
        the same answer as an id that doesn't exist — and the route turns
        that into a 400 rather than retiring somebody else's week."""
        week, approved = _approved_week(stub_model)
        with pytest.raises(ValueError, match="No weekly plan with id"):
            tools.discard_draft_plan(approved + 9999)

    def test_the_route_drops_the_draft_the_screen_names(self, recipes, stub_model, signed_in):
        """The week key resolves to whatever is newest under it; the Plan tab
        knows which draft it is showing, and the body's id wins."""
        week, approved = _approved_week(stub_model)
        days = tools.period_dates(week, 7)
        draft = _draft_over(stub_model, days[3], 4)

        res = signed_in.post(
            f"/api/week/{days[3]}/discard", json={"weekly_plan_id": draft["weekly_plan_id"]}
        )
        assert res.status_code == 200, res.text
        assert res.json()["approved_week_label"] == _weekly_plan._format_period_range(week, 7)
        assert _plan_row(draft["weekly_plan_id"])["status"] == "retired"
        assert _dates_on(approved) == set(days)

    def test_the_route_refuses_an_approved_week_as_a_200_that_says_no(self, recipes, stub_model, signed_in):
        """The shape add_dish_day and the chore rows already answer a
        refusal in, so the screen shows the server's sentence rather than
        reporting an app that did the right thing as broken."""
        week, approved = _approved_week(stub_model)
        res = signed_in.post(f"/api/week/{week}/discard", json={"weekly_plan_id": approved})
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "refused"
        assert "reopen it or re-plan it" in res.json()["message"]
        assert _plan_row(approved)["status"] == "approved"

    def test_an_id_this_household_does_not_have_takes_the_plain_line(self, recipes, stub_model, signed_in):
        """An id is not a sentence — it stays a 400, and the screen says its
        own calm line rather than printing a row number into somebody's
        week (CLAUDE.md, 2026-09-11)."""
        week, approved = _approved_week(stub_model)
        res = signed_in.post(f"/api/week/{week}/discard", json={"weekly_plan_id": approved + 9999})
        assert res.status_code == 400
        assert "No weekly plan with id" in res.json()["detail"]

    def test_the_route_falls_back_to_the_week_key(self, recipes, stub_model, signed_in):
        week = _monday()
        stub_model(_full_period(week, 7, meal="Katsu"))
        draft = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        res = signed_in.post(f"/api/week/{week}/discard", json={})
        assert res.status_code == 200, res.text
        assert res.json()["weekly_plan_id"] == draft["weekly_plan_id"]

    def test_chat_can_drop_a_draft_and_never_an_approved_week(self, recipes, stub_model):
        from app import main as _main
        assert agent.TOOL_FUNCTIONS["discard_draft_plan"] is tools.discard_draft_plan
        spec = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "discard_draft_plan")
        assert "never on your own initiative" in spec["description"]
        assert "reopen_weekly_plan" in spec["description"]
        # Tagged `week`, so the Plan tab refreshes after a chat drop.
        assert "discard_draft_plan" in _main._WEEK_TOOLS


def test_the_more_sheet_offers_the_row_on_a_draft_only():
    """Source markers for the Plan tab's More sheet — the row, its two
    sub-lines and the confirm it goes through."""
    import pathlib
    shell = (pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text()
    sheet = shell.split("function renderMealsMoreSheet()", 1)[1].split("\n  function ", 1)[0]
    assert "'wk-more-discard', 'Drop this draft'" in sheet
    assert "Your approved week stays as it is" in sheet
    assert "Nothing's on your list from it" in sheet
    # In the draft-only block, beside Try again / Change my answers — an
    # approved week never gets the row.
    draft_block = sheet.split("hasPlan && data.status !== 'approved'", 1)[1].split(": '') +", 1)[0]
    assert "wk-more-discard" in draft_block
    assert "discardDraft(panel, data)" in sheet
    # Asked once before anything is retired.
    drop = shell.split("async function discardDraft(", 1)[1].split("\n  function ", 1)[0]
    assert "await askAboutDroppingDraft(label)" in drop
    assert "'/discard'" in drop
    assert "Dropped. ' + out.approved_week_label + ' is still your week." in drop
    # A refusal is the server's sentence, never the generic line.
    assert "out.status === 'refused'" in drop
    assert "showToast(out.message || DISCARD_TROUBLE)" in drop


def test_the_confirm_is_a_real_dialog_and_not_just_the_insides():
    """The container rules in shell.css are ID-scoped — reusing
    .reset-title / .reset-actions gets the insides and none of the box. The
    first cut of this shipped without them: it rendered in document flow
    under the tab bar, with no scrim, so the week behind it stayed live and
    the screen had two apricot fills on it at once."""
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.css").read_text()
    html = (pathlib.Path(__file__).resolve().parents[1] / "static" / "shell.html").read_text()
    for anchor in (
        "#reset-scrim, #dinner-confirm-scrim, #approve-who-scrim, #discard-draft-scrim {\n  position: fixed;",
        "#reset-dialog, #dinner-confirm-dialog, #approve-who-dialog, #discard-draft-dialog {\n  position: fixed;",
        "#discard-draft-scrim[hidden], #discard-draft-dialog[hidden] { display: none; }",
        "#reset-scrim, #dinner-confirm-scrim, #approve-who-scrim, #discard-draft-scrim {\n  animation: none;",
        "#discard-draft-scrim.is-open {",
    ):
        assert anchor in css, anchor
    # And the box's own fade/scale, which is attribute-driven.
    assert 'id="discard-draft-dialog" hidden data-motion="dialog"' in html
