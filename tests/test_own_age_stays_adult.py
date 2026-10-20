"""
A signed-in adult can't change their OWN age group off Adult in Preferences
-> Who's here; another adult still can (Emily, 2026-10-20). Before: in a
two-adult home, Emily marking herself Teen made her phone quietly act as the
other adult.
"""
from app import tools
from app.db import get_conn

SENTENCE = "Your own age stays Adult here. Another adult can change it."


def _adult(name):
    mid = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "adult")
    return mid


def _age(name):
    conn = get_conn()
    row = conn.execute("SELECT age_group FROM members WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row["age_group"]


def _post(c, name, group):
    return c.post("/api/memory/member/age-group", json={"name": name, "age_group": group})


def test_own_age_cannot_leave_adult(signed_in):
    """CATCH. Red before: 200 and Emily is Teen."""
    emily, _ = _adult("Emily"), _adult("Vineeth")
    assert signed_in.post("/api/whoami/pick", json={"member_id": emily}).status_code == 200
    for group in ("teen", "child", "Teen"):
        res = _post(signed_in, "Emily", group)
        assert res.status_code == 400
        assert res.json()["detail"] == SENTENCE
    assert _age("Emily") == "adult"
    # Case of the name doesn't dodge it.
    assert _post(signed_in, "EMILY", "teen").status_code == 400
    # Saying Adult again is fine.
    assert _post(signed_in, "Emily", "adult").status_code == 200


def test_the_other_adult_can_still_be_changed(signed_in):
    emily, _ = _adult("Emily"), _adult("Vineeth")
    signed_in.post("/api/whoami/pick", json={"member_id": emily})
    assert _post(signed_in, "Vineeth", "teen").status_code == 200
    assert _age("Vineeth") == "teen"


def test_own_age_is_free_when_nobody_is_picked(signed_in):
    """No pick on this device keeps today's behaviour."""
    _adult("Emily"), _adult("Vineeth")
    assert _post(signed_in, "Emily", "teen").status_code == 200
    assert _age("Emily") == "teen"


def test_a_lone_adult_still_hears_the_last_adult_sentence(signed_in):
    emily = _adult("Emily")
    signed_in.post("/api/whoami/pick", json={"member_id": emily})
    res = _post(signed_in, "Emily", "teen")
    assert res.status_code == 400
    assert res.json()["detail"] == "Emily is the only adult here. Add another adult before changing this."
