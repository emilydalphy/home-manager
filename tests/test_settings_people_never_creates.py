"""
Settings' people writes never create a person (2026-10-10, overnight
defect hunt).

POST /api/memory/member/age-group and /api/memory/member/restrictions
handed the name straight to tools that get-or-create by name. Reproduced
over HTTP on a throwaway database: an adult removed from the household
came back — "Sam", adult, ['shellfish'] — the moment a Settings sheet
opened before he left tapped his age chip or added a restriction; and a
blank name made a nameless member who became the household's main person.
The age route next door has refused this since 2026-10-06; these two now
do the same: 400 for a blank name, 404 for a stranger, nothing written.
"""
from app import tools
from app.db import get_conn


def _people():
    conn = get_conn()
    rows = conn.execute("SELECT name, age_group, dietary_restrictions_json FROM members ORDER BY id").fetchall()
    conn.close()
    return [(r["name"], r["age_group"], r["dietary_restrictions_json"]) for r in rows]


def _household():
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_member("Sam")
    tools.set_member_age_group("Sam", "adult")


def _sam_leaves():
    conn = get_conn()
    conn.execute("DELETE FROM members WHERE LOWER(name) = 'sam'")
    conn.commit()
    conn.close()


def test_a_stale_screen_does_not_bring_back_someone_who_left(signed_in):
    """CATCH. Red before: both writes 200 and Sam is a member again."""
    _household()
    _sam_leaves()
    before = _people()

    r1 = signed_in.post("/api/memory/member/restrictions",
                        json={"name": "Sam", "restrictions": ["shellfish"], "replace": False})
    r2 = signed_in.post("/api/memory/member/age-group", json={"name": "Sam", "age_group": "adult"})

    assert (r1.status_code, r2.status_code) == (404, 404)
    assert r1.json()["detail"] == "Sam isn't someone in this household."
    assert _people() == before


def test_a_blank_name_is_refused_and_makes_nobody(signed_in):
    """CATCH. Red before: a nameless member, first in line to be the main person."""
    _household()
    before = _people()

    r1 = signed_in.post("/api/memory/member/restrictions", json={"name": "  ", "restrictions": ["peanut"]})
    r2 = signed_in.post("/api/memory/member/age-group", json={"name": "", "age_group": "adult"})

    assert (r1.status_code, r2.status_code) == (400, 400)
    assert _people() == before


def test_someone_here_is_still_written_whatever_the_case(signed_in):
    """GUARD. The lookup is the tools' own, case-blind: the screen's name lands."""
    _household()

    r1 = signed_in.post("/api/memory/member/restrictions",
                        json={"name": "sam", "restrictions": ["shellfish"], "replace": False})
    r2 = signed_in.post("/api/memory/member/age-group", json={"name": "SAM", "age_group": "teen"})

    assert (r1.status_code, r2.status_code) == (200, 200)
    sam = next(m for m in r2.json()["members"] if m["name"] == "Sam")
    assert sam["age_group"] == "teen" and sam["dietary_restrictions"] == ["shellfish"]
    assert len(_people()) == 2
