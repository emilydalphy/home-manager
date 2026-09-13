"""
A dinner answered on Now, when no plan covers today, has to show up
somewhere a person can see it.

Bug, 2026-09-13, on the beta tester's first evening. A brand-new household
has no weekly plan at all, so Now offers "Tonight needs a dinner". Answering
it really did save the meal — and then nothing showed it: no move on Now, no
start-by time, no tick, no cook mode, nothing in the morning text. The card
disappeared (so it read as accepted) and Now fell straight back to "Quiet
day. Want me to sort dinner, or the whole week?" — offering to sort the
dinner it had just been given.

Root cause, and it is one step behind the obvious one:
resolve_needs_you_dinner writes the entry with weekly_plan_id = NULL when
the current plan's period doesn't cover the date, which is CORRECT and is
the deliberate 2026-09-11 fix for a 500 on that same tap. What was wrong is
that get_cooker_view — the read every cooking surface is built on (moves.py,
cook mode, digest.build_morning_text) — was strictly plan-scoped, so an
unlinked entry was invisible to all of them at once.

The 2026-09-11 entry's own claim, "the unlinked meal is visible tonight, so
this isn't a 500 traded for a silent loss", was true of get_meal_plan and
false of every screen. Its two tests only asked get_meal_plan, which is
exactly how it survived; they are widened in test_tools.py alongside this.

Every test here that names a screen fails on 0d359e5.
"""
import datetime

import pytest

from app import households, tools
from app.tools import digest


def _d(offset_days: int = 0) -> str:
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).isoformat()


def _a_recipe(name: str = "Chili"):
    tools.add_recipe(
        name,
        ingredients=[{"item": "beans", "qty": "1 tin"}],
        instructions=["Cook it."],
        prep_time_minutes=10,
        cook_time_minutes=20,
    )


def _cooks_today():
    return [m for m in tools.today_moves()["moves"] if m["kind"] == "cook"]


class TestABrandNewHouseholdWithNoPlanAtAll:
    """The beta tester's first evening: no plan on file anywhere."""

    def test_the_card_is_offered_and_the_meal_is_saved(self):
        _a_recipe()
        assert [i["type"] for i in tools.get_needs_you_items()] == ["dinner_decision"]

        tools.resolve_needs_you_dinner(_d(), "Chili")

        assert [e["meal"] for e in tools.get_meal_plan(days_ahead=1)] == ["Chili"]

    def test_it_becomes_a_cook_move_on_now_with_a_start_by_time_and_a_tick(self):
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili")

        cooks = _cooks_today()
        assert [c["title"] for c in cooks] == ["Chili"]
        move = cooks[0]
        assert move["date"] == _d()
        assert move["slot"] == "dinner"
        # 10 + 20 minutes of recipe, so the move knows when to start.
        assert move["duration_min"] == 30
        assert any(c.startswith("Start by ") for c in move["chips"])
        assert move["tickable"] is True
        assert move["done"] is False

    def test_cook_mode_is_reachable_and_carries_the_recipe(self):
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili")

        view = tools.get_cooker_view()
        assert [m["meal"] for m in view["meals"]] == ["Chili"]
        meal = view["meals"][0]
        assert meal["has_full_recipe"] is True
        assert meal["instructions"] == ["Cook it."]
        assert view["meals_total"] == 1
        assert view["meals_done"] == 0
        # There genuinely is no week, and nothing here invents one — Today's
        # week-state badge still has to read "none".
        assert view["weekly_plan_id"] is None
        assert tools.today_moves()["week_state"] == "none"

        # The move's own target is what shell.js opens cook mode with.
        focus = _cooks_today()[0]["action"]["target"]["cookFocus"]
        assert focus["entryId"] == meal["entry_id"]
        assert focus["title"] == "Chili"

    def test_the_morning_text_says_it(self):
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili")

        assert "Chili" in (digest.build_morning_text() or "")

    def test_ticking_it_works_and_counts_once(self):
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili")
        move_id = _cooks_today()[0]["id"]

        tools.set_move_done(move_id, True)

        payload = tools.today_moves()
        assert [m["done"] for m in payload["moves"] if m["kind"] == "cook"] == [True]
        assert (payload["done"], payload["total"]) == (1, 1)
        view = tools.get_cooker_view()
        assert (view["meals_done"], view["meals_total"]) == (1, 1)

        tools.set_move_done(move_id, False)
        assert [m["done"] for m in _cooks_today()] == [False]

    def test_the_shop_move_can_see_it_too(self):
        """
        _shop_move reads the same view: "the list has things on it AND
        there is a real cook close enough for that to matter". With the
        cook invisible, a brand-new household that answered tonight and
        said yes to the ingredients was never told to go and buy them.
        """
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili", add_ingredients_to_grocery_list=True)

        assert "shop" in [m["kind"] for m in tools.today_moves()["moves"]]

    def test_nothing_planned_still_means_nothing_to_cook(self):
        """The empty state is still empty — this only shows real rows."""
        _a_recipe()

        assert tools.get_cooker_view()["meals"] == []
        assert _cooks_today() == []
        assert "Chili" not in (digest.build_morning_text() or "")


class TestAPlanThatDoesNotCoverToday:
    """
    Both are real states (see the 2026-09-11 stale-draft entry):
    _current_weekly_plan_row falls back to the newest plan when none covers
    today, so `get_weekly_plan()` answers with a week that has already gone
    or has not started.
    """

    def test_a_past_week_is_the_only_plan_on_file(self):
        tools.create_weekly_plan(_d(-21))
        _a_recipe()

        tools.resolve_needs_you_dinner(_d(), "Chili")

        assert [c["title"] for c in _cooks_today()] == ["Chili"]
        assert _d() in [m["date"] for m in tools.get_cooker_view()["meals"]]

    def test_a_future_week_is_the_only_plan_on_file(self):
        future_start = _d(14)
        plan_id = tools.create_weekly_plan(future_start)["weekly_plan_id"]
        _a_recipe()
        _a_recipe("Salmon")
        tools.plan_meal(future_start, "Salmon", slot="dinner", weekly_plan_id=plan_id)

        tools.resolve_needs_you_dinner(_d(), "Chili")

        assert [c["title"] for c in _cooks_today()] == ["Chili"]

    def test_the_days_come_back_in_eating_order_not_source_order(self):
        """
        Tonight's answered dinner is read from a different place than the
        plan's meals, and kitchenTodayRows / cookRestOfWeekHtml (shell.js)
        walk this list unsorted — see the 2026-09-10 "a day printed dinner
        before lunch" entry. Appended rather than merged, tonight would
        print after next week.
        """
        future_start = _d(3)
        plan_id = tools.create_weekly_plan(
            future_start, content_start_date=future_start, day_count=4
        )["weekly_plan_id"]
        _a_recipe()
        _a_recipe("Salmon")
        tools.plan_meal(future_start, "Salmon", slot="dinner", weekly_plan_id=plan_id)

        tools.resolve_needs_you_dinner(_d(), "Chili")

        assert [m["date"] for m in tools.get_cooker_view()["meals"]] == [_d(), future_start]


class TestAPlanThatDoesCoverToday:
    """
    The regression side. On a day a plan speaks for, the plan is the answer
    and none of this changes anything — an unlinked entry and a planned one
    must never both show for the same slot.
    """

    def _plan_from_today(self, days: int = 7) -> int:
        return tools.create_weekly_plan(
            _d(), content_start_date=_d(), day_count=days
        )["weekly_plan_id"]

    def test_the_planned_dinner_shows_exactly_once(self):
        plan_id = self._plan_from_today()
        _a_recipe("Salmon")
        tools.plan_meal(_d(), "Salmon", slot="dinner", weekly_plan_id=plan_id)

        assert [m["meal"] for m in tools.get_cooker_view()["meals"]] == ["Salmon"]
        assert [c["title"] for c in _cooks_today()] == ["Salmon"]

    def test_an_unlinked_meal_on_a_covered_day_changes_nothing(self):
        """
        A one-off chat plan_meal never passes a weekly_plan_id, so it writes
        an unlinked row even on a day the plan owns. Deliberately still
        invisible here: "exactly as before" for a covered day is the whole
        no-duplicates rule, and making a chat one-off visible beside the
        plan's own row is a separate decision nobody has made.
        """
        plan_id = self._plan_from_today()
        _a_recipe("Salmon")
        _a_recipe("Tacos")
        tools.plan_meal(_d(), "Salmon", slot="dinner", weekly_plan_id=plan_id)
        tools.plan_meal(_d(), "Tacos", slot="lunch")

        assert [m["meal"] for m in tools.get_cooker_view()["meals"]] == ["Salmon"]

    def test_a_day_past_the_plans_last_one_is_not_covered_by_it(self):
        """A three-day plan speaks for three days; day four is loose."""
        plan_id = self._plan_from_today(days=3)
        _a_recipe("Salmon")
        _a_recipe("Tacos")
        tools.plan_meal(_d(), "Salmon", slot="dinner", weekly_plan_id=plan_id)
        tools.plan_meal(_d(4), "Tacos", slot="dinner")

        assert [m["meal"] for m in tools.get_cooker_view()["meals"]] == ["Salmon", "Tacos"]


class TestWhichLooseMealsCountAsThisWeeksCooking:
    def test_the_window_is_today_through_six_days_out(self):
        for offset, name in ((-2, "Yesterdays"), (0, "Tonights"), (6, "Fridays"), (7, "NextWeeks")):
            _a_recipe(name)
            tools.plan_meal(_d(offset), name, slot="dinner")

        assert [m["meal"] for m in tools.get_cooker_view()["meals"]] == ["Tonights", "Fridays"]

    def test_naming_a_plan_asks_about_that_plan_and_nothing_else(self):
        """
        get_cooker_view(plan_id) is a question about ONE plan — the share
        view and /api/cooker/{id} both ask it that way — so loose meals stay
        out of it however close their dates are.
        """
        plan_id = tools.create_weekly_plan(
            _d(), content_start_date=_d(), day_count=7
        )["weekly_plan_id"]
        _a_recipe("Salmon")
        _a_recipe("Tacos")
        tools.plan_meal(_d(), "Salmon", slot="dinner", weekly_plan_id=plan_id)
        tools.plan_meal(_d(8), "Tacos", slot="dinner")

        assert [m["meal"] for m in tools.get_cooker_view(plan_id)["meals"]] == ["Salmon"]

    def test_a_loose_meal_is_scoped_to_its_own_household(self):
        _a_recipe()
        tools.resolve_needs_you_dinner(_d(), "Chili")
        beta = households.create_household("The Beta Testers", "a-distinct-passphrase")

        with tools.use_household(beta):
            assert tools.get_cooker_view()["meals"] == []


class TestTheRouteTheCardActuallyPosts:
    def test_answering_over_http_leaves_a_move_on_todays_timeline(self, signed_in):
        _a_recipe("Tacos")

        res = signed_in.post(
            "/api/needs-you/dinner",
            json={"date": _d(), "meal": "Tacos", "add_ingredients": False},
        )
        assert res.status_code == 200
        # The card that was just answered is gone from the refreshed list.
        assert "dinner_decision" not in [
            i["type"] for i in res.json()["items"] if i.get("date") == _d()
        ]

        moves = signed_in.get("/api/today/moves").json()
        cooks = [m for m in moves["moves"] if m["kind"] == "cook"]
        assert [c["title"] for c in cooks] == ["Tacos"]

        view = signed_in.get("/api/cooker-view").json()
        assert [m["meal"] for m in view["meals"]] == ["Tacos"]

    def test_ticking_it_over_http_sticks(self, signed_in):
        _a_recipe("Tacos")
        signed_in.post(
            "/api/needs-you/dinner",
            json={"date": _d(), "meal": "Tacos", "add_ingredients": False},
        )
        move_id = [
            m for m in signed_in.get("/api/today/moves").json()["moves"]
            if m["kind"] == "cook"
        ][0]["id"]

        res = signed_in.post(f"/api/today/moves/{move_id}/done", json={"done": True})
        assert res.status_code == 200

        after = signed_in.get("/api/today/moves").json()
        assert [m["done"] for m in after["moves"] if m["kind"] == "cook"] == [True]
