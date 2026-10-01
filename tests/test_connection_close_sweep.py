"""
No function in app/ opens its own connection, WRITES through it, and leaves
the close where an exception can skip it.

READ THE SEVERITY HONESTLY, AND DO NOT READ THIS FILE'S COUNT AS A COUNT OF
BUGS. Nothing here is a reported failure. This card went through a
regression review (2026-09-27) which bounded it sharply, and the bound is
the most important thing on it:

    The title overstates it. This card's own findings say not to wrap 176
    call sites (2 of 2 hand-checked hits were false positives; reachable
    cases are plausibly zero). ... do not start from 349, or 176, or 288,
    or 21. A sweep for this class needs control-flow awareness to be worth
    running; without it the false-positive rate on the sharpest-looking
    candidates was 2 out of 2.

So this sweep deliberately does NOT answer "is there a reachable leak
here". That question needs to know what can raise between the open and the
close, and the two sharpest-looking candidates anybody has hand-checked
were both wrong. It answers a different question, which is DECIDABLE and
which it can be exactly right about:

    IS THE CLOSE PROTECTED?

That is a property of the SHAPE of a function, not of what can happen
inside it, so there is no false-positive rate to quote — only a definition
to agree with. A function on the census below is not accused of leaking; it
is recorded as not being protected if it ever does.

WHY A SWEEP AT ALL, GIVEN THAT. Because the class is still GROWING, and
that is the whole argument for the fix this branch builds. Quoting the same
review:

    app/tools/cap_enforce.py — a module written tonight — has two
    connections. One is exactly right. The other closes outside a try. ...
    Nothing in the repo makes the correct shape the default, so the count
    does not shrink by attention — it grows by one every time somebody adds
    a module.

db.write() (app/db.py) is now the correct shape, available in the opener.
This file is what stops the count growing while the census is worked
through: the allowlist is asserted by EQUALITY, so a NEW unsafe-shaped
writer is a red test NAMING IT, and a FIXED one is a red test saying take
it off the list.

WHAT COUNTS AS A CANDIDATE, exactly:
  * a function (or method) anywhere under app/ that OPENS A CONNECTION OF
    ITS OWN — get_conn(), or db.write(), which opens one too — attributed
    to the innermost enclosing def, so a nested helper's connection is the
    nested helper's;
  * AND writes through it: a .commit() or .executescript() call, or an
    INSERT / UPDATE / DELETE / REPLACE / CREATE / DROP / ALTER / BEGIN
    statement in a string literal or an f-string.

SAFE means no bare close AND a close in a `finally`, or the connection
taken under `with closing(...)` / `with write()`. ANY unprotected close
makes a function unsafe-shaped, even beside a protected one — see
test_a_function_with_one_protected_and_one_bare_close_is_not_safe for the
three functions in app/ that are really that shape and why the looser rule
("every close is in a finally") read them as safe.

TWO DELIBERATE EXEMPTIONS, both narrower than they sound:

  1. A FUNCTION THAT TAKES A CALLER'S conn AND NEVER OPENS ONE IS NOT A
     CANDIDATE, BY CONSTRUCTION. Those functions deliberately neither
     commit nor close — the caller owns the connection and the transaction,
     and closing it from inside would close something the caller is still
     writing through. That contract is CLAUDE.md's swap-atomic /
     atomic-period-takeover / away-night-atomic entries, three separate
     fixes that exist because one transaction has to span several
     functions. The exemption needs no list: the sweep only ever looks at
     functions that open a connection of their own, so a pure conn-taker
     is never seen. Asserted by
     test_a_function_that_only_takes_a_callers_conn_is_never_a_candidate.

     A function with `conn=None` that FALLS BACK to opening its own IS a
     candidate, and should be — that branch really does own a connection.
     It cannot be fixed by wrapping the whole body in db.write(), which
     would close the caller's connection on the other branch; it needs the
     own-conn branch broken out. The census marks those.

  2. A READ-ONLY function is passed over, and the write lock is why. The
     expensive property of this class is that the leaked connection holds
     SQLite's WRITE lock, because the write had already begun: the next
     writer waits out the busy timeout and fails, and tools.record_error —
     which has to write — cannot record any of it. A leaked read-only
     connection costs a file descriptor until refcounting takes it, which
     is survivable and invisible. cap_enforce.py is the clean
     demonstration and is pinned below: of its three connections, the
     WRITER (_note_move) is protected and is reported SAFE, and the two
     unprotected ones are read-only and are passed over.

MEASURED, 2026-10-01, on this branch: 497 functions in app/ open their own
connection; 242 of those write and 255 only read; 71 of the writers are
protected and 171 are not. On main the writers were 241, of which 68
protected and 173 not — the two that moved are the proof-of-shape tranche
below, and the extra candidate is db.write() itself, which opens a
connection, writes and is swept by its own sweep. The 71 are not an
accident: they are, almost exactly, the functions CLAUDE.md records
somebody deliberately protecting
(_replace_slot_entries, _release_plan_days, _settle_weekly_plan_approval,
_settle_slot_empty, _claim_inventory_depletion, record_error,
_maybe_auto_attribute_solo_night ...), which is the best evidence there is
that this sweep is measuring the thing those fixes were about.

WHAT IT CANNOT SEE, asserted rather than described (see
test_what_this_sweep_cannot_see), so that whoever teaches it more gets a
red test and deletes a limitation instead of discovering one:
  * REACHABILITY. Stated above; it is the point, not an oversight.
  * WHICH connection a close closes. The sweep counts closes, it does not
    match them to opens. A function holding two connections where both are
    protected reads SAFE even if one `finally` closes the same one twice.
  * SQL built by a HELPER FUNCTION — `conn.execute(_update_sql(col))` with
    the literal in another function reads as read-only and is passed over.
    (A literal assigned to a LOCAL VARIABLE first IS seen, which is not
    what the first draft of this docstring claimed: _writes asks whether
    the function CONTAINS a write-shaped literal, not whether that literal
    reaches .execute. The test below caught the false claim, which is the
    whole reason these are asserted rather than described.)
  * The looseness that follows from that, stated as what it is rather than
    hidden: any write-shaped string anywhere in a function makes it a
    writer, DOCSTRINGS INCLUDED. A read-only function whose docstring
    begins "UPDATE the household's goals, one day." lands on the census.
    That errs toward sweeping, which is the right direction for a guard
    whose job is to not miss the next unsafe writer — a wrongly-censused
    read-only function costs one line on a list, and a missed writer costs
    the thing this card is about.
  * A connection opened by a helper of ours and handed back. get_conn and
    db.write are the only openers this knows.
  * A close reached through something other than `.close()` — a name
    bound to the bound method, say.

RED AGAINST MAIN IS 4 OF 24, decomposed rather than quoted. Measured by
reverting app/ and re-running: the equality guard goes red naming
recipes.mark_recipe_feedback and recipes.attribute_recipe_feedback as
UNPROTECTED (a real behaviour catch — on main they are), both parameters of
test_the_two_functions_this_branch_converted_read_as_safe go red on
`assert False is True` (also real), and
test_the_opener_is_importable_under_both_spellings_the_census_will_need
dies on `ImportError: cannot import name 'write'` (a NAME, not a catch).
So: 3 behaviour catches, 1 name. The other 20 are green on both trees and
each says in its own docstring which mutation pins it — the mutation
results, with red counts, are in the branch's CLAUDE.md entry.

It reads app/ with `ast` and never line by line. CLAUDE.md's
leftover-chain-household-filter entry (2026-09-24) records the reason in
one sentence: this repo's first sweep of this shape read quoted runs line
by line and was defeated by a PURE REFORMAT — rewriting one guarded
statement as a triple-quoted block took the guard off with the whole suite
green. Parse, never grep.
"""
import ast
import collections
import functools
import os
import re

import pytest

_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

# A statement that takes the write lock. BEGIN is in here because a function
# whose only SQL is `BEGIN IMMEDIATE` plus calls into conn-taking helpers is
# still the one holding the lock.
_WRITE_SQL = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|BEGIN)\b", re.I)
_WRITE_CALLS = {"commit", "executescript"}
# Context managers that close what they are given. `closing` is
# contextlib's; `write` is app.db's, added by this branch.
_CLOSING_CMS = {"closing", "write"}


# --------------------------------------------------------------------------
# The reader.
# --------------------------------------------------------------------------

def _opens_a_connection(call: ast.Call) -> bool:
    """
    A call that opens a connection of this function's own: get_conn()
    however it is spelled (bare, or db.get_conn()), OR db.write(), the
    opener this branch adds.

    write() HAS to count as an opener, and that was found by converting two
    functions and watching them vanish from the census altogether rather
    than move to its SAFE side. Vanishing is harmless for the equality
    assertion (not unsafe either way) and quietly wrong everywhere else:
    _SAFE_FLOOR would decay as the census is worked through, the
    converted-function guards would have nothing to look up, and a
    function that used write() AND left a second bare close behind would
    be invisible — which is the one mistake a half-done conversion makes.

    `write` is matched as a BARE NAME, or as an attribute of `db`/`_db`
    specifically, and never as a plain attribute: app/recipe_photos.py
    line 112 is `f.write(...)` on a file, and a reader that counted every
    `.write(` would call that an opener.
    """
    f = call.func
    if isinstance(f, ast.Name):
        return f.id in ("get_conn", "write")
    if isinstance(f, ast.Attribute):
        if f.attr == "get_conn":
            return True
        if f.attr == "write":
            return isinstance(f.value, ast.Name) and f.value.id in ("db", "_db")
    return False


def _own_nodes(fn) -> list[ast.AST]:
    """
    Every node inside fn that is not inside a nested def. A nested
    function's connection is the nested function's problem, and it is
    reported under its own name — otherwise a closure that opens one would
    be attributed to whichever function happens to enclose it, which is
    neither where the bug is nor where the fix goes.
    """
    out: list[ast.AST] = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            out.append(child)
            walk(child)

    walk(fn)
    return out


def _sql_literals(nodes) -> list[str]:
    """
    Every string this function hands to SQLite that can be read off the
    tree: plain constants and the constant parts of an f-string. A call
    inside an f-string contributes nothing rather than swallowing the text
    around it, which is the household-scope sweep's own behaviour and for
    its reason.
    """
    out = []
    for n in nodes:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            out.append(
                "".join(
                    v.value for v in n.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
            )
    return out


def _writes(nodes) -> bool:
    for n in nodes:
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in _WRITE_CALLS
        ):
            return True
    return any(_WRITE_SQL.match(s) for s in _sql_literals(nodes))


def _close_shape(fn) -> tuple[int, int, int]:
    """
    (bare closes, closes inside a finally, connections taken under a
    closing context manager). The finally depth is tracked by walking the
    tree rather than by looking for the word, so a close nested three
    blocks deep inside a finally still counts as protected.
    """
    bare = fin = cm = 0

    def walk(nodes, depth):
        nonlocal bare, fin, cm
        for c in nodes:
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "close":
                if depth:
                    fin += 1
                else:
                    bare += 1
            if isinstance(c, ast.Try):
                walk(c.body, depth)
                for h in c.handlers:
                    walk(h.body, depth)
                walk(c.orelse, depth)
                walk(c.finalbody, depth + 1)
                continue
            if isinstance(c, (ast.With, ast.AsyncWith)):
                for item in c.items:
                    ce = item.context_expr
                    if isinstance(ce, ast.Call):
                        f = ce.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                        if name in _CLOSING_CMS:
                            cm += 1
                walk(c.body, depth)
                continue
            walk(list(ast.iter_child_nodes(c)), depth)

    walk(list(ast.iter_child_nodes(fn)), 0)
    return bare, fin, cm


def _app_files():
    for root, _dirs, names in os.walk(_APP):
        for name in sorted(names):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, os.path.dirname(_APP)).replace(os.sep, "/")
                yield rel, path


@functools.lru_cache(maxsize=1)
def _candidates_cached() -> tuple[dict, ...]:
    """
    Cached for the session: every test in this file asks the same question
    of the same ~60 files, and parsing them 24 times over took 18s of a
    14-minute suite. Keyed on nothing, because app/ does not change inside
    a run — the per-mutation measurements in this branch's CLAUDE.md entry
    are separate pytest sessions, so each gets its own parse.
    """
    return tuple(_scan())


def _candidates() -> list[dict]:
    """A fresh list of copies, so a test cannot poison the cache for the next."""
    return [dict(c) for c in _candidates_cached()]


@functools.lru_cache(maxsize=None)
def _parse(path: str) -> ast.Module:
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read())


def _scan() -> list[dict]:
    """Every function in app/ that opens its own connection and writes through it."""
    out = []
    for rel, path in _app_files():
        tree = _parse(path)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = _own_nodes(fn)
            if not any(isinstance(n, ast.Call) and _opens_a_connection(n) for n in nodes):
                continue
            if not _writes(nodes):
                continue
            bare, fin, cm = _close_shape(fn)
            out.append({
                "file": rel, "name": fn.name, "lineno": fn.lineno,
                "bare": bare, "finally": fin, "closing_cm": cm,
                "takes_conn": any(
                    a.arg == "conn" for a in list(fn.args.args) + list(fn.args.kwonlyargs)
                ),
                "safe": bare == 0 and (fin > 0 or cm > 0),
            })
    return out


def _unsafe() -> collections.Counter:
    """(file, function) -> how many definitions of that name are unsafe-shaped."""
    found: collections.Counter = collections.Counter()
    for c in _candidates():
        if not c["safe"]:
            found[(c["file"], c["name"])] += 1
    return found


# --------------------------------------------------------------------------
# THE CENSUS. 171 unsafe-shaped writers in 43 files, measured 2026-10-01 on
# this branch (173 before it converted recipes.mark_recipe_feedback and
# recipes.attribute_recipe_feedback as a proof of shape).
#
# ALLOWED TO SHRINK, NEVER TO GROW. A new entry here is somebody silencing
# the guard instead of writing `with write() as conn:`.
#
# These are SHAPES, not bugs. See the module docstring. Three entries are
# worth reading before converting anything:
#   * app/db.py init_db — a startup path; the process is dying anyway if
#     the schema or the migrations raise, so it is the cheapest of the 171
#     and is left for that reason rather than overlooked.
#   * the own_conn pattern (conn=None with a get_conn fallback) CANNOT be
#     converted by wrapping the whole body — it would close a connection
#     the caller still owns. Marked ^ below.
#   * app/tools/weekly_plan.py, defrost.py, meal_variety.py and
#     cap_enforce.py were off-limits to this branch (other builders were in
#     them), so none of their entries was even considered for conversion.
# --------------------------------------------------------------------------
UNPROTECTED_WRITERS: dict[str, list[str]] = {
    "app/calendar_feed.py": [
        "_store_error", "_store_read", "connect", "disconnect"
    ],
    "app/db.py": [
        "init_db"
    ],
    "app/households.py": [
        "create_household", "set_passphrase"
    ],
    "app/tools/attendance.py": [
        "_write", "clear_slot_attendance"
    ],
    "app/tools/attention.py": [
        "add_attention_item", "record_attention_item_usage",
        "resolve_attention_item"
    ],
    "app/tools/batch_components.py": [
        "_batch_rows", "clear_batch_component", "set_batch_component"
    ],
    "app/tools/batch_undo.py": [
        "_write_derived"
    ],
    "app/tools/big_meal.py": [
        "_delete_prep", "_forget_moved", "_mark_entry", "_save_menu",
        "_unmark_entry", "_write_sides", "spread_prep"
    ],
    "app/tools/chores.py": [
        "add_chore", "generate_chore_schedule", "schedule_chore_instance",
        "set_chores_enabled", "set_chores_profile", "update_chore"
    ],
    "app/tools/cook_ahead.py": [
        "_save_derived", "mark_cook_ahead_asked"
    ],
    "app/tools/cooker.py": [
        "_use_inventory_row_by_id", "check_off_meal", "check_off_prep_step",
        "save_prep_tasks", "start_cooking"
    ],
    "app/tools/defrost.py": [
        "_release_frozen_item", "confirm_frozen_items",
        "defrost_task_from_ready_made", "mark_defrost_asked"
    ],
    "app/tools/digest.py": [
        "_run_household_evening", "set_evening_nudge_for_member",
        "set_morning_text_for_member"
    ],
    "app/tools/draft_flags.py": [
        "add", "dismiss", "record", "refresh"
    ],
    "app/tools/first_open.py": [
        "mark_first_open_seen"
    ],
    "app/tools/grocery.py": [
        "_recompute_plan_line_from_ledger", "_reverse_meal_grocery_contributions",
        "add_grocery_item", "clear_grocery_list", "clear_stale_grocery_items",
        "consolidate_grocery_list", "drop_carried_over_item", "exclude_grocery_item",
        "include_grocery_item", "keep_carried_over_item",
        "move_grocery_item_to_inventory", "remove_grocery_item",
        "repair_grocery_quantities", "set_aside_carried_over_items",
        "substitute_grocery_item", "undo_carried_over_decision", "undo_substitution",
        "update_grocery_item"
    ],
    "app/tools/held.py": [
        "hold_thing", "resolve_held_thing", "restore_held_thing"
    ],
    "app/tools/holidays.py": [
        "answer_holiday", "set_holiday_region"
    ],
    "app/tools/household.py": [
        "_log_preference_event", "add_pet", "set_household_goals",
        "set_member_dietary_restrictions"
    ],
    "app/tools/inventory.py": [
        "_add_to_inventory", "remove_inventory_item", "set_inventory_location",
        "step_inventory_expiration", "step_inventory_quantity", "update_inventory"
    ],
    "app/tools/meal_plans.py": [
        "create_weekly_plan", "plan_meal"
    ],
    "app/tools/meal_variety.py": [
        "_write_batches", "enforce_snacks_per_day"
    ],
    "app/tools/memory.py": [
        "add_fact", "delete_fact", "delete_preference", "edit_preference",
        "update_fact"
    ],
    "app/tools/notifications.py": [
        "dismiss_notification"
    ],
    "app/tools/plan_quality.py": [
        "_swap_entry_dates", "repair_recipe_titles"
    ],
    "app/tools/plates.py": [
        "_set_side_field", "attach_sides", "remove_component"
    ],
    "app/tools/pre_shop.py": [
        "drop_grocery_item_pre_shop", "keep_all_pre_shop_flags",
        "mark_grocery_item_already_have_reviewed", "undo_pre_shop_drop"
    ],
    "app/tools/preferences.py": [
        "add_food_dislikes", "add_store_typical_items", "add_usual_stores",
        "dismiss_stores_prompt", "remove_item_from_all_stores_typical_list",
        "remove_store_typical_item", "save_onboarding_answers",
        "set_household_meal_preferences"
    ],
    "app/tools/prep_sessions.py": [
        "add_prep_cut", "set_skip_prep_this_week"
    ],
    "app/tools/recipes.py": [
        "_record_grocery_link", "add_recipe", "flag_recipe_temporary",
        "log_cooking_deviation", "log_recipe_note", "save_cooking_quantities",
        "update_recipe_details"
    ],
    "app/tools/reset.py": [
        "clear_weekly_plan"
    ],
    "app/tools/rhythm.py": [
        "clear_lunch_location_override", "set_cooking_role", "set_dinner_window",
        "set_leftovers_stance", "set_lunch_location", "set_meals_together",
        "set_planning_anchor", "set_prep_days"
    ],
    "app/tools/sharing.py": [
        "eater_add_note", "get_or_create_member_share_link",
        "get_or_create_share_link", "revoke_member_share_link"
    ],
    "app/tools/slot_needs.py": [
        "clear_slot_need", "confirm_slot_recommendation", "set_away_stretch",
        "set_slot_recommendation"
    ],
    "app/tools/spices.py": [
        "list_spices_this_week", "tick_spice"
    ],
    "app/tools/staples.py": [
        "add_staple", "decide_staple_line", "mark_staple_plenty", "pause_staple",
        "record_staple_purchase", "remove_staple", "sync_due_staples",
        "undo_staple_decision", "unrecord_staple_purchase"
    ],
    "app/tools/stores.py": [
        "close_shopping_trip", "set_item_store"
    ],
    "app/tools/swap_in_place.py": [
        "_write_entry_note"
    ],
    "app/tools/typed_requests.py": [
        "_cite", "_note_must_use"
    ],
    "app/tools/week_intake.py": [
        "clear_week_intake"
    ],
    "app/tools/weekday_lunches.py": [
        "_save_derived"
    ],
    "app/tools/weekly_plan.py": [
        "_dedupe_duplicate_slots", "_freeze_instead", "attach_intake_to_plan",
        "clear_plan_slot", "discard_draft_plan", "drop_dish_from_day",
        "mark_plates_intro_shown", "plan_slot_empty", "plan_slot_open",
        "record_plan_requests", "reopen_weekly_plan", "repair_leftover_chains",
        "retire_expired_drafts", "set_planning_mode", "set_week_constraints",
        "swap_component_in_plan", "swap_meal_in_plan"
    ],
    "app/tools/yesterday_check.py": [
        "answer_yesterday"
    ],
}

# The conn=None-with-a-get_conn-fallback shape, picked out of the census
# above rather than left as a comment, because the distinction decides HOW
# each one is fixed: these cannot be converted by wrapping the whole body
# in db.write() -- that would close a connection the caller still owns on
# the other branch. The own-conn branch has to be broken out first.
# test_every_own_conn_fallback_entry_really_takes_a_conn keeps it honest.
_OWN_CONN_FALLBACK: frozenset[tuple[str, str]] = frozenset({
    ("app/tools/grocery.py", "_recompute_plan_line_from_ledger"),
    ("app/tools/grocery.py", "_reverse_meal_grocery_contributions"),
    ("app/tools/grocery.py", "add_grocery_item"),
    ("app/tools/grocery.py", "set_aside_carried_over_items"),
    ("app/tools/held.py", "hold_thing"),
    ("app/tools/inventory.py", "_add_to_inventory"),
    ("app/tools/meal_plans.py", "plan_meal"),
    ("app/tools/recipes.py", "_record_grocery_link"),
    ("app/tools/weekly_plan.py", "clear_plan_slot"),
    ("app/tools/weekly_plan.py", "plan_slot_empty"),
    ("app/tools/weekly_plan.py", "plan_slot_open"),
})


def _expected() -> collections.Counter:
    expected: collections.Counter = collections.Counter()
    for rel, names in UNPROTECTED_WRITERS.items():
        for name in names:
            expected[(rel, name)] += 1
    return expected


# --------------------------------------------------------------------------
# THE GUARD.
# --------------------------------------------------------------------------

def test_no_new_function_opens_a_connection_writes_and_leaves_the_close_unprotected():
    """
    CATCH for anything added after this commit; GREEN on main, because on
    main the census IS main's census and this file does not exist there.
    That is the honest label: what this test catches is the 172nd, not the
    171 it lists.

    Equality rather than containment, deliberately — the same choice
    test_household_scope_sweep.py made and for its reason: fixing
    something on the list makes this go red saying "take it off", which is
    what keeps the list shrinking honestly instead of outliving the work.
    """
    found = _unsafe()
    expected = _expected()
    if found == expected:
        return

    new = found - expected
    fixed = expected - found
    lines = []
    for (rel, name), n in sorted(new.items()):
        lines.append(f"  UNPROTECTED: {rel}  {name}()" + (f"   x{n}" if n > 1 else ""))
    for (rel, name), n in sorted(fixed.items()):
        lines.append(
            f"  NOW PROTECTED, take it off UNPROTECTED_WRITERS: {rel}  {name}()"
            + (f"   x{n}" if n > 1 else "")
        )
    raise AssertionError(
        "the connection-close sweep disagrees with UNPROTECTED_WRITERS.\n\n"
        "A function that opens its own connection and WRITES through it must "
        "close it however the block leaves. Use the opener:\n\n"
        "    from ..db import write        # or `from .db import write`\n\n"
        "    with write() as conn:\n"
        "        conn.execute(...)\n\n"
        "write() commits on a clean exit, rolls back if the block raises, and "
        "closes either way — so an existing conn.commit() can stay (a second "
        "commit is a no-op) and an existing conn.close() comes out. If the "
        "function takes a caller's conn and falls back to opening its own, the "
        "own-conn branch has to be broken out first: wrapping the whole body "
        "would close the caller's connection.\n\n"
        "Do NOT add an entry to UNPROTECTED_WRITERS to make this pass. That "
        "list is only allowed to shrink.\n\n" + "\n".join(lines)
    )


# --------------------------------------------------------------------------
# THE GUARD ON THE GUARD. This repo has recorded a sweep silently stopping
# matching twice; a sweep that quietly finds nothing passes for ever.
# --------------------------------------------------------------------------

_CANDIDATE_FLOOR = 200
_SAFE_FLOOR = 50


def test_the_sweep_still_finds_the_functions_it_is_named_for():
    """
    GUARD. Blind the reader — point _opens_a_connection at a name nothing
    calls, or make _writes return False — and the census empties, which the
    equality test above would report as 171 fixes rather than as a broken
    sweep. These floors are what tell the two apart.

    MUTATION that pins it: `return False` at the top of
    _opens_a_connection (this and the equality test both go red).
    """
    cands = _candidates()
    assert len(cands) >= _CANDIDATE_FLOOR, (
        f"the sweep found only {len(cands)} functions in app/ that open their own "
        f"connection and write through it, against a floor of {_CANDIDATE_FLOOR}. "
        "Either a very large tranche has landed, or the reader has stopped "
        "matching — check _opens_a_connection and _writes before lowering this."
    )
    assert sum(1 for c in cands if c["safe"]) >= _SAFE_FLOOR, (
        "the sweep can no longer see a PROTECTED writer, so its 'safe' verdict "
        "means nothing — check _close_shape's finally handling."
    )


def test_the_sweep_finds_the_one_instance_whose_correct_shape_is_already_proven():
    """
    GUARD, and the validation case the card itself names: "a sweep that
    could not find the one instance already proven would not be worth
    quoting."

    recipes._maybe_auto_attribute_solo_night is where the cost of this
    class was MEASURED (CLAUDE.md 2026-09-26: the next write waited out the
    full 5s busy timeout and then 500'd, and error_events held nothing at
    all for the crash). It grew a try/finally on both its connections in
    that commit. So the sweep must (a) see it at all and (b) report it
    SAFE — if it could not see it, its "safe" verdict there would be
    vacuous, and the whole census would be a list of functions the reader
    happened to notice.

    MUTATION that pins it: make _close_shape count a finally-close as bare.
    """
    by_name = {(c["file"], c["name"]): c for c in _candidates()}
    key = ("app/tools/recipes.py", "_maybe_auto_attribute_solo_night")
    assert key in by_name, (
        "the sweep cannot see recipes._maybe_auto_attribute_solo_night, the one "
        "function in this repo whose leak was measured and whose fix is recorded. "
        "Its verdict on everything else is worth nothing until it can."
    )
    found = by_name[key]
    assert found["safe"] is True, f"expected SAFE, got {found}"
    assert found["bare"] == 0 and found["finally"] == 2, (
        f"it has two connections, both closed in a finally; sweep read {found}"
    )


@pytest.mark.parametrize("rel,name", [
    ("app/tools/recipes.py", "mark_recipe_feedback"),
    ("app/tools/recipes.py", "attribute_recipe_feedback"),
])
def test_the_two_functions_this_branch_converted_read_as_safe(rel, name):
    """
    GUARD. The proof-of-shape tranche: the two sites the card's reviewer
    named with line numbers and an argument. They were unsafe-shaped on
    main and are `with write()` now, so the sweep must report them safe —
    and they must NOT be on the census. If this goes red because somebody
    reverted them, the equality test above goes red too and says so.
    """
    by_name = {(c["file"], c["name"]): c for c in _candidates()}
    found = by_name[(rel, name)]
    assert found["safe"] is True, f"{name} reads unsafe: {found}"
    assert found["closing_cm"] >= 1, f"{name} should be under `with write()`: {found}"
    assert name not in UNPROTECTED_WRITERS.get(rel, []), (
        f"{name} is protected now but is still on UNPROTECTED_WRITERS"
    )


def test_a_read_only_function_is_passed_over_and_cap_enforce_is_the_demonstration():
    """
    GUARD for the read-only exemption, on real code rather than a fixture.
    app/tools/cap_enforce.py holds all three shapes at once, which is why
    the card's own review used it as the example of the class growing:

      * _note_move WRITES and closes in a finally          -> SAFE, seen
      * _load_dinners reads, closes outside a try          -> passed over
      * _whole_dish_nights reads, closes outside a try     -> passed over

    The two unprotected ones are deliberately not on the census: a leaked
    READ connection costs a file descriptor, not the write lock, and the
    write lock is this card's whole severity. If a sweep of read-only leaks
    is ever wanted it is a different card with a different argument.

    MUTATION that pins it: make _writes return True unconditionally — the
    equality test goes red with ~100 read-only functions on it, and this
    one names the two.
    """
    seen = {c["name"] for c in _candidates() if c["file"] == "app/tools/cap_enforce.py"}
    assert "_note_move" in seen, "the protected writer in cap_enforce.py is not seen"
    assert "_load_dinners" not in seen and "_whole_dish_nights" not in seen, (
        "cap_enforce.py's two read-only connections are being swept; this sweep "
        "is about the write lock — see the module docstring."
    )
    assert ("app/tools/cap_enforce.py", "_load_dinners") not in _expected()
    assert ("app/tools/cap_enforce.py", "_whole_dish_nights") not in _expected()


def test_a_function_that_only_takes_a_callers_conn_is_never_a_candidate():
    """
    GUARD for exemption 1, and the reason it needs no list: a function that
    is HANDED a connection opens none of its own, so the sweep never looks
    at it. app/household_deletion.py's helpers are the clean example —
    _delete_rows(conn, table, household_id) writes and must neither commit
    nor close, because delete_household owns the transaction that spans all
    of them.

    MUTATION that pins it: make _candidates() consider any function with
    `conn` in its signature — these three appear and the equality test
    goes red.
    """
    seen = {(c["file"], c["name"]) for c in _candidates()}
    for name in ("_delete_rows", "_begin", "household_tables"):
        assert ("app/household_deletion.py", name) not in seen, (
            f"{name} takes a caller's conn and opens none of its own; it must "
            "never be swept, or a 'fix' would close a connection the caller "
            "is still writing through."
        )
    # ...and the function that DOES own the transaction is seen, and is safe.
    assert ("app/household_deletion.py", "delete_household") in seen


def test_a_conn_defaulting_function_that_falls_back_to_opening_one_IS_a_candidate():
    """
    GUARD for the other half of exemption 1, which is the half most easily
    got wrong. held.hold_thing takes `conn=None` and opens its own when
    none is given, closing it with `if own_conn: conn.close()` — an
    unprotected close on a connection it really does own. It is a
    candidate, and it is on the census.

    It is ALSO the one shape that must not be converted by wrapping the
    whole body: `with write() as conn:` there would close the caller's
    connection on the other branch. Whoever takes it off the census has to
    break the own-conn branch out first.
    """
    by_name = {(c["file"], c["name"]): c for c in _candidates()}
    found = by_name[("app/tools/held.py", "hold_thing")]
    assert found["takes_conn"] is True and found["safe"] is False, found
    assert "hold_thing" in UNPROTECTED_WRITERS["app/tools/held.py"]


def test_a_function_with_one_protected_and_one_bare_close_is_not_safe():
    """
    GUARD, and it records a rule this sweep was nearly written without.
    The obvious definition — "every close is inside a finally" — reads a
    function holding TWO connections, one protected and one not, as SAFE.
    Three functions in app/ are really that shape (measured 2026-10-01:
    weekly_plan.drop_dish_from_day, weekly_plan._freeze_instead,
    meal_variety._write_batches), so the looser rule would have waved
    three genuine unprotected writes through.

    Hence: ANY bare close makes a function unsafe-shaped. The cost is that
    a bare close on a connection whose block demonstrably cannot raise also
    reads unsafe — but "cannot raise" is reachability, and this sweep
    refuses to judge that. See the module docstring.

    MUTATION that pins it: `safe = fin > 0 or cm > 0` (drop the
    `bare == 0`) — these three drop off the census and the equality test
    goes red asking for them to be taken off UNPROTECTED_WRITERS.
    """
    by_name = {(c["file"], c["name"]): c for c in _candidates()}
    mixed = [
        ("app/tools/weekly_plan.py", "drop_dish_from_day"),
        ("app/tools/weekly_plan.py", "_freeze_instead"),
        ("app/tools/meal_variety.py", "_write_batches"),
    ]
    for key in mixed:
        found = by_name[key]
        assert found["bare"] >= 1 and found["finally"] >= 1, (
            f"{key[1]} is meant to hold one protected and one bare close; "
            f"sweep read {found}. If it was tidied up, pick another example."
        )
        assert found["safe"] is False, f"{key[1]} read SAFE with a bare close: {found}"
        assert key[1] in UNPROTECTED_WRITERS[key[0]]


@pytest.mark.parametrize("src,expected_safe", [
    # The shape the card is about.
    ("""
def f():
    conn = get_conn()
    conn.execute("UPDATE t SET x = 1")
    conn.commit()
    conn.close()
""", False),
    # try/finally — the shape CLAUDE.md records as the fix.
    ("""
def f():
    conn = get_conn()
    try:
        conn.execute("UPDATE t SET x = 1")
        conn.commit()
    finally:
        conn.close()
""", True),
    # The opener this branch adds.
    ("""
def f():
    with write() as conn:
        conn.execute("UPDATE t SET x = 1")
""", True),
    # contextlib.closing, which nothing in app/ uses today but which is
    # correct and must not be reported.
    ("""
def f():
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM t")
        conn.commit()
""", True),
    # A close nested deep inside a finally still counts.
    ("""
def f():
    conn = get_conn()
    try:
        conn.execute("INSERT INTO t VALUES (1)")
    finally:
        if conn is not None:
            for _ in (1,):
                conn.close()
""", True),
    # Opened and never closed at all: worse, and still unsafe.
    ("""
def f():
    conn = get_conn()
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
""", False),
    # f-string SQL — the reformat the 2026-09-24 sweep was defeated by,
    # here as a write that must still be seen.
    ("""
def f(col):
    conn = get_conn()
    conn.execute(f"UPDATE t SET {col} = 1 WHERE id = ?", (1,))
    conn.close()
""", False),
    # Implicit concatenation across lines, which a line-by-line reader
    # cannot join and `ast` joins for free.
    ("""
def f():
    conn = get_conn()
    conn.execute(
        "UPDATE t "
        "SET x = 1"
    )
    conn.close()
""", False),
    # A triple-quoted block, which is exactly what defeated the first
    # version of the household-scope sweep.
    ('''
def f():
    conn = get_conn()
    conn.execute("""
        DELETE FROM t
        WHERE id = ?
    """, (1,))
    conn.close()
''', False),
    # db.get_conn() spelled through the module.
    ("""
def f():
    conn = db.get_conn()
    conn.execute("INSERT INTO t VALUES (1)")
    conn.close()
""", False),
])
def test_the_reader_sees_every_shape_app_could_use(src, expected_safe):
    """
    GUARD. The classifier run against each shape directly, so a reader that
    quietly stopped understanding one of them is caught here rather than by
    the census silently shrinking.
    """
    fn = ast.parse(src).body[0]
    nodes = _own_nodes(fn)
    assert any(isinstance(n, ast.Call) and _opens_a_connection(n) for n in nodes)
    assert _writes(nodes) is True
    bare, fin, cm = _close_shape(fn)
    assert (bare == 0 and (fin > 0 or cm > 0)) is expected_safe, (
        f"bare={bare} finally={fin} closing_cm={cm}"
    )


def test_a_nested_helpers_connection_is_the_nested_helpers():
    """
    GUARD. A closure that opens a connection is reported under its OWN
    name, not its enclosing function's — otherwise the census would name a
    function whose body contains no get_conn call at all, and whoever went
    to fix it would not find it.
    """
    src = """
def outer():
    def inner():
        conn = get_conn()
        conn.execute("UPDATE t SET x = 1")
        conn.close()
    return inner
"""
    outer = ast.parse(src).body[0]
    assert not any(
        isinstance(n, ast.Call) and _opens_a_connection(n) for n in _own_nodes(outer)
    ), "the enclosing function was credited with its closure's connection"
    inner = outer.body[0]
    assert any(isinstance(n, ast.Call) and _opens_a_connection(n) for n in _own_nodes(inner))


def test_what_this_sweep_cannot_see():
    """
    GUARD, asserting the limitations rather than describing them — the
    household-scope sweep's own discipline, so that a later reader who
    teaches this more gets a red test and deletes a limitation instead of
    discovering one.
    """
    # SQL built by a helper: the literal is in another function, so this one
    # reads as read-only and is passed over.
    via_helper_sql = ast.parse("""
def f(col):
    conn = get_conn()
    conn.execute(_update_sql(col), (1,))
    conn.close()
""").body[0]
    assert _writes(_own_nodes(via_helper_sql)) is False, (
        "the reader learned to follow a call into another function — good; "
        "delete this limitation from the module docstring and re-measure the "
        "census, which will grow."
    )

    # A literal in a LOCAL VARIABLE, on the other hand, IS seen — because
    # _writes scans every literal in the function rather than tracing which
    # ones reach .execute. The first draft of the docstring claimed the
    # opposite; this is here so the correction cannot drift back.
    via_variable = ast.parse("""
def f():
    conn = get_conn()
    sql = "UPDATE t SET x = 1"
    conn.execute(sql)
    conn.close()
""").body[0]
    assert _writes(_own_nodes(via_variable)) is True

    # ...and the price of that looseness, named: a write-shaped DOCSTRING is
    # a literal too, so a read-only function can land on the census. Erring
    # toward sweeping is the deliberate direction; see the docstring.
    docstring_only = ast.parse(
        "def f():\n"
        '    "UPDATE the household goals, one day."\n'
        "    conn = get_conn()\n"
        '    rows = conn.execute("SELECT 1").fetchall()\n'
        "    conn.close()\n"
        "    return rows\n"
    ).body[0]
    assert _writes(_own_nodes(docstring_only)) is True, (
        "the reader learned to tell a docstring from SQL — good, and the "
        "census may shrink; re-measure."
    )

    # A connection handed back by a helper of ours: not seen at all.
    via_helper = ast.parse("""
def f():
    conn = _open_for_writing()
    conn.execute("UPDATE t SET x = 1")
    conn.close()
""").body[0]
    assert not any(
        isinstance(n, ast.Call) and _opens_a_connection(n) for n in _own_nodes(via_helper)
    ), "the reader learned about a second opener — add it and re-measure"

    # WHICH connection a close closes. Two opens, one finally that closes
    # the same one twice: reads SAFE, and is not.
    double_close = ast.parse("""
def f():
    a = get_conn()
    b = get_conn()
    try:
        b.execute("UPDATE t SET x = 1")
    finally:
        a.close()
        a.close()
""").body[0]
    bare, fin, cm = _close_shape(double_close)
    assert (bare == 0 and (fin > 0 or cm > 0)) is True, (
        "the reader learned to match closes to opens — a real improvement; "
        "delete this limitation and re-measure."
    )


def test_every_own_conn_fallback_entry_really_takes_a_conn_and_is_on_the_census():
    """
    GUARD for the sub-list that decides HOW each of eleven entries gets
    fixed. A stale entry there is worse than no list: it would tell the next
    person a function cannot be wrapped when it can, and the conversion they
    then avoid is the easy kind.

    MUTATION that pins it: add any non-conn-taking name to
    _OWN_CONN_FALLBACK.
    """
    by_name = {(c["file"], c["name"]): c for c in _candidates()}
    for key in sorted(_OWN_CONN_FALLBACK):
        found = by_name.get(key)
        assert found is not None, f"{key} is on _OWN_CONN_FALLBACK and is not swept at all"
        assert found["takes_conn"] is True, (
            f"{key[1]} does not take a `conn` argument, so it is an ordinary "
            "conversion — take it off _OWN_CONN_FALLBACK"
        )
        assert found["safe"] is False
        assert key[1] in UNPROTECTED_WRITERS[key[0]]
    # ...and nothing conn-taking is missing from it, or the list says less
    # than it claims.
    really = {k for k, c in by_name.items() if c["takes_conn"] and not c["safe"]}
    assert really == set(_OWN_CONN_FALLBACK), (
        "_OWN_CONN_FALLBACK is out of date. Really conn-taking and unsafe: "
        f"{sorted(really)}; listed: {sorted(_OWN_CONN_FALLBACK)}"
    )


def test_the_census_names_only_functions_that_exist():
    """
    GUARD. A stale entry is how an allowlist quietly stops meaning
    anything: a renamed or deleted function leaves a name behind that
    nothing can ever take off, and the equality test above would then
    always be red for a reason nobody can fix. Every name on the census
    must really be in its file.
    """
    present = collections.defaultdict(set)
    for rel, path in _app_files():
        for fn in ast.walk(_parse(path)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                present[rel].add(fn.name)
    stale = [
        f"{rel}  {name}"
        for rel, names in UNPROTECTED_WRITERS.items()
        for name in names
        if name not in present.get(rel, set())
    ]
    assert not stale, (
        "UNPROTECTED_WRITERS names functions that are not in those files any "
        "more (renamed, moved or deleted): " + ", ".join(sorted(stale))
    )


def test_the_opener_is_importable_under_both_spellings_the_census_will_need():
    """
    GUARD. Converting a function on the census means importing write(), and
    app/ reaches db.py two ways — `from .db import ...` at the top level and
    `from ..db import ...` inside app/tools/. Both have to work, or the
    first conversion in a new package fails on an import nobody expected.
    """
    import app.db
    from app.db import write as top_level            # `from .db import ...`
    from app.tools.recipes import write as in_tools  # `from ..db import ...`

    assert top_level is app.db.write
    assert in_tools is app.db.write, (
        "app/tools/ reached a different write() — the two spellings must be "
        "the same function, or a conversion in app/tools/ is wrapping "
        "something else."
    )
    assert hasattr(app.db.write, "__wrapped__"), "write() is a @contextmanager"
