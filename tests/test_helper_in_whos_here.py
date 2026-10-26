"""
A helper ("Someone not eating here" — a nanny, a grandparent who helps with
dinners; members.eats_here = 0) has a row in Preferences -> Who's here
(defect hunt, 2026-10-20).

Who's here lists the people meals are planned for, so a helper invited at
setup appeared nowhere in Preferences: nobody could see whether they had
joined, or send them a new link once the first one ran out after its week.
The invite read (/api/household/invites) now says which adults eat here, and
the section draws a row for each one who doesn't: their name, "Helps out",
and the same invite row the adults above it carry — no age chips, no
restrictions, no "Make main person".

The rendering half is RUN under node with the real inviteHelpersHtml,
inviteRowHtml and wwkPeopleHtml (tests/test_settings_main_person_row.py's
block, with only the helpers stub swapped for the real thing).
"""
from __future__ import annotations

import json

from tests import nodeharness
from tests.test_settings_main_person_row import SHELL_JS, _function, _people_block


def _household(signed_in):
    res = signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "adult"}, {"name": "Greg", "age_group": "adult"},
                    {"name": "Reid", "age_group": "child", "age_years": 6}],
        "pets": [], "goals": "",
    })
    assert res.status_code == 200, res.text


# --- the server half -----------------------------------------------------------

def test_the_invite_read_says_which_adults_eat_here(signed_in):
    _household(signed_in)
    res = signed_in.post("/api/household/invites", json={"name": "Maria", "eats_here": False})
    assert res.status_code == 200, res.text
    adults = {a["name"]: a for a in signed_in.get("/api/household/invites").json()["adults"]}
    assert set(adults) == {"Emily", "Greg", "Maria"}
    assert adults["Maria"]["eats_here"] is False
    assert adults["Emily"]["eats_here"] is True and adults["Greg"]["eats_here"] is True
    # Still not one of the people meals are planned for — the row below is
    # drawn from the invite read, never by putting her back in this list.
    assert "Maria" not in [m["name"] for m in signed_in.get("/api/memory").json()["members"]]


# --- the rendering half ----------------------------------------------------------

MEMORY = {
    "members": [
        {"id": 7, "name": "Emily", "age_group": "adult", "dietary_restrictions": [], "is_primary": True},
        {"id": 8, "name": "Greg", "age_group": "adult", "dietary_restrictions": [], "is_primary": False},
    ]
}


def _render(adults: list[dict], memory: dict = MEMORY) -> str:
    block = _people_block().replace(
        "function inviteHelpersHtml() { return ''; }\n",
        "var inviteState = %s;\n" % json.dumps({"adults": adults, "newOpen": False, "busy": False, "ready": {}})
        + _function("inviteRowHtml") + "\n" + _function("inviteRemoveHtml") + "\n"
        + _function("inviteHelpersHtml") + "\n",
    )
    assert "function inviteHelpersHtml() {\n" in block, "the real helpers row, not the stub"
    res = nodeharness.run_node(block + "console.log(JSON.stringify(wwkPeopleHtml(%s)));\n" % json.dumps(memory))
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return json.loads(res.stdout.strip())


def _adult(id_, name, *, eats_here=True, joined=False, is_you=False):
    return {"id": id_, "name": name, "is_you": is_you, "joined": joined,
            "joined_label": "Oct 12" if joined else "", "eats_here": eats_here}


def _helper_rows(html: str) -> list[str]:
    return html.split('<div class="wwk-person wwk-helper">')[1:]


def test_a_helper_who_has_not_joined_gets_a_row_with_an_invite():
    html = _render([_adult(7, "Emily", is_you=True), _adult(8, "Greg"), _adult(11, "Maria", eats_here=False)])
    rows = _helper_rows(html)
    assert len(rows) == 1, html
    row = rows[0]
    assert "Maria" in row and "Helps out" in row
    assert 'data-invite="send" data-member-id="11"' in row and "Invite Maria" in row
    # Nothing about eating: she isn't planned for.
    assert 'data-member="Maria"' not in html, "no age chips or restrictions for a helper"
    assert "Make main person" not in row


def test_a_helper_who_joined_reads_joined_with_a_new_link_on_offer():
    html = _render([_adult(7, "Emily", is_you=True), _adult(11, "Maria", eats_here=False, joined=True)])
    row = _helper_rows(html)[0]
    assert "Joined Oct 12" in row and "Send a new link" in row


def test_the_helper_looking_at_their_own_row_is_not_offered_an_invite():
    html = _render([_adult(7, "Emily"), _adult(11, "Maria", eats_here=False, is_you=True)])
    row = _helper_rows(html)[0]
    assert "Maria" in row and "data-invite" not in row


def test_adults_who_eat_here_are_not_drawn_twice():
    # eats_here true — and a payload from before the field existed — draw no helper row.
    legacy = {"id": 8, "name": "Greg", "is_you": False, "joined": False, "joined_label": ""}
    html = _render([_adult(7, "Emily", is_you=True), legacy])
    assert _helper_rows(html) == []
    assert "wwk-helper" not in html


def test_who_s_here_asks_for_the_helper_rows():
    assert "inviteHelpersHtml() + inviteNewHtml()" in _function("wwkPeopleHtml", SHELL_JS)
