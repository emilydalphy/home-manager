"""
A typo in Settings gets a polite "that doesn't look right" (a 400 with a
sentence), not a broken save, a 500, or a morning text that can never send
(defect hunt, 2026-10-28). Four routes, one file:

1. PATCH /api/preferences/meal-planning — a time limit of 1e400 (JSON's
   infinity) was a 500; 10**30 reached the SQLite bind and was logged as a
   server error on its way to a 400. Both are refused at validation now.
2. POST /api/morning-text — "+0 1234 5678" was stored as +012345678. No
   country code starts with 0; Twilio refused it every morning.
3. POST /api/member-needs — snacks of true / 2.7 were stored as 1 / 2.
4. POST /api/memory/edit — goals: null stored the word "None".

Twilio is never configured here; nothing is sent.
"""
import pytest

from app import tools
from app.db import get_conn


def _member(name: str, age_group: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, age_group)
    return member_id


def _column(sql: str, *args):
    conn = get_conn()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


# ---------- 1. time limits ----------

@pytest.mark.parametrize("field", ["weeknight_max_minutes", "weekday_lunch_max_minutes"])
@pytest.mark.parametrize("raw", ["1e400", "1" + "0" * 30, "1441"])
def test_an_impossible_time_limit_is_a_400_and_changes_nothing(signed_in, field, raw, caplog):
    assert signed_in.patch("/api/preferences/meal-planning", json={"field": field, "value": 30}).status_code == 200
    # Raw body: 1e400 has to reach the server as JSON's own number, which
    # json.dumps can't write.
    res = signed_in.patch(
        "/api/preferences/meal-planning",
        content='{"field": "%s", "value": %s}' % (field, raw),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 400, res.text
    assert field in res.json()["detail"]
    assert tools.get_household_memory()[field] == 30
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize("field", ["weeknight_max_minutes", "weekday_lunch_max_minutes"])
def test_a_whole_day_and_no_cap_are_still_answers(signed_in, field):
    for minutes in (1440, 0, 45):
        res = signed_in.patch("/api/preferences/meal-planning", json={"field": field, "value": minutes})
        assert res.status_code == 200, res.text
        assert tools.get_household_memory()[field] == minutes


# ---------- 2. the morning text's number ----------

@pytest.mark.parametrize("typed", ["+0 1234 5678", "+01 416 555 0100", "+0044 20 7946 0958"])
def test_a_plus_zero_number_is_refused_and_not_stored(signed_in, typed):
    me = _member("Emily", "adult")
    assert signed_in.post("/api/morning-text", json={"member_id": me, "phone": "416-555-0100"}).status_code == 200
    res = signed_in.post("/api/morning-text", json={"member_id": me, "phone": typed})
    assert res.status_code == 400, res.text
    assert "mobile number" in res.json()["detail"]
    assert _column("SELECT phone FROM members WHERE id = ?", me) == "+14165550100"


@pytest.mark.parametrize("typed, stored", [
    ("+44 20 7946 0958", "+442079460958"),
    ("+1 416 555 0100", "+14165550100"),
    ("(416) 555-0100", "+14165550100"),
])
def test_real_numbers_still_save(signed_in, typed, stored):
    me = _member("Emily", "adult")
    res = signed_in.post("/api/morning-text", json={"member_id": me, "phone": typed})
    assert res.status_code == 200, res.text
    assert _column("SELECT phone FROM members WHERE id = ?", me) == stored


# ---------- 3. snacks a day ----------

@pytest.mark.parametrize("bad", [True, False, 2.7, 0.5])
def test_snacks_must_be_a_whole_number_not_a_bool_or_fraction(signed_in, bad):
    kid = _member("Sam", "child")
    assert signed_in.post("/api/member-needs", json={"snacks": {"Sam": 1}}).status_code == 200
    res = signed_in.post("/api/member-needs", json={"snacks": {"Sam": bad}})
    assert res.status_code == 400, res.text
    assert "Sam's snacks a day" in res.json()["detail"]
    assert _column("SELECT snacks_per_day FROM members WHERE id = ?", kid) == 1


@pytest.mark.parametrize("good, stored", [(2, 2), (0, 0), (3.0, 3), ("2", 2)])
def test_whole_snack_counts_still_save(signed_in, good, stored):
    kid = _member("Sam", "child")
    res = signed_in.post("/api/member-needs", json={"snacks": {"Sam": good}})
    assert res.status_code == 200, res.text
    assert _column("SELECT snacks_per_day FROM members WHERE id = ?", kid) == stored


# ---------- 4. goals ----------

@pytest.mark.parametrize("bad", [None, ["eat better"], 5])
def test_goals_null_is_refused_like_notes_and_never_becomes_the_word_none(signed_in, bad):
    assert signed_in.post("/api/memory/edit", json={"field": "goals", "value": "eat better"}).status_code == 200
    res = signed_in.post("/api/memory/edit", json={"field": "goals", "value": bad})
    assert res.status_code == 400, res.text
    assert "an empty string clears it" in res.json()["detail"]
    assert _column("SELECT goals FROM households WHERE id = 1") == "eat better"


def test_an_empty_string_still_clears_goals(signed_in):
    assert signed_in.post("/api/memory/edit", json={"field": "goals", "value": "eat better"}).status_code == 200
    assert signed_in.post("/api/memory/edit", json={"field": "goals", "value": ""}).status_code == 200
    assert _column("SELECT goals FROM households WHERE id = 1") == ""
