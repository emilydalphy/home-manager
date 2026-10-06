"""
Onboarding, regrouped — slice 3: planning reads the needs per person
(Loop Board, Emily's locked design of 2026-10-05).

  - Weekday lunches: ONE cook, packed several ways. A planned weekday lunch
    carries each person's line, worked out in code from who is at it and
    what they said (member_needs.lunch_packing), and the generator is told.
  - Snacks: each person's snacks a day sizes the snack shop.
  - Portions from age: a toddler half a plate, a school-age child three
    quarters — a child with no age yet stays a full plate.
  - Nut-free stays a household-wide hard avoidance (covered in
    test_onboarding_regrouped.py) — never narrower.

The model is stubbed everywhere (generate_weekly_plan_llm).
"""
from __future__ import annotations

import datetime

import pytest

from app import agent, tools
from conftest import household_today


def _monday() -> str:
    today = household_today()
    return (today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(days=7)).isoformat()


def _days() -> list[str]:
    return tools._week_dates(_monday())


@pytest.fixture
def week() -> int:
    return tools.create_weekly_plan(_monday())["weekly_plan_id"]


def _qty(item: str) -> str | None:
    rows = tools.list_grocery_list() + tools.list_grocery_list(status="spice")
    return {r["item"]: r["quantity"] for r in rows}.get(item)


def _child(name: str, age) -> None:
    tools.add_member(name)
    tools.set_member_age_group(name, "child")
    if age is not None:
        tools.set_member_age(name, age)


def _gowthami_household() -> None:
    tools.add_member("Gowthami")
    tools.add_member("Ravi")
    _child("Arjun", 7)
    tools.save_member_needs(lunch_needs={
        "Gowthami": ["reheat"],
        "Ravi": ["cold_packed"],
        "Arjun": {"needs": ["thermos", "nut_free"], "days": {"tuesday": ["made_fresh"]}},
    })


# ---------- one cook, packed several ways ----------


def test_one_lunch_is_packed_each_person_s_way(week):
    _gowthami_household()
    monday, tuesday, saturday = _days()[0], _days()[1], _days()[5]
    for d in (monday, tuesday, saturday):
        tools.plan_meal(d, "Chickpea curry with rice", slot="lunch", weekly_plan_id=week)

    lunches = {m["date"]: m for m in tools.get_weekly_plan(week)["meals"] if m["slot"] == "lunch"}
    assert len([m for m in tools.get_weekly_plan(week)["meals"] if m["slot"] == "lunch" and m["date"] == monday]) == 1, \
        "one cook: still one lunch row for everyone"
    assert [(p["name"], p["how"]) for p in lunches[monday]["packed_as"]] == [
        ("Gowthami", "reheated"),
        ("Ravi", "cold, packed"),
        ("Arjun", "nut-free, warm in a thermos"),
    ]
    assert ("Arjun", "made fresh") in [(p["name"], p["how"]) for p in lunches[tuesday]["packed_as"]], \
        "a day-by-day answer wins on its day"
    assert "packed_as" not in lunches[saturday], "weekday lunches only"

    menu_day = next(d for d in tools.get_week_menu(week)["days"] if d["date"] == monday)
    assert [p["name"] for p in menu_day["lunch"]["packed_as"]] == ["Gowthami", "Ravi", "Arjun"]


def test_someone_away_at_lunch_has_nothing_packed(week):
    _gowthami_household()
    monday = _days()[0]
    tools.plan_meal(monday, "Pasta salad", slot="lunch", weekly_plan_id=week)
    tools.set_member_attendance(monday, "lunch", "Ravi", present=False)

    lunch = next(m for m in tools.get_weekly_plan(week)["meals"] if m["slot"] == "lunch")
    assert [p["name"] for p in lunch["packed_as"]] == ["Gowthami", "Arjun"]


def test_a_household_that_never_answered_reads_as_before(week):
    tools.add_member("A")
    tools.add_member("B")
    tools.plan_meal(_days()[0], "Soup", slot="lunch", weekly_plan_id=week)
    lunch = next(m for m in tools.get_weekly_plan(week)["meals"] if m["slot"] == "lunch")
    assert "packed_as" not in lunch
    assert tools.member_needs_generation_context(_days()) == {}


def test_the_generator_is_told_each_person_s_needs(monkeypatch):
    _gowthami_household()
    tools.save_member_needs(snacks={"Arjun": 2, "Gowthami": 1, "Ravi": 1})
    seen = {}

    def fake(ctx):
        seen.update(ctx)
        return []

    monkeypatch.setattr(agent, "generate_weekly_plan_llm", fake)
    try:
        agent.generate_weekly_plan(_monday())
    except Exception:
        pass  # an empty week is refused; the context is what this test reads
    per_person = seen["per_person"]
    monday = next(d for d in per_person["lunch_needs"] if d["date"] == _days()[0])
    assert {"name": "Arjun", "how": "nut-free, warm in a thermos"} in monday["people"]
    assert per_person["snacks_by_person"] == {"Gowthami": 1, "Ravi": 1, "Arjun": 2}
    assert per_person["portions"] == [{"name": "Arjun", "stage": "child", "plate": 0.75}]
    assert any(line.startswith("Arjun: no nuts or peanuts") for line in seen["must_not_contain"]), \
        "the model is told, not only checked afterwards"


# ---------- the list and the Cook screen agree (review, 2026-10-06) ----------


def _cook_card(week: int, d: str, slot: str) -> dict:
    return next(m for m in tools.get_cooker_view(week)["meals"] if m["date"] == d and m["slot"] == slot)


def test_a_toddler_s_shop_and_cook_screen_ask_for_the_same_amount(week):
    """Review repro: 2 adults + a toddler (2) + a child (7), a recipe for 4
    naming 800 g of chicken. Portions from age are told to the planner, but
    the shop and the Cook screen both still count heads, so neither asks
    for more than the other (it was 650 g bought, 800 g asked)."""
    tools.add_member("A")
    tools.add_member("B")
    _child("Mia", 2)
    _child("Arjun", 7)
    tools.add_recipe("Chicken stew", ingredients=[{"item": "Chicken thighs", "qty": "800 g", "category": "meat"}],
                     default_servings=4)
    monday = _days()[0]
    tools.plan_meal(monday, "Chicken stew", slot="dinner", weekly_plan_id=week)
    tools.approve_weekly_plan(week, approved_by="A")

    cook = _cook_card(week, monday, "dinner")
    asked = next(i["qty"] for i in cook["ingredients"] if i["item"] == "Chicken thighs")
    assert _qty("Chicken thighs") == "800 g"
    assert asked.replace(" ", "") == "800g", asked


def test_snacks_bought_are_the_snacks_the_cook_screen_makes(week):
    tools.add_member("A")
    tools.add_member("B")
    _child("Arjun", 6)
    tools.save_member_needs(snacks={"A": 1, "B": 1, "Arjun": 2})
    tools.add_recipe("Yogurt cup", ingredients=[{"item": "Greek yogurt", "qty": "100 g", "category": "dairy"}],
                     default_servings=1)
    monday = _days()[0]
    tools.plan_meal(monday, "Yogurt cup", slot="snack", weekly_plan_id=week)
    tools.approve_weekly_plan(week, approved_by="A")
    asked = next(i["qty"] for i in _cook_card(week, monday, "snack")["ingredients"] if i["item"] == "Greek yogurt")
    assert _qty("Greek yogurt").replace(" ", "") == asked.replace(" ", "") == "300g"


# ---------- the household's snacks a day ----------


def test_settings_snack_counts_move_the_household_number_to_the_most_anyone_has(signed_in):
    tools.add_member("A")
    _child("Arjun", 6)
    r = signed_in.post("/api/member-needs", json={"snacks": {"Arjun": 3}})
    assert r.status_code == 200, r.text
    assert tools.resolve_snacks_per_day() == 3
    signed_in.post("/api/member-needs", json={"snacks": {"Arjun": 1, "A": 1}})
    assert tools.resolve_snacks_per_day() == 1


# ---------- the public share link ----------


def test_the_share_link_carries_no_one_s_lunch_needs(week, client):
    _gowthami_household()
    tools.plan_meal(_days()[0], "Pasta salad", slot="lunch", weekly_plan_id=week)
    assert any(m.get("packed_as") for m in tools.get_weekly_plan(week)["meals"])
    token = tools.get_or_create_share_link()["token"]
    assert token, "share link helper"
    r = client.get(f"/api/share/{token}")
    assert r.status_code == 200
    assert "packed_as" not in r.text and "nut_free" not in r.text
