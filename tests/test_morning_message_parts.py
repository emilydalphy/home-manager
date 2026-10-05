"""
The morning message is the parts the household chose — Loop Board "Morning
message: the household chooses what it includes (today's meals, freezer,
prep, start time, shop day, who's away)", from Gowthami's household on
2026-10-04: "we want to have the notification customize what kind of
details to include".

ONE setting for the whole household (Emily, 2026-10-04, overriding the
card's own "per person": one setting "to keep it simple"), stored on
`households.morning_text_parts`.

WHAT IS PINNED HERE, and what each half is worth as evidence:

- the MAPPING, which is the thing a reader will want to know first: the
  six boxes against the lines `_digest_parts` already built. Today's meals
  is three of them (tonight, the day's other cooks, a lunch made ahead);
  start time and who's away are new; the attention queue's item and the
  use-it-up nudge are a seventh part nobody is offered a box for.
- the STORAGE, where '' ("nobody asked") has to be told from '[]' ("none
  of the six") or a household that unticked everything reads back as the
  three defaults next time.
- the BUDGET, which is the one thing six switchable parts genuinely
  changed: admission is by part now, so three fridge moves cannot eat the
  room a ticked part was waiting for.
- the PREVIEW being the SENDER's own text, which is the card's test and is
  driven through the real pass with a stub channel rather than by reading
  the composer twice.

Most of this is behaviour and is driven. The front end is deliberately
NOT here: the Settings section and its live preview are another session's
(static/shell.js was owned by another builder the night this was written),
and the spec for them is in the Decision log entry. So nothing in this
file is a source marker.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app import households, tools
from app.db import get_conn
from app.tools import digest
from conftest import household_today

# The HOUSEHOLD's today, never the process's: the message is built off
# today_moves for the household's own local day.
TODAY = household_today()
ISO_TODAY = TODAY.isoformat()
WEEK_START = (TODAY - dt.timedelta(days=2)).isoformat()
MORNING = dt.datetime.combine(TODAY, dt.time(7, 0))


# ---------- helpers ----------

def _adults(*names):
    for n in names or ("Emily", "Vineeth"):
        tools.add_member(n)
        tools.set_member_age_group(n, "adult")


def _member_id(name: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM members WHERE household_id = ? AND name = ?", (tools.household_id(), name)
    ).fetchone()
    conn.close()
    return row["id"]


def _stored() -> str:
    conn = get_conn()
    row = conn.execute(
        "SELECT morning_text_parts FROM households WHERE id = ?", (tools.household_id(),)
    ).fetchone()
    conn.close()
    return row["morning_text_parts"]


def _seed_day(*, dinner=True, lunch=False, fridge=False, prep=False, shop=False):
    """
    A day with a thing on it per part. The dinner carries minutes, which is
    what gives the start-time part a clock to name; `shop` puts the dinner's
    OWN ingredients on the list, because since 2026-09-16 a shop move only
    names a deadline for something a cook is actually waiting on.
    """
    tools.add_recipe(
        "Chicken Skewers",
        ingredients=[{"item": "Chicken Thighs", "qty": "1 lb"}],
        prep_time_minutes=10, cook_time_minutes=25, default_servings=3,
    )
    plan_id = tools.create_weekly_plan(WEEK_START)["weekly_plan_id"]
    if dinner:
        tools.plan_meal(ISO_TODAY, "Chicken Skewers", slot="dinner", weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=shop)
    if lunch:
        tools.add_recipe("Chicken Salad", ingredients=[{"item": "Lettuce", "qty": "1 head"}],
                         prep_time_minutes=10, cook_time_minutes=0, default_servings=3)
        tools.plan_meal(ISO_TODAY, "Chicken Salad", slot="lunch", weekly_plan_id=plan_id,
                        add_ingredients_to_grocery_list=False)
    conn = get_conn()
    if fridge:
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'defrost')",
            (tools.household_id(), plan_id, ISO_TODAY,
             "Move the chicken thighs to the fridge — for Thursday’s skewers.", "Chicken Skewers"),
        )
    if prep:
        conn.execute(
            "INSERT INTO prep_tasks (household_id, weekly_plan_id, task_date, description, "
            "related_meal, status, task_type) VALUES (?, ?, ?, ?, ?, 'pending', 'general')",
            (tools.household_id(), plan_id, ISO_TODAY, "Chop the onions", "Chicken Skewers"),
        )
    conn.commit()
    conn.close()
    return plan_id


def _everything(**kw):
    return _seed_day(dinner=True, lunch=True, fridge=True, prep=True, shop=True, **kw)


def _lines(parts=None, now=MORNING) -> list[str]:
    return digest.morning_text_preview(now_local=now, parts=parts)["lines"]


@pytest.fixture
def link(monkeypatch):
    """The production shape: a text with the app's address on the end."""
    monkeypatch.setenv("HOME_MANAGER_URL", "https://pomona.example")


@pytest.fixture
def twilio_env(monkeypatch):
    """Keys set, so the pass reaches the channel; the channel is a stub."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-never-logged")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+16475550100")


def _at(hour: int, minute: int = 0):
    """A wall-clock moment in TORONTO, handed to the loop as UTC."""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    return dt.datetime.combine(
        TODAY, dt.time(hour, minute), tzinfo=ZoneInfo("America/Toronto")
    ).astimezone(timezone.utc)


@pytest.fixture
def nolink(monkeypatch):
    monkeypatch.delenv("HOME_MANAGER_URL", raising=False)
    monkeypatch.setattr(digest, "PUBLIC_BASE_URL", "")


# ---------------------------------------------------------------------------
# What is on offer, and what a household that has never been asked gets
# ---------------------------------------------------------------------------

def test_the_six_boxes_are_the_cards_six_in_the_cards_order():
    """CATCH. The order is the message's order too, not only the screen's."""
    assert tools.MORNING_PART_CHOICES == ("meals", "freezer", "prep", "start", "shop", "away")


def test_an_unanswered_household_gets_the_three_the_card_switches_on():
    """
    CATCH, and the one that says plainly what the card's defaults COST: the
    shop reminder is in every morning text today and is not one of the
    three, so it is off until somebody ticks it. One tuple to change.
    """
    _adults()
    assert tools.MORNING_PART_DEFAULTS == ("meals", "freezer", "prep")
    assert tools.morning_text_parts() == ["meals", "freezer", "prep"]
    assert _stored() == "", "nobody has answered, and that is what is on disk"


def test_the_settings_carry_every_box_with_its_words_and_whether_it_is_ticked():
    """
    CATCH. The screen draws the section off `part_choices` rather than
    holding its own copy of the six, so rewording one is a change to
    MORNING_PART_WORDS and nothing else.
    """
    _adults()
    choices = tools.get_morning_text_settings()["part_choices"]
    assert [c["key"] for c in choices] == list(tools.MORNING_PART_CHOICES)
    assert [c["on"] for c in choices] == [True, True, True, False, False, False]
    assert [c["default"] for c in choices] == [True, True, True, False, False, False]
    for c in choices:
        assert c["label"] and c["says"], c
        assert c["label"] == tools.MORNING_PART_WORDS[c["key"]]["label"]
        assert c["says"] == tools.MORNING_PART_WORDS[c["key"]]["says"]


def test_every_sentence_a_person_reads_about_this_is_in_the_one_block():
    """
    GUARD on the card's own ask — "keep the strings in one place so they're
    easy to change, because Emily will review the wording". Every label,
    every description and the one line this module composes itself are in
    MORNING_PART_WORDS / MORNING_START_LINE, so none of them may be a
    literal anywhere else in app/.

    Pinned by mutation: hard-code "Start cooking at" into _digest_parts and
    this fails. Green either way for the labels, because nothing else has
    ever said them — which is the state it exists to keep.
    """
    import ast
    import pathlib
    said = [w["label"] for w in tools.MORNING_PART_WORDS.values()]
    said += [w["says"] for w in tools.MORNING_PART_WORDS.values()]
    said.append(tools.MORNING_START_LINE.split("{")[0].strip())

    # STRING LITERALS, read with ast — not raw source. A comment is allowed
    # to mention a phrase (agent.py has "Today's meals" in two of them, about
    # a different thing entirely); what must not exist is a second LITERAL,
    # because a literal is what reaches a person. This is the repo's own
    # comment-stripping idiom done with the parser instead of a regex.
    app = pathlib.Path(__file__).resolve().parent.parent / "app"
    literals: list[str] = []
    for path in sorted(app.rglob("*.py")):
        if path.name == "digest.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
    for phrase in said:
        where = [text[:70] for text in literals if phrase in text]
        assert not where, f"{phrase!r} is a literal outside digest.py's one block: {where}"


# ---------------------------------------------------------------------------
# The mapping: which lines belong to which box
# ---------------------------------------------------------------------------

def test_todays_meals_is_tonight_the_days_other_cooks_and_a_lunch_made_ahead(nolink):
    """
    CATCH. Three of the lines _digest_parts already built are one part,
    because all three are what you're eating today — and one block, so
    "Lunch: …" sits under "Tonight: …" rather than after the prep.
    """
    _adults()
    _everything()
    assert _lines(["meals"]) == ["Tonight: Chicken Skewers.", "Lunch: Chicken Salad."]


def test_the_freezer_is_the_fridge_moves_and_nothing_else(nolink):
    _adults()
    _everything()
    assert _lines(["freezer"]) == [
        "Move the chicken thighs to the fridge — for Thursday’s skewers."
    ]


def test_the_prep_is_the_prep_moves_and_nothing_else(nolink):
    _adults()
    _everything()
    assert _lines(["prep"]) == ["Chop the onions — by tonight."]


def test_the_shop_is_the_timed_shop_move_and_nothing_else(nolink):
    _adults()
    _everything()
    lines = _lines(["shop"])
    assert len(lines) == 1 and lines[0].startswith("Shop for tonight — 1 item, by")


def test_the_start_time_is_a_line_of_its_own_naming_only_the_clock(nolink):
    """
    CATCH. The clock, never a clause on "Tonight:" — the two are separate
    boxes, so each has to stand without the other, and a start line that
    named the dish would name the dish the household had just unticked.
    """
    _adults()
    _everything()
    lines = _lines(["start"])
    assert len(lines) == 1
    assert lines[0] == "Start cooking at 5:55."
    assert "Skewers" not in lines[0], "the start line must stand without the meals line"


def test_who_is_away_is_attendances_own_sentence_word_for_word(nolink):
    """
    CATCH. summary_line is where "Dinner for 1 — Vineeth’s out." lives, and
    a second copy here is the two-implementations trap that function's own
    docstring records being bitten by.
    """
    _adults()
    _everything()
    tools.set_member_attendance(ISO_TODAY, "dinner", _member_id("Vineeth"), False)
    said = tools.get_slot_attendance(ISO_TODAY, "dinner")["summary"]
    assert said == "Dinner for 1 — Vineeth’s out."
    assert _lines(["away"]) == [said]


def test_everyone_home_means_the_away_part_says_nothing(nolink):
    """
    CATCH on the card's "parts with nothing today are left out", for the
    part where that falls out of the sentence rather than being coded:
    summary_line is '' when nobody is missing and no guests are coming.
    """
    _adults()
    _everything()
    assert _lines(["away"]) == []
    assert digest.morning_text_preview(now_local=MORNING, parts=["away"])["would_send"] is False


def test_guests_are_in_the_away_line_because_the_box_asks_who_is_at_the_table(nolink):
    """
    CHARACTERISATION rather than a catch: "Who's away tonight" shows
    "Dinner for 4 — with 2 guests." Deliberate — it is attendance's own
    sentence, and an away-only variant would be a second one. Invert this
    if Emily wants the box to be strictly about absences.
    """
    _adults()
    _everything()
    tools.set_guest_count(ISO_TODAY, "dinner", 2)
    # "with" only arrives when somebody is ALSO out — summary_line's own
    # rule, said here as it is said rather than as it might read.
    assert _lines(["away"]) == ["Dinner for 4 — 2 guests."]


# ---------------------------------------------------------------------------
# The seventh part nobody is offered a box for
# ---------------------------------------------------------------------------

def test_the_kitchen_nudge_is_a_part_that_is_not_a_choice(nolink):
    """
    CATCH. The attention queue's item and the use-it-up nudge are in the
    morning text today and belong to none of the card's six, so they are a
    part that is always in — which is neither a seventh checkbox the design
    has not got nor two lines silently lost.
    """
    _adults()
    assert "kitchen" in tools.MORNING_PARTS
    assert "kitchen" not in tools.MORNING_PART_CHOICES
    assert tools.MORNING_PARTS_ALWAYS == ("kitchen",)
    tools.add_attention_item(kind="use_soon", summary="Use up the spinach")
    tools.set_morning_text_parts([])
    pv = digest.morning_text_preview(now_local=MORNING)
    assert pv["chosen"] == []
    assert pv["parts"] == ["kitchen"]
    assert pv["lines"] == ["Use up the spinach."]


def test_the_kitchen_part_is_last_so_it_never_outranks_a_chosen_one(nolink):
    _adults()
    _everything()
    tools.add_attention_item(kind="use_soon", summary="Use up the spinach")
    pv = digest.morning_text_preview(now_local=MORNING, parts=list(tools.MORNING_PART_CHOICES))
    assert pv["parts"][-1] == "kitchen"
    assert pv["lines"][-1] == "Use up the spinach."


# ---------------------------------------------------------------------------
# The message is the chosen parts only, in the card's order
# ---------------------------------------------------------------------------

def test_all_six_read_in_the_cards_order(nolink):
    """CATCH. The order is the card's, which puts the prep ahead of the
    shop — today's text had them the other way round."""
    _adults()
    _everything()
    tools.set_member_attendance(ISO_TODAY, "dinner", _member_id("Vineeth"), False)
    pv = digest.morning_text_preview(now_local=MORNING, parts=list(tools.MORNING_PART_CHOICES))
    assert pv["parts"] == ["meals", "freezer", "prep", "start", "shop", "away"]
    assert pv["lines"] == [
        "Tonight: Chicken Skewers.",
        "Lunch: Chicken Salad.",
        "Move the chicken thighs to the fridge — for Thursday’s skewers.",
        "Chop the onions — by tonight.",
        "Start cooking at 5:55.",
        pv["lines"][5],
        "Dinner for 1 — Vineeth’s out.",
    ]
    assert pv["lines"][5].startswith("Shop for tonight")


def test_a_part_nobody_chose_is_not_in_the_message(nolink):
    """CATCH — the card's "built from the chosen parts only"."""
    _adults()
    _everything()
    text = tools.build_morning_text(MORNING, parts=["meals"])
    assert text == "Tonight: Chicken Skewers. Lunch: Chicken Salad."
    for absent in ("fridge", "Chop", "Shop", "Start cooking"):
        assert absent not in text


def test_a_chosen_part_with_nothing_today_is_simply_left_out(nolink):
    """CATCH. A day with a dinner and nothing else, every box ticked."""
    _adults()
    _seed_day()
    pv = digest.morning_text_preview(now_local=MORNING, parts=list(tools.MORNING_PART_CHOICES))
    assert pv["parts"] == ["meals", "start"], "no fridge move, no prep, no shop, everyone home"
    assert pv["would_send"] is True


def test_nothing_in_any_chosen_part_is_no_message_at_all(nolink):
    """
    CATCH — the card's "if everything is empty that day, no message is
    sent". A day with only a fridge move, for a household that asked about
    meals alone.
    """
    _adults()
    # A day with real news on it — a dinner and a fridge move — where the
    # one box ticked is the one with nothing behind it (nothing is on the
    # shopping list). The first cut of this test chose "meals" on a day with
    # no dinner, and the meals part correctly says "Tonight's still open",
    # so it never reached the claim it is named for.
    _seed_day(dinner=True, fridge=True, shop=False)
    pv = digest.morning_text_preview(now_local=MORNING, parts=["shop"])
    assert pv["would_send"] is False
    assert pv["text"] is None and pv["lines"] == []
    assert tools.build_morning_text(MORNING, parts=["shop"]) is None


def test_the_sender_records_the_skip_when_the_chosen_parts_are_empty(twilio_env):
    """
    CATCH, through the real pass: a household whose chosen parts have
    nothing in them today is a skipped-empty row, not a text saying so.
    The day it is handed has real news on it (a fridge move) — it is the
    CHOICE that makes the morning empty, which is the whole point.
    """
    _adults("Emily")
    _seed_day(dinner=True, fridge=True, shop=False)
    tools.set_morning_text(phone="4165550100", on=True)
    tools.set_morning_text_parts(["shop"])
    sent: list[tuple[str, str]] = []
    res = tools.run_morning_texts_once(
        now_utc=_at(7, 30), send=lambda to, body: sent.append((to, body)) or {"status": "ok"}
    )
    assert [r["status"] for r in res] == ["skipped-empty"]
    assert sent == []


# ---------------------------------------------------------------------------
# Storage: '' is not '[]'
# ---------------------------------------------------------------------------

def test_none_of_the_six_is_a_real_answer_and_does_not_read_back_as_the_three():
    """
    CATCH, and the reason this is one TEXT column rather than six INTEGERs
    or a column plus an answered-flag: '' means nobody asked and '[]' means
    they said none, and a column whose default IS a valid answer cannot
    tell those apart (meal_preferences.snacks_per_week_set is where this
    repo learned that).
    """
    _adults()
    assert tools.set_morning_text_parts([]) == []
    assert _stored() == "[]"
    assert tools.morning_text_parts() == []
    assert tools.get_morning_text_settings()["parts"] == []


def test_the_answer_is_stored_in_message_order_whatever_order_it_arrives_in():
    _adults()
    assert tools.set_morning_text_parts(["away", "meals", "shop"]) == ["meals", "shop", "away"]
    assert json.loads(_stored()) == ["meals", "shop", "away"]


def test_a_key_nobody_knows_is_refused_in_a_sentence_and_nothing_is_stored():
    """
    CATCH. A screen or a model sending an unknown key has a bug, and
    storing fewer parts than it asked for is how that bug survives.
    """
    _adults()
    tools.set_morning_text_parts(["meals"])
    with pytest.raises(ValueError) as e:
        tools.set_morning_text_parts(["meals", "biscuits"])
    assert "biscuits" in str(e.value)
    assert "meals, freezer, prep, start, shop, away" in str(e.value)
    assert tools.morning_text_parts() == ["meals"], "the refusal wrote nothing"


def test_a_stored_key_this_version_does_not_know_is_dropped_rather_than_carried():
    """
    GUARD. Renaming a part is a rename, not a migration: a key left on disk
    by an older deploy is dropped on the way out. Pinned by mutation — have
    _read_parts return the stored list as it stands and this fails.
    """
    _adults()
    conn = get_conn()
    conn.execute(
        "UPDATE households SET morning_text_parts = ? WHERE id = ?",
        (json.dumps(["meals", "gone_in_a_later_version"]), tools.household_id()),
    )
    conn.commit()
    conn.close()
    assert tools.morning_text_parts() == ["meals"]


def test_a_blob_nothing_can_read_falls_back_to_the_defaults_rather_than_crashing(caplog):
    """
    GUARD, the stance _zone already takes towards an unusable timezone: a
    bad stored value must not cost everybody their morning. Pinned by
    mutation — let json.loads raise out of _read_parts and this fails.
    """
    _adults()
    for bad in ("not json at all", '{"meals": true}', "17"):
        conn = get_conn()
        conn.execute(
            "UPDATE households SET morning_text_parts = ? WHERE id = ?", (bad, tools.household_id())
        )
        conn.commit()
        conn.close()
        assert tools.morning_text_parts() == list(tools.MORNING_PART_DEFAULTS), bad


def test_the_setting_stays_inside_its_household():
    """CATCH on the isolation, driven over the boundary rather than read."""
    _adults("Emily")
    tools.set_morning_text_parts(["shop"])
    other = households.create_household("The Beta Testers", "a-safe-distinct-passphrase")
    with tools.use_household(other):
        _adults("Julia")
        assert tools.morning_text_parts() == list(tools.MORNING_PART_DEFAULTS)
        tools.set_morning_text_parts(["away"])
        assert tools.morning_text_parts() == ["away"]
    assert tools.morning_text_parts() == ["shop"]


# ---------------------------------------------------------------------------
# One setting for the whole household, not one per member
# ---------------------------------------------------------------------------

def test_the_parts_are_the_households_however_the_change_is_addressed():
    """
    CATCH on Emily's 2026-10-04 override of the card's "per person":
    everyone who gets the message gets the same parts, so a change made in
    one adult's name changes it for the other too — the same way the hour
    and the zone always have.
    """
    _adults("Emily", "Vineeth")
    tools.set_morning_text(name="Emily", parts=["meals", "shop"])
    assert tools.set_morning_text(name="Vineeth")["parts"] == ["meals", "shop"]
    assert tools.get_morning_text_settings()["parts"] == ["meals", "shop"]


def test_the_chat_door_adds_and_drops_one_part_in_one_turn():
    """
    CATCH. Nothing hands the model the current set to edit, so "also tell
    me when to start cooking" has to work as a delta or it cannot work in
    one turn at all.
    """
    _adults("Emily")
    tools.set_morning_text_parts(["meals"])
    assert tools.set_morning_text(add_parts=["start"])["parts"] == ["meals", "start"]
    assert tools.set_morning_text(drop_parts=["meals"])["parts"] == ["start"]


def test_a_delta_on_an_unanswered_household_composes_off_the_defaults():
    """
    CATCH. "Also tell me about the shop" from a household that has never
    been asked adds to the three the card switches on — it does not replace
    them with one part.
    """
    _adults("Emily")
    assert tools.set_morning_text(add_parts=["shop"])["parts"] == [
        "meals", "freezer", "prep", "shop"
    ]


def test_all_three_ways_in_compose_in_one_order_and_never_need_a_refusal():
    _adults("Emily")
    assert tools.set_morning_text(
        parts=["meals", "freezer"], add_parts=["shop"], drop_parts=["meals"]
    )["parts"] == ["freezer", "shop"]


def test_the_chat_tool_offers_the_six_keys_and_nothing_else():
    """
    GUARD. The model picks from an enum rather than inventing a word, and
    the enum is read off MORNING_PART_CHOICES so it cannot drift from what
    the tool will accept.
    """
    from app import agent
    schema = next(t for t in agent.TOOL_DEFINITIONS if t["name"] == "set_morning_text")
    props = schema["input_schema"]["properties"]
    for field in ("parts", "add_parts", "drop_parts"):
        assert props[field]["items"]["enum"] == list(tools.MORNING_PART_CHOICES), field


def test_a_part_change_leaves_the_number_and_the_hour_alone():
    """GUARD on the existing promise that only what's given changes."""
    _adults("Emily")
    tools.set_morning_text(phone="4165550100", on=True, time="6:30")
    before = tools.get_morning_text_settings()
    tools.set_morning_text(parts=["away"])
    after = tools.get_morning_text_settings()
    assert after["time"] == before["time"] == "06:30"
    assert after["adults"] == before["adults"]


# ---------------------------------------------------------------------------
# The character budget, with six switchable parts in front of it
# ---------------------------------------------------------------------------

def test_every_chosen_part_gets_its_first_line_before_any_part_gets_a_second():
    """
    CATCH, and the answer to what six switchable parts do to a budget.
    Before this, lines were offered strictly in order, so an early part's
    extra lines could eat the room a part the household had explicitly
    ticked was waiting for — and the part that lost its only line was
    always the one furthest down.

    Driven on the trimmer directly rather than through a seeded day,
    because what has to be pinned is the admission rule and not one
    household's dish names: two meals lines and a shop line against a
    budget that fits two of the three.
    """
    tagged = [("meals", "A" * 40), ("meals", "B" * 40), ("shop", "C" * 40)]
    lines, said, dropped = digest._trim_to_budget(list(tagged), 85)
    assert lines == ["A" * 40, "C" * 40]
    assert said == ["meals", "shop"]
    assert dropped == []


def test_the_lines_still_come_out_in_part_order_whatever_got_in():
    """GUARD. Admission changed; the order of the message did not."""
    tagged = [("meals", "A" * 40), ("meals", "B" * 40), ("freezer", "C" * 40), ("shop", "D" * 40)]
    lines, said, dropped = digest._trim_to_budget(list(tagged), 125)
    assert lines == ["A" * 40, "C" * 40, "D" * 40]
    assert said == ["meals", "freezer", "shop"]


def test_a_part_the_budget_cut_outright_is_named_so_the_screen_can_say_so():
    """
    CATCH. A box the household ticked that said nothing today because the
    message ran out of room is worth a sentence under the preview. A part
    that got its FIRST line and lost a later one is not in `dropped` —
    that is what the budget has always done to a long day.
    """
    tagged = [("meals", "A" * 60), ("freezer", "B" * 60), ("shop", "C" * 60)]
    lines, said, dropped = digest._trim_to_budget(list(tagged), 121)
    assert lines == ["A" * 60, "B" * 60]
    assert said == ["meals", "freezer"]
    assert dropped == ["shop"]


def test_the_first_line_is_still_always_kept_trimmed_if_it_must_be():
    """GUARD on the rule that predates this: a text that says "Tonight:
    chicken tacos" and nothing else is still the text."""
    lines, said, dropped = digest._trim_to_budget([("meals", "A" * 400), ("shop", "B" * 20)], 50)
    assert len(lines) == 1 and lines[0].endswith("…") and len(lines[0]) == 50
    assert said == ["meals"] and dropped == ["shop"]


def test_the_whole_message_still_fits_two_segments(link):
    """GUARD. Six parts' worth of a busy day is still one short message."""
    _adults()
    _everything()
    tools.set_member_attendance(ISO_TODAY, "dinner", _member_id("Vineeth"), False)
    tools.add_attention_item(kind="use_soon", summary="Use up the spinach")
    text = tools.build_morning_text(MORNING, parts=list(tools.MORNING_PART_CHOICES))
    assert len(text) <= tools.MORNING_TEXT_MAX_CHARS
    assert "\n" not in text, "a text is a line, not a page"
    assert "!" not in text


# ---------------------------------------------------------------------------
# The preview IS the sender's text — the card's own test
# ---------------------------------------------------------------------------

def test_the_preview_is_the_text_the_sender_would_send(link, twilio_env):
    """
    CATCH, and the card's own test — driven through the REAL daily pass
    with a stub channel, so what is compared is the string that reached
    `send` and not a second reading of the composer.
    """
    _adults("Emily")
    _everything()
    tools.set_morning_text(phone="4165550100", on=True)
    tools.set_morning_text_parts(["meals", "start", "shop"])
    sent: list[tuple[str, str]] = []
    res = tools.run_morning_texts_once(
        now_utc=_at(7, 30), send=lambda to, body: sent.append((to, body)) or {"status": "ok"}
    )
    assert [r["status"] for r in res] == ["ok"], res
    preview = tools.morning_text_preview(now_local=dt.datetime.combine(TODAY, dt.time(7, 30)))
    assert sent[0][1] == preview["text"]
    assert preview["text"].endswith(" https://pomona.example/")


def test_the_preview_also_carries_the_push_body_which_has_no_address(link):
    """
    CATCH. Push sends the same lines without the link (app/push.py), so the
    screen can show either without composing one itself.
    """
    _adults()
    _everything()
    pv = tools.morning_text_preview(now_local=MORNING, parts=["meals"])
    assert pv["text"].endswith(" https://pomona.example/")
    assert pv["push_text"] == "Tonight: Chicken Skewers. Lunch: Chicken Salad."
    assert pv["push_text"] == tools.build_morning_text(MORNING, link=False, parts=["meals"])


def test_the_preview_reads_the_stored_answer_when_it_is_given_no_override(nolink):
    _adults()
    _everything()
    tools.set_morning_text_parts(["prep"])
    pv = tools.morning_text_preview(now_local=MORNING)
    assert pv["chosen"] == ["prep"]
    assert pv["lines"] == ["Chop the onions — by tonight."]


def test_an_override_previews_without_saving_so_the_boxes_can_repaint(nolink):
    """
    CATCH, and what makes the screen half trivial: the preview under the
    boxes can show "as it would read if you ticked this" before anything is
    saved.
    """
    _adults()
    _everything()
    tools.set_morning_text_parts(["prep"])
    pv = tools.morning_text_preview(now_local=MORNING, parts=["meals", "shop"])
    assert pv["chosen"] == ["meals", "shop"]
    assert tools.morning_text_parts() == ["prep"], "a preview writes nothing"


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------

def test_the_routes_read_the_boxes_save_them_and_preview_in_one_trip(signed_in):
    _adults("Emily")
    _everything()
    got = signed_in.get("/api/morning-text").json()
    assert got["parts"] == ["meals", "freezer", "prep"]
    assert [c["key"] for c in got["part_choices"]] == list(tools.MORNING_PART_CHOICES)

    res = signed_in.post("/api/morning-text/parts", json={"parts": ["meals", "away"]})
    assert res.status_code == 200
    body = res.json()
    assert body["parts"] == ["meals", "away"]
    assert body["settings"]["parts"] == ["meals", "away"]
    # The fresh preview rides back with the save, so the boxes and the
    # message under them land together rather than the screen showing a
    # preview of the answer before last.
    assert body["preview"]["chosen"] == ["meals", "away"]
    assert body["preview"]["lines"][0] == "Tonight: Chicken Skewers."


def test_the_preview_route_with_no_parts_is_exactly_todays_message(signed_in):
    _adults("Emily")
    _everything()
    signed_in.post("/api/morning-text/parts", json={"parts": ["meals", "prep"]})
    pv = signed_in.get("/api/morning-text/preview").json()
    assert pv["chosen"] == ["meals", "prep"]
    # Not `a == b or would_send`, which the first cut wrote and which cannot
    # fail. The route reads the household's own clock, so this compares the
    # LINES rather than the whole text: the clock decides which day's moves
    # are read, and a test that pinned a wall-clock instant here would be
    # pinning the test's hour and not the route's answer.
    assert pv["would_send"] is True
    assert pv["lines"] == [
        "Tonight: Chicken Skewers.",
        "Lunch: Chicken Salad.",
        "Chop the onions — by tonight.",
    ]


def test_the_preview_route_takes_an_override_and_tells_none_from_unasked(signed_in):
    """
    CATCH on the one subtlety in the route: `?parts=` (empty) is the real
    answer "none of the six", and no `parts` at all is "no override" — so
    the two are told apart by the parameter being ABSENT rather than by it
    being falsy.
    """
    _adults("Emily")
    _everything()
    both = signed_in.get("/api/morning-text/preview?parts=meals,shop").json()
    assert both["chosen"] == ["meals", "shop"]
    none = signed_in.get("/api/morning-text/preview?parts=").json()
    assert none["chosen"] == [] and none["would_send"] is False and none["text"] is None
    unasked = signed_in.get("/api/morning-text/preview").json()
    assert unasked["chosen"] == ["meals", "freezer", "prep"]


def test_the_preview_route_drops_a_stale_key_rather_than_refusing(signed_in):
    """A screen a deploy left holding a key this version no longer knows
    should show a preview, not an error."""
    _adults("Emily")
    _everything()
    res = signed_in.get("/api/morning-text/preview?parts=meals,gone_in_a_later_version")
    assert res.status_code == 200
    assert res.json()["chosen"] == ["meals"]


def test_the_save_route_refuses_a_key_nobody_knows_with_the_sentence(signed_in):
    _adults("Emily")
    res = signed_in.post("/api/morning-text/parts", json={"parts": ["meals", "biscuits"]})
    assert res.status_code == 400
    assert "biscuits" in res.json()["detail"]
    assert tools.morning_text_parts() == ["meals", "freezer", "prep"], "nothing was stored"


def test_the_save_route_needs_no_member_id_because_the_setting_is_the_households(signed_in):
    """
    CATCH on the shape of the route, which is the shape of the decision:
    its two neighbours are each about one adult's number and switch and so
    carry a member_id; this one is the household's and does not.
    """
    from app.main import MorningPartsRequest
    assert set(MorningPartsRequest.model_fields) == {"parts"}
    _adults("Emily", "Vineeth")
    assert signed_in.post("/api/morning-text/parts", json={"parts": ["shop"]}).status_code == 200


def test_both_routes_are_signed_in_only(client):
    assert client.get("/api/morning-text/preview").status_code == 401
    assert client.post("/api/morning-text/parts", json={"parts": []}).status_code == 401


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

def test_the_column_lands_on_a_database_that_predates_it(tmp_path, monkeypatch):
    """
    CATCH. Emily's shape: schema.sql with this column cut out of it, opened
    by the new code. '' on every existing household — the truth, since
    nobody has been asked — read back as the three the card switches on.
    NOT backfilled with those three, because '[]' has to stay available as
    a real answer and a backfill would spend the sentinel.
    """
    import pathlib
    import re
    import sqlite3

    import app.db as db_module

    schema = (pathlib.Path(__file__).resolve().parent.parent / "app" / "schema.sql").read_text()
    older = re.sub(
        r"\n    -- Which parts the morning message.*?morning_text_parts TEXT NOT NULL DEFAULT '',",
        "", schema, flags=re.S,
    )
    assert "morning_text_parts" not in older, "the cut did not take"

    path = tmp_path / "before_the_deploy.db"
    conn = sqlite3.connect(path)
    conn.executescript(older)
    conn.execute("UPDATE households SET name = 'Before the deploy' WHERE id = 1")
    conn.commit()
    assert "morning_text_parts" not in {r[1] for r in conn.execute("PRAGMA table_info(households)")}
    conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", str(path))
    db_module.init_db()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert "morning_text_parts" in {r[1] for r in conn.execute("PRAGMA table_info(households)")}
    row = conn.execute("SELECT morning_text_parts FROM households WHERE id = 1").fetchone()
    conn.close()
    assert row["morning_text_parts"] == ""
    assert digest._read_parts(row["morning_text_parts"]) == list(tools.MORNING_PART_DEFAULTS)
