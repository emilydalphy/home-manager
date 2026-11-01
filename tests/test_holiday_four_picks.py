"""
Emily's four holiday picks (2026-10-31, Loop Board "Boxing Day's fresh shop
lands on Christmas Eve"):

1. A US household has a US holiday list (Thanksgiving 2026-11-26).
2. Remembrance Day and Civic Holiday are shops-open days in Ontario, Quebec
   and Manitoba — and a "closed on holidays" store is shut on them elsewhere.
3. Boxing Day's make-ahead steps move earlier, off another hosted day.
4. Christmas Eve is on the list, and asked about.
"""
import pytest

from app import tools
from tests.test_big_meal import family, proposer, recipes, _holiday_tasks  # noqa: F401
from tests.test_big_meal_hour_and_shut_shops import _approved_week

PINNED = pytest.mark.today("2026-12-14T09:00:00-05:00")


# ---------- 1. a US list ----------

def test_a_us_household_gets_us_holidays():
    tools.set_holiday_region(country="US")
    by_name = {h["name"]: h for h in tools.rule_holidays(2026, "US", "")}
    assert by_name["Thanksgiving"]["date"] == "2026-11-26" and by_name["Thanksgiving"]["asks"]
    assert by_name["Independence Day"]["date"] == "2026-07-04"
    assert by_name["Memorial Day"]["date"] == "2026-05-25"
    assert "Remembrance Day" not in by_name and "Canada Day" not in by_name
    # And the household's own reads use it.
    assert tools.get_upcoming_holidays("2026-11-20", 10)[0]["name"] == "Thanksgiving"


def test_moving_to_the_us_clears_the_ontario_province_and_takes_a_state():
    assert tools.set_holiday_region(country="US") == {"country": "US", "province": ""}
    assert tools.set_holiday_region(province="Ohio") == {"country": "US", "province": "OH"}
    with pytest.raises(ValueError):
        tools.set_holiday_region(province="Ontario")
    assert tools.set_holiday_region(country="Canada", province="BC") == {"country": "CA", "province": "BC"}


# ---------- 2. shops open in ON / QC / MB ----------

@pytest.mark.parametrize("province,closes", [("ON", False), ("QC", False), ("MB", False), ("NS", True), ("", True)])
def test_remembrance_day_shuts_a_holiday_closed_store_only_outside_on_qc_mb(province, closes):
    tools.set_holiday_region(province=province)
    assert tools.holidays.shops_close_for("Remembrance Day") is closes
    assert tools.holidays.shops_close_for("Civic Holiday") is closes
    # A real day off everywhere still shuts it.
    assert tools.holidays.shops_close_for("Christmas Day") is True


def test_a_store_closed_on_holidays_is_shut_on_remembrance_day_in_nova_scotia():
    from datetime import date
    from app.tools import stores
    tools.add_usual_stores(["Sobeys"])
    tools.set_store_hours("Sobeys", {
        "days": {d: ["08:00", "21:00"] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")},
        "closed_on_holidays": True,
    })
    tools.set_holiday_region(province="NS")
    assert stores.open_window("Sobeys", date(2026, 11, 11)) == {"closed": True, "holiday": True}
    tools.set_holiday_region(province="ON")
    assert stores.open_window("Sobeys", date(2026, 11, 11)).get("closed") is not True


# ---------- 3. make-ahead off another hosted day ----------

def _ahead_days(name: str) -> list[str]:
    return [t["task_date"] for t in _holiday_tasks()
            if t["related_meal"] != "Shop" and t["description"].endswith(f"big meal on {name}.")]


@PINNED
def test_boxing_days_stuffing_is_not_made_on_a_hosted_christmas(family, recipes, proposer):
    _approved_week()
    tools.answer_holiday("2026-12-25", "hosting", headcount=6, on_table_at="5pm")
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")
    boxing = _ahead_days("Saturday")
    assert boxing and "2026-12-25" not in boxing, boxing
    assert all(d <= "2026-12-24" for d in boxing)


@PINNED
def test_hosting_christmas_after_boxing_day_moves_its_prep_off(family, recipes, proposer):
    _approved_week()
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")
    assert "2026-12-25" in _ahead_days("Saturday")  # the day before, nothing hosted on it yet
    tools.answer_holiday("2026-12-25", "hosting", headcount=6, on_table_at="5pm")
    assert "2026-12-25" not in _ahead_days("Saturday")


@PINNED
def test_without_another_hosted_day_the_prep_stays_the_day_before(family, recipes, proposer):
    _approved_week()
    tools.answer_holiday("2026-12-25", "out")
    tools.answer_holiday("2026-12-26", "hosting", headcount=4, on_table_at="6pm")
    assert "2026-12-25" in _ahead_days("Saturday")


# ---------- 4. Christmas Eve ----------

def test_christmas_eve_is_on_the_list_and_asked_about():
    for country in ("CA", "US"):
        eve = next(h for h in tools.rule_holidays(2026, country, "") if h["name"] == "Christmas Eve")
        assert eve["date"] == "2026-12-24" and eve["asks"] is True
    # And it shuts no shop.
    assert tools.holidays.shops_close_for("Christmas Eve") is False
    assert tools.answer_holiday("2026-12-24", "just_us")["holiday_name"] == "Christmas Eve"
