"""
The change card: what the chat proposes for the week, held until the
household saves it.

Emily, 2026-09-13 ("Shaping the Draft", Flows C and D): opened from Plan,
the chat is about the whole draft; a change it suggests comes back as a
CARD — what was → what would be, one row per night — with Save changes,
an Another link on every row, and a quiet way to leave the week as it
was. Nothing is written until Save (DESIGN_SYSTEM §2b S10). "Tacos is
good but make it chicken" keeps the tacos; "something else" comes back as
three to tap; a reason ("the kids won't eat shrimp") is remembered by the
memory tools and the card re-picks around it.

What this module is:
  * `propose_plan_changes` — the AGENT tool. It writes nothing to the plan.
    It resolves each row against the live plan (the slot's current entry),
    keeps the proposal in memory under an id, and hands the model back a
    compact echo so its one reply line matches the card. The chat route
    lifts the proposal off the turn and sends it to the shell.
  * `apply_proposal` — the household's Save. Every chosen candidate goes
    through swap_in_place.pick_gate (allergen + one-veto taste) and
    swap_in_place.apply_pick (save-if-new, swap_meal_in_plan, undo note):
    the same door as "Swap · I'll pick", so leftovers, grocery and undo
    behave identically. Rows the gate refuses are reported, not written.
  * `another_for_row` — the row's Another: one small utility call (the
    swap's own picker and context) with everything this row has already
    been offered on `avoid`; the new pick becomes the row's chosen
    candidate. Not written either.
  * `undo_proposal` — the pop-up's Undo: swap_in_place.undo_meal_swap on
    each row that was applied.

Held in memory, per household, for two hours and at most ten at a time —
a proposal is a conversation's scratch, not a record. A server restart
loses open proposals; the household sees a plain "that offer has gone —
ask me again" and nothing on the plan is any different.
"""
from __future__ import annotations

import datetime
import logging
import secrets
import time

from ._shared import household_id
from . import recipes as _recipes
from . import swap_in_place as _swap
from . import weekly_plan as _weekly_plan

logger = logging.getLogger("home_manager")

_SLOTS = ("breakfast", "lunch", "dinner", "snack")
_TTL_SECONDS = 2 * 60 * 60
_MAX_PER_HOUSEHOLD = 10
_MAX_ROWS = 14
_MAX_CANDIDATES = 4

# household_id -> {proposal_id: proposal}
_PROPOSALS: dict[int, dict[str, dict]] = {}

# Said when a proposal id is unknown, expired, or another household's —
# calm, and it names the state of the plan (DESIGN_SYSTEM §8).
GONE = "That offer has gone — ask me again and I’ll make it fresh. Nothing on the week has changed."


def _now() -> float:
    return time.time()


def _household_bucket() -> dict[str, dict]:
    hh = household_id()
    bucket = _PROPOSALS.setdefault(hh, {})
    cutoff = _now() - _TTL_SECONDS
    for pid in [k for k, v in bucket.items() if v["created_at"] < cutoff]:
        del bucket[pid]
    return bucket


def get_proposal(proposal_id: str) -> dict | None:
    """Household-scoped: an id from another household is simply not found."""
    if not isinstance(proposal_id, str) or not proposal_id:
        return None
    return _household_bucket().get(proposal_id)


def _weekday(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso).strftime("%A")
    except ValueError:
        return iso


def _slot_entry(weekly_plan_id: int, meal_date: str, slot: str) -> dict | None:
    """
    The planned meal on that slot of that plan, as swap_in_place reads it —
    None when the slot is open, away, or empty. By date+slot rather than
    id so a row survives a swap made between the proposal and the save.
    """
    described = _weekly_plan.describe_planned_meal(meal_date=meal_date, slot=slot)
    if not described or described.get("weekly_plan_id") != weekly_plan_id:
        return None
    try:
        return _swap._entry(weekly_plan_id, described["entry_id"])
    except ValueError:
        return None


def _clean_candidate(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = (raw.get("meal_name") or raw.get("name") or "").strip()
    if not name:
        return None
    cand = {
        "meal_name": name,
        "reason": (raw.get("reason") or "").strip(),
        "ingredients": _swap._clean_ingredients(raw.get("ingredients")),
        "instructions": [s for s in (raw.get("instructions") or []) if isinstance(s, str) and s.strip()],
        "food_groups": [g for g in (raw.get("food_groups") or []) if g in ("protein", "carb", "vegetable")],
        "cuisine": (raw.get("cuisine") or "").strip(),
        "main_protein": (raw.get("main_protein") or "").strip(),
    }
    for key in ("prep_time_minutes", "cook_time_minutes", "default_servings", "minutes"):
        val = raw.get(key)
        if isinstance(val, int) and val >= 0:
            cand[key] = val
    if "minutes" not in cand:
        total = (cand.get("prep_time_minutes") or 0) + (cand.get("cook_time_minutes") or 0)
        if total:
            cand["minutes"] = total
    return cand


def propose_plan_changes(weekly_plan_id: int, rows: list[dict], line: str = "") -> dict:
    """
    The agent tool. `rows` is what the card will show, one per slot:
      {date, slot, action: 'change'|'keep', candidates: [pick, ...]}
    A 'change' row needs at least one candidate (the swap picker's own
    shape: meal_name, reason, ingredients, instructions, food_groups,
    cuisine, main_protein, prep/cook minutes); several candidates become
    tappable options on the card. A 'keep' row is shown as Kept and never
    written. Returns the proposal as the shell will draw it, so the reply
    line can match it — and a per-row `problem` where a slot has no meal
    to change (an open or away night is planned with plan_meal, not here).
    """
    plan = _weekly_plan.get_weekly_plan(weekly_plan_id)
    if not plan.get("weekly_plan_id"):
        return {"error": "No plan by that id for this household."}
    out_rows = []
    for raw in (rows or [])[:_MAX_ROWS]:
        if not isinstance(raw, dict):
            continue
        date = (raw.get("date") or "").strip()
        slot = (raw.get("slot") or "dinner").strip().lower()
        if slot not in _SLOTS:
            slot = "dinner"
        action = "keep" if (raw.get("action") or "").strip().lower() == "keep" else "change"
        entry = _slot_entry(weekly_plan_id, date, slot)
        row = {
            "date": date, "weekday": _weekday(date), "slot": slot, "action": action,
            "current": {"entry_id": entry["entry_id"], "meal": entry["meal"]} if entry else None,
            "candidates": [], "chosen": 0, "problem": None,
        }
        if action == "change":
            cands = [c for c in (_clean_candidate(r) for r in (raw.get("candidates") or [])) if c]
            # A dish with no ingredients and no saved recipe would land as a
            # freeform entry — nothing to cook from, nothing for the list —
            # which is the opposite of what the card promises. Dropped, and
            # said so, so the model fills it in rather than the household
            # finding out on approval.
            shoppable = [c for c in cands if c["ingredients"] or _recipe_exists(c["meal_name"])]
            dropped = [c["meal_name"] for c in cands if c not in shoppable]
            # A dish somebody at the table can't have is not an option
            # (Emily, 2026-09-20: "just don't suggest anything that fits
            # that"). Matched on name AND ingredient list HERE, before the
            # card is drawn — apply_proposal's gate on Save would refuse
            # it too, but by then the household has been shown it. The
            # model is told which dish and why, so its next offer is safe.
            unsafe = []
            safe = []
            for c in shoppable:
                # _hard_clash reads a reused dish off its saved list itself
                # (recipes.saved_ingredients) — the same fallback every
                # other door makes.
                clash = _swap._hard_clash(c)
                if clash:
                    unsafe.append(_clash_line(c["meal_name"], clash))
                else:
                    safe.append(c)
            shoppable = safe
            row["candidates"] = shoppable[:_MAX_CANDIDATES]
            # Everything this row has ever been offered, so Another can avoid
            # all of it even once the visible list is capped.
            row["offered"] = [c["meal_name"] for c in row["candidates"]]
            if not row["candidates"]:
                if unsafe:
                    row["problem"] = ("; ".join(unsafe) +
                                      " — offer something without it, outside the cuisine if that’s what it takes")
                else:
                    row["problem"] = ("no dish was offered for this slot" if not dropped else
                                      f"{', '.join(dropped)} came without ingredients — a new dish needs them to be cookable and shoppable")
            else:
                if dropped:
                    row["dropped"] = dropped
                if unsafe:
                    row["unsafe"] = unsafe
                if entry is None:
                    row["problem"] = "nothing is planned on that slot to change — plan it with plan_meal instead"
        elif entry is None:
            row["problem"] = "nothing is planned on that slot"
        if action == "change" and entry is None and row["candidates"]:
            row["problem"] = "nothing is planned on that slot to change — plan it with plan_meal instead"
        out_rows.append(row)
    if not out_rows:
        return {"error": "No rows to propose."}
    bucket = _household_bucket()
    while len(bucket) >= _MAX_PER_HOUSEHOLD:
        oldest = min(bucket, key=lambda k: bucket[k]["created_at"])
        del bucket[oldest]
    pid = secrets.token_urlsafe(9)
    proposal = {
        "proposal_id": pid,
        "weekly_plan_id": weekly_plan_id,
        "week_start_date": plan.get("week_start_date"),
        "line": (line or "").strip(),
        "rows": out_rows,
        "status": "open",
        "applied": [],
        "created_at": _now(),
    }
    bucket[pid] = proposal
    return public_view(proposal)


def _clash_line(meal_name: str, clash: list[dict]) -> str:
    """\"Pineapple Salsa has pineapple, which Emily can't have\" — what the
    model is told about a candidate it may not offer."""
    food = next((c.get("matched") for c in clash if c.get("matched")), None) \
        or next((c.get("restriction") for c in clash if c.get("restriction")), None) \
        or "something this house can’t have"
    who = next((c.get("member") for c in clash if c.get("member")), None)
    return f"{meal_name} has {food}, which {who + ' can’t have' if who else 'this house can’t have'}"


def _recipe_exists(name: str) -> bool:
    wanted = name.strip().lower()
    try:
        return any((r.get("name") or "").strip().lower() == wanted for r in _recipes.list_recipes())
    except Exception:
        return False


def public_view(proposal: dict) -> dict:
    """What crosses the wire — the recipes stay server-side."""
    rows = []
    for r in proposal["rows"]:
        rows.append({
            "date": r["date"], "weekday": r["weekday"], "slot": r["slot"], "action": r["action"],
            "current": r["current"],
            "candidates": [
                {"meal_name": c["meal_name"], "reason": c.get("reason") or "", "minutes": c.get("minutes")}
                for c in r["candidates"]
            ],
            "chosen": r["chosen"],
            "problem": r["problem"],
            "dropped": r.get("dropped") or [],
        })
    return {
        "proposal_id": proposal["proposal_id"],
        "weekly_plan_id": proposal["weekly_plan_id"],
        "week_start_date": proposal["week_start_date"],
        "line": proposal["line"],
        "status": proposal["status"],
        "rows": rows,
        "applied": [
            {"date": a["date"], "slot": a["slot"], "entry_id": a["entry_id"], "meal": a["meal"], "replaced": a["replaced"]}
            for a in proposal["applied"]
        ],
    }


def choose_candidate(proposal_id: str, row_index: int, candidate_index: int) -> dict:
    """A tap on one of a row's options. Nothing written."""
    proposal = get_proposal(proposal_id)
    if not proposal:
        return {"status": "gone", "message": GONE}
    rows = proposal["rows"]
    if not (0 <= row_index < len(rows)) or not (0 <= candidate_index < len(rows[row_index]["candidates"])):
        return {"status": "refused", "message": "That option isn’t on the card."}
    rows[row_index]["chosen"] = candidate_index
    return {"status": "chosen", "proposal": public_view(proposal)}


def another_for_row(proposal_id: str, row_index: int, picker=None) -> dict:
    """
    A different dish for one row, without touching the others: the swap's
    own picker, told to avoid the slot's current dish and everything this
    row has already been offered. The new pick joins the row's candidates
    and becomes the chosen one. Nothing written.
    """
    proposal = get_proposal(proposal_id)
    if not proposal:
        return {"status": "gone", "message": GONE}
    rows = proposal["rows"]
    if not (0 <= row_index < len(rows)) or rows[row_index]["action"] != "change":
        return {"status": "refused", "message": "That row isn’t one I can re-pick."}
    row = rows[row_index]
    entry = _slot_entry(proposal["weekly_plan_id"], row["date"], row["slot"])
    if entry is None:
        return {"status": "refused", "message": "Nothing is planned on that night any more."}
    pick_one = picker or _swap._pick_replacement
    offered = row.setdefault("offered", [c["meal_name"] for c in row["candidates"]])
    avoid = _swap._dedup([entry["meal"]] + offered + [c["meal_name"] for c in row["candidates"]])
    for attempt in range(1, _swap.MAX_PICK_ATTEMPTS + 1):
        context = _swap.build_swap_context(proposal["weekly_plan_id"], entry, avoid)
        pick = pick_one(context) or {}
        cand = _clean_candidate(pick)
        if not cand:
            logger.warning("proposal another came back with no dish (attempt %d)", attempt)
            break
        offered.append(cand["meal_name"])
        why = _swap.pick_gate(cand, entry)
        if why is None:
            row["candidates"] = (row["candidates"] + [cand])[-_MAX_CANDIDATES:]
            row["chosen"] = len(row["candidates"]) - 1
            row["problem"] = None
            return {"status": "picked", "proposal": public_view(proposal)}
        logger.warning("proposal another picked %r, refused: %s (attempt %d)", cand["meal_name"], why, attempt)
        avoid.append(cand["meal_name"])
    return {"status": "refused", "message": _swap.REFUSAL}


def _as_why(sentence: str) -> str:
    """A refusal SENTENCE reshaped into the fragment a refused row renders.

    changeRowHtml puts this after "<dish> stays — ", so it wants no capital
    and no stop on the end. weekly_plan.NIGHT_GONE_WHY is the hand-written
    fragment for the one refusal that reaches the card today, and it wins
    here so the card says ONE thing about one fact however the refusal
    arrived; anything a future backstop adds gets reshaped instead of
    printed as a sentence inside somebody else's.
    """
    if sentence == _weekly_plan.NIGHT_GONE:
        return _weekly_plan.NIGHT_GONE_WHY
    text = sentence.strip().rstrip(".")
    return (text[:1].lower() + text[1:]) if text else "it can’t be changed"


def apply_proposal(proposal_id: str) -> dict:
    """
    Save changes. Each 'change' row's chosen candidate goes through the
    same two gates and the same apply as Swap · I'll pick. A row the gate
    refuses is skipped and named in `refused`; the rest still land. The
    proposal remembers what it applied so the pop-up's Undo can put every
    row back.
    """
    proposal = get_proposal(proposal_id)
    if not proposal:
        return {"status": "gone", "message": GONE}
    if proposal["status"] == "applied":
        return {"status": "applied", "proposal": public_view(proposal), "refused": []}
    plan_id = proposal["weekly_plan_id"]
    # ONE reading of the household's clock for the card's own JUDGEMENT,
    # resolved before the loop rather than inside it. night_has_gone opens
    # a connection of its own, so asking it per row cost a seven-row card
    # seven of them (measured, 7 -> 1) — and it is the shape
    # weekly-plan-last-clock-reads settled on 2026-09-16, for a second
    # reason worth more than the connections: every row is judged against
    # the same day, so a card cannot refuse one night and accept another
    # because midnight passed between two iterations.
    #
    # Say JUDGEMENT rather than "the whole card", because apply_pick's own
    # backstop reads the clock again further down and can refuse a row this
    # loop accepted. That is not wrong — by then the night really is over
    # — and the except below is what keeps it a refused ROW rather than an
    # exception that takes the rest of the card with it.
    today = _weekly_plan._household_today().isoformat()
    applied: list[dict] = []
    refused: list[dict] = []
    for row in proposal["rows"]:
        if row["action"] != "change" or not row["candidates"]:
            continue
        cand = row["candidates"][row["chosen"]]
        entry = _slot_entry(plan_id, row["date"], row["slot"])
        if entry is None:
            refused.append({"date": row["date"], "slot": row["slot"], "meal": cand["meal_name"],
                            "why": "nothing is planned on that slot any more"})
            continue
        if entry["meal"].strip().lower() == cand["meal_name"].strip().lower():
            # Already what's there — nothing to write, nothing to undo.
            continue
        if row["date"] < today:
            # A night that has already gone by — weekly_plan.night_has_gone's
            # rule, against the one day resolved above rather than its own
            # read, which is the only thing the hoist changes. A row of this
            # card is a person tapping Save changes, so it gets the same
            # refusal the Review stepper and "Swap · I'll pick" get — but
            # PER ROW, in the shape the gate refusals below already use,
            # rather than as the raise apply_pick would give it: a card can
            # name several nights, and one that is over must not take the
            # rest of them down with it. The fragment, not the sentence —
            # changeRowHtml renders this as "<dish> stays — <why>".
            refused.append({"date": row["date"], "slot": row["slot"],
                            "meal": cand["meal_name"], "why": _weekly_plan.NIGHT_GONE_WHY})
            continue
        why = _swap.pick_gate(cand, entry)
        if why:
            refused.append({"date": row["date"], "slot": row["slot"], "meal": cand["meal_name"], "why": why})
            continue
        # The name apply_pick will actually write, asked for here so the
        # "already what's there" test above can be made again against it.
        # A candidate whose title corrects back to the dish already on the
        # slot is not a change, and putting it through anyway deletes and
        # reinserts the row for nothing — a new entry_id, the sides
        # dropped, an undo note offering to undo a change nobody made.
        # After the gate, never before it: the allergen matcher reads the
        # name and must see the one the model wrote.
        cand["meal_name"] = _swap.honest_meal_name(cand)
        if entry["meal"].strip().lower() == cand["meal_name"].strip().lower():
            continue
        try:
            result = _swap.apply_pick(plan_id, entry, cand)
        except _weekly_plan.SlotRefused as refusal:
            # apply_pick carries its own past-night backstop, and it reads
            # the clock for itself rather than taking the one resolved
            # above — so a card being saved across a midnight tick can
            # reach it even though the row passed the test at the top of
            # this loop. Rare to the point of theoretical, and caught
            # rather than left, because a raise here takes the REST of the
            # card down with it, which is the one thing the per-row
            # refusal exists to prevent. Any other refusal a future
            # backstop adds lands here too, in words, per row.
            refused.append({"date": row["date"], "slot": row["slot"],
                            "meal": cand["meal_name"], "why": _as_why(str(refusal))})
            continue
        landed = {
            "date": row["date"], "slot": row["slot"], "entry_id": result["entry_id"],
            "meal": result["meal"], "replaced": result["replaced"],
        }
        applied.append(dict(landed, day=result.get("day")))
        # Recorded as each row lands, not after the loop: if a later row
        # raises, the card's Undo can still put back the ones that did.
        proposal["applied"].append(landed)
        proposal["status"] = "applied"
    if applied:
        status = "applied"
    elif refused:
        # Every row was refused by a gate: nothing written, and the reason
        # is the thing to say — not "the week already says that".
        status = "refused"
    else:
        status = "nothing"
    return {
        "status": status,
        "proposal": public_view(proposal),
        "refused": refused,
        "days": [a["day"] for a in applied if a.get("day")],
    }


def undo_proposal(proposal_id: str) -> dict:
    """Put back every row Save changes wrote, through the swap's own undo."""
    proposal = get_proposal(proposal_id)
    if not proposal:
        return {"status": "gone", "message": GONE}
    plan_id = proposal["weekly_plan_id"]
    restored: list[dict] = []
    for a in list(proposal["applied"]):
        entry = _slot_entry(plan_id, a["date"], a["slot"])
        if entry is None:
            continue
        try:
            result = _swap.undo_meal_swap(plan_id, entry["entry_id"])
        except ValueError:
            continue
        restored.append({"date": a["date"], "slot": a["slot"], "meal": result["meal"], "day": result.get("day")})
    proposal["applied"] = []
    proposal["status"] = "open"
    return {"status": "restored", "restored": [{k: v for k, v in r.items() if k != "day"} for r in restored],
            "days": [r["day"] for r in restored if r.get("day")]}


def describe_plan_for_chat(week_start: str | None = None, weekly_plan_id: int | None = None) -> dict | None:
    """
    The whole week, as the chat's subject: every planned slot with its
    dish and entry id, plus the plan's id and status, so "less chicken this
    week" or "Thursday needs to be quick" needs no lookup. None when there
    is no such plan for this household.
    """
    if weekly_plan_id is None:
        if not week_start:
            return None
        weekly_plan_id = _weekly_plan.get_plan_id_for_week(week_start)
        if weekly_plan_id is None:
            return None
    try:
        menu = _weekly_plan.get_week_menu(weekly_plan_id)
    except Exception:
        logger.exception("Could not read the week for the chat's subject")
        return None
    if not menu.get("weekly_plan_id"):
        return None
    days = []
    for day in menu.get("days") or []:
        slots = []
        for slot in ("breakfast", "lunch", "dinner"):
            e = day.get(slot)
            if not e:
                continue
            state = e.get("state") or "planned"
            # get_week_menu names the dish `title` on a planned slot.
            title = e.get("title") or e.get("meal")
            if state == "planned" and title:
                slots.append({"slot": slot, "meal": title, "entry_id": e.get("entry_id"),
                              "source": e.get("source") or "", "meta": e.get("meta") or ""})
            elif state == "open":
                slots.append({"slot": slot, "meal": None, "state": "open (the household's call)"})
            elif state == "planned_empty":
                slots.append({"slot": slot, "meal": None, "state": "nobody home"})
        for snack in day.get("snacks") or []:
            if isinstance(snack, dict) and (snack.get("title") or snack.get("meal")):
                slots.append({"slot": "snack", "meal": snack.get("title") or snack.get("meal"),
                              "entry_id": snack.get("entry_id")})
            elif isinstance(snack, str) and snack:
                slots.append({"slot": "snack", "meal": snack})
        days.append({"date": day.get("date"), "weekday": _weekday(day.get("date") or ""), "slots": slots})
    approval = menu.get("approval") or {}
    return {
        "weekly_plan_id": menu["weekly_plan_id"],
        "week_start_date": menu.get("week_start_date"),
        "status": approval.get("status") or "draft",
        "days": days,
    }
