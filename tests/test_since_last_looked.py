"""
"Since you last looked" — what the other adult did, the first card on Today.

Loop Board 'Household: "Since you last looked"' (Emily, 2026-10-18). See
app/tools/since_last_looked.py. What each test is evidence of:

1. THE LOOK — the very first look shows nothing (no history dump) and
   stamps; one adult, or no adult picked, is no card and no stamp.
2. THE LINES — each recorded fact reads as the card's exact sentence, only
   the OTHER adult's, and the second look is empty.
3. FOR YOU FIRST — a "Who's on it?" pick naming me leads, with the target
   that opens it on Cook.
4. THE ROUTE — only ?look=1 reads and stamps; a re-read carries no key.
5. THE CARD — one line is a sentence, several are rows with "and N more".
"""
from __future__ import annotations

import datetime
import json

from app import main, tools
from app.db import get_conn
from app.tools import _shared
from app.tools import cooker as _cooker
from app.tools import moves as _moves
from conftest import household_today
import nodeharness
from pathlib import Path

SHELL_JS = (Path(__file__).resolve().parent.parent / "static" / "shell.js").read_text(encoding="utf-8")

TODAY = household_today()
WEEK_START = TODAY - datetime.timedelta(days=TODAY.weekday())
LONG_AGO = "2000-01-01 00:00:00"


def _adults(*names) -> dict[str, int]:
    for n in names:
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")
    conn = get_conn()
    rows = conn.execute("SELECT id, name FROM members WHERE household_id = ?", (tools.household_id(),)).fetchall()
    conn.close()
    return {r["name"]: r["id"] for r in rows}


def _looked_long_ago(member_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE members SET last_looked_at = ? WHERE id = ?", (LONG_AGO, member_id))
    conn.commit()
    conn.close()


def _last_looked(member_id: int):
    conn = get_conn()
    row = conn.execute("SELECT last_looked_at FROM members WHERE id = ?", (member_id,)).fetchone()
    conn.close()
    return row["last_looked_at"]


def _look_as(member_id: int):
    with _shared.use_member(member_id):
        return tools.look_since_last()


def _texts(lines):
    return [l["text"] for l in lines]


def _plan_tonight(dish="Chicken Skewers") -> tuple[int, int]:
    tools.add_recipe(dish, ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
                     prep_time_minutes=10, cook_time_minutes=25, default_servings=3)
    plan_id = tools.create_weekly_plan(WEEK_START.isoformat())["weekly_plan_id"]
    for i in range(7):
        tools.plan_meal((WEEK_START + datetime.timedelta(days=i)).isoformat(), dish,
                        slot="dinner", weekly_plan_id=plan_id)
    conn = get_conn()
    entry = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), TODAY.isoformat()),
    ).fetchone()["id"]
    conn.close()
    return plan_id, entry


# ---------- 1. the look ----------

def test_the_first_look_shows_nothing_and_stamps_it():
    ids = _adults("Emily", "Vineeth")
    with _shared.use_member(ids["Vineeth"]):
        tools.add_grocery_item("Milk")
    assert _last_looked(ids["Emily"]) is None
    assert _look_as(ids["Emily"]) == []          # never a history dump
    assert _last_looked(ids["Emily"])            # but the look is recorded


def test_one_adult_or_no_pick_is_no_card_and_no_stamp():
    ids = _adults("Emily")
    _looked_long_ago(ids["Emily"])
    assert _look_as(ids["Emily"]) == []
    assert _last_looked(ids["Emily"]) == LONG_AGO

    ids = _adults("Vineeth")
    _looked_long_ago(ids["Vineeth"])
    with _shared.use_member(None):               # two adults, nobody picked
        assert tools.look_since_last() == []
    assert _last_looked(ids["Vineeth"]) == LONG_AGO


# ---------- 2. the lines ----------

def test_the_other_adults_list_changes_read_as_the_cards_sentences_once():
    ids = _adults("Emily", "Vineeth")
    _looked_long_ago(ids["Emily"])
    with _shared.use_member(ids["Vineeth"]):
        for item in ("Milk", "Eggs", "Bread", "Butter", "Jam"):
            tools.add_grocery_item(item)
        coffee = tools.add_grocery_item("Coffee")["item_id"]
        tools.drop_grocery_item_pre_shop(coffee)
    with _shared.use_member(ids["Emily"]):
        tools.add_grocery_item("Apples")         # her own — never told to her

    lines = _look_as(ids["Emily"])
    # Coffee was added AND taken off since she looked: nothing to tell.
    assert _texts(lines) == ["Vineeth added milk, eggs and 3 more."]
    assert _look_as(ids["Emily"]) == []          # gone on the next open

    with _shared.use_member(ids["Vineeth"]):
        tea = tools.add_grocery_item("Tea")["item_id"]
    _looked_long_ago(ids["Emily"])
    conn = get_conn()   # an older line of hers, then dropped by him
    conn.execute("UPDATE grocery_items SET created_at = ? WHERE id = ?", (LONG_AGO, tea))
    conn.commit()
    conn.close()
    with _shared.use_member(ids["Vineeth"]):
        tools.drop_grocery_item_pre_shop(tea)
    assert "Vineeth took tea off the list." in _texts(_look_as(ids["Emily"]))


def test_approval_questions_and_credited_ticks_are_told():
    ids = _adults("Emily", "Vineeth")
    plan_id, entry = _plan_tonight()
    tools.set_cooking_role("one_person", who="Vineeth")
    conn = get_conn()
    task = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, related_meal, "
        "status, task_type, meal_plan_entry_id) VALUES (?, ?, ?, ?, 'Chicken Skewers', 'pending', 'defrost', ?)",
        (tools.household_id(), plan_id, TODAY.isoformat(),
         "Move the chicken thighs to the fridge — for tonight's skewers.", entry),
    ).lastrowid
    conn.commit()
    conn.close()
    _looked_long_ago(ids["Emily"])

    with _shared.use_member(ids["Vineeth"]):
        tools.approve_weekly_plan(plan_id)
        tools.save_week_intake((WEEK_START + datetime.timedelta(days=7)).isoformat(), night_tags={})
        _moves.set_move_done(f"fridge:{task}", True)
        _moves.set_move_done(f"cook:{entry}", True)

    texts = _texts(_look_as(ids["Emily"]))
    for want in ("Vineeth approved the week.",
                 "Vineeth answered next week’s questions.",
                 "Vineeth moved the chicken thighs to the fridge.",
                 "Vineeth made the Chicken Skewers."):
        assert want in texts, texts
    # Vineeth, looking himself, is told nothing of his own doing.
    _looked_long_ago(ids["Vineeth"])
    assert _look_as(ids["Vineeth"]) == []

    # An un-ticked cook is no longer something he made.
    _looked_long_ago(ids["Emily"])
    _cooker.check_off_meal(entry, "pending")
    assert "Vineeth made the Chicken Skewers." not in _texts(_look_as(ids["Emily"]))


# ---------- 3. for you first ----------

def test_a_pick_that_names_me_comes_first_and_opens_the_move():
    ids = _adults("Emily", "Vineeth")
    _plan_id, entry = _plan_tonight()
    tools.set_cooking_role("one_person", who="Vineeth")
    _looked_long_ago(ids["Emily"])
    with _shared.use_member(ids["Vineeth"]):
        tools.add_grocery_item("Milk")
        tools.set_move_owner(f"cook:{entry}", ids["Emily"], TODAY.isoformat())

    lines = _look_as(ids["Emily"])
    assert lines[0]["text"] == "Vineeth put you on today’s dinner."
    assert lines[0]["for_you"] is True
    assert lines[0]["target"]["cookFocus"]["entryId"] == entry
    assert _texts(lines)[1:] == ["Vineeth added milk."]

    # A pick of himself is not something left for her.
    _looked_long_ago(ids["Emily"])
    with _shared.use_member(ids["Vineeth"]):
        tools.set_move_owner(f"cook:{entry}", ids["Vineeth"], TODAY.isoformat())
    assert not any(l["for_you"] for l in _look_as(ids["Emily"]))


# ---------- 4. the route ----------

def test_only_a_look_reads_and_stamps():
    ids = _adults("Emily", "Vineeth")
    _looked_long_ago(ids["Emily"])
    with _shared.use_member(ids["Vineeth"]):
        tools.add_grocery_item("Milk")
    with _shared.use_member(ids["Emily"]):
        reread = main.today_moves()
        assert "since_last_looked" not in reread       # the client keeps its card
        assert _last_looked(ids["Emily"]) == LONG_AGO  # and nothing was stamped
        looked = main.today_moves(look=1)
    assert _texts(looked["since_last_looked"]) == ["Vineeth added milk."]
    assert _last_looked(ids["Emily"]) != LONG_AGO


# ---------- 5. the card ----------

def _card(lines, opened=False) -> str:
    src = SHELL_JS
    start = src.index("  var SINCE_SHOWN_ROWS = 3;")
    end = src.index("  function renderSinceLooked(", start)
    script = (
        "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
        ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
        + src[start:end]
        + f"\nconsole.log(JSON.stringify(sinceLookedCardHtml({json.dumps(lines)}, {json.dumps(opened)})));"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_one_line_is_a_sentence_several_are_rows_with_and_n_more():
    assert _card([]) == ""
    one = _card([{"text": "Vineeth approved the week.", "for_you": False, "target": None}])
    assert "since-one" in one and "since-row" not in one and "Since you last looked" in one

    lines = [{"text": f"Vineeth line {i}.", "for_you": i == 0,
              "target": {"tab": "kitchen"} if i == 0 else None} for i in range(5)]
    many = _card(lines)
    assert many.count('class="since-row') == 3 and "and 2 more" in many
    assert 'data-since-go="0"' in many                   # the pick is the tap
    opened = _card(lines, True)
    assert opened.count('class="since-row') == 5 and "more</button>" not in opened


# ---------- review follow-ups (2026-10-19) ----------

def test_a_tick_is_told_as_whoever_tapped_it_not_the_moves_owner():
    """Vineeth is the standing cook; Emily ticks tonight's dinner. She did
    not read "Vineeth made…" — he reads "Emily made…". And the other way."""
    ids = _adults("Emily", "Vineeth")
    _plan_id, entry = _plan_tonight()
    tools.set_cooking_role("one_person", who="Vineeth")
    _looked_long_ago(ids["Emily"])
    _looked_long_ago(ids["Vineeth"])
    with _shared.use_member(ids["Emily"]):
        _moves.set_move_done(f"cook:{entry}", True)
    assert _look_as(ids["Emily"]) == []
    assert _texts(_look_as(ids["Vineeth"])) == ["Emily made the Chicken Skewers."]

    _cooker.check_off_meal(entry, "pending")
    tools.set_cooking_role("one_person", who="Emily")
    _looked_long_ago(ids["Emily"])
    _looked_long_ago(ids["Vineeth"])
    with _shared.use_member(ids["Vineeth"]):
        _moves.set_move_done(f"cook:{entry}", True)
    assert _texts(_look_as(ids["Emily"])) == ["Vineeth made the Chicken Skewers."]
    assert _look_as(ids["Vineeth"]) == []


def test_a_tick_with_nobody_picked_is_nobodys_to_tell():
    ids = _adults("Emily", "Vineeth")
    _plan_id, entry = _plan_tonight()
    tools.set_cooking_role("one_person", who="Vineeth")
    _looked_long_ago(ids["Emily"])
    with _shared.use_member(None):
        _moves.set_move_done(f"cook:{entry}", True)
    assert _look_as(ids["Emily"]) == []


def test_the_stamp_never_moves_backwards():
    ids = _adults("Emily", "Vineeth")
    conn = get_conn()
    conn.execute("UPDATE members SET last_looked_at = '2999-01-01 00:00:00' WHERE id = ?", (ids["Emily"],))
    conn.commit()
    conn.close()
    _look_as(ids["Emily"])
    assert _last_looked(ids["Emily"]) == "2999-01-01 00:00:00"


def test_a_pwa_back_after_half_an_hour_is_a_new_open_a_glance_is_not():
    start = SHELL_JS.index("  var SINCE_SHOWN_ROWS = 3;")
    end = SHELL_JS.index("  function sinceLookedLineHtml(", start)
    min_ = 60 * 1000
    script = SHELL_JS[start:end] + (
        "var out = [];\n"
        "out.push(sinceLookedResumeIsOpen('visible', 0));\n"            # never hidden
        f"sinceLookedResumeIsOpen('hidden', 0); out.push(sinceLookedResumeIsOpen('visible', {5 * min_}));\n"
        f"sinceLookedResumeIsOpen('hidden', 0); out.push(sinceLookedResumeIsOpen('visible', {31 * min_}));\n"
        f"out.push(sinceLookedResumeIsOpen('visible', {90 * min_}));\n"  # no second hide: no second open
        "console.log(JSON.stringify(out));"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout.strip().splitlines()[-1]) == [False, False, True, False]
