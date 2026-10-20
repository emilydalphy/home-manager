"""
Settings' age chip only stores a real age group, and never leaves the
household without an adult (2026-10-20, overnight defect hunt).

Reproduced over HTTP on a throwaway database: POST
/api/memory/member/age-group stored "banana" with a 200; and with two
adults, Emily marking herself Teen and then Vineeth Child left /api/whoami
with `adults: []` and `member: null` — nobody to pick, nobody to credit.
"""
from app import tools
from app.db import get_conn


def _age(name):
    conn = get_conn()
    row = conn.execute("SELECT age_group FROM members WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row["age_group"]


def _household(*adults):
    for name in adults:
        tools.add_member(name)
        tools.set_member_age_group(name, "adult")
    tools.add_member("Kid")
    tools.set_member_age_group("Kid", "child")


def test_a_made_up_age_group_is_refused(signed_in):
    """CATCH. Red before: 200 and Kid's age group is 'banana'."""
    _household("Emily")
    for bad in ("banana", "", "  "):
        res = signed_in.post("/api/memory/member/age-group", json={"name": "Kid", "age_group": bad})
        assert res.status_code == 400
        assert res.json()["detail"] == "Pick Adult, Teen or Child."
    assert _age("Kid") == "child"


def test_real_age_groups_still_land_normalised(signed_in):
    _household("Emily")
    assert signed_in.post("/api/memory/member/age-group",
                          json={"name": "Kid", "age_group": " Teen "}).status_code == 200
    assert _age("Kid") == "teen"
    # The chat's little-one words still fold into Child, as the tool does.
    assert signed_in.post("/api/memory/member/age-group",
                          json={"name": "Kid", "age_group": "toddler"}).status_code == 200
    assert _age("Kid") == "child"


def test_the_last_adult_cannot_stop_being_one(signed_in):
    """CATCH. Red before: 200, and the household has no adult left."""
    _household("Emily", "Vineeth")
    # Two adults: one may move.
    assert signed_in.post("/api/memory/member/age-group",
                          json={"name": "Emily", "age_group": "teen"}).status_code == 200

    res = signed_in.post("/api/memory/member/age-group", json={"name": "Vineeth", "age_group": "child"})
    assert res.status_code == 400
    assert res.json()["detail"] == "Vineeth is the only adult here. Add another adult before changing this."
    assert _age("Vineeth") == "adult"
    assert [a["name"] for a in tools.household_adults()] == ["Vineeth"]

    # Re-saying Adult is not a change, and an older household's "Adult"
    # (capitalised, as onboarding once wrote) is still the last adult.
    assert signed_in.post("/api/memory/member/age-group",
                          json={"name": "vineeth", "age_group": "adult"}).status_code == 200
    conn = get_conn()
    conn.execute("UPDATE members SET age_group = 'Adult' WHERE name = 'Vineeth'")
    conn.commit()
    conn.close()
    assert signed_in.post("/api/memory/member/age-group",
                          json={"name": "VINEETH", "age_group": "teen"}).status_code == 400
