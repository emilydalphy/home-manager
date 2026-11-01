"""
An allergy typed as a What we know note — "Sam is allergic to sesame" —
asked about once, and enforced only on the tap (Loop Board, Emily
2026-10-31: "ask, then enforce").

A note is a hint to the planner, never a rule: the allergy check reads a
person's restrictions (and the must-avoid notes), not an ordinary note. So
a parent who wrote the allergy where it felt natural could still get a
sesame dish planned. Now saving such a note answers with an offer — "Save
sesame as Sam's allergy?" — and the yes writes the same restriction Who's
here writes, through the same `set_member_dietary_restrictions`, so from
then on it is held back exactly like one typed on Sam's chips.

Nothing here writes without the tap. The note itself is saved either way;
`allergy_offer` only reads; `save_offered_allergy` re-reads the note as it
is stored NOW and saves only what that reading offers, so a client cannot
save an allergy the note never said, and a note edited since the offer was
shown is refused rather than saved under its old reading.

The reading is coordination's own (`_fact_keywords`, `_name_words`), the
same one the allergy check runs on a must-avoid note — a second matcher
here would drift from what is enforced.
"""
from __future__ import annotations

import re

from ..db import get_conn
from ._shared import household_id
from . import coordination as _coordination
from . import household as _household

# Allergy wording. Narrower than the check's own trigger list on purpose:
# that one reads bare "no" and "never" because a must-avoid note has
# already been SAID to be about avoiding food, and on an ordinary note it
# would offer "Save school as Sam's allergy?" for "Sam has no school on
# Fridays". "no … ever" is the card's own example of the shape.
_ALLERGY_WORDING_RE = re.compile(
    r"\ballerg(?:ic|y|ies)\b"
    r"|\bintoleran(?:t|ce)\b"
    r"|\bcan(?:no|')?t\s+(?:have|eat)\b"
    r"|\bno\b[^.;!?]*\bever\b"
)


def _named_members(text: str, members: list[dict]) -> list[dict]:
    """Every person the note names, whole word, in household order —
    "Sam and Mia are allergic to sesame" is an allergy for both."""
    lowered = (text or "").lower()
    return [
        m for m in members
        if (m["name"] or "").strip()
        and re.search(r"\b" + re.escape(m["name"].strip().lower()) + r"\b", lowered)
    ]


def _on_file(food: str, restrictions: list[str]) -> bool:
    """Sesame is already Sam's when his chips say "sesame" or "allergy: sesame"."""
    have = set()
    for r in restrictions or []:
        r = (r or "").strip().lower()
        have.add(r)
        if r.startswith("allergy:"):
            have.add(r[len("allergy:"):].strip())
    return food.lower() in have


def _possessive(name: str) -> str:
    return name + "’s"


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def allergy_offer(text: str) -> dict | None:
    """
    The one-tap question for a note that reads as a person's allergy, or
    None. Shape: {"question": "Save sesame as Sam’s allergy?",
    "members": [{"id", "name"}], "foods": ["sesame"]}.

    None when the note names nobody (a household-wide "no pork in this
    house" is not one person's allergy), has no allergy wording, names no
    food, or every food it names is already on everyone it names.
    """
    text = (text or "").strip()
    lowered = text.lower().replace("’", "'")
    if not text or not _ALLERGY_WORDING_RE.search(lowered):
        return None
    named = _named_members(text, _household.list_members())
    if not named:
        return None
    drop: set[str] = set()
    for m in named:
        drop |= _coordination._name_words(m["name"])
    phrases, _excepted = _coordination._fact_keywords(text, drop=drop)
    foods: list[str] = []
    for words in phrases:
        food = " ".join(words)
        if food and food not in foods:
            foods.append(food)
    # Only what is new to at least one of them. Asking to save what Sam's
    # chips already say is a question with nothing behind the yes.
    foods = [f for f in foods if any(not _on_file(f, m["dietary_restrictions"]) for m in named)]
    if not foods:
        return None
    names = [m["name"].strip() for m in named]
    whose = _join([_possessive(n) for n in names])
    noun = "allergy" if len(foods) == 1 else "allergies"
    return {
        "question": f"Save {_join(foods)} as {whose} {noun}?",
        "members": [{"id": m["id"], "name": m["name"].strip()} for m in named],
        "foods": foods,
    }


def allergy_offer_for_fact(fact_id: int) -> dict | None:
    """allergy_offer on the note as it is stored now, or None if there is
    no such note in this household."""
    conn = get_conn()
    row = conn.execute(
        "SELECT text FROM facts WHERE id = ? AND household_id = ?", (fact_id, household_id()),
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No fact with id {fact_id}")
    return allergy_offer(row["text"])


def save_offered_allergy(fact_id: int, foods: list[str] | None = None) -> dict:
    """
    The yes. Writes each offered food onto each person the note names as
    "allergy: <food>" — the restriction Who's here keeps — merged, never
    replacing what is there.

    `foods` is what the screen showed. It must be exactly what the note
    offers now: a client cannot add a food, and a note edited since the
    question was drawn is refused (ValueError) rather than saved on a
    reading the household never saw.
    """
    offer = allergy_offer_for_fact(fact_id)
    if not offer:
        raise ValueError("There’s no allergy in that note to save.")
    if foods is not None:
        shown = sorted({(f or "").strip().lower() for f in foods if (f or "").strip()})
        if shown != sorted(offer["foods"]):
            raise ValueError("That note has changed since I asked — nothing was saved.")
    on_file = {m["id"]: m["dietary_restrictions"] for m in _household.list_members()}
    saved = []
    for m in offer["members"]:
        # Per person: Mia may already have sesame on her chips as "sesame",
        # and a second chip saying "allergic to sesame" is the same thing twice.
        new = [f"allergy: {f}" for f in offer["foods"] if not _on_file(f, on_file.get(m["id"], []))]
        if new:
            saved.append(_household.set_member_dietary_restrictions(m["name"], new, replace=False))
    return {"saved": True, "foods": offer["foods"], "members": saved}
