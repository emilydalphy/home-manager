"""
The grocery list: adding, merging, marking, clearing and repairing items.
"""
from __future__ import annotations

import json
from datetime import date
from ..db import get_conn
from ._shared import acting_name, household_id, require_household_row
from . import inventory as _inventory
from . import quantities as _quantities
from . import weekly_plan as _weekly_plan


# Single-word names where the plural is a DIFFERENT product, not more of
# the same one. "Pepper" is the black pepper in the cupboard; "peppers"
# are the bell peppers in the fridge. Merging those puts a pantry staple
# under Produce and it never gets bought -- silently, which is the whole
# failure this normalisation is supposed to avoid.
#
# Only applies to a bare one-word name, because that is where the
# ambiguity lives. "Bell peppers" and "chocolate chips" say which thing
# they are, so they still merge with their singulars normally.
_NUMBER_CHANGES_MEANING = {
    "pepper", "peppers",
    "chili", "chilis", "chilli", "chillies", "chile", "chiles",
    "green", "greens",
    "chip", "chips", "crisp", "crisps",
    "ground", "grounds",
    "sprout", "sprouts",
    "grit", "grits",
    "bitter", "bitters",
    # Uncountables people only ever write one way; the "singular" is not a
    # word anyone would type on a list.
    "oat", "oats", "pea", "peas", "grape", "grapes",
}

# Plurals that are just the word plus an "s", where the general rules
# would guess wrong: "cookies" is cookie+s, not cooky, and "quiches" is
# quiche+s, not quich. English has no reliable way to tell these from
# "berries" and "peaches" by spelling alone, so the honest fix is to name
# the ones that actually turn up on a shopping list.
_JUST_ADD_S = {
    "cookies", "brownies", "smoothies", "veggies", "pies", "quiches",
    "brioches", "cupcakes", "pastries",
}


def _merge_key(name: str) -> str:
    """
    The name two grocery lines have to share to be the same thing.

    Case and spacing were already ignored; number was not, so "Bell
    pepper" and "Bell peppers" sat on the list as two separate lines whose
    quantities never combined -- which reads to whoever is shopping as two
    different things to buy.

    Only the LAST word is singularised. In an English food name the
    trailing word is the thing itself and the leading words describe it
    ("bell pepper", "spring onion", "chicken thigh"), so that is where
    number lives; touching earlier words would start collapsing names that
    genuinely differ.

    Deliberately conservative, and it fails toward leaving two lines
    rather than combining two things. A duplicate line is visible and
    mildly annoying; a wrong merge is invisible and means something never
    gets bought.
    """
    cleaned = " ".join((name or "").strip().lower().split())
    if not cleaned:
        return ""
    words = cleaned.split(" ")
    # A bare ambiguous noun is left exactly as written, so "Pepper" and
    # "Peppers" stay the two different things they are.
    if len(words) == 1 and words[0] in _NUMBER_CHANGES_MEANING:
        return cleaned
    words[-1] = _singular_word(words[-1])
    return " ".join(words)


def _singular_word(word: str) -> str:
    if len(word) <= 3 or not word.endswith("s"):
        return word
    if word.endswith("ss"):                  # glass, cress, watercress
        return word
    if word in _JUST_ADD_S:
        return word[:-1]
    if word.endswith("ies"):                 # berries -> berry
        return word[:-3] + "y"
    if word.endswith("oes"):                 # tomatoes -> tomato
        return word[:-2]
    if word.endswith(("ches", "shes", "xes", "zes")):   # peaches -> peach
        return word[:-2]
    # Everything else drops the s. Words like "asparagus" and "hummus"
    # come out mangled ("asparagu") but consistently so, which is all a
    # matching key needs -- they only ever have to equal themselves.
    return word[:-1]


def _try_consolidate_quantity(existing_qty: str, new_qty: str) -> tuple[str, bool]:
    """
    Try to merge two quantity strings for the same grocery item. Returns
    (resulting_quantity_string, was_merged). Merges when both are parseable
    and share the same unit (e.g. "2 cups" + "1 cup" -> "3 cups"), or when
    one side is blank. When units are both present but don't reconcile
    (e.g. "2 cups flour" + "1 lb flour"), nothing is guessed — both amounts
    are kept together on one line so the shopper sees both rather than a
    silently wrong conversion.

    What it will NOT do is write the same quantity twice. Two sides that
    are the same words are the same unit whether or not this module can
    read that unit, and "2 lb bag (frozen) + 2 lb bag (frozen) + 2 lb bag
    (frozen) + 2 lb bag (frozen)" is not an honest report of uncertainty,
    it is four copies of one line — see _repeat_or_concatenate.
    """
    note = _quantities._merge_notes(
        _quantities._quantity_note(existing_qty), _quantities._quantity_note(new_qty)
    )
    existing_qty = _quantities._strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _quantities._strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    new_parsed = _quantities._parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], existing_parsed[1]),
            note,
        ), True
    shared_unit = _shared_package_unit(existing_parsed, new_parsed)
    if shared_unit:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], shared_unit), note
        ), True
    return _repeat_or_concatenate(existing_qty, new_qty, sum_counts=True)


def _shared_package_unit(existing_parsed, new_parsed) -> str | None:
    """
    The unit two parsed quantities should merge under when their units
    differ only in whether the package's SIZE was stated — "1 bag (2 lb)"
    from one recipe beside "1 bag" from another. To a shopper those are
    two bags of the same thing, and leaving them as "1 bag (2 lb) + 1 bag"
    is the concatenation bug wearing a different hat. The stated size
    wins, because it says more.

    Returns None — and the caller falls through to the honest "both
    amounts, unreconciled" line — whenever there is a real disagreement to
    report: a different container word, or two DIFFERENT stated sizes
    ("1 bag (2 lb)" against "1 bag (500 g)"), where picking one would
    silently throw away what the other recipe asked for.
    """
    if not existing_parsed or not new_parsed or not existing_parsed[1] or not new_parsed[1]:
        return None
    existing_head, existing_size = _quantities._split_package_size(existing_parsed[1])
    new_head, new_size = _quantities._split_package_size(new_parsed[1])
    if existing_head != new_head or existing_head not in _quantities._CONTAINER_UNIT_PLURALS:
        return None
    if existing_size and new_size:
        return None
    return existing_parsed[1] if existing_size else new_parsed[1]


def _repeat_or_concatenate(existing_qty: str, new_qty: str, sum_counts: bool) -> tuple[str, bool]:
    """
    The last resort when two quantities for the same item can't be added.
    Identical text is never written twice: it collapses to one copy with a
    "×N" repeat marker ("a handful" + "a handful" -> "a handful ×2"), and
    an existing marker counts up rather than starting a second line.
    sum_counts says whether N adds (four meals each wanting one) or takes
    the larger (a sealed package, where four meals still want one) — the
    same split as _try_consolidate_quantity vs _greater_of_quantity.

    Genuinely different text still concatenates, because that is the
    honest answer: "2 cups + 1 lb" tells the shopper there are two amounts
    and this module could not reconcile them. What it stops being is a way
    for one repeated breakfast to fill a line with copies of itself.
    """
    existing_base, existing_count = _quantities._split_repeat_count(existing_qty)
    new_base, new_count = _quantities._split_repeat_count(new_qty)
    if existing_base.lower() == new_base.lower():
        count = existing_count + new_count if sum_counts else max(existing_count, new_count)
        return _quantities._with_repeat_count(existing_base, count), True
    return f"{existing_qty} + {new_qty}", False


def _merge_target(same_name: list, quantity: str, consolidate, standing: bool):
    """
    Which of the lines already on the list with this name a new amount
    joins — (row, merged quantity, units reconciled) — or None for a line
    of its own.

    The first line the amount adds up with cleanly wins, in list order.
    Failing that, the amount concatenates ("2 cups + 1 lb") onto the
    first line of its OWN KIND — a person's add onto a person's line, a
    plan's onto a plan's — and never across that line. `standing` says
    which kind the add is (True: no plan behind it).

    Why the line between the two kinds matters (Loop Board bug,
    2026-09-13): a hand-added "1" eggs that a recipe's "2 cups" was
    concatenated onto read "1 + 2 cups", and nothing could ever take the
    plan's share back off — the reversal reads a line as one number in one
    unit, and "1 + 2 cups" is not one — so every week added another
    "+ 2 cups" for as long as the eggs stayed unbought. Two lines are the
    honest answer: the person's want, untouched, and the plan's amount on
    a plan-owned line that recomputes from its ledger, leaves with its
    week, and is set aside as a leftover like any other plan line. A
    plan's two recipes that disagree on a unit still share ONE plan line,
    exactly as before, because that line is fully described by its ledger
    and recomputes correctly whatever it reads.

    One line is a person's to join whatever it reads: a spice still
    unticked in the "Spices this week" section (status 'spice',
    spices.py). That line is the plan's reminder, not an amount, and a
    person asking for cumin is answering it — their add ticks it onto the
    list (add_grocery_item), so it must land there and not beside it.
    """
    kin = None
    for row in same_name:
        merged_qty, merged = consolidate(row["quantity"] or "", quantity)
        if merged:
            return row, merged_qty, True
        own_kind = (row["source_weekly_plan_id"] is None) == standing or (standing and row["status"] == "spice")
        if kin is None and own_kind:
            kin = (row, merged_qty, False)
    return kin


def _greater_of_quantity(existing_qty: str, new_qty: str) -> tuple[str, bool]:
    """
    Keep the LARGER of two quantities for the same grocery item instead of
    their sum. Same contract as _try_consolidate_quantity — returns
    (resulting_quantity_string, was_merged) and concatenates rather than
    guessing when the units don't reconcile.

    This is what "one bottle of olive oil per week" means in code. Three
    dinners that each list "1 bottle" want one bottle between them, not
    three; a recipe that genuinely asks for "2 bottles" still wins over a
    "1 bottle" beside it. Only the sealed-package quantities identified by
    quantities.package_unit are merged this way — see
    recipes._add_recipe_ingredients_for_entries, the only caller.
    """
    note = _quantities._merge_notes(
        _quantities._quantity_note(existing_qty), _quantities._quantity_note(new_qty)
    )
    existing_qty = _quantities._strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _quantities._strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    new_parsed = _quantities._parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(max(existing_parsed[0], new_parsed[0]), existing_parsed[1]),
            note,
        ), True
    shared_unit = _shared_package_unit(existing_parsed, new_parsed)
    if shared_unit:
        return _quantities._with_note(
            _quantities._humanize_grocery_quantity(max(existing_parsed[0], new_parsed[0]), shared_unit), note
        ), True
    return _repeat_or_concatenate(existing_qty, new_qty, sum_counts=False)


def _subtract_quantity(current_qty: str, remove_qty: str) -> tuple[str, bool]:
    """
    The inverse of _try_consolidate_quantity — used when a meal that
    contributed some amount to a grocery line is being un-planned (see
    _reverse_meal_grocery_contributions) and that amount needs to come back
    out. Returns (resulting_quantity_string, fully_removed). When both sides
    parse and their units reconcile — the same unit, or two units in the
    same measurable family (lb/oz, cup/tbsp/tsp, g/kg, ml/l; see
    quantities._convert_to_unit) — subtracts normally, treating a
    non-positive remainder as "nothing left" (fully_removed=True, resulting
    string blank). When they can't be reconciled (freeform text, unrelated
    units) but the two strings are identical, that means this contribution
    *is* the whole line (nothing else merged into it), so it's still safe
    to remove entirely. Otherwise nothing is guessed — the line is left
    exactly as-is (fully_removed=False, resulting string unchanged) rather
    than risk deleting an amount still needed for something else.

    This is the fallback path now — a grocery line with other ledger rows
    still on it is reconciled by summing what's LEFT instead (see
    quantities._sum_ledger_quantities), which is what actually fixed the
    lb/oz stranding this function alone couldn't: it only ever sees the
    one subtraction in front of it, not that the line's display unit
    rolled between when this contribution was recorded and now. This still
    has to be unit-normalising itself, though, for the lines that never
    had ledger rows to recompute from (a hand-added line with its own
    quantity edit) and for the household's own standing wants, which are
    never replaced by a recompute — see _reverse_meal_grocery_contributions.

    Whatever _try_consolidate_quantity can write, this has to be able to
    unwrite, or a swap leaves the list slowly drifting: the note a merged
    line carries survives the subtraction ("3 bags, frozen" less one bag
    is "2 bags, frozen"), and a "×N" repeat marker counts back down
    instead of being left as an unreconcilable string.
    """
    note = _quantities._quantity_note(current_qty)
    current_qty = _quantities._strip_prep_descriptor((current_qty or "").strip())
    remove_qty = _quantities._strip_prep_descriptor((remove_qty or "").strip())
    if not current_qty:
        return "", True
    if not remove_qty or current_qty == remove_qty:
        return "", True
    current_parsed = _quantities._parse_quantity(current_qty)
    remove_parsed = _quantities._parse_quantity(remove_qty)
    if current_parsed and remove_parsed:
        remove_amount = _quantities._convert_to_unit(remove_parsed[0], remove_parsed[1], current_parsed[1])
        if remove_amount is not None:
            remainder = current_parsed[0] - remove_amount
            if remainder <= 0.0001:
                return "", True
            return _quantities._with_note(
                _quantities._humanize_grocery_quantity(remainder, current_parsed[1]), note
            ), False
    current_base, current_count = _quantities._split_repeat_count(current_qty)
    remove_base, remove_count = _quantities._split_repeat_count(remove_qty)
    if current_base.lower() == remove_base.lower():
        remaining = current_count - remove_count
        if remaining <= 0:
            return "", True
        return _quantities._with_repeat_count(current_base, remaining), False
    return current_qty, False


def _restate_standing_want(
    current_qty: str, total_before: float, total_after: float, unit: str | None,
) -> tuple[str, bool] | None:
    """
    Write a household's own standing want back out with the plan's share
    changed — "what is on this line now, less what the plan's meals rounded
    to, plus what the ones still planned round to."

    Returns (quantity, fully_removed) or None when the line itself can't be
    read as a number, or its unit can't be reconciled with the plan's, in
    which case the caller falls back to _subtract_quantity.

    Three things make it work, and every one of them was learned by
    measuring drift that survived the version before it.

    ONE STEP, not a running subtraction. Taking a share off and
    re-humanizing the whole line at every removal snaps the result to a
    quantum each time, and the residue accumulates instead of cancelling.

    THE PLAN'S SHARE IS ROUNDED THE WAY THE INGEST ROUNDED IT
    (_shopping_round on the ledger's own unit, rolled up exactly as
    recipes._week_bought_amount does, and only then converted into the
    line's unit). Rounding it in the LINE's unit instead looks tidier and
    is wrong whenever the line's display unit rolls mid-sequence: the first
    removal is then rounded at a quarter-POUND, over-crediting the plan,
    the line rolls to ounces, and the later removals — now on a quarter-
    ounce quantum — never give the over-credit back. Measured: a hand-added
    "2 oz" under three dinners came back BLANK, and a "500 ml" staple came
    back as 416.75 ml and kept falling.

    THE HOUSEHOLD'S OWN AMOUNT IS SNAPPED BACK ONTO THE LINE'S QUANTUM
    (_snap_to_unit_quantum). It is re-derived out of the displayed line
    every time, and the line has been re-rounded for display in between, so
    without this the derived value drifts a little further each removal.
    Snapping is skipped when it would annihilate a genuinely positive
    amount — a 2 oz want on a line reading in pounds is smaller than half
    that line's quantum, and the household's own two ounces must not
    disappear because of the unit its line happens to be shown in.

    WHAT IS NOT PROMISED. An exact restate is not reachable across a
    rolling display unit: the line is a rounded string, and what the add
    rounded away is not recoverable from it — 15 oz of plan on a
    household's own 8 oz is stored as "1.5 lbs" and the 1.75 oz difference
    is gone before any reversal runs. "Never below the household's own
    amount" is therefore NOT the promise, and is not a bar the code this
    replaces clears either. What IS held to: a line somebody typed is never
    blanked; nothing compounds week to week; and there are strictly fewer
    below-own outcomes than before, at comparable worst case. The tail that
    remains is the snap below — putting a display-drifted number back on a
    quantum can be half a quantum out, which at a quarter-POUND is three
    ounces. One-shot, never cumulative, and measured better in aggregate
    than what it replaces. The numbers are in CLAUDE.md's entry for this
    change. The bias is upward wherever the arithmetic has any freedom,
    which is this module's whole stance (an extra pepper costs a pepper; a
    missing one costs the dinner).

    The household's own amount is also floored at nothing, which is load-
    bearing rather than defensive: hand-edit an 8 down to 2 mid-week and
    the plan's share is bigger than the whole line, so without the floor
    the next removal blanks something the household typed.
    """
    note = _quantities._quantity_note(current_qty or "")
    parsed = _quantities._parse_quantity(current_qty or "")
    if not parsed:
        return None
    current_amount, line_unit = parsed
    rounded_before, before_unit = _quantities._shopping_round(total_before, unit)
    rounded_after, after_unit = _quantities._shopping_round(total_after, unit)
    plan_before = _quantities._convert_to_unit(rounded_before, before_unit, line_unit)
    plan_after = _quantities._convert_to_unit(rounded_after, after_unit, line_unit)
    if plan_before is None or plan_after is None:
        return None
    households_own = max(0.0, current_amount - plan_before)
    snapped = _quantities._snap_to_unit_quantum(households_own, line_unit)
    if snapped > 0 or households_own <= 0:
        households_own = snapped
    total = households_own + plan_after
    if total <= 0:
        return "", True
    return _quantities._with_note(
        _quantities._humanize_grocery_quantity(total, line_unit), note,
    ), False


def _reverse_meal_grocery_contributions(entry_id: int, conn=None, only_items=None, only_link_ids=None) -> dict:
    """
    Undo whatever a meal_plan_entries row added to the grocery list, via the
    meal_plan_grocery_links ledger recorded at plan_meal() time — called
    right before that entry is deleted (see swap_meal_in_plan/
    swap_component_in_plan) so changing a planned meal actually replaces its
    ingredients on the grocery list instead of only ever piling the new
    meal's ingredients on top of the old ones. For each linked grocery line,
    puts the line back to what every OTHER meal still on the ledger for it
    actually adds up to (see quantities._sum_ledger_quantities) —
    removing the line entirely once no ledger row for it remains at all,
    or updating it to that recomputed total otherwise. A line already
    moved to in_cart/purchased is left alone regardless — the shopper has
    already acted on it, so this won't yank something out of a cart
    mid-trip. Always clears the ledger rows for this entry afterward,
    whether or not anything was adjusted.

    Recomputing from the ledger, rather than subtracting this one
    contribution out of whatever the line currently displays, is what
    keeps this exactly reversible regardless of what order a week's
    reversals happen in (see clear_weekly_plan, which reverses a week's
    entries in no particular order). A grocery line's display unit rolls
    to whatever reads best AT ITS CURRENT TOTAL (see
    quantities._humanize_grocery_quantity) — sequentially subtracting one
    contribution at a time can walk that total down through a unit
    boundary (a line at "1.25 lbs" becomes "8 oz" once it drops under a
    pound), and the NEXT contribution still on the ledger was recorded
    against the total as it stood at ingest, not against whatever unit the
    line happens to display now. Recomputing the whole remaining total in
    one pass sidesteps that entirely: it only ever asks what's left, never
    what changed.

    That recompute only ever replaces a line's quantity with something the
    ledger can fully account for, which is true whenever the line's
    source_weekly_plan_id is set — every dollar of it came from a
    recipe, and the ledger has a row for each one. A line with no
    source_weekly_plan_id (see add_grocery_item's keep_standing) has at
    least one contribution the ledger doesn't know about — a person's own
    standing want, or an amount they added by hand — so it falls back to
    subtracting this one meal's share out of the current display instead
    (see _subtract_quantity), and is never deleted by this at all: the
    meals that borrowed space on it are gone, but the want isn't.

    A SEALED-PACKAGE line (one bottle of oil, one bag of granola — see
    quantities.package_unit) is a separate exception, and has to be,
    because the add side no longer adds one per meal: the whole week's oil
    is a single bottle however many dinners named it. Subtracting a bottle
    per meal would take the line off the list the first time any one of
    those meals changed, while the rest still needed it. So a package line
    survives until the LAST meal holding a link to it goes, and then goes
    with it — which keeps clearing a whole week exactly symmetric with
    approving it, and leaves the bottle alone when a single meal is
    swapped. A package line with no source_weekly_plan_id was asked for by
    a person directly and is never removed by this at all; the plan
    borrowed it, it doesn't own it.

    `conn` is for one caller and is not part of the assistant-facing API:
    retire_overlapping_plans runs a whole multi-plan takeover inside ONE
    write transaction, so it hands its connection down rather than letting
    each meal's reversal commit on its own — a crash between two of those
    commits used to leave the first plan's groceries reversed while the
    caller reported failure. Given a connection, this reads and writes on
    it and neither commits nor closes: the caller owns both. Left unset,
    every other call site behaves exactly as before — its own connection,
    its own commit, its own close.

    `only_link_ids` narrows the reversal to those ledger rows of the entry;
    `only_items` to the rows whose item is one of these names (matched on
    _merge_key). Both are plates.remove_component taking one added side
    back off a meal without touching the dish's own shopping. Left unset,
    every row the entry holds is reversed, as before. In every case
    "every OTHER contribution" to a line means every ledger row not being
    reversed here, by row id — so a side's lemon coming off leaves the
    recipe's own lemon, on the same entry, counted.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    links = conn.execute(
        "SELECT id, grocery_item_id, item, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? AND meal_plan_entry_id = ?",
        (household_id(), entry_id),
    ).fetchall()
    if only_link_ids is not None:
        keep_ids = {int(i) for i in only_link_ids}
        links = [link for link in links if link["id"] in keep_ids]
    if only_items is not None:
        wanted = {_merge_key(name) for name in only_items if name}
        links = [link for link in links if _merge_key(link["item"] or "") in wanted]
    narrowed = only_items is not None or only_link_ids is not None
    reversing_ids = [link["id"] for link in links]
    not_reversing = "(" + ",".join("?" * len(reversing_ids)) + ")" if reversing_ids else "(-1)"
    removed_items = []
    trimmed_items = []
    for link in links:
        grocery_row = conn.execute(
            "SELECT id, item, quantity, status, source_weekly_plan_id FROM grocery_items WHERE id = ? AND household_id = ?",
            (link["grocery_item_id"], household_id()),
        ).fetchone()
        # A pending spice ('spice', see spices.py) is a needed line the
        # shopper hasn't ticked; it recomputes and clears like one.
        live = bool(grocery_row) and grocery_row["status"] in ("needed", "spice")
        if live and _quantities.package_unit(link["quantity"] or ""):
            still_wanted = conn.execute(
                "SELECT COUNT(*) AS n FROM meal_plan_grocery_links "
                f"WHERE household_id = ? AND grocery_item_id = ? AND id NOT IN {not_reversing}",
                (household_id(), link["grocery_item_id"], *reversing_ids),
            ).fetchone()["n"]
            if not still_wanted and grocery_row["source_weekly_plan_id"] is not None:
                conn.execute("DELETE FROM grocery_items WHERE id = ?", (grocery_row["id"],))
                removed_items.append(grocery_row["item"])
        elif live:
            is_standing_want = grocery_row["source_weekly_plan_id"] is None
            other_rows = conn.execute(
                "SELECT quantity FROM meal_plan_grocery_links "
                f"WHERE household_id = ? AND grocery_item_id = ? AND id NOT IN {not_reversing}",
                (household_id(), link["grocery_item_id"], *reversing_ids),
            ).fetchall()
            other_qtys = [row["quantity"] or "" for row in other_rows]
            summed = None if is_standing_want else _quantities._sum_ledger_quantities(other_qtys)
            if summed is not None:
                # Fully accounted for by the ledger — recompute rather than
                # subtract (see the docstring above for why).
                current_note = _quantities._quantity_note(grocery_row["quantity"] or "")
                new_qty = _quantities._with_note(summed, current_note) if summed else ""
                fully_removed = not other_qtys
            else:
                # A standing want, or a line the ledger can't fully account
                # for — the person's own amount is in this line and no
                # recompute can see it, so this contribution has to be
                # subtracted out of the current display instead.
                #
                # WHAT to subtract is the whole difficulty, and it is not
                # this meal's own share. The ingest adds ONE rounded week
                # total to the line, so taking back each meal's unrounded
                # share leaves a remainder that rounds straight back up and
                # the line ratchets a little higher every week for ever.
                # _ledger_totals + _restate_standing_want put the same
                # single rounding on both sides, in the line's own unit,
                # and restate the line in one step; either declines (None)
                # for the rows it can't read, and then the one contribution
                # in front of us is still the best available answer.
                totals = _quantities._ledger_totals(
                    other_qtys + [link["quantity"] or ""], other_qtys,
                )
                restated = (
                    None if totals is None
                    else _restate_standing_want(grocery_row["quantity"] or "", *totals)
                )
                if restated is None:
                    new_qty, fully_removed = _subtract_quantity(
                        grocery_row["quantity"] or "", link["quantity"] or "",
                    )
                else:
                    new_qty, fully_removed = restated
            if fully_removed:
                if is_standing_want:
                    if new_qty != (grocery_row["quantity"] or ""):
                        conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (new_qty, grocery_row["id"]))
                        trimmed_items.append(grocery_row["item"])
                else:
                    conn.execute("DELETE FROM grocery_items WHERE id = ?", (grocery_row["id"],))
                    removed_items.append(grocery_row["item"])
            elif new_qty != (grocery_row["quantity"] or ""):
                conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (new_qty, grocery_row["id"]))
                trimmed_items.append(grocery_row["item"])
    if not narrowed:
        conn.execute("DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?", (household_id(), entry_id))
    elif reversing_ids:
        conn.execute(
            f"DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND id IN {not_reversing}",
            (household_id(), *reversing_ids),
        )
    if own_conn:
        conn.commit()
        conn.close()
    return {"removed_items": removed_items, "trimmed_items": trimmed_items}


def _recompute_plan_line_from_ledger(item_id: int, conn=None) -> None:
    """
    Put a plan-owned grocery line back to what its whole ledger adds up
    to — the recompute half of _reverse_meal_grocery_contributions, run
    after an ADD instead of after a removal.

    Needed for a counted pack (quantities._PACK_CONVERSION_GROUPS), and
    only there. Every ingest pass rounds its own total up to whole packs
    before it reaches the list, so a second pass onto the same line — a
    meal swapped in after approval, a dinner planned in chat — adds a
    whole carton to a whole carton: three eggs already on the line and
    three more arriving read "1 dozen" + "1 dozen" = "2 dozen" for six
    eggs. The ledger still holds the six, so the line is simply re-read
    from it, exactly as a reversal would. A measurable unit rounds to a
    quarter and a count to a whole one, both small enough that summing
    two rounded displays has always been accepted; a pack is twelve.

    Only a line the ledger fully describes (source_weekly_plan_id set)
    is touched; a person's standing want keeps whatever it reads. A
    ledger the recompute can't read (a freeform row) leaves the line
    alone too.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    row = conn.execute(
        "SELECT id, quantity, status, source_weekly_plan_id FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if not row or row["source_weekly_plan_id"] is None or row["status"] not in ("needed", "spice"):
        if own_conn:
            conn.close()
        return
    qtys = [
        r["quantity"] or "" for r in conn.execute(
            "SELECT quantity FROM meal_plan_grocery_links WHERE household_id = ? AND grocery_item_id = ? ORDER BY id",
            (household_id(), item_id),
        ).fetchall()
    ]
    summed = _quantities._sum_ledger_quantities(qtys)
    if summed:
        new_qty = _quantities._with_note(summed, _quantities._quantity_note(row["quantity"] or ""))
        if new_qty != (row["quantity"] or ""):
            conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (new_qty, row["id"]))
    if own_conn:
        conn.commit()
        conn.close()


def add_grocery_item(
    item: str,
    quantity: str = "",
    category: str = "other",
    added_by: str = "user",
    source_weekly_plan_id: int | None = None,
    quantity_mode: str = "sum",
    conn=None,
) -> dict:
    """
    Add an item to the grocery list. If an item with the same name is
    already on the list (status 'needed'), the quantity is consolidated
    into that single line — e.g. "2 cups flour" + "1 cup flour" becomes
    "3 cups flour" — instead of creating a duplicate line. If the
    quantities can't be reconciled (different, incompatible units), both
    are kept together on the one line rather than silently guessing a
    conversion — EXCEPT across the line between a person's own want and a
    plan's: a hand-added "1" eggs and a recipe's "2 cups" stay two lines,
    the person's and the plan's, so each can be read, bought and cleared
    on its own (see _merge_target). category should be one of: produce,
    dairy, meat/seafood, pantry, frozen, other — pick the one that
    actually matches the item so the list stays organized by store
    section. Leave source_weekly_plan_id
    unset for anything a person asked for directly (or an ad hoc one-off
    meal) — it marks the item as a standing want that should never be
    auto-cleared. It's set automatically when ingredients come from a
    generated weekly plan (see plan_meal/generate_weekly_plan), so
    clear_stale_grocery_items can tell a current week's ingredients apart
    from an old week's leftovers.

    quantity_mode is for callers, not for the assistant: leave it at "sum"
    for anything a person asks for. "max" keeps the larger of the two
    quantities rather than adding them, which is how a sealed package a
    whole week draws on lands once instead of once per meal — see
    _greater_of_quantity and recipes._add_recipe_ingredients_for_entries.

    `conn` is for the grocery ingest and is not part of the assistant-facing
    API, the same arrangement _reverse_meal_grocery_contributions has:
    swap_meal_in_plan buys the new meal's ingredients inside ONE write
    transaction, and every line that ingest lands has to be written on that
    connection — a second connection writing while the first holds the
    write lock is the "database is locked" trap. Given a connection this
    reads and writes on it and neither commits nor closes; left unset,
    every other call site behaves exactly as before.
    """
    quantity = _quantities._normalize_grocery_quantity(quantity or "")
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    # Compared in Python rather than SQL because the comparison is a
    # normalised key, not a column value. The 'needed' list is a shopping
    # list -- tens of rows -- so reading it to find one match is cheaper
    # than it looks and far clearer than encoding the rule in SQL.
    # 'spice' as well as 'needed': a spice the week's recipes put in the
    # "Spices this week" section, still unticked (see spices.py). A second
    # recipe's cumin joins that line rather than starting another, and a
    # person asking for cumin ticks it — below.
    candidates = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id, status FROM grocery_items "
        "WHERE household_id = ? AND status IN ('needed', 'spice') ORDER BY id",
        (household_id(),),
    ).fetchall()
    wanted = _merge_key(item)
    same_name = [r for r in candidates if _merge_key(r["item"]) == wanted]
    consolidate = _greater_of_quantity if quantity_mode == "max" else _try_consolidate_quantity
    target = _merge_target(same_name, quantity, consolidate, standing=source_weekly_plan_id is None)
    # Matched on the same key the list itself merges on. Otherwise a
    # preference saved for "bell peppers" never applies to the line that
    # won the merge under the name "Bell pepper": the app confirms the
    # preference and it silently never takes effect.
    prefs = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?",
        (household_id(),),
    ).fetchall()
    pref = next((p for p in prefs if _merge_key(p["item"]) == _merge_key(item)), None)
    preferred_store = pref["store"] if pref else ""
    if target:
        existing, merged_qty, merged = target
        # A row with no source_weekly_plan_id is something a person asked
        # for directly, and clear_stale_grocery_items is required to leave
        # those alone forever. Stamping this week's plan id onto it during
        # a merge would quietly convert a standing want into a line the
        # next generation deletes -- so a plan can add quantity to a
        # hand-added item, but it cannot take ownership of it.
        keep_standing = existing["source_weekly_plan_id"] is None
        conn.execute(
            "UPDATE grocery_items SET quantity = ?, category = ?, "
            "source_weekly_plan_id = CASE WHEN ? THEN NULL ELSE ? END, "
            # Fills in a store the row doesn't have yet, without
            # overwriting one already chosen for this line.
            "store = CASE WHEN store = '' THEN ? ELSE store END WHERE id = ?",
            (merged_qty, category, 1 if keep_standing else 0, source_weekly_plan_id,
             preferred_store, existing["id"]),
        )
        # A person asking for a spice by name wants it bought: their add
        # ticks the pending line onto the list. A plan's add leaves it in
        # the section, unticked, whatever the quantity now reads.
        if existing["status"] == "spice" and source_weekly_plan_id is None:
            conn.execute("UPDATE grocery_items SET status = 'needed' WHERE id = ?", (existing["id"],))
        item_id = existing["id"]
        # The name already on the list, not the one just asked for: the
        # row keeps its own wording, so saying "item" back means the line
        # the shopper will actually see.
        item_name = existing["item"]
        if own_conn:
            conn.commit()
            conn.close()
        return {"item_id": item_id, "item": item_name, "quantity": merged_qty, "merged": True, "units_reconciled": merged}

    # Who added it: the adult picked on this device, when the caller did
    # not say (the shell sends nothing; the offline queue replays the same
    # body). "ai" and an explicit name are kept — see _shared.acting_name.
    # A plan's spice starts in the "Spices this week" section, unticked —
    # the list assumes a spice rack (Emily, 2026-09-13; see spices.py). A
    # person's own add is a thing to buy, spice or not. Imported here, not
    # at the top: spices.py imports this module for the merge key.
    from . import spices as _spices
    status = "spice" if source_weekly_plan_id is not None and _spices.is_spice(item) else "needed"
    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, store, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (household_id(), item, quantity, category, acting_name(added_by), source_weekly_plan_id, preferred_store, status),
    )
    item_id = cur.lastrowid
    if own_conn:
        conn.commit()
        conn.close()
    return {"item_id": item_id, "item": item, "quantity": quantity, "merged": False, "units_reconciled": True}


def add_grocery_items(items: list, category: str = "other", added_by: str = "user") -> dict:
    """
    Add several items to the grocery list at once. Each entry can be a
    plain string (e.g. "milk") or, when you know more, a dict like
    {"item": "flour", "quantity": "2 cups", "category": "pantry"} — mix
    and match as needed. Prefer setting an accurate category per item
    (produce, dairy, meat/seafood, pantry, frozen, other) so the list
    stays organized by store section; the category argument is only a
    fallback for entries you didn't categorize individually. Quantities
    are consolidated with any matching item already on the list rather
    than creating duplicate lines (see add_grocery_item).
    """
    added, merged = [], []
    for raw in items:
        if isinstance(raw, dict):
            name = (raw.get("item") or "").strip()
            qty = raw.get("quantity", "")
            cat = raw.get("category") or category
        else:
            name = (raw or "").strip()
            qty = ""
            cat = category
        if not name:
            continue
        result = add_grocery_item(name, quantity=qty, category=cat, added_by=added_by)
        (merged if result["merged"] else added).append(name)
    return {"added": added, "merged_with_existing": merged}


def list_grocery_list(status: str = "needed") -> list[dict]:
    """
    List grocery items, optionally filtered by status: 'needed', 'in_cart',
    'purchased', 'all' (every status, including excluded items), or
    'excluded' (only items hidden via exclude_grocery_item). Two more
    statuses exist and are never "to buy": 'spice' (a recipe's spice
    waiting unticked in the "Spices this week" section — see spices.py)
    and 'carried' (last week's leftover waiting for keep-or-drop). For 'needed'/
    'in_cart'/'purchased', items excluded from the list (see
    exclude_grocery_item) are left out automatically — they're still
    tracked, just not shown on the normal shopping list.
    """
    conn = get_conn()
    if status == "excluded":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 1 ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    elif status == "all":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id FROM grocery_items "
            "WHERE household_id = ? ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, store_decided, excluded_from_list, already_have_reviewed, added_by, staple_id FROM grocery_items "
            "WHERE household_id = ? AND status = ? AND excluded_from_list = 0 ORDER BY category, item",
            (household_id(), status),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def exclude_grocery_item(item_id: int) -> dict:
    """
    Hide an item from the normal shown/shopped grocery list without
    deleting it — for something the Shopper will get elsewhere (a butcher,
    a farmers market) rather than on the regular trip. It stays tracked:
    still in grocery_items with its status unchanged, so a future
    add_grocery_item call for the same item still consolidates into this
    same line instead of creating a duplicate — only its visibility in the
    default 'needed'/'in_cart'/'purchased' views changes. See
    include_grocery_item to undo, and list_grocery_list(status='excluded')
    to see what's currently hidden this way.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 1 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": True}


def include_grocery_item(item_id: int) -> dict:
    """Undo exclude_grocery_item — put an item back on the normal shown/shopped grocery list."""
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    conn.execute(
        "UPDATE grocery_items SET excluded_from_list = 0 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "excluded_from_list": False}


def get_grocery_list_by_section(status: str = "needed") -> dict:
    """
    Get the grocery list grouped into standard store sections (produce,
    dairy, meat/seafood, pantry, frozen, other) in a sensible shopping
    order, rather than a flat list. Use this whenever showing or reviewing
    the grocery list to the user so it reads like something they can
    actually shop from, aisle by aisle, instead of a flat ingredient dump.
    Items hidden via exclude_grocery_item are left out automatically (see
    list_grocery_list) unless status='excluded' or 'all' is passed.
    """
    if status == "needed":
        # A staple that is probably due goes on the list the moment the
        # list is read — the one place the household is already looking.
        # Idempotent, and a no-op for a household with no staples.
        from . import staples as _staples
        _staples.sync_due_staples()
    items = list_grocery_list(status=status)
    sections: dict[str, list[dict]] = {s: [] for s in _quantities._GROCERY_SECTION_ORDER}
    for it in items:
        cat = _quantities._GROCERY_CATEGORY_ALIASES.get(it["category"], it["category"])
        sections.setdefault("other", [])
        sections[cat if cat in sections else "other"].append(it)
    return {"sections": [{"section": s, "items": sections[s]} for s in _quantities._GROCERY_SECTION_ORDER if sections[s]]}


def consolidate_grocery_list(status: str = "needed") -> dict:
    """
    Merge any duplicate lines already on the list (the same item name
    ignoring case and singular/plural) into one line each, combining
    quantities with the
    same logic add_grocery_item uses automatically for new additions.
    Call this if the user asks to clean up/consolidate the list, or if you
    notice the same item appears more than once — items added since
    consolidation shipped shouldn't duplicate going forward, but this
    cleans up anything added before that, or any way it happens to slip
    through.
    """
    conn = get_conn()
    # excluded_from_list rows are hidden from the list on purpose ("we get
    # those at the market"). Folding a visible line into a hidden one --
    # which happened whenever the hidden row had the lower id -- made the
    # visible line disappear and parked its quantity somewhere nobody can
    # see. They are left out of consolidation entirely instead.
    rows = conn.execute(
        "SELECT id, item, quantity, category FROM grocery_items "
        "WHERE household_id = ? AND status = ? AND excluded_from_list = 0 ORDER BY id",
        (household_id(), status),
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_merge_key(r["item"]), []).append(dict(r))

    merged_count = 0
    for entries in groups.values():
        if len(entries) < 2:
            continue
        keep = entries[0]
        merged_qty = keep["quantity"] or ""
        for extra in entries[1:]:
            # Two lines the list keeps apart on purpose — a person's "1"
            # beside a plan's "2 cups" (see _merge_target) — stay apart
            # here too, rather than being glued into the "1 + 2 cups"
            # nothing can take back apart.
            candidate, reconciled = _try_consolidate_quantity(merged_qty, extra["quantity"] or "")
            if not reconciled:
                continue
            merged_qty = candidate
            conn.execute(
                "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
                (extra["id"], household_id()),
            )
            merged_count += 1
        conn.execute(
            "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
            (merged_qty, keep["id"], household_id()),
        )
    conn.commit()
    conn.close()
    return {"lines_merged_away": merged_count}


def repair_grocery_quantities(status: str = "needed") -> dict:
    """
    One-time cleanup for grocery lines whose quantity got stuck as an
    ugly, concatenated "+"-joined string from the prep-descriptor
    consolidation bug (see _strip_prep_descriptor) — e.g. "3, diced + 1,
    diced + 1, diced" instead of a clean "5", or "4.75 cups, sliced + 1/4
    cup, sliced" instead of "5 cups". Re-parses each "+"-joined segment
    (stripping any prep descriptor first) and re-sums same-unit segments
    into one clean total, using the same logic add_grocery_item now uses
    automatically for new additions. A segment that still can't be
    reconciled (mixed incompatible units, or genuinely non-numeric text
    like "a bunch") is left joined with " + " exactly as that same fallback
    would produce today — so this is safe to run more than once. The
    underlying bug is fixed at the source now (see _strip_prep_descriptor),
    so this is purely for repairing lines that already got mangled before
    that fix existed; it isn't something that needs to run automatically
    going forward.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity FROM grocery_items WHERE household_id = ? AND status = ?",
        (household_id(), status),
    ).fetchall()
    fixed = []
    for r in rows:
        qty = r["quantity"] or ""
        if " + " not in qty and "," not in qty:
            continue  # nothing to clean on this line
        segments = [s.strip() for s in qty.split(" + ") if s.strip()]
        cleaned = ""
        for seg in segments:
            cleaned, _ = _try_consolidate_quantity(cleaned, seg)
        if cleaned != qty:
            conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (cleaned, r["id"]))
            fixed.append({"item": r["item"], "before": qty, "after": cleaned})
    conn.commit()
    conn.close()
    return {"fixed_count": len(fixed), "fixed": fixed}


def _live_plan_ids(current_id: int | None) -> list[int]:
    """
    The plans whose ingredients are not stale: the one being generated, plus
    every non-retired plan still holding a day from today onward. Empty only
    when the household has no plan at all, which is the one case that falls
    back to the pre-period query below unchanged.
    """
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, status "
        "FROM weekly_plans WHERE household_id = ?",
        (household_id(),),
    ).fetchall()
    conn.close()
    live = {current_id} if current_id is not None else set()
    for row in rows:
        if row["status"] == "retired":
            continue
        start, days = _weekly_plan.plan_period(row)
        if days > 0 and _weekly_plan.period_end_date(start, days) >= today:
            live.add(row["id"])
    return sorted(live)


def clear_stale_grocery_items(current_weekly_plan_id: int | None = None) -> dict:
    """
    Remove 'needed' grocery items that came from an OLDER generated weekly
    plan — not the current one — and were never marked purchased. This is
    the fix for quantities silently stacking up across several weeks'
    plans onto the same line (e.g. "9 lbs chicken breast" built from 4
    different weeks). Items a person added directly, or that came from an
    ad hoc one-off meal rather than a generated week, are never touched —
    those represent a standing want, not a stale one. Pass
    current_weekly_plan_id explicitly when you already know it (e.g. right
    after creating a new plan); otherwise it falls back to whichever plan
    get_weekly_plan considers most recent. Called automatically at the
    start of every generate_weekly_plan; also fine to call directly if the
    user notices buildup and asks to clean it up.

    "Older" means a plan that no longer holds any day from today onward —
    not merely "not the current one". Before Loop Board "Planning periods,
    not weeks" those were the same sentence, because generating a plan
    replaced whatever was there. They are not the same now: a period
    starting Thursday leaves the previous plan alive with Monday to
    Wednesday still on it, and treating that live sibling as stale would
    delete the ingredients for three days the household is about to cook.
    Plans whose days have genuinely gone by are still cleared, which is the
    quantity-stacking bug this function exists to fix ("9 lbs chicken
    breast built from 4 different weeks"), and so are retired ones.

    Note what this does NOT do to the overlap it leaves behind: the days a
    new period takes over are reconciled by retire_overlapping_plans, which
    runs later in the same generation and reverses per meal — so an item
    already bought is spared. Doing it here instead would have deleted it,
    because this is a blunt DELETE with no ledger behind it.
    """
    current_id = current_weekly_plan_id
    if current_id is None:
        current_id = _weekly_plan.get_weekly_plan().get("weekly_plan_id")
    live_ids = _live_plan_ids(current_id)
    conn = get_conn()
    # 'carried' as well as 'needed': a line set aside by a newer week's
    # approval (set_aside_carried_over_items) and never answered is the
    # same stale leftover this function exists to clear, once the plan it
    # came from has genuinely gone by. 'spice' too — an unticked spice
    # from a week that is over was never wanted.
    if live_ids:
        keep = ",".join("?" * len(live_ids))
        rows = conn.execute(
            f"SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            f"AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id NOT IN ({keep})",
            (household_id(), *live_ids),
        ).fetchall()
    elif current_id is None:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            "AND source_weekly_plan_id IS NOT NULL",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status IN ('needed', 'carried', 'spice') "
            "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
            (household_id(), current_id),
        ).fetchall()
    removed = [r["item"] for r in rows]
    if rows:
        conn.executemany("DELETE FROM grocery_items WHERE id = ?", [(r["id"],) for r in rows])
        conn.commit()
    conn.close()
    return {"removed_count": len(removed), "removed_items": removed}


# ---------- Last week's leftovers ----------
# Loop Board, 2026-09-13 (Emily: "some of the quantities are so high but I
# think it might be because it was adding on from last week's"). Approving
# a new week used to pour its recipes straight onto whatever was still
# unbought from the last one: add_grocery_item found the old "2 lbs chicken
# thighs", summed the new week's 2 lbs into it and stamped the new plan's
# id on the row — so the list said 4 lbs, nothing on screen said why, and
# clear_stale_grocery_items could never take the old share back off,
# because the row now belonged to the new week. It happened every time a
# week was approved while the previous one still had a day left (any
# Sunday), since _live_plan_ids keeps a plan alive through its last day.
#
# Now the old lines are SET ASIDE first (status 'carried'), so the new
# week's ingest writes clean lines of its own, and the Shop tab asks about
# the leftovers one screen before sorting: "Still on the list from last
# week — keep or drop?" Keep adds the old amount onto this week's line
# (or restores the line on its own); Don't need takes it off. Both undo.

_CARRIED_KEPT = "carried_kept"
_CARRIED_DROPPED = "carried_dropped"


def set_aside_carried_over_items(weekly_plan_id: int, conn=None) -> list[dict]:
    """
    Move every still-'needed' line that came from an EARLIER plan's
    recipes out of the way of `weekly_plan_id`'s ingest, so this week's
    quantities land on their own lines rather than on top of last week's.
    Called by approve_weekly_plan on the transition into 'approved', before
    a single ingredient is added. Returns the lines set aside.

    What counts as "from last week": a line whose source_weekly_plan_id is
    another plan whose period has already STARTED. A plan that hasn't
    begun yet is not a leftover — a household that approves two weeks in
    advance is building next week's list, and asking them to keep-or-drop
    it would be asking about groceries nobody has had the chance to buy.
    Hand-added lines (source NULL) are a person's standing want and are
    left exactly where they are; a staple's suggestion has its own
    answers. Excluded lines ("somewhere else") and lines already in a
    cart are the shopper's, not the plan's, and stay too.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_conn()
    today = date.today().isoformat()
    started = set()
    for plan in conn.execute(
        "SELECT id, week_start_date, content_start_date, day_count, status FROM weekly_plans "
        "WHERE household_id = ? AND id != ?",
        (household_id(), weekly_plan_id),
    ).fetchall():
        start, _days = _weekly_plan.plan_period(plan)
        if start <= today:
            started.add(plan["id"])
    rows = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' AND excluded_from_list = 0 "
        "AND staple_id IS NULL AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ? "
        "ORDER BY id",
        (household_id(), weekly_plan_id),
    ).fetchall()
    set_aside = [r for r in rows if r["source_weekly_plan_id"] in started]
    if set_aside:
        conn.executemany(
            "UPDATE grocery_items SET status = 'carried', carried_from_plan_id = source_weekly_plan_id "
            "WHERE id = ? AND household_id = ?",
            [(r["id"], household_id()) for r in set_aside],
        )
    # An earlier week's spices still UNTICKED in the section were never
    # wanted, so there is nothing to keep or drop: they go, and this
    # week's recipes put their own in (spices.py).
    stale_spices = conn.execute(
        "SELECT id, source_weekly_plan_id FROM grocery_items WHERE household_id = ? AND status = 'spice' "
        "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
        (household_id(), weekly_plan_id),
    ).fetchall()
    stale_spices = [r for r in stale_spices if r["source_weekly_plan_id"] in started]
    if stale_spices:
        conn.executemany(
            "DELETE FROM grocery_items WHERE id = ? AND household_id = ?",
            [(r["id"], household_id()) for r in stale_spices],
        )
    if own_conn:
        conn.commit()
        conn.close()
    return [{"item_id": r["id"], "item": r["item"], "quantity": r["quantity"] or ""} for r in set_aside]


def _this_weeks_line(conn, item: str, quantity: str):
    """
    The line a carried-over amount would merge into, if this week's
    recipes (or a hand add) put one there: a 'needed' line, or a spice
    still unticked in the section (spices.py) — keeping last week's cumin
    is saying you want to buy cumin, so the kept amount lands on that line
    and ticks it.

    Only a line the amount adds up with CLEANLY counts. Last week's "2 lbs"
    beside this week's hand-added "1" is the same unit clash add_grocery_item
    keeps as two lines (see _merge_target), and Keep must not put it back
    together as "1 + 2 lbs" — the caller restores the carried line on its
    own instead.
    """
    wanted = _merge_key(item)
    for r in conn.execute(
        "SELECT id, item, quantity, status FROM grocery_items WHERE household_id = ? "
        "AND status IN ('needed', 'spice') ORDER BY id",
        (household_id(),),
    ).fetchall():
        if _merge_key(r["item"]) == wanted and _try_consolidate_quantity(r["quantity"] or "", quantity)[1]:
            return r
    return None


def list_carried_over_items() -> list[dict]:
    """
    What is still on the list from an earlier week and waiting for an
    answer — the rows set aside by set_aside_carried_over_items and not
    yet kept or dropped. Each carries `this_week_quantity`: what this
    week's recipes put on the list for the same thing, so the two amounts
    can be shown side by side rather than as one inflated number.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, store, carried_from_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'carried' ORDER BY category, item",
        (household_id(),),
    ).fetchall()
    out = []
    for r in rows:
        this_week = _this_weeks_line(conn, r["item"], r["quantity"] or "")
        out.append({
            "item_id": r["id"], "item": r["item"], "quantity": r["quantity"] or "",
            "category": r["category"], "store": r["store"] or "",
            "carried_from_plan_id": r["carried_from_plan_id"],
            "this_week_quantity": (this_week["quantity"] or "") if this_week else None,
        })
    conn.close()
    return out


def keep_carried_over_item(item_id: int) -> dict:
    """
    "Keep" on a carried-over line: the household still wants it. When this
    week's recipes put the same thing on the list, the old amount is added
    onto that line — the same consolidation add_grocery_item does, but
    asked for out loud this time — and the carried row is soft-removed
    (removed_by 'carried_kept') so an undo can take exactly that amount
    back off. Otherwise — nothing on the list for it this week, or only a
    line in a unit the old amount can't add up with ("1" beside "2 lbs";
    see _this_weeks_line) — the line itself comes back as needed, as the
    household's own standing want (source NULL): it was asked for, so no
    later week's cleanup may quietly delete it.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, category, status FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] != "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    target = _this_weeks_line(conn, row["item"], row["quantity"] or "")
    if target is not None:
        merged_qty, _reconciled = _try_consolidate_quantity(target["quantity"] or "", row["quantity"] or "")
        conn.execute(
            "UPDATE grocery_items SET quantity = ?, status = 'needed' WHERE id = ? AND household_id = ?",
            (merged_qty, target["id"], household_id()),
        )
        conn.execute(
            "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
            "WHERE id = ? AND household_id = ?",
            (_CARRIED_KEPT, item_id, household_id()),
        )
        conn.commit()
        conn.close()
        return {
            "item_id": item_id, "item": target["item"], "kept": True,
            "merged_into": target["id"], "quantity": merged_qty,
        }
    conn.execute(
        "UPDATE grocery_items SET status = 'needed', source_weekly_plan_id = NULL "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "kept": True, "merged_into": None, "quantity": row["quantity"] or ""}


def drop_carried_over_item(item_id: int) -> dict:
    """"Don't need" on a carried-over line: soft-removed (removed_by 'carried_dropped'), so it can be undone."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, status FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] != "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
        "WHERE id = ? AND household_id = ?",
        (_CARRIED_DROPPED, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "dropped": True}


def undo_carried_over_decision(item_id: int) -> dict:
    """
    Put a carried-over line back to "waiting for an answer", whichever
    answer it got. A kept line that was merged onto this week's line has
    its amount subtracted back out of that line first (_subtract_quantity,
    the same inverse a swapped meal uses); a kept line restored on its own
    goes back to 'carried' with its old plan as its source again; a
    dropped line simply comes back. A row that was never carried over is
    left alone.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, removed_by, carried_from_plan_id FROM grocery_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["carried_from_plan_id"] is None or row["status"] == "carried":
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    if row["status"] == "removed" and row["removed_by"] == _CARRIED_KEPT:
        # The kept amount has to come back OFF this week's line before the
        # question can be asked again — otherwise a second Keep counts it
        # twice. Two cases where it can't: the line has gone (bought, in a
        # cart, removed), or the merge can't be read back apart ("1 bag"
        # kept onto "1 bag (2 lb)" is "2 bags (2 lb)", and _subtract_quantity
        # has no bag to take off it). Then the honest answer is "too late",
        # not a reopened question (verifier, 2026-09-13 — reproduced both).
        target = _this_weeks_line(conn, row["item"], row["quantity"] or "")
        if target is None or target["status"] != "needed":
            conn.close()
            return {"item_id": item_id, "item": row["item"], "unchanged": True, "reason": "acted_on"}
        new_qty, fully_removed = _subtract_quantity(target["quantity"] or "", row["quantity"] or "")
        if not fully_removed and new_qty == (target["quantity"] or ""):
            conn.close()
            return {"item_id": item_id, "item": row["item"], "unchanged": True, "reason": "acted_on"}
        # Fully removed means the line held nothing but the kept amount
        # (this week's line was blank), so blank is what it goes back to.
        conn.execute(
            "UPDATE grocery_items SET quantity = ? WHERE id = ? AND household_id = ?",
            ("" if fully_removed else new_qty, target["id"], household_id()),
        )
    elif row["status"] not in ("removed", "needed"):
        # Bought or in a cart since: the decision has been acted on.
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    conn.execute(
        "UPDATE grocery_items SET status = 'carried', removed_by = '', removed_at = NULL, "
        "source_weekly_plan_id = carried_from_plan_id WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "status": "carried"}


# ---------- "I'll use something else instead" ----------
# Loop Board, 2026-09-13 (Emily: "if we want to use an alternative that
# should be a spot we can put it here too, for example, instead of fresh
# oregano I'll use dry oregano"). A per-week swap, made while sorting the
# list: the line becomes the alternative — or comes off, when the
# alternative is already in the house — and the recipe's ingredient line
# says "using dry oregano instead" when cooking (cooker.get_cooker_view).
# Never a recipe edit, never an inventory record. The "have it already"
# half of the same ticket is pre_shop.drop_grocery_item_pre_shop: a
# soft-remove with an undo, listed on the wrap-up, and nothing written to
# inventory (policy 2026-09-01 — nobody does inventory work to finish the
# loop).


def substitute_grocery_item(item_id: int, alternative: str, at_home: bool = False, author: str = "") -> dict:
    """
    Swap a grocery line for something else this week. The line is renamed
    to `alternative` (its quantity kept as written — nobody can convert
    fresh oregano into dry, so the number stays for the person to adjust)
    and picks up the alternative's usual store if the line had none. With
    `at_home`, the alternative is already in the house, so the line is
    soft-removed as well (the same removal "have it already" makes —
    reversible, and listed on the wrap-up). Either way the swap is recorded
    against the week, so the recipe can say so when it is cooked. Raises
    ValueError for an unknown line or a blank alternative.
    """
    alternative = " ".join((alternative or "").strip().split())
    if not alternative:
        raise ValueError("Say what to use instead.")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, status, store, source_weekly_plan_id FROM grocery_items "
        "WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    if row["status"] not in ("needed", "spice"):
        conn.close()
        return {"item_id": item_id, "item": row["item"], "unchanged": True}
    plan_id = row["source_weekly_plan_id"]
    if plan_id is None:
        current = _weekly_plan._current_weekly_plan_row(conn)
        plan_id = current["id"] if current else None
    conn.execute(
        "INSERT INTO grocery_substitutions (household_id, grocery_item_id, weekly_plan_id, original_item, alternative, at_home) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (household_id(), item_id, plan_id, row["item"], alternative, 1 if at_home else 0),
    )
    prefs = conn.execute(
        "SELECT item, store FROM item_store_preferences WHERE household_id = ?", (household_id(),),
    ).fetchall()
    pref = next((p for p in prefs if _merge_key(p["item"]) == _merge_key(alternative)), None)
    store = row["store"] or (pref["store"] if pref else "")
    if at_home:
        conn.execute(
            "UPDATE grocery_items SET item = ?, store = ?, status = 'removed', removed_by = ?, "
            "removed_at = datetime('now') WHERE id = ? AND household_id = ?",
            (alternative, store, acting_name(author) or "", item_id, household_id()),
        )
    else:
        # A spice waiting unticked becomes a needed line: choosing what
        # to buy instead is choosing to buy it.
        conn.execute(
            "UPDATE grocery_items SET item = ?, store = ?, status = 'needed' WHERE id = ? AND household_id = ?",
            (alternative, store, item_id, household_id()),
        )
    conn.commit()
    conn.close()
    return {
        "item_id": item_id, "item": alternative, "original_item": row["item"],
        "at_home": bool(at_home), "status": "removed" if at_home else "needed",
    }


def undo_substitution(item_id: int) -> dict:
    """Put a substituted line back under its original name (and back on the list, if the swap took it off)."""
    conn = get_conn()
    sub = conn.execute(
        "SELECT id, original_item, at_home FROM grocery_substitutions WHERE household_id = ? AND grocery_item_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (household_id(), item_id),
    ).fetchone()
    if sub is None:
        conn.close()
        return {"item_id": item_id, "unchanged": True}
    row = conn.execute(
        "SELECT status FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id()),
    ).fetchone()
    if row is not None:
        if sub["at_home"] and row["status"] == "removed":
            conn.execute(
                "UPDATE grocery_items SET item = ?, status = 'needed', removed_by = '', removed_at = NULL "
                "WHERE id = ? AND household_id = ?",
                (sub["original_item"], item_id, household_id()),
            )
        else:
            conn.execute(
                "UPDATE grocery_items SET item = ? WHERE id = ? AND household_id = ?",
                (sub["original_item"], item_id, household_id()),
            )
    conn.execute("DELETE FROM grocery_substitutions WHERE id = ?", (sub["id"],))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": sub["original_item"], "status": "needed"}


def substitutions_for_plan(plan_id: int | None) -> list[dict]:
    """
    This week's swaps, for the cook view: [{original_item, alternative,
    at_home}]. Only swaps made against THIS plan, and only while the
    grocery line they were made on still exists — a line the ledger
    deleted (both meals that wanted it swapped out, the week cleared) takes
    its swap with it, so a fresh need for the same thing later in the week
    is not told it was substituted (verifier, 2026-09-13 — reproduced). A
    swap made with no plan at all annotates nothing: there is no card for
    it, and matching every later week would make it permanent.
    """
    if plan_id is None:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.original_item, s.alternative, s.at_home FROM grocery_substitutions s "
        "WHERE s.household_id = ? AND s.weekly_plan_id = ? "
        "AND EXISTS (SELECT 1 FROM grocery_items g WHERE g.id = s.grocery_item_id) ORDER BY s.id",
        (household_id(), plan_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_grocery_list(status: str = "needed") -> dict:
    """
    Remove ALL items with the given status (default 'needed') in one shot —
    a full reset, not a merge or a staleness check. Use only when the user
    explicitly asks to clear/empty/start the grocery list over (e.g. "wipe
    the list, we're starting fresh"). For routine cleanup use
    consolidate_grocery_list (duplicates) or clear_stale_grocery_items (old
    plan leftovers) instead — this one has no way to know what's still
    actually needed.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?)",
        (household_id(), status, status),
    ).fetchall()
    count = len(rows)
    conn.execute(
        "DELETE FROM grocery_items WHERE household_id = ? AND (? = 'all' OR status = ?)",
        (household_id(), status, status),
    )
    conn.commit()
    conn.close()
    return {"removed_count": count}


RESTORED, ROW_GONE, LEFT_ALONE = "restored", "gone", "left"


def _restore_inventory_from_receipt(conn, receipt_json: str | None) -> str:
    """
    Reverse exactly the inventory write a purchased tick recorded, on the
    caller's connection. Returns one of three answers, because the caller
    treats them differently:

    RESTORED — the kitchen was put back. The receipt
    (grocery_items.inventory_receipt_json) says which row the tick wrote,
    whether it created that row or merged into stock already there, and
    what the row read on either side of the write. The row is reversed
    ONLY if it still reads exactly what the write left it at — proven by
    inventory_items.rev, the per-row write counter its trigger bumps on
    every update. Not updated_at: datetime('now') is whole-second, so "set
    to 8, set back to 12" inside the tick's own second read as untouched
    (reproduced 2026-09-13); rev counts both. Then: a row the tick created
    is deleted (it exists only because of the tick, the same call
    undo_pre_shop_drop makes on already_have_inventory_id); a merged row
    gets its quantity, source, category and expiry put back to the
    recorded before-state, not to "now minus what we added" — the
    before-state is known, so nothing is computed.

    ROW_GONE — the row the tick wrote no longer exists (deleted from the
    Kitchen screen, or used down to zero, which deletes). Nothing to put
    back, and nothing left to double onto either, so the caller clears the
    stamp: a re-tick is a first tick again. The alternative — keeping the
    stamp — locks a line out of the kitchen for good, silently, the moment
    somebody tidies the kitchen by hand before fixing the list; the cost of
    clearing is one contrived sequence (eat all twelve, THEN untick, then
    re-tick) that puts twelve back. Certainty about a row that is gone is a
    different thing from certainty about a row that was merely edited.

    LEFT_ALONE — the row is there but has been written since (used from,
    edited, nudged, moved, re-set), or there is no receipt at all (a line
    bought before the column existed). Nothing is guessed: the row stays,
    and so does the stamp, so the re-tick adds nothing on top of what is
    already there. Never subtracts, never deletes on a guess: the
    household's later edits are the truer record of the shelf.
    """
    if not receipt_json:
        return LEFT_ALONE
    try:
        receipt = json.loads(receipt_json)
    except (TypeError, ValueError):
        return LEFT_ALONE
    inventory_id = receipt.get("inventory_id")
    after = receipt.get("after") or {}
    if not inventory_id or not after:
        return LEFT_ALONE
    current = _inventory._receipt_snapshot(conn, inventory_id)
    if current is None:
        return ROW_GONE
    if current.get("rev") != after.get("rev") or current.get("quantity") != after.get("quantity"):
        return LEFT_ALONE
    if receipt.get("fresh"):
        conn.execute(
            "DELETE FROM inventory_items WHERE id = ? AND household_id = ?", (inventory_id, household_id())
        )
        return RESTORED
    before = receipt.get("before") or {}
    if not before:
        return LEFT_ALONE
    conn.execute(
        "UPDATE inventory_items SET quantity = ?, source = ?, category = ?, expiration_date = ?, "
        "updated_at = datetime('now') WHERE id = ? AND household_id = ?",
        (
            before.get("quantity") if before.get("quantity") is not None else "",
            before.get("source") or "chat",
            before.get("category") or "other",
            before.get("expiration_date"),
            inventory_id,
            household_id(),
        ),
    )
    return RESTORED


def mark_grocery_item(item_id: int, status: str = "purchased") -> dict:
    """
    Update a grocery item's status (needed/in_cart/purchased). Marking
    something purchased also adds it to tracked pantry/fridge inventory
    automatically (source='grocery_checkoff'), with expiration left unset —
    see update_inventory/get_inventory. That add happens ONCE per line
    however many times it is ticked, un-ticked and ticked again; moving a
    purchased line back off 'purchased' takes it back OUT of the kitchen
    when the kitchen row is still exactly as the tick left it, and leaves
    the kitchen alone otherwise (the result says which:
    inventory_added / inventory_restored). See _restore_inventory_from_
    receipt for the argument.
    """
    conn = get_conn()
    added = None
    restored = None
    try:
        # One BEGIN IMMEDIATE, so the write lock is held from the first read
        # (the shape cooker._claim_inventory_depletion and
        # weekly_plan._replace_slot_entries use, for the same reason): the
        # status route is a sync def in a threadpool, and two 'purchased'
        # posts for one line at the same instant both read the old status
        # otherwise — 20/20 concurrent pairs doubled before this.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT item, quantity, category, status, staple_id, inventory_added_at, inventory_receipt_json "
            "FROM grocery_items WHERE id = ? AND household_id = ?",
            (item_id, household_id()),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError(f"No grocery list item with id {item_id}.")
        # Idempotent on purpose (grocery offline, 2026-09-11): a phone in a
        # store with one bar can send the same status twice — the request
        # got through but the reply didn't, so the queue in
        # static/grocery-offline.js sends it again, and "Done at <store>"
        # tapped twice after a "try again" does the same. Setting an
        # unchanged status is a no-op. This guard is necessary and NOT
        # sufficient: purchased -> needed -> purchased changes the status
        # each time, which is why the inventory write is keyed on
        # inventory_added_at below and not on this comparison.
        if row["status"] == status:
            conn.rollback()
            return {"item_id": item_id, "status": status, "unchanged": True}
        fields = "status = ?"
        params: list = [status]
        if status == "purchased":
            if row["inventory_added_at"] is None:
                receipt = _inventory._add_to_inventory(
                    row["item"], row["quantity"] or "", source="grocery_checkoff", category=row["category"], conn=conn
                )
                fields += ", inventory_added_at = datetime('now'), inventory_receipt_json = ?"
                params.append(json.dumps({
                    "inventory_id": receipt["item_id"],
                    "fresh": receipt["fresh"],
                    "before": receipt["before"],
                    "after": receipt["after"],
                }))
                added = True
            else:
                # Already in the kitchen from an earlier tick on this line
                # and never taken back out — the status moves, the shelf
                # does not.
                added = False
        elif row["status"] == "purchased" or row["inventory_added_at"] is not None:
            # Leaving 'purchased' (or a line whose earlier untick could not
            # restore): try the exact reversal. The stamp clears only when
            # the shelf really was put back, so a later re-tick is a first
            # tick again; when it was not, the stamp stays and the re-tick
            # adds nothing on top of what is already there. A row that is
            # GONE also clears the stamp (nothing left to double onto — see
            # the helper). A line bought before the stamp existed has no
            # receipt, so this reports inventory_restored: False rather than
            # staying quiet.
            outcome = _restore_inventory_from_receipt(conn, row["inventory_receipt_json"])
            restored = outcome == RESTORED
            if outcome in (RESTORED, ROW_GONE):
                fields += ", inventory_added_at = NULL, inventory_receipt_json = NULL"
        conn.execute(
            f"UPDATE grocery_items SET {fields} WHERE id = ? AND household_id = ?",
            (*params, item_id, household_id()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    result: dict = {"item_id": item_id, "status": status}
    if added is not None:
        result["inventory_added"] = added
    if restored is not None:
        result["inventory_restored"] = restored
    # Imported here, not at the top: staples.py imports this module for the
    # merge key. Both calls run after the commit above, on their own
    # connection.
    from . import staples as _staples

    if status == "purchased":
        # A bought staple teaches its rhythm, whoever put the line there —
        # a hand-added "coffee" counts the same as the suggestion Pomona
        # made. No-op for anything that isn't a staple — except a spice,
        # which becomes one (the spice rack, kept from purchases; see
        # staples.py's note on sections) — and one bought date per day per
        # staple, so a re-tick teaches nothing twice. The event remembers
        # this line as its creator, so the untick below can take it back.
        _staples.record_staple_purchase(
            row["item"], source="grocery", staple_id=row["staple_id"],
            grocery_item_id=item_id, category=row["category"],
        )
    elif row["status"] == "purchased":
        # Un-ticked: the purchase this line taught did not happen. Takes
        # back today's bought event only when this line created it and
        # nothing else bought the same thing today — see
        # staples.unrecord_staple_purchase. Earlier days are never touched.
        _staples.unrecord_staple_purchase(row["item"], staple_id=row["staple_id"], grocery_item_id=item_id)
    return result


def update_grocery_item(item_id: int, quantity: str | None = None, category: str | None = None) -> dict:
    """
    Directly edit an already-listed grocery item's quantity and/or category
    by id — for correcting something already on the list (wrong amount,
    miscategorized) rather than adding a new line. Unlike add_grocery_item,
    this never merges/consolidates with another row since it's already
    targeting one specific, known item. Leave a field as None to leave it
    unchanged.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    new_quantity = quantity if quantity is not None else row["quantity"]
    new_category = category if category is not None else row["category"]
    conn.execute(
        "UPDATE grocery_items SET quantity = ?, category = ? WHERE id = ?",
        (new_quantity, new_category, item_id),
    )
    conn.commit()
    conn.close()
    return {"item_id": item_id, "item": row["item"], "quantity": new_quantity, "category": new_category, "found": True}


def remove_grocery_item(item_id: int) -> dict:
    """
    Take an item off the grocery list. A hard delete — except for a
    still-needed line a staple put there, which is soft-removed and counts
    as "not this trip" (see below); a bought staple line is deleted like
    anything else.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    row = conn.execute(
        "SELECT id, staple_id, status FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    if row is not None and row["staple_id"] and row["status"] == "needed":
        # Removing a staple's suggestion is "not this trip" — otherwise the
        # next list read would put it straight back. Soft-removed rather
        # than deleted, so the staple's own Undo can restore it.
        from . import staples as _staples
        _staples.note_line_removed(conn, row, how="skip")
        conn.execute(
            "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
            "WHERE id = ? AND household_id = ? AND status != 'removed'",
            (_staples.ADDED_BY_STAPLE, item_id, household_id()),
        )
        conn.commit()
        conn.close()
        return {"item_id": item_id, "deleted": True, "staple_id": row["staple_id"]}
    conn.execute("DELETE FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id()))
    conn.commit()
    conn.close()
    return {"item_id": item_id, "deleted": True}


def move_grocery_item_to_inventory(item_id: int) -> dict:
    """
    For a grocery list item the household realizes they already have on
    hand (turns out there's already a box in the pantry, a bag in the
    freezer, etc.) — not the get_grocery_already_have_items cross-reference
    case, which only catches items inventory already happens to know
    about, but the "oh wait, I actually have this" moment on any item,
    known to inventory or not. Adds it straight to pantry/fridge inventory
    (merging into a matching existing row the same way _add_to_inventory
    always does) carrying over its grocery-list quantity and category, then
    soft-removes it from the grocery list (status='removed',
    removed_by='already_have') — same soft-delete philosophy as
    drop_grocery_item_pre_shop, rather than a hard delete, so the Review
    screen's confirmation section (get_already_have_decisions) can list
    and undo the decision with undo_pre_shop_drop like any other pre-shop
    drop. If undo_pre_shop_drop restores this item, it also needs to
    decide what to do with the inventory row just written: when there was
    no existing stock to merge into (a brand-new inventory_items row), it's
    unambiguously safe to delete on undo, so that row's id is recorded on
    already_have_inventory_id below; when it merged into existing stock,
    reversing it would need the pre-merge quantity, which nothing tracks
    (deliberately — see the "inventory is deferred as policy" roadmap
    decision), so that case is left for undo to leave inventory alone.
    Raises ValueError if the item isn't found.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No grocery list item with id {item_id}.")
    # Same match _add_to_inventory itself will use (no location passed
    # below) — checked first so we know, after the fact, whether its write
    # was a fresh insert or a merge into this pre-existing row.
    pre_existing = conn.execute(
        "SELECT id FROM inventory_items WHERE household_id = ? AND LOWER(item) = LOWER(?)",
        (household_id(), row["item"]),
    ).fetchone()
    conn.close()

    inventory_result = _inventory._add_to_inventory(
        row["item"],
        row["quantity"] or "",
        source="grocery_list_already_have",
        category=row["category"] or None,
    )
    fresh_inventory_id = None if pre_existing else inventory_result.get("item_id")

    conn = get_conn()
    conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = 'already_have', "
        "removed_at = datetime('now'), already_have_inventory_id = ? "
        "WHERE id = ? AND household_id = ?",
        (fresh_inventory_id, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    return {
        "item_id": item_id,
        "item": row["item"],
        "moved_to_inventory": True,
        "inventory_item_id": inventory_result.get("item_id"),
    }
