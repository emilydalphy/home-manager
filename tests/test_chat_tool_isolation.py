"""
Household B's chat assistant, handed household A's ids, can neither read
nor change anything of A's — tool by tool.

The model writes the arguments, so an id in a tool call is untrusted input:
a stale id from an old conversation, a hallucinated one, or a prompt that
talks the model into it. The HTTP side of this is test_isolation_attack.py
(every route, every id A owns). This is the same attack one layer down, on
the functions in agent.TOOL_FUNCTIONS, which no route reaches.

test_multi_household.test_write_tools_reject_a_foreign_households_record_id
already drives the grocery/fact/attention/cook-tick writes with a foreign
id; those are listed in ALREADY_PINNED and not driven twice. Everything
else here is in CASES. test_every_id_taking_tool_is_classified fails the day
a tool gains an id argument nobody has put in either list.

Two kinds of id are driven:
  * the ids each tool's schema DECLARES (weekly_plan_id, held_id, ...);
  * ids a function ACCEPTS but the schema doesn't declare (member_id on
    hold_thing, grocery_item_id on add_staple, ...). read_tool_input passes
    an undeclared key straight through to the function, so the model can
    send one.

Per case: A's rows are unchanged and nothing new appears under A
(_owned_state/_damage, shared with the route harness), and neither the
return value, the error, nor B's own lists afterwards carry A's markers —
a write that lands in B pointing at A's row (a held thing "said by" A's
adult) leaks on B's next read even though A's tables never moved.

Chores tools are paused and not driven. The two LLM tools
(generate_weekly_plan's intake_id, generate_prep_schedule) resolve their id
through get_weekly_plan / get_week_intake_history before any model call;
generate_prep_schedule is driven here up to the offline-model refusal.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import sys

import pytest

from conftest import household_today

from app import agent, households, tools
from app.db import get_conn

from test_isolation_attack import (  # noqa: F401 — a_household_named is a fixture
    A, A_NAME, _damage, _owned_state, _seed_a, _week_start, a_household_named,
)

MARKERS = ("Zqs", "Zqn", A_NAME)

# Driven with a foreign id in test_multi_household.py already.
ALREADY_PINNED = {
    "remove_grocery_item", "mark_grocery_item", "exclude_grocery_item",
    "include_grocery_item", "update_grocery_item", "check_off_meal",
    "check_off_prep_step", "resolve_attention_item", "update_fact",
    "delete_fact", "mark_grocery_item_already_have_reviewed",
    "set_week_constraints",
}
PAUSED_OR_BETA = {"update_chore", "complete_chore", "remove_inventory_item"}
# LLM-only; see the module docstring.
NOT_DRIVEN = {"generate_weekly_plan"}


def _today():
    return household_today().isoformat()


def _tomorrow():
    import datetime
    return (household_today() + datetime.timedelta(days=1)).isoformat()


# (tool name, kwargs built from A's ids). `ids` keys: plan, entry, grocery,
# staple, held, member, prep, recipe, stretch.
CASES = [
    # --- declared ids ---
    ("get_weekly_plan", lambda i: {"weekly_plan_id": i["plan"]}),
    ("swap_meal_in_plan", lambda i: {"weekly_plan_id": i["plan"], "meal_date": _today(),
                                     "new_meal": "Injected Stew", "old_entry_id": i["entry"]}),
    ("propose_plan_changes", lambda i: {"weekly_plan_id": i["plan"], "rows": [
        {"date": _today(), "slot": "dinner", "action": "change",
         "candidates": [{"meal_name": "Injected Stew",
                         "ingredients": [{"item": "beans", "qty": "1 can"}]}]}]}),
    ("swap_dinner_nights", lambda i: {"weekly_plan_id": i["plan"], "date_a": _today(),
                                      "date_b": _tomorrow()}),
    ("swap_component_in_plan", lambda i: {"weekly_plan_id": i["plan"], "component_category": "dinner",
                                          "old_meal": "Zqn Lentil Soup", "new_meal": "Injected Stew"}),
    ("approve_weekly_plan", lambda i: {"weekly_plan_id": i["plan"], "approved_by": "B"}),
    ("discard_draft_plan", lambda i: {"weekly_plan_id": i["plan"]}),
    ("generate_prep_schedule", lambda i: {"weekly_plan_id": i["plan"]}),
    ("get_prep_schedule", lambda i: {"weekly_plan_id": i["plan"]}),
    ("get_plan_progress", lambda i: {"weekly_plan_id": i["plan"]}),
    ("get_week_ingredients", lambda i: {"weekly_plan_id": i["plan"]}),
    ("get_cooker_view", lambda i: {"weekly_plan_id": i["plan"]}),
    ("check_plan_conflicts", lambda i: {"weekly_plan_id": i["plan"]}),
    ("resolve_held_thing", lambda i: {"held_id": i["held"]}),
    # --- accepted through a *_for_chat wrapper, undeclared in the schema ---
    ("plan_meal", lambda i: {"meal_date": _today(), "meal": "Injected Stew", "slot": "lunch",
                             "weekly_plan_id": i["plan"]}),
    # --- accepted but undeclared ids (read_tool_input passes them through) ---
    ("add_grocery_item", lambda i: {"item": "injected beans", "source_weekly_plan_id": i["plan"]}),
    ("add_staple", lambda i: {"item": "injected coffee", "every_days": 7,
                              "grocery_item_id": i["grocery"]}),
    ("hold_thing", lambda i: {"text": "injected reminder", "member_id": i["member"]}),
    ("set_slot_need", lambda i: {"date_str": _today(), "slot": "dinner", "need": "quick",
                                 "for_member_ids": [i["member"]]}),
    ("set_slot_need", lambda i: {"date_str": _today(), "slot": "dinner", "need": "quick",
                                 "away_stretch_id": i["stretch"]}),
    ("consolidate_grocery_list", lambda i: {"only_ids": [i["grocery"]]}),
    ("clear_stale_grocery_items", lambda i: {"current_weekly_plan_id": i["plan"]}),
]


def _id_params(name):
    """Every argument of a chat tool that names a row: declared in the
    schema, or accepted by the function itself."""
    out = set()
    for d in agent.TOOL_DEFINITIONS:
        if d["name"] == name:
            out |= {p for p in (d["input_schema"].get("properties") or {})
                    if p.endswith("_id") or p.endswith("_ids")}
    fn = _real_function(agent.TOOL_FUNCTIONS[name])
    out |= {p for p in inspect.signature(fn).parameters if p.endswith("_id") or p.endswith("_ids")}
    return out


# Chat wrappers whose **kwargs land somewhere other than a same-module
# function of the same name. agent.add_recipe_from_chat (chat on Haiku,
# 2026-10-26) hands a dictated recipe's arguments to
# tools.add_recipe_for_chat, and builds the writer's own call from the rest;
# its update_recipe_details and set_big_meal_dish doors hand theirs to the
# tools of those names.
_ARGUMENTS_LAND_ON = {
    "add_recipe_from_chat": lambda: tools.add_recipe_for_chat,
    "update_recipe_details_from_chat": lambda: tools.update_recipe_details,
    "set_big_meal_dish_from_chat": lambda: tools.set_big_meal_dish,
}


def _real_function(fn):
    """The function a chat tool's arguments actually land on. The
    `*_for_chat(*args, **kwargs)` wrappers (plan_meal, add_recipe,
    swap_meal_in_plan) hand everything through to the bare function of the
    same name in the same module, so their own signature says nothing —
    plan_meal's weekly_plan_id was invisible here until this followed it."""
    fn = inspect.unwrap(fn)
    params = inspect.signature(fn).parameters.values()
    if not any(p.kind is p.VAR_KEYWORD for p in params):
        return fn
    if fn.__name__ in _ARGUMENTS_LAND_ON:
        return _real_function(_ARGUMENTS_LAND_ON[fn.__name__]())
    module = sys.modules[fn.__module__]
    real = getattr(module, fn.__name__.removesuffix("_for_chat"), None)
    assert real is not None and real is not fn, (
        f"{fn.__module__}.{fn.__name__} takes **kwargs and isn't a *_for_chat wrapper "
        "of a same-module function — say here where its arguments go")
    return _real_function(real)


def test_every_id_taking_tool_is_classified():
    driven = {name for name, _ in CASES}
    known = driven | ALREADY_PINNED | PAUSED_OR_BETA | NOT_DRIVEN
    missing = sorted(n for n in agent.TOOL_FUNCTIONS if _id_params(n) and n not in known)
    assert not missing, (
        "these chat tools take an id but no test proves household B can't use "
        f"household A's with them — add each to CASES: {missing}")
    assert not driven & ALREADY_PINNED, "a tool is both driven here and listed as pinned elsewhere"
    assert set(agent.TOOL_FUNCTIONS) >= known, f"stale names: {sorted(known - set(agent.TOOL_FUNCTIONS))}"


def _a_ids():
    conn = get_conn()
    one = lambda sql: conn.execute(sql, (A,)).fetchone()["id"]  # noqa: E731
    ids = {
        "plan": one("SELECT id FROM weekly_plans WHERE household_id = ?"),
        "entry": one("SELECT id FROM meal_plan_entries WHERE household_id = ? AND recipe_id IS NOT NULL"),
        "grocery": one("SELECT id FROM grocery_items WHERE household_id = ?"),
        "staple": one("SELECT id FROM staples WHERE household_id = ?"),
        "held": one("SELECT id FROM held_things WHERE household_id = ?"),
        "member": one("SELECT id FROM members WHERE household_id = ?"),
        "recipe": one("SELECT id FROM recipes WHERE household_id = ?"),
    }
    cur = conn.execute(
        "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description) VALUES (?, ?, ?, ?)",
        (A, ids["plan"], _today(), "Zqs chop the onions"))
    ids["prep"] = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO away_stretches (household_id, from_date, from_slot, to_date, to_slot, reason) "
        "VALUES (?, ?, 'dinner', ?, 'dinner', 'Zqs at the cottage')", (A, _tomorrow(), _tomorrow()))
    ids["stretch"] = cur.lastrowid
    conn.commit()
    conn.close()
    return ids


def _b_views():
    """What B's own screens and chat would read back afterwards."""
    out = []
    for fn in (tools.get_weekly_plan, tools.list_grocery_list, tools.list_held_things,
               tools.list_staples, tools.get_prep_schedule, tools.get_cooker_view):
        try:
            out.append(str(fn()))
        except Exception as exc:  # an empty household may answer with an error
            out.append(str(exc))
    return "\n".join(out)


# A row of B's pointing at a row of A's is a leak waiting for the next
# join, even when the join that reads it today happens to be scoped.
_POINTS_AT = {"member_id": "members", "weekly_plan_id": "weekly_plans",
              "source_weekly_plan_id": "weekly_plans", "grocery_item_id": "grocery_items",
              "recipe_id": "recipes", "staple_id": "staples", "meal_plan_entry_id": "meal_plan_entries",
              "away_stretch_id": "away_stretches"}


def _bs_rows_pointing_at_as(b):
    conn = get_conn()
    out = []
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        cols = [c["name"] for c in conn.execute(f"PRAGMA table_info({table})")]
        if "household_id" not in cols:
            continue
        for col in cols:
            if col.endswith("member_ids_json"):
                # A JSON array of members.id (slot_needs.for_member_ids_json, ...).
                theirs = {r["id"] for r in conn.execute(
                    "SELECT id FROM members WHERE household_id = ?", (A,))}
                for r in conn.execute(
                        f"SELECT rowid AS rid, {col} AS v FROM {table} WHERE household_id = ?", (b,)):
                    try:
                        held = set(json.loads(r["v"] or "[]"))
                    except (TypeError, ValueError):
                        continue
                    if held & theirs:
                        out.append(f"{table}.{col} row {r['rid']}")
                continue
            target = _POINTS_AT.get(col)
            if not target:
                continue
            hit = conn.execute(
                f"SELECT t.rowid AS rid FROM {table} t JOIN {target} x ON x.id = t.{col} "
                "WHERE t.household_id = ? AND x.household_id = ?", (b, A)).fetchall()
            out += [f"{table}.{col} row {r['rid']}" for r in hit]
    conn.close()
    return out


@pytest.fixture
def two_households(a_household_named):
    with tools.use_household(A):
        _seed_a()
    ids = _a_ids()
    b = households.create_household("Beta Chatters", "chat-isolation-b-passphrase")
    assert b != A
    with tools.use_household(b):
        tools.add_member("Bea")
    return b, ids


@pytest.mark.parametrize("name,build", CASES, ids=[f"{c[0]}-{n}" for n, c in enumerate(CASES)])
def test_household_bs_chat_tool_cannot_reach_household_as_rows(two_households, name, build):
    b, ids = two_households
    fn = agent.TOOL_FUNCTIONS[name]
    before = _owned_state()
    with tools.use_household(b):
        try:
            said = repr(fn(**build(ids)))
        except Exception as exc:  # a refusal is a fine answer; it must not leak either
            # ...but a database error is not a refusal: a guard that closed
            # the connection under its own caller surfaced as "Cannot
            # operate on a closed database", which hid the real answer.
            assert not isinstance(exc, sqlite3.Error), f"{name} crashed instead of refusing: {exc!r}"
            said = f"{type(exc).__name__}: {exc}"
        afterwards = _b_views()
    damage = _damage(before, _owned_state()) + [
        f"B's {r} now points at A's row" for r in _bs_rows_pointing_at_as(b)]
    assert not damage, f"{name} with A's id changed A's data: {damage}"
    for where, text in (("its answer", said), ("B's own lists afterwards", afterwards)):
        leaked = [m for m in MARKERS if m in text]
        assert not leaked, f"{name} with A's id showed A's {leaked} in {where}:\n{text[:800]}"


def test_a_refused_member_leaves_a_callers_connection_open(two_households):
    """hold_thing's foreign-member refusal closes only a connection it
    opened: the one caller that passes its own (weekly_plan._release_prep_rows)
    is mid-transaction, and must still be able to roll back."""
    b, ids = two_households
    conn = get_conn()
    try:
        with tools.use_household(b):
            with pytest.raises(ValueError):
                tools.hold_thing("injected", member_id=ids["member"], conn=conn)
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()
