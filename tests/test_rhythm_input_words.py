"""
Rhythm answers that aren't answers are refused in words, never a 500 or
Python's own text (defect hunt 2026-10-24): a 5,000-character time zone
crashed Preferences' clock ("File name too long" from the zone lookup), and
a prep day's minutes of "abc" came back as "invalid literal for int() with
base 10: 'abc'", while 10**30 minutes was stored as typed.
"""
from app import tools


def test_a_very_long_time_zone_is_a_400_in_words_on_both_doors(signed_in):
    for path, body in (("/api/household/clock", {"timezone": "a" * 5000}),
                       ("/api/onboarding/rhythm", {"timezone": "a" * 5000})):
        res = signed_in.post(path, json=body)
        assert res.status_code == 400, (path, res.text[:200])
        assert "it wants a name like America/Toronto" in res.json()["detail"]
    assert signed_in.post("/api/household/clock", json={"timezone": "America/Vancouver"}).json()["timezone"] == "America/Vancouver"


def test_prep_minutes_that_are_not_a_number_are_refused_in_words(signed_in):
    res = signed_in.post("/api/onboarding/rhythm", json={"prep_days": [{"weekday": "sunday", "minutes": "abc"}]})
    assert res.status_code == 400
    assert res.json()["detail"] == "minutes must be a whole number of minutes, or omitted."


def test_prep_minutes_past_a_day_are_refused_and_nothing_is_saved(signed_in):
    res = signed_in.post("/api/onboarding/rhythm", json={"prep_days": [{"weekday": "sunday", "minutes": 10 ** 30}]})
    assert res.status_code == 400
    assert "more than a day" in res.json()["detail"]
    ok = signed_in.post("/api/onboarding/rhythm", json={"prep_days": [{"weekday": "sunday", "minutes": 1440}]})
    assert ok.status_code == 200
    assert tools.get_household_memory()["rhythm"]["prep_days"][0]["minutes"] == 1440
