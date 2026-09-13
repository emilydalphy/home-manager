"""
The chat on the draft, and the change card (Emily, 2026-09-13, "Shaping the
Draft" Flows C and D; app/tools/proposals.py, app/agent.py's week subject
block, the four /api/chat/proposals routes, shell.js mountChangeCard).

BEHAVIOUR: the week as the turn's subject (every planned slot, with entry
ids, plus the card protocol); a proposal that writes nothing; Save changes
landing every row through the swap's own gates and apply; a refused row
reported, the rest saved; Another re-picking one row with everything it was
offered avoided; Undo putting every row back; options chosen by tap; a
proposal that is household-scoped and expires; the chat route lifting the
card off the turn without an action card (so the shell never says
"Changes saved" for a change that isn't made yet).

SOURCE, for the shell (the house standard for handlers too entangled with
the DOM): the FAB opening the chat about the week, the About chip carrying
a week, the card drawn under the reply, the Remembered chip, the Changed
word on the rows, and the five behaviours in the prompt.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from app import agent, tools
from app.tools import proposals as prop
from app.tools import swap_in_place as sip
from app.tools._shared import use_household
from app import main as app_main


TODAY = datetime.date.today()
WEEK_START = (TODAY - datetime.timedelta(days=TODAY.weekday())).isoformat()
DAYS = [(datetime.date.fromisoformat(WEEK_START) + datetime.timedelta(days=i)).isoformat()
        for i in range(7)]
MONDAY, TUESDAY, WEDNESDAY, THURSDAY = DAYS[0], DAYS[1], DAYS[2], DAYS[3]

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _cand(name, minutes=25, protein="chicken", ingredients=None, reason="Quick, and nothing to thaw."):
    return {
        "meal_name": name, "reason": reason,
        "ingredients": ingredients if ingredients is not None else [
            {"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"},
            {"item": "Limes", "qty": "2", "category": "produce"},
        ],
        "instructions": ["Cook it.", "Serve it."],
        "food_groups": ["protein", "carb"], "main_protein": protein,
        "prep_time_minutes": 5, "cook_time_minutes": minutes - 5, "default_servings": 2,
    }


@pytest.fixture
def week():
    tools.add_member("Emily")
    tools.add_member("Vineeth")
    for name, groups in (("Pork Chops", ["protein"]), ("Chili", ["protein", "vegetable"]),
                         ("Chicken Tikka", ["protein", "carb"])):
        tools.add_recipe(
            name,
            ingredients=[{"item": name.split()[0], "qty": "1 lb", "category": "meat/seafood"}],
            food_groups=groups, prep_time_minutes=10, cook_time_minutes=20,
        )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    for day, dish in ((MONDAY, "Pork Chops"), (TUESDAY, "Chili"), (THURSDAY, "Chicken Tikka")):
        tools.plan_meal(day, dish, slot="dinner", weekly_plan_id=plan_id, reasoning="fits the week")
    prop._PROPOSALS.clear()
    return plan_id


def _dinner(plan_id, day):
    menu = tools.get_week_menu(plan_id)
    for d in menu["days"]:
        if d["date"] == day:
            return d["dinner"]
    return None


# ---------- the subject ----------

def test_the_week_is_described_with_every_planned_slot_and_its_entry_id(week):
    out = tools.describe_plan_for_chat(week_start=WEEK_START)
    assert out["weekly_plan_id"] == week
    assert out["status"] == "draft"
    by_date = {d["date"]: d for d in out["days"]}
    thu = [s for s in by_date[THURSDAY]["slots"] if s["slot"] == "dinner"][0]
    assert thu["meal"] == "Chicken Tikka"
    assert thu["entry_id"] == _dinner(week, THURSDAY)["entry_id"]
    assert tools.describe_plan_for_chat(week_start="2031-01-06") is None


def test_the_week_block_names_the_plan_and_the_card_protocol(week):
    block = agent._build_chat_context_block({"kind": "weekly_plan", "week_start": WEEK_START})
    text = block["text"]
    assert f"weekly_plan_id {week}" in text
    assert "Chicken Tikka" in text and "Pork Chops" in text
    assert "propose_plan_changes" in text
    assert "Never call swap_meal_in_plan or plan_meal for a planned slot here" in text
    assert "Never say the change is made" in text
    # A week with no plan is an ordinary turn, not a failed one.
    assert agent._build_chat_context_block({"kind": "weekly_plan", "week_start": "2031-01-06"}) is None


def test_the_five_behaviours_are_in_the_prompt_for_every_turn():
    for phrase in ("Read the intent and change the smallest thing",
                   "A reason is a fact",
                   "Say the consequence once, before it bites",
                   "Offer three, never an open question",
                   "Never ask which one they meant when the screen already says"):
        assert phrase in agent.SYSTEM_PROMPT, phrase
    assert "propose_plan_changes" in agent.TOOL_FUNCTIONS
    assert any(t["name"] == "propose_plan_changes" for t in agent.TOOL_DEFINITIONS)


# ---------- proposing ----------

def test_a_proposal_writes_nothing_and_echoes_the_card(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos", 20, "shrimp")]},
        {"date": MONDAY, "slot": "dinner", "action": "keep"},
    ])
    assert out["proposal_id"] and out["status"] == "open"
    rows = out["rows"]
    assert rows[0]["current"]["meal"] == "Chicken Tikka"
    assert rows[0]["candidates"] == [{"meal_name": "Shrimp Tacos", "reason": "Quick, and nothing to thaw.", "minutes": 20}]
    assert rows[1]["action"] == "keep" and rows[1]["current"]["meal"] == "Pork Chops"
    # The recipes stay server-side; the wire shape carries names and minutes.
    assert "ingredients" not in rows[0]["candidates"][0]
    # Nothing on the plan moved, and the dish was not saved as a recipe.
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tikka"
    assert not any(r["name"] == "Shrimp Tacos" for r in tools.list_recipes())


def test_a_row_for_an_empty_slot_carries_a_problem_not_a_crash(week):
    out = tools.propose_plan_changes(week, [
        {"date": WEDNESDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Anything")]},
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": []},
    ])
    assert "plan_meal" in out["rows"][0]["problem"]
    assert "no dish" in out["rows"][1]["problem"]


# ---------- saving ----------

def test_save_lands_every_row_through_the_swaps_own_door_and_undo_puts_them_back(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos", 20, "shrimp")]},
        {"date": MONDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Grilled Pork Chops", 25, "pork")]},
        {"date": TUESDAY, "slot": "dinner", "action": "keep"},
    ])
    pid = out["proposal_id"]
    applied = tools.apply_proposal(pid)
    assert applied["status"] == "applied" and applied["refused"] == []
    assert [a["meal"] for a in applied["proposal"]["applied"]] == ["Shrimp Tacos", "Grilled Pork Chops"]
    assert _dinner(week, THURSDAY)["title"] == "Shrimp Tacos"
    assert _dinner(week, MONDAY)["title"] == "Grilled Pork Chops"
    assert _dinner(week, TUESDAY)["title"] == "Chili"
    # Cookable and shoppable: the dish became a recipe, as a swap's does.
    assert any(r["name"] == "Shrimp Tacos" for r in tools.list_recipes())
    # The undo note is the swap's own, so the swap's undo works on it.
    entry = sip._entry(week, _dinner(week, THURSDAY)["entry_id"])
    assert entry["derived_from"]["swapped_from"]["meal"] == "Chicken Tikka"
    # The refreshed days ride along for the screen behind the sheet.
    assert sorted(d["date"] for d in applied["days"]) == sorted([THURSDAY, MONDAY])

    undone = tools.undo_proposal(pid)
    assert undone["status"] == "restored"
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tikka"
    assert _dinner(week, MONDAY)["title"] == "Pork Chops"


def test_a_row_the_gate_refuses_is_reported_and_the_rest_still_land(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change",
         "candidates": [_cand("Shrimp Tacos", 20, "shrimp", ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"}])]},
        {"date": MONDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Grilled Pork Chops", 25, "pork")]},
    ])
    applied = tools.apply_proposal(out["proposal_id"])
    assert applied["status"] == "applied"
    assert [r["meal"] for r in applied["refused"]] == ["Shrimp Tacos"]
    assert "clashes" in applied["refused"][0]["why"]
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tikka"
    assert _dinner(week, MONDAY)["title"] == "Grilled Pork Chops"


def test_saving_the_dish_already_there_writes_nothing(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Chicken Tikka")]},
    ])
    applied = tools.apply_proposal(out["proposal_id"])
    assert applied["status"] == "nothing"
    assert applied["proposal"]["applied"] == []


# ---------- options and Another ----------

def test_options_are_chosen_by_tap_and_the_chosen_one_is_what_saves(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change",
         "candidates": [_cand("Sheet-pan Sausages", 25, "pork"), _cand("Veggie Fried Rice", 20, "vegetarian"),
                        _cand("Pasta with Peas", 15, "vegetarian")]},
    ])
    pid = out["proposal_id"]
    assert out["rows"][0]["chosen"] == 0
    chosen = tools.choose_candidate(pid, 0, 2)
    assert chosen["status"] == "chosen" and chosen["proposal"]["rows"][0]["chosen"] == 2
    assert tools.choose_candidate(pid, 0, 9)["status"] == "refused"
    tools.apply_proposal(pid)
    assert _dinner(week, THURSDAY)["title"] == "Pasta with Peas"


def test_another_repicks_one_row_avoiding_everything_it_was_offered(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos", 20, "shrimp")]},
        {"date": MONDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Grilled Pork Chops", 25, "pork")]},
    ])
    pid = out["proposal_id"]
    seen = []

    def picker(context):
        seen.append(context)
        return _cand("Sheet-pan Sausages with Peppers", 30, "pork")

    again = prop.another_for_row(pid, 0, picker=picker)
    assert again["status"] == "picked"
    row = again["proposal"]["rows"][0]
    assert [c["meal_name"] for c in row["candidates"]] == ["Shrimp Tacos", "Sheet-pan Sausages with Peppers"]
    assert row["chosen"] == 1
    # The other row is untouched, and the pick was told what to avoid.
    assert again["proposal"]["rows"][1]["candidates"][0]["meal_name"] == "Grilled Pork Chops"
    assert set(seen[0]["avoid"]) >= {"Chicken Tikka", "Shrimp Tacos"}
    # Nothing written by Another either.
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tikka"


def test_another_refuses_a_pick_the_gate_refuses_and_tries_once_more(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Chicken Tacos", 20)]},
    ])
    picks = iter([
        _cand("Shrimp Bowls", 20, "shrimp", ingredients=[{"item": "Shrimp", "qty": "1 lb", "category": "meat/seafood"}]),
        _cand("Bean Burritos", 20, "vegetarian"),
    ])
    again = prop.another_for_row(out["proposal_id"], 0, picker=lambda ctx: next(picks))
    assert again["status"] == "picked"
    assert again["proposal"]["rows"][0]["candidates"][-1]["meal_name"] == "Bean Burritos"


# ---------- scope and life ----------

def test_a_proposal_is_household_scoped_and_expires(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos")]},
    ])
    pid = out["proposal_id"]
    with use_household(2):
        assert tools.get_proposal(pid) is None
        assert tools.apply_proposal(pid)["status"] == "gone"
    assert tools.get_proposal(pid) is not None
    tools.get_proposal(pid)["created_at"] -= prop._TTL_SECONDS + 1
    assert tools.get_proposal(pid) is None
    assert tools.apply_proposal(pid) == {"status": "gone", "message": prop.GONE}


# ---------- the routes ----------

def test_the_routes_choose_apply_and_undo(signed_in, week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change",
         "candidates": [_cand("Shrimp Tacos", 20, "shrimp"), _cand("Chicken Tacos", 20)]},
    ])
    pid = out["proposal_id"]
    res = signed_in.post(f"/api/chat/proposals/{pid}/choose", json={"row": 0, "candidate": 1})
    assert res.status_code == 200 and res.json()["proposal"]["rows"][0]["chosen"] == 1
    res = signed_in.post(f"/api/chat/proposals/{pid}/apply", json={})
    assert res.status_code == 200 and res.json()["status"] == "applied"
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tacos"
    res = signed_in.post(f"/api/chat/proposals/{pid}/undo", json={})
    assert res.status_code == 200 and res.json()["status"] == "restored"
    assert _dinner(week, THURSDAY)["title"] == "Chicken Tikka"
    assert signed_in.post("/api/chat/proposals/nope/apply", json={}).status_code == 404


def test_the_another_route_uses_the_swaps_picker(signed_in, week, monkeypatch):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos", 20, "shrimp")]},
    ])
    monkeypatch.setattr(sip, "_pick_replacement", lambda ctx: _cand("Bean Burritos", 20, "vegetarian"))
    res = signed_in.post(f"/api/chat/proposals/{out['proposal_id']}/another", json={"row": 0})
    assert res.status_code == 200
    assert res.json()["proposal"]["rows"][0]["candidates"][-1]["meal_name"] == "Bean Burritos"


# ---------- the chat turn ----------

def _turn_with_proposal(proposal):
    before = [{"role": "user", "content": "less chicken"}]
    after = before + [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "propose_plan_changes", "input": {"weekly_plan_id": 1, "rows": []}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(proposal), "is_error": False},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "Two to look at."}]},
    ]
    return before, after


def test_the_chat_turn_carries_the_card_and_no_action_card_for_it(week):
    out = tools.propose_plan_changes(week, [
        {"date": THURSDAY, "slot": "dinner", "action": "change", "candidates": [_cand("Shrimp Tacos", 20, "shrimp")]},
    ])
    before, after = _turn_with_proposal(out)
    assert app_main.summarize_chat_actions(before, after) == []
    assert app_main._proposal_from_turn(before, after)["proposal_id"] == out["proposal_id"]
    # ...and a proposal still counts as the turn having done something, so
    # the reply's "here are two changes" is not retracted as an empty claim.
    assert agent._turn_wrote_anything(after[len(before):]) is True


def test_the_chat_context_model_accepts_a_week():
    ctx = app_main.ChatContext(kind="weekly_plan", week_start=WEEK_START, weekly_plan_id=3)
    assert ctx.model_dump()["week_start"] == WEEK_START
    assert "proposal" in app_main.ChatResponse.model_fields


# ---------- the shell (source) ----------

def test_the_fab_opens_the_chat_about_the_week_on_plan():
    assert "askBar.addEventListener('click', function () { openAskSheet('', weekAskContext()); });" in SHELL_JS
    i = SHELL_JS.index("function weekAskContext()")
    body = SHELL_JS[i:i + 900]
    assert "currentTabKey() !== 'week'" in body
    assert "kind: 'weekly_plan'" in body
    assert "This week’s draft" in body
    j = SHELL_JS.index("function askContextPayload()")
    assert "kind: 'weekly_plan', week_start: askContext.week_start" in SHELL_JS[j:j + 500]


def test_the_card_is_drawn_under_the_reply_and_saves_with_the_pop_up():
    assert "if (data.proposal) mountChangeCard(replyEls, data.proposal);" in SHELL_JS
    i = SHELL_JS.index("function wireChangeCard(")
    body = SHELL_JS[i:SHELL_JS.index("function undoChangeCard(")]
    assert "/apply'" in body and "/another'" in body and "/choose'" in body
    assert "toastSaved({ label: 'Undo', onClick: function () { undoChangeCard(state); } }, SWAP_UNDO_MS);" in body
    assert "markRecentlyChanged(a.date, a.slot)" in body
    assert "loadWeekMenu(panels.week)" in body
    # Every control the card draws is wired, and none of the copy asks a question.
    for marker in ("Save changes", "Leave the week as it was", ">Another<", "Kept", "Finding another…"):
        assert marker in SHELL_JS, marker
    assert "class=\"wk-changed\">Changed</span>" in SHELL_JS


def test_a_remembered_fact_is_a_chip_with_a_way_to_correct_it():
    i = SHELL_JS.index("function buildAskMessageEl(")
    body = SHELL_JS[i:i + 3000]
    assert "action.href === '/memory'" in body
    assert "ask-remembered" in body and "Not quite" in body
    for cls in (".ask-change-card", ".ask-change-save", ".ask-change-another", ".ask-change-opt", ".ask-remembered", ".wk-changed"):
        assert cls in SHELL_CSS, cls
    # Tokens only — no literal hex in the new CSS (Rule 9).
    start = SHELL_CSS.index("The change card and the Remembered chip")
    end = SHELL_CSS.index(".ask-chips { display: flex;")
    assert "#" not in SHELL_CSS[start:end].replace("#ask", "")
