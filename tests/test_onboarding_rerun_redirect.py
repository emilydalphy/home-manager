"""
Loop Board card: re-running setup (/onboarding) on a set-up household starts
blank and adds rather than edits.

A household that had already finished setup could still land back on
/onboarding — a bookmark, a shared link, the browser's back/forward, the
gear's "Household" link before it pointed anywhere else. The wizard always
starts from a blank "Who's in your household?" step, and
POST /api/onboarding/household -> add_member is get-or-create BY NAME:
running the whole flow again with a name spelled even slightly differently
("Emily" typed as "Em") adds a second person rather than editing the one
already on record. There was no way back out — nothing in this app deletes
a member.

The fix: GET /onboarding now checks the same `has_members` signal
/api/onboarding/status reports and shell.js's own boot check
(checkOnboarding, static/shell.js) uses to decide whether to *send* someone
to onboarding in the first place. A household that already has members is
sent on to the Preferences sheet instead (/week?prefs=open — it was
/meal-setup until that page was folded into Preferences, 2026-09-25) —
rather than being handed the blank wizard again.

The two tests below are the server-side half of the card and fail on main
(where GET /onboarding always 200s with the wizard, no matter how set up the
household already is) and pass on this branch.

The card's other two acceptance criteria turned out to already be true on
main, characterised by existing tests rather than needing new code:
- The Who screen's Continue with no names already shows an inline line
  instead of doing nothing (showHouseholdEmptyNote /
  #household-empty, static/onboarding.html) — see
  tests/test_onboarding_go_back.py's
  test_the_empty_household_is_told_in_a_line_on_the_step_not_an_alert and
  test_setup_cannot_complete_with_nobody_in_the_household.
- /api/onboarding/household already never creates a second member for a
  name that already exists (case-insensitive get-or-create,
  app/tools/household.py's _get_or_create_member) and already ignores blank
  names (the `if not m.name.strip(): continue` guard in
  app/main.py's onboarding_household). test_a_corrected_name_would_leave_
  two_of_the_same_person in test_onboarding_go_back.py already exercises the
  re-post path but only with names that differ; the two tests below add the
  same-name and blank-name cases the card specifically asks for.
"""
from __future__ import annotations

from app import tools


def test_rerunning_onboarding_on_a_finished_household_redirects_to_preferences(signed_in):
    """
    has_members becomes true the moment the household step is saved — the
    same signal the shell's own boot check reads to decide whether to send
    someone *to* /onboarding. Once it's true, /onboarding sends them
    somewhere they can see and change what's on record instead of handing
    them a second blank wizard.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Robin", "age_group": "adult"}],
    })
    res = signed_in.get("/onboarding", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/week?prefs=open"


def test_onboarding_still_opens_for_a_household_that_has_not_set_up_yet(signed_in):
    """The other half: a genuinely new household (no members yet) still gets
    the wizard, not bounced somewhere it has nothing to show yet."""
    res = signed_in.get("/onboarding", follow_redirects=False)
    assert res.status_code == 200
    assert "onboarding" in res.text.lower() or "Pomona" in res.text


def test_household_route_never_creates_a_second_member_for_the_same_name(signed_in):
    """
    Re-running setup and typing the SAME name again (not a correction) must
    not double a person. _get_or_create_member's case-insensitive match
    already covers this; this pins it at the route the wizard actually
    calls, and across the mixed case a real re-run is likely to produce.
    """
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Robin", "age_group": "adult"}],
    })
    signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "robin", "age_group": "adult"}],
    })
    names = [m["name"] for m in tools.list_members()]
    assert names == ["Robin"], "the same person, typed back in a different case, got a second row"


def test_household_route_ignores_blank_names(signed_in):
    """A blank or whitespace-only name is nothing to save, not an empty
    person — the route already skips it (main.py's `if not m.name.strip():
    continue`); this pins that a re-run posting a mix of real and blank rows
    only writes the real ones."""
    signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Robin", "age_group": "adult"},
            {"name": "   ", "age_group": "adult"},
            {"name": "", "age_group": "adult"},
        ],
    })
    names = [m["name"] for m in tools.list_members()]
    assert names == ["Robin"]
