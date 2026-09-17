"""
A person may not swap a dish onto a night that has already gone by.

`swap_meal_in_plan` is the write behind EVERY swap in this app and it read
no clock at all. Reproduced over the real function on an approved week
before anything was touched: a swap onto a past night rewrote the dish and
put a NEW line on the shopping list for a dinner that was over —
`{'Black beans': '4 cans'}` became `{'Black beans': '2 cans',
'Carrots': '6'}`. A decision nobody can act on, and a shop for a night that
has been and gone.

HOW REACHABLE EACH DOOR IS, measured rather than assumed, because the doors
differ and the file should not flatten them. The two chat doors have no
client date filter at all, so both are fully reachable — and the change
card was measured APPLYING a change to a past night on the unmodified app
(`assert 'applied' == 'refused'` below), which is the strongest evidence
here. The three screen doors are all gated on `day.isPast`, which is
computed from the BROWSER's date while the refusal reads
households.timezone — so they are reachable from a tab drawn yesterday, a
retried POST, a direct call, or a phone west of the stored zone. That last
one is a false positive this change INHERITS from its two siblings, and it
is measured at exactly 3 hours a night for a Vancouver phone on a household
row left at the Toronto default. Named, not fixed: the honest fix is the
stored zone.

WHY THE REFUSAL IS NOT IN THAT FUNCTION, which is the whole of this card.
`plan_quality.repair_snack_clashes` reaches it AT GENERATION TIME, and a
plan whose period STARTED before today is an ordinary shape here (a
Saturday sign-up's Sat–Sun week, a custom date range, a takeover remnant).
Measured, on a plan begun three days ago: the repair legitimately swaps a
snack on a day two days behind the household's today. Measured again with
the naive fix in place — a refusal inside `swap_meal_in_plan` — and the
repair returned `moved: []` with its exception swallowed by its own
try/except, which wraps the WHOLE loop, so every other repair on the week
went with it. Silently.

So the rule is asked where the DECISION is, which is also what both halves
of the Review stepper already do (`add_dish_day`, `drop_dish_from_day`).
`weekly_plan.night_has_gone` is the one statement of it; five person-facing
doors ask it — the chat twin, "Swap · I'll pick", the protein change, the
chat change card's Save changes, and `apply_pick` as the backstop the last
three share. `swap_meal_in_plan` itself is byte-identical, so generation is
untouched by construction rather than exempted by an argument.

The clock is the other subtlety. The container runs UTC and households
default to America/Toronto, so between 8pm local and midnight the server's
date is already tomorrow; a refusal on the server's date would refuse
TONIGHT for four hours every evening, which is worse than the bug it fixes.
Both directions are pinned here the way `tests/test_weekly_plan_household_
clock.py` pins them: `cooker.datetime` frozen at one UTC instant with
`households.timezone` set, so the household's day genuinely moves while the
server's does not. The zone lookup and the column read both run for real.

Each test says in its own docstring whether it is a CATCH (red on the
unmodified app) or a NO-REGRESSION GUARD (green either way, here to say
what did not change). The guards are pinned by MUTATION instead, and every
mutation named in one was run.

Everything UNFROZEN counts its days off `conftest.household_today()`, never
off `date.today()`. The two are different days for part of every UTC day,
in both directions, so a file seeded on the process's clock that then asks
the app about "today" is asserting the two agree — the exact trap
`test_add_a_night_refuses_the_past.py` fell into once and wrote down.
`_server_day` exists for the frozen tests alone, where naming a day in the
process's own terms is the entire point.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from conftest import household_today

from app import agent, tools
from app.db import get_conn
from app.tools import cooker as _cooker
from app.tools import plan_quality as _pq
from app.tools import plate_parts as _parts
from app.tools import proposals as _proposals
from app.tools import swap_in_place as _swap
from app.tools import weekly_plan as _wp


SERVER_TODAY = date.today()
# Today where the seeded household lives — the clock the refusal reads, and
# so the only honest anchor for the unfrozen tests. See the module
# docstring; the frozen tests keep the SERVER anchor on purpose.
HOUSEHOLD_TODAY = household_today()

# 01:30 UTC is 21:30 the evening BEFORE in Toronto, so the household is a
# whole day BEHIND the server. That is the production direction, and every
# evening between 8pm and midnight Eastern.
UTC_EVENING = datetime.combine(SERVER_TODAY, time(1, 30), tzinfo=timezone.utc)
# 23:30 UTC is 08:30 the morning AFTER in Tokyo — the household a day
# AHEAD, the same split read from the other side.
UTC_LATE = datetime.combine(SERVER_TODAY, time(23, 30), tzinfo=timezone.utc)

TORONTO = "America/Toronto"
TOKYO = "Asia/Tokyo"

# Wider than any one test needs, and starting four days back from whichever
# anchor is earlier, so a past night is inside the period whichever clock
# named it and plan_meal's own period guard never refuses anything here.
PLAN_START = min(SERVER_TODAY, HOUSEHOLD_TODAY) - timedelta(days=4)
PLAN_DAYS = 11

REFUSAL = "That night’s already gone."
REFUSAL_WHY = "that night has already gone"


# ---------------------------------------------------------------- helpers

def _set_timezone(name: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE households SET timezone = ? WHERE id = ?", (name, tools.household_id())
    )
    conn.commit()
    conn.close()


def _freeze(monkeypatch, instant: datetime) -> None:
    """Every reading of the wall clock inside cooker answers `instant`."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone(timezone.utc).replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(_cooker, "datetime", _Frozen)


def _behind(monkeypatch) -> date:
    """Household a day behind the server. Returns the household's today."""
    _set_timezone(TORONTO)
    _freeze(monkeypatch, UTC_EVENING)
    return SERVER_TODAY - timedelta(days=1)


def _ahead(monkeypatch) -> date:
    """Household a day ahead of the server. Returns the household's today."""
    _set_timezone(TOKYO)
    _freeze(monkeypatch, UTC_LATE)
    return SERVER_TODAY + timedelta(days=1)


def _day(offset: int) -> str:
    """A day of the plan, counted off the HOUSEHOLD's today — the clock the
    refusal reads. Everything unfrozen uses this."""
    return (HOUSEHOLD_TODAY + timedelta(days=offset)).isoformat()


def _server_day(offset: int) -> str:
    """A day counted off the SERVER's today. Only the frozen tests use it,
    and only because naming a day in the process's own terms is exactly what
    they are for."""
    return (SERVER_TODAY + timedelta(days=offset)).isoformat()


def _plan() -> int:
    return tools.create_weekly_plan(
        PLAN_START.isoformat(),
        content_start_date=PLAN_START.isoformat(),
        day_count=PLAN_DAYS,
    )["weekly_plan_id"]


def _recipes() -> None:
    tools.add_recipe(
        "Bean Chili",
        ingredients=[{"item": "Black beans", "qty": "2 cans", "category": "pantry"}],
        instructions=["Simmer."], default_servings=4,
    )
    tools.add_recipe(
        "Carrot Soup",
        ingredients=[{"item": "Carrots", "qty": "6", "category": "produce"}],
        instructions=["Simmer."], default_servings=4,
    )


def _ids(day: str, slot: str = "dinner") -> list[int]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = ? "
        "ORDER BY id ASC", (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def _state(day: str, slot: str = "dinner") -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT mpe.slot_state, COALESCE(r.name, mpe.freeform_meal) AS meal "
        "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "WHERE mpe.household_id = ? AND mpe.date = ? AND mpe.slot = ? ORDER BY mpe.id ASC",
        (tools.household_id(), day, slot),
    ).fetchall()
    conn.close()
    return [(r["slot_state"], r["meal"]) for r in rows]


def _glist() -> dict:
    return {r["item"]: r["quantity"] for r in tools.list_grocery_list()}


def _seed(*days: str, approve: bool = False) -> int:
    """Bean Chili on each named day, of a plan wide enough to hold them."""
    plan = _plan()
    _recipes()
    for day in days:
        tools.plan_meal(day, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    if approve:
        tools.approve_weekly_plan(plan)
    return plan


def _never_asked(*_a, **_k):
    """A picker that fails the test if the model is reached at all."""
    raise AssertionError("a model call was spent on a night that has gone by")


# -------------------------------------------- the harness's own two clocks

class TestTheFreezeReallySplitsTheTwoClocks:
    def test_a_toronto_evening_puts_the_household_a_day_behind_the_server(self, monkeypatch):
        """GUARD on the harness, not on the app. If the two clocks ever
        agree here, every clock test below passes without testing
        anything."""
        today = _behind(monkeypatch)
        assert today != SERVER_TODAY
        assert _wp._household_today() == today

    def test_a_tokyo_morning_puts_the_household_a_day_ahead(self, monkeypatch):
        """GUARD, the other direction."""
        today = _ahead(monkeypatch)
        assert today != SERVER_TODAY
        assert _wp._household_today() == today

    def test_the_unfrozen_tests_count_their_days_off_the_households_clock(self):
        """GUARD on the anchor every unfrozen test rests on, and the one
        that stops this file going red on a runner in another timezone.
        Trivially true when the two clocks agree and a real assertion
        whenever they do not, which is exactly when it matters."""
        assert _day(0) == _wp._household_today().isoformat()


# ------------------------------------------------------- 1. the chat door

class TestTheChatSwap:
    def test_a_past_night_is_refused(self):
        """CATCH. The tool the model calls is the twin, and the twin says
        no."""
        plan = _seed(_day(-1))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.swap_meal_in_plan_for_chat(plan, _day(-1), "Carrot Soup", slot="dinner")

        assert str(excinfo.value) == REFUSAL

    def test_the_measured_harm_is_gone(self):
        """CATCH, and the reproduction itself. On an approved week the
        swap used to rewrite the night AND put a new line on the shopping
        list for a dinner that was over."""
        plan = _seed(_day(-2), _day(2), approve=True)
        before_list = _glist()
        assert before_list == {"Black beans": "4 cans"}

        with pytest.raises(_wp.SlotRefused):
            tools.swap_meal_in_plan_for_chat(
                plan, _day(-2), "Carrot Soup", slot="dinner", old_meal="Bean Chili"
            )

        assert _state(_day(-2)) == [("planned", "Bean Chili")]
        assert _glist() == before_list

    def test_the_refusal_is_a_slotrefused_so_the_route_can_tell_it_apart(self):
        """CATCH. A sentence written for a person, in the type the routes
        answer 200 for — and still a ValueError underneath, so every
        existing `except ValueError` around a swap keeps catching it."""
        plan = _seed(_day(-1))

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.swap_meal_in_plan_for_chat(plan, _day(-1), "Carrot Soup", slot="dinner")

        assert isinstance(excinfo.value, ValueError)

    def test_a_night_days_back_is_refused_too(self):
        """CATCH. Not just yesterday — anything behind today."""
        plan = _seed(_day(-3))

        with pytest.raises(_wp.SlotRefused):
            tools.swap_meal_in_plan_for_chat(plan, _day(-3), "Carrot Soup", slot="dinner")

        assert _state(_day(-3)) == [("planned", "Bean Chili")]

    def test_the_date_is_read_when_it_arrives_as_a_keyword(self):
        """CATCH. The twin takes *args so it never restates the real
        function's signature, which means it has to find the date in
        either shape. A positional-only read would wave this through."""
        plan = _seed(_day(-1))

        with pytest.raises(_wp.SlotRefused):
            tools.swap_meal_in_plan_for_chat(
                weekly_plan_id=plan, meal_date=_day(-1), new_meal="Carrot Soup", slot="dinner"
            )

    def test_the_agents_tool_table_points_at_the_twin(self):
        """CATCH. The refusal is worth nothing if the model still reaches
        the bare function. The tool's NAME is unchanged, which is what the
        schema and the prompt both say."""
        assert agent.TOOL_FUNCTIONS["swap_meal_in_plan"] is tools.swap_meal_in_plan_for_chat
        assert "swap_meal_in_plan" in {t["name"] for t in agent.TOOL_DEFINITIONS}

    def test_tonight_is_still_an_ordinary_swap(self):
        """GUARD. Green either way — here because refusing tonight would
        be the same bug wearing the other hat. Pinned by mutation: it
        reddens under `<=`."""
        plan = _seed(_day(0))

        out = tools.swap_meal_in_plan_for_chat(plan, _day(0), "Carrot Soup", slot="dinner")

        assert out["entry_id"]
        assert _state(_day(0)) == [("planned", "Carrot Soup")]

    def test_a_night_still_ahead_is_an_ordinary_swap(self):
        """GUARD. The everyday case, on a plan that started four days ago
        — the shape a mid-week household is actually in."""
        plan = _seed(_day(2))

        out = tools.swap_meal_in_plan_for_chat(plan, _day(2), "Carrot Soup", slot="dinner")

        assert out["entry_id"]
        assert _state(_day(2)) == [("planned", "Carrot Soup")]


# ------------------------------------------- 2. Swap · I'll pick / Change one

class TestSwapInPlace:
    def test_a_past_night_is_refused_and_no_model_call_is_spent(self):
        """CATCH. The Review row's "Change one" and the Day/Meal step's
        "Swap · I'll pick" both land here. Asked above the picker on
        purpose: every attempt down there is a real API call, and spending
        one to be told the night is gone is a cost with nothing on the
        other side of it."""
        plan = _seed(_day(-1))

        out = tools.swap_meal_in_place(plan, _ids(_day(-1))[0], picker=_never_asked)

        assert out["status"] == "refused"
        assert out["message"] == REFUSAL

    def test_nothing_is_written_when_it_is_refused(self):
        """CATCH. Its own contract already says a refusal means nothing
        changed, and on an approved week that has to include the list."""
        plan = _seed(_day(-2), _day(2), approve=True)
        before = _glist()

        out = tools.swap_meal_in_place(plan, _ids(_day(-2))[0], picker=_never_asked)

        assert out["status"] == "refused"
        assert _state(_day(-2)) == [("planned", "Bean Chili")]
        assert _glist() == before

    def test_tonight_still_swaps(self):
        """GUARD / anti-wrong-fix. Reddens under `<=`, which is the whole
        reason it is here."""
        plan = _seed(_day(0))
        pick = {"meal_name": "Carrot Soup", "reason": "quick", "food_groups": ["veg"],
                "ingredients": [{"item": "Carrots", "qty": "6", "category": "produce"}],
                "instructions": ["Simmer."]}

        out = tools.swap_meal_in_place(plan, _ids(_day(0))[0], picker=lambda ctx: dict(pick))

        assert out["status"] == "swapped"
        assert _state(_day(0)) == [("planned", "Carrot Soup")]

    def test_the_route_answers_it_as_a_refusal(self, signed_in):
        """CATCH, with one caveat worth stating. 200 with the sentence —
        the shape this route already uses for an allergen refusal, and the
        one runSwapInPlace shows as a plain line, so no client change was
        needed.

        On the unmodified app it is red as a **500**, not as a swap that
        landed: the route takes no injectable picker, so without the
        refusal it reaches a real model call, which has no key in a test
        run. That redness is still caused by the missing refusal — nothing
        else short-circuits before the API — but it is not proof the swap
        went through. The proof of that is the change card's own route
        test below, which makes no model call at all, and the
        reproduction in the module docstring."""
        plan = _seed(_day(-1))
        entry = _ids(_day(-1))[0]

        res = signed_in.post(
            f"/api/week/{PLAN_START.isoformat()}/swap-in-place",
            json={"entry_id": entry},
        )

        assert res.status_code == 200, res.text
        assert res.json()["status"] == "refused"
        assert res.json()["message"] == REFUSAL
        assert _state(_day(-1)) == [("planned", "Bean Chili")]


# ------------------------------------------------- 3. the shared backstop

class TestApplyPickIsTheBackstop:
    def test_a_past_entry_is_refused_at_the_shared_write(self):
        """CATCH. Deliberately dead code from the three doors that ask
        first — it is here so a NEW door that forgets gets a refusal
        rather than the bug back. Called directly, the way a new caller
        would."""
        plan = _seed(_day(-1))
        entry = _swap._entry(plan, _ids(_day(-1))[0])

        with pytest.raises(_wp.SlotRefused) as excinfo:
            _swap.apply_pick(plan, entry, {
                "meal_name": "Carrot Soup",
                "ingredients": [{"item": "Carrots", "qty": "6", "category": "produce"}],
                "instructions": ["Simmer."],
            })

        assert str(excinfo.value) == REFUSAL
        assert _state(_day(-1)) == [("planned", "Bean Chili")]

    def test_it_refuses_before_it_saves_a_recipe(self):
        """CATCH. First line of the function: a refusal that ran after
        _save_recipe_if_new would leave a recipe behind for a dish nobody
        is going to cook."""
        plan = _seed(_day(-1))
        entry = _swap._entry(plan, _ids(_day(-1))[0])
        names_before = {(r.get("name") or "") for r in tools.list_recipes()}

        with pytest.raises(_wp.SlotRefused):
            _swap.apply_pick(plan, entry, {
                "meal_name": "A Dish Nobody Asked For",
                "ingredients": [{"item": "Carrots", "qty": "6", "category": "produce"}],
                "instructions": ["Simmer."],
            })

        assert {(r.get("name") or "") for r in tools.list_recipes()} == names_before


# ------------------------------------------------ 4. a different protein

class TestChangeThePart:
    def test_a_past_night_is_refused_and_no_model_call_is_spent(self):
        """CATCH. A different protein in a dinner that has been eaten is
        still a rewrite of a night nobody can act on, and on an approved
        week it still moves the shopping list."""
        plan = _seed(_day(-1))

        out = _parts.change_part(plan, _ids(_day(-1))[0], "protein", "beef", asker=_never_asked)

        assert out["status"] == "refused"
        assert out["message"] == REFUSAL
        assert _state(_day(-1)) == [("planned", "Bean Chili")]

    def test_tonight_still_changes(self):
        """GUARD / anti-wrong-fix. Reddens under `<=`."""
        plan = _seed(_day(0))
        variant = {"meal_name": "Bean Chili", "main_protein": "beef",
                   "ingredients": [{"item": "Ground beef", "qty": "1 lb", "category": "meat/seafood"}],
                   "instructions": ["Brown the beef.", "Simmer."], "reason": "beef tonight"}

        out = _parts.change_part(plan, _ids(_day(0))[0], "protein", "beef",
                                 asker=lambda ctx: dict(variant))

        assert out["status"] == "changed"


# -------------------------------------------- 5. the chat change card

class TestTheChangeCard:
    def _card(self, plan, days):
        rows = [{
            "date": day, "slot": "dinner", "action": "change",
            "candidates": [{
                "meal_name": f"Carrot Soup {i}", "reason": "lighter",
                "ingredients": [{"item": "Carrots", "qty": "6", "category": "produce"}],
                "instructions": ["Simmer."], "food_groups": ["veg"],
            }],
        } for i, day in enumerate(days)]
        return tools.propose_plan_changes(plan, rows)["proposal_id"]

    def test_a_past_row_is_refused_and_the_rest_of_the_card_still_lands(self):
        """CATCH, and the reason this is a per-row refusal rather than the
        raise apply_pick would give it: a card can name several nights, and
        one that is over must not take the others down with it."""
        plan = _seed(_day(-1), _day(2))
        pid = self._card(plan, [_day(-1), _day(2)])

        out = tools.apply_proposal(pid)

        assert [r["date"] for r in out["refused"]] == [_day(-1)]
        assert _state(_day(-1)) == [("planned", "Bean Chili")]
        assert _state(_day(2)) == [("planned", "Carrot Soup 1")]
        assert out["status"] == "applied"

    def test_the_reason_is_the_fragment_the_card_can_read(self):
        """CATCH. changeRowHtml renders a refused row as "<dish> stays —
        <why>" and its toast as "I left the week as it was — <why>.", so
        the whole sentence with a stop on it would land as "— That night's
        already gone..". Lower case, no stop."""
        plan = _seed(_day(-1))
        pid = self._card(plan, [_day(-1)])

        out = tools.apply_proposal(pid)

        assert out["refused"][0]["why"] == REFUSAL_WHY
        assert not REFUSAL_WHY.endswith(".")
        assert REFUSAL_WHY[0].islower()

    def test_the_route_refuses_the_row_and_makes_no_model_call(self, signed_in):
        """CATCH, and the cleanest evidence in this file that the swap
        really landed on the unmodified app: applying a card runs no model
        call at all, so nothing about this test can be explained by a
        missing API key. Red on main as `'applied' == 'refused'` — the card
        rewrote a night that was over."""
        plan = _seed(_day(-1))
        pid = self._card(plan, [_day(-1)])

        res = signed_in.post(f"/api/chat/proposals/{pid}/apply")

        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "refused"
        assert body["refused"][0]["why"] == REFUSAL_WHY
        assert _state(_day(-1)) == [("planned", "Bean Chili")]

    def test_a_card_of_only_past_rows_refuses_the_whole_thing(self):
        """CATCH. Nothing written, and `status` 'refused' rather than
        'nothing' — the reason is the thing to say."""
        plan = _seed(_day(-1))
        pid = self._card(plan, [_day(-1)])

        out = tools.apply_proposal(pid)

        assert out["status"] == "refused"
        assert _state(_day(-1)) == [("planned", "Bean Chili")]


# -------------------------- 6. GENERATION IS UNTOUCHED — the whole card

class TestWeekGenerationStillWorksOnAPartWeek:
    """
    A plan whose period STARTED before today is an ordinary shape in this
    app, and the week generator's own snack repair legitimately swaps a
    snack on a day that is behind the household's today. These are the
    tests the caller-side decision exists for, and they are GREEN on main
    — which is the point: generation must be byte-identical.

    All three are pinned by the same MUTATION, which was run: move the
    refusal into `swap_meal_in_plan` itself and every one of them reddens.
    """

    SNACKS = ["Sliced Pear", "Carrot Sticks", "Cheese Cubes", "Grapes",
              "Hummus Cup", "Trail Mix", "Boiled Egg"]

    def _part_week(self):
        """A week begun three days ago, every breakfast oatmeal so the
        clashing oatmeal cookies have nowhere to move to — which is what
        forces the repair down its `copy` arm, the one that calls
        swap_meal_in_plan. The clash is seeded on a day two days BEHIND
        the household's today."""
        start = (HOUSEHOLD_TODAY - timedelta(days=3)).isoformat()
        plan = tools.create_weekly_plan(
            start, content_start_date=start, day_count=7
        )["weekly_plan_id"]
        days = [(HOUSEHOLD_TODAY + timedelta(days=n)).isoformat() for n in range(-3, 4)]
        for i, day in enumerate(days):
            tools.plan_meal(day, "Oatmeal Porridge", slot="breakfast", weekly_plan_id=plan)
            tools.plan_meal(day, self.SNACKS[i], slot="snack", weekly_plan_id=plan)
        clash_day = days[1]
        tools.plan_meal(clash_day, "Oatmeal Cookies", slot="snack", weekly_plan_id=plan)
        return plan, clash_day

    def test_the_snack_repair_still_fixes_a_clash_on_a_day_that_has_gone(self):
        """GUARD, and the criterion this whole card turns on. Measured
        with the naive in-function fix: `moved` came back EMPTY, with the
        exception swallowed by repair_snack_clashes' own try/except — and
        because that wraps the entire loop, every other repair on the week
        went with it. Silently."""
        plan, clash_day = self._part_week()
        assert clash_day < HOUSEHOLD_TODAY.isoformat()

        moved = _pq.repair_snack_clashes(plan)

        assert [m["date"] for m in moved] == [clash_day]
        assert moved[0]["was"] == "Oatmeal Cookies"
        assert "Oatmeal Cookies" not in [meal for _, meal in _state(clash_day, "snack")]

    def test_the_whole_finishing_pass_repairs_it_too(self):
        """GUARD. The real generation path (_finish_week_slots), not the
        repair called on its own — and it leaves the week with no
        duplicated slots."""
        plan, clash_day = self._part_week()
        start = (HOUSEHOLD_TODAY - timedelta(days=3)).isoformat()

        agent._finish_week_slots(plan, start, None,
                                 tools.get_household_memory(), day_count=7)

        snacks = [meal for _, meal in _state(clash_day, "snack")]
        assert snacks and "Oatmeal Cookies" not in snacks
        assert tools.audit_plan_slots(plan)["duplicated"] == []

    def test_swap_meal_in_plan_itself_still_takes_a_night_that_has_gone(self):
        """GUARD, and the one that says out loud what was given up. The
        bare function is deliberately unguarded: it is the shared write,
        not the decision, and add_dish_day and the snack repair both
        compose it. A new caller of it is NOT covered by anything here —
        that is the cost of the caller-side answer, written down rather
        than hoped about."""
        plan = _seed(_day(-1))

        out = _wp.swap_meal_in_plan(plan, _day(-1), "Carrot Soup", slot="dinner")

        assert out["entry_id"]
        assert _state(_day(-1)) == [("planned", "Carrot Soup")]


# ---------------------------- 7. whose clock, in both directions

class TestTheHouseholdsClockAndNotTheServers:
    def test_a_household_a_day_behind_can_still_swap_its_own_tonight(self, monkeypatch):
        """GUARD against main and a CATCH against the obvious wrong fix.

        Nine at night in Toronto: the server is already on tomorrow, so on
        its clock the household's TONIGHT is yesterday. Refusing on the
        server's date would take tonight's swap away for four hours every
        evening. Green on the unmodified app (which refuses nothing) and
        green here; it reddens the moment `_household_today()` is swapped
        for `date.today()`, which is measured and is why it exists."""
        today = _behind(monkeypatch)
        assert today == SERVER_TODAY - timedelta(days=1)
        plan = _seed(today.isoformat())

        out = tools.swap_meal_in_plan_for_chat(
            plan, today.isoformat(), "Carrot Soup", slot="dinner"
        )

        assert out["entry_id"]
        assert _state(today.isoformat()) == [("planned", "Carrot Soup")]

    def test_a_household_a_day_behind_is_still_refused_its_own_yesterday(self, monkeypatch):
        """CATCH. The bug itself, read on the household's clock: the day
        before the household's today is gone wherever the server thinks it
        is."""
        today = _behind(monkeypatch)
        gone = (today - timedelta(days=1)).isoformat()
        plan = _seed(gone)

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.swap_meal_in_plan_for_chat(plan, gone, "Carrot Soup", slot="dinner")

        assert str(excinfo.value) == REFUSAL
        assert _state(gone) == [("planned", "Bean Chili")]

    def test_a_household_a_day_ahead_is_refused_the_servers_today(self, monkeypatch):
        """CATCH, and the direction a server-clock fix gets wrong too.

        Half past eight on a Tokyo morning: the server is still on
        yesterday. The night the SERVER calls today is one the household
        has already had, so it is refused — on the unmodified app it is
        accepted, and on the server's date it would be accepted as well."""
        _ahead(monkeypatch)
        gone = _server_day(0)
        plan = _seed(gone)

        with pytest.raises(_wp.SlotRefused) as excinfo:
            tools.swap_meal_in_plan_for_chat(plan, gone, "Carrot Soup", slot="dinner")

        assert str(excinfo.value) == REFUSAL
        assert _state(gone) == [("planned", "Bean Chili")]

    def test_a_household_a_day_ahead_can_swap_the_day_the_server_calls_tomorrow(self, monkeypatch):
        """GUARD. The household's own today, which the server calls
        tomorrow, is an ordinary night to swap. Pinned by mutation: it
        reddens under `<=`."""
        today = _ahead(monkeypatch)
        plan = _seed(today.isoformat())

        out = tools.swap_meal_in_plan_for_chat(
            plan, today.isoformat(), "Carrot Soup", slot="dinner"
        )

        assert out["entry_id"]
        assert _state(today.isoformat()) == [("planned", "Carrot Soup")]

    def test_swap_in_place_reads_the_households_clock_too(self, monkeypatch):
        """CATCH. Every door asks the one predicate, so none of them can
        be on a different clock from the others — but only a test says
        so."""
        today = _behind(monkeypatch)
        gone = (today - timedelta(days=1)).isoformat()
        plan = _seed(gone, today.isoformat())

        refused = tools.swap_meal_in_place(plan, _ids(gone)[0], picker=_never_asked)
        allowed = tools.swap_meal_in_place(
            plan, _ids(today.isoformat())[0],
            picker=lambda ctx: {"meal_name": "Carrot Soup", "reason": "lighter",
                                "food_groups": ["veg"], "instructions": ["Simmer."],
                                "ingredients": [{"item": "Carrots", "qty": "6",
                                                 "category": "produce"}]},
        )

        assert refused["status"] == "refused"
        assert allowed["status"] == "swapped"


# ------------------------------ 8. the clock is read on nobody's lock

def test_the_clock_is_read_with_no_connection_of_this_callers_open(monkeypatch):
    """
    GUARD, and the one this repo has twice earned. `night_has_gone` reaches
    `cooker.household_now`, which opens a connection of its own; asked
    while a caller is holding one it would be a nested get_conn, and this
    app has twice paid for that with an intermittent "database is locked"
    rather than a wrong answer — the kind of failure no test sees until
    production.

    RED on the unmodified app, and for the wrong reason: the refusal is
    absent there, so the picker is reached and it dies on "a model call was
    spent" without ever reaching the assertion it is named after. So it is
    pinned by MUTATION rather than by that redness — holding a connection
    open across the clock read fails it on `assert 2 == 1`, which is the
    thing it is for.

    Both modules import get_conn by name, so both have to be patched — a
    patch of app.db.get_conn alone would watch a door neither of them uses.
    A proxy rather than a patched method: sqlite3.Connection.close is
    read-only.
    """
    depth = {"now": 0, "max": 0}
    real = _wp.get_conn

    class _Counted:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def close(self):
            depth["now"] -= 1
            self._conn.close()

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_conn"), name)

        def __setattr__(self, name, value):
            setattr(object.__getattribute__(self, "_conn"), name, value)

    def tracking():
        conn = _Counted(real())
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        return conn

    plan = _seed(_day(-1))
    entry = _ids(_day(-1))[0]
    monkeypatch.setattr(_wp, "get_conn", tracking)
    monkeypatch.setattr(_cooker, "get_conn", tracking)
    monkeypatch.setattr(_swap, "get_conn", tracking)

    depth["max"] = 0
    out = tools.swap_meal_in_place(plan, entry, picker=_never_asked)

    assert out["status"] == "refused"
    assert depth["max"] == 1


# ------------------------------------- 9. what was deliberately left

def test_undo_is_deliberately_not_refused_on_a_night_that_has_gone():
    """
    CHARACTERISATION, green either way, and a decision rather than an
    oversight.

    An undo is the WITHDRAWAL of a decision the app allowed, not a new one:
    it can only ever put back the dish that was already on that slot, never
    an arbitrary one, so it is a repair and not a change. Refusing it would
    also create a dead end this change itself put the household in — swap
    at 11:59, midnight passes, Undo refused, and the re-swap that would fix
    it is refused too. And after this fix a past-night undo is strictly
    LESS reachable than before, because the swap that writes the undo note
    is now refused.

    Invert this test if that call is ever reversed.
    """
    plan = _seed(_day(0))
    pick = {"meal_name": "Carrot Soup", "reason": "lighter", "food_groups": ["veg"],
            "instructions": ["Simmer."],
            "ingredients": [{"item": "Carrots", "qty": "6", "category": "produce"}]}
    swapped = tools.swap_meal_in_place(plan, _ids(_day(0))[0], picker=lambda ctx: dict(pick))
    assert swapped["status"] == "swapped"

    # Move the swapped entry onto a night that has gone, the way a
    # midnight crossing would leave it.
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET date = ? WHERE id = ? AND household_id = ?",
        (_day(-1), swapped["entry_id"], tools.household_id()),
    )
    conn.commit()
    conn.close()

    out = tools.undo_meal_swap(plan, swapped["entry_id"])

    assert out["status"] == "restored"
    assert out["meal"] == "Bean Chili"
