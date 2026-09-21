"""Two people with the same initial — tell them apart wherever initials appear.

Loop Board card (Emily, 2026-09-21; Needs Your Call — the recommended
default is built). The rule: when two people's first letters clash, the
colliding people — and only they — get the shortest prefix that tells them
apart. Emily / Ethan → Em / Et; Emily / Emma → Emi / Emm; a household with
no clash sees no change.

The rule lives in ONE place (tools.display_initials, in app/tools/_shared.py)
and every surface that draws an initial reads it: the day sheet's rows
(static/plan-week.html, through /api/week/{week}/attendance's members), the
"Who's this?" pick (static/shell.js, through /api/whoami's adults) and
/api/people's avatars. Screen readers get the full name, never the
abbreviation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import tools
from app.tools._shared import display_initials

REPO = Path(__file__).resolve().parents[1]
PAGE = (REPO / "static" / "plan-week.html").read_text(encoding="utf-8")
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")
SERVER = "".join(
    (REPO / "app" / "tools" / name).read_text(encoding="utf-8")
    for name in ("_shared.py", "coordination.py", "household.py")
)


# ==========================================================================
# The rule
# ==========================================================================

@pytest.mark.parametrize("names,expected", [
    (["Emily", "Vic"], ["E", "V"]),
    (["Emily", "Ethan"], ["Em", "Et"]),
    (["Emily", "Emma"], ["Emi", "Emm"]),
    (["Emily", "Ethan", "Vic"], ["Em", "Et", "V"]),
    (["Emily", "Ethan", "Vic", "Vera"], ["Em", "Et", "Vi", "Ve"]),
    (["Emily", "Emma", "Ethan"], ["Emi", "Emm", "Et"]),
    (["Sam", "Samantha"], ["Sam", "Sama"]),
    (["Sam", "Sam"], ["Sam", "Sam"]),
    (["emily", "ETHAN"], ["Em", "ET"]),
    # Accents are folded for the comparison and kept in what is shown.
    (["Émile", "Emma"], ["Émi", "Emm"]),
    (["Émile", "Ethan"], ["Ém", "Et"]),
    (["Émile", "Vic"], ["É", "V"]),
    (["Zoë", "Zoe"], ["Zoë", "Zoe"]),
    (["Ödön", "Olga"], ["Öd", "Ol"]),
    (["", "Bob"], ["?", "B"]),
    (["Emily"], ["E"]),
    ([], []),
])
def test_the_shortest_distinct_prefix_for_the_colliding_people_only(names, expected):
    assert display_initials(names) == expected


def test_the_rule_is_positional_and_case_insensitive():
    assert display_initials([" Ethan ", "emily"]) == ["Et", "Em"]


# ==========================================================================
# One place, every surface
# ==========================================================================

@pytest.fixture
def emily_and_ethan():
    tools.add_member("Emily")
    tools.add_member("Ethan")
    tools.set_member_age_group("Emily", "adult")
    tools.set_member_age_group("Ethan", "adult")


def test_the_households_members_carry_their_display_initial(emily_and_ethan):
    assert [(m["name"], m["initial"]) for m in tools.list_members()] == [("Emily", "Em"), ("Ethan", "Et")]
    assert [(a["name"], a["initial"]) for a in tools.household_adults()] == [("Emily", "Em"), ("Ethan", "Et")]
    assert [(p["name"], p["initial"]) for p in tools.get_household_people()] == [("Emily", "Em"), ("Ethan", "Et")]
    ids = {m["name"]: m["id"] for m in tools.list_members()}
    assert tools.household_initials() == {ids["Emily"]: "Em", ids["Ethan"]: "Et"}


def test_a_household_with_no_clash_sees_no_change():
    tools.add_member("Emily")
    tools.add_member("Vic")
    assert [m["initial"] for m in tools.list_members()] == ["E", "V"]


def test_a_child_who_shares_a_letter_counts_too():
    # The day sheet has a row for everyone, so a parent and a child who
    # share a letter are told apart there — and, for one identity
    # everywhere, on the adults-only surfaces as well.
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Ethan")
    tools.set_member_age_group("Ethan", "child")
    assert [m["initial"] for m in tools.list_members()] == ["Em", "Et"]
    assert [a["initial"] for a in tools.household_adults()] == ["Em"]


def test_the_three_routes_hand_the_same_initials_out(signed_in, emily_and_ethan):
    week = signed_in.get("/api/week/2027-03-01/attendance").json()
    assert [(m["name"], m["initial"]) for m in week["members"]] == [("Emily", "Em"), ("Ethan", "Et")]
    who = signed_in.get("/api/whoami").json()
    assert [(a["name"], a["initial"]) for a in who["adults"]] == [("Emily", "Em"), ("Ethan", "Et")]
    people = signed_in.get("/api/people").json()["people"]
    assert [(p["name"], p["initial"]) for p in people] == [("Emily", "Em"), ("Ethan", "Et")]


def test_no_surface_computes_a_first_letter_of_its_own():
    # The one rule, on the server; nothing else slices a name for a letter.
    assert SERVER.count("def display_initials(") == 1
    assert '[:1] or "?"' not in SERVER.replace('(name[:1] or "?").upper()', "", 1), \
        "a first-letter fallback outside _member_row_dict's last resort"
    assert "initialFor" not in PAGE
    # The day sheet and the "Who's this?" pick both read the server's initial.
    assert "m.initial || (m.name || '?').charAt(0).toUpperCase()" in PAGE
    assert "a.initial || (a.name || '?').charAt(0).toUpperCase()" in SHELL_JS


def test_the_circle_grows_and_the_name_is_read_out():
    who_row = SHELL_CSS[SHELL_CSS.index(".who-row-initial {"):SHELL_CSS.index(".who-row-name {")]
    assert "min-width: 40px" in who_row and "width: 40px;" not in who_row.replace("min-width: 40px", "")
    assert "padding: 0 9px" in who_row
    # The "Who's this?" pick draws the initial for the eye and the name beside it.
    assert '<span class="who-row-initial" aria-hidden="true">' in SHELL_JS
    assert '<span class="who-row-name">' in SHELL_JS
    # The day sheet: the initial hidden from AT, the full name for it, and
    # the pills named for the person.
    assert '<span class="who-avatar" aria-hidden="true">' in PAGE
    assert "'<span class=\"sr-only\">' + esc(m.name) + '</span>'" in PAGE
