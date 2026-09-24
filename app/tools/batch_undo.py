"""
Un-batching: "don't batch the rice", "cook the chili fresh on Thursday",
"no batch cooking this week".

Emily, 2026-09-21, after verification found the chat had no batch or
cook-ahead tool at all: silent learning needs a visible undo right where
it shows. Batching is assumed from prep days now
(cook_ahead.apply_prep_day_batches, 2026-09-18/21) — nobody is asked, the
week is approved and the All set line says what happened — so the only
way out was Swap on the Plan tab, which changes the DISH rather than how
it is cooked. Saying "don't batch the rice" did nothing at all.

TWO SHAPES OF BATCH, and this module is one door onto both, because the
household says "don't batch the rice" without knowing which kind the rice
is:

  a repeated DISH   one cook on the first day feeds the later ones — a
                    leftover chain, both halves (cook_ahead.py).
  a shared COMPONENT  two different dishes that each boil the same eggs —
                    one prep_tasks row (batch_components.py).

NOTHING HERE PRUNES A CHAIN ITSELF. Un-batching a dish is
cook_ahead.set_cook_ahead with the freed days taken off the list — the
very write that made the batch, run backwards — so the source's note, the
released day's links_to and the cook_ahead flag, and the sibling a chip
stands for are all handled by the one function that already knows about
them. A component is batch_components.clear_batch_component (a whole
batch) or set_batch_component with the remaining dishes (one dish out of
it).

THE GROCERY LIST, and the honest version is narrower than the ticket's.
A batch changes what is COOKED, not what is eaten: a batched source's
share is exactly the sum of the shares of the days it feeds, and the
recipe-week is rounded once either way, so the LINES do not move. Measured
rather than reasoned — "its ingredients return to the grocery list at the
single-cook amount" is already true at the moment this runs, because the
approval-time batch lands AFTER the grocery ingest and nothing has taken
them off.

What DOES differ is the LEDGER, and whether it is in the batched shape
depends on history: any later rescale (a swap on that recipe) moves the
whole amount onto the cook night and leaves the fed night carrying none of
it. So the whole recipe-week is reversed and re-ingested through
weekly_plan._rescale_leftover_source_grocery with NOTHING excluded — the
freed day is back in the group and buys its own portion again. Usually a
no-op on the lines and always right on the ledger, which is what the next
removal there subtracts against.

ONE line really does move, and it is a pre-existing defect made visible
rather than one this creates: a fed night contributes nothing while it is
a reheat, INCLUDING ITS SIDE, so a Thursday reheat with a fresh green
salad has no lettuce on the list. Un-batching puts it back.
`list_changed` says whether any line's words actually moved, so the reply
only claims the list changed when it did — which is that case and the
repair of a list that had already drifted, and nothing else.

REMEMBERING THE CHOICE lives on the entries, not in a new table. A freed
day carries derived_from.no_batch; a dish dropped out of a component
batch carries derived_from.no_batch_components (a list of keys).
apply_prep_day_batches reads both and skips what the household has
already said no to, so re-approving the week doesn't quietly put it back.
A row swapped away takes its objection with it, which is right — a new
row is a new decision.

WHAT THIS DELIBERATELY WILL NOT TOUCH: a leftovers night the PLANNER
wrote. Nobody batched that — the week was planned around cooking once and
eating twice — so it is not in batched_dishes (which reads the cook_ahead
flag) and it is not this tool's to undo. Changing one of those is
changing the plan, which is swap_meal_in_plan's job.
"""
from __future__ import annotations

import json
import re

from ..db import get_conn
from ._shared import household_id
from . import batch_components as _batch_components
from . import cook_ahead as _cook_ahead
from . import leftovers as _leftovers
from . import weekly_plan as _weekly_plan

# derived_from keys. Two rather than one because the two batches are
# identified differently — a dish batch is about the DAY that was freed,
# a component batch about a named component on a dish that may still be
# in a batch for something else.
NO_BATCH_FLAG = "no_batch"
NO_BATCH_COMPONENTS_FLAG = "no_batch_components"

# Words that say nothing about which batch is meant. "batch"/"batching"
# are in here for the obvious reason: they are in almost every sentence
# that reaches this tool.
_STOPWORDS = {
    "a", "an", "the", "this", "that", "these", "those", "my", "our", "your",
    "batch", "batched", "batches", "batching", "cook", "cooking", "cooked",
    "make", "making", "made", "fresh", "freshly", "separate", "separately",
    "own", "each", "every", "all", "just", "please", "dont", "don", "not",
    "no", "on", "in", "for", "of", "and", "or", "it", "them", "week", "weeks",
    "day", "days", "night", "nights", "again", "instead", "up", "ahead",
    "everything", "anything", "one", "once", "twice", "from", "to", "with",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _content_words(text: str) -> list[str]:
    return [w for w in _words(text) if w not in _STOPWORDS and len(w) > 2]


def _derived(entry_id: int, conn=None) -> dict:
    own = conn is None
    if own:
        conn = get_conn()
    row = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries WHERE id = ? AND household_id = ?",
        (entry_id, household_id()),
    ).fetchone()
    if own:
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["derived_from_json"] or "{}")
    except (TypeError, ValueError):
        return {}


def _write_derived(entry_id: int, derived: dict) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE meal_plan_entries SET derived_from_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(derived), entry_id, household_id()),
    )
    conn.commit()
    conn.close()


def remember_no_batch(entry_ids: list[int]) -> None:
    """Mark these days as ones the household has said not to batch."""
    for entry_id in entry_ids:
        derived = _derived(entry_id)
        if derived.get(NO_BATCH_FLAG):
            continue
        derived[NO_BATCH_FLAG] = True
        _write_derived(entry_id, derived)


def forget_no_batch(entry_ids: list[int]) -> None:
    for entry_id in entry_ids:
        derived = _derived(entry_id)
        if NO_BATCH_FLAG not in derived:
            continue
        derived.pop(NO_BATCH_FLAG, None)
        _write_derived(entry_id, derived)


def remember_no_batch_component(entry_ids: list[int], key: str) -> None:
    """
    Mark a component as one these dishes should not be batched into. On
    every entry that was in the batch, not just one: a swap takes an
    entry's objection with it, and the rest still standing is the
    conservative direction — stay un-batched.
    """
    for entry_id in entry_ids:
        derived = _derived(entry_id)
        keys = [k for k in (derived.get(NO_BATCH_COMPONENTS_FLAG) or []) if isinstance(k, str)]
        if key in keys:
            continue
        derived[NO_BATCH_COMPONENTS_FLAG] = sorted(keys + [key])
        _write_derived(entry_id, derived)


def forget_no_batch_component(entry_ids: list[int], key: str) -> None:
    for entry_id in entry_ids:
        derived = _derived(entry_id)
        keys = [k for k in (derived.get(NO_BATCH_COMPONENTS_FLAG) or []) if isinstance(k, str)]
        if key not in keys:
            continue
        remaining = [k for k in keys if k != key]
        if remaining:
            derived[NO_BATCH_COMPONENTS_FLAG] = remaining
        else:
            derived.pop(NO_BATCH_COMPONENTS_FLAG, None)
        _write_derived(entry_id, derived)


def declined_dish_entry_ids(weekly_plan_id: int) -> set[int]:
    """Every day on this plan the household has said not to batch."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    out = set()
    for row in rows:
        try:
            derived = json.loads(row["derived_from_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if derived.get(NO_BATCH_FLAG):
            out.add(row["id"])
    return out


def declined_component_keys(weekly_plan_id: int) -> set[str]:
    """Every component key this plan's entries have said no to."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT derived_from_json FROM meal_plan_entries "
        "WHERE weekly_plan_id = ? AND household_id = ? AND component_category IS NULL",
        (weekly_plan_id, household_id()),
    ).fetchall()
    conn.close()
    out: set[str] = set()
    for row in rows:
        try:
            derived = json.loads(row["derived_from_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for key in derived.get(NO_BATCH_COMPONENTS_FLAG) or []:
            if isinstance(key, str):
                out.add(key)
    return out


# ---------- what is batched, in one shape ----------

def batches_on_plan(weekly_plan_id: int) -> list[dict]:
    """
    Every batch standing on this plan, dish batches and component batches
    together, in the week's order — the candidate set a household's words
    are resolved against.

    {kind, label, cook_date, covered: [{entry_id, date, dish}], words,
     source_entry_id | key}

    `words` is the haystack a phrase is matched against: for a dish, its
    own name; for a component, the thing cooked, how, and the dishes that
    cook it, because "don't batch the rice" names the ingredient and
    "don't batch the egg salad" names a dish.
    """
    out: list[dict] = []
    for batch in _cook_ahead.batched_dishes(weekly_plan_id):
        dish = batch["dish"]
        out.append({
            "kind": "dish",
            "label": dish,
            "source_entry_id": batch["source_entry_id"],
            "cook_date": batch["date"],
            "slot": batch["slot"],
            "covered": [
                {"entry_id": c["entry_id"], "date": c["date"], "dish": dish}
                for c in batch["covered"]
            ],
            "words": set(_words(dish)),
        })
    for batch in _batch_components.batched_components(weekly_plan_id):
        words = set(_words(batch["ingredient"])) | set(_words(batch["label"])) | set(_words(batch["dish"]))
        for covered in batch["covered"]:
            words |= set(_words(covered["dish"]))
        out.append({
            "kind": "component",
            "key": batch["key"],
            "label": batch["label"],
            "ingredient": batch["ingredient"],
            "verb": batch["verb"],
            "cook_date": batch["date"],
            "cook_dish": batch["dish"],
            "source_entry_id": batch["source_entry_id"],
            "covered": [
                {"entry_id": c["entry_id"], "date": c["date"], "dish": c["dish"]}
                for c in batch["covered"]
            ],
            "words": words,
        })
    out.sort(key=lambda b: (b["cook_date"], b["label"].lower()))
    return out


def _matches(batch: dict, asked: list[str]) -> int:
    """
    How many of the household's own content words this batch answers to.
    Zero means it isn't the one. A word counts when it is one of the
    batch's words or a prefix of one ("chili" answers "Turkey Chili",
    "egg" answers "eggs"), which is as much guessing as a set this small
    needs — the alternative is stemming, and a wrong match here costs the
    household a batch they meant to keep.
    """
    hits = 0
    for word in asked:
        if any(w == word or w.startswith(word) or word.startswith(w) for w in batch["words"]):
            hits += 1
    return hits


def _resolve(batches: list[dict], what: str, day: str) -> tuple[list[dict], str]:
    """
    (the batches meant, a question to ask instead). Exactly one of the two
    is ever non-empty.

    Blank `what` means all of them — "no batch cooking this week". A `day`
    narrows to the batches that touch it, whether it is the day that cooks
    or a day being fed. More than one match with nothing to choose between
    them is a question, not a guess.
    """
    candidates = batches
    if day:
        candidates = [
            b for b in candidates
            if b["cook_date"] == day or any(c["date"] == day for c in b["covered"])
        ]
        if not candidates:
            return [], ""
    asked = _content_words(what)
    if not asked:
        return candidates, ""
    scored = [(b, _matches(b, asked)) for b in candidates]
    best = max((s for _, s in scored), default=0)
    if best == 0:
        return [], ""
    hits = [b for b, s in scored if s == best]
    if len(hits) > 1:
        names = _leftovers._join_days([b["label"] for b in hits])
        return [], f"Which one — {names}?"
    return hits, ""


# ---------- the writes ----------

def _grocery_lines(weekly_plan_id: int) -> list[tuple]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT item, quantity, status FROM grocery_items WHERE household_id = ? ORDER BY id",
        (household_id(),),
    ).fetchall()
    conn.close()
    return sorted((r["item"], r["quantity"], r["status"]) for r in rows)


def _free_dish_days(batch: dict, free_ids: list[int]) -> str:
    """
    Take days off a dish batch through the write that made it. Returns a
    refusal sentence, or "" on success.
    """
    keep = [c["entry_id"] for c in batch["covered"] if c["entry_id"] not in set(free_ids)]
    result = _cook_ahead.set_cook_ahead(batch["source_entry_id"], keep)
    return result if isinstance(result, str) else ""


def _free_component_dishes(weekly_plan_id: int, batch: dict, free_ids: list[int], source_id: int | None) -> str:
    """
    Take dishes out of a component batch. A batch is two dishes or it is
    nothing, so once fewer than two are left the row goes entirely rather
    than standing as a batch of one. Returns a refusal sentence, or "".
    """
    freed = set(free_ids)
    # The cook day's own dish counts toward the two; `covered` is only the
    # later ones (batch_components.batched_components).
    everyone = ([source_id] if source_id else []) + [c["entry_id"] for c in batch["covered"]]
    keep = [e for e in everyone if e not in freed]
    if len(keep) < 2:
        _batch_components.clear_batch_component(weekly_plan_id, batch["key"])
        return ""
    result = _batch_components.set_batch_component(weekly_plan_id, batch["key"], keep)
    return result if isinstance(result, str) else ""


def batch_source_id(batch: dict) -> int | None:
    """
    The entry a batch is cooked on — the row the household's "don't batch
    this" objection is written onto.

    BOTH KINDS NOW ANSWER FROM THE PLAN THE BATCH BELONGS TO. The
    component half used to re-find it with a household-only query taking
    `ORDER BY id DESC LIMIT 1`, i.e. the most recently inserted row for
    that key across EVERY plan the household has — and a household with
    two approved weeks sharing a component key (eggs, rice, the card's
    own examples) is the app's own "Plan next week ›", not an exotic
    shape. Found by review, 2026-09-24, reproduced through that ordinary
    sequence: un-batching THIS week resolved the source to NEXT week's
    entry, so the objection landed on next week (which then silently
    declined a batch nobody objected to), this week's own source never
    got it, and the card's Undo button answered "Pick at least two dishes
    to make them at once." — the one sentence clear_batch_component's
    docstring says must never be shown to somebody undoing a batch.

    Nothing was being read across HOUSEHOLDS; the miss was across PLANS,
    which is why 40 tests passed over it — `_plan` builds exactly one, so
    no fixture crossed the boundary. The same shape as an isolation test
    that passes because it never crossed the one it names.
    """
    return batch.get("source_entry_id")


# ---------- the sentences ----------

def _day_word(date_str: str) -> str:
    return _weekly_plan._weekday_label(date_str)


def _dish_said(batch: dict, freed: list[dict], whole: bool) -> str:
    days = _leftovers._join_days([_day_word(c["date"]) for c in freed])
    cook_day = _day_word(batch["cook_date"])
    if whole:
        return f"{batch['label']} cooks fresh on {days} now, not from {cook_day}’s batch."
    return f"{days}’s {batch['label']} cooks fresh now, not from {cook_day}’s batch."


def _present(participle: str) -> str:
    """
    "boiled" -> "boils": the verb the sentence needs, taken from
    batch_components' own two tables rather than a third list of verbs
    here. "makes" when the participle isn't one it knows — a true sentence
    with a duller verb beats a made-up one.
    """
    stem = _batch_components._PARTICIPLES.get((participle or "").lower())
    imperative = (_batch_components._PREP_VERBS.get(stem, ("", ""))[0] or "").lower() if stem else ""
    if not imperative:
        return "makes"
    return imperative + ("es" if imperative.endswith(("s", "x", "z", "ch", "sh")) else "s")


def _component_said(batch: dict, freed: list[dict], whole: bool) -> str:
    verb = _present(batch.get("verb") or "")
    if whole:
        return f"Each dish {verb} its own {batch['ingredient']} now."
    days = _leftovers._join_days([f"{_day_word(c['date'])}’s {c['dish']}" for c in freed])
    return f"{days} {verb} its own {batch['ingredient']} now."


def _said_for(results: list[dict], swept: bool) -> str:
    """
    One line for the whole answer. `swept` is "every batch on the week is
    gone" — the only case that earns the sweeping sentence; anything else
    says what it actually did, batch by batch, rather than claiming more.
    """
    if swept:
        return "Nothing’s batched this week now — every dish cooks its own."
    return " ".join(r["said"] for r in results)


# ---------- the tool ----------

def unbatch(what: str = "", day: str = "") -> dict:
    """
    Undo a batch the household didn't want — one repeated dish, one shared
    component, one day of either, or everything on the week.

    `what` names the dish or the thing being cooked ("the rice", "the
    chili", "the eggs"); blank means every batch on the plan. `day`
    (YYYY-MM-DD) narrows to a batch that touches it, and when it is a day
    being FED rather than the day that cooks, only that day is freed.

    Returns {status, said, undo, list_changed}. `status`:
      'unbatched'  something changed; `said` is the line to say back and
                   `undo` is the payload that puts it back.
      'ambiguous'  more than one batch answers to those words; `said` is
                   the question to ask.
      'nothing'    nothing on this week is batched that way; `said` says
                   which of the two it is.
      'refused'    the write declined (a day that has moved under the
                   request); `said` is its own sentence.
    """
    conn = get_conn()
    plan = _weekly_plan._current_weekly_plan_row(conn)
    conn.close()
    if plan is None:
        return {"status": "nothing", "said": "There’s no plan on this week yet.", "undo": None,
                "list_changed": False}
    plan_id = plan["id"]

    batches = batches_on_plan(plan_id)
    if not batches:
        return {"status": "nothing", "said": "Nothing’s batched on this week.", "undo": None,
                "list_changed": False}

    day = (day or "").strip()
    hits, question = _resolve(batches, what or "", day)
    if question:
        return {"status": "ambiguous", "said": question, "undo": None, "list_changed": False}
    if not hits:
        named = (what or "").strip()
        said = (f"I’m not batching {named} on this week." if named
                else "Nothing’s batched on that day.")
        return {"status": "nothing", "said": said, "undo": None, "list_changed": False}

    before = _grocery_lines(plan_id)
    approved = _weekly_plan._weekly_plan_is_approved(plan_id)
    results: list[dict] = []
    undo: dict = {"weekly_plan_id": plan_id, "dishes": [], "components": []}

    for batch in hits:
        # A day named as one this batch FEEDS frees that day alone; the
        # day that cooks, or no day at all, frees the whole batch.
        covered_that_day = [c for c in batch["covered"] if day and c["date"] == day]
        freed = covered_that_day or list(batch["covered"])
        whole = len(freed) == len(batch["covered"])
        free_ids = [c["entry_id"] for c in freed]

        if batch["kind"] == "dish":
            refusal = _free_dish_days(batch, free_ids)
            if refusal:
                return {"status": "refused", "said": refusal, "undo": None, "list_changed": False}
            remember_no_batch(free_ids)
            undo["dishes"].append({
                "source_entry_id": batch["source_entry_id"],
                "covered_entry_ids": [c["entry_id"] for c in batch["covered"]],
            })
            if approved:
                # The whole recipe-week, re-derived from the chain as it now
                # stands. Nothing is excluded — the freed day is back in the
                # group and buys its own portion again. See the module note.
                _weekly_plan._rescale_leftover_source_grocery(batch["source_entry_id"], 0)
            results.append({"said": _dish_said(batch, freed, whole), "whole": whole})
        else:
            source_id = batch_source_id(batch)
            refusal = _free_component_dishes(plan_id, batch, free_ids, source_id)
            if refusal:
                return {"status": "refused", "said": refusal, "undo": None, "list_changed": False}
            all_ids = [source_id] + [c["entry_id"] for c in batch["covered"]]
            remember_no_batch_component([i for i in all_ids if i], batch["key"])
            undo["components"].append({
                "key": batch["key"],
                "entry_ids": [i for i in all_ids if i],
            })
            results.append({"said": _component_said(batch, freed, whole), "whole": whole})

    list_changed = _grocery_lines(plan_id) != before
    swept = len(hits) == len(batches) and all(r["whole"] for r in results) and len(results) > 1
    said = _said_for(results, swept)
    if list_changed:
        said = f"{said} Your list has changed to match."
    return {"status": "unbatched", "said": said, "undo": undo, "list_changed": list_changed}


def rebatch(undo: dict) -> dict:
    """
    Put back what unbatch took apart — the Undo on the change card.

    Written as the same two writes that made the batch in the first place
    (set_cook_ahead / set_batch_component) rather than as a stored
    snapshot: re-batching is exactly a batch, so there is nothing to
    restore that making it again doesn't produce, and a batch the week has
    moved under refuses in its own words instead of forcing a stale shape
    back onto it. Every id goes through a household-scoped write, so a
    payload naming somebody else's plan writes nothing.
    """
    if not isinstance(undo, dict):
        return {"status": "refused", "said": "There’s nothing to put back."}
    plan_id = undo.get("weekly_plan_id")
    conn = get_conn()
    plan = conn.execute(
        "SELECT id FROM weekly_plans WHERE id = ? AND household_id = ?", (plan_id, household_id())
    ).fetchone()
    conn.close()
    if plan is None:
        return {"status": "refused", "said": "That week isn’t on the plan any more."}

    # NOTHING HERE TOUCHES THE GROCERY LIST, and the asymmetry with
    # unbatch is the app's own rule rather than an oversight: making a
    # batch never changes what has to be bought (cook_ahead.py's
    # docstring, and the arithmetic behind it — a batched source's share
    # is exactly the sum of the shares of the days it feeds, so the line
    # is the same either way). Un-batching rescales only because a
    # previous rescale may have taken a freed day's own portion off the
    # ledger; putting the batch back has nothing to put right.
    #
    # Measured, and it is why this is not symmetric for the sake of it: a
    # rescale run here re-ingests the whole recipe-week with the chain
    # back in place, and a reheat night contributes nothing — including
    # its SIDE. A Thursday reheat with a fresh green salad lost the
    # lettuce off the list, which is worse than the state the undo is
    # undoing to. (That a chained night's side is never bought at all is
    # pre-existing and its own card.)
    for dish in undo.get("dishes") or []:
        source_id = dish.get("source_entry_id")
        covered = [e for e in (dish.get("covered_entry_ids") or []) if isinstance(e, int)]
        result = _cook_ahead.set_cook_ahead(source_id, covered)
        if isinstance(result, str):
            return {"status": "refused", "said": result}
        forget_no_batch(covered)
    for comp in undo.get("components") or []:
        key = comp.get("key") or ""
        entry_ids = [e for e in (comp.get("entry_ids") or []) if isinstance(e, int)]
        result = _batch_components.set_batch_component(plan_id, key, entry_ids)
        if isinstance(result, str):
            return {"status": "refused", "said": result}
        forget_no_batch_component(entry_ids, key)
    return {"status": "rebatched", "said": "Put back."}
