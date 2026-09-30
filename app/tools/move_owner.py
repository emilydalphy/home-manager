"""
Whose move is it — one answer, read by Today, by Cook, and by nothing else.

Loop Board "Every move has an owner (slice 1)": *as an adult in the
household, I want each cook, prep and shop on Today to show whose it is,
so that we both know without asking.*

This is NOT a second "who cooks" source. `cooking_role` (rhythm.py, the
third of the six locked rhythm questions) has been the app's answer since
2026-09-03 and `cooker._cook_name` has been reading its `one_person` half
onto Cook's Tonight card since 2026-09-13. That function now delegates
here in one line; this module is that same answer widened to the other two
values of the same fact, and put on a move rather than on the view.

Three values, three answers:

    one_person     the name the household gave, on every cook.
    turns          the adults, in the order setup named them, one cook DAY
                   each, round and round from the plan's first cook day.
    whoever_free   nobody. The household said it isn't fixed; saying a
                   name would be inventing one.

An unanswered question is `whoever_free` in every way that matters here:
nothing is claimed. That is what makes this safe to ship — a household
that has never answered renders exactly what it rendered before.

WHAT CARRIES AN OWNER, and what deliberately does not:

- A COOK does. That is what `cooking_role` is an answer about.
- A FRIDGE MOVE or a PREP TASK does, when it says which meal it is for
  (`prep_tasks.meal_plan_entry_id`) and that meal is a cook: it takes that
  cook's owner, on the cook's night rather than its own. This is reading a
  link that is already there, not a guess — the app files a thaw under the
  meal it feeds everywhere else (defrost._describe writes "for Thursday's
  skewers"; cookFocusPrepTasks shows it on that meal's own screen), and
  under `turns` "you're cooking Thursday, so the chicken is yours to move"
  is the exact thing the household would otherwise have to ask about. A
  task with no entry behind it gets nobody: matching by dish NAME would be
  a guess, and this module doesn't make any.
- A REHEAT does NOT. Nothing is cooked on a reheat night — the whole app
  is careful about that (moves.py never features one, cooker never scales
  one, time_caps exempts one), so "Emily's cooking" over a plate being
  warmed is a thing that isn't true, and inventing a second verb for it
  would be inventing a fact nobody stated.
- A SHOP does NOT, and it is worth saying why plainly rather than leaving
  it looking like an oversight: THERE IS NO "WHO SHOPS" ANSWER ANYWHERE IN
  THIS APP. Checked when this was built — `household_rhythm` holds seven
  fact types (lunch_location, meals_together, cooking_role, dinner_window,
  planning_anchor, leftovers_stance, prep_days) and none of them asks;
  `meal_preferences` has no such column; nothing in `members`; and a grep
  of app/ and static/ for shopper/who_shops/shopping_role finds only the
  grocery list's own use of the word "shopper" for whoever happens to be
  holding the phone. Guessing that the one person who COOKS also shops
  would be exactly the kind of traditional assumption rhythm.py's own
  docstring exists to refuse. So the shop is empty until somebody is
  asked, and asking is a question for Emily, not for this module.

COST, measured rather than reasoned about. Resolving this is ONE read of
the rhythm — which moves_for_day was already making for `_dinner_clock`, so
it is threaded rather than repeated — plus, for the two answers that name
somebody (`turns` AND `one_person`), one read of the members table.
`whoever_free` and an unanswered household spend nothing beyond the rhythm
read they already paid for, and that is every household this ships to on
day one. Measured at sqlite3.connect over a whole Today payload on a busy
day of five moves: main 71; this branch 71 unanswered, 71 whoever_free, 72
one_person, 72 turns.

It is resolved ONCE per moves_for_day call and handed to the stamping pass,
never per move: a per-move read would be a connection per row, which is the
mistake CLAUDE.md's clock entries record being made twice already.
"""
from __future__ import annotations

import logging

from ..db import get_conn
from ._shared import household_id
from . import rhythm as _rhythm

logger = logging.getLogger(__name__)


def _adults() -> list[dict]:
    """
    The adults on record, in the order setup named them.

    `ORDER BY id` IS that order — members are inserted as the household
    step of onboarding adds them — and it is the same rule
    household.first_run_hints and db._backfill_member_colors already use
    for "which adult", so the turns rotation and the avatar colours agree
    about who is first. LOWER() because onboarding writes "Adult" and older
    rows say "adult" (the 2026-08-31 entry: both of those readers matched
    'adult' exactly and found nobody).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, name FROM members
            WHERE household_id = ? AND LOWER(age_group) = 'adult' AND TRIM(name) != ''
            ORDER BY id
            """,
            (household_id(),),
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "name": (r["name"] or "").strip()} for r in rows]


def _is_a_cook(meal: dict) -> bool:
    """
    Exactly what becomes a cook move in moves._cook_and_reheat_moves: a
    dated entry that is not a reheat. Kept as one predicate so the
    rotation can never count a night the screen doesn't draw a cook on.
    """
    return bool(meal.get("date")) and not meal.get("is_leftovers")


class MoveOwners:
    """
    The day's answer to "whose is it", resolved once and asked many times.

    Built by `resolve` below; `for_meal` and `for_task` are the only two
    questions. Everything it needs is read in the constructor, so asking
    it costs nothing.
    """

    def __init__(self, role: dict | None, adults: list[dict], view: dict | None):
        self.value = (role or {}).get("value") or ""
        self.who = ((role or {}).get("who") or "").strip()
        self.adults = adults
        # entry_id -> the date its cook sits on, so a prep task can find
        # the night it is for. Both keys, because a component plan's merged
        # card carries `entry_ids` and every other carries `entry_id` —
        # the same reading cooker.start_cooking makes of one.
        self._cook_date_by_entry: dict[int, str] = {}
        cook_days: set[str] = set()
        floor = ((view or {}).get("period_start_date") or "") if view else ""
        for meal in (view or {}).get("meals") or []:
            if not _is_a_cook(meal):
                continue
            day = meal["date"]
            for entry_id in (meal.get("entry_ids") or [meal.get("entry_id")]):
                if entry_id is not None:
                    self._cook_date_by_entry[entry_id] = day
            # A cook day from BEFORE the plan's period is a leftover of an
            # earlier week (get_cooker_view folds loose meals in beside the
            # plan's own); counting it would move the rotation's anchor
            # without the household having changed anything.
            if not floor or day >= floor:
                cook_days.add(day)
        self._cook_days = sorted(cook_days)

    # ---------- the three answers ----------

    def _turns_owner(self, day: str) -> dict | None:
        """
        THE JUDGEMENT CALL, and it is Emily's to overrule in one line (the
        `% len` here, and `_adults`' ORDER BY above).

        "We take turns" records WHO but no ORDER and no ANCHOR, so both are
        picked here:

          ORDER — the adults as setup named them (members.id ascending).
          ANCHOR — the plan's first cook DAY is the first adult's; the
                   second cook day is the second adult's; round and round.

        Cook DAYS, not cook meals: Emily's own words are "alternates cook
        nights between the adults", and a 21-slot week alternating by MEAL
        would hand one adult breakfast and the other lunch on the same day,
        which is not what anybody means by taking turns. Every cook on a
        day therefore belongs to that day's cook.

        It is deterministic from the plan alone — a tick, a start, a swap
        of dish, a re-read all leave it exactly where it was, which is the
        one thing this must not get wrong. TWO honest limits:

        - Adding or dropping a cook DAY mid-week re-indexes every day after
          it. That is a change to the week rather than a wobble between two
          reads of the same one, and the alternative (anchor on the date's
          distance from period_start) is worse: a household cooking Monday,
          Wednesday and Friday with two adults would give every night to
          the same person, since 0, 2 and 4 are all even.
        - With NO plan at all the anchor is the earliest cook day the view
          holds, and that view is a sliding window of loose meals ahead —
          so it can move as days pass. Such a household has essentially
          nothing on Today anyway; a plan is what makes this stable.

        One adult is a rotation of one — every cook is theirs, which is the
        truthful answer rather than a special case. Three or more rotate in
        the same order, one cook day each.
        """
        if not self.adults:
            return None
        try:
            i = self._cook_days.index(day)
        except ValueError:
            # A cook on a day the rotation never counted (before the plan's
            # period). Nobody, rather than a name picked off the end.
            return None
        return self.adults[i % len(self.adults)]

    def for_meal(self, meal: dict) -> tuple[int | None, str | None]:
        """(member id, name) for a cook, or (None, None). A reheat is never
        a cook — see the module docstring."""
        if not _is_a_cook(meal):
            return (None, None)
        if self.value == "one_person":
            if not self.who:
                return (None, None)
            # The answer is free text (rhythm.set_cooking_role takes a
            # name, not a member), so it may or may not be somebody on
            # record. The name is what the screen says either way; the id
            # rides along only when it is genuinely known, because slice 3
            # will credit a tick with it and a guessed id would credit the
            # wrong person.
            match = next(
                (a for a in self.adults if a["name"].casefold() == self.who.casefold()),
                None,
            )
            return ((match or {}).get("id"), self.who)
        if self.value == "turns":
            adult = self._turns_owner(meal.get("date") or "")
            return ((adult or {}).get("id"), (adult or {}).get("name"))
        return (None, None)

    def for_task(self, task: dict) -> tuple[int | None, str | None]:
        """
        (member id, name) for a fridge move or a prep task: whoever cooks
        the meal it is for. Nobody when it names no meal, or names one that
        is not a cook.
        """
        entry_id = task.get("meal_plan_entry_id")
        if entry_id is None:
            return (None, None)
        day = self._cook_date_by_entry.get(entry_id)
        if not day:
            return (None, None)
        return self.for_meal({"date": day, "is_leftovers": False})


def resolve(view: dict | None = None, rhythm: dict | None = None) -> MoveOwners:
    """
    One resolver for one day's moves. `rhythm` lets the caller hand in the
    read it has already made (moves_for_day makes one for the dinner
    clock); `view` is the cooker view the moves are built from, which is
    where the rotation's cook days come from.

    Anything raising is "nobody owns anything" rather than a screen that
    doesn't render: a name is a nicety and the moves are the point.
    """
    try:
        rhythm = rhythm if rhythm is not None else _rhythm.get_household_rhythm()
        role = rhythm.get("cooking_role") or {}
        # Both answers that name somebody need the adults on record, for
        # different reasons: `turns` rotates through them, and `one_person`
        # matches its free-text `who` against them so the move can carry a
        # member id (see for_meal). `whoever_free` and an unanswered
        # household claim nobody, so they spend no query at all beyond the
        # rhythm read the caller already made.
        adults = _adults() if role.get("value") in ("turns", "one_person") else []
        return MoveOwners(role, adults, view)
    except Exception:
        logger.exception("Couldn't work out whose moves these are; leaving them unowned")
        return MoveOwners(None, [], None)


def one_person_cook_name(rhythm: dict | None = None) -> str | None:
    """
    The one person who cooks, by name, or None — cooker._cook_name's whole
    body, moved here so there is one reader of `cooking_role` rather than
    two that can drift. Deliberately still `one_person` only: it is a
    HOUSEHOLD-level answer on the cooker view, and under `turns` there
    isn't one (the name is per night, and rides on the move).
    """
    try:
        rhythm = rhythm if rhythm is not None else _rhythm.get_household_rhythm()
        role = rhythm.get("cooking_role") or {}
    except Exception:
        return None
    if role.get("value") != "one_person":
        return None
    return (role.get("who") or "").strip() or None
