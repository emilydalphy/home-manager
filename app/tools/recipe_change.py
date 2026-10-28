"""
"Change recipe" — the household likes the meal idea and not the recipe
Pomona wrote (Gowthami's household, 2026-10-04: "they might like the idea
of having a suggestion meal type, but they don't like that actual
recipe").

The plan's SHAPE is the thing being kept. One meal's recipe is re-pointed
— to one of the household's own, to one just imported from a link, or to a
rewrite of the same dish — and its slot, its day, who is eating and its
leftover chain are all untouched. Only the food changes, and so the
shopping does.

Three things in here are worth reading before changing any of it.

1. THE WRITE IS ONE TRANSACTION, and it has to be. It re-points
   `recipe_id` on the entry AND on the chain's other nights, reverses
   every grocery contribution those entries made, and buys for the new
   recipe — and a failure part-way through would leave a week holding a
   dish whose ingredients had already been taken off the list. Every
   helper it reaches (`_reverse_meal_grocery_contributions`,
   `_ingest_recipe_group_and_sides`, `plan_leftover_chains`) takes a
   `conn`, so the whole thing runs on one, with `BEGIN IMMEDIATE` taken
   before the first read.

2. THE ALLERGY GATE RUNS BEFORE ANYTHING IS WRITTEN. Not after: a check
   below the write means reversing a change the household never asked for
   and dragging the grocery list through it, which is the reasoning
   `swap_in_place.cap_gate` already follows. It is
   `coordination.check_meal_conflicts` — the matcher the draft's own
   banner and the swap gate share, never a second copy of it.

3. THE REQUEST TEXT IS PROSE, AND PROSE HAS A RULE IN THIS REPO. There is
   exactly one precedent for storing what somebody typed — `feedback.py`,
   the "Something not working?" box — and Emily's own rule for it
   (2026-09-08, option (a)) is: keep the prose, let the default morning
   report carry a COUNT only, and print the words behind an explicit flag
   inside a fence marking them untrusted. `recipe_change_requests` follows
   that exactly. Nothing in here is an agent tool and nothing in here is
   re-exported into `agent.TOOL_FUNCTIONS`.

Why `observability_report.py` needs the theme at all: Emily, 2026-10-04 —
"track every request so the common ones can become buttons later". The
grouping is a word list a person can argue with
(`_THEME_WORDS`), on this repo's own stated preference for that over
anything cleverer: it runs over a handful of rows, a wrong answer costs
one misfiled line in a report nobody acts on automatically, and a list is
legible where a judgement is not.
"""
from __future__ import annotations

import json
import logging
import re

from ..db import get_conn
from . import coordination as _coordination
from . import grocery as _grocery
from . import leftovers as _leftovers
from . import quantities as _quantities
from . import recipes as _recipes
from ._shared import household_id, member_id

logger = logging.getLogger("home_manager")

# Long enough for a real sentence or two ("make it less spicy, the kids
# won't touch it, and we don't have a pressure cooker") and short enough
# that the column cannot be used as storage. feedback.MAX_WHAT_HAPPENED's
# reasoning at a tenth of the size: this is one ask, not a bug report.
MAX_REQUEST_TEXT = 500
MAX_DISH_NAME = 200

# Per household, on feedback._prune's reasoning: these arrive at human
# speed, and the whole point of keeping them is to read a few months of
# them at once and see what people ask for most.
_KEEP_ROWS = 300
_KEEP_DAYS = 180

# What a request was answered with, for the "kept or undone" half of
# Emily's ask. 'rewritten' is the state a request lands in; the undo moves
# it to 'undone', and nothing moves it to 'kept' — a request nobody undid
# IS kept, so a second write to say so would be a fact the app made up
# about somebody's silence. The report reads 'rewritten' as kept and says
# which word it is using.
OUTCOME_REWRITTEN = "rewritten"
OUTCOME_UNDONE = "undone"
OUTCOME_FAILED = "failed"
OUTCOMES = (OUTCOME_REWRITTEN, OUTCOME_UNDONE, OUTCOME_FAILED)

# Rough themes, in the card's own five words. Order matters only for a
# request that trips two: the first match wins, so the more specific
# themes come first and "ingredient swap" — the widest of them — last.
# Deliberately a list and not a model call: see the module docstring.
_THEME_WORDS = (
    ("time", ("quick", "quicker", "faster", "fast", "less time", "weeknight", "30 min",
              "20 min", "half an hour", "takes too long", "too long", "shorter", "slow cooker",
              "overnight", "make ahead", "ahead of time")),
    ("equipment", ("instant pot", "pressure cooker", "air fryer", "oven", "stovetop",
                   "one pot", "one pan", "sheet pan", "fewer dishes", "fewer pans",
                   "no blender", "blender", "grill", "wok", "slow-cooker", "microwave")),
    ("spice", ("spicy", "spicier", "less spice", "more spice", "mild", "milder", "heat",
               "chilli", "chili", "chile", "pepper", "bland", "kid-friendly", "kid friendly",
               "for the kids", "too hot")),
    ("authenticity", ("authentic", "more authentic", "traditional", "proper", "the real",
                      "how my mum", "how my mom", "my grandmother", "restaurant",
                      "the way it", "south indian", "north indian", "not authentic")),
    ("ingredient swap", ("instead of", "swap", "replace", "without", "use ", "add ", "no ",
                         "dried", "canned", "fresh", "leave out", "more veg", "extra",
                         "substitute", "sub ")),
)
THEMES = tuple(name for name, _ in _THEME_WORDS) + ("other",)


def request_theme(text: str) -> str:
    """
    Which of the card's five rough themes a request reads as, or 'other'.

    Matched on the lower-cased text, first theme wins. A word list rather
    than anything cleverer, and it is allowed to be wrong: the only reader
    is a heading in a report a person reads, and the request itself is
    printed VERBATIM underneath whichever heading it lands under — so a
    misfiled line costs a person one glance, never a wrong answer in the
    app.
    """
    low = " " + str(text or "").lower().strip() + " "
    if not low.strip():
        return "other"
    for name, words in _THEME_WORDS:
        for word in words:
            if word in low:
                return name
    return "other"


def _prune(conn, hid: int) -> None:
    conn.execute(
        f"DELETE FROM recipe_change_requests WHERE household_id = ? "
        f"AND created_at < datetime('now', '-{_KEEP_DAYS} days')",
        (hid,),
    )
    conn.execute(
        "DELETE FROM recipe_change_requests WHERE household_id = ? AND id NOT IN "
        "(SELECT id FROM recipe_change_requests WHERE household_id = ? ORDER BY id DESC LIMIT ?)",
        (hid, hid, _KEEP_ROWS),
    )


def record_recipe_change_request(
    dish_name: str,
    request_text: str,
    meal_plan_entry_id: int | None = None,
    recipe_id: int | None = None,
    outcome: str = OUTCOME_REWRITTEN,
) -> int | None:
    """
    File one "tell Pomona what to change" request. Never raises.

    `request_text` is stored exactly as typed, minus a length cap. That is
    the feature — see the module docstring and feedback.py's.

    The member is the session's own adult (`member_id()`), not a name the
    caller passes: the card asks for "member (if known)", and the one
    honest answer to that is whoever this device is signed in as. NULL
    where a device has not been asked.

    Returns the row id so the rewrite can move it to 'undone' later, or
    None when nothing was filed.
    """
    conn = None
    try:
        text = str(request_text or "").strip()[:MAX_REQUEST_TEXT]
        if not text:
            return None
        if outcome not in OUTCOMES:
            outcome = OUTCOME_REWRITTEN
        conn = get_conn()
        # record_error's reasoning: give up on a locked database quickly
        # rather than parking a worker, on a path that is a side-record of
        # something else the household actually asked for.
        conn.execute("PRAGMA busy_timeout = 500")
        hid = household_id()
        cur = conn.execute(
            """
            INSERT INTO recipe_change_requests
                (household_id, member_id, dish_name, request_text,
                 meal_plan_entry_id, recipe_id, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (hid, member_id(), str(dish_name or "")[:MAX_DISH_NAME], text,
             meal_plan_entry_id, recipe_id, outcome),
        )
        row_id = cur.lastrowid
        _prune(conn, hid)
        conn.commit()
        return row_id
    except Exception:
        logger.exception("Recording a recipe change request failed")
        return None
    finally:
        if conn is not None:
            conn.close()


def mark_recipe_change_request(request_id: int, outcome: str) -> bool:
    """
    Move one request to its outcome ('undone' when the household put the
    recipe back). Never raises; False when nothing moved.
    """
    if outcome not in OUTCOMES:
        return False
    conn = None
    try:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE recipe_change_requests SET outcome = ? WHERE id = ? AND household_id = ?",
            (outcome, int(request_id), household_id()),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        logger.exception("Marking a recipe change request failed")
        return False
    finally:
        if conn is not None:
            conn.close()


def recent_recipe_change_requests(days: int = 30) -> list[dict]:
    """
    This household's requests from the last `days`, newest first, each
    with the theme it reads as.

    Household-scoped like every other read here. The caller is
    observability_report.py and nothing else — see the module docstring on
    why this is not an agent tool.
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT r.id, r.dish_name, r.request_text, r.outcome, r.created_at, "
            "       r.meal_plan_entry_id, r.recipe_id, m.name AS member_name "
            "FROM recipe_change_requests r "
            "LEFT JOIN members m ON m.id = r.member_id AND m.household_id = r.household_id "
            "WHERE r.household_id = ? AND r.created_at >= datetime('now', ?) "
            "ORDER BY r.created_at DESC, r.id DESC",
            (household_id(), f"-{int(days)} days"),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        row = dict(r)
        row["theme"] = request_theme(row.get("request_text") or "")
        out.append(row)
    return out


def count_recipe_change_requests(days: int = 30) -> int:
    """
    How many requests are on file — a number, never a word of what they
    say. This is the only thing about them the default morning report is
    allowed to know, for feedback.count_feedback_reports' reason exactly.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM recipe_change_requests "
            "WHERE household_id = ? AND created_at >= datetime('now', ?)",
            (household_id(), f"-{int(days)} days"),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"] or 0) if row else 0


# ---------- the write ----------

# What the sheet shows "Change recipe" on, and what the write will accept.
# A reheat night has no cook in it, so there is no recipe behind it to
# change — the honest answer there is to change the source dinner, which
# the Meal step's own "See Thursday's recipe" already walks to, and which
# this repo holds in several other places (moves.py never features one,
# cooker never scales one, time_caps exempts one).
CHANGEABLE_SLOT_STATE = "planned"

# A night written in words rather than planned from a recipe — see
# _is_reheat on why this is here and not borrowed.
_READS_AS_REHEAT = re.compile(r"leftovers?\b", re.IGNORECASE)

NOT_CHANGEABLE = "There’s no recipe on that one to change."
REHEAT_NOT_CHANGEABLE = (
    "That night reheats an earlier batch — change the dinner it comes from and "
    "this one follows."
)
NO_SUCH_RECIPE = "I don’t have that recipe saved."
SAME_RECIPE = "That’s the recipe it already uses."
NO_INGREDIENTS_YET = (
    "That recipe doesn’t have its ingredients yet. Open it in Recipes to finish it first."
)


def _entry_row(conn, entry_id: int):
    return conn.execute(
        "SELECT mpe.id, mpe.weekly_plan_id, mpe.date, mpe.slot, mpe.slot_state, "
        "       mpe.recipe_id, mpe.freeform_meal, mpe.sides_json, "
        "       COALESCE(r.name, mpe.freeform_meal) AS meal, wp.status AS plan_status "
        "FROM meal_plan_entries mpe "
        "LEFT JOIN recipes r ON r.id = mpe.recipe_id "
        "JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "WHERE mpe.id = ? AND mpe.household_id = ?",
        (int(entry_id), household_id()),
    ).fetchone()


def _chain_entry_ids(conn, entry) -> list[int]:
    """
    This meal and the nights that eat off it — "replaces the recipe for
    this meal (and its leftovers)", the card's own words.

    Read off the HONOURED chains (leftovers.plan_leftover_chains, which
    requires both halves to agree), never off the raw derived_from, so a
    half-written chain is treated as no chain here exactly as it is
    everywhere else. A reheat night carries the SAME recipe_id as its
    cook in this app, which is why it has to move with it: leaving it
    behind would point two nights of one chain at two different dishes.

    Deliberately NOT every entry in the plan sharing the old recipe_id.
    An unrelated Thursday that happens to cook the same dish is a
    different meal the household did not open, and changing it would be
    this write deciding something nobody asked for. Its shopping still
    comes out right: the ingest groups by recipe-week, and the entries
    left on the old recipe are simply a smaller group than before.
    """
    ids = [int(entry["id"])]
    chains = _leftovers.plan_leftover_chains(entry["weekly_plan_id"], conn=conn)
    source = chains["sources"].get(int(entry["id"]))
    if source:
        for t in source.get("targets") or []:
            if int(t["entry_id"]) not in ids:
                ids.append(int(t["entry_id"]))
    return ids


def recipe_change_blocked(recipe_name: str, ingredients, sides=None) -> dict | None:
    """
    The allergy gate, run on the dish about to be planned and BEFORE
    anything is written.

    `coordination.check_meal_conflicts` is the matcher — the one the
    draft's banner and swap_in_place's own gate share. A HARD clash is the
    refusal; a soft one (a won't-eat) is not, for the same reason a swap
    does not refuse on one: the household asked for this recipe by name,
    and refusing their own cookbook over a dislike would be the app
    overruling a choice.

    Returns {"message", "member", "ingredient"} or None. The sentence
    names WHICH PERSON and WHICH INGREDIENT, which is the card's own
    criterion — a bare "that doesn't work" leaves nowhere to go.
    """
    clashes = _coordination.check_meal_conflicts(
        recipe_name, ingredients=list(ingredients or []), sides=list(sides or []),
    )
    hard = [c for c in clashes if c.get("severity") == "hard"]
    if not hard:
        return None
    first = hard[0]
    who = first.get("member") or "someone here"
    word = first.get("matched_word") or first.get("matched") or first.get("restriction") or ""
    # Both facts, each said once (§8): who, and what. "That one has peanut
    # in it — Reid can't have peanut" says peanut twice, which reads like
    # the app padding.
    message = (f"{who} can’t have {word}, and that one has it in. Pick another."
               if word else f"{who} can’t eat that one. Pick another.")
    return {"message": message, "member": first.get("member") or "", "ingredient": word}


def _needed_lines(conn) -> dict:
    """
    What is on the shopping list right now, as {name: quantity} — the
    'needed'/'in_cart'/'spice' rows, i.e. what the Shop tab opens on plus
    the spice section waiting to be ticked.

    Read before and after so the toast can say how many lines the change
    moved. By LINE and not by amount-difference: "3 things changed on the
    list" is a count of rows a shopper would notice, which is a line that
    appeared, went, or now says a different number.
    """
    rows = conn.execute(
        "SELECT item, quantity FROM grocery_items "
        "WHERE household_id = ? AND status IN ('needed', 'in_cart', 'spice')",
        (household_id(),),
    ).fetchall()
    return {str(r["item"]).strip().lower(): str(r["quantity"] or "") for r in rows}


def _lines_changed(before: dict, after: dict) -> int:
    return len(set(before) ^ set(after)) + sum(
        1 for k in set(before) & set(after) if before[k] != after[k]
    )


def _said(old_name: str, new_name: str, changed: int, approved: bool) -> str:
    """
    The toast, built beside the data so no screen can compose a different
    one (§8: never say a thing that isn't true).

    The card's own sentence — "Chana masala now uses your recipe." — is
    said when the dish is still called the same thing, which is the
    ordinary case for a household picking their own version of it. A
    different name gets a sentence that is true instead of that one:
    re-pointing the recipe is what renames the meal, since the plan reads
    a meal's name off its recipe.

    The grocery clause is only there on an approved week, and only when a
    line really moved — a draft has nothing on the list yet, and "0 things
    changed" is a sentence about nothing.
    """
    old = (old_name or "that one").strip()
    new = (new_name or "").strip()
    if new and new.lower() != old.lower():
        said = f"{old} is now {new}."
    else:
        said = f"{old} now uses your recipe."
    if approved and changed:
        said += f" {changed} thing{'' if changed == 1 else 's'} changed on the list."
    return said


def change_meal_recipe(entry_id: int, recipe_id: int) -> dict:
    """
    Point one meal at a different recipe, keeping its slot, its day, who
    is eating and its leftover chain — the card's whole acceptance
    criterion.

    One transaction (see the module docstring, reason 1), `BEGIN
    IMMEDIATE` before the first read so the plan's status and the chain
    cannot move under the decision. The allergy gate runs before it opens
    (reason 2), so a refusal has written nothing at all.

    On an APPROVED plan the entries' grocery contributions are reversed
    and the new recipe is bought, through the same
    _reverse_meal_grocery_contributions / _ingest_recipe_group_and_sides
    pair the leftover rescale and the swap both use — never a second
    arithmetic that could disagree with them about one household's list.
    On a DRAFT nothing is on the list yet, so nothing is reversed and
    nothing is bought; approval does it, as it does for every other draft.

    Returns {"status", "said", ...}. A refusal is `status: 'refused'` with
    a sentence written for a reader at 200, the shape add_dish_day and the
    chore rows already answer in — an app that did exactly the right thing
    must not report itself broken.
    """
    from . import weekly_plan as _weekly_plan   # circular at module scope

    hid = household_id()
    # The gate's reads first, on a connection of their own, so nothing slow
    # or model-shaped happens inside the write transaction.
    conn = get_conn()
    try:
        entry = _entry_row(conn, entry_id)
        if not entry:
            raise ValueError(f"No meal with id {int(entry_id)}.")
        recipe = conn.execute(
            "SELECT id, name, ingredients_json, default_servings, details_pending FROM recipes "
            "WHERE id = ? AND household_id = ?",
            (int(recipe_id), hid),
        ).fetchone()
        if not recipe:
            return {"status": "refused", "said": NO_SUCH_RECIPE}
        if entry["slot_state"] != CHANGEABLE_SLOT_STATE:
            return {"status": "refused", "said": NOT_CHANGEABLE}
        if _is_reheat(conn, entry):
            return {"status": "refused", "said": REHEAT_NOT_CHANGEABLE}
        if entry["recipe_id"] and int(entry["recipe_id"]) == int(recipe["id"]):
            return {"status": "refused", "said": SAME_RECIPE}
        old_name = entry["meal"] or ""
        new_name = recipe["name"] or ""
        try:
            ingredients = json.loads(recipe["ingredients_json"] or "[]")
        except (TypeError, ValueError):
            ingredients = []
        if entry["plan_status"] == "approved" and (
                recipe["details_pending"] or not any(
                    isinstance(i, dict) and str(i.get("item") or "").strip() for i in ingredients)):
            # Nothing to buy for it: the old lines would come off the list
            # and nothing would go on, and the household would shop for a
            # dish with no ingredients.
            return {"status": "refused", "said": NO_INGREDIENTS_YET}
        blocked = recipe_change_blocked(new_name, ingredients, _sides_of(entry))
        if blocked:
            return {"status": "blocked", "said": blocked["message"],
                    "member": blocked["member"], "ingredient": blocked["ingredient"]}
    finally:
        conn.close()

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        entry = _entry_row(conn, entry_id)
        if not entry or entry["slot_state"] != CHANGEABLE_SLOT_STATE:
            conn.rollback()
            return {"status": "refused", "said": NOT_CHANGEABLE}
        plan_id = int(entry["weekly_plan_id"])
        approved = entry["plan_status"] == "approved"
        ids = _chain_entry_ids(conn, entry)
        # Only nights still ahead have anything on the list to move; a
        # past or cooked night changes recipe and leaves the list alone.
        live_ids = _live_ids(conn, ids) if approved else []
        before = _needed_lines(conn) if approved else {}
        conn.execute(
            "UPDATE meal_plan_entries SET recipe_id = ?, freeform_meal = NULL "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND household_id = ?",
            [int(recipe_id)] + ids + [hid],
        )
        # Less what is already in the cart or bought for these nights: the
        # chicken ticked for Fried Rice is the Curry's chicken too.
        after = _resync_users(conn, {plan_id: live_ids}) if live_ids else before
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    changed = _lines_changed(before, after)
    return {
        "status": "changed",
        "said": _said(old_name, new_name, changed, approved),
        "entry_ids": ids,
        "recipe_id": int(recipe_id),
        "name": new_name,
        "previous_name": old_name,
        "lines_changed": changed,
        "approved": approved,
    }


def _sides_of(entry) -> list[dict]:
    try:
        sides = json.loads(entry["sides_json"] or "[]")
    except (TypeError, ValueError):
        return []
    return sides if isinstance(sides, list) else []


def _is_reheat(conn, entry) -> bool:
    """
    Whether this night eats an earlier batch rather than cooking.

    The honoured chain first (leftovers.plan_leftover_chains' `leftovers`
    map, which requires both halves to agree), then the words, for a night
    the model wrote as "Leftover chili" with no recipe behind it at all — a
    chain this app wrote carries a recipe_id and the other kind does not,
    and both are reheats.

    _READS_AS_REHEAT asks the SAME question build_slot's own inline
    `leftovers?\b` asks of the same field, and is deliberately its own
    constant rather than a shared one: that reading is a line inside a
    function that returns a whole slot payload, there is no helper to
    borrow, and a regex hoisted out of it would change a function three
    other cards are live in tonight. Named here so a later tidy-up has one
    thing to fold rather than two it has to find. (Hazard: two
    implementations of one RULE is what this repo warns about — this is one
    rule written twice and said so, not two rules.)
    """
    chains = _leftovers.plan_leftover_chains(entry["weekly_plan_id"], conn=conn)
    if int(entry["id"]) in chains["leftovers"]:
        return True
    if entry["recipe_id"]:
        return False
    return bool(_READS_AS_REHEAT.search(str(entry["freeform_meal"] or "")))


def _approved_users_of(conn, recipe_id: int) -> dict[int, list[int]]:
    """
    {plan_id: [entry ids]} for every entry in an APPROVED plan of this
    household that cooks or reheats `recipe_id`.

    A rewrite or an undo edits the recipes row in place, so it changes the
    food on EVERY night that points at that row — Tuesday's and the Friday
    nobody opened, this week's and another approved week's. The chain is
    not enough: two unchained nights on one recipe share the row, and the
    shopping for the one left behind would still carry the old
    ingredients. Only approved plans have bought anything; a draft's
    shopping is written at approval from whatever the row says then.

    Only nights still AHEAD: dated on or after the household's today (its
    clock, `grocery._household_today`, not the server's) and still
    'planned' — the reading `grocery._live_plan_ids` uses for "not stale".
    A plan stays 'approved' after its week ends, and last month's Chili or
    the night already cooked this week must not be bought again onto the
    live list. That holds for the meal the household opened too: fixing
    the recipe of a night already cooked changes the recipe and leaves the
    list alone — which is why a night ticked done (cooked_status, set by
    check_off_meal) is not a user either, even dated today (review,
    2026-10-24: tonight's eaten Fried Rice bought a dozen eggs).
    """
    today = _grocery._household_today(conn=conn).isoformat()
    out: dict[int, list[int]] = {}
    for r in conn.execute(
        "SELECT mpe.id, mpe.weekly_plan_id FROM meal_plan_entries mpe "
        "JOIN weekly_plans wp ON wp.id = mpe.weekly_plan_id "
        "WHERE mpe.recipe_id = ? AND mpe.household_id = ? AND wp.status = 'approved' "
        "AND mpe.date >= ? AND mpe.slot_state = 'planned' AND mpe.cooked_status != 'done' "
        "ORDER BY mpe.date ASC, mpe.id ASC",
        (int(recipe_id), household_id(), today),
    ).fetchall():
        out.setdefault(int(r["weekly_plan_id"]), []).append(int(r["id"]))
    return out


def _live_ids(conn, ids: list[int]) -> list[int]:
    """The entries among `ids` that are still ahead, 'planned' and not
    ticked cooked — _approved_users_of's reading. A night already eaten
    tonight is not still ahead: "we had tacos instead" put the tacos on
    the live list (defect hunt, 2026-10-27)."""
    today = _grocery._household_today(conn=conn).isoformat()
    marks = ",".join("?" * len(ids))
    return [int(r["id"]) for r in conn.execute(
        f"SELECT id FROM meal_plan_entries WHERE id IN ({marks}) AND household_id = ? "
        "AND date >= ? AND slot_state = 'planned' AND cooked_status != 'done' "
        "ORDER BY date ASC, id ASC",
        [*ids, household_id(), today],
    ).fetchall()]


def _rebuy_after_edit(conn, users: dict[int, list[int]]) -> None:
    """
    update_recipe_details' resync: buy for these nights from the recipe as
    it now stands, less what the shopper has already put in the cart or
    bought for them.

    _unbuy/_rebuy take the whole ledger off and buy the whole recipe again,
    and a ticked line is left as it is — so a serving count raised after
    the chicken was ticked bought the full new amount on top: 1 lb bought,
    "2 lbs" needed, for a 2 lb need (review, 2026-10-24). Here only the
    ledger rows on UNTICKED lines are reversed; a ticked line keeps its
    rows (bring_over.bought_items reads them next week), and each night's
    new share of that food is cut by what its ticked rows already hold.
    Nothing is bought when that covers it.
    """
    hid = household_id()
    _done = ("in_cart", "purchased")
    for plan_id, ids in users.items():
        marks = ",".join("?" * len(ids))
        links = conn.execute(
            "SELECT l.id, l.meal_plan_entry_id, l.item, l.quantity, g.status "
            "FROM meal_plan_grocery_links l LEFT JOIN grocery_items g ON g.id = l.grocery_item_id "
            f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({marks})",
            [hid, *ids],
        ).fetchall()
        bought: dict[tuple[int, str], list[str]] = {}
        undone: dict[int, list[int]] = {}
        for link in links:
            if link["status"] in _done:
                key = (int(link["meal_plan_entry_id"]), _grocery._merge_key(link["item"] or ""))
                bought.setdefault(key, []).append(link["quantity"] or "")
            else:
                undone.setdefault(int(link["meal_plan_entry_id"]), []).append(int(link["id"]))
        old_ids = {int(link["id"]) for link in links}
        for eid, link_ids in undone.items():
            _grocery._reverse_meal_grocery_contributions(eid, conn=conn, only_link_ids=link_ids)
        _rebuy(conn, plan_id, ids)
        if not bought:
            continue
        fresh = conn.execute(
            "SELECT l.id, l.meal_plan_entry_id, l.item, l.quantity, l.grocery_item_id, g.status, "
            "g.quantity AS line_qty, g.source_weekly_plan_id "
            "FROM meal_plan_grocery_links l JOIN grocery_items g ON g.id = l.grocery_item_id "
            f"WHERE l.household_id = ? AND l.meal_plan_entry_id IN ({marks})",
            [hid, *ids],
        ).fetchall()
        touched: set[int] = set()
        # A person's own line the shares were added onto (source_weekly_plan_id
        # IS NULL) can't be recomputed from the ledger. Its ledger is read once
        # before any cut and once after, and the line restated in one step
        # with the plan's ROUNDED part changed (_ledger_totals +
        # _restate_standing_want, the reversal's own pair). Taking the whole
        # bought amount off the line, link by link from one snapshot, left the
        # new share on it with no ledger row behind it, and two cuts on one
        # line overwrote each other (review, 2026-10-27).
        standing_before: dict[int, list[str]] = {}
        standing_cut: dict[int, list[tuple[str, str]]] = {}
        for link in fresh:
            if int(link["id"]) in old_ids or link["status"] in _done:
                continue
            had = bought.get((int(link["meal_plan_entry_id"]), _grocery._merge_key(link["item"] or "")))
            if not had:
                continue
            share = link["quantity"] or ""
            left, gone = share, False
            for qty in had:
                left, gone = _grocery._subtract_quantity(left, qty)
                if gone:
                    break
            item_id = int(link["grocery_item_id"])
            if link["source_weekly_plan_id"] is None and item_id not in standing_before:
                standing_before[item_id] = _grocery._ledger_quantities(item_id, conn)
            if gone:
                conn.execute("DELETE FROM meal_plan_grocery_links WHERE id = ? AND household_id = ?", (link["id"], hid))
            elif left != share:
                conn.execute("UPDATE meal_plan_grocery_links SET quantity = ? WHERE id = ? AND household_id = ?",
                             (left, link["id"], hid))
            else:
                continue
            if link["source_weekly_plan_id"] is None:
                standing_cut.setdefault(item_id, []).append((share, "" if gone else left))
            touched.add(item_id)
        for item_id, before_qtys in standing_before.items():
            if item_id not in standing_cut:
                continue
            _restate_after_cut(conn, item_id, before_qtys, standing_cut[item_id])
        for item_id in touched:
            still = conn.execute(
                "SELECT 1 FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? LIMIT 1",
                (hid, item_id),
            ).fetchone()
            if still:
                _grocery._recompute_plan_line_from_ledger(item_id, conn=conn)
            else:
                # Every share on it is already bought: a plan line with no
                # meal left wanting it goes (a person's own line never does).
                conn.execute(
                    "DELETE FROM grocery_items WHERE id = ? AND household_id = ? "
                    "AND source_weekly_plan_id IS NOT NULL AND status IN ('needed', 'spice') "
                    "AND COALESCE(kept_amount, '') = ''",
                    (item_id, hid),
                )


def _restate_after_cut(conn, item_id: int, before_qtys: list[str], cuts: list[tuple[str, str]]) -> None:
    """
    A household's own line after _rebuy_after_edit cut the plan's shares on
    it: restated from the ledger before and after (the plan's rounded part
    swapped), or — when those rows can't be read that way — each cut taken
    off in turn (share less what is left of it), never more than that and
    never below nothing.
    """
    hid = household_id()
    row = conn.execute("SELECT quantity FROM grocery_items WHERE id = ? AND household_id = ?",
                       (item_id, hid)).fetchone()
    if not row:
        return
    current = row["quantity"] or ""
    totals = _quantities._ledger_totals(before_qtys, _grocery._ledger_quantities(item_id, conn))
    restated = None if totals is None else _grocery._restate_standing_want(current, *totals)
    if restated is not None:
        new_qty, gone = restated
    else:
        new_qty, gone = current, False
        for share, left in cuts:
            cut = _quantities._parse_quantity(share)
            rest = _quantities._parse_quantity(left) if left else (0.0, cut[1] if cut else None)
            if not cut or not rest:
                continue
            rest_amount = _quantities._convert_to_unit(rest[0], rest[1], cut[1]) if left else 0.0
            if rest_amount is None or cut[0] - rest_amount <= 0:
                continue
            new_qty, gone = _grocery._subtract_quantity(new_qty, f"{cut[0] - rest_amount:g} {cut[1] or ''}".strip())
            if gone:
                break
    if gone or not new_qty:
        return   # a line somebody typed is never blanked here
    new_qty = _grocery._no_more_than(new_qty, current)
    if new_qty != current:
        conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
                     (new_qty, item_id, hid))


def _resync_users(conn, users: dict[int, list[int]]) -> dict:
    """
    Redo these nights' shopping after their recipe changed (a pick, a
    rewrite, an undo): _rebuy_after_edit's reading — unticked lines come
    off and the recipe as it now stands is bought less what is already in
    the cart or bought for those nights — and what the list reads after.

    Called AFTER the write; the reversal reads the ledger, never the
    recipe. _unbuy/_rebuy left a ticked line alone and bought the whole
    new amount beside it, so switching Fried Rice to Chicken Curry with
    the chicken in the cart put a second 2 lbs on the list (defect hunt,
    2026-10-27; the 2026-10-24 entry's "apply_rewrite still re-buys over a
    ticked line").
    """
    users = {k: v for k, v in users.items() if v}
    if users:
        _rebuy_after_edit(conn, users)
    return _needed_lines(conn)


def _unbuy(conn, entry_ids: list[int]) -> None:
    """Take these entries' ingredients back off the list."""
    for eid in entry_ids:
        _grocery._reverse_meal_grocery_contributions(eid, conn=conn)


def _rebuy(conn, plan_id: int, entry_ids: list[int]) -> dict:
    """
    Buy for these entries' recipe as it now stands, and answer what the
    list reads afterwards.

    _ingest_recipe_group_and_sides and ONE WeekGroceryBuffer, exactly as
    the leftover rescale and the swap use them: the group is rounded once,
    on its combined total, which is what stops a chain's two nights being
    rounded separately and the amounts drifting (see
    _rescale_leftover_source_grocery's own note on the same hazard).
    """
    from . import weekly_plan as _weekly_plan   # circular at module scope

    hid = household_id()
    marks = ",".join("?" * len(entry_ids))
    rows = conn.execute(
        "SELECT mpe.id, mpe.recipe_id, r.ingredients_json, r.default_servings, mpe.sides_json "
        "FROM meal_plan_entries mpe JOIN recipes r ON r.id = mpe.recipe_id "
        f"WHERE mpe.id IN ({marks}) AND mpe.household_id = ? "
        "AND mpe.component_category IS NULL ORDER BY mpe.date ASC, mpe.id ASC",
        entry_ids + [hid],
    ).fetchall()
    if rows:
        buffer = _recipes.WeekGroceryBuffer(plan_id, conn=conn)
        _weekly_plan._ingest_recipe_group_and_sides(rows, plan_id, buffer)
        buffer.flush()
    return _needed_lines(conn)


REWRITE_NOT_CHANGEABLE = "There’s no recipe on that one to rewrite."
REWRITE_NO_RECIPE = "That meal has no saved recipe yet — I’ll write one when you approve the week."


def meal_for_request(entry_id: int) -> dict | None:
    """
    The dish and recipe one of this household's meals is on, for filing a
    request that did not land (agent.rewrite_meal_recipe). None for an id
    that isn't this household's, so a stray id never puts another
    household's meal on this one's record.
    """
    conn = get_conn()
    try:
        entry = _entry_row(conn, entry_id)
    finally:
        conn.close()
    if not entry:
        return None
    return {"entry_id": int(entry["id"]), "dish": entry["meal"] or "",
            "recipe_id": int(entry["recipe_id"]) if entry["recipe_id"] else None}


def unwritten_recipe(entry_id: int) -> dict | None:
    """
    {"id", "name"} of the recipe when this meal is a draft dish whose recipe has not
    been written yet (details_pending — the recipe pass writes it at
    approval), else None. The Tweak sheet's "Something else" reaches these
    where the Change recipe button never does (that only shows on a
    written recipe), and the answer there is to write it now, the way the
    Cook screen does (agent.fill_in_recipe), then change it.
    """
    conn = get_conn()
    try:
        entry = _entry_row(conn, entry_id)
        if (not entry or entry["slot_state"] != CHANGEABLE_SLOT_STATE or not entry["recipe_id"]
                or _is_reheat(conn, entry)):
            return None
        recipe = conn.execute(
            "SELECT id, name, details_pending FROM recipes WHERE id = ? AND household_id = ?",
            (int(entry["recipe_id"]), household_id()),
        ).fetchone()
    finally:
        conn.close()
    if not recipe or not recipe["details_pending"]:
        return None
    return {"id": int(recipe["id"]), "name": recipe["name"]}


# The Won't eat list against a rewrite (the Tweak sheet's "Something
# else", review 2026-10-19). A dislike is soft everywhere else — the draft
# mentions it, Change recipe's own pick ignores it — because there the
# household chose a whole recipe by name. A TYPED change is different: it
# is the app writing ingredients, and chat files "we don't eat pork" on
# this list, so a rewrite may not bring one in. Only foods the current
# recipe did not already have: a household that planned mushroom risotto
# knowingly is not refused for leaving the mushrooms in.
def wont_eat_list() -> list[str]:
    try:
        from . import memory as _memory
        return [str(d).strip() for d in (_memory.get_household_memory().get("dislikes") or [])
                if str(d).strip()]
    except Exception:
        logger.exception("Reading the Won't eat list failed; rewriting without it")
        return []


def wont_eat_added(name: str, old_ingredients, new_ingredients) -> str | None:
    """The first Won't eat food the new ingredients bring in that the old
    ones did not have, as the household wrote it, or None."""
    soft = [a for a in _coordination._avoidances() if a.get("source") == "dislike"]
    if not soft:
        return None

    def labels(ings):
        return {c.get("restriction") or c.get("label") or "" for c in _coordination.check_meal_conflicts(
            name, ingredients=list(ings or []), avoidances=soft)}
    added = sorted(labels(new_ingredients) - labels(old_ingredients) - {""})
    return added[0] if added else None


def wont_eat_said(food: str) -> str:
    food = (food or "").strip()
    verb = "are" if food.lower().endswith("s") else "is"
    return f"{food[:1].upper()}{food[1:]} {verb} on your Won’t eat list, so I left it as it was."


# What makes a typed change read as a standing rule rather than one
# meal's ("we never want cilantro", "the kids don't eat the sauce") — the
# answer then offers to remember it (Tweak sheet, 2026-10-19). A word
# list, for request_theme's reason: a wrong guess costs one offer the
# household ignores, and the chat — which does the remembering — reads
# the words itself and picks the right list for them.
_LASTING_WORDS = (
    "never", "ever", "always", "any more", "anymore", "for good", "from now on",
    "every time", "don't eat", "dont eat", "doesn't eat", "doesnt eat", "do not eat",
    "won't eat", "wont eat", "can't stand", "cant stand", "hate", "hates",
    "allergic", "can't have", "cant have", "don't like", "doesn't like",
)


def reads_as_lasting(text: str) -> bool:
    """Whether a typed change reads as a standing rule (_LASTING_WORDS)."""
    low = re.sub("[\u2019\u2018]", "'", str(text or "").lower())
    return any(re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) for w in _LASTING_WORDS)


def rewrite_spec(entry_id: int) -> dict:
    """
    What the recipe writer is given for a rewrite: the dish as it stands,
    plus the one thing a rewrite must be held to that a first write is not
    — its own name. The dish does not change; only the recipe behind it
    does ("Pomona rewrites the recipe for the same dish", the card).

    Raises ValueError with a sentence for a reader when there is nothing to
    rewrite. The allergy list, the time limit and the servings ride in
    through agent's own _shared_recipe_details_context, which is where
    every other recipe call gets them — so a rewrite honours exactly what
    a first write honours, rather than a second list of what matters.
    """
    conn = get_conn()
    try:
        entry = _entry_row(conn, entry_id)
        if not entry:
            raise ValueError(f"No meal with id {int(entry_id)}.")
        if entry["slot_state"] != CHANGEABLE_SLOT_STATE:
            raise ValueError(REWRITE_NOT_CHANGEABLE)
        if _is_reheat(conn, entry):
            raise ValueError(REHEAT_NOT_CHANGEABLE)
        if not entry["recipe_id"]:
            raise ValueError(REWRITE_NO_RECIPE)
        recipe = conn.execute(
            "SELECT id, name, cuisine, main_protein, tags_json, food_groups_json, dish_note, "
            "       prep_time_minutes, cook_time_minutes, default_servings, "
            "       ingredients_json, instructions_json, details_pending "
            "FROM recipes WHERE id = ? AND household_id = ?",
            (int(entry["recipe_id"]), household_id()),
        ).fetchone()
    finally:
        conn.close()
    if not recipe:
        raise ValueError(REWRITE_NO_RECIPE)
    if recipe["details_pending"]:
        raise ValueError(REWRITE_NO_RECIPE)
    return {
        "entry_id": int(entry["id"]),
        "recipe_id": int(recipe["id"]),
        "weekly_plan_id": int(entry["weekly_plan_id"]),
        "slot": entry["slot"] or "dinner",
        "name": recipe["name"],
        "cuisine": recipe["cuisine"] or "",
        "main_protein": recipe["main_protein"] or "",
        "tags": json.loads(recipe["tags_json"] or "[]"),
        "food_groups": json.loads(recipe["food_groups_json"] or "[]"),
        "dish_note": recipe["dish_note"] or "",
        "prep_time_minutes": recipe["prep_time_minutes"],
        "cook_time_minutes": recipe["cook_time_minutes"],
        "default_servings": recipe["default_servings"],
        "ingredients": json.loads(recipe["ingredients_json"] or "[]"),
        "instructions": json.loads(recipe["instructions_json"] or "[]"),
    }


# What a rewrite replaced, kept on the request row so the undo can put it
# back exactly. plan_undo's shape and its reason: the record of a change
# is where what-it-replaced belongs, and a version table for one undo
# would be a second place a recipe lives.
_SNAPSHOT_COLUMNS = (
    "name", "ingredients_json", "instructions_json", "default_servings",
    "prep_time_minutes", "cook_time_minutes", "advance_prep_notes",
    "advance_prep_step_indices_json",
)


def _fork_name(conn, name: str, date: str) -> str:
    """A name of its own for this meal's copy of a shared recipe, so a
    lookup by name (the Cook screen, get_recipe) never finds two."""
    import datetime as _dt
    try:
        day = _dt.date.fromisoformat(str(date)[:10]).strftime("%A")
    except ValueError:
        day = "this one"
    taken = {str(r["name"] or "").strip().lower() for r in conn.execute(
        "SELECT name FROM recipes WHERE household_id = ?", (household_id(),))}
    base = f"{name} ({day})"
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate, n = f"{base} {n}", n + 1
    return candidate


def _fork_recipe(conn, recipe_id: int, name: str) -> int:
    """A copy of the recipe row under `name`, history reset (it has never
    been cooked), for apply_rewrite's this-meal-only change."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(recipes)") if r["name"] != "id"]
    fresh = {"name": name, "times_cooked": 0, "last_cooked_date": None, "rating": "", "feedback_notes": ""}
    row = dict(conn.execute(
        f"SELECT {', '.join(cols)} FROM recipes WHERE id = ? AND household_id = ?",
        (int(recipe_id), household_id()),
    ).fetchone())
    row.update({k: v for k, v in fresh.items() if k in row})
    cur = conn.execute(
        f"INSERT INTO recipes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [row[c] for c in cols],
    )
    return int(cur.lastrowid)


def apply_rewrite(spec: dict, detail: dict, request_text: str, this_meal_only: bool = False) -> dict:
    """
    Save a rewrite of one meal's recipe IN PLACE, keeping the dish's name,
    and redo its shopping.

    In place, not as a new recipe, for two reasons. The dish's name is read
    off its recipe, so a new row under a new name would rename the meal —
    and the card's own sentence is that the dish stays. And the recipe is
    the household's own record of how they make this thing; a rewrite is a
    correction to it, not a second entry in the book.

    What it replaced goes on the request row (_SNAPSHOT_COLUMNS), which is
    what makes the undo exact rather than a guess — and what lets the
    report say whether a rewrite was kept or undone, which is the half of
    Emily's ask that is not the text itself.

    One transaction, `BEGIN IMMEDIATE` before the first read, for
    change_meal_recipe's reason: the ingredients come off the list and the
    new ones go on, and a failure between those two is a week holding a
    dish nobody bought for.

    `this_meal_only` (the Tweak sheet's "Something else"): when any meal
    outside this one's leftover chain also cooks this recipe — Tuesday's
    and Thursday's tacos, or the same dish in another week — the change
    goes on a COPY of the recipe that only this chain is pointed at, and
    only this chain's shopping is redone. The others keep theirs. Change
    recipe's own rewrite still edits the row in place (every night on it),
    which is that sheet's established answer.
    """
    ingredients = [
        i for i in (detail.get("ingredients") or [])
        if isinstance(i, dict) and str(i.get("item") or "").strip()
    ]
    instructions = list(detail.get("instructions") or [])
    if not ingredients or not instructions:
        raise ValueError("That rewrite didn’t come back in one piece — try it again.")
    servings = (_recipes.positive_servings(detail.get("default_servings"))
                or _recipes.positive_servings(spec.get("default_servings")) or 4)
    # What the steps use goes on the list, as on every save (2026-10-12).
    # A rewrite is the model's recipe even where it copied a line back
    # from the household's (2026-10-18): the "as written" mark comes off.
    ingredients = _recipes.complete_ingredient_list(_recipes.without_household_marks(ingredients), instructions, servings)
    settled = _recipes.settle_cooking_quantities(ingredients, servings, measure_spices=True)
    hid = household_id()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        entry = _entry_row(conn, spec["entry_id"])
        if not entry or entry["slot_state"] != CHANGEABLE_SLOT_STATE:
            conn.rollback()
            return {"status": "refused", "said": REWRITE_NOT_CHANGEABLE}
        if not entry["recipe_id"] or int(entry["recipe_id"]) != int(spec["recipe_id"]):
            # The meal moved to another recipe between the ask and the
            # answer (the other adult, or chat). Writing this rewrite onto
            # whatever is there now would be answering a question about a
            # dish nobody asked about.
            conn.rollback()
            return {"status": "refused", "said": "That meal changed while I was writing — have another look."}
        plan_id = int(entry["weekly_plan_id"])
        approved = entry["plan_status"] == "approved"
        ids = _chain_entry_ids(conn, entry)
        target_id = int(spec["recipe_id"])
        shared_elsewhere = this_meal_only and conn.execute(
            f"SELECT 1 FROM meal_plan_entries WHERE recipe_id = ? AND household_id = ? "
            f"AND id NOT IN ({','.join('?' * len(ids))}) LIMIT 1",
            [target_id, hid, *ids],
        ).fetchone() is not None
        snapshot = dict(conn.execute(
            f"SELECT {', '.join(_SNAPSHOT_COLUMNS)} FROM recipes WHERE id = ? AND household_id = ?",
            (target_id, hid),
        ).fetchone())
        if shared_elsewhere:
            # Only this chain's nights still ahead have shopping to move.
            users = {plan_id: _live_ids(conn, ids)} if approved else {}
            users = {k: v for k, v in users.items() if v}
            before = _needed_lines(conn) if approved else {}
            target_id = _fork_recipe(conn, target_id, _fork_name(conn, spec.get("name") or "", entry["date"]))
            conn.execute(
                "UPDATE meal_plan_entries SET recipe_id = ? "
                f"WHERE id IN ({','.join('?' * len(ids))}) AND household_id = ?",
                [target_id, *ids, hid],
            )
            # The undo puts the chain back on the recipe it came from.
            snapshot = {"forked_from": int(spec["recipe_id"]), "chain": ids}
        else:
            # Every approved night on this recipe row, not only this chain: the
            # row is edited in place, so they all change (_approved_users_of).
            users = _approved_users_of(conn, target_id)
            approved = approved or bool(users)
            before = _needed_lines(conn) if approved else {}
        conn.execute(
            "UPDATE recipes SET ingredients_json = ?, instructions_json = ?, default_servings = ?, "
            "prep_time_minutes = COALESCE(?, prep_time_minutes), "
            "cook_time_minutes = COALESCE(?, cook_time_minutes), "
            "advance_prep_notes = ?, advance_prep_step_indices_json = ?, details_pending = 0 "
            "WHERE id = ? AND household_id = ?",
            (json.dumps(settled), json.dumps(instructions), servings,
             detail.get("prep_time_minutes"), detail.get("cook_time_minutes"),
             detail.get("advance_prep_notes") or "",
             json.dumps(detail.get("advance_prep_step_indices") or []),
             target_id, hid),
        )
        after = _resync_users(conn, users) if users else before
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    changed = _lines_changed(before, after)
    request_id = record_recipe_change_request(
        spec.get("name") or "", request_text,
        meal_plan_entry_id=int(spec["entry_id"]), recipe_id=target_id,
        outcome=OUTCOME_REWRITTEN,
    )
    if request_id:
        _store_snapshot(request_id, snapshot)
    said = (spec.get("name") or "That one") + " is rewritten."
    if approved and changed:
        said += f" {changed} thing{'' if changed == 1 else 's'} changed on the list."
    return {
        "status": "rewritten", "said": said, "request_id": request_id,
        "recipe_id": target_id, "name": spec.get("name") or "",
        "entry_ids": ids, "forked": bool(shared_elsewhere), "lines_changed": changed, "approved": approved,
    }


def _store_snapshot(request_id: int, snapshot: dict) -> None:
    """Never raises: the rewrite itself has already landed, and losing the
    undo is a smaller harm than reporting a change that happened as a
    failure."""
    try:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE recipe_change_requests SET previous_json = ? WHERE id = ? AND household_id = ?",
                (json.dumps(snapshot), int(request_id), household_id()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("Storing what a recipe rewrite replaced failed; the rewrite itself stands")


def _fork_version_label(date, request_text: str) -> str:
    """'the Tuesday version (Shrimp instead of salmon)' — how a note on the
    original names the this-meal-only copy it was really about."""
    import datetime as _dt
    try:
        day = _dt.date.fromisoformat(str(date)[:10]).strftime("%A") + " "
    except ValueError:
        day = ""
    words = " ".join(str(request_text or "").split())
    if len(words) > 80:
        words = words[:79].rstrip() + "…"
    return f"the {day}version" + (f" ({words})" if words else "")


def _carry_fork_history(conn, fork: int, original: int, label: str) -> None:
    """
    Keep what the household said about a this-meal-only copy, on the recipe
    its night goes back to, before the undo deletes the copy.

    recipe_notes and member_recipe_feedback reference recipes(id) with no
    ON DELETE CASCADE, so a cooking note ("used turkey instead") or a
    person's rating saved on the copy blocked the delete outright — "FOREIGN
    KEY constraint failed", a 500, the night left on the copy (overnight
    defect hunt, 2026-10-28).

    Notes move across as they are: they are one-off soft signals by design
    (recipe_notes' own comment), and Put it back says "not that change",
    not "forget what we said".

    Ratings NEVER become ratings on the original (review, same night). A
    verdict on the copy was most likely about the change the household
    then undid — "Shrimp instead of salmon" disliked, then put back — and
    'disliked' is the app's strong, standing veto: moved across it made
    Salmon Bowls itself an avoid (taste_verdict) and dropped it from quick
    dinners. So each rating, per person or the household's, and the copy's
    feedback text, is kept as a feedback NOTE on the original naming the
    version ("Sam: disliked the Tuesday version (Shrimp instead of
    salmon)"), and the copy's rating rows go with the copy. Nothing already
    on the original is touched. A cook of the copy still counts as a cook
    of the dish, as it would have had the Tweak never forked. Same
    household only, on the caller's connection; commits nothing.
    """
    hid = household_id()
    conn.execute(
        "UPDATE recipe_notes SET recipe_id = ? WHERE recipe_id = ? AND household_id = ?",
        (original, fork, hid),
    )
    said = [
        f"{r['name'] or 'Someone'}: {r['rating']} {label}" + (f" — {r['notes']}" if r["notes"] else "")
        for r in conn.execute(
            "SELECT m.name, f.rating, f.notes FROM member_recipe_feedback f "
            "LEFT JOIN members m ON m.id = f.member_id AND m.household_id = f.household_id "
            "WHERE f.recipe_id = ? AND f.household_id = ? ORDER BY f.id",
            (fork, hid),
        )
    ]
    conn.execute("DELETE FROM member_recipe_feedback WHERE recipe_id = ? AND household_id = ?", (fork, hid))
    copy = conn.execute(
        "SELECT rating, feedback_notes, times_cooked, last_cooked_date FROM recipes "
        "WHERE id = ? AND household_id = ?", (fork, hid),
    ).fetchone()
    if not copy:
        return
    if copy["rating"]:
        said.append(f"{str(copy['rating']).capitalize()} {label}")
    if (copy["feedback_notes"] or "").strip():
        said.append(f"About {label}: {copy['feedback_notes'].strip()}")
    for note in said:
        conn.execute(
            "INSERT INTO recipe_notes (household_id, recipe_id, note_type, note) VALUES (?, ?, 'feedback', ?)",
            (hid, original, note),
        )
    conn.execute(
        "UPDATE recipes SET times_cooked = COALESCE(times_cooked, 0) + ?, "
        "last_cooked_date = CASE WHEN last_cooked_date IS NULL OR ? > last_cooked_date "
        "  THEN COALESCE(?, last_cooked_date) ELSE last_cooked_date END "
        "WHERE id = ? AND household_id = ?",
        (int(copy["times_cooked"] or 0), copy["last_cooked_date"], copy["last_cooked_date"], original, hid),
    )


def _undo_blocked(name: str, ingredients, entry) -> dict | None:
    """
    The refusal an undo returns when the recipe it would put back trips a
    hard avoidance: one sentence for both undo paths (in place, and a
    this-meal-only copy), so the sheet shows the same words either way.
    Household-wide like recipe_change_blocked itself; no attendance read.
    """
    blocked = recipe_change_blocked(name, ingredients, _sides_of(entry))
    if not blocked:
        return None
    who = blocked["member"] or "Someone here"
    what = blocked["ingredient"]
    return {"status": "refused",
            "said": (f"{who} can’t have {what}, and the old recipe has it in, "
                     "so I can’t put it back." if what else
                     f"{who} can’t eat the old recipe, so I can’t put it back.")}


def _undo_fork(conn, req, entry, snapshot: dict) -> dict:
    """
    Put back a this-meal-only rewrite (apply_rewrite's fork): the chain
    goes back onto the recipe it was copied from, its shopping with it, and
    the copy — now planned nowhere — is deleted. Runs inside the caller's
    open transaction and finishes it.
    """
    hid = household_id()
    try:
        original = int(snapshot["forked_from"])
        fork = int(req["recipe_id"])
        if not conn.execute("SELECT 1 FROM recipes WHERE id = ? AND household_id = ?",
                            (original, hid)).fetchone():
            conn.rollback()
            return {"status": "refused", "said": UNDO_NOTHING}
        # The same allergy gate the in-place undo runs (2026-10-28). Undoing
        # a "no peanuts, this meal only" put the peanut original straight
        # back on that night with no check, after the household had told us
        # someone can't have it. Checked on the original as it stands NOW,
        # not as it was at the Tweak: that is the recipe the night goes back to.
        orig_row = conn.execute(
            "SELECT name, ingredients_json FROM recipes WHERE id = ? AND household_id = ?",
            (original, hid),
        ).fetchone()
        try:
            orig_ings = json.loads(orig_row["ingredients_json"] or "[]")
        except (TypeError, ValueError):
            orig_ings = []
        refused = _undo_blocked(orig_row["name"] or req["dish_name"] or "", orig_ings, entry)
        if refused:
            conn.rollback()
            return refused
        ids = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM meal_plan_entries WHERE recipe_id = ? AND household_id = ?", (fork, hid))]
        approved = entry["plan_status"] == "approved"
        live = _live_ids(conn, ids) if (approved and ids) else []
        before = _needed_lines(conn) if approved else {}
        conn.execute(
            "UPDATE meal_plan_entries SET recipe_id = ? WHERE recipe_id = ? AND household_id = ?",
            (original, fork, hid),
        )
        after = _resync_users(conn, {int(entry["weekly_plan_id"]): live}) if live else before
        _carry_fork_history(conn, fork, original,
                            _fork_version_label(entry["date"], req["request_text"]))
        conn.execute("DELETE FROM recipes WHERE id = ? AND household_id = ?", (fork, hid))
        conn.execute(
            "UPDATE recipe_change_requests SET outcome = ? WHERE id = ? AND household_id = ?",
            (OUTCOME_UNDONE, int(req["id"]), hid),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    changed = _lines_changed(before, after)
    said = (req["dish_name"] or "That one") + " is back to the recipe it had."
    if approved and changed:
        said += f" {changed} thing{'' if changed == 1 else 's'} changed on the list."
    return {"status": "undone", "said": said, "lines_changed": changed}


UNDO_GONE = "That one’s already been put back."
UNDO_NOTHING = "I don’t have the old recipe to put back."
UNDO_MOVED_ON = "That meal has changed since, so I can’t put the old recipe back."
UNDO_NEWER = "A newer change to that recipe is on top of this one. Put that one back first."


def undo_recipe_change(request_id: int) -> dict:
    """
    Put back the recipe a rewrite replaced, exactly, and record that the
    household did not keep it.

    Restores from the request's own snapshot rather than regenerating:
    nothing else can know what the recipe said before, and a second model
    call would answer a different question. The shopping is redone the same
    way the rewrite did it, so the list comes back with the recipe.
    """
    hid = household_id()
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        req = conn.execute(
            "SELECT id, dish_name, recipe_id, meal_plan_entry_id, outcome, previous_json, request_text "
            "FROM recipe_change_requests WHERE id = ? AND household_id = ?",
            (int(request_id), hid),
        ).fetchone()
        if not req:
            conn.rollback()
            return {"status": "refused", "said": UNDO_NOTHING}
        if req["outcome"] == OUTCOME_UNDONE:
            conn.rollback()
            return {"status": "refused", "said": UNDO_GONE}
        try:
            snapshot = json.loads(req["previous_json"] or "null")
        except (TypeError, ValueError):
            snapshot = None
        if not isinstance(snapshot, dict) or not req["recipe_id"]:
            conn.rollback()
            return {"status": "refused", "said": UNDO_NOTHING}
        entry = _entry_row(conn, req["meal_plan_entry_id"]) if req["meal_plan_entry_id"] else None
        if not entry or not entry["recipe_id"] or int(entry["recipe_id"]) != int(req["recipe_id"]):
            # Swapped or picked away since: putting the old text back would
            # edit a recipe the meal no longer uses, on other nights' food.
            conn.rollback()
            return {"status": "refused", "said": UNDO_MOVED_ON}
        # Before the fork branch, not after it (2026-10-28): a second Tweak of
        # the same night rewrites the COPY in place, so its request names the
        # copy too, and undoing the older fork deleted the copy with the newer
        # change on it — both gone for one tap. Newest first, either path.
        newer = conn.execute(
            "SELECT 1 FROM recipe_change_requests WHERE household_id = ? AND recipe_id = ? "
            "AND id > ? AND outcome = ? LIMIT 1",
            (hid, int(req["recipe_id"]), int(request_id), OUTCOME_REWRITTEN),
        ).fetchone()
        if newer:
            conn.rollback()
            return {"status": "refused", "said": UNDO_NEWER}
        if snapshot.get("forked_from"):
            return _undo_fork(conn, req, entry, snapshot)
        try:
            old_ings = json.loads(snapshot.get("ingredients_json") or "[]")
        except (TypeError, ValueError):
            old_ings = []
        refused = _undo_blocked(snapshot.get("name") or req["dish_name"] or "", old_ings, entry)
        if refused:
            conn.rollback()
            return refused
        users = _approved_users_of(conn, req["recipe_id"])
        approved = bool(users)
        before = _needed_lines(conn) if approved else {}
        conn.execute(
            "UPDATE recipes SET " + ", ".join(f"{c} = ?" for c in _SNAPSHOT_COLUMNS) +
            " WHERE id = ? AND household_id = ?",
            [snapshot.get(c) for c in _SNAPSHOT_COLUMNS] + [int(req["recipe_id"]), hid],
        )
        after = _resync_users(conn, users) if users else before
        conn.execute(
            "UPDATE recipe_change_requests SET outcome = ? WHERE id = ? AND household_id = ?",
            (OUTCOME_UNDONE, int(request_id), hid),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    changed = _lines_changed(before, after)
    said = (req["dish_name"] or "That one") + " is back to the recipe it had."
    if approved and changed:
        said += f" {changed} thing{'' if changed == 1 else 's'} changed on the list."
    return {"status": "undone", "said": said, "lines_changed": changed}
