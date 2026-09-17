"""
An `open` dinner reaches the bell and the morning text, not just Now.

Loop Board, Phase 0: `get_needs_you_items` emits TWO dinner shapes and two
readers saw only one of them.

  dinner_decision — there is no dinner row for that night at all.
  dinner_open     — there IS a row, its slot_state is `open`, and the app
                    wrote a sentence saying why it handed the night back
                    ("You cut Bean Chili back, so this one is yours to
                    fill.").

Both are a decision waiting on the household. `notifications.py` filtered to
the first and `digest.py` gated the morning text's "tonight's still open"
line on the first's dismissal key, so an open dinner produced a card on Now
and nothing anywhere else — while the comment directly above that filter
promised "the notification and the band never disagree about what's open".

What is pinned here, and the shape of it is the point: the two channel tests
are PARAMETRIZED OVER BOTH SHAPES, so a fix that works for one can never
again read as a fix that works. That is the whole lesson of the branch that
found this — a test naming only `dinner_decision` was blind to `dinner_open`
on every weekday of its life.

Also pinned: the wording (the bell repeats the app's own reason; neither
channel says "nothing planned yet" about a night the app opened on purpose),
the two dismissal keys, and that a `planned_empty` night — nobody home —
stays silent in both channels.

Nothing here reaches the network: the morning text is BUILT, never sent.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from app import tools
from app.db import get_conn
from conftest import household_today

REPO = Path(__file__).resolve().parent.parent
NOTIFICATIONS_PY = (REPO / "app" / "tools" / "notifications.py").read_text(encoding="utf-8")

# The HOUSEHOLD's today, never the process's: get_needs_you_items resolves
# "tonight" on households.timezone and the morning text is built for the
# household's own local day. See conftest.household_today.
TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
ISO_TOMORROW = (TODAY + dt.timedelta(days=1)).isoformat()
WEEK_START = (TODAY - dt.timedelta(days=1)).isoformat()

MORNING = dt.datetime.combine(TODAY, dt.time(7, 0))

DROPPED_REASON = "You cut Bean Chili back, so this one is yours to fill."


# ---------- seeding ----------

def _plan() -> int:
    """A household with two recipes saved and a live plan covering today.

    The recipes matter: _suggest_quick_dinners returns nothing without
    them, and the dinner_decision card is skipped entirely when it has
    nothing to offer.
    """
    tools.add_member("Emily")
    tools.set_member_age_group("Emily", "adult")
    tools.add_recipe(
        "Bean Chili", ingredients=[{"item": "Beans", "qty": "1 can"}],
        prep_time_minutes=10, cook_time_minutes=20, default_servings=2,
    )
    tools.add_recipe(
        "Quick Eggs", ingredients=[{"item": "Eggs", "qty": "6"}],
        prep_time_minutes=5, cook_time_minutes=5, default_servings=2,
    )
    return tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]


def _entry_id(plan: int, iso: str = ISO_TODAY, slot: str = "dinner") -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM meal_plan_entries WHERE weekly_plan_id = ? AND date = ? AND slot = ?",
        (plan, iso, slot),
    ).fetchone()
    conn.close()
    return row["id"]


def _slot_state(iso: str = ISO_TODAY) -> str | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT slot_state FROM meal_plan_entries WHERE household_id = ? AND date = ? AND slot = 'dinner'",
        (tools.household_id(), iso),
    ).fetchone()
    conn.close()
    return row["slot_state"] if row else None


def _open_tonight(plan: int, iso: str = ISO_TODAY) -> None:
    """Hand that night back the honest way — the Review stepper's "−".

    Not a hand-written row: drop_dish_from_day is what a household
    actually taps, and it is what writes the open_reason this file then
    asserts the bell repeats.
    """
    tools.plan_meal(iso, "Bean Chili", slot="dinner", weekly_plan_id=plan)
    tools.drop_dish_from_day(plan, _entry_id(plan, iso))


def _decision_tonight(plan: int) -> None:
    """Nothing planned for tonight at all — plan tomorrow so the band's
    'soonest undecided dinner' is today's and not a later one."""
    tools.plan_meal(ISO_TOMORROW, "Quick Eggs", slot="dinner", weekly_plan_id=plan)


SEEDS = {"dinner_decision": _decision_tonight, "dinner_open": _open_tonight}


# ---------- readers ----------

def _band_types() -> list[str]:
    return [i["type"] for i in tools.get_needs_you_items() if i["type"].startswith("dinner")]


def _dinner_bell() -> list[dict]:
    return [n for n in tools.get_active_notifications() if n["type"].startswith("dinner")]


def _text() -> str:
    return tools.build_morning_text(MORNING) or ""


# ---------- both shapes reach both channels ----------

@pytest.mark.parametrize("shape", sorted(SEEDS))
def test_a_dinner_waiting_on_the_household_rings_the_bell(shape):
    """CATCH for dinner_open, GUARD for dinner_decision.

    Parametrized on purpose: this is the assertion that used to name only
    `dinner_decision` and so passed while an open dinner rang nothing.
    """
    plan = _plan()
    SEEDS[shape](plan)
    assert _band_types() == [shape], _band_types()
    bell = _dinner_bell()
    assert [n["type"] for n in bell] == [shape], bell
    assert bell[0]["tab"] == "today"
    assert bell[0]["title"] and bell[0]["body"]


@pytest.mark.parametrize("shape", sorted(SEEDS))
def test_a_dinner_waiting_on_the_household_reaches_the_morning_text(shape):
    """CATCH for dinner_open, GUARD for dinner_decision. The morning text
    is the channel built to reach the house OUT of the app; it went quiet
    for exactly the nights the app itself could not answer."""
    plan = _plan()
    SEEDS[shape](plan)
    assert _text().startswith("Tonight's still open — ")


# ---------- the wording ----------

def test_the_bell_repeats_the_reason_the_app_itself_wrote():
    """CATCH. The card carries plan_slot_open's own sentence; the bell says
    the same words, so the two cannot disagree about one night."""
    plan = _plan()
    _open_tonight(plan)
    bell = _dinner_bell()[0]
    assert bell["body"] == DROPPED_REASON
    assert bell["title"] == "Tonight’s dinner needs your call"


def test_neither_channel_says_nothing_planned_about_a_night_the_app_opened():
    """CATCH. An open slot is not an empty one — the app tried, could not
    settle it, and said why. §8 doesn't let the app say a thing that isn't
    true, and "nothing planned yet" about that night isn't."""
    plan = _plan()
    _open_tonight(plan)
    assert "nothing's planned yet" not in _text()
    assert "Nothing planned yet" not in _dinner_bell()[0]["body"]
    assert _text().startswith("Tonight's still open — it's your call.")


def test_the_decision_shapes_own_words_are_untouched():
    """GUARD. Byte-for-byte what a household with no dinner row has always
    been told, on both channels. Mutation-checked: pointing the decision
    branch at the open branch's copy reddens this."""
    plan = _plan()
    _decision_tonight(plan)
    assert _text().startswith("Tonight's still open — nothing's planned yet.")
    assert _dinner_bell()[0]["body"] == "Nothing planned yet — take a look at tonight's options."
    assert _dinner_bell()[0]["title"] == "Tonight needs a dinner"
    assert _dinner_bell()[0]["action_label"] == "Show options"


def test_the_morning_text_is_a_line_not_the_whole_reason():
    """CATCH. The open reason can run past 120 characters and half of them
    open with a weekday name that argues with "Tonight" two words earlier,
    and this is the first line — the one build_morning_text always keeps.
    So the text nudges and the card carries the reason."""
    plan = _plan()
    tools.plan_slot_open(
        plan, ISO_TODAY, "dinner",
        open_reason=(
            "Thursday I’d rather ask than guess: I couldn’t settle this dinner "
            "without guessing at what you’d want. What would you prefer?"
        ),
    )
    text = _text()
    assert text.startswith("Tonight's still open — it's your call.")
    assert "rather ask than guess" not in text


def test_the_action_label_never_names_options_that_are_not_there():
    """CATCH. drop_dish_from_day opens a slot with no options at all and
    its card offers "Tell me what you'd like instead" in their place, so
    "Show options" would name a control that isn't on the screen."""
    plan = _plan()
    _open_tonight(plan)
    assert _dinner_bell()[0]["action_label"] == "Take a look"


def test_an_open_slot_that_does_offer_options_says_so():
    """CATCH. The leftovers repair hands back three things to tap; that
    card really does show options, so the bell may name them."""
    plan = _plan()
    tools.plan_slot_open(
        plan, ISO_TODAY, "dinner",
        open_reason="Monday I’d rather ask than guess: there’s nothing to reheat.",
        options=[{"label": "Something quick", "meta": "20 min or less"}],
    )
    assert _dinner_bell()[0]["action_label"] == "Show options"


# ---------- the dismissal keys ----------

def test_the_two_shapes_carry_their_own_dismissal_keys():
    """CATCH. Different news about the same night — "you haven't decided"
    against "I couldn't, and here's why" — so one dismissal must not
    silence the other."""
    plan = _plan()
    _open_tonight(plan)
    assert _dinner_bell()[0]["key"] == f"dinner_open:{ISO_TODAY}"

    tools.dismiss_notification(f"dinner_open:{ISO_TODAY}")
    assert _dinner_bell() == []

    # The night turns into a plain gap: different news, and it rings.
    tools.clear_plan_slot(plan, ISO_TODAY, "dinner")
    assert _slot_state() is None
    bell = _dinner_bell()
    assert [(n["type"], n["key"]) for n in bell] == [("dinner_decision", f"dinner_gap:{ISO_TODAY}")]


def test_a_dismissal_already_on_record_keeps_working():
    """GUARD. `dinner_gap:<date>` is deliberately left as the decision
    shape's key — renaming it would resurrect every card any household has
    ever tapped away. Mutation-checked: giving the decision shape the
    `dinner_open:` key reddens this."""
    plan = _plan()
    _decision_tonight(plan)
    assert _dinner_bell()[0]["key"] == f"dinner_gap:{ISO_TODAY}"
    tools.dismiss_notification(f"dinner_gap:{ISO_TODAY}")
    assert _dinner_bell() == []


def test_the_open_shapes_key_is_per_date_like_every_other_key_here():
    """GUARD. Dismissing one night never suppresses another — the reason
    every key in this feed carries a date (see get_active_notifications'
    docstring). Mutation-checked: dropping the date from the open key
    reddens this."""
    plan = _plan()
    tools.plan_meal(ISO_TODAY, "Quick Eggs", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_open(plan, ISO_TOMORROW, "dinner", open_reason="Tomorrow is yours to fill.")
    tools.dismiss_notification(f"dinner_open:{ISO_TODAY}")
    bell = _dinner_bell()
    assert [(n["type"], n["key"]) for n in bell] == [("dinner_open", f"dinner_open:{ISO_TOMORROW}")]


# ---------- tomorrow, and the night nobody is home ----------

def test_tomorrows_open_dinner_is_not_in_tonights_text():
    """CATCH. The feed's nudge covers tonight OR tomorrow and the text is
    about today — the same rule test_morning_text.py already pins for the
    decision shape, now true of this one too."""
    plan = _plan()
    tools.plan_meal(ISO_TODAY, "Quick Eggs", slot="dinner", weekly_plan_id=plan)
    tools.plan_slot_open(plan, ISO_TOMORROW, "dinner", open_reason="Tomorrow is yours to fill.")
    assert _band_types() == ["dinner_open"]
    assert [n["type"] for n in _dinner_bell()] == ["dinner_open"]
    assert "still open" not in _text()


def test_a_night_nobody_is_home_stays_silent_everywhere():
    """GUARD, and it is a guard on the whole pipeline rather than on this
    change: the silence comes from get_needs_you_items never emitting a
    card for a `planned_empty` night, one module up, so no mutation of
    notifications.py or digest.py can redden it. It is here because
    "planned_empty must stay silent" is this ticket's own criterion and
    because the shape that would break it — a future reader deciding an
    empty night is a question — is exactly what this branch just did to
    `open`.
    """
    plan = _plan()
    tools.plan_slot_empty(plan, ISO_TODAY, "dinner", reason="You're out — nothing to cook.")
    # Tomorrow planned too, or its genuine gap becomes the band's card.
    tools.plan_meal(ISO_TOMORROW, "Quick Eggs", slot="dinner", weekly_plan_id=plan)
    assert _slot_state() == "planned_empty"
    assert _band_types() == []
    assert _dinner_bell() == []
    assert "still open" not in _text()


# ---------- the comment, and the dead day word ----------

def test_the_comment_above_the_filter_is_true_again():
    """CATCH. The filter's own comment promised the notification and the
    band never disagree about what's open, and the next line made it
    false. A now-false comment is corrected in the change that makes it
    false — or, as here, in the change that makes it true."""
    assert "never disagree about what's open" in NOTIFICATIONS_PY
    assert '("dinner_decision", "dinner_open")' in NOTIFICATIONS_PY


def test_the_dead_server_clock_day_word_is_gone():
    """CATCH. `day_label` was computed off date.today() and read by
    nothing — it looked like the household-clock bug class this repo has
    been sweeping and was simply dead code. Deleted rather than moved onto
    the household's clock, because the band item's title already carries
    the day word and a second one would be a second answer."""
    # The name survives in the comment that explains the deletion, which
    # is the whole point of the comment; what must not come back is the
    # read. Anything that assigned it would match "day_label =".
    assert "day_label =" not in NOTIFICATIONS_PY
    assert "Don't reinstate it" in NOTIFICATIONS_PY
