"""
Cook tab: a prepped lunch is cooked on its PREP day, not on the first day
it is eaten.

Loop Board card (Emily's own decision, 2026-09-25 — "yes to both" to
"Merge as is and file the Cook-tab fix as a follow-up?"): "As someone who
preps lunches on Sunday, I want Sunday's Cook tab and prep list to show
what I'm prepping, so that I cook it on the day I actually cook it and
Monday just says it's ready."

What was wrong, reproduced on a throwaway database before anything moved:
weekday_lunches.apply_to_plan writes the batch's cook onto the FIRST
prepped lunch's entry — a chain cannot hold a cook on a day the dish isn't
eaten (cook_ahead.apply_prep_day_batches says the same in its own
docstring) — and stamps the prep day only on that entry's derived_from and
its reasoning. Nothing read the stamp. So on a Sunday-start week the Sunday
prep session held the breakfast batch and not the Chili, and Monday's Cook
card read "Cooking for 4 — enough for Monday and Tuesday" with a start-by
time, under an entry whose own reasoning said "Cook this Sunday". On the
commoner Monday-start week the Sunday falls the day BEFORE the period, so
prep_sessions_for_plan returned [] and there was no session at all.

The fix is READ-SIDE and writes nothing new: weekday_lunches.prepped_batches
reads the stamp back, prep_sessions puts the work on the prep day, and
cooker._apply_prepped_lunches stops the lunch day claiming the cook.

MEASURED against main (6f6a5b3) with the two new names stubbed so every test
reaches its own assertion rather than the file failing to collect:
**24 red, 6 green** — and the 24 is decomposed per test in each docstring
rather than quoted, because a red count where the test dies on a missing
symbol is close to worthless (CLAUDE.md has had to unpick that statistic
several times).

**SIXTEEN fail on the assertion they are named for**: the two "the prep day
has no session at all" ones (Sunday-start and Monday-start), the midweek prep
day, the cross-plan session, the missing `prepped_ahead` on the cook card, the
later lunch's headline, the household with no rhythm prep days, the row's
badge and line, the subtitle's count, the card landing on the real cook, the
pinned prepped card, the card's note and times, the dock, the reheat note, the
session item's line, and the cross-plan tick.

**EIGHT are red for a reason other than their own claim** and say so: six die
on a session or an item that does not exist on main at all (the
exactly-once duplicate, the one-lunch batch, the one-phrase guard, the
tick-once counting, the plan-as-it-stands reader, the isolation guard), one on
a precondition (skip_prep_this_week), one is a source marker. Each names the
mutation that pins it instead.

**One was first labelled GUARD and measured RED on its own claim**
(test_a_pinned_prepped_lunch_is_still_an_honest_card); it is relabelled
rather than quietly kept.

The 6 green are the no-regression guards, and every one names the mutation it
is held up by.
"""
from __future__ import annotations

import datetime
import json
import shutil

import pytest

import nodeharness
from conftest import household_today
from app import agent, tools
from app.db import get_conn
from app.tools import cooker, prep_sessions, weekday_lunches

_needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the screen's own functions"
)

SHELL_JS = (nodeharness.REPO / "static" / "shell.js").read_text(encoding="utf-8") \
    if hasattr(nodeharness, "REPO") else None


# ==========================================================================
# seeding
# ==========================================================================

def _slot(date, slot, name, minutes=30):
    return {"date": date, "slot": slot, "meal_name": name, "is_new_recipe": True,
            "ingredients": [{"item": f"{name} bits", "qty": "2 lb", "category": "pantry"}],
            "reasoning": f"{name} because", "food_groups": ["protein", "vegetable", "carb"],
            "prep_time_minutes": 10, "cook_time_minutes": minutes - 10}


def _week(dates, lunches, dinners=None, breakfasts=None):
    dinners = dinners or [f"Din {i}" for i in range(len(dates))]
    breakfasts = breakfasts or [f"Toast {i}" for i in range(len(dates))]
    out = []
    for i, d in enumerate(dates):
        out.append(_slot(d, "breakfast", breakfasts[i]))
        out.append(_slot(d, "lunch", lunches[i]))
        out.append(_slot(d, "dinner", dinners[i]))
    return out


def _next_weekday(name: str, weeks_ahead: int = 1) -> datetime.date:
    """The `name` of the week `weeks_ahead` weeks from the household's own
    today — seeded off the HOUSEHOLD's clock, never the process's."""
    want = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(name)
    today = household_today()
    monday = today - datetime.timedelta(days=today.weekday())
    return monday + datetime.timedelta(days=7 * weeks_ahead + want)


def _dates(start: datetime.date, n: int = 7) -> list[str]:
    return [(start + datetime.timedelta(days=i)).isoformat() for i in range(n)]


@pytest.fixture
def two_adults():
    for name in ("Emily", "Vineeth"):
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda ctx: days)
    return _stub


def _plan(stub_model, start, kinds, prep_days, lunches, day_count=7,
          dinners=None, breakfasts=None, approve=True):
    """One seeded, generated (and by default approved) week whose weekday
    lunches the household answered. `kinds` is {date index: kind}."""
    s = start.isoformat()
    dates = _dates(start, day_count)
    tools.save_week_intake(s, weekday_lunches={
        "prep_days": prep_days,
        "days": [{"date": dates[i], "kind": k} for i, k in sorted(kinds.items())],
    }, day_count=day_count)
    stub_model(_week(dates, lunches, dinners, breakfasts))
    plan_id = agent.generate_weekly_plan(s, day_count=day_count, confirm_takeover=True)["weekly_plan_id"]
    if approve:
        tools.approve_weekly_plan(plan_id)
    return plan_id, dates


def _session(plan_id, iso):
    for s in prep_sessions.prep_sessions_for_plan(plan_id):
        if s["date"] == iso:
            return s
    return None


def _lunch_cards(plan_id):
    return {m["date"]: m for m in cooker.get_cooker_view(plan_id)["meals"] if m.get("slot") == "lunch"}


# ==========================================================================
# 1. The batch is on its prep day
# ==========================================================================

class TestTheBatchIsOnItsPrepDay:
    def test_a_sunday_start_week_shows_the_batch_under_sundays_prep(self, two_adults, stub_model):
        """CATCH. On main the Sunday session holds whatever else landed there
        and never the Chili: the chain's source is dated MONDAY, which is the
        only date _cook_ahead_items looks at."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        session = _session(plan_id, dates[0])
        assert session is not None, "the Sunday prep day has no session at all"
        titles = [i["title"] for i in session["items"]]
        assert "Chili" in titles, titles
        item = next(i for i in session["items"] if i["title"] == "Chili")
        assert item["line"] == "For Monday and Tuesday’s lunches."
        assert item["kind"] == "cook_ahead"
        assert item["covers"] == [dates[1], dates[2]]

    def test_a_monday_start_weeks_sunday_prep_day_falls_before_the_period_and_still_gets_a_session(
            self, two_adults, stub_model):
        """CATCH, and the commonest shape there is: `planning_anchor`
        defaults to sunday_before, so a household that preps on Sunday preps
        the day BEFORE the week. No weekday of a Mon-Sun period maps to that
        Sunday, so main returns [] — no session, nowhere for the batch."""
        mon = _next_weekday("monday")
        plan_id, dates = _plan(
            stub_model, mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "S2", "S3", "S4", "S5", "S6"],
        )
        sunday_before = (datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()
        session = _session(plan_id, sunday_before)
        assert session is not None, "the Sunday before the week has no session"
        assert session["weekday"] == "Sunday"
        item = next(i for i in session["items"] if i["title"] == "Chili")
        assert item["line"] == "For Monday and Tuesday’s lunches."

    def test_the_midweek_second_prep_day_the_lunches_step_added_gets_one_too(
            self, two_adults, stub_model):
        """CATCH — the card's fourth criterion. The Wednesday prep day here is
        NOT one of the household's standing rhythm prep days: it is one this
        week's answer added (plan-week's ensurePrepReach does exactly that
        when a prepped lunch is out of a prep day's reach). main maps only
        rhythm.prep_days onto the period, so Wednesday has no session."""
        tools.set_prep_days(["sunday"])
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 4: "prepped", 5: "prepped"},
            ["sunday", "wednesday"],
            lunches=["S0", "Chili", "S2", "S3", "Curry", "Soup", "S6"],
        )
        wednesday = dates[3]
        session = _session(plan_id, wednesday)
        assert session is not None, "the midweek prep day has no session"
        assert session["weekday"] == "Wednesday"
        item = next(i for i in session["items"] if i["title"] == "Curry")
        assert item["line"] == "For Thursday and Friday’s lunches."
        # And Sunday's own batch is still Sunday's.
        assert [i["title"] for i in _session(plan_id, dates[0])["items"] if i["line"]] == ["Chili"]

    def test_a_batch_shows_exactly_once_even_when_its_prep_day_is_its_cook_day(
            self, two_adults, stub_model):
        """RED ON MAIN, NOT FOR ITS OWN CLAIM — it dies on `session["items"]`
        with no session at all, and what it is named for is the DUPLICATE: a
        Wednesday prep day whose own Wednesday lunch is prepped puts the cook
        and the prep on ONE day, so the batch would be gathered twice, once by
        _prepped_lunch_items and once by _cook_ahead_items, which reads the
        source's own date. That half is pinned by the mutation that drops
        prepped_cook_ids (2 red), not by this number."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {3: "prepped", 4: "prepped"}, ["wednesday"],
            lunches=["S0", "S1", "S2", "Chili", "Soup", "S5", "S6"],
        )
        session = _session(plan_id, dates[3])
        assert [i["title"] for i in session["items"]] == ["Chili"], session["items"]
        assert session["items"][0]["line"] == "For Wednesday and Thursday’s lunches."

    def test_the_one_lunch_batch_is_a_session_item_too(self, two_adults, stub_model):
        """RED ON MAIN on the missing session rather than on its own claim.
        The claim: a single prepped lunch is no chain at all — no target, no
        make_double_for — so nothing about it reaches plan_leftover_chains. It
        is still work on the prep day, which is why prepped_batches reads the
        ENTRIES and not the chains; the mutation that points it at the chains
        instead is what pins this one."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "S2", "S3", "S4", "S5", "S6"],
        )
        item = next(i for i in _session(plan_id, dates[0])["items"] if i["title"] == "Chili")
        assert item["line"] == "For Monday’s lunch."
        assert item["covers"] == [dates[1]]

    def test_the_line_and_the_cooks_own_reasoning_are_the_same_sentence(
            self, two_adults, stub_model):
        """GUARD on the one phrase in two places. Red on main, and NOT
        evidence: it dies on the missing session. Pinned by the mutation that
        gives _prepped_lunch_items a phrase of its own."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        item = next(i for i in _session(plan_id, dates[0])["items"] if i["title"] == "Chili")
        conn = get_conn()
        reasoning = conn.execute(
            "SELECT reasoning FROM meal_plan_entries WHERE id = ?", (item["entry_id"],)
        ).fetchone()["reasoning"]
        conn.close()
        assert reasoning == "Cook this Sunday for Monday and Tuesday’s lunches."
        assert item["line"].rstrip(".").lower().endswith(
            reasoning.rstrip(".").split(" for ", 1)[1].lower()
        )


# ==========================================================================
# 2. The lunch days stop reading as the cook
# ==========================================================================

class TestTheLunchDaysAreNotTheCook:
    def test_the_cook_card_says_which_day_it_was_prepped_on(self, two_adults, stub_model):
        """CATCH on main's own field: `prepped_ahead` is absent there, so the
        Monday card is an ordinary cook with a start-by time."""
        mon = _next_weekday("monday")
        plan_id, dates = _plan(
            stub_model, mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "S2", "S3", "S4", "S5", "S6"],
        )
        sunday_before = (datetime.date.fromisoformat(dates[0]) - datetime.timedelta(days=1)).isoformat()
        cards = _lunch_cards(plan_id)
        assert cards[dates[0]]["prepped_ahead"] == {
            "date": sunday_before, "weekday": "Sunday", "lunches": [dates[0], dates[1]],
        }
        # It is still the cook: the recipe, the entry and the ingredients stay,
        # because the prep session's own item opens exactly this card.
        assert cards[dates[0]]["ingredients"]
        assert cards[dates[0]]["is_leftovers"] in (False, None)

    def test_the_later_lunches_headline_points_at_the_prep_day_not_the_cooks_day(
            self, two_adults, stub_model):
        """CATCH. main says "Made ahead — Monday's Chili" on Tuesday, which
        after this change names a day the app tells them nothing happens on."""
        mon = _next_weekday("monday")
        plan_id, dates = _plan(
            stub_model, mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "S2", "S3", "S4", "S5", "S6"],
        )
        card = _lunch_cards(plan_id)[dates[1]]
        assert card["is_leftovers"] is True
        assert card["leftovers_headline"] == "Made ahead — Sunday’s Chili"
        # leftovers_from still names the ENTRY it reheats — the row really is
        # on Monday, and a label must not make the data lie about that.
        assert card["leftovers_from"]["date"] == dates[0]

    def test_a_prep_day_that_is_the_cook_day_changes_nothing_about_the_card(
            self, two_adults, stub_model):
        """GUARD. A Wednesday prep for Wednesday's own lunch is an ordinary
        cook that day, so there is nothing to move and nothing to say. Pinned
        by the mutation that drops the prep_date < cook_date test."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {3: "prepped", 4: "prepped"}, ["wednesday"],
            lunches=["S0", "S1", "S2", "Chili", "Soup", "S5", "S6"],
        )
        assert _lunch_cards(plan_id)[dates[3]].get("prepped_ahead") is None


# ==========================================================================
# 3. Ticking it on the prep day counts once
# ==========================================================================

class TestTickingItCountsOnce:
    def test_the_session_item_is_the_cooks_own_entry_and_three_ticks_deplete_once(
            self, two_adults, stub_model):
        """The card's third criterion. RED ON MAIN on the missing item, not on
        the counting claim — the counting half is check_off_meal's own
        inventory_depleted_at claim (2026-09-13) and is green either way. What
        this really pins is that the session's item carries the COOK's entry_id,
        so the tick is that one write and not a second fact; the mutation that
        points the item at another entry is what bites."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        tools.update_inventory("Chili bits", "add", quantity="10 lb", location="pantry")
        item = next(i for i in _session(plan_id, dates[0])["items"] if i["title"] == "Chili")
        assert item["done"] is False

        def _shelf():
            conn = get_conn()
            row = conn.execute(
                "SELECT quantity FROM inventory_items WHERE LOWER(item) = 'chili bits'"
            ).fetchone()
            conn.close()
            return row["quantity"] if row else None

        def _times():
            conn = get_conn()
            row = conn.execute("SELECT times_cooked FROM recipes WHERE name = 'Chili'").fetchone()
            conn.close()
            return row["times_cooked"]

        assert _times() == 0
        for _ in range(3):
            tools.check_off_meal(item["entry_id"], "done")
        after = _shelf()
        assert _times() == 1, "three ticks cooked it three times"
        assert after not in (None, "10 lb"), f"nothing came off the shelf: {after}"
        conn = get_conn()
        again = conn.execute(
            "SELECT quantity FROM inventory_items WHERE LOWER(item) = 'chili bits'"
        ).fetchone()
        conn.close()
        assert again["quantity"] == after, "a second tick depleted again"
        # And the session says so.
        assert _session(plan_id, dates[0])["items_done"] == 1


# ==========================================================================
# 4. A week that never answered the question
# ==========================================================================

class TestNothingChangesForAWeekThatNeverAnswered:
    def test_no_weekday_lunches_answer_reads_batches_as_nothing(self, two_adults, stub_model):
        """GUARD, and the one this whole change rests on: every new branch is
        gated on derived_from.prep_date, which ONLY
        weekday_lunches.apply_to_plan writes (grep: one call site). Pinned by
        the mutation that makes prepped_batches return the plan's lunches."""
        mon = _next_weekday("monday")
        dates = _dates(mon)
        stub_model(_week(dates, ["Chili", "Chili", "Chili", "C3", "C4", "C5", "C6"]))
        plan_id = agent.generate_weekly_plan(mon.isoformat(), day_count=7,
                                             confirm_takeover=True)["weekly_plan_id"]
        tools.approve_weekly_plan(plan_id)
        assert weekday_lunches.prepped_batches(plan_id) == []
        for card in cooker.get_cooker_view(plan_id)["meals"]:
            assert card.get("prepped_ahead") is None
        for session in prep_sessions.prep_sessions_for_plan(plan_id):
            assert all(i.get("line") is None for i in session["items"])

    def test_a_household_with_no_rhythm_prep_days_still_gets_the_weeks_own(
            self, two_adults, stub_model):
        """CATCH. main returns [] on the rhythm gate before it looks at
        anything: a household that answered the weekday-lunches step without
        ever answering the rhythm question had no session at all."""
        assert prep_sessions.has_prep_days() is False
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        session = _session(plan_id, dates[0])
        assert session is not None
        assert [i["title"] for i in session["items"]] == ["Chili"]

    def test_skip_prep_this_week_still_silences_every_session(self, two_adults, stub_model):
        """GUARD. Red on main on its PRECONDITION (there is no session to
        silence there), so the red is not evidence. "I can't prep this Sunday"
        is a fact about one week and it outranks the answer; pinned by the
        mutation that drops the skip_prep_this_week guard."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        assert _session(plan_id, dates[0]) is not None
        tools.set_skip_prep_this_week(True, plan_id)
        assert prep_sessions.prep_sessions_for_plan(plan_id) == []


# ==========================================================================
# 5. prepped_batches itself
# ==========================================================================

class TestTheReader:
    def test_it_reads_the_plan_as_it_stands_not_the_answer(self, two_adults, stub_model):
        """Red on main only because prepped_batches is not there — the weakest
        kind of red, and said so. The claim, pinned by the mutation that reads
        the intake's own `days` instead of the rows: a day swapped away since
        takes itself out of the batch."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        [batch] = weekday_lunches.prepped_batches(plan_id)
        assert batch["lunch_dates"] == [dates[1], dates[2]]
        tools.swap_meal_in_plan(plan_id, dates[2], "Something Else", slot="lunch")
        [batch] = weekday_lunches.prepped_batches(plan_id)
        assert batch["lunch_dates"] == [dates[1]]

    def test_another_households_batch_is_never_read(self, two_adults, stub_model):
        """GUARD on the isolation. Red on main on its precondition (no such
        function), which is no evidence at all; pinned by the mutation that
        drops household_id() from prepped_batches' query."""
        sun = _next_weekday("sunday")
        plan_id, dates = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        assert len(weekday_lunches.prepped_batches(plan_id)) == 1
        with tools.use_household(2):
            assert weekday_lunches.prepped_batches(plan_id) == []
            assert prep_sessions.prep_sessions_for_plan(plan_id) == []

    def test_a_batch_on_another_live_plan_is_read_into_this_periods_window(
            self, two_adults, stub_model):
        """CATCH. THE shape the household is standing in on the Sunday: this
        week's plan (Mon-Sun) is what the Cook tab resolves to, and next
        week's Chili batch preps on that Sunday. main reads one plan, so the
        Sunday session cannot hold it."""
        this_mon = _next_weekday("monday", weeks_ahead=0)
        next_mon = _next_weekday("monday", weeks_ahead=1)
        this_dates = _dates(this_mon)
        stub_model(_week(this_dates, [f"A{i}" for i in range(7)]))
        this_plan = agent.generate_weekly_plan(this_mon.isoformat(), day_count=7,
                                               confirm_takeover=True)["weekly_plan_id"]
        tools.approve_weekly_plan(this_plan)
        next_plan, next_dates = _plan(
            stub_model, next_mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "B2", "B3", "B4", "B5", "B6"],
        )
        sunday = this_dates[6]
        assert sunday == (datetime.date.fromisoformat(next_dates[0])
                          - datetime.timedelta(days=1)).isoformat()
        session = _session(this_plan, sunday)
        assert session is not None, "the Sunday of this week has no session"
        item = next(i for i in session["items"] if i["title"] == "Chili")
        assert item["line"] == "For Monday and Tuesday’s lunches."
        assert item["entry_id"] is not None

    def test_a_retired_plans_batch_is_not_read_across(self, two_adults, stub_model):
        """GUARD — the same guard get_prep_schedule puts on its own wider
        read. Pinned by the mutation that drops the status filter."""
        this_mon = _next_weekday("monday", weeks_ahead=0)
        next_mon = _next_weekday("monday", weeks_ahead=1)
        this_dates = _dates(this_mon)
        stub_model(_week(this_dates, [f"A{i}" for i in range(7)]))
        this_plan = agent.generate_weekly_plan(this_mon.isoformat(), day_count=7,
                                               confirm_takeover=True)["weekly_plan_id"]
        tools.approve_weekly_plan(this_plan)
        next_plan, _ = _plan(
            stub_model, next_mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "B2", "B3", "B4", "B5", "B6"],
        )
        conn = get_conn()
        conn.execute("UPDATE weekly_plans SET status = 'retired' WHERE id = ?", (next_plan,))
        conn.commit()
        conn.close()
        assert _session(this_plan, this_dates[6]) is None


# ==========================================================================
# 6. The screen
# ==========================================================================

from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SHELL_JS_SRC = (REPO / "static" / "shell.js").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = SHELL_JS_SRC.index(f"function {name}(")
    i = SHELL_JS_SRC.index("{", start)
    depth, j = 0, i
    while True:
        if SHELL_JS_SRC[j] == "{":
            depth += 1
        elif SHELL_JS_SRC[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return SHELL_JS_SRC[start:j + 1]


def _between(a: str, b: str) -> str:
    i = SHELL_JS_SRC.index(a)
    return SHELL_JS_SRC[i:SHELL_JS_SRC.index(b, i)]


_PRELUDE = (
    "function escapeHtml(s){return String(s == null ? '' : s)"
    ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
    "const GRO_ICONS = { chevRight: '<svg data-icon=\"chev\"></svg>' };\n"
    "const COOK_ICONS = { check: '<svg data-icon=\"check\"></svg>' };\n"
    "const REHEAT_ACTION_LABEL = 'Mark eaten';\n"
    "const REHEAT_UNDO_LABEL = 'Mark not eaten';\n"
    "var cookState = { tonightIdx: 0 };\n"
    "function dayName(d, o){ return new Date(d + 'T00:00:00').toLocaleDateString('en-US', o); }\n"
    "function dayNameShort(i){ return new Date(i + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short' }); }\n"
    + _function("addDaysLocal") + "\n"
    + _function("daysBetweenLocal") + "\n"
    + _function("cookMinutesLabel") + "\n"
    + _function("cookCoversLabel") + "\n"
    + _function("cookFocusPrepTasks") + "\n"
    + _function("kitchenLoosePrepTasks") + "\n"
    + _function("cookPreppedAhead") + "\n"
    + _function("kitchenTodayLine") + "\n"
    + _function("kitchenTodayRows") + "\n"
    + _function("kitchenTodayRowHtml") + "\n"
    + _function("kitchenSubtitle") + "\n"
    + _function("cookSessionItemHtml") + "\n"
    + _function("cookResolveFocusIndex") + "\n"
    + _function("cookMealKey") + "\n"
    + _between("  var COOK_DISH_WORDS = [", "  function renderKitchen()")
)

MON, TUE = "2026-09-28", "2026-09-29"
SUN_BEFORE = "2026-09-27"
_PREPPED = {"date": SUN_BEFORE, "weekday": "Sunday", "lunches": [MON, TUE]}


def _meal(**kw):
    base = {"entry_id": 1, "date": MON, "slot": "lunch", "meal": "Chili",
            "cooked_status": "pending", "prep_time_minutes": 10,
            "cook_time_minutes": 30, "ingredients": [], "instructions": []}
    base.update(kw)
    return base


_COOK_MOVE = {"kind": "cook", "entry_id": 1, "done": False,
              "chips": ["Start by 11:20"], "time_label": "11:50", "detail": "lunch · 40 min"}


def _node(script: str):
    res = nodeharness.run_node(_PRELUDE + script, timeout=30)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _root(meals, moves=(), data=None, iso=MON, tonight_idx=0):
    return _node(
        "cookState.tonightIdx = " + json.dumps(tonight_idx) + ";\n"
        "var meals = " + json.dumps(meals) + ", moves = " + json.dumps(list(moves)) + ";\n"
        "var data = " + json.dumps(data or {"prep_sessions": [], "prep_tasks": [], "meals": meals}) + ";\n"
        "var rows = kitchenTodayRows(meals, moves, " + json.dumps(iso) + ");\n"
        "console.log(JSON.stringify({\n"
        "  rows: rows.map(function (r) { return { idx: r.idx, badge: r.badge, line: r.line,"
        "      title: r.title, prepped: r.prepped || null }; }),\n"
        "  subtitle: kitchenSubtitle(rows, meals, " + json.dumps(iso) + "),\n"
        "  card: kitchenCookingTodayHtml(rows, meals, " + json.dumps(iso) + ", data),\n"
        "  dock: cookRootDockHtml(cookTonightRow(rows))\n"
        "}));\n"
    )


@_needs_node
class TestTheScreen:
    def test_the_row_reads_prepped_sunday_and_wears_the_prepped_badge(self):
        """CATCH. main reads no such field: the row is "Cook" with
        "start by 11:20" on it."""
        out = _root([_meal(prepped_ahead=_PREPPED)], [_COOK_MOVE])
        [row] = out["rows"]
        assert row["badge"] == "Prepped"
        assert row["line"] == "Prepped Sunday"
        assert row["title"] == "Chili", "the dish keeps its name — it is still what is eaten"
        assert "start by" not in out["card"].lower()

    def test_it_is_not_counted_as_a_cook_left_to_do(self):
        """CATCH — "not shown as a cook", in the band's own words. main says
        "1 cook today"."""
        out = _root([_meal(prepped_ahead=_PREPPED)], [_COOK_MOVE])
        assert out["subtitle"] == "nothing left to cook today"
        # A real cook beside it is still counted.
        pair = [_meal(prepped_ahead=_PREPPED), _meal(entry_id=2, slot="dinner", meal="Tacos")]
        out = _root(pair, [_COOK_MOVE])
        assert out["subtitle"] == "1 cook tonight"

    def test_with_nothing_pinned_the_card_is_the_real_cook_not_the_prepped_lunch(self):
        """CATCH on cookTonightRow's new preference. With no tonightIdx to
        follow the card is what to DO now, and there is nothing to do about
        food that is already made."""
        pair = [_meal(prepped_ahead=_PREPPED), _meal(entry_id=2, slot="dinner", meal="Tacos")]
        out = _root(pair, [_COOK_MOVE], tonight_idx=None)
        assert "Tacos" in out["card"].split('cook-tonight-dish">')[1][:40]
        # The prepped lunch is still there, as a row with its own tick.
        assert "Prepped Sunday" in out["card"] and 'data-cook="check-meal"' in out["card"]

    def test_a_pinned_prepped_lunch_is_still_an_honest_card(self):
        """CATCH — measured red on main on its own assertion. Also a behaviour
        worth writing down rather than changing: cookTonightIndex is untouched,
        so at eleven in the morning the tab still pins the lunch as tonight.
        The card then reads what is true — prepped, ready, here is the tick —
        rather than a start time."""
        pair = [_meal(prepped_ahead=_PREPPED), _meal(entry_id=2, slot="dinner", meal="Tacos")]
        out = _root(pair, [_COOK_MOVE], tonight_idx=0)
        assert "Chili" in out["card"].split('cook-tonight-dish">')[1][:40]
        assert "Prepped Sunday \u2014 ready to go." in out["card"]
        assert ">Mark it cooked<" in out["dock"]

    def test_the_card_says_prepped_sunday_and_offers_no_start_time(self):
        """CATCH. main's card carries Start 11:20 / On the table 11:50 and the
        note "Nothing to thaw or prep ahead."."""
        out = _root([_meal(prepped_ahead=_PREPPED)], [_COOK_MOVE])
        assert "Prepped Sunday — ready to go." in out["card"]
        assert "cook-tonight-tile-value" not in out["card"], "a start time for a cook already done"
        assert "11:20" not in out["card"]

    def test_the_dock_is_the_tick_not_start_cooking(self):
        """CATCH. The card's own row is not drawn under it
        (kitchenCookingTodayHtml), so the dock is the ONLY tick a prepped
        card has — an empty dock would leave the meal with nowhere to tick
        it, and "Start cooking" would be the app asking for a cook it has
        already asked for on the prep day."""
        out = _root([_meal(prepped_ahead=_PREPPED)], [_COOK_MOVE])
        assert 'data-cook="start-tonight"' not in out["dock"]
        assert 'data-cook="check-meal"' in out["dock"] and 'data-entry-id="1"' in out["dock"]
        assert 'data-next="done"' in out["dock"]
        # The aria-label is what tells cookCheckMeal this is a cook being
        # logged rather than one being put back.
        assert 'aria-label="Mark cooked"' in out["dock"]
        assert ">Mark it cooked<" in out["dock"]

    def test_a_done_prepped_lunch_reads_cooked_and_has_no_dock(self):
        """GUARD on the existing done rule — pinned by the mutation that makes
        the prepped branch run before the done one."""
        out = _root([_meal(prepped_ahead=_PREPPED, cooked_status="done")], [_COOK_MOVE])
        assert out["rows"][0]["badge"] == "cooked"
        assert out["dock"] == ""

    def test_a_prep_day_on_the_meals_own_day_is_not_read_as_prepped_ahead(self):
        """GUARD on cookPreppedAhead's one clause. A Wednesday prep for
        Wednesday's own lunch is an ordinary cook that day. Pinned by the
        mutation that drops the date comparison."""
        same = {"date": MON, "weekday": "Monday", "lunches": [MON]}
        out = _root([_meal(prepped_ahead=same)], [_COOK_MOVE])
        assert out["rows"][0]["badge"] == "Cook"
        assert out["rows"][0]["line"] == "start by 11:20"
        assert 'data-cook="start-tonight"' in out["dock"]

    def test_a_meal_with_no_prepped_ahead_is_exactly_what_it_was(self):
        """GUARD — the byte-identical criterion, at the row level. Pinned by
        the mutation that makes cookPreppedAhead answer for every meal."""
        out = _root([_meal()], [_COOK_MOVE])
        assert out["rows"][0]["badge"] == "Cook"
        assert out["rows"][0]["line"] == "start by 11:20"
        assert out["subtitle"] == "1 cook today"
        assert 'data-cook="start-tonight"' in out["dock"]

    def test_a_reheat_of_a_prepped_batch_says_the_prep_day_was_the_cook_day(self):
        """CATCH. The later lunches' own card: main's note reads "Reheat —
        cooked on Monday", which names the day nothing is cooked on now."""
        out = _node(
            "var meals = " + json.dumps([_meal(
                entry_id=3, date=TUE, is_leftovers=True, prepped_ahead=_PREPPED,
                leftovers_headline="Made ahead — Sunday’s Chili",
                leftovers_from={"entry_id": 1, "date": MON, "slot": "lunch", "meal": "Chili"},
            )]) + ";\n"
            "console.log(JSON.stringify(cookTonightNote({prep_tasks: [], prep_sessions: []}, meals[0])));\n"
        )
        assert out == "Reheat — cooked on Sunday."

    def test_the_session_item_carries_the_line_under_the_row(self):
        """CATCH. main's cookSessionItemHtml renders no `line` at all."""
        item = {"kind": "cook_ahead", "prep_task_id": None, "entry_id": 1, "title": "Chili",
                "feeds": "Chili", "line": "For Monday and Tuesday’s lunches.",
                "done": False, "covers": [MON, TUE], "minutes": 40}
        html = _node(
            "var meals = " + json.dumps([_meal()]) + ";\n"
            "console.log(JSON.stringify(cookSessionItemHtml(" + json.dumps(item) + ", meals)));\n"
        )
        assert '<p class="cook-week-sub">For Monday and Tuesday’s lunches.</p>' in html
        # The dish is not said twice: the badge is dropped when it matches.
        assert "cook-badge" not in html
        # And the box opens the recipe, as every cook_ahead item's does.
        assert 'data-cook="focus"' in html

    def test_a_batch_on_another_plan_ticks_the_entry_rather_than_opening_nothing(self):
        """CATCH on the cross-plan case. The cook entry is not one of THIS
        plan's cards, so there is no recipe index to open — but it is still one
        entry and one cooked_status, so the box is the same check_off_meal
        write kitchenTodayRowHtml makes, not a dead control."""
        item = {"kind": "cook_ahead", "prep_task_id": None, "entry_id": 99, "title": "Chili",
                "feeds": "Chili", "line": "For Monday’s lunch.", "done": False,
                "covers": [MON], "minutes": 40}
        html = _node(
            "var meals = " + json.dumps([_meal()]) + ";\n"
            "console.log(JSON.stringify(cookSessionItemHtml(" + json.dumps(item) + ", meals)));\n"
        )
        assert 'data-cook="check-meal"' in html and 'data-entry-id="99"' in html
        assert 'aria-label="Mark cooked"' in html
        assert 'data-cook="focus"' not in html

    def test_one_place_asks_whether_a_meal_was_prepped_ahead(self):
        """SOURCE MARKER on the rule living once, red on main because the
        function is new — not a behaviour catch. Four decisions read it (the
        row's badge, its line, the card's note, the dock) and two copies of a
        date comparison is this codebase's named bug generator."""
        assert SHELL_JS_SRC.count("function cookPreppedAhead(") == 1
        # Nothing else reads the field by hand. Comment-stripped, because a
        # claim a comment can satisfy is not a claim (CLAUDE.md records this
        # exact test shape going toothless three times).
        code = "\n".join(
            line for line in SHELL_JS_SRC.splitlines() if not line.strip().startswith("//")
        )
        assert code.count("prepped_ahead") == 1, "prepped_ahead is read outside cookPreppedAhead"


# ==========================================================================
# 7. What is NOT fixed — characterised so nobody reports it as new
# ==========================================================================

class TestWhatIsLeft:
    def test_nows_timeline_still_calls_the_prepped_lunch_a_cook(self, two_adults, stub_model):
        """CHARACTERISATION, green on main and green here, and deliberately
        NOT fixed: app/tools/moves.py builds the Monday lunch as a `cook`
        move with "Start by noon" and "Cook this", so Now and Cook disagree
        about the same meal.

        Left alone because moves.py is also what the morning text is built
        from (digest.build_morning_text reads today_moves and nothing else),
        so changing the words changes an outbound SMS — and what a "prepped
        Sunday" move should SAY on Now, and whether it is tickable there, is
        a product decision Emily has not made. Invert this when she does."""
        mon = _next_weekday("monday")
        plan_id, dates = _plan(
            stub_model, mon, {0: "prepped", 1: "prepped"}, ["sunday"],
            lunches=["Chili", "Soup", "L2", "L3", "L4", "L5", "L6"],
        )
        from app.tools import moves as _moves
        day = _moves.moves_for_day(
            dates[0], now=datetime.datetime.fromisoformat(dates[0] + "T09:00:00")
        )
        lunch = next(m for m in day if m.get("kind") == "cook" and m["title"] == "Chili")
        assert lunch["kind"] == "cook", "if this is no longer a cook, Now was taught the rule"
        assert any(c.startswith("Start by") for c in lunch["chips"])

    def test_a_past_prep_days_session_is_not_a_get_ready_row_which_is_why_the_tick_stays(
            self, two_adults, stub_model):
        """CHARACTERISATION of an existing rule (cookGetReadyMoves filters
        sessions to `s.date >= todayIso`), and the reason the prepped lunch
        keeps its tick on its own day: once the prep day has gone by there is
        no row on the root that opens its session, so the lunch day's tick is
        the only way left to log it."""
        assert "s.date >= todayIso" in _function("cookGetReadyMoves")


# ==========================================================================
# 8. What it costs
# ==========================================================================

class TestWhatItCosts:
    def test_the_prepped_batch_read_is_one_connection_however_big_the_week(
            self, two_adults, stub_model):
        """GUARD on the shape of the cost rather than on a number.

        MEASURED 2026-09-26: get_cooker_view goes 108 -> 110 connections for
        a week with NO answer and 108 -> 112 for one with a batch, because
        prepped_batches is read once by prep_sessions_for_plan and once by
        cooker._apply_prepped_lunches. Constant per payload, never per day,
        per meal or per batch — which is what this asserts, since the number
        itself will move the next time anything else on that payload does.

        What this does NOT claim: prep_sessions_for_plan as a whole grows with
        the number of SESSIONS (13 connections for one session, 20 for two on
        a fortnight, measured), and that is pre-existing — _cook_ahead_items
        has always re-read plan_leftover_chains and list_recipes once per prep
        date. _prepped_lunch_items inherits that shape and adds nothing to it:
        it reads the recipes lazily, only on a date that actually has a batch.

        Counted at sqlite3.connect, because a module-level get_conn patch
        cannot see a function-local import (_shared.py has exactly that
        shape)."""
        import sqlite3
        tools.set_prep_days(["sunday", "wednesday"])

        def _count(fn):
            real, seen = sqlite3.connect, []
            sqlite3.connect = lambda *a, **k: (seen.append(1), real(*a, **k))[1]
            try:
                fn()
            finally:
                sqlite3.connect = real
            return len(seen)

        sun = _next_weekday("sunday")
        one, _ = _plan(
            stub_model, sun, {1: "prepped", 2: "prepped"}, ["sunday"],
            lunches=["S0", "Chili", "Soup", "S3", "S4", "S5", "S6"],
        )
        period = [sun.isoformat(), (sun + datetime.timedelta(days=6)).isoformat()]
        assert _count(lambda: weekday_lunches.prepped_batches(one)) == 1
        assert _count(lambda: weekday_lunches.prepped_batches(one, window=period)) == 1

        # Three batches over a fortnight, two prep days, twice the days.
        sun2 = _next_weekday("sunday", weeks_ahead=3)
        lunches = ["A", "Chili", "Soup", "B", "Curry", "Stew", "C",
                   "D", "Dal", "Dal2", "E", "F", "G", "H"]
        many, dates = _plan(
            stub_model, sun2,
            {1: "prepped", 2: "prepped", 4: "prepped", 5: "prepped", 8: "prepped", 9: "prepped"},
            ["sunday", "wednesday"], lunches=lunches, day_count=14,
        )
        assert len(weekday_lunches.prepped_batches(many)) >= 3, "the seed grew no batches"
        assert _count(lambda: weekday_lunches.prepped_batches(many)) == 1
        assert _count(lambda: weekday_lunches.prepped_batches(many, window=[dates[0], dates[-1]])) == 1
