"""US Memorial Day / July 4 / Labor Day are shopping days; Canada's are not."""
import pytest

from app import tools


def _shut(name):
    return tools.holidays.shops_close_for(name)


@pytest.mark.parametrize("name", ["Memorial Day", "Independence Day", "Labor Day"])
def test_these_days_are_shopping_days_in_the_us(name):
    tools.set_holiday_region(country="US")
    assert _shut(name) is False


@pytest.mark.parametrize("name", ["Thanksgiving", "Christmas Day", "New Year’s Day", "Easter Sunday"])
def test_the_big_days_may_still_shut_a_us_store(name):
    tools.set_holiday_region(country="US")
    assert _shut(name) is True


@pytest.mark.parametrize("province", ["", "ON"])
@pytest.mark.parametrize("name", ["Labour Day", "Labor Day", "Memorial Day", "Independence Day"])
def test_the_same_names_still_shut_a_canadian_store(name, province):
    tools.set_holiday_region(country="CA", province=province)
    assert _shut(name) is True
