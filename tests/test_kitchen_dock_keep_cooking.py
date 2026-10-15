"""
Cook's root dock says "Keep cooking" once tonight's cook has started
(defect hunt, 2026-10-14).

Found by using the app: tap "Start cooking", step back out to Cook's root,
and the Tonight card reads "STARTED 5:50 · ON THE TABLE 6:25 — Started 5
minutes early" over a dock that still says "Start cooking". The recipe
screen's dock (cookRecipeDockHtml) and the Meal step's (mealDockHtml) both
already say "Keep cooking" for the same cook; the root's was the one door
that never learned it. The tap itself was always harmless (start_cooking is
first-tap-wins) — it is the word that was wrong.
"""
from __future__ import annotations

from test_cook_shelf import MON, _MOVE, _meal, _needs_node, _tonight


@_needs_node
def test_a_started_cook_says_keep_cooking_on_the_root_dock():
    meals = [_meal(1, MON, "Weeknight Chili", cook_started_at=f"{MON}T17:50:00")]
    out = _tonight(meals, [_MOVE], {}, MON, 0)
    assert 'data-cook="start-tonight" data-idx="0">Keep cooking</button>' in out["dock"]
    assert "Start cooking" not in out["dock"]


@_needs_node
def test_a_cook_not_yet_started_still_says_start_cooking():
    meals = [_meal(1, MON, "Weeknight Chili", cook_started_at=None)]
    out = _tonight(meals, [_MOVE], {}, MON, 0)
    assert 'data-cook="start-tonight" data-idx="0">Start cooking</button>' in out["dock"]


@_needs_node
def test_a_finished_cook_keeps_no_dock_whatever_its_start():
    # "Mark not cooked" is the dish's own screen's job, never the root's.
    meals = [_meal(1, MON, "Weeknight Chili", cooked_status="done",
                   cook_started_at=f"{MON}T17:50:00")]
    out = _tonight(meals, [_MOVE], {}, MON, 0)
    assert out["dock"] == ""
