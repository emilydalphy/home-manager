"""
The pre-shop check -- what the household may already have before shopping,
and the wording used to ask about it.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from ..db import get_conn
from ._shared import acting_name, household_id, require_household_row
from . import cooker as _cooker
from . import defrost as _defrost
from . import grocery as _grocery
from . import inventory as _inventory
from . import quantities as _quantities
from . import recipes as _recipes

logger = logging.getLogger("home_manager")


# ---------- was the check right? the accuracy ledger ----------
#
# Emily, 2026-09-22: "track the number of times people say they actually
# want to keep it on vs they do have it ... so that we can measure the
# accuracy." Until this, only the DROP was countable — it writes status,
# removed_by and removed_at. A keep set already_have_reviewed = 1 and
# nothing else: no timestamp, so keeps could not be counted over a window,
# and no record that a flag had ever been RAISED, so a card nobody
# answered looked exactly like one that was never shown. A rate with no
# denominator is not a rate.
#
# See schema.sql on pre_shop_decisions for the shape and why it is a table
# rather than columns on the grocery line.

KEPT, KEPT_ALL, DROPPED, UNDONE = "kept", "kept_all", "dropped", "undone"


def _record_flags_raised(item_ids: list[int]) -> None:
    """
    Remember that the card ASKED about these lines — once per line, ever.

    WHAT "FLAGGED" HONESTLY MEANS, because it is the denominator and a
    denominator that drifts makes the whole number a lie: one grocery line
    that the "Maybe already home" card held off the shopping list, counted
    the FIRST time it was held. get_pre_shop_flags is computed on read and
    runs on every Shop-tab load and on every /api/grocery-list read that
    has to exclude the flagged rows, so counting a raise per read would
    count one question a dozen times and flatter the accuracy rate by as
    much. INSERT OR IGNORE against the UNIQUE (household, line) is what
    makes the re-reads free.

    It also means "flagged" counts questions ASKED, not questions ANSWERED
    — which is the point: a line raised and never answered is a card the
    household walked past, and it should be visible as the gap between
    flagged and the three answers rather than disappear.

    Never raises. This is bookkeeping wrapped around a real read; a locked
    database must cost a statistic, not the shopping list. (Same rule
    usage.record_chat_turn follows, for the same reason.)
    """
    if not item_ids:
        return
    conn = None
    try:
        conn = get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO pre_shop_decisions (household_id, grocery_item_id) VALUES (?, ?)",
            [(household_id(), int(i)) for i in item_ids],
        )
        conn.commit()
    except Exception:
        logger.exception("Recording pre-shop flags raised failed")
    finally:
        if conn is not None:
            conn.close()


def _record_decision(conn, item_id: int, decision: str, *, only_when: str | None = None) -> None:
    """
    Record which way one raised flag went. Takes the caller's OPEN
    connection and does not commit: the decision and the thing it decided
    have to land together or not at all, and a nested get_conn inside an
    open one is how this repo has twice earned an intermittent "database
    is locked".

    ONLY EVER UPDATES A ROW THAT IS ALREADY THERE, which is the whole
    honesty of the count. Both write paths this hangs off are shared with
    flows that are not the pre-shop card at all: the Shop tab's "Wait, I
    already have this" on any list row posts the same drop, and
    mark_grocery_item_already_have_reviewed is also a chat tool and the
    older "Already have this?" section's confirm button. A decision on a
    line the card never raised is not an answer to a question it never
    asked, and counting it would mean kept + dropped could exceed flagged.

    `only_when` narrows it further to a line currently in one state —
    used by the undo, so that cancelling the freezer step's set-aside
    (defrost.py calls the same drop and the same undo) is not filed as the
    household catching a wrong flag.
    """
    try:
        sql = (
            "UPDATE pre_shop_decisions SET decision = ?, decided_at = datetime('now') "
            "WHERE household_id = ? AND grocery_item_id = ?"
        )
        params: list = [decision, household_id(), int(item_id)]
        if only_when is not None:
            sql += " AND decision = ?"
            params.append(only_when)
        conn.execute(sql, params)
    except Exception:
        logger.exception("Recording a pre-shop decision failed")


def _kitchen_stock() -> "_recipes._KitchenStock":
    """
    One reading of the kitchen for one pass over the list.

    The same class the grocery ingest asks — deliberately the same class
    and not a second copy of the arithmetic, because two implementations
    of "is there enough of this at home" are exactly the bug this closes:
    the approval would restore a line and the pre-shop check would divert
    it again. See recipes._KitchenStock for the rule and its fail-safe
    direction.

    A fresh instance per call, never one shared between the two readers
    below: _KitchenStock keeps a claim ledger, and a ledger carried from
    one question into another would have the second answer depend on
    whether the first had been asked.
    """
    conn = get_conn()
    try:
        return _recipes._KitchenStock(conn)
    finally:
        conn.close()


def _pre_shop_parse_total(raw_qty: str) -> tuple[float, str | None] | None:
    """
    A quantity string read as ONE (amount, unit), or None when it can't
    be: freeform wording ("a bunch", "to taste"), or an unreconciled
    "X + Y" whose pieces are written in different units.

    This is the amount half of _pre_shop_humanize_label, pulled out so the
    number the coverage check compares and the phrase the sentence prints
    come from the same read of the same string — the card must never say
    "You want 2 lbs" about a figure it compared as something else.
    """
    raw = (raw_qty or "").strip()
    if not raw:
        return None
    pieces = [p.strip() for p in raw.split(" + ") if p.strip()]
    parsed = [_quantities._parse_quantity(p) for p in pieces]
    if any(p is None for p in parsed):
        return None
    units = {p[1] for p in parsed}
    if len(units) > 1:
        return None
    return sum(p[0] for p in parsed), parsed[0][1]


def _pre_shop_on_hand(stock, match: dict) -> tuple[str | None, int, float, str | None]:
    """
    What is on hand for a matched row, said as the card should say it:
    (label, how many rows it came from, the total, the unit).

    ONE ROW IS NOT THE ANSWER, because _KitchenStock sums every row of a
    name and the decision is made on that sum, while
    cooker._find_inventory_match hands back a single row. A household
    keeping a pound of broccoli in the fridge and a pound and a half in
    the pantry has two and a half pounds; printing whichever row came
    back first gives "You want 2 lbs. Fridge shows 1 lb." over a line the
    card has just hidden — this module's own bug, arriving from the side
    it was not fixed on, and arbitrary besides (swap the rows and it
    reads "a lb and a half"). So the printed figure is the compared
    figure. (0, ...) means there is nothing sayable, and the caller does
    not flag.

    Rendered in the unit the MATCHED row was written in, so a household
    that tracks in pounds is not suddenly told about ounces. Every row of
    the name converts into it whenever covers() would say yes, since
    conversion is within one family.
    """
    parsed = _pre_shop_parse_total(match.get("quantity") or "")
    if parsed is None:
        return None, 0, 0.0, None
    total = stock.on_hand_total(match["item"], parsed[1])
    if total is None:
        return None, 0, 0.0, None
    count, amount = total
    return _pre_shop_amount_words(amount, parsed[1]), count, amount, parsed[1]


def get_grocery_already_have_items() -> list[dict]:
    """
    Cross-reference the 'needed' grocery list against tracked inventory to
    flag items that may not actually need buying — e.g. added ad hoc in
    chat before checking, or left over from before inventory caught up.
    Uses the same confident name-match logic as meal-plan ingredient
    auto-adding (get_inventory's items with a non-blank tracked quantity),
    not a guess. Only returns items not yet reviewed (see
    mark_grocery_item_already_have_reviewed) — once the shopper confirms
    they still need something, it drops out of this list for good (even
    though the inventory match still technically exists) rather than
    nagging about the same item every time. Powers the Grocery List view's
    "Already have this?" review section, which pulls these out of the
    normal To-buy list until reviewed.

    The amount is compared, not merely present — see get_pre_shop_flags,
    whose sibling gate this is. Two ounces of chicken thighs is not an
    answer to a line asking for two pounds, and this function is read back
    to the household in words by the assistant, so a wrong "you already
    have that" here is said out loud.
    """
    needed = _grocery.list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = _inventory.get_inventory()
    stock = _kitchen_stock()
    have_matches = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _cooker._find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        _label, rows, total, unit = _pre_shop_on_hand(stock, match)
        if not stock.covers(match["item"], _pre_shop_parse_total(it["quantity"])):
            continue  # tracked, but not demonstrably enough — see get_pre_shop_flags
        have_matches.append({
            "item_id": it["id"], "item": it["item"], "quantity": it["quantity"], "category": it["category"],
            # The amount that was actually compared. With one row that is
            # the row's own words; with several it is their sum, because
            # this is read back to the household out loud and naming one
            # shelf's worth of a two-shelf food is how "you already have
            # that" stops being true. The shelf goes unnamed for the same
            # reason — see _pre_shop_on_hand.
            "inventory_quantity": (
                match["quantity"] if rows <= 1 else _quantities._format_quantity(total, unit)
            ),
            "inventory_location": match.get("location", "") if rows <= 1 else "",
        })
    return have_matches


def get_pre_shop_flags() -> list[dict]:
    """
    Same confident inventory cross-reference as get_grocery_already_have_items,
    reshaped for the Grocery screen's pinned "Maybe already home" pre-shop
    check (PRE_SHOP_CHECK.md): a humanised, single-sentence comparison per
    item, computed here so the client never touches raw pack quantities —
    see _pre_shop_humanize_label, which is exactly where the old block's
    "1 stick + 1 stick" bug lived. An item whose wanted or on-hand amount
    can't be reduced to one confident phrase, or whose full sentence would
    run past ~60 characters, is left off entirely rather than shown
    garbled (PRE_SHOP_CHECK.md's "if it can't be said in one sentence,
    don't flag the item"). Also read by main.py's _stamp_pre_shop_flags,
    which puts the same sentence on the line's own row in the
    /api/grocery-list and /api/grocery-list/by-store "needed" views — so
    the flag reaches the household while they are sorting, not only from
    the banner at the top (Emily, 2026-09-22).

    THE AMOUNT HAS TO BE COMPARED, and that was true when a flag was far
    more dangerous than it is now. Until 2026-09-23 those two views
    EXCLUDED a flagged id outright, so a flag did not merely annotate a
    line — it took the line off what the household shops from until
    somebody tapped through the card. Until 2026-09-14, on top of that,
    this asked inventory the same question the grocery ingest did, "is
    this name in there with a non-blank quantity?", and threw the quantity
    away; the sentence then rendered both amounts, so the card could read
    "You want 3 lbs. Fridge shows 2 lbs." while holding that line off the
    list. A week shopped normally writes an inventory row per ticked line,
    so the week after it the whole list went behind the card and the Shop
    tab opened empty. Reproduced over HTTP before this was touched.

    The line stays on the list now, so a wrong flag costs a wrong celadon
    block rather than a missing dinner — but it is still read back to the
    household in words, from two places, so the comparison below stays
    exactly as strict.

    An amount is now compared, through recipes._KitchenStock — the same
    class the ingest uses, so the two cannot disagree about one kitchen.
    Everything that cannot be compared stays on the list: a freeform
    wanted amount ("a bunch"), a row nobody can parse, two unit families
    that don't convert. Same bias as the ingest — an extra line beats a
    missing dinner.

    THE STOCK IS CLAIMED AS IT IS GRANTED, one pass over the list. Two
    lines of one food can co-exist (a household's standing want beside a
    plan's line, in units that wouldn't add up — see grocery._merge_target),
    and one dozen eggs on the shelf is an answer to one of them, not to
    both. Claiming makes the second line stay on the list, which is the
    honest answer and the safe one. The cost is that which of the two gets
    the flag depends on the order list_grocery_list returns — by category,
    then item — which need not put the pair together at all, since the two
    lines can be filed under different sections. Stable either way, and
    either answer is defensible since the pair is one food; what matters
    is that only one of them comes off the list.

    The kitchen is asked LAST, after the wording checks, so a line the
    card declines to phrase spends nothing.
    """
    needed = _grocery.list_grocery_list(status="needed")
    if not needed:
        return []
    inventory = _inventory.get_inventory()
    stock = _kitchen_stock()
    flags = []
    for it in needed:
        if it.get("already_have_reviewed"):
            continue
        match, confident = _cooker._find_inventory_match(it["item"], inventory)
        if not match or not confident:
            continue
        if not (match.get("quantity") or "").strip():
            continue  # tracked but with no quantity on hand isn't a confident "we have it"
        wanted_label = _pre_shop_humanize_label(it["quantity"])
        # The amount COMPARED, not the one row that happened to match —
        # see _pre_shop_on_hand. Read-only, so it is safe above covers().
        on_hand_label, on_hand_rows, _t, _u = _pre_shop_on_hand(stock, match)
        if not wanted_label or not on_hand_label:
            continue
        sentence = f"You want {wanted_label}. Fridge shows {on_hand_label}."
        if len(sentence) > 60:
            continue
        # ...and only once the kitchen can be SHOWN to cover the amount.
        # Asked LAST, because covers() spends what it grants: a line left
        # off the card for a wording reason above must not quietly claim
        # stock the next line of the same food could have used.
        #
        # Asked under the MATCHED ROW's name, not the grocery line's.
        # _KitchenStock keys on the plain stripped name while
        # _find_inventory_match's confident test forgives a trailing "s",
        # so keying on the line ("Eggs") would silently stop asking about
        # a row called "Egg" — a narrowing this ticket never asked for.
        # The matched row's own name asks the amount question about
        # exactly the row the sentence is about to name, and picks up any
        # duplicate rows of it (the opened jar in the fridge beside the
        # unopened one in the pantry, which _KitchenStock sums).
        if not stock.covers(match["item"], _pre_shop_parse_total(it["quantity"])):
            continue
        flags.append({
            "itemId": it["id"],
            "name": it["item"],
            "wantedLabel": wanted_label,
            "onHandLabel": on_hand_label,
            # One shelf is only named when one shelf is the whole of it;
            # a total spread over two of them belongs to neither.
            "onHandLocation": (match.get("location") or None) if on_hand_rows <= 1 else None,
            "sentence": sentence,
        })
    # Every line above is one the card is about to hold off the shopping
    # list, which is the honest moment to count the question as asked —
    # see _record_flags_raised for why it is stamped once and not per read.
    _record_flags_raised([f["itemId"] for f in flags])
    return flags


def drop_grocery_item_pre_shop(item_id: int, author: str = "") -> dict:
    """
    'Drop it' on a pre-shop flag — soft-removes the item (status:
    'removed') rather than deleting it outright, so undo_pre_shop_drop can
    restore it and so a pattern of repeat drops stays around for the
    assistant to learn from later (same reasoning as
    exclude_grocery_item/move_grocery_item_to_inventory's soft-delete
    philosophy — see DATA_AND_API.md's "Sync between the two adults").
    Idempotent: dropping an already-removed item is a no-op. `author` is
    who dropped it: the adult picked on this device when the caller did not
    name one (the shell sends the generic "user"; see _shared.acting_name),
    otherwise the name given. Recorded, and not yet driving a live
    cross-device "the other adult changed something" notification.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    # A staple's line dropped here means "we already have it" — say so to the
    # staple, or the next list read puts the line straight back.
    _staple_row = conn.execute(
        "SELECT id, staple_id FROM grocery_items WHERE id = ? AND household_id = ? AND status != 'removed'",
        (item_id, household_id()),
    ).fetchone()
    if _staple_row is not None and _staple_row["staple_id"]:
        from . import staples as _staples
        _staples.note_line_removed(conn, _staple_row, how="plenty")
    who = acting_name(author) or ""
    changed = conn.execute(
        "UPDATE grocery_items SET status = 'removed', removed_by = ?, removed_at = datetime('now') "
        "WHERE id = ? AND household_id = ? AND status != 'removed'",
        (who, item_id, household_id()),
    ).rowcount
    # Counted only when this call actually dropped something (the function
    # is idempotent, and a second tap is not a second decision) and only
    # when a person made it: the freezer step calls this route too
    # (defrost.py, author=FREEZER_REMOVED_BY) to set a line aside because
    # it is in the freezer, which is a different question with a different
    # right answer and must not land in the accuracy count. A line the
    # card never raised is ignored inside _record_decision.
    if changed and who != _defrost.FREEZER_REMOVED_BY:
        _record_decision(conn, item_id, DROPPED)
    conn.commit()
    conn.close()
    return {"item_id": item_id, "status": "removed"}


def undo_pre_shop_drop(item_id: int) -> dict:
    """
    Undo a pre-shop 'Drop it' — restores the item to 'needed' and marks it
    already_have_reviewed so it goes straight back to its store card
    without being re-flagged this same trip (PRE_SHOP_CHECK.md: undo
    "does not re-add the flag this trip"). Also reused by the Review
    screen's "already have" confirmation section (see
    get_already_have_decisions) to undo a Have it / Already have action, so
    there's no separate "already-have-undo" endpoint — but that flow wrote
    an inventory row a plain pre-shop drop never did, so this also deletes
    that row when (and only when) it's safe to: already_have_inventory_id
    is set only for a fresh-insert write (see
    grocery_items.already_have_inventory_id / move_grocery_item_to_
    inventory), never for one that merged into pre-existing stock — undoing
    a merge would need the pre-merge quantity, which nothing tracks, so
    that case leaves inventory untouched on undo by design.

    A line the freezer step set aside (removed_by defrost.FREEZER_REMOVED_BY,
    2026-09-21) comes back the same way — and its still-pending defrost
    move goes with it (defrost._release_frozen_item): "Actually, I need
    it" on that line means "it isn't in my freezer after all", and a
    fridge move for food that is being bought fresh is a reminder for
    something no longer true. One write path for the put-back, whichever
    screen asks for it; `moves_cancelled` says how many went.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    row = conn.execute(
        "SELECT item, already_have_inventory_id, staple_id, removed_by, source_weekly_plan_id "
        "FROM grocery_items WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    ).fetchone()
    if row is not None and row["staple_id"]:
        # "Put back on the list" on a staple's line: the staple was wrong to
        # say plenty, so its last answer goes too — otherwise the line is
        # back but the staple still believes the cupboard is full.
        from . import staples as _staples
        _staples.reverse_last_answer(conn, row["staple_id"])
    # removed_by is cleared with the removal (it was not until 2026-09-21):
    # a stale 'freezer' mark on a line back on the list would read as
    # "set aside by the freezer step" to defrost._grocery_lines_by_item the
    # next time anything else soft-removed the line without saying who.
    conn.execute(
        "UPDATE grocery_items SET status = 'needed', already_have_reviewed = 1, removed_by = '', "
        "removed_at = NULL, already_have_inventory_id = NULL WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    # A drop the household took back is a flag that was WRONG, and the one
    # kind of wrong this app can actually observe — so it is counted, in
    # its own bucket rather than left inside 'dropped'. `only_when` keeps
    # it to a line this card actually dropped: this same function is the
    # undo for the freezer set-aside and for a Review-screen "Have it",
    # neither of which recorded a drop to take back.
    _record_decision(conn, item_id, UNDONE, only_when=DROPPED)
    conn.commit()
    conn.close()
    if row and row["already_have_inventory_id"]:
        _inventory.remove_inventory_item(row["already_have_inventory_id"])
    moves_cancelled = 0
    if row and row["removed_by"] == _defrost.FREEZER_REMOVED_BY:
        moves_cancelled = _defrost._release_frozen_item(row["item"], row["source_weekly_plan_id"])
    return {"item_id": item_id, "status": "needed", "moves_cancelled": moves_cancelled}


def _household_day_start_utc(day: date, zone) -> str:
    """
    The UTC instant the household's clock struck midnight on `day`, in the
    shape SQLite stamps removed_at with.

    grocery_items.removed_at is an instant — `datetime('now')`, i.e. UTC,
    from all SEVEN writers of it: five in grocery.py, one here (the
    pre-shop drop), one in staples.py — and the window below is a
    household DAY. Comparing the
    two as strings compares an instant against a date, which reads as
    "midnight UTC", not "midnight where they live". So the day is turned
    into the instant it began at and the comparison is instant against
    instant. The conversion has to happen here rather than in the query
    because SQLite knows UTC and the SERVER's zone, and the household's is
    neither.
    """
    return (
        datetime.combine(day, time(), tzinfo=zone)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def get_already_have_decisions() -> list[dict]:
    """
    Review screen's confirmation section (Loop Board: "Review screen
    should confirm the already have decisions for the week") — every
    grocery item currently soft-removed (status='removed') because the
    household said they already have it, from either flow: a pre-shop
    "Maybe already home" Drop it (removed_by holds who dropped it, e.g.
    'user') or a Have it / Already have action on any Grocery List row
    (removed_by == 'already_have', see move_grocery_item_to_inventory).
    Scoped to the household's CURRENT PLANNING PERIOD (by removed_at) so old
    decisions don't linger here forever the way they would with no cutoff at
    all — nothing today ever clears a 'removed' row's removed_at. Restorable
    with undo_pre_shop_drop regardless of which flow removed it.

    That cutoff used to be "this Monday", which was grocery state quietly
    carrying a plan-shaped assumption (Loop Board "Planning periods, not
    weeks" flagged this one as easy to miss for exactly that reason). For a
    household planning Thursday to Thursday, a Monday cutoff throws away the
    first half of the very shop the Review screen is confirming: they said
    "we already have rice" on Thursday, and by the following Monday that
    decision has silently aged out of a period still running. Falls back to
    this Monday when no plan covers today, which is byte-identical to the
    old behaviour for every household without a plan.

    Both the day and the boundary are the HOUSEHOLD's, which they were not
    until 2026-09-18. The day was `date.today()` — the container's, so a
    period the household is standing in the middle of could fail the
    "covers today" test by one evening and drop the window back to a
    Monday cutoff the docstring above exists to be rid of; and
    _current_weekly_plan_row picked the plan on the household's clock in
    the first place, so the two reads could disagree about the same day.
    The boundary was the cutoff DATE compared against a UTC timestamp,
    which begins the window at midnight UTC: for a Toronto household four
    hours early in EDT and five in EST — the last evening before the
    period, from 20:00 local, 19:00 between November and March — which is
    harmless, and nine hours LATE for a household east of UTC,
    which loses them the first morning of their own period off the Review
    screen with no way to undo a decision they can no longer see. Nobody
    lives east of UTC today; households.timezone is a column anyone can
    set. See _household_day_start_utc for why the conversion is in Python.

    ONE NARROWING FALLS OUT OF THAT AND IT IS EMILY'S TO OVERRULE. A
    decision made the evening BEFORE a period starts is no longer listed,
    and for a household on the default `sunday_before` planning anchor
    that evening is exactly when they approve the week and sort the list
    these decisions come off. Nothing else can take one back: this is the
    only screen that shows a removed row, and undo_pre_shop_drop is not a
    chat tool. The window being the period is the fix; widening it back is
    a product decision, not a clock one. Measured and written up in
    CLAUDE.md, 2026-09-18.
    """
    from . import weekly_plan as _weekly_plan

    # Both halves of the comparison below are read here, before any
    # connection opens: each opens one of its own, and a nested get_conn
    # inside an open one is how this repo has twice earned an intermittent
    # "database is locked".
    today = _cooker.household_today()
    zone = _cooker.household_zone()
    conn = get_conn()
    plan = _weekly_plan._current_weekly_plan_row(conn)
    conn.close()
    cutoff = _household_day_start_utc(today - timedelta(days=today.weekday()), zone)
    if plan:
        period_start, period_days = _weekly_plan.plan_period(plan)
        if period_start <= today.isoformat() <= _weekly_plan.period_end_date(period_start, period_days):
            cutoff = _household_day_start_utc(date.fromisoformat(period_start), zone)
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, category, store, removed_by, removed_at FROM grocery_items "
        # removed_by 'staple' is a "not this trip" / "we have plenty" answer
        # on a staple's own line, not an already-have decision a person
        # made here — it would read as their words otherwise.
        # Nor a last-week leftover kept or dropped on the carry-over step
        # (grocery.keep_carried_over_item / drop_carried_over_item): that
        # step has its own undo, and a kept line was merged onto this
        # week's, so "put it back" here would list it twice.
        "WHERE household_id = ? AND status = 'removed' AND removed_by != '' AND removed_by != 'staple' "
        "AND removed_by NOT IN ('carried_kept', 'carried_dropped') "
        "AND removed_at IS NOT NULL AND removed_at >= ? ORDER BY removed_at DESC",
        (household_id(), cutoff),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def keep_all_pre_shop_flags() -> dict:
    """
    'Keep all {n}' — resolves every currently flagged pre-shop item as keep, in one write.

    Recorded as 'kept_all' rather than 'kept': one tap that dismisses five
    cards is not five people disagreeing with five flags, and the accuracy
    number reads differently if the two are told apart. The report adds
    them together; the column keeps the difference for whenever that
    question gets asked.
    """
    flags = get_pre_shop_flags()
    conn = get_conn()
    for f in flags:
        conn.execute(
            "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
            (f["itemId"], household_id()),
        )
        _record_decision(conn, f["itemId"], KEPT_ALL)
    conn.commit()
    conn.close()
    return {"resolved_count": len(flags)}


def mark_grocery_item_already_have_reviewed(item_id: int) -> dict:
    """
    Confirm an item flagged by get_grocery_already_have_items is still
    needed despite the inventory match (e.g. running low) — moves it back
    into the normal To-buy list and stops it from being flagged again for
    this same listing. Does not touch quantity/status; only clears the flag.

    Also the pre-shop card's "Buy it anyway" (POST /api/grocery-list/{id}
    /pre-shop with decision 'keep'), which is why the keep is counted here
    — and why it is counted only for a line the card actually raised, since
    this same function is a chat tool and the older "Already have this?"
    section's confirm button. See _record_decision.
    """
    conn = get_conn()
    require_household_row(conn, "grocery_items", item_id, label="grocery list item")
    conn.execute(
        "UPDATE grocery_items SET already_have_reviewed = 1 WHERE id = ? AND household_id = ?",
        (item_id, household_id()),
    )
    # ...but only when the line is actually still on the list. This clears
    # a flag; it does not put anything back. Called on a line that is
    # already dropped — which the chat tool and the old "Already have
    # this?" confirm can both do, since neither looks at status — it would
    # otherwise overwrite that line's 'dropped' with 'kept' and file a
    # line still sitting off the shopping list as the household having
    # disagreed with the check. That is a wrong answer inside the one
    # number this whole ledger exists to produce. undo_pre_shop_drop is
    # the only thing that takes a drop back, and it records 'undone'.
    still_on_the_list = conn.execute(
        "SELECT 1 FROM grocery_items WHERE id = ? AND household_id = ? AND status != 'removed'",
        (item_id, household_id()),
    ).fetchone()
    if still_on_the_list:
        _record_decision(conn, item_id, KEPT)
    conn.commit()
    conn.close()
    return {"item_id": item_id, "already_have_reviewed": True}


# A separate formatter from _humanize_grocery_quantity above, which rounds
# to nice *decimal* quarters ("3.25 cups") for the normal list display.
# The pre-shop sentence has a stricter rule: never a decimal — a
# fractional remainder becomes a word ("half a stick", not "0.5 stick").
# This is exactly the humanising the old "Already have this?" block
# skipped, showing raw pack math instead (see PRE_SHOP_CHECK.md "Why it
# changed" #3).
_PRE_SHOP_FRACTION_LEAD = {0.25: "a quarter of a {u}", 0.5: "half a {u}", 0.75: "three quarters of a {u}"}


_PRE_SHOP_FRACTION_TAIL = {0.25: "and a quarter", 0.5: "and a half", 0.75: "and three quarters"}


def _pre_shop_pluralize(unit: str, n: float) -> str:
    if n == 1:
        return unit
    if unit in _quantities._UNIT_PLURALS:
        return _quantities._UNIT_PLURALS[unit]
    if unit in _quantities._CONTAINER_UNIT_PLURALS:
        return _quantities._CONTAINER_UNIT_PLURALS[unit]
    return unit


def _pre_shop_amount_words(amount: float, unit: str | None) -> str | None:
    """
    Render amount+unit the way a person would say it out loud: whole
    numbers as plain digits, any fractional remainder as a word ("half",
    "a quarter") rather than a decimal — e.g. 0.5/"stick" -> "half a
    stick", 1.5/"stick" -> "a stick and a half". Returns None for a
    non-positive amount (nothing to say).
    """
    if amount <= 0:
        return None
    whole = math.floor(amount + 1e-9)
    frac = amount - whole
    nearest = min((0.0, 0.25, 0.5, 0.75, 1.0), key=lambda f: abs(f - frac))
    if nearest >= 1.0:
        whole, nearest = whole + 1, 0.0
    if not unit:
        # A bare count ("3", "2.5") — nobody buys a fraction of a plain
        # count, so round to the nearest whole rather than use a fraction
        # word (PRE_SHOP_CHECK.md's "round counts; never show decimals").
        return str(max(int(round(amount)), 1))
    if nearest == 0.0:
        n = max(whole, 1)
        return f"{n} {_pre_shop_pluralize(unit, n)}"
    if whole == 0:
        return _PRE_SHOP_FRACTION_LEAD[nearest].format(u=unit)
    if whole == 1:
        return f"a {unit} {_PRE_SHOP_FRACTION_TAIL[nearest]}"
    return f"{whole} {_pre_shop_pluralize(unit, whole)} {_PRE_SHOP_FRACTION_TAIL[nearest]}"


def _pre_shop_humanize_label(raw_qty: str) -> str | None:
    """
    Turn a raw grocery/inventory quantity string into the plain-language
    label the pre-shop sentence needs. Collapses the "X + Y" artifact left
    behind when _try_consolidate_quantity couldn't reconcile two lines
    (the old block's raw-pack-math bug) into one total whenever every
    piece shares a unit. Returns None when the amount can't be reduced to
    one confident, single-unit phrase, so the caller skips flagging that
    item rather than showing something garbled (PRE_SHOP_CHECK.md: "if it
    can't be said in one sentence, don't flag the item").
    """
    raw = (raw_qty or "").strip()
    if not raw:
        return None
    total = _pre_shop_parse_total(raw)
    if total is not None:
        return _pre_shop_amount_words(total[0], total[1])
    pieces = [p.strip() for p in raw.split(" + ") if p.strip()]
    if len(pieces) > 1:
        return None  # two amounts that don't reconcile into one phrase
    # Freeform text ("a bunch", "to taste") is already a single clean
    # phrase — just drop any trailing prep descriptor. It reads fine and
    # says nothing a coverage check can use, which is why a freeform
    # wanted amount is never flagged — see get_pre_shop_flags.
    cleaned = _quantities._strip_prep_descriptor(pieces[0])
    return cleaned or None
