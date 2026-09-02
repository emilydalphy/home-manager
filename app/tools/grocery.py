"""
The grocery list: adding, merging, marking, clearing and repairing items.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id
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
    """
    existing_qty = _quantities._strip_prep_descriptor((existing_qty or "").strip())
    new_qty = _quantities._strip_prep_descriptor((new_qty or "").strip())
    if not existing_qty:
        return new_qty, True
    if not new_qty:
        return existing_qty, True
    existing_parsed = _quantities._parse_quantity(existing_qty)
    new_parsed = _quantities._parse_quantity(new_qty)
    if existing_parsed and new_parsed and existing_parsed[1] == new_parsed[1]:
        return _quantities._humanize_grocery_quantity(existing_parsed[0] + new_parsed[0], existing_parsed[1]), True
    return f"{existing_qty} + {new_qty}", False


def _subtract_quantity(current_qty: str, remove_qty: str) -> tuple[str, bool]:
    """
    The inverse of _try_consolidate_quantity — used when a meal that
    contributed some amount to a grocery line is being un-planned (see
    _reverse_meal_grocery_contributions) and that amount needs to come back
    out. Returns (resulting_quantity_string, fully_removed). When both sides
    parse with the same unit, subtracts normally, treating a non-positive
    remainder as "nothing left" (fully_removed=True, resulting string
    blank). When they can't be reconciled (freeform text, mismatched units)
    but the two strings are identical, that means this contribution *is*
    the whole line (nothing else merged into it), so it's still safe to
    remove entirely. Otherwise nothing is guessed — the line is left
    exactly as-is (fully_removed=False, resulting string unchanged) rather
    than risk deleting an amount still needed for something else.
    """
    current_qty = _quantities._strip_prep_descriptor((current_qty or "").strip())
    remove_qty = _quantities._strip_prep_descriptor((remove_qty or "").strip())
    if not current_qty:
        return "", True
    if not remove_qty or current_qty == remove_qty:
        return "", True
    current_parsed = _quantities._parse_quantity(current_qty)
    remove_parsed = _quantities._parse_quantity(remove_qty)
    if current_parsed and remove_parsed and current_parsed[1] == remove_parsed[1]:
        remainder = current_parsed[0] - remove_parsed[0]
        if remainder <= 0.0001:
            return "", True
        return _quantities._humanize_grocery_quantity(remainder, current_parsed[1]), False
    return current_qty, False


def _reverse_meal_grocery_contributions(entry_id: int) -> dict:
    """
    Undo whatever a meal_plan_entries row added to the grocery list, via the
    meal_plan_grocery_links ledger recorded at plan_meal() time — called
    right before that entry is deleted (see swap_meal_in_plan/
    swap_component_in_plan) so changing a planned meal actually replaces its
    ingredients on the grocery list instead of only ever piling the new
    meal's ingredients on top of the old ones. For each linked grocery line,
    subtracts back out exactly the amount this meal contributed (see
    _subtract_quantity) — removing the line entirely if nothing's left,
    trimming it if something is, or leaving it untouched if the amounts
    can't be safely reconciled. A line already moved to in_cart/purchased is
    left alone regardless — the shopper has already acted on it, so this
    won't yank something out of a cart mid-trip. Always clears the ledger
    rows for this entry afterward, whether or not anything was adjusted.
    """
    conn = get_conn()
    links = conn.execute(
        "SELECT id, grocery_item_id, item, quantity FROM meal_plan_grocery_links "
        "WHERE household_id = ? AND meal_plan_entry_id = ?",
        (household_id(), entry_id),
    ).fetchall()
    removed_items = []
    trimmed_items = []
    for link in links:
        grocery_row = conn.execute(
            "SELECT id, item, quantity, status FROM grocery_items WHERE id = ? AND household_id = ?",
            (link["grocery_item_id"], household_id()),
        ).fetchone()
        if grocery_row and grocery_row["status"] == "needed":
            new_qty, fully_removed = _subtract_quantity(grocery_row["quantity"] or "", link["quantity"] or "")
            if fully_removed:
                conn.execute("DELETE FROM grocery_items WHERE id = ?", (grocery_row["id"],))
                removed_items.append(grocery_row["item"])
            elif new_qty != (grocery_row["quantity"] or ""):
                conn.execute("UPDATE grocery_items SET quantity = ? WHERE id = ?", (new_qty, grocery_row["id"]))
                trimmed_items.append(grocery_row["item"])
    conn.execute("DELETE FROM meal_plan_grocery_links WHERE household_id = ? AND meal_plan_entry_id = ?", (household_id(), entry_id))
    conn.commit()
    conn.close()
    return {"removed_items": removed_items, "trimmed_items": trimmed_items}


def add_grocery_item(
    item: str,
    quantity: str = "",
    category: str = "other",
    added_by: str = "user",
    source_weekly_plan_id: int | None = None,
) -> dict:
    """
    Add an item to the grocery list. If an item with the same name is
    already on the list (status 'needed'), the quantity is consolidated
    into that single line — e.g. "2 cups flour" + "1 cup flour" becomes
    "3 cups flour" — instead of creating a duplicate line. If the
    quantities can't be reconciled (different, incompatible units), both
    are kept together on the one line rather than silently guessing a
    conversion. category should be one of: produce, dairy, meat/seafood,
    pantry, frozen, other — pick the one that actually matches the item so
    the list stays organized by store section. Leave source_weekly_plan_id
    unset for anything a person asked for directly (or an ad hoc one-off
    meal) — it marks the item as a standing want that should never be
    auto-cleared. It's set automatically when ingredients come from a
    generated weekly plan (see plan_meal/generate_weekly_plan), so
    clear_stale_grocery_items can tell a current week's ingredients apart
    from an old week's leftovers.
    """
    quantity = _quantities._normalize_grocery_quantity(quantity or "")
    conn = get_conn()
    # Compared in Python rather than SQL because the comparison is a
    # normalised key, not a column value. The 'needed' list is a shopping
    # list -- tens of rows -- so reading it to find one match is cheaper
    # than it looks and far clearer than encoding the rule in SQL.
    candidates = conn.execute(
        "SELECT id, item, quantity, source_weekly_plan_id FROM grocery_items "
        "WHERE household_id = ? AND status = 'needed' ORDER BY id",
        (household_id(),),
    ).fetchall()
    wanted = _merge_key(item)
    existing = next((r for r in candidates if _merge_key(r["item"]) == wanted), None)
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
    if existing:
        merged_qty, merged = _try_consolidate_quantity(existing["quantity"] or "", quantity)
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
        conn.commit()
        item_id = existing["id"]
        # The name already on the list, not the one just asked for: the
        # row keeps its own wording, so saying "item" back means the line
        # the shopper will actually see.
        item_name = existing["item"]
        conn.close()
        return {"item_id": item_id, "item": item_name, "quantity": merged_qty, "merged": True, "units_reconciled": merged}

    cur = conn.execute(
        "INSERT INTO grocery_items (household_id, item, quantity, category, added_by, source_weekly_plan_id, store) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (household_id(), item, quantity, category, added_by, source_weekly_plan_id, preferred_store),
    )
    conn.commit()
    item_id = cur.lastrowid
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
    'excluded' (only items hidden via exclude_grocery_item). For 'needed'/
    'in_cart'/'purchased', items excluded from the list (see
    exclude_grocery_item) are left out automatically — they're still
    tracked, just not shown on the normal shopping list.
    """
    conn = get_conn()
    if status == "excluded":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
            "WHERE household_id = ? AND excluded_from_list = 1 ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    elif status == "all":
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
            "WHERE household_id = ? ORDER BY category, item",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item, quantity, category, status, store, excluded_from_list, already_have_reviewed, added_by FROM grocery_items "
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
            merged_qty, _ = _try_consolidate_quantity(merged_qty, extra["quantity"] or "")
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
    """
    current_id = current_weekly_plan_id
    if current_id is None:
        current_id = _weekly_plan.get_weekly_plan().get("weekly_plan_id")
    conn = get_conn()
    if current_id is None:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status = 'needed' "
            "AND source_weekly_plan_id IS NOT NULL",
            (household_id(),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, item FROM grocery_items WHERE household_id = ? AND status = 'needed' "
            "AND source_weekly_plan_id IS NOT NULL AND source_weekly_plan_id != ?",
            (household_id(), current_id),
        ).fetchall()
    removed = [r["item"] for r in rows]
    if rows:
        conn.executemany("DELETE FROM grocery_items WHERE id = ?", [(r["id"],) for r in rows])
        conn.commit()
    conn.close()
    return {"removed_count": len(removed), "removed_items": removed}


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


def mark_grocery_item(item_id: int, status: str = "purchased") -> dict:
    """
    Update a grocery item's status (needed/in_cart/purchased). Marking
    something purchased also adds it to tracked pantry/fridge inventory
    automatically (source='grocery_checkoff'), with expiration left unset —
    see update_inventory/get_inventory.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?", (item_id, household_id())
    ).fetchone()
    conn.execute(
        "UPDATE grocery_items SET status = ? WHERE id = ? AND household_id = ?",
        (status, item_id, household_id()),
    )
    conn.commit()
    conn.close()
    if status == "purchased" and row:
        _inventory._add_to_inventory(row["item"], row["quantity"] or "", source="grocery_checkoff", category=row["category"])
    return {"item_id": item_id, "status": status}


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
        return {"item_id": item_id, "found": False}
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
    """Delete an item from the grocery list."""
    conn = get_conn()
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
    removes it from the grocery list — no separate manual inventory entry
    needed. Raises ValueError if the item isn't found.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT item, quantity, category FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No grocery list item with id {item_id}.")

    inventory_result = _inventory._add_to_inventory(
        row["item"],
        row["quantity"] or "",
        source="grocery_list_already_have",
        category=row["category"] or None,
    )
    remove_grocery_item(item_id)
    return {
        "item_id": item_id,
        "item": row["item"],
        "moved_to_inventory": True,
        "inventory_item_id": inventory_result.get("item_id"),
    }
