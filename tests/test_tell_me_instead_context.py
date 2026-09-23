"""
Loop Board "'Tell me what instead' knows which meal you tapped it on, and
acts on one yes" (the button is called "Ask for something else" since the
copy sweep of 2026-09-23) (Emily, 2026-09-13, on the Tuesday burgers).

Three layers, tested where each lives:

(1) tools.describe_planned_meal — the household-scoped lookup that turns
    the card's pointer ({entry_id, date, slot}) into the meal chat is
    about, and says None for anything that isn't a real meal to talk
    about. The date+slot fallback is the load-bearing part: a swap
    replaces the row, and "actually, chicken" one message later must
    still find the meal.

(2) agent.run_agent_turn's `context` — the per-turn system block naming
    the meal and the confirm-once rule, present exactly when a context
    resolves, and an ordinary turn's system blocks byte-for-byte what
    they always were. Stubbed client, same idea as
    tests/test_tweak_the_week.py.

(3) The two chat routes pass a request's `context` through, and pass
    nothing extra without one. Plus source markers for the shell's side,
    since shell.js has no JS harness here.
"""
from __future__ import annotations

import datetime
import types
from pathlib import Path

import pytest

from app import agent, main as app_main, tools
from app.db import get_conn
from app.tools._shared import use_household


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
MONDAY, TUESDAY, WEDNESDAY = DAYS[0], DAYS[1], DAYS[2]

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_HTML = (REPO / "static" / "shell.html").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")

BURGERS = "Turkish-Style Grilled Kofte-Spiced Turkey Burgers with Charred Vegetables"


# ---------- fixtures ----------


@pytest.fixture
def week():
    """A draft week with the burgers on Tuesday. Returns the plan id."""
    tools.add_recipe(
        BURGERS,
        ingredients=[
            {"item": "Ground turkey", "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Red peppers", "qty": "2", "category": "produce"},
        ],
        food_groups=["protein", "vegetable"], main_protein="turkey",
        prep_time_minutes=10, cook_time_minutes=20,
    )
    tools.add_recipe(
        "Chili", ingredients=[{"item": "Beans", "qty": "2 cans", "category": "pantry"}],
        food_groups=["protein"],
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    tools.plan_meal(TUESDAY, BURGERS, slot="dinner", weekly_plan_id=plan_id, reasoning="quick")
    tools.plan_meal(MONDAY, "Chili", slot="dinner", weekly_plan_id=plan_id, reasoning="easy")
    return plan_id


def _entry_id(plan_id: int, day: str, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND weekly_plan_id = ? "
        "AND date = ? AND slot = ? ORDER BY id DESC",
        (tools.household_id(), plan_id, day, slot),
    ).fetchone()
    conn.close()
    return row["id"]


# ---------- (1) the lookup ----------


def test_the_card_pointer_resolves_to_the_meal_chat_is_about(week):
    entry_id = _entry_id(week, TUESDAY)
    meal = tools.describe_planned_meal(entry_id=entry_id)
    assert meal["meal"] == BURGERS
    assert meal["date"] == TUESDAY
    assert meal["weekday"] == datetime.date.fromisoformat(TUESDAY).strftime("%A")
    assert meal["slot"] == "dinner"
    assert meal["weekly_plan_id"] == week
    assert meal["entry_id"] == entry_id
    assert meal["approved"] is False
    assert meal["main_protein"] == "turkey"
    assert {"item": "Ground turkey", "qty": "1 lb"} in meal["ingredients"]


def test_an_approved_week_says_so(week):
    tools.approve_weekly_plan(week)
    assert tools.describe_planned_meal(entry_id=_entry_id(week, TUESDAY))["approved"] is True


def test_a_stale_entry_id_falls_back_to_the_slot_after_a_swap(week):
    """A swap deletes the row and inserts a new one. The chip still points
    at the old id, and the follow-up must land on what is there now."""
    old_id = _entry_id(week, TUESDAY)
    tools.swap_meal_in_plan(week, TUESDAY, "Chili", slot="dinner", old_meal=BURGERS)
    meal = tools.describe_planned_meal(entry_id=old_id, meal_date=TUESDAY, slot="dinner")
    assert meal is not None
    assert meal["meal"] == "Chili"
    assert meal["entry_id"] != old_id


def test_a_pointer_with_no_id_resolves_by_the_slot_alone(week):
    meal = tools.describe_planned_meal(meal_date=TUESDAY, slot="dinner")
    assert meal["meal"] == BURGERS


def test_a_slot_with_nothing_planned_is_no_subject(week):
    assert tools.describe_planned_meal(meal_date=WEDNESDAY, slot="dinner") is None
    assert tools.describe_planned_meal(entry_id=999999) is None
    assert tools.describe_planned_meal() is None


def test_a_night_nobody_is_home_is_never_a_subject(week):
    """planned_empty is not a decision — CLAUDE.md's standing rule — so it is
    not something chat is invited to change either."""
    tools.plan_slot_empty(week, WEDNESDAY, "dinner", reason="everyone out")
    entry_id = _entry_id(week, WEDNESDAY)
    assert tools.describe_planned_meal(entry_id=entry_id) is None
    assert tools.describe_planned_meal(meal_date=WEDNESDAY, slot="dinner") is None


def test_a_bad_slot_or_date_is_no_subject_not_an_error(week):
    assert tools.describe_planned_meal(meal_date=TUESDAY, slot="brunch") is None
    assert tools.describe_planned_meal(meal_date="not-a-date", slot="dinner") is None


def test_another_households_entry_is_not_found(week):
    entry_id = _entry_id(week, TUESDAY)
    conn = get_conn()
    conn.execute("INSERT INTO households (id, name) VALUES (2, 'Next door')")
    conn.commit()
    conn.close()
    with use_household(2):
        assert tools.describe_planned_meal(entry_id=entry_id) is None
        assert tools.describe_planned_meal(meal_date=TUESDAY, slot="dinner") is None


# ---------- (2) the turn ----------


class _Usage:
    input_tokens = 0
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0
    output_tokens = 0


class _CapturingMessages:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="Done.")],
            stop_reason="end_turn",
            usage=_Usage(),
        )


def _system_blocks_for(monkeypatch, message: str, context=None) -> list[dict]:
    captured = _CapturingMessages()
    monkeypatch.setattr(agent, "_client", lambda: types.SimpleNamespace(messages=captured))
    agent.run_agent_turn([], message, context=context)
    assert captured.calls
    return captured.calls[0]["system"]


def test_a_turn_from_the_card_is_told_which_meal_it_is_about(week, monkeypatch):
    context = {"kind": "planned_meal", "entry_id": _entry_id(week, TUESDAY),
               "date": TUESDAY, "slot": "dinner"}
    blocks = _system_blocks_for(monkeypatch, "swap the turkey for ground beef", context)
    block = blocks[-1]["text"]
    weekday = datetime.date.fromisoformat(TUESDAY).strftime("%A")
    assert BURGERS in block
    assert f"{weekday}'s dinner" in block
    assert f"weekly_plan_id {week}" in block
    assert "Ground turkey" in block, "the ingredients ride along so the proposal needs no lookup"
    # The rule the ticket is about.
    assert "Confirm ONCE" in block
    assert "do it?" in block
    assert "No \"are you sure\"" in block
    # And the shape of the change that keeps the dish in its slot.
    assert "add_recipe" in block and "swap_meal_in_plan" in block
    assert f"old_meal \"{BURGERS}\"" in block
    assert "still a draft" in block


def test_the_block_says_the_list_follows_on_an_approved_week(week, monkeypatch):
    tools.approve_weekly_plan(week)
    context = {"kind": "planned_meal", "entry_id": _entry_id(week, TUESDAY),
               "date": TUESDAY, "slot": "dinner"}
    block = _system_blocks_for(monkeypatch, "make it beef", context)[-1]["text"]
    assert "APPROVED" in block
    assert "no asking again whether to update the grocery list" in block


def test_the_subject_survives_the_swap_it_asked_for(week, monkeypatch):
    """The chip keeps sending the id it was drawn with. After the swap that
    id is gone; the next turn is still about that slot."""
    old_id = _entry_id(week, TUESDAY)
    tools.swap_meal_in_plan(week, TUESDAY, "Chili", slot="dinner", old_meal=BURGERS)
    context = {"kind": "planned_meal", "entry_id": old_id, "date": TUESDAY, "slot": "dinner"}
    block = _system_blocks_for(monkeypatch, "actually make it chicken", context)[-1]["text"]
    assert "\"Chili\"" in block
    assert BURGERS not in block


def test_an_ordinary_turn_is_left_exactly_as_it_was(week, monkeypatch):
    blocks = _system_blocks_for(monkeypatch, "What should I prep today?")
    assert len(blocks) == 2
    assert blocks[0]["text"] == agent.SYSTEM_PROMPT
    assert "Confirm ONCE" not in "\n".join(b["text"] for b in blocks)


@pytest.mark.parametrize(
    "context",
    [
        None,
        {},
        {"kind": "something_else", "entry_id": 1},
        {"kind": "planned_meal"},
        {"kind": "planned_meal", "entry_id": 424242},
        {"kind": "planned_meal", "entry_id": None, "date": WEDNESDAY, "slot": "dinner"},
        "not even a dict",
    ],
)
def test_a_context_that_names_nothing_makes_an_ordinary_turn(week, monkeypatch, context):
    blocks = _system_blocks_for(monkeypatch, "hello", context)
    assert len(blocks) == 2
    assert "Confirm ONCE" not in "\n".join(b["text"] for b in blocks)


def test_the_block_never_takes_the_turn_down(week, monkeypatch):
    monkeypatch.setattr(tools, "describe_planned_meal", lambda **kw: 1 / 0)
    blocks = _system_blocks_for(
        monkeypatch, "hello", {"kind": "planned_meal", "entry_id": 1, "date": TUESDAY, "slot": "dinner"},
    )
    assert len(blocks) == 2


# ---------- (3) the routes ----------


def _capturing_turn(captured: dict):
    def fake_turn(conversation, user_message, **kwargs):
        captured["kwargs"] = kwargs
        return "Done.", conversation + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "Done."},
        ]
    return fake_turn


def test_the_chat_route_hands_the_context_to_the_turn(signed_in, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(app_main, "run_agent_turn", _capturing_turn(captured))
    res = signed_in.post("/api/chat", json={
        "message": "make it beef",
        "context": {"kind": "planned_meal", "entry_id": 7, "date": TUESDAY, "slot": "dinner"},
    })
    assert res.status_code == 200, res.text
    assert captured["kwargs"]["context"] == {
        "kind": "planned_meal", "entry_id": 7, "date": TUESDAY, "slot": "dinner",
        # The week kind's two pointers ride along empty (2026-09-13).
        "week_start": None, "weekly_plan_id": None,
    }


def test_the_chat_route_passes_nothing_extra_without_one(signed_in, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(app_main, "run_agent_turn", _capturing_turn(captured))
    res = signed_in.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 200, res.text
    assert "context" not in captured["kwargs"]


def test_the_streaming_route_hands_the_context_to_the_turn(signed_in, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(app_main, "run_agent_turn", _capturing_turn(captured))
    monkeypatch.setattr(app_main, "summarize_chat_actions", lambda old, new: [])
    with signed_in.stream("POST", "/api/chat/stream", json={
        "message": "make it beef",
        "context": {"kind": "planned_meal", "entry_id": 7, "date": TUESDAY, "slot": "dinner"},
    }) as res:
        assert res.status_code == 200
        body = "".join(res.iter_text())
    assert "event: done" in body
    assert captured["kwargs"]["context"]["entry_id"] == 7


def test_a_malformed_context_is_refused_not_guessed_at(signed_in, monkeypatch):
    monkeypatch.setattr(app_main, "run_agent_turn", _capturing_turn({}))
    res = signed_in.post("/api/chat", json={"message": "hi", "context": {"entry_id": "seven"}})
    assert res.status_code == 422


# ---------- the shell's side (source markers) ----------


def test_the_link_opens_chat_about_the_meal_with_an_empty_composer():
    # The button carries the real slot key, so a snack resolves to its entry.
    assert "data-wk-tell=\"' + slot + '\"" in SHELL_JS
    assert "var context = mealAskContext(day, slot);" in SHELL_JS
    assert "if (context) openAskSheet('', context);" in SHELL_JS
    # The old sentence is only for a slot with no real meal to be about.
    assert "for something else" in SHELL_JS


def test_the_subject_is_sent_with_every_message_and_forgotten_on_close():
    assert "context: askContextPayload()" in SHELL_JS
    assert "kind: 'planned_meal'" in SHELL_JS
    assert "function closeAskSheet() {" in SHELL_JS
    close_body = SHELL_JS.split("function closeAskSheet() {", 1)[1].split("\n  }", 1)[0]
    assert "setAskContext(null)" in close_body


def test_the_chip_shows_what_chat_understood():
    assert 'id="ask-context"' in SHELL_HTML
    assert "ask-context-label" in SHELL_JS and "ask-context-clear" in SHELL_JS
    assert ".ask-context {" in SHELL_CSS
    assert ".ask-context[hidden] { display: none; }" in SHELL_CSS
    # The one tappable thing on the line is 44px (DESIGN_SYSTEM.md rule 6).
    clear = SHELL_CSS.split(".ask-context-clear {", 1)[1].split("}", 1)[0]
    assert "width: 44px" in clear and "height: 44px" in clear
