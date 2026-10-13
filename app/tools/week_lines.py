"""
One line on Shop for a thing two live weeks both need (Loop Board "Shop
shows one line per item while two weeks are live", Emily 2026-10-03;
rebuilt on main 2026-10-13).

Next week can be approved while this week still runs, and each week keeps
its OWN grocery line for the same food (grocery._merge_target never folds
two plans' lines: a swap or a removal in one week must take off only that
week's amount — see the ledger notes in CLAUDE.md). That is right for the
data and wrong for the shopper, who read "Onions 2" and "Onions 3" and
wondered which to buy.

So the grouping is DISPLAY ONLY. Nothing here writes. `stamp_week_groups`
marks the lines of one group with the same `week_group` — the summed
amount, the per-week note ("2 this week · 3 next week"), every id in it,
and which one leads — and the Shop screen draws the group as one row and
sends each tap to every id (static/shell.js, groCollapseWeeks /
groIdsFor). Every other reader of the list is unchanged.

What groups, and what never does:
  * only lines a plan put there (source_weekly_plan_id set), from two or
    more DIFFERENT plans, one line each — two lines of one plan are a
    duplicate, which the list's own "Merge" handles;
  * never a staple's line or a hand/chat add: those are the household's
    own wants, not a week's;
  * only lines that read the same way on the screen right now — same
    store, same aisle, same status, both on or both off the list, set
    aside for the same reason — so one row never hides two different
    answers. A week ticked and a week not are two rows again.
"""
from __future__ import annotations

from datetime import date

from ..db import get_conn
from ._shared import household_id
from . import cooker as _cooker
from . import grocery as _grocery
from . import quantities as _quantities
from . import weekly_plan as _weekly_plan

_STATUSES_NEVER_GROUPED = ("spice", "carried")


def _week_label(start: str, end: str, today: date, by_date: bool = False) -> str:
    """How the note names a week: "this week" for the one the household is
    in, "next week" for one starting within the coming seven days, "last
    week" for the one just ended, otherwise (or with by_date) "week of
    Oct 19"."""
    iso = today.isoformat()
    first = date.fromisoformat(start)
    if by_date:
        return f"week of {first.strftime('%b')} {first.day}"
    if start <= iso <= end:
        return "this week"
    if first > today and (first - today).days <= 7:
        return "next week"
    if end < iso and (today - date.fromisoformat(end)).days <= 7:
        return "last week"
    return f"week of {first.strftime('%b')} {first.day}"


def _total(quantities: list[str]) -> str:
    """The amounts added up the way a grocery line is written
    (quantities._sum_ledger_quantities). Two amounts that won't add — a
    note on one, two unit families — read "1 lb + 8 oz", the same honest
    disagreement the list shows elsewhere. A week that wants "some" makes
    any number a guess, so the row then says none and the note says why."""
    if any(not q for q in quantities):
        return ""
    summed = _quantities._sum_ledger_quantities(quantities)
    return summed or " + ".join(quantities)


def _grouped_flag(members: list[dict], total: str) -> dict | None:
    """
    "Maybe already home" on a grouped row, only when the kitchen covers
    EVERY week's line. get_pre_shop_flags claims the stock as it grants it,
    so the second week's line is flagged only if what's left after the
    first still covers it — every line flagged means the shelf covers the
    sum. Anything less and the row asks nothing: "Remove from list" would
    take next week's onions off because this week's are in the fridge.
    The sentence says the total wanted, never one week's share.
    """
    flags = [m.get("pre_shop") for m in members]
    if not all(flags):
        return None
    from . import pre_shop as _pre_shop
    wanted = _pre_shop._pre_shop_humanize_label(total) if total else None
    if not wanted:
        return None
    lead = flags[0]
    sentence = f"You want {wanted}. Fridge shows {lead.get('onHandLabel')}."
    if len(sentence) > 60:
        return None
    return {**lead, "sentence": sentence, "wantedLabel": wanted}


def stamp_week_groups(items: list[dict]) -> list[dict]:
    """
    Put `week_group` on every line in `items` that shares one Shop row with
    another week's line (see the module doc). Mutates and returns `items`.
    A line in no group is left exactly as it was.
    """
    ids = []
    for it in items:
        try:
            ids.append(int(it.get("id")))
        except (TypeError, ValueError):
            continue
    if len(ids) < 2:
        return items
    marks = ",".join("?" * len(ids))
    conn = get_conn()
    try:
        meta = {
            r["id"]: r for r in conn.execute(
                "SELECT id, source_weekly_plan_id, staple_id, status, excluded_from_list, removed_by, store, category "
                f"FROM grocery_items WHERE household_id = ? AND id IN ({marks})",
                (household_id(), *ids),
            ).fetchall()
        }
        plans = {
            r["id"]: r for r in conn.execute(
                "SELECT id, week_start_date, content_start_date, day_count FROM weekly_plans WHERE household_id = ?",
                (household_id(),),
            ).fetchall()
        }
        today = _cooker.household_today(conn=conn)
    finally:
        conn.close()

    groups: dict[tuple, list[dict]] = {}
    for it in items:
        try:
            row = meta.get(int(it.get("id")))
        except (TypeError, ValueError):
            continue
        if (
            row is None or row["source_weekly_plan_id"] is None or row["staple_id"] is not None
            or row["status"] in _STATUSES_NEVER_GROUPED or row["source_weekly_plan_id"] not in plans
        ):
            continue
        category = row["category"] or "other"
        key = (
            _grocery._merge_key(it.get("item") or ""),
            (row["store"] or "").strip().lower(),
            _quantities._GROCERY_CATEGORY_ALIASES.get(category, category),
            row["status"],
            int(row["excluded_from_list"] or 0),
            row["removed_by"] or "",
        )
        groups.setdefault(key, []).append({"item": it, "plan": row["source_weekly_plan_id"]})

    for members in groups.values():
        plan_ids = [m["plan"] for m in members]
        if len(members) < 2 or len(set(plan_ids)) != len(plan_ids):
            continue
        spans = {}
        for pid in plan_ids:
            start, days = _weekly_plan.plan_period(plans[pid])
            spans[pid] = (start, _weekly_plan.period_end_date(start, days))
        members.sort(key=lambda m: (spans[m["plan"]][0], int(m["item"]["id"])))
        labels = [_week_label(*spans[m["plan"]], today) for m in members]
        if len(set(labels)) != len(labels):
            # Two weeks that would both read "next week": name them by date.
            labels = [_week_label(*spans[m["plan"]], today, by_date=True) for m in members]
        quantities = [(m["item"].get("quantity") or "").strip() for m in members]
        total = _total(quantities)
        lines = [m["item"] for m in members]
        group = {
            "lead_id": lines[0]["id"],
            "ids": [it["id"] for it in lines],
            "total": total,
            "note": " · ".join(f"{q or 'some'} {label}" for q, label in zip(quantities, labels)),
            "parts": [
                {"id": it["id"], "quantity": q, "label": label}
                for it, q, label in zip(lines, quantities, labels)
            ],
            "pre_shop": _grouped_flag(lines, total),
        }
        for it in lines:
            it["week_group"] = group
    return items
