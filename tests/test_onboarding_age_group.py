"""
Onboarding must know which household members are kids.

Loop Board ticket (Emily, 2026-09-04): onboarding never asked whether each
person was an adult or a child, so the household-rhythm step offered a kid
as the cook and asked them where they eat lunch on a normal weekday, same
as anyone else. The fix adds an age-group chip per member row in
static/onboarding.html and gates the rhythm step's cook/lunch-location
questions in JS — untestable here (no JS harness in this repo; see
test_onboarding_inputs.py's docstring for that limit and the manual-check
convention it follows).

What *is* testable at this layer is the server-side half: the wizard now
calls POST /api/onboarding/household (with each member's age_group)
alongside the existing POST /api/onboarding/answers (which only ever
carried bare names). That household endpoint, and the
add_member/set_member_age_group tools underneath it, already existed and
already worked (app/main.py's onboarding_household, ~line 626) — this
pins that calling it with real onboarding data actually lands age_group in
the members table, the same way a household's answers reaching
/api/onboarding/answers already do.

Manual check for the JS gating itself (rhythm-step chips, done once in a
real browser against this branch, not automated):
1. Add three people on the "Who am I cooking for" step: an adult (default),
   a second adult, and a child (tap the "Child" chip for the third row).
2. Continue to the rhythm step. Confirm the lunch-location question only
   lists the two adults, and that "Mostly one person" -> "Who?" only offers
   the two adults as options, not the child.
3. Remove the two adults, leaving only the child, and confirm the lunch
   card disappears (not an empty box) and Continue is never blocked waiting
   on a "Who?" pick with no candidates.
"""
from __future__ import annotations

from app import tools


def test_onboarding_household_post_saves_age_group_per_member(signed_in):
    """
    The wizard now sends the age-group chip's value for every member to
    POST /api/onboarding/household right after the household step, before
    the rhythm step (which needs it) is even built. This is the save path
    that made age_group land in the DB before this ticket started (chores
    rotation and set_member_age_group already used it) — the gap being
    fixed was that onboarding.html was never calling it with real members.
    """
    res = signed_in.post(
        "/api/onboarding/household",
        json={
            "members": [
                {"name": "Robin", "age_group": "adult"},
                {"name": "Sam", "age_group": "teen"},
                {"name": "Wren", "age_group": "child"},
                {"name": "Bug", "age_group": "toddler"},
            ],
            "pets": [],
            "goals": "",
        },
    )
    assert res.status_code == 200

    status = signed_in.get("/api/onboarding/status").json()
    by_name = {m["name"]: m["age_group"] for m in status["household"]["members"]}
    assert by_name == {
        "Robin": "adult",
        "Sam": "teen",
        "Wren": "child",
        "Bug": "toddler",
    }


def test_onboarding_answers_and_household_post_together_populate_the_same_members(signed_in):
    """
    In the real wizard both calls fire during one pass through onboarding —
    /api/onboarding/household when the household step is left (carrying
    age_group), then /api/onboarding/answers later (carrying the fuller
    7-question set, keyed by the same names, but no age_group of its own).
    They must land on the same member rows rather than each creating its
    own, since add_member/_get_or_create_member match by name
    case-insensitively.
    """
    signed_in.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Robin", "age_group": "adult"}, {"name": "Wren", "age_group": "child"}]},
    )
    signed_in.post(
        "/api/onboarding/answers",
        json={
            "member_names": ["Robin", "Wren"],
            "household_restrictions": {},
            "eating_style": "",
            "wont_eat": [],
            "excited_about": [],
            "dinners_per_week": 5,
        },
    )

    members = tools.list_members()
    assert len(members) == 2, "the answers POST must not create duplicate member rows for the same names"

    status = tools.get_household_setup_status()
    by_name = {m["name"]: m["age_group"] for m in status["members"]}
    assert by_name == {"Robin": "adult", "Wren": "child"}
