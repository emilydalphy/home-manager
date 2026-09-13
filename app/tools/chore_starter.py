"""
The starter chore list — proposed from what Pomona already knows.

Loop Board "Chores v1: A starter list from what Pomona already knows"
(Emily, Phase 2). Somebody setting up chores should keep and tweak a list
rather than type their housework out from scratch, and nobody should have
to become the app's administrator to get there. So the list is built from
facts the household has already given — home type, bedrooms, bathrooms,
yard, pets, who lives here, the upkeep standard, any help that comes in —
by RULES, in code, with no model in the loop:

  * deterministic and testable: a 3-bed/2-bath house with a yard and a
    dog gets the same list every time, and a condo with no pets never
    gets a lawn or a litter box;
  * always present: agent.generate_chore_recommendations starts from this
    list and lets the model ADD to it or ADJUST it from the household's
    free-text notes, but the model can never drop the laundry or the
    garbage (NEVER_DROPPED) — the household drops rows by hand, in review;
  * the chat path is the same list: tools.get_starter_chore_list() reads
    the saved profile, the pets and the adults and hands the model the
    rows to read back.

Every row leaves here with an owner (mode + owner_name), a frequency and a
category, so the household changes any of the three or drops the row
before saving — nothing here writes to the database. `frequency_label` is
the rhythm in the household's words (FREQUENCY_WORDS in chores.py; the
same words static/shell.js prints on Plan | Chores) and `basis` says which
fact put the row there ('home', 'rooms', 'yard', 'pets', 'always',
'help', 'notes') so a screen can say "because you have a yard".

Scope, per the card: keeping the home running — cleaning by room,
laundry, garbage / recycling / green bin, yard if there is one, pet CARE
if there are pets (not pet food — that is the grocery list's), a few
seasonal transitions at a twice-a-year rhythm and a monthly tidy-and-
donate pass. Not admin, errands or appointments.

Owner rule (simple, and stated so it can be argued with): rows are dealt
round the rotation in the order setup named them, top to bottom, one row
each; a row the household's help covers is tagged outsourced and skips
the deal; with nobody named, every row is 'whoever'. It is a proposal —
the point is that no row arrives nobody's.
"""
from __future__ import annotations

import re

from ._shared import household_adults
from .chores import FREQUENCY_WORDS, _FREQUENCY_DAYS
# Module, not name (CLAUDE.md on the tools package): read at call time.
from . import chores as _chores
from . import household as _household

# The cleanliness standard moves a scaling row one step along this ladder:
# relaxed = one step less often, meticulous = one step more often. Only
# the rows marked `scales` move — bins go out when the truck comes, a flea
# dose is monthly whatever the standard, and a gutter is a gutter. Weekly
# is the floor on purpose: "meticulous" never turns the vacuuming daily.
_LADDER = ("weekly", "biweekly", "monthly", "quarterly")

# The two rows the model may never drop, whatever the notes say (the
# household still can, by hand). Matched by casefolded name.
NEVER_DROPPED = frozenset({"laundry", "garbage out"})

# Words a household uses for the help that comes in, and which rows that
# help covers. Free text is imperfect — this is the deterministic first
# pass; the model's adjust step reads the same sentence and can re-tag
# what these keywords miss ("Maria does the bathrooms and the floors").
_HELP_COVERS = (
    (re.compile(r"clean|housekeep|maid"), "cleaning_visit"),
    (re.compile(r"lawn|mow|yard|garden|landscap"), "yard"),
    (re.compile(r"laundry|wash and fold|wash-and-fold"), "laundry"),
    (re.compile(r"window"), "windows"),
    (re.compile(r"gutter"), "gutters"),
    (re.compile(r"groom"), "grooming"),
    (re.compile(r"walker|dog walk"), "walks"),
)

# How often the help comes, in their words, to a stored rhythm.
_HELP_FREQUENCY = (
    (re.compile(r"every ?day|daily"), "daily"),
    (re.compile(r"every (other|second|two|2|couple)|bi-?weekly|fortnight|twice a month"), "biweekly"),
    (re.compile(r"week"), "weekly"),
    (re.compile(r"month"), "monthly"),
)

_ORDINALS = ("Main", "Second", "Third", "Fourth", "Fifth")


def _norm(text) -> str:
    return (text or "").strip().lower()


def _is_house(home_type: str) -> bool:
    """'House' (and townhouse, semi, bungalow…) as opposed to an apartment or condo."""
    t = _norm(home_type)
    if not t:
        return False
    return not re.search(r"apartment|condo|flat|unit|suite", t)


def _step(frequency: str, standard: str) -> str:
    """One rung less often for 'relaxed', one rung more often for 'meticulous'."""
    if frequency not in _LADDER:
        return frequency
    i = _LADDER.index(frequency)
    if standard == "relaxed":
        i = min(i + 1, len(_LADDER) - 1)
    elif standard == "meticulous":
        i = max(i - 1, 0)
    return _LADDER[i]


def _help_frequency(text: str) -> str | None:
    t = _norm(text)
    for pattern, frequency in _HELP_FREQUENCY:
        if pattern.search(t):
            return frequency
    return None


def _help_groups(existing_help: str) -> set[str]:
    t = _norm(existing_help)
    if not t or t in ("no", "none", "nobody", "n/a", "nope"):
        return set()
    return {group for pattern, group in _HELP_COVERS if pattern.search(t)}


def _row(name: str, frequency: str, category: str, basis: str, *, group: str = "", scales: bool = False) -> dict:
    return {
        "name": name, "frequency": frequency, "category": category, "basis": basis,
        "_group": group, "_scales": scales,
    }


def _pet_kind(pet_type: str) -> str:
    t = _norm(pet_type)
    if re.search(r"dog|pupp|hound|retriever|spaniel|terrier|poodle", t):
        return "dog"
    if re.search(r"cat|kitten", t):
        return "cat"
    if re.search(r"fish|aquarium|turtle", t):
        return "tank"
    if re.search(r"bird|parrot|budgie|canary|cockatiel", t):
        return "bird"
    if re.search(r"rabbit|bunny|guinea|hamster|gerbil|rat|mouse|mice|chinchilla|ferret", t):
        return "small"
    if re.search(r"snake|lizard|gecko|reptile|bearded|iguana|tortoise|frog", t):
        return "reptile"
    return "other"


def _pet_rows(pets: list[dict]) -> list[dict]:
    """
    Recurring CARE for the pets on file, at real frequencies: daily walks
    or litter, weekly bedding/cage/tank, a monthly flea and tick dose, a
    yearly vet visit, grooming at the animal's cadence. Named for the
    animal when there is one of its kind ("Walk Biscuit"), for the kind
    when there are several ("Walk the dogs"). Feeding is left off on
    purpose — it is not a thing a list reminds a person of — and pet
    FOOD belongs to the grocery list, not here.
    """
    by_kind: dict[str, list[str]] = {}
    for p in pets or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        by_kind.setdefault(_pet_kind(str(p.get("pet_type") or "")), []).append(name)

    rows = []
    for kind, names in by_kind.items():
        one = len(names) == 1
        plural = {
            "dog": "the dogs", "cat": "the cats", "tank": "the tanks", "bird": "the birds",
            "small": "the small animals", "reptile": "the reptiles", "other": "the pets",
        }[kind]
        # "Walk Biscuit"; an unnamed single animal is "the dog"; several are "the dogs".
        who = (names[0] or plural[:-1]) if one else plural
        # "Biscuit's bed" / "the dogs' beds": one possessive per row, not a rule.
        poss = f"{who}'s" if one else f"{plural}'"
        p = "pets"
        if kind == "dog":
            rows.append(_row(f"Walk {who}", "daily", "other", p, group="walks"))
            rows.append(_row(f"Wash {poss} bed and bowls" if one else f"Wash {poss} beds and bowls", "weekly", "cleaning", p))
            rows.append(_row(f"Flea and tick dose for {who}", "monthly", "other", p))
            rows.append(_row(f"Grooming for {who}", "monthly", "other", p, group="grooming"))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
        elif kind == "cat":
            rows.append(_row("Scoop the litter box" if one else "Scoop the litter boxes", "daily", "cleaning", p))
            rows.append(_row("Change the litter", "weekly", "cleaning", p))
            rows.append(_row(f"Brush {who}", "weekly", "other", p, group="grooming"))
            rows.append(_row(f"Flea and tick dose for {who}", "monthly", "other", p))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
        elif kind == "tank":
            rows.append(_row(f"Clean {poss} tank" if one else "Clean the tanks", "biweekly", "cleaning", p))
        elif kind == "bird":
            rows.append(_row(f"Clean {poss} cage" if one else "Clean the bird cages", "weekly", "cleaning", p))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
        elif kind == "small":
            rows.append(_row(f"Clean {poss} cage" if one else "Clean the cages", "weekly", "cleaning", p))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
        elif kind == "reptile":
            rows.append(_row(f"Clean {poss} tank" if one else "Clean the tanks", "weekly", "cleaning", p))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
        else:
            rows.append(_row(f"Clean up after {who}", "weekly", "cleaning", p))
            rows.append(_row(f"Vet checkup for {who}", "yearly", "other", p))
    return rows


def _bathroom_rows(bathrooms: int) -> list[dict]:
    """One row per bathroom while the names stay sayable, else one row for all of them."""
    n = max(int(bathrooms or 0), 0)
    if n <= 1:
        return [_row("Bathroom", "weekly", "cleaning", "rooms", group="cleaning_visit", scales=True)]
    if n <= len(_ORDINALS):
        return [
            _row(f"{_ORDINALS[i]} bathroom", "weekly", "cleaning", "rooms", group="cleaning_visit", scales=True)
            for i in range(n)
        ]
    return [_row(f"All {n} bathrooms", "weekly", "cleaning", "rooms", group="cleaning_visit", scales=True)]


def _baseline_rows(profile: dict) -> list[dict]:
    home_type = str(profile.get("home_type") or "")
    house = _is_house(home_type)
    has_yard = bool(profile.get("has_yard"))
    pets = profile.get("pets") or []

    rows: list[dict] = []
    # Cleaning, by room. The kitchen and the floors are every home's.
    rows.append(_row("Kitchen counters and sink", "daily", "cleaning", "always"))
    rows.append(_row("Dishes", "daily", "cleaning", "always"))
    rows.extend(_bathroom_rows(profile.get("bathrooms") or 0))
    rows.append(_row("Vacuum", "weekly", "cleaning", "always", group="cleaning_visit", scales=True))
    rows.append(_row("Mop the hard floors", "biweekly", "cleaning", "always", group="cleaning_visit", scales=True))
    rows.append(_row("Dust", "biweekly", "cleaning", "always", group="cleaning_visit", scales=True))
    rows.append(_row("Change the sheets", "biweekly", "cleaning", "rooms", scales=True))
    rows.append(_row("Clean out the fridge", "monthly", "cleaning", "always"))
    # Laundry and the bins — the two the model may never drop.
    rows.append(_row("Laundry", "weekly", "cleaning", "always", group="laundry"))
    rows.append(_row("Garbage out", "weekly", "other", "always"))
    rows.append(_row("Recycling out", "weekly", "other", "always"))
    rows.append(_row("Green bin out", "weekly", "other", "always"))
    # Yard, only where there is one.
    if has_yard:
        rows.append(_row("Mow the lawn", "weekly", "maintenance", "yard", group="yard"))
        rows.append(_row("Weed and tidy the garden beds", "biweekly", "maintenance", "yard", group="yard"))
    # Pets, only where there are some.
    rows.extend(_pet_rows(pets))
    # The monthly pass and the deep or seasonal items, at their real rhythm.
    rows.append(_row("Tidy and donate", "monthly", "other", "always"))
    rows.append(_row("Clean the oven", "quarterly", "cleaning", "always"))
    rows.append(_row("Test the smoke detectors", "semiannual", "maintenance", "always"))
    rows.append(_row("Swap the closets for the season", "semiannual", "other", "always"))
    rows.append(_row("Wash the windows", "semiannual", "cleaning", "always", group="windows"))
    if house:
        rows.append(_row("Change the furnace filter", "quarterly", "maintenance", "home"))
        rows.append(_row("Clean the gutters", "semiannual", "maintenance", "home", group="gutters"))
        rows.append(_row("Winter tires on / off", "semiannual", "maintenance", "home"))
    if has_yard:
        rows.append(_row("Patio furniture out / in", "semiannual", "other", "yard"))
    return rows


def starter_chore_list(profile: dict) -> list[dict]:
    """
    The proposed list for a household, from its profile facts. Pure: reads
    nothing, writes nothing. `profile` carries home_type, bedrooms,
    bathrooms, has_yard, standard, rotation_members, pets (list of
    {name, pet_type}), existing_help and existing_help_frequency — the
    shape /api/onboarding/chores/recommend builds and set_chores_profile
    stores. Missing keys mean "not known", and an unknown home gets the
    rows every home has.

    Each row: name, frequency, frequency_label, category, mode,
    owner_name, assignee_names, outsourced_to, basis.
    """
    standard = _norm(profile.get("standard")) or "standard"
    people = [str(n).strip() for n in (profile.get("rotation_members") or []) if str(n or "").strip()]
    help_text = str(profile.get("existing_help") or "").strip()
    covered = _help_groups(help_text)
    help_frequency = _help_frequency(str(profile.get("existing_help_frequency") or ""))

    out = []
    deal = 0
    for raw in _baseline_rows(profile):
        frequency = _step(raw["frequency"], standard) if raw["_scales"] else raw["frequency"]
        row = {
            "name": raw["name"],
            "frequency": frequency,
            "frequency_label": FREQUENCY_WORDS.get(frequency, frequency),
            "category": raw["category"],
            "mode": "whoever",
            "owner_name": "",
            "assignee_names": [],
            "outsourced_to": "",
            "basis": raw["basis"],
        }
        if raw["_group"] and raw["_group"] in covered:
            # The help the household already told us about does this one.
            # It stays on the list — knowing Thursday is cleaner day is
            # the point — tagged in their words, and nobody here is dealt it.
            row["mode"] = "outsourced"
            row["outsourced_to"] = help_text
            row["basis"] = "help"
            if help_frequency and raw["_group"] == "cleaning_visit":
                row["frequency"] = help_frequency
                row["frequency_label"] = FREQUENCY_WORDS.get(help_frequency, help_frequency)
        elif people:
            owner = people[deal % len(people)]
            deal += 1
            row["mode"] = "owned"
            row["owner_name"] = owner
            row["assignee_names"] = [owner]
        out.append(row)
    return out


def with_frequency_label(row: dict) -> dict:
    """The rhythm in the household's words, added to any row that lacks it."""
    frequency = row.get("frequency") if row.get("frequency") in _FREQUENCY_DAYS else "weekly"
    row["frequency"] = frequency
    row["frequency_label"] = FREQUENCY_WORDS.get(frequency, frequency)
    return row


def frequency_choices() -> list[dict]:
    """Every rhythm the schedule supports, in order, with its words — for a picker."""
    return [{"value": f, "label": FREQUENCY_WORDS[f]} for f in _FREQUENCY_DAYS if f in FREQUENCY_WORDS]


def known_for_chores() -> dict:
    """
    What Pomona already knows that a chores setup must not ask again: the
    people (with age group) and the adults it can propose as owners, the
    pets on file, and the home facts if a chores profile was ever saved.
    `home.known` is False until one was — the screen asks then, and only
    then. `frequencies` is every rhythm with its words, for a picker.
    Read-only.
    """
    profile = _chores.get_chores_profile()
    status = _household.get_household_setup_status()
    home_known = bool(profile.get("has_profile")) and bool(profile.get("home_type"))
    return {
        "people": [{"name": m["name"], "age_group": m.get("age_group", "")} for m in status["members"]],
        "adults": [a["name"] for a in household_adults() if a["name"]],
        "pets": [{"name": p["name"], "pet_type": p["pet_type"]} for p in status["pets"]],
        "home": {
            "known": home_known,
            "home_type": profile.get("home_type", "") if home_known else "",
            "bedrooms": profile.get("bedrooms", 0) if home_known else 0,
            "bathrooms": profile.get("bathrooms", 0) if home_known else 0,
            "has_yard": bool(profile.get("has_yard")) if home_known else False,
        },
        "profile": profile,
        "frequencies": frequency_choices(),
    }


def profile_for_starter(overrides: dict | None = None) -> dict:
    """
    The profile the starter list is built from: the saved chores profile
    underneath, whatever the caller answered just now on top (a None or
    missing key means "use what's saved"), the pets from the pets table,
    and — when nobody was named for the rotation — the household's adults.
    Setup never asks again for what is already here; only the genuinely
    new answers arrive in `overrides`.
    """
    saved = _chores.get_chores_profile()
    profile = {
        "home_type": saved.get("home_type", ""),
        "bedrooms": saved.get("bedrooms", 0),
        "bathrooms": saved.get("bathrooms", 0),
        "has_yard": bool(saved.get("has_yard", False)),
        "standard": saved.get("standard", "") or "standard",
        "rotation_members": list(saved.get("rotation_members") or []),
        "existing_help": saved.get("existing_help", ""),
        "existing_help_frequency": saved.get("existing_help_frequency", ""),
        "include_notes": saved.get("include_notes", ""),
        "exclude_notes": saved.get("exclude_notes", ""),
    }
    for key, value in (overrides or {}).items():
        if key in profile and value is not None:
            profile[key] = value
    profile["pets"] = _household.list_pets()
    profile["goals"] = _household.get_household_setup_status().get("goals", "")
    if not [n for n in profile["rotation_members"] if isinstance(n, str) and n.strip()]:
        profile["rotation_members"] = [a["name"] for a in household_adults() if a["name"]]
    return profile


def get_starter_chore_list() -> dict:
    """
    The chat's door onto the same list: the starter chores Pomona proposes
    from what it already knows — the saved chores profile (set_chores_profile
    / get_chores_profile), the pets on file and the household's adults.
    Rule-based, and nothing is saved by calling it. Read the rows back to
    the household with each one's proposed owner and its rhythm in words
    (frequency_label), take their changes, then add_chore each row they
    keep and generate_chore_schedule. `profile` is what the list was built
    from, so a missing fact (no home type yet) is visible rather than
    silently assumed.
    """
    profile = profile_for_starter()
    rows = starter_chore_list(profile)
    return {
        "chores": rows,
        "count": len(rows),
        "profile": {k: v for k, v in profile.items() if k != "goals"},
        "nothing_saved_yet": True,
    }
