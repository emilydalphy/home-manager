"""
Two routes that answered a bad request in the wrong voice (defect hunt
2026-10-21). Onboarding stored any age group at all, and an away stretch
whose start was days after its end answered with Python's own words.
"""
from conftest import household_date


def test_onboarding_refuses_an_age_group_that_is_not_one(signed_in):
    res = signed_in.post("/api/onboarding/household", json={
        "members": [{"name": "Emily", "age_group": "bogus"}],
    })
    assert res.status_code == 400
    assert res.json()["detail"] == "Pick Adult, Teen or Child."
    # Nothing written: the refusal comes before anyone is saved.
    memory = signed_in.get("/api/memory").json()
    names = [m["name"] for m in memory.get("members", [])]
    assert "Emily" not in names


def test_onboarding_still_takes_the_wizards_own_groups(signed_in):
    res = signed_in.post("/api/onboarding/household", json={
        "members": [
            {"name": "Emily", "age_group": "Adult"},
            {"name": "Sam", "age_group": "teen"},
            {"name": "Lou", "age_group": "child", "age_years": 4},
            {"name": "Max", "age_group": ""},
        ],
    })
    assert res.status_code == 200, res.text


def test_an_away_stretch_ending_days_before_it_starts_says_so(signed_in):
    week = household_date(0)
    res = signed_in.post(f"/api/week/{week}/away-stretch", json={
        "from_date": household_date(30), "from_slot": "dinner",
        "to_date": household_date(20), "to_slot": "lunch",
    })
    assert res.status_code == 400
    assert res.json()["detail"] == "An away stretch's start must come before (or be) its end."


def test_same_day_slots_out_of_order_still_say_so(signed_in):
    week = household_date(0)
    day = household_date(10)
    res = signed_in.post(f"/api/week/{week}/away-stretch", json={
        "from_date": day, "from_slot": "dinner", "to_date": day, "to_slot": "breakfast",
    })
    assert res.status_code == 400
    assert res.json()["detail"] == "An away stretch's start must come before (or be) its end."
