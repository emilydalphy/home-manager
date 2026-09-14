"""
"Olives" as a restriction must not match olive oil.

Found 2026-09-14 by driving the app on a throwaway DB: a member restriction
of "olives" produced a HARD clash on essentially every dinner, because olive
oil is in most of them. `_keyword_variants("olives")` yields {olives, olive,
oliv} and `olive` whole-word-matches inside "olive oil"; `_COMPOUND_EXCEPTIONS`
covers exactly this shape for peanut butter, coconut milk, sugar snap, rice
flour and soba noodles, and simply had no `olive oil` entry.

Why a false positive is worth a test here rather than a shrug: it puts an
allergy-shaped gate in front of Approve every single week, and a warning a
household learns to click past is the thing this whole check exists to
prevent. That is the reasoning already written into the 2026-09-04 pass.

7 of the 15 tests here are CATCHES — red on the code as it was before this
file existed. The other 8 are no-regression GUARDS: green either way, and
here so a future widening of the exception cannot quietly stop real olives
from clashing. Each class says which of the two it holds, and the one class
that holds both says so.
"""
import datetime

import pytest

from app import tools
from app.tools import coordination


def _week_start(offset_weeks: int = 1) -> str:
    """A week that has not started yet, so no expired-draft sweep touches it."""
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    return (monday + datetime.timedelta(days=7 * offset_weeks)).isoformat()


def _matched(restriction: str, text: str) -> str | None:
    """What `restriction` finds in one segment of recipe text, or None."""
    terms = coordination._match_terms(coordination._conflict_phrases(restriction))
    return coordination._matches(terms, [text])


# ---------- 1. the false positive itself ----------

class TestOliveOilIsNotAnOlive:
    """CATCHES. Each of these returned "olives" before the fix."""

    @pytest.mark.parametrize("text", [
        "olive oil",
        "extra virgin olive oil",
        "2 tbsp olive oil",
        "olive oils",
        "light olive oil, for frying",
    ])
    def test_olives_does_not_match_olive_oil(self, text):
        assert _matched("olives", text) is None

    def test_the_singular_spelling_is_discounted_too(self):
        """A household may write "olive" rather than "olives"."""
        assert _matched("olive", "olive oil") is None


# ---------- 2. the path that actually bit ----------

class TestApprovalIsNotGatedByOliveOil:
    """
    The reproduction from the card, through the real check rather than the
    matcher: a member who avoids olives, a week of ordinary dinners, and
    `needs_confirmation` on every one of them.

    ONE CATCH AND ONE GUARD, unlike the classes either side of it. The
    olive-oil test is red on the parent; the real-olives test is green
    either way and is here because discounting the compound must not cost
    us the thing the check exists for.
    """

    def _plan_with(self, meal: str) -> int:
        week = _week_start()
        plan = tools.create_weekly_plan(week)
        tools.plan_meal(
            tools._week_dates(week)[0], meal, slot="dinner",
            weekly_plan_id=plan["weekly_plan_id"],
        )
        return plan["weekly_plan_id"]

    def test_an_ordinary_dinner_cooked_in_olive_oil_is_not_a_clash(self):
        tools.add_member("Iris")
        tools.add_recipe(
            "Bean Chili",
            ingredients=[{"item": "beans", "qty": "1 tin"},
                         {"item": "olive oil", "qty": "2 tbsp"}],
        )
        tools.set_member_dietary_restrictions("Iris", ["olives"])

        found = tools.check_plan_conflicts(self._plan_with("Bean Chili"))["conflicts"]

        assert found == []

    def test_a_dish_that_really_has_olives_still_clashes(self):
        """
        The other half of the same fix: discounting the compound must not
        cost us the real thing, which is the only reason the check exists.
        """
        tools.add_member("Iris")
        tools.add_recipe(
            "Greek Salad",
            ingredients=[{"item": "kalamata olives", "qty": "100g"},
                         {"item": "olive oil", "qty": "2 tbsp"}],
        )
        tools.set_member_dietary_restrictions("Iris", ["olives"])

        found = tools.check_plan_conflicts(self._plan_with("Greek Salad"))["conflicts"]

        assert [c["matched"] for c in found] == ["olives"]
        assert found[0]["member"] == "Iris"
        assert found[0]["severity"] == "hard"


# ---------- 3. what must not change ----------

class TestRealOlivesStillCount:
    """
    GUARDS — green before the fix as well. They are here so a later, broader
    olive exception cannot quietly stop an actual olive from matching.
    """

    @pytest.mark.parametrize("text", [
        "kalamata olives",
        "castelvetrano olives",
        "green olives, sliced",
        "olive tapenade",
        "black olive paste",
    ])
    def test_a_real_olive_still_matches(self, text):
        assert _matched("olives", text) == "olives"

    def test_writing_the_compound_yourself_is_taken_at_your_word(self):
        """
        The rule the existing compound exceptions already follow: a discount
        only applies to a ONE-WORD avoidance. Someone who writes "olive oil"
        as the restriction means olive oil.
        """
        assert _matched("olive oil", "olive oil") == "olive oil"

    def test_the_other_compound_exceptions_are_untouched(self):
        assert _matched("nuts", "peanut butter") == "nuts"
        assert _matched("butter", "peanut butter") is None
        assert _matched("milk", "coconut milk") is None
        assert _matched("sugar", "sugar snap peas") is None
