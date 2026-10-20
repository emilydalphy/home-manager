"""
Removing one restriction in Settings removes exactly that one
(2026-10-20, overnight defect hunt).

Settings removed a chip by resending its own copy of the member's whole
list under replace=True. An allergy added after the sheet opened — by the
member through their share link, or by the other adult on another phone —
was not in that copy, so removing any other chip erased it, and the
planner's hard gate stopped holding that food back. The chip remove now
names the one restriction (POST /api/memory/member/restrictions/remove)
and the server edits the list as stored.
"""
from app import tools


def _priya():
    return next(m for m in tools.list_members() if m["name"] == "Priya")["dietary_restrictions"]


def _setup(signed_in):
    tools.add_member("Priya")
    signed_in.post("/api/memory/member/restrictions",
                   json={"name": "Priya", "restrictions": ["vegetarian", "peanut allergy"]})
    # The sheet opens now: this is the list its screen holds.
    return tools.get_or_create_member_share_link("Priya")["token"]


def test_removing_one_chip_keeps_an_allergy_added_elsewhere_since(signed_in):
    """CATCH. Before, the sheet sent its stale list with replace=True and
    sesame was gone; the route did what it was told."""
    token = _setup(signed_in)
    # Priya adds sesame on her own phone while the parent's sheet is open.
    assert signed_in.post(f"/api/member-share/{token}/restriction",
                          json={"restriction": "sesame allergy"}).status_code == 200

    res = signed_in.post("/api/memory/member/restrictions/remove",
                         json={"name": "Priya", "restriction": "vegetarian"})

    assert res.status_code == 200
    assert _priya() == ["peanut allergy", "sesame allergy"]
    # The sheet redraws from this answer, so the parent sees sesame appear.
    answered = next(m for m in res.json()["members"] if m["name"] == "Priya")
    assert answered["dietary_restrictions"] == ["peanut allergy", "sesame allergy"]


def test_a_settings_add_keeps_an_allergy_added_elsewhere_and_is_trimmed(signed_in):
    """GUARD. The add path merges against the stored list; padding goes."""
    token = _setup(signed_in)
    signed_in.post(f"/api/member-share/{token}/restriction", json={"restriction": "sesame allergy"})

    res = signed_in.post("/api/memory/member/restrictions",
                         json={"name": "Priya", "restrictions": [" Shellfish "], "replace": False})

    assert res.status_code == 200
    assert _priya() == ["vegetarian", "peanut allergy", "sesame allergy", "Shellfish"]


def test_remove_matches_trimmed_and_case_blind_and_refuses_a_stranger(signed_in):
    """GUARD. An untrimmed chip saved before this still comes off; a stranger
    is a 404 with nobody created, like its neighbour routes."""
    _setup(signed_in)
    tools.set_member_dietary_restrictions("Priya", [" Peanut Allergy "], replace=False)  # dupe: no-op
    res = signed_in.post("/api/memory/member/restrictions/remove",
                         json={"name": "priya", "restriction": "  PEANUT ALLERGY"})
    assert res.status_code == 200
    assert _priya() == ["vegetarian"]

    stranger = signed_in.post("/api/memory/member/restrictions/remove",
                              json={"name": "Sam", "restriction": "vegetarian"})
    assert stranger.status_code == 404
    assert [m["name"] for m in tools.list_members()] == ["Priya"]
