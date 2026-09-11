"""
Replanning over a running week asks first — Loop Board "Replanning over a
running week takes it over with no confirmation."

FOUND 2026-09-11 reviewing the ask-sheet named-intents branch, pre-existing
on main: asking chat to plan a period took over every day it overlapped —
meals gone, grocery lines reversed — and nothing confirmed first.
retire_overlapping_plans had no exemption for an APPROVED plan, and the
chat tool had no way to carry a "yes". "Plan the rest of my week" is now one
tap on the ask sheet, so a household mid-week reaches this in one gesture.

Emily's decision (2026-09-11, live): taking the days over is still the
household's rule; the fix is a confirmation step BEFORE it happens, not
removing the take-over. So:

1.  A chat planning request that would replace days of an APPROVED plan
    does NOT plan. It comes back `needs_confirmation`, naming the specific
    days and meals that would go, so the assistant can ask in plain words.
2.  The same request with `confirm_takeover=True` proceeds exactly as
    before — the household said yes, and the days are taken over.
3.  A DRAFT is still taken over without asking. Drafts are drafts; nothing
    of theirs has reached the shopping list.
4.  The plan-week screen's own routes keep working. That screen already
    tells the household before the questions ("… is already approved and on
    your shopping list. If we re-plan it …"), so it passes the flag itself
    rather than asking twice.

The takeover mechanics themselves (which days a plan keeps, grocery
reconciliation) are pinned in test_planning_periods.py and are not
re-tested here.
"""
from __future__ import annotations

import datetime
import inspect

import pytest

from app import agent, tools
from app.db import get_conn


# ---------- helpers (same shapes as test_planning_periods.py) ----------

def _monday(offset_weeks: int = 1) -> str:
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _full_period(start: str, count: int, meal: str = "Bean Chili") -> list[dict]:
    return [
        {"date": day, "slot": slot, "meal_name": meal, "is_new_recipe": False,
         "reasoning": "fits the period"}
        for day in tools.period_dates(start, count)
        for slot in tools.WEEK_SLOTS
    ]


def _plan_row(plan_id: int) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM weekly_plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    return dict(row)


def _dates_on(plan_id: int) -> set[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT date FROM meal_plan_entries WHERE weekly_plan_id = ? "
        "AND component_category IS NULL",
        (plan_id,),
    ).fetchall()
    conn.close()
    return {r["date"] for r in rows}


def _plan_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM weekly_plans").fetchone()["n"]
    conn.close()
    return n


def _needed_grocery_items() -> set[str]:
    conn = get_conn()
    rows = conn.execute("SELECT item FROM grocery_items WHERE status = 'needed'").fetchall()
    conn.close()
    return {r["item"] for r in rows}


def _weekday(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime("%A")


@pytest.fixture
def recipes():
    tools.add_recipe("Bean Chili", ingredients=[{"item": "black beans", "qty": "2 tins"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Salmon", ingredients=[{"item": "salmon fillets", "qty": "4"}],
                     prep_time_minutes=10, cook_time_minutes=20)
    tools.add_recipe("Katsu", ingredients=[{"item": "panko", "qty": "1 bag"}],
                     prep_time_minutes=10, cook_time_minutes=20)


@pytest.fixture
def stub_model(monkeypatch):
    def _stub(days):
        monkeypatch.setattr(agent, "generate_weekly_plan_llm", lambda context: days)
    return _stub


def _chat_plan(**tool_input):
    """
    Exactly what the assistant's tool call does: run_agent_turn looks the
    tool up in TOOL_FUNCTIONS and calls it with the model's input as
    keyword arguments (app/agent.py, the `fn(**block.input)` line). Going
    through the same table keeps this honest about what chat can reach.
    """
    return agent.TOOL_FUNCTIONS["generate_weekly_plan"](**tool_input)


def _approved_week(stub_model, meal="Bean Chili", start=None, days=7):
    """An APPROVED plan — its groceries are on the list, it is being cooked from."""
    start = start or _monday()
    stub_model(_full_period(start, days, meal=meal))
    # No flag needed: nothing is in the way of it yet.
    plan = agent.generate_weekly_plan(start, day_count=days, period_start=start)
    approved = tools.approve_weekly_plan(plan["weekly_plan_id"])
    assert approved["status"] == "approved"
    return plan


# ---------- 1. the reproduction ----------

class TestChatAsksBeforeReplanningAnApprovedWeek:

    def test_an_approved_weeks_days_are_not_taken_over_without_asking(self, recipes, stub_model):
        # FAILS ON MAIN: the chat request generated straight through, the
        # approved plan lost Thursday-Sunday and their grocery lines came off.
        week = _monday()
        approved = _approved_week(stub_model)
        before_dates = _dates_on(approved["weekly_plan_id"])
        before_groceries = _needed_grocery_items()
        plans_before = _plan_count()
        thursday = tools.period_dates(week, 7)[3]

        stub_model(_full_period(thursday, 4, meal="Katsu"))
        result = _chat_plan(week_start_date=thursday, day_count=4)

        assert result["status"] == "needs_confirmation"
        assert "weekly_plan_id" not in result or result.get("weekly_plan_id") is None
        # Nothing was planned and nothing was destroyed.
        assert _plan_count() == plans_before
        assert _dates_on(approved["weekly_plan_id"]) == before_dates
        assert _plan_row(approved["weekly_plan_id"])["status"] == "approved"
        assert tools.plan_period(_plan_row(approved["weekly_plan_id"])) == (week, 7)
        assert _needed_grocery_items() == before_groceries

    def test_the_result_names_the_days_and_meals_that_would_go(self, recipes, stub_model):
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        # Give Thursday a different dinner so the message has to name meals
        # per day rather than one dish for the lot.
        conn = get_conn()
        salmon = conn.execute("SELECT id FROM recipes WHERE name = 'Salmon'").fetchone()["id"]
        conn.execute(
            "UPDATE meal_plan_entries SET recipe_id = ? WHERE date = ? AND slot = 'dinner'",
            (salmon, days[3]),
        )
        conn.commit()
        conn.close()

        result = _chat_plan(week_start_date=days[3], day_count=4)

        assert result["status"] == "needs_confirmation"
        assert [d["date"] for d in result["days"]] == days[3:]
        assert result["days"][0]["weekday"] == _weekday(days[3])
        thursday_dinner = [m for m in result["days"][0]["meals"] if m["slot"] == "dinner"]
        assert thursday_dinner == [{"slot": "dinner", "meal_name": "Salmon"}]
        # The sentence the assistant can say as-is: the span, the dinners,
        # the grocery cost, and the way out — a question, not a verdict.
        note = result["note"]
        assert _weekday(days[3]) in note and _weekday(days[6]) in note
        assert "Salmon" in note and "Bean Chili" in note
        assert "shopping list" in note
        assert note.rstrip().endswith("?")
        assert result["meal_count"] == 4 * len(tools.WEEK_SLOTS)
        # Calm and plain: no exclamation marks in a message about losing days.
        assert "!" not in note

    def test_a_day_of_the_approved_plan_outside_the_request_is_not_named(self, recipes, stub_model):
        # Only the days that would actually be replaced, not the whole plan.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        result = _chat_plan(week_start_date=days[5], day_count=2)   # Sat-Sun
        assert result["status"] == "needs_confirmation"
        assert [d["date"] for d in result["days"]] == days[5:]
        assert result["orphaned_dates"] == []
        assert _weekday(days[0]) not in result["note"]

    def test_days_the_takeover_would_orphan_are_named_too(self, recipes, stub_model):
        # A period strictly inside an approved week: the old plan keeps the
        # longer side and the shorter side is lost WITHOUT being replanned
        # (see test_planning_periods). That is the worst case for a
        # household, so the confirmation has to say it.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        result = _chat_plan(week_start_date=days[2], day_count=2)   # Wed-Thu
        assert result["status"] == "needs_confirmation"
        assert result["orphaned_dates"] == days[:2]                   # Mon-Tue lost, not replanned
        assert [d["date"] for d in result["days"]] == days[:4]        # Mon-Thu all go
        assert _weekday(days[0]) in result["note"]


    def test_the_grocery_count_is_read_off_the_list_and_matches_the_takeover(self, recipes, stub_model):
        # The card's own criterion: the number in the question is what
        # actually happens, never an estimate. So the count the question
        # carries must equal what the confirmed takeover then reports.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        asked = _chat_plan(week_start_date=days[3], day_count=4)
        assert asked["status"] == "needs_confirmation"
        assert asked["grocery_line_count"] >= 1
        assert f"{asked['grocery_line_count']} thing" in asked["note"] or asked["grocery_line_count"] == 1

        stub_model(_full_period(days[3], 4, meal="Katsu"))
        done = _chat_plan(week_start_date=days[3], day_count=4, confirm_takeover=True)
        took = done["took_over"]
        # took_over reports per meal reversed; the question counts LINES.
        touched = set(took["grocery_removed"]) | set(took["grocery_trimmed"])
        assert asked["grocery_line_count"] == len(touched)

    def test_a_line_already_bought_is_not_counted_and_the_sentence_says_so(self, recipes, stub_model):
        # Same rule as the takeover itself: bought and in-cart lines are
        # left alone, so they are not something the household would lose.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        for item in tools.list_grocery_list(status="needed"):
            tools.mark_grocery_item(item["id"], "purchased")
        result = _chat_plan(week_start_date=days[3], day_count=4)
        assert result["status"] == "needs_confirmation"
        assert result["grocery_line_count"] == 0
        assert result["grocery_bought_line_count"] >= 1
        note = result["note"]
        assert "all bought already" in note
        assert "Bean Chili" in note                      # the meals still go, and are still named
        assert "nothing would be lost" not in note       # something IS lost: the meals
        assert note.rstrip().endswith("Go ahead?")

    def test_days_nobody_is_home_are_not_described_as_bought(self, recipes, stub_model):
        # Review found this: an approved week whose overlapping days are all
        # "out" (planned_empty) also has zero needed lines, and the first
        # version read that zero as "it's all bought already". Nothing was
        # ever planned or bought for those days, and the sentence says so.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        conn = get_conn()
        placeholders = ",".join("?" * 4)
        conn.execute(
            f"DELETE FROM meal_plan_grocery_links WHERE meal_plan_entry_id IN "
            f"(SELECT id FROM meal_plan_entries WHERE date IN ({placeholders}))",
            tuple(days[3:]),
        )
        conn.execute(
            f"UPDATE meal_plan_entries SET slot_state = 'planned_empty', recipe_id = NULL, "
            f"reasoning = 'nobody home' WHERE date IN ({placeholders})",
            tuple(days[3:]),
        )
        conn.commit()
        conn.close()

        result = _chat_plan(week_start_date=days[3], day_count=4)
        assert result["status"] == "needs_confirmation"
        assert result["meal_count"] == 0
        assert result["grocery_line_count"] == 0
        assert result["grocery_bought_line_count"] == 0
        note = result["note"]
        assert "bought" not in note
        assert "Nothing's planned for" in note and _weekday(days[3]) in note
        assert "nothing would be lost" in note
        assert "Bean Chili" not in note
        assert note.rstrip().endswith("Go ahead?")

    def test_meals_with_nothing_on_the_list_are_neither_bought_nor_lost(self, recipes, stub_model):
        # The third zero: real meals whose ingredients never reached the
        # list (already in the pantry, say). The meals go; the list is
        # untouched; neither "bought" nor "nothing lost" would be true.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        conn = get_conn()
        conn.execute("DELETE FROM meal_plan_grocery_links")
        conn.commit()
        conn.close()
        result = _chat_plan(week_start_date=days[3], day_count=4)
        note = result["note"]
        assert result["grocery_line_count"] == 0 and result["grocery_bought_line_count"] == 0
        assert "Nothing on the shopping list changes" in note
        assert "bought" not in note and "nothing would be lost" not in note
        assert "Bean Chili" in note

    def test_no_dangling_dash_in_any_shape_of_the_sentence(self, recipes, stub_model):
        # The dinner names are set off by dashes that lead INTO the
        # shopping clause; when that clause is a separate sentence there is
        # no closing dash to strand ("— Bean Chili —." was the bug).
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        notes = [_chat_plan(week_start_date=days[3], day_count=4)["note"]]
        for item in tools.list_grocery_list(status="needed"):
            tools.mark_grocery_item(item["id"], "purchased")
        notes.append(_chat_plan(week_start_date=days[3], day_count=4)["note"])
        conn = get_conn()
        conn.execute("DELETE FROM meal_plan_grocery_links")
        conn.commit()
        conn.close()
        notes.append(_chat_plan(week_start_date=days[3], day_count=4)["note"])
        for note in notes:
            assert "—." not in note, note
            assert "— ." not in note, note
            assert "  " not in note, note
            assert "Bean Chili" in note


# ---------- 2. a confirmed request proceeds as before ----------

class TestAConfirmedRequestProceeds:

    def test_confirm_takeover_true_replaces_the_days(self, recipes, stub_model):
        week = _monday()
        approved = _approved_week(stub_model)
        thursday = tools.period_dates(week, 7)[3]

        stub_model(_full_period(thursday, 4, meal="Katsu"))
        result = _chat_plan(week_start_date=thursday, day_count=4, confirm_takeover=True)

        assert result.get("status") != "needs_confirmation"
        assert result["weekly_plan_id"] != approved["weekly_plan_id"]
        assert result["took_over"]["shortened_plan_ids"] == [approved["weekly_plan_id"]]
        assert tools.plan_period(_plan_row(approved["weekly_plan_id"])) == (week, 3)
        assert _dates_on(approved["weekly_plan_id"]) == set(tools.period_dates(week, 3))

    def test_no_overlap_means_no_question(self, recipes, stub_model):
        # The flag is only about approved days in the way; a period that
        # touches nothing is planned straight away, flag or no flag.
        _approved_week(stub_model)
        next_week = _monday(2)
        stub_model(_full_period(next_week, 7, meal="Katsu"))
        result = _chat_plan(week_start_date=next_week, day_count=7)
        assert result.get("status") != "needs_confirmation"
        assert result["took_over"]["retired_plan_ids"] == []
        assert result["took_over"]["shortened_plan_ids"] == []


# ---------- 3. drafts are still taken over silently ----------

class TestDraftsAreStillTakenOver:

    def test_a_draft_is_replaced_without_asking(self, recipes, stub_model):
        week = _monday()
        stub_model(_full_period(week, 7))
        draft = agent.generate_weekly_plan(week, day_count=7, period_start=week)
        assert _plan_row(draft["weekly_plan_id"])["status"] == "draft"
        thursday = tools.period_dates(week, 7)[3]

        stub_model(_full_period(thursday, 4, meal="Katsu"))
        result = _chat_plan(week_start_date=thursday, day_count=4)

        assert result.get("status") != "needs_confirmation"
        assert result["took_over"]["shortened_plan_ids"] == [draft["weekly_plan_id"]]

    def test_only_the_approved_plan_counts_when_both_are_in_the_way(self, recipes, stub_model):
        # An approved week and, later, a draft for the following days. A
        # request across both asks about the approved one's days only.
        week = _monday()
        days = tools.period_dates(week, 7)
        _approved_week(stub_model)
        next_monday = _monday(2)
        stub_model(_full_period(next_monday, 7, meal="Katsu"))
        agent.generate_weekly_plan(next_monday, day_count=7, period_start=next_monday)

        result = _chat_plan(week_start_date=days[5], day_count=4)   # Sat, Sun, Mon, Tue
        assert result["status"] == "needs_confirmation"
        assert [d["date"] for d in result["days"]] == days[5:]


# ---------- 4. the plan-week screen's own routes keep working ----------

class TestThePlanWeekScreenIsNotAskedTwice:

    def test_the_generate_route_still_replans_an_approved_week(self, recipes, stub_model, signed_in):
        # plan-week.html tells the household before the questions start
        # (its `plan_exists` line), so the route carries the yes itself.
        week = _monday()
        approved = _approved_week(stub_model)
        stub_model(_full_period(week, 7, meal="Katsu"))
        res = signed_in.post(f"/api/week/{week}/generate", json={})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get("status") != "needs_confirmation"
        assert body["took_over"]["retired_plan_ids"] == [approved["weekly_plan_id"]]


# ---------- the tool schema and the prompt rule ----------

class TestTheAssistantIsToldToAskFirst:

    def _tool(self):
        return next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "generate_weekly_plan")

    def test_the_tool_carries_the_flag_and_it_defaults_to_no(self):
        props = self._tool()["input_schema"]["properties"]
        assert props["confirm_takeover"]["type"] == "boolean"
        assert "confirm_takeover" not in self._tool()["input_schema"]["required"]
        assert "never" in props["confirm_takeover"]["description"].lower()

    def test_the_tool_description_says_what_needs_confirmation_means(self):
        assert "needs_confirmation" in self._tool()["description"]

    def test_the_prompt_rule_matches_the_existing_ask_first_rules(self):
        # Same shape as the confirm_hard_conflicts rule: the tool refuses,
        # the assistant asks naming the specifics, the flag is never set on
        # its own initiative — and only after a yes in THIS conversation.
        prompt = agent.SYSTEM_PROMPT
        start = prompt.index("If those days belong to an APPROVED plan")
        rule = prompt[start:prompt.index("\n- ", start)]
        assert "confirm_takeover" in rule
        assert "needs_confirmation" in rule
        assert "never set it on your own initiative" in rule
        assert "said yes in this conversation" in rule
        assert "after being told what would be replaced" in rule
        # The example sentence the rule carries is in the app's voice:
        # states the thing, then the way out, no exclamation mark.
        assert "Go ahead?" in rule
        assert "!" not in rule

    def test_the_function_signature_defaults_to_asking(self):
        sig = inspect.signature(agent.generate_weekly_plan)
        assert sig.parameters["confirm_takeover"].default is False
