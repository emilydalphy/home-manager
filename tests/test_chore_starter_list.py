"""
Chores v1: a starter list from what Pomona already knows.

Loop Board "Chores v1: A starter list from what Pomona already knows"
(Emily, Phase 2 — Chores). Somebody setting up chores keeps and tweaks a
proposed list instead of typing their housework out, and nobody becomes
the app's administrator. Before this card the whole list came from one
model call with nothing underneath it: no laundry unless the model
thought of it, no bins, no seasonal items, and the home facts had to be
posted back by the page even when a profile was already saved.

What this file pins, one section per acceptance criterion:

    1. The list is built from the household's facts by rules
       (tools.starter_chore_list): a 3-bed / 2-bath house with a yard and
       a dog gets a row per bathroom, laundry, garbage / recycling / green
       bin, the yard, the dog's care (flea and tick, vet, grooming), the
       seasonal transitions and a monthly tidy-and-donate; a condo with no
       pets gets none of yard / pets / tires / gutters.
    2. Every row has an owner, a frequency and a category, and the owner
       is dealt round the rotation in order — never all on one person.
    3. Frequencies are stored keys (chores._FREQUENCY_DAYS, now with
       semiannual and yearly) and carried in the household's words
       (frequency_label / FREQUENCY_WORDS), the same words shell.js prints.
    4. The help the household already described tags the rows it covers
       as outsourced — nobody is asked twice.
    5. Setup never asks again: /api/onboarding/chores/known reports what
       is on file, and the recommend route fills unanswered fields from
       the saved profile, the pets table and the adults.
    6. The model only ADJUSTS the baseline — adds, re-rhythms, re-tags,
       leaves off — and can never drop laundry or the garbage; if it is
       unavailable the household still gets the list.
    7. Nothing is created until the household says so: recommend writes
       nothing; the profile-only save creates no chores; save creates the
       rows and generates the next two weeks.
    8. The chat path: get_starter_chore_list is a gated chores tool that
       returns the same list, saving nothing.
    9. The empty state is a blank, never somebody else's housework.
"""
from __future__ import annotations

import datetime
import re
import types
from pathlib import Path

import pytest

from app import agent, tools
from app.db import get_conn

REPO = Path(__file__).resolve().parent.parent
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
CHORES_SETUP = (REPO / "static" / "chores-setup.html").read_text(encoding="utf-8")
TODAY = datetime.date.today()


def _adult(name: str) -> int:
    member_id = tools.add_member(name)["member_id"]
    tools.set_member_age_group(name, "Adult")
    return member_id


@pytest.fixture
def two_adults():
    return {"Emily": _adult("Emily"), "Vineeth": _adult("Vineeth")}


HOUSE = {
    "home_type": "House", "bedrooms": 3, "bathrooms": 2, "has_yard": True,
    "standard": "standard", "rotation_members": ["Emily", "Vineeth"],
    "pets": [{"name": "Biscuit", "pet_type": "dog"}],
}
CONDO = {
    "home_type": "Apartment / condo", "bedrooms": 1, "bathrooms": 1, "has_yard": False,
    "standard": "standard", "rotation_members": ["Jamie"], "pets": [],
}


def _names(rows) -> list[str]:
    return [r["name"] for r in rows]


def _by_name(rows) -> dict[str, dict]:
    return {r["name"]: r for r in rows}


def _stub_adjustments(monkeypatch, adjustments: dict):
    """The one forced tool call, answered with the given adjustments."""
    block = types.SimpleNamespace(type="tool_use", name="submit_chore_recommendations", input=adjustments, id="tu_1")
    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", lambda client, **kwargs: types.SimpleNamespace(content=[block]))


# --- 1. the list comes from the facts ----------------------------------------

def test_a_house_with_a_yard_and_a_dog_gets_the_whole_home():
    rows = tools.starter_chore_list(HOUSE)
    names = _names(rows)
    # Cleaning by room: one row per bathroom, the floors, the sheets.
    assert "Main bathroom" in names and "Second bathroom" in names
    assert "Vacuum" in names and "Mop the hard floors" in names and "Change the sheets" in names
    # Laundry and the three bins.
    assert "Laundry" in names
    assert "Garbage out" in names and "Recycling out" in names and "Green bin out" in names
    # The yard.
    assert "Mow the lawn" in names
    # The dog's care, at real frequencies — and no feeding, no food.
    by = _by_name(rows)
    assert by["Walk Biscuit"]["frequency"] == "daily"
    assert by["Flea and tick dose for Biscuit"]["frequency"] == "monthly"
    assert by["Vet checkup for Biscuit"]["frequency"] == "yearly"
    assert by["Grooming for Biscuit"]["frequency"] == "monthly"
    assert not [n for n in names if re.search(r"feed|food", n, re.I)]
    # Seasonal transitions, twice a year, from home type and yard.
    for seasonal in ("Winter tires on / off", "Patio furniture out / in", "Swap the closets for the season", "Clean the gutters"):
        assert by[seasonal]["frequency"] == "semiannual", seasonal
    # The filter at its real rhythm, and the monthly tidy-and-donate pass.
    assert by["Change the furnace filter"]["frequency"] == "quarterly"
    assert by["Tidy and donate"]["frequency"] == "monthly"
    # Not admin, errands or appointments.
    assert not [n for n in names if re.search(r"bill|tax|insurance|appointment|book|renew|shop", n, re.I)]


def test_a_condo_with_no_pets_gets_none_of_yard_pets_or_tires():
    names = _names(tools.starter_chore_list(CONDO))
    assert "Bathroom" in names and "Laundry" in names and "Garbage out" in names
    assert not [n for n in names if re.search(r"lawn|garden|patio|tire|gutter|furnace", n, re.I)]
    assert not [n for n in names if re.search(r"walk|litter|flea|vet|groom|pet", n, re.I)]
    assert "Tidy and donate" in names and "Swap the closets for the season" in names


def test_the_same_facts_give_the_same_list():
    """Rule-based: no model in the loop, so it is repeatable."""
    assert tools.starter_chore_list(HOUSE) == tools.starter_chore_list(dict(HOUSE))


def test_bathrooms_scale_and_stay_sayable():
    assert [n for n in _names(tools.starter_chore_list({**CONDO, "bathrooms": 0})) if "athroom" in n] == ["Bathroom"]
    assert [n for n in _names(tools.starter_chore_list({**CONDO, "bathrooms": 3})) if "athroom" in n] == [
        "Main bathroom", "Second bathroom", "Third bathroom",
    ]
    assert [n for n in _names(tools.starter_chore_list({**CONDO, "bathrooms": 7})) if "athroom" in n] == ["All 7 bathrooms"]


def test_cats_get_litter_and_several_dogs_are_the_dogs():
    rows = tools.starter_chore_list({**CONDO, "pets": [{"name": "Mochi", "pet_type": "cat"}]})
    by = _by_name(rows)
    assert by["Scoop the litter box"]["frequency"] == "daily"
    assert by["Change the litter"]["frequency"] == "weekly"
    assert by["Vet checkup for Mochi"]["frequency"] == "yearly"
    assert "Walk Mochi" not in by
    rows = tools.starter_chore_list({**HOUSE, "pets": [{"name": "Biscuit", "pet_type": "dog"}, {"name": "Rex", "pet_type": "Dog"}]})
    names = _names(rows)
    assert "Walk the dogs" in names and "Walk Biscuit" not in names
    assert "Wash the dogs' beds and bowls" in names


def test_the_standard_moves_only_the_cleaning_rows():
    relaxed = _by_name(tools.starter_chore_list({**HOUSE, "standard": "relaxed"}))
    meticulous = _by_name(tools.starter_chore_list({**HOUSE, "standard": "meticulous"}))
    standard = _by_name(tools.starter_chore_list(HOUSE))
    # Meticulous never means "vacuum every day" — weekly is the floor.
    assert (relaxed["Vacuum"]["frequency"], standard["Vacuum"]["frequency"], meticulous["Vacuum"]["frequency"]) == (
        "biweekly", "weekly", "weekly")
    assert (relaxed["Dust"]["frequency"], standard["Dust"]["frequency"], meticulous["Dust"]["frequency"]) == (
        "monthly", "biweekly", "weekly")
    # Bins, laundry, a flea dose and a gutter are not a matter of taste.
    for fixed in ("Garbage out", "Laundry", "Flea and tick dose for Biscuit", "Clean the gutters"):
        assert relaxed[fixed]["frequency"] == meticulous[fixed]["frequency"] == standard[fixed]["frequency"]


# --- 2. every row has an owner, a frequency and a category --------------------

def test_every_row_has_owner_frequency_and_category_and_the_deal_goes_round():
    rows = tools.starter_chore_list(HOUSE)
    for r in rows:
        assert r["mode"] == "owned" and r["owner_name"] in ("Emily", "Vineeth"), r
        assert r["assignee_names"] == [r["owner_name"]]
        assert r["frequency"] in tools._FREQUENCY_DAYS, r
        assert r["category"] in ("cleaning", "maintenance", "other"), r
    owners = [r["owner_name"] for r in rows]
    # Dealt in turn, top to bottom, in the order setup named them.
    assert owners[:4] == ["Emily", "Vineeth", "Emily", "Vineeth"]
    assert abs(owners.count("Emily") - owners.count("Vineeth")) <= 1


def test_with_nobody_named_every_row_is_whoever():
    rows = tools.starter_chore_list({**CONDO, "rotation_members": []})
    assert rows and all(r["mode"] == "whoever" and r["owner_name"] == "" for r in rows)


def test_a_single_person_owns_it_all():
    rows = tools.starter_chore_list(CONDO)
    assert rows and all(r["mode"] == "owned" and r["owner_name"] == "Jamie" for r in rows)


# --- 3. frequencies: stored keys, household words -----------------------------

def test_twice_a_year_and_yearly_are_rhythms_the_schedule_keeps():
    assert tools._FREQUENCY_DAYS["semiannual"] == 182
    assert tools._FREQUENCY_DAYS["yearly"] == 365
    assert list(tools._FREQUENCY_DAYS)[-1] == "once", "'once' stays the odd one out at the end"
    for name in ("add_chore", "update_chore"):
        tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == name)
        assert set(tool["input_schema"]["properties"]["frequency"]["enum"]) == set(tools._FREQUENCY_DAYS)


def test_every_row_carries_its_rhythm_in_the_households_words():
    for r in tools.starter_chore_list(HOUSE):
        assert r["frequency_label"] == tools.FREQUENCY_WORDS[r["frequency"]]
    assert tools.FREQUENCY_WORDS["biweekly"] == "Every two weeks"
    assert tools.FREQUENCY_WORDS["semiannual"] == "Twice a year"
    assert tools.FREQUENCY_WORDS["yearly"] == "Once a year"
    assert set(tools.FREQUENCY_WORDS) == set(tools._FREQUENCY_DAYS)


def test_the_python_words_and_the_shell_words_agree():
    """One rhythm, one set of words, on every surface — pinned so they can't drift."""
    block = re.search(r"var CHORE_RHYTHM_LABELS = \{(.*?)\};", SHELL_JS, re.S).group(1)
    js_words = dict(re.findall(r"(\w+): '([^']*)'", block))
    assert js_words == tools.FREQUENCY_WORDS
    order = re.search(r"var CHORE_RHYTHM_ORDER = \[(.*?)\];", SHELL_JS).group(1)
    assert re.findall(r"'(\w+)'", order) == list(tools._FREQUENCY_DAYS)


def test_frequency_choices_are_a_picker_in_order():
    choices = tools.frequency_choices()
    assert [c["value"] for c in choices] == list(tools._FREQUENCY_DAYS)
    assert choices[2] == {"value": "biweekly", "label": "Every two weeks"}


# --- 4. the help they already told us about ----------------------------------

def test_a_cleaner_tags_the_cleaning_rows_and_skips_the_deal():
    rows = tools.starter_chore_list({**HOUSE, "existing_help": "a cleaner", "existing_help_frequency": "every two weeks"})
    by = _by_name(rows)
    for covered in ("Main bathroom", "Second bathroom", "Vacuum", "Mop the hard floors", "Dust"):
        assert by[covered]["mode"] == "outsourced", covered
        assert by[covered]["outsourced_to"] == "a cleaner"
        assert by[covered]["owner_name"] == "" and by[covered]["assignee_names"] == []
        assert by[covered]["frequency"] == "biweekly", "scaled to how often they come"
        assert by[covered]["basis"] == "help"
    # Everything else is still ours, and the deal wasn't thrown off by the skipped rows.
    assert by["Laundry"]["mode"] == "owned" and by["Garbage out"]["mode"] == "owned"
    ours = [r["owner_name"] for r in rows if r["mode"] == "owned"]
    assert ours[:2] == ["Emily", "Vineeth"]


def test_the_lawn_people_take_the_yard_only():
    by = _by_name(tools.starter_chore_list({**HOUSE, "existing_help": "the lawn people"}))
    assert by["Mow the lawn"]["mode"] == "outsourced" and by["Mow the lawn"]["outsourced_to"] == "the lawn people"
    assert by["Weed and tidy the garden beds"]["mode"] == "outsourced"
    assert by["Main bathroom"]["mode"] == "owned"


def test_no_help_means_nothing_is_tagged():
    for answer in ("", "no", "None"):
        rows = tools.starter_chore_list({**HOUSE, "existing_help": answer})
        assert not [r for r in rows if r["mode"] == "outsourced"], answer


# --- 5. setup never asks again ------------------------------------------------

def test_known_reports_people_pets_and_home_on_file(signed_in, two_adults):
    tools.add_member("Kid")
    tools.set_member_age_group("Kid", "Child")
    tools.add_pet("Biscuit", "dog")
    res = signed_in.get("/api/onboarding/chores/known")
    assert res.status_code == 200
    body = res.json()
    assert [p["name"] for p in body["people"]] == ["Emily", "Vineeth", "Kid"]
    assert body["adults"] == ["Emily", "Vineeth"]
    assert body["pets"] == [{"name": "Biscuit", "pet_type": "dog"}]
    assert body["home"]["known"] is False, "no chores profile yet, so the home has to be asked"
    assert body["frequencies"][0] == {"value": "daily", "label": "Every day"}

    tools.set_chores_profile(home_type="House", bedrooms=3, bathrooms=2, has_yard=True, standard="relaxed")
    body = signed_in.get("/api/onboarding/chores/known").json()
    assert body["home"] == {"known": True, "home_type": "House", "bedrooms": 3, "bathrooms": 2, "has_yard": True}
    assert body["profile"]["standard"] == "relaxed"


def test_recommend_fills_what_was_not_asked_from_what_is_saved(signed_in, two_adults, monkeypatch):
    """
    The screen asks only the new questions. The home, the pets and the
    people come from what is already on file, not from the request.
    """
    tools.add_pet("Biscuit", "dog")
    tools.set_chores_profile(home_type="House", bedrooms=3, bathrooms=2, has_yard=True, standard="standard")
    seen = {}

    def fake(profile):
        seen.update(profile)
        return tools.starter_chore_list(profile)

    from app import main
    monkeypatch.setattr(main, "generate_chore_recommendations", fake)
    res = signed_in.post("/api/onboarding/chores/recommend", json={"standard": "meticulous", "existing_help": "a cleaner"})
    assert res.status_code == 200
    assert (seen["home_type"], seen["bedrooms"], seen["bathrooms"], seen["has_yard"]) == ("House", 3, 2, True)
    assert seen["standard"] == "meticulous" and seen["existing_help"] == "a cleaner", "what was answered now wins"
    assert seen["pets"] == [{"id": seen["pets"][0]["id"], "name": "Biscuit", "pet_type": "dog"}]
    assert seen["rotation_members"] == ["Emily", "Vineeth"], "nobody named in setup: the adults"
    body = res.json()
    assert body["profile"]["home_type"] == "House" and "goals" not in body["profile"]
    assert body["frequencies"] == tools.frequency_choices()
    names = [r["name"] for r in body["chores"]]
    assert "Mow the lawn" in names and "Walk Biscuit" in names and "Second bathroom" in names


def test_the_profile_save_route_still_saves_exactly_what_it_is_sent(signed_in):
    """Optional fields on the request must not change the skip path."""
    res = signed_in.post("/api/onboarding/chores-profile", json={"home_type": "House", "standard": "standard"})
    assert res.status_code == 200
    saved = tools.get_chores_profile()
    assert saved["home_type"] == "House" and saved["bedrooms"] == 0 and saved["has_yard"] is False
    assert saved["rotation_members"] == [] and saved["existing_help"] == ""


def test_the_setup_page_reads_what_is_known_and_reviews_before_saving():
    """The page exposes the contract: /known first, recommend, then save or skip."""
    assert "/api/onboarding/chores/known" in CHORES_SETUP
    assert "/api/onboarding/chores/recommend" in CHORES_SETUP
    assert "/api/onboarding/chores/save" in CHORES_SETUP
    assert "/api/onboarding/chores-profile" in CHORES_SETUP
    # Per row: whose, how often, someone else does it, drop.
    assert 'class="owner"' in CHORES_SETUP and 'class="rhythm"' in CHORES_SETUP
    assert "Someone else does it" in CHORES_SETUP
    assert 'aria-label="Drop this one"' in CHORES_SETUP
    # The word "outsourced" is ours, never theirs.
    visible = re.findall(r"<label[^>]*>(.*?)</label>|<option[^>]*>(.*?)</option>|label: '([^']*)'", CHORES_SETUP)
    assert "outsourc" not in " ".join("".join(v) for v in visible).lower()


# --- 6. the model adjusts, never replaces --------------------------------------

def test_the_model_may_add_change_and_leave_off_but_never_the_laundry_or_the_garbage(monkeypatch):
    _stub_adjustments(monkeypatch, {
        "chores": [
            {"name": "Descale the kettle", "category": "cleaning", "frequency": "monthly", "mode": "owned"},
            {"name": "Green bin out", "category": "other", "frequency": "weekly", "mode": "owned"},  # already there
        ],
        "drop": ["Laundry", "Garbage out", "Winter tires on / off", "Not a row"],
        "change": [
            {"name": "Main bathroom", "mode": "outsourced", "outsourced_to": "Maria"},
            {"name": "Dust", "frequency": "monthly"},
            {"name": "Nothing here", "frequency": "daily"},
        ],
    })
    rows = agent.generate_chore_recommendations({**HOUSE, "existing_help": "Maria comes Thursdays and does the bathrooms"})
    by = _by_name(rows)
    assert "Laundry" in by and "Garbage out" in by, "never dropped by the model"
    assert "Winter tires on / off" not in by, "a plain drop is honoured"
    assert by["Descale the kettle"]["mode"] == "owned" and by["Descale the kettle"]["owner_name"] in ("Emily", "Vineeth")
    assert by["Descale the kettle"]["frequency_label"] == "Every month" and by["Descale the kettle"]["basis"] == "notes"
    assert _names(rows).count("Green bin out") == 1, "an add already on the list is not a duplicate"
    assert by["Main bathroom"]["mode"] == "outsourced" and by["Main bathroom"]["outsourced_to"] == "Maria"
    assert by["Main bathroom"]["owner_name"] == ""
    assert by["Dust"]["frequency"] == "monthly" and by["Dust"]["frequency_label"] == "Every month"


def test_the_deterministic_list_is_always_underneath(monkeypatch):
    """An empty answer from the model still leaves the household the whole list."""
    _stub_adjustments(monkeypatch, {"chores": []})
    rows = agent.generate_chore_recommendations(HOUSE)
    assert _names(rows) == _names(tools.starter_chore_list(HOUSE))


def test_when_the_model_is_unavailable_the_list_still_arrives(monkeypatch):
    def down(client, **kwargs):
        raise agent.AssistantUnavailableError("busy")

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", down)
    rows = agent.generate_chore_recommendations(HOUSE)
    assert "Laundry" in _names(rows) and len(rows) == len(tools.starter_chore_list(HOUSE))


def test_the_prompt_hands_the_model_the_starter_list_and_only_asks_for_adjustments(monkeypatch):
    calls = {}

    def fake_create(client, **kwargs):
        calls.update(kwargs)
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input={"chores": []})])

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", fake_create)
    agent.generate_chore_recommendations(HOUSE)
    prompt = calls["messages"][0]["content"]
    assert "Laundry" in prompt and "Garbage out" in prompt, "the baseline is in front of the model"
    assert "ADJUST" in prompt and "can never be dropped" in prompt
    schema = agent._RECOMMEND_CHORES_TOOL["input_schema"]["properties"]
    assert set(schema) == {"chores", "drop", "change"}
    assert "semiannual" in schema["chores"]["items"]["properties"]["frequency"]["enum"]


def test_additions_continue_the_deal_rather_than_landing_on_the_first_person():
    rows = agent._normalize_chore_recommendations(
        [{"name": "A", "mode": "owned"}, {"name": "B", "mode": "owned"}], ["Emily", "Vineeth"], deal_from=1,
    )
    assert [r["owner_name"] for r in rows] == ["Vineeth", "Emily"]


# --- 7. nothing is created until they say so ---------------------------------

def _chore_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM chores").fetchone()["c"]
    conn.close()
    return n


def _instance_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS c FROM chore_instances").fetchone()["c"]
    conn.close()
    return n


def test_recommend_writes_nothing(signed_in, two_adults, monkeypatch):
    _stub_adjustments(monkeypatch, {"chores": []})
    res = signed_in.post("/api/onboarding/chores/recommend", json={"home_type": "House", "bathrooms": 2, "has_yard": True})
    assert res.status_code == 200 and len(res.json()["chores"]) > 10
    assert _chore_count() == 0 and _instance_count() == 0
    assert tools.get_chores_profile() == {"has_profile": False}, "recommend is a read; the profile is saved by the skip or the keep"


def test_skipping_the_review_saves_the_profile_only(signed_in, two_adults):
    res = signed_in.post("/api/onboarding/chores-profile", json={
        "home_type": "House", "bedrooms": 3, "bathrooms": 2, "has_yard": True, "standard": "relaxed",
        "rotation_members": ["Emily", "Vineeth"],
    })
    assert res.status_code == 200
    assert tools.get_chores_profile()["has_profile"] is True
    assert _chore_count() == 0 and _instance_count() == 0


def test_saving_the_kept_rows_creates_them_and_the_next_two_weeks(signed_in, two_adults, monkeypatch):
    _stub_adjustments(monkeypatch, {"chores": []})
    proposed = signed_in.post("/api/onboarding/chores/recommend", json={
        "home_type": "House", "bedrooms": 3, "bathrooms": 2, "has_yard": True,
        "rotation_members": ["Emily", "Vineeth"], "existing_help": "a cleaner",
    }).json()["chores"]
    # The household reviews: drops one, hands one over, changes a rhythm.
    kept = [r for r in proposed if r["name"] != "Winter tires on / off"]
    by = _by_name(kept)
    by["Laundry"]["owner_name"], by["Laundry"]["assignee_names"] = "Vineeth", ["Vineeth"]
    by["Dust"]["frequency"] = "monthly"
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": kept})
    assert res.status_code == 200
    body = res.json()
    assert body["saved"] is True and body["created"] == len(kept) and body["skipped"] == []
    assert body["scheduled"] > 0

    defs = {d["name"]: d for d in tools.list_chore_definitions()}
    assert "Winter tires on / off" not in defs
    assert defs["Laundry"]["mode"] == "owned" and defs["Laundry"]["owner"] == "Vineeth"
    assert defs["Dust"]["frequency"] == "monthly"
    assert defs["Main bathroom"]["mode"] == "outsourced" and defs["Main bathroom"]["outsourced_to"] == "a cleaner"

    # The next two weeks exist, so Now and Plan | Chores have something to show.
    conn = get_conn()
    dues = [r["due_date"] for r in conn.execute("SELECT due_date FROM chore_instances WHERE chore_id = (SELECT id FROM chores WHERE name = 'Kitchen counters and sink')")]
    conn.close()
    assert len(dues) == 15 and dues[0] == TODAY.isoformat() and dues[-1] == (TODAY + datetime.timedelta(days=14)).isoformat()
    tools.set_chores_enabled(True)
    today = signed_in.get("/api/chores/today").json()
    assert today["chores_set_up"] is True and len(today["chores"]) >= len(kept)
    pending = tools.get_chores_pending()["chores"]
    assert {c["chore"] for c in pending} == set(defs)


def test_a_yearly_row_is_scheduled_once_in_the_fortnight(two_adults):
    tools.add_chore("Vet checkup for Biscuit", frequency="yearly", category="other", owner_name="Emily")
    tools.add_chore("Winter tires on / off", frequency="semiannual", category="maintenance", owner_name="Vineeth")
    created = tools.generate_chore_schedule(days_ahead=14)
    assert [c["chore"] for c in created] == ["Vet checkup for Biscuit", "Winter tires on / off"]
    assert all(c["due_date"] == TODAY.isoformat() for c in created)


def test_save_refuses_a_rhythm_it_cannot_keep(signed_in, two_adults):
    res = signed_in.post("/api/onboarding/chores/save", json={"chores": [
        {"name": "Bins", "frequency": "fortnightly", "owner_name": "Emily"},
        {"name": "Dust", "frequency": "semiannual", "owner_name": "Emily"},
    ]})
    body = res.json()
    assert body["created"] == 1
    assert body["skipped"] == [{"name": "Bins", "reason": "'fortnightly' isn't a rhythm I can keep."}]


# --- 8. the chat path ---------------------------------------------------------

def test_the_chat_tool_returns_the_same_list_and_saves_nothing(two_adults):
    tools.add_pet("Biscuit", "dog")
    tools.set_chores_profile(home_type="House", bedrooms=3, bathrooms=2, has_yard=True, standard="standard")
    out = tools.get_starter_chore_list()
    assert out["nothing_saved_yet"] is True
    assert out["count"] == len(out["chores"]) > 10
    assert _names(out["chores"]) == _names(tools.starter_chore_list({
        **HOUSE, "rotation_members": ["Emily", "Vineeth"],
    }))
    assert out["profile"]["rotation_members"] == ["Emily", "Vineeth"], "the adults, since setup named nobody"
    assert "goals" not in out["profile"]
    assert all(r["frequency_label"] for r in out["chores"])
    assert _chore_count() == 0


def test_the_chat_tool_is_registered_and_gated():
    assert agent.TOOL_FUNCTIONS["get_starter_chore_list"] is tools.get_starter_chore_list
    assert "get_starter_chore_list" in agent.CHORES_TOOLS
    tool = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "get_starter_chore_list")
    desc = tool["description"].lower()
    assert "saves nothing" in desc and "never re-ask" in desc
    assert "'every two weeks', never 'biweekly'" in desc


def test_the_system_prompt_sends_the_model_to_the_starter_list():
    assert "get_starter_chore_list" in agent.SYSTEM_PROMPT
    assert "never ask for them again" in agent.SYSTEM_PROMPT
    assert "Never type out a list of your own instead" in agent.SYSTEM_PROMPT
    assert "Offer common examples" not in agent.SYSTEM_PROMPT


# --- 9. the empty state is a blank -------------------------------------------

def test_a_household_that_added_nothing_has_nothing(signed_in, two_adults):
    tools.set_chores_enabled(True)
    assert tools.get_chores_pending()["chores"] == []
    today = signed_in.get("/api/chores/today").json()
    assert today["chores"] == [] and today["chores_set_up"] is False
    assert _chore_count() == 0


def test_any_api_failure_still_leaves_the_household_the_list(monkeypatch):
    """Found in live verification: a 401 is not an AssistantUnavailableError."""
    def broken(client, **kwargs):
        raise RuntimeError("Error code: 401 - invalid x-api-key")

    monkeypatch.setattr(agent, "_client", lambda: object())
    monkeypatch.setattr(agent, "_create_with_retry", broken)
    assert _names(agent.generate_chore_recommendations(HOUSE)) == _names(tools.starter_chore_list(HOUSE))
