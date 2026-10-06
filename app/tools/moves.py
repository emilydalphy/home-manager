"""
Today's moves — one timeline of "what's next for us?".

Emily's approved Today design (2026-09-08) replaced a screen of separate
cards (a tall dinner hero, a prep tile, a defrost tile, a grocery tile, a
notifications feed) with two blocks: ONE compact "Next up" card and a plain
list of everything else today. That only works if all of those things are
the SAME kind of thing, ranked against each other — so this module turns
them into one shape, a *move*, and ranks them.

A move is a thing the household has to actually do today, with a window in
which doing it makes sense:

    {id, kind, title, detail, reason, date, slot, window_start, window_end,
     weight, action: {label, target}, done, tickable, overdue, entry_id,
     task_id, duration_min, time_label, meta, chips, owner, owner_name}

`owner` (a member id) and `owner_name` say whose move it is, and are None on
most of them — a cook carries one when the household has said who cooks, the
prep behind that cook carries the same one, and nothing else carries any.
See move_owner.py, which owns that answer, and _stamp_owners below, which is
the one place it reaches a move.

`meta` is the one clock-free line under the title on Today (Emily,
2026-09-17, "Today: Shop / Cook, Morning · Afternoon · Evening"): what the
move is FOR or how long it takes — "for Thursday's skewers", "35 min",
"made ahead Sunday · reheat" — never "6:30". The clock belongs to the
Morning / Afternoon / Evening tag beside the row (shell.js moveTimeOfDay,
off window_start / window_end), so it must not be said twice. `detail`
keeps the clock: the morning text (digest.py) and Cook still read it.
A shop move also carries `stops`, one per store with something to buy
(see _shop_stops) — Today draws one row per stop.

`tickable` is true only when ticking the move actually does something —
set_move_done dispatches cook/reheat to check_off_meal and fridge/prep to
check_off_prep_step, but a shop move has nothing behind its tick to flip
(see set_move_done's own `kind == "shop"` branch), so it renders with no
tick at all rather than one that fills in and silently snaps back.

`overdue` is true for an undone fridge/prep move whose window has closed
for the day, and for a shop move once its cook's start time has passed —
see _prep_moves, _shop_move and featured_move_id below.

`timed` is false for a move with no deadline of its own, which today means
one thing: a shopping list nothing being cooked is waiting on. It is stated
on the timeline and never featured — see _standing_list_move. Absent means
true, so every other move here says nothing about it.

Nothing here is new state. Every move is derived from something that
already exists — a cooker-view card, a prep_tasks row, the grocery list —
and every tick dispatches back to the tool that owns that state
(check_off_meal / check_off_prep_step). There is deliberately no
`today_moves` table: a second store for "is this done?" is a second answer
to a question that already has one, and the two would drift the first time
someone checked a meal off in Cook mode instead of on Today.

Ranking (the whole point of the screen):

    Next up = among the moves that are not done, are not a reheat, and
    whose window is open now or opens within the next four hours — the
    highest weight first, then the earliest window_start.

Reheats are excluded from being featured on Emily's instruction: made-ahead
food "is a line, never the card". If nothing qualifies there is no featured
move at all and the screen shows tomorrow's first move instead — an honest
"nothing left today" rather than a card promoting whatever is nearest.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

# Every read below goes through another tool module, so this never touches
# the DB directly and never needs household_id() itself — the scoping comes
# from get_cooker_view / list_grocery_list / get_household_rhythm, each of
# which is already request-scoped (see _shared.household_id).
from . import cooker as _cooker
from . import day_meals as _day_meals
from . import defrost as _defrost
from . import grocery as _grocery
from . import move_owner as _move_owner
from . import quantities as _quantities
from . import rhythm as _rhythm
from . import weekly_plan as _weekly_plan

logger = logging.getLogger(__name__)


# How much a kind of move matters when two of them are open at once. Three
# steps, not a score: the screen only ever has to answer "which one is the
# card", and a finer scale would be a precision nobody set.
WEIGHT_HIGH = 3
WEIGHT_MEDIUM = 2
WEIGHT_LOW = 1

# The hours a household's meals usually land at, when nothing better is
# known. Breakfast and lunch have no rhythm question behind them yet (the
# six locked ones are in rhythm.py and none of them ask), so these are
# stated as defaults here rather than pretending to be household facts.
# Dinner DOES have one — dinner_window — and is read from it below.
DEFAULT_SLOT_HOURS = {
    "breakfast": time(8, 0),
    "lunch": time(12, 30),
    "dinner": time(18, 30),
    "snack": time(15, 30),
}

# How long after its usual hour a meal is still the thing to be doing.
SLOT_WINDOW_HOURS = 2

# How far ahead a move can be and still be offered as "next up". Four hours
# is the difference between "start the slow cooker now" and a card that
# tells you at eight in the morning to cook dinner.
URGENT_HOURS = 1  # a deadline this close takes the card whatever its weight (Emily, 2026-09-08)
LOOKAHEAD_HOURS = 4

# A cook needing shopping this far out is close enough that the shop is
# today's problem.
SHOP_HORIZON_HOURS = 36

REHEAT_ACTION_LABEL = "Mark eaten"  # keep in step with shell.js's own constant


# ---------- small time helpers ----------

def _as_date(day: str | date | None) -> date:
    """
    Parse a caller's day. `None` falls back to the SERVER's date — every
    entry point below resolves the household's own today before calling
    this, so nothing in this module reaches that branch; it stays as the
    last resort for a caller that hands in nothing at all.
    """
    if day is None:
        return date.today()
    if isinstance(day, date):
        return day
    return date.fromisoformat(day)


def _household_now() -> datetime:
    """
    Now on the household's own clock, as the naive local datetime every
    window and slot time in this module is already written in.

    The deployed container runs in UTC and households default to
    America/Toronto, so from 8pm Toronto the server's date is already
    tomorrow: /api/today/moves answered about TOMORROW while
    /api/today/tonight — which has read the household's clock since it
    shipped — still said today. Two cards on one screen disagreeing about
    what day it is, every evening, at dinner time; and Now would offer a
    cook that /api/cooker/start then refused as "that's Monday's".
    digest.build_morning_text has always passed `day=`/`now=` off the
    household's clock, so the screen was the half that was wrong.

    cooker.household_now is the one reader of households.timezone for this
    (imported as a module, per the package's own convention, because these
    domains are circular). It opens a connection, so this is called ONCE
    per request at the entry points below and threaded down, never inside
    a loop and never inside an open write transaction. A clock that can't
    be read must never stop the screen rendering, so anything raising here
    falls back to the server's own now — worse than before by nothing, and
    the same stance household_now itself takes towards an unreadable zone.
    """
    try:
        return _cooker.household_now()
    except Exception:
        logger.exception("Couldn't read the household's clock; falling back to the server's")
        return datetime.now()


def _clock(t: time) -> str:
    """"6:30", "8:00", "noon" — the time the way a person says it aloud."""
    if t.hour == 12 and t.minute == 0:
        return "noon"
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d}"


def _household_rhythm() -> dict:
    """
    The rhythm answers, once. `get_household_rhythm` opens a connection, and
    a day's moves need it twice over — the dinner clock and (2026-09-30) who
    owns each move — so moves_for_day reads it here and threads it into both
    rather than paying for it twice. Unreadable is an empty answer, never an
    exception: every reader below has a default, and a screen that renders
    nothing because a preference could not be read is the worse failure.
    """
    try:
        return _rhythm.get_household_rhythm()
    except Exception:
        logger.exception("Couldn't read the household's rhythm; using the defaults")
        return {}


def _dinner_clock(rhythm: dict | None = None) -> time:
    """
    When dinner actually lands for this household, from the dinner_window
    rhythm fact. defrost.py already owns that mapping (it schedules
    backwards from the same clock) — read it rather than writing a second
    copy that can drift. 'all_over' and an unanswered question both mean
    there is no household clock, so the default stands.

    `rhythm` is the caller's own read of the answers (see _household_rhythm);
    omitted, it makes one — big_meal, weekly_plan, today_meals and cooker all
    call this with no arguments and are unchanged by that.
    """
    rhythm = rhythm if rhythm is not None else _household_rhythm()
    window = rhythm.get("dinner_window")
    return _defrost._DINNER_CLOCK_BY_WINDOW.get(window or "") or DEFAULT_SLOT_HOURS["dinner"]


def _slot_time(slot: str, dinner_clock: time) -> time:
    if slot == "dinner":
        return dinner_clock
    return DEFAULT_SLOT_HOURS.get(slot or "", DEFAULT_SLOT_HOURS["dinner"])


def _slot_dt(day: date, slot: str, dinner_clock: time) -> datetime:
    return datetime.combine(day, _slot_time(slot, dinner_clock))


def _slot_time_label(slot: str, at: datetime) -> str:
    """"6:30 tonight", "8:00 this morning" — never "18:30"."""
    clock = _clock(at.time())
    if slot == "dinner":
        return f"{clock} tonight"
    if slot == "breakfast":
        return f"{clock} this morning"
    return f"{clock} today"


# "Shop for tonight" is only true when the cook the list is holding up IS
# tonight's. The deadline has been the soonest waiting cook's start time
# since 2026-09-16, and that is very often breakfast or lunch — most of all
# in the morning text, which goes out at 07:00, when today's dinner is the
# LAST cook still ahead rather than the first. Measured before this: a
# household with one breakfast and one dinner, nothing shopped, was texted
# "Shop for tonight — 4 items, by 7:55" at seven in the morning. The
# deadline was honest; the word was not, and that function's own docstring
# says an invented deadline at seven in the morning is how a morning
# check-in stops being believed.
# One grammar, because "Shop for tonight" is the line already approved and
# three siblings in its shape read as one family. "Shop BEFORE breakfast"
# was the first wording and was dropped on review: `for` says what the trip
# is FOR and leaves the clock to the detail line, where the deadline
# already lives, while `before` issues an instruction the app sometimes
# cannot stand behind — a long breakfast bake puts the deadline at 5:00 in
# the morning, and "be at a shop before 5:00" is not something to say to a
# person. §8: don't encode the same thing twice, and describe only what is
# true. It is also five characters shorter, which matters: the morning text
# SKIPS a line that does not fit its budget, so a longer title can silently
# cost a household the whole "Lunch: ..." line.
_SHOP_TITLE_BY_SLOT = {
    "dinner": "Shop for tonight",
    "breakfast": "Shop for breakfast",
    "lunch": "Shop for lunch",
}


def _shop_title(slot: str) -> str:
    """
    The shop, named after the meal it is actually for.

    A snack answers "Shop for today". Not a rare fallback, despite how it
    reads: measured, a snack is the soonest waiting cook for a whole band
    of the afternoon and IS the featured card at every hour of it, so with
    `snacks_per_day` defaulting to 2 this is the headline most afternoons
    of an unshopped day. It is the weakest of the four — the only one that
    does not say what the shop is for — and "Shop before the snack" is
    worse. Emily's to better.

    The unknown-slot fallback is the same string on purpose, so this
    function's answer never depends on the call site's own `or "dinner"`
    — the two used to disagree, and a later tidy-up dropping that `or`
    would have flipped the title to "today" while `_slot_time`'s own
    unknown-slot fallback still computed a DINNER-hour deadline.
    """
    return _SHOP_TITLE_BY_SLOT.get((slot or "").strip().lower(), "Shop for today")


def _weekday(day: str) -> str:
    return date.fromisoformat(day).strftime("%A")


# ---------- the sources ----------

def _cook_and_reheat_moves(view: dict, day: date, dinner_clock: time) -> list[dict]:
    day_str = day.isoformat()
    moves: list[dict] = []
    for meal in view.get("meals") or []:
        if meal.get("date") != day_str:
            continue
        slot = meal.get("slot") or "dinner"
        at = _slot_dt(day, slot, dinner_clock)
        done = meal.get("cooked_status") == "done"
        if meal.get("is_leftovers"):
            # "Made ahead — Sunday's Egg White Bites" vs "Leftovers —
            # Sunday's Bulgogi": the same fact, one honest word apart (see
            # leftovers.made_ahead_headline). The list wants the dish as
            # the title and the provenance as the small grey line.
            # EXTRACTED 2026-10-05 (Today's own meals row now says the
            # same thing on the same screen — day_meals.provenance_note is
            # the one implementation). A portion out of the freezer has no
            # night on this week to point at — a night off froze it,
            # possibly weeks and two plans ago (cooker._apply_leftover_
            # chains' own pass; the chain pass always sets leftovers_from,
            # so a dateless reheat is exactly this one) — so it says where
            # it came from rather than leaving "leftovers from" dangling.
            headline = meal.get("leftovers_headline") or ""
            provenance = _day_meals.provenance_note(meal)
            # A made-ahead portion eaten cold is not reheated (Emily,
            # 2026-09-25), so the line drops the word: "made ahead
            # Wednesday · 3:30". cooker._apply_leftover_chains decides it
            # (leftovers.served_cold); the kind stays "reheat" because what
            # the move DOES — tick it once it's eaten — is the same.
            served_cold = bool(meal.get("served_cold"))
            how = "" if served_cold else " · reheat"
            moves.append({
                "id": f"reheat:{meal['entry_id']}",
                "kind": "reheat",
                "title": meal.get("meal") or headline or "Leftovers",
                "detail": f"{provenance}{how} · {_clock(at.time())}",
                "reason": meal.get("reheat_note") or "",
                "date": day_str,
                "slot": slot,
                "window_start": at.isoformat(),
                "window_end": (at + timedelta(hours=SLOT_WINDOW_HOURS)).isoformat(),
                "weight": WEIGHT_LOW,
                "action": {"label": REHEAT_ACTION_LABEL, "target": {"kind": "check_meal", "entryId": meal["entry_id"]}},
                "done": done,
                "tickable": True,
                "overdue": False,
                "entry_id": meal["entry_id"],
                "task_id": None,
                "duration_min": 0,
                "time_label": _slot_time_label(slot, at),
                "meta": f"{provenance}{how}",
                "served_cold": served_cold,
                "chips": [],
            })
            continue

        # A lunch whose batch was cooked on an earlier (prep) day is not a
        # cook today (Emily, 2026-09-30, option (a) on the "Today's timeline
        # still calls a prepped lunch a cook" card): it reads "Chili —
        # prepped Sunday", no start time, no "Cook this". It is still tickable
        # and the tick still logs the lunch (check_off_meal, exactly as a
        # cook's). The kind is "reheat" because that is the one existing move
        # kind that is "already made, tick it when it's eaten": Next up skips
        # it, and the morning text (digest.py) lists only cooks and dinner
        # reheats, so it stops saying "Start by noon" for it and says nothing.
        # The words are Cook's own (shell.js kitchenTodayLine: "Prepped " +
        # weekday), lower-cased the way every Today meta line is.
        prepped = meal.get("prepped_ahead") or {}
        if prepped.get("date") and prepped["date"] < day_str:
            # day_meals.provenance_note again — one wording, two surfaces.
            prepped_line = _day_meals.provenance_note(meal)
            moves.append({
                "id": f"reheat:{meal['entry_id']}",
                "kind": "reheat",
                "title": meal.get("meal") or slot.capitalize(),
                "detail": f"{prepped_line} · {_clock(at.time())}",
                "reason": "",
                "date": day_str,
                "slot": slot,
                "window_start": at.isoformat(),
                "window_end": (at + timedelta(hours=SLOT_WINDOW_HOURS)).isoformat(),
                "weight": WEIGHT_LOW,
                "action": {"label": REHEAT_ACTION_LABEL, "target": {"kind": "check_meal", "entryId": meal["entry_id"]}},
                "done": done,
                "tickable": True,
                "overdue": False,
                "entry_id": meal["entry_id"],
                "task_id": None,
                "duration_min": 0,
                "time_label": _slot_time_label(slot, at),
                "meta": prepped_line,
                "served_cold": False,
                "prepped_ahead": dict(prepped),
                "prepped": True,
                "chips": [],
            })
            continue

        # The one total every clock uses — the recipe's minutes, or a
        # longer side's (cooker.cook_total_minutes). Until 2026-09-13 this
        # added prep + cook alone while the Meal step's stops counted the
        # side, so Now and Plan could name two different starts for one
        # dinner.
        duration = _cooker.cook_total_minutes(meal) or 0
        planned_start = at - timedelta(minutes=duration)
        # The real start, once "Start cooking" has been tapped (Emily,
        # 2026-09-13: the clock "should auto connect to whatever time it
        # is for them and update the done time accordingly too"). The
        # window opens when the cook actually began, the table time moves
        # with it, and the chip says what happened ("Started 6:02") rather
        # than what should have ("Start by 5:45"). Nothing else about the
        # move changes: it is the same cook, the same tick, the same card.
        started = _cooker.cook_started_dt(meal.get("cook_started_at"))
        start = started or planned_start
        # With no minutes on the card there is nothing to move the table
        # time by: the plan's stands (the receipt and the hero say the same).
        table = (started + timedelta(minutes=duration)) if (started and duration) else at
        detail_bits = [slot]
        if duration:
            detail_bits.append(_quantities.format_duration(duration))
        detail_bits.append(_clock(table.time()))
        chips = []
        if duration:
            chips.append(_quantities.format_duration(duration))
        if started:
            chips.append(f"Started {_clock(started.time())}")
        elif duration:
            chips.append(f"Start by {_clock(start.time())}")
        moves.append({
            "id": f"cook:{meal['entry_id']}",
            "kind": "cook",
            "title": meal.get("meal") or slot.capitalize(),
            "detail": " · ".join(detail_bits),
            # The batch note only ("Cooking for 6 — covers tonight and
            # leftovers on Thursday"): a fact about the cook. The planner's
            # own `reasoning` used to be the fallback and was cut on
            # 2026-09-11 (copy cleanse) — it read as the planner explaining
            # itself, not as something a person would say across the table.
            "reason": meal.get("covers_note") or "",
            "date": day_str,
            "slot": slot,
            "window_start": start.isoformat(),
            # A cook begun late is still tonight's for two hours after it
            # actually lands, not after it was meant to.
            "window_end": (max(at, table) + timedelta(hours=SLOT_WINDOW_HOURS)).isoformat(),
            "weight": WEIGHT_HIGH,
            "action": {
                "label": "Cook this",
                # Exactly the focus payload Meals' own "Cook this" passes —
                # see shell.js activateTab('kitchen', …, {cookFocus}). Cook
                # mode is a step of the Kitchen tab as of 2026-09-08; it was
                # a state of Meals ({tab: "week", mealsView: "cook"}) until
                # then, and nothing but this dict decided which.
                "target": {
                    "tab": "kitchen",
                    "cookFocus": {
                        "entryId": meal["entry_id"],
                        "date": day_str,
                        "slot": slot,
                        "title": meal.get("meal") or "",
                    },
                },
            },
            "done": done,
            "tickable": True,
            "overdue": False,
            "entry_id": meal["entry_id"],
            "task_id": None,
            "duration_min": duration,
            "time_label": _slot_time_label(slot, table),
            # "35 min", or the slot when the recipe has no minutes on it.
            "meta": _quantities.format_duration(duration) if duration else slot,
            "chips": chips,
            # Both clocks, so a reader can say how far apart they are
            # ("Started 17 minutes late" on Cook's Tonight card) without
            # re-deriving the plan's start from the chips.
            "started_at": started.isoformat() if started else None,
            "planned_start": planned_start.isoformat(),
        })
    return moves


def _prep_moves(view: dict, day: date, now: datetime, dinner_clock: time) -> list[dict]:
    """
    Fridge moves (task_type='defrost') and the rest of the day's prep.

    Both are all-day moves with a deadline rather than a start time: taking
    something out of the freezer at nine in the morning and at four in the
    afternoon are both fine, and only the "by" time is real.

    Two different clocks are in play here, and conflating them was the bug:

    - `window_end`, the hard end of this move's day — 22:00, not tonight's
      dinner. A defrost row due TODAY is always for a LATER day's meal
      (defrost._move_date never schedules the cook's own day; it floors at
      one full day of buffer), so tying the window's *close* to tonight's
      dinner_clock meant an undone fridge move failed featured_move_id's
      `window_end >= now` test the moment dinner passed and dropped out of
      the candidate set entirely — the evening card could never say "take
      tomorrow's chicken out" because the move it needed had already
      (wrongly) expired mid-evening.
    - the household's ordinary evening hour (dinner_clock) still marks the
      point after which this move is running late — not because the fridge
      move is FOR tonight's dinner, but because "tonight" as a household
      concept has, by then, effectively started. Past it, the move is
      `overdue` and the copy switches from the forward-looking "by tonight"
      to the plainer "still to do"; before it, the window is still fully
      open. Either way it stays a candidate all the way to `window_end`
      (and even past it, per `overdue`) — see featured_move_id.
    """
    day_str = day.isoformat()
    window_end = datetime.combine(day, time(22, 0))
    evening = datetime.combine(day, dinner_clock)
    open_from = datetime.combine(day, time(0, 0))
    moves = []
    for task in view.get("prep_tasks") or []:
        if task.get("task_date") != day_str:
            continue
        is_fridge = task.get("task_type") == "defrost"
        # A holiday's shop row (big_meal.SHOP_MARK) is a shop, not a prep:
        # it reads as one on the strip and opens the list, and still ticks
        # like the prep_tasks row it is.
        is_shop = task.get("task_type") == "holiday" and task.get("related_meal") == "Shop"
        description = (task.get("description") or "").strip()
        # defrost descriptions are written "<the move> — <what it's for>."
        # (defrost._describe). Split them so the title stays a title and the
        # rest becomes the featured card's one italic line.
        head, _, tail = description.partition(" — ")
        title = (head or description).rstrip(".")
        reason = tail.rstrip(".")
        # 'skipped' is a resolution too — check_off_prep_step accepts it
        # and the Today tile used to offer it — so it counts as handled,
        # not as still waiting.
        done = task.get("status") in ("done", "skipped")
        overdue = (not done) and evening < now
        when = "still to do" if overdue else "by tonight"
        reason = reason or (task.get("related_meal") and f"for {task['related_meal']}") or ""
        # What it's for, first; "still to do" only once it's running late —
        # "by tonight" is the Evening tag's job on Today.
        kind_word = "fridge move" if is_fridge else ("shop" if is_shop else "prep")
        meta = " · ".join(b for b in (reason or kind_word, "still to do" if overdue else "") if b)
        moves.append({
            "id": ("fridge:" if is_fridge else "prep:") + str(task["id"]),
            "kind": "fridge" if is_fridge else ("shop" if is_shop else "prep"),
            "title": title or ("Fridge move" if is_fridge else "Prep"),
            "detail": f"{kind_word} · {when}",
            "reason": reason,
            "date": day_str,
            "slot": None,
            "window_start": open_from.isoformat(),
            "window_end": window_end.isoformat(),
            "weight": WEIGHT_MEDIUM,
            "action": {"label": "Done", "target": {"kind": "check_prep", "taskId": task["id"]}},
            "done": done,
            "tickable": True,
            "overdue": overdue,
            "entry_id": task.get("meal_plan_entry_id"),
            "task_id": task["id"],
            "duration_min": 0,
            "time_label": when,
            "meta": meta,
            "chips": [],
        })
    return moves


SHOP_ACTION_LABEL = "Go shopping"  # Today's dock when the shop is next up; opens the list
STOP_PREVIEW_ITEMS = 3  # how many of a stop's things the row names before "…"


def _shop_stops(needed: list[dict]) -> list[dict]:
    """
    The list, one entry per store with something to buy on it — what
    Today's Shop group draws a row from ("Costco · 6 things", then the
    first few things). Rows tagged to no shop are one stop of their own,
    named "" here and "Any store" on screen (the Shop tab's own word for
    that pile, groStoreLabel), last; a list nothing is tagged on is one
    stop, its whole self. Stores keep the order the list first names them
    in (the list is sorted by category then item, so that is stable).
    """
    by_store: dict[str, list[str]] = {}
    for row in needed:
        store = (row.get("store") or "").strip()
        if store == "Unassigned":
            store = ""
        by_store.setdefault(store, []).append((row.get("item") or "").strip())
    names = [n for n in by_store if n] + ([""] if "" in by_store else [])
    return [{"store": n, "count": len(by_store[n]), "items": by_store[n][:STOP_PREVIEW_ITEMS]}
            for n in names]


def _shop_meta(stops: list[dict]) -> str:
    """"orzo, salmon, black beans…" — the first few things across the list."""
    items = [i for stop in stops for i in stop["items"]][:STOP_PREVIEW_ITEMS]
    more = sum(stop["count"] for stop in stops) > len(items)
    return ", ".join(i for i in items if i) + ("…" if more else "")


def _standing_list_move(day: date, needed: list[dict]) -> list[dict]:
    """
    The list, with no deadline on it: things are waiting to be bought, and
    none of them is what today's cooking is waiting on.

    Said rather than hidden, because "is there a shop to do?" is a question
    a person standing in the kitchen actually has — but said as a fact, not
    as a job with a clock on it. No "by", no urgency, and `timed: False`
    keeps it out of "next up", so Now's one apricot never becomes "Open the
    list" for a list nothing today needs (Emily, Flow 0, 2026-09-15).

    WORDING ASSUMPTION (Emily's to overrule in one line, here): "3 things
    on the list" as the title, and "2 stops" beside it only when the list's
    rows actually name that many shops. How many stops a trip really has is
    the Shop tab's own answer (shell.js groStoresWithNeeded), which falls
    back to the household's most-used shop when nothing is tagged yet; this
    counts only what the rows themselves say, rather than being a second
    implementation of that rule.

    An earlier version of this docstring said it could be "quieter than the
    Shop tab but never louder". That is FALSE and was measured so: the
    pre-shop "maybe already home" filter lives in main.py's
    /api/grocery-list routes, not in list_grocery_list, so a stop whose only
    row is flagged is counted here and not there — Now can read "2 stops"
    over a Shop tab showing one. The item count has diverged the same way
    since before this function existed; the stop count is new. Left rather
    than papered over, because the honest fix is for one of the two to stop
    disagreeing about what is on the list, which is its own card.
    """
    count = len(needed)
    stops = len({(r.get("store") or "").strip() for r in needed} - {"", "Unassigned"})
    stop_rows = _shop_stops(needed)
    return [{
        "id": f"shop:{day.isoformat()}",
        "kind": "shop",
        "title": f"{count} thing{'' if count == 1 else 's'} on the list",
        "detail": f"{stops} stop{'' if stops == 1 else 's'}" if stops else "",
        "reason": "",
        "date": day.isoformat(),
        "slot": None,
        # Open all day and closing with it: the honest window for something
        # that can be done any time today and is late for nothing.
        "window_start": datetime.combine(day, time(0, 0)).isoformat(),
        "window_end": datetime.combine(day, time(23, 59)).isoformat(),
        "weight": WEIGHT_LOW,
        "action": {"label": SHOP_ACTION_LABEL, "target": {"tab": "grocery"}},
        "done": False,
        "tickable": False,
        # No deadline, so never the card — see featured_move_id.
        "timed": False,
        "overdue": False,
        "entry_id": None,
        "task_id": None,
        "duration_min": 0,
        "time_label": "",
        "meta": _shop_meta(stop_rows),
        "stops": stop_rows,
        "chips": [],
    }]


def _shop_move(view: dict, day: date, now: datetime, dinner_clock: time) -> list[dict]:
    """
    The list, said the one way that is true of it today.

    "Shop for tonight · by 6:05" is a deadline, so it is only ever said
    when there is a cook close enough to have one AND something on the list
    that cook is actually waiting on. Emily walked Flow 0 on 2026-09-15 and
    Now told her to shop by 6:05 for a dinner already thawed in the fridge,
    against a list of chicken, rice and dish soap for nothing that night:
    the rule used to be "anything on the list, any cook in the horizon",
    which is two true facts standing next to each other pretending to be
    one. An invented deadline at seven in the morning is how a morning
    check-in stops being believed.

    With a cook in the horizon but nothing on the list for it, the list is
    still worth naming and has no deadline at all — an untimed line, never
    the card (see the `timed` flag below). With nothing to cook against it
    is not today's business at all and there is no move, as before: that is
    also what keeps the empty moment empty for a household with a standing
    list and no plan.
    """
    if day != now.date():
        return []
    try:
        needed = _grocery.list_grocery_list(status="needed")
    except Exception:
        needed = []
    if not needed:
        return []

    # Which meals still have something on the list waiting to be bought for
    # them, off the per-meal ledger — never by matching ingredient names,
    # which would be a second answer to a question the ledger already
    # answers exactly. Unreadable is the quiet direction: no waiting meal
    # means no deadline is claimed, rather than one being guessed.
    try:
        waiting = _grocery.entry_ids_awaiting_a_shop()
    except Exception:
        logger.exception("Today's shop move could not read the grocery ledger")
        waiting = set()

    horizon = now + timedelta(hours=SHOP_HORIZON_HOURS)
    a_cook_at_all = False
    soonest = None
    soonest_meal = None
    for meal in view.get("meals") or []:
        if meal.get("is_leftovers") or meal.get("cooked_status") == "done":
            continue
        try:
            at = _slot_dt(date.fromisoformat(meal["date"]), meal.get("slot") or "dinner", dinner_clock)
        except (KeyError, ValueError):
            continue
        if not (now <= at <= horizon):
            continue
        a_cook_at_all = True
        # entry_ids for a component-based plan's merged card, entry_id for
        # every other — the same reading cooker.start_cooking makes of one.
        ids = [i for i in (meal.get("entry_ids") or [meal.get("entry_id")]) if i]
        if not any(i in waiting for i in ids):
            continue
        if soonest is None or at < soonest:
            soonest = at
            soonest_meal = meal
    if not a_cook_at_all:
        return []
    if soonest is None:
        return _standing_list_move(day, needed)

    # The deadline is when the bags need to be back, not when the plate
    # lands: the cook's own start time, the same arithmetic behind the
    # Meal step's "Start at" hero (cooker.planned_start_for — the slot
    # time minus the recipe's prep + cook minutes, shared here rather than
    # re-derived so the two screens can't name two different starts for
    # one dinner). Falls back to the slot time itself when the meal
    # carries no known duration: there's nothing to move the deadline
    # earlier by, so "by" still means dinner time, as before.
    deadline = _cooker.planned_start_for(soonest_meal) or soonest
    # A cook can need to have started already while dinner itself is still
    # ahead (a 110-minute roast for a 5:30 table needs the bags back by
    # 3:40) — so `now` past this new, earlier deadline is an everyday case,
    # not a corner one. Without `overdue`, featured_move_id's own
    # `window_end >= now` candidacy test would drop the move from "next
    # up" the instant the deadline passed, the exact bug already fixed for
    # the fridge move (see _prep_moves): the card would simply stop asking
    # for a shop that is now more urgent, not less.
    overdue = now > deadline
    is_today = soonest.date() == now.date()
    # Once the deadline has gone by, "by 3:40" reads as a time still ahead
    # of you when it's actually behind you — the same reason _prep_moves'
    # fridge copy swaps to "still to do" rather than naming a clock that
    # has already passed.
    if overdue:
        when = "still to do"
    elif is_today:
        when = f"by {_clock(deadline.time())}"
    else:
        when = "by tomorrow"

    count = len(needed)
    stop_rows = _shop_stops(needed)
    return [{
        "id": f"shop:{day.isoformat()}",
        "kind": "shop",
        "title": (
            _shop_title(soonest_meal.get("slot") or "dinner") if is_today
            else "Shop before tomorrow"
        ),
        "detail": f"{count} item{'' if count == 1 else 's'} · {when}",
        "reason": "",
        "date": day.isoformat(),
        "slot": None,
        # Start of day, not "now". The shop has been open all day, and this
        # is also what makes shopping outrank the cook it is for: they share
        # the top weight, and the tie-break is the earlier window.
        "window_start": datetime.combine(day, time(0, 0)).isoformat(),
        # The cook's start time, not the meal's — see `deadline` above.
        # Everything downstream that ranks or colours moves by window_end
        # (featured_move_id's urgency, the day strip) already treats it as
        # "whatever this move's real deadline is", so moving it earlier
        # here is enough; nothing else assumes it equals the slot time.
        "window_end": deadline.isoformat(),
        "weight": WEIGHT_HIGH,
        "action": {"label": SHOP_ACTION_LABEL, "target": {"tab": "grocery"}},
        # Derived, like every other tick: the move exists only while the
        # list has needed items on it, so it is never "done" — it stops
        # being a move instead.
        "done": False,
        # Nothing to tick: set_move_done's "shop" branch is a no-op (see its
        # own docstring) — there is no flag here for a tick to flip, so the
        # UI renders no tick at all rather than one that fills in and snaps
        # back. See the module docstring's `tickable` note.
        "tickable": False,
        # This one has a real deadline behind it — a cook waiting on the
        # list. See _standing_list_move for the half that doesn't.
        "timed": True,
        "overdue": overdue,
        "entry_id": None,
        "task_id": None,
        "duration_min": 0,
        "time_label": when,
        # The things, not the deadline: "still to do" rides on the tag's
        # side of the row only through `detail` (the morning text's line).
        "meta": _shop_meta(stop_rows),
        "stops": stop_rows,
        "chips": [],
    }]


# ---------- the shopping-day line ----------

# Today's fourth section is a LINE, not a task (the card: "a celadon line,
# not a task"), on every day but the household's own shopping day — "You
# shop on Saturday. 14 things on the list so far." On shopping day the
# existing Shop card takes its place, stops and all, because that is the day
# the list is a job rather than a fact.
#
# ASSUMPTION (Emily's to reverse in one line, _shop_day_line below): a
# TOP-UP shop day counts as a shopping day too. It is a day the household
# shops, and showing them a line telling them when they shop on a day they
# are shopping would be the screen arguing with itself.
#
# WORDING, also one line: "You shop on Saturday." names the day they gave,
# then the list's own size. "Nothing on the list yet" rather than "0 things"
# — a count of nothing is a number where a sentence belongs (§8), and the
# same call _standing_list_move's own title makes.

def _shop_day_line(shop_day: str, count: int) -> str:
    """The celadon line's words. '' with no shop day on record — the caller
    says the other thing then, because an unanswered question is a real
    state and "No shopping day set" is an offer, not a statement."""
    if not shop_day:
        return ""
    things = f"{count} thing{'' if count == 1 else 's'} on the list so far." if count else "Nothing on the list yet."
    return f"You shop on {shop_day.capitalize()}. {things}"


def _first_shop_is_now(day: date, shop_day: str) -> bool:
    """
    A first week that starts BEFORE the household's usual shop day shops
    right away (Emily's walkthrough, Tuesday 2026-10-06: the first plan began
    that night, the shop day was Saturday, and Today said "You shop on
    Saturday" while tonight's dinner needed those groceries). The usual shop
    day applies from the second week on.

    True from the first plan's first day up to (not including) the first
    usual shop day on or after it — a household whose first plan began ON
    its shop day was never in this case, and one whose plan began last month
    is long past it. The caller only asks while something is on the list:
    with nothing left to buy the line is the right thing to say.
    """
    try:
        start_iso = _weekly_plan.first_plan_start()
        wd = _rhythm.SHOP_DAY_WEEKDAYS.index(shop_day)
    except Exception:
        return False
    if not start_iso:
        return False
    start = date.fromisoformat(start_iso)
    first_usual = start + timedelta(days=(wd - start.weekday()) % 7)
    return start <= day < first_usual


def _shop_block(day: date, rhythm: dict) -> dict:
    """
    What Today's Shop section says, decided here rather than on the screen:
    which weekday the household shops, whether `day` is one of them, how
    many things are still to buy, and the line itself.

    The count is `list_grocery_list(status="needed")`'s own length — the
    same read _shop_move makes and the same number the Shop tab's needed
    view opens on. It is read here rather than taken off a shop MOVE
    because the line has to be true on a day with no move at all: a
    household with nothing to cook against its list gets no shop move
    (_shop_move's own rule) and still shops on Saturday.

    KNOWN, and inherited rather than introduced: that count can differ from
    the Shop tab's by the pre-shop "maybe already home" filter, which lives
    in main.py's grocery routes rather than in list_grocery_list. See
    _standing_list_move's docstring, which measured it; the honest fix is
    for one of the two to stop disagreeing about what is on the list, and
    it is its own card.
    """
    shop_day = (rhythm.get("shop_day") or "").strip().lower()
    top_up = (rhythm.get("top_up_shop_day") or "").strip().lower()
    try:
        count = len(_grocery.list_grocery_list(status="needed"))
    except Exception:
        logger.exception("Today's shop line could not read the grocery list")
        count = 0
    weekday = day.strftime("%A").lower()
    is_shop_day = bool(shop_day) and weekday in {shop_day, top_up} - {""}
    if shop_day and not is_shop_day and count:
        is_shop_day = _first_shop_is_now(day, shop_day)
    return {
        "shop_day": shop_day or None,
        "top_up_shop_day": top_up or None,
        "is_shop_day": is_shop_day,
        "count": count,
        "line": _shop_day_line(shop_day, count),
        # The household's own sentence for the setting, for the row that
        # opens it (rhythm.shop_days_summary) — '' when never answered.
        "summary": rhythm.get("shop_days_summary") or "",
    }


# TODO(prep sessions): a scheduled prep session is a high-weight move with a
# "Start prep" action, but nothing on main reports one yet — there is no
# /api/week/{ws}/prep-sessions route and get_cooker_view carries no session
# in its payload. Add the source here (not a new store) once that lands.


# ---------- whose move is it ----------

def _stamp_owners(moves: list[dict], owners) -> list[dict]:
    """
    Put `owner` (a member id) and `owner_name` on every move — both None
    wherever nothing is known, which is most households and every move kind
    but a cook and the prep behind one. See move_owner.py for which kinds
    carry a name and, more usefully, why the shop and the reheat do not.

    A PASS, deliberately, rather than five builders each learning about
    this: every field it reads (`kind`, `date`, `entry_id`) is already on
    the move, so the builders are untouched and a household with no answer
    renders byte-for-byte what it rendered before this existed. The keys
    are always present so a reader never has to know whether the payload
    predates them.

    A holiday's own shop row (big_meal.SHOP_MARK) reaches this as
    kind='shop' carrying the big meal's entry_id, and is deliberately left
    unowned with the generic shop: this app has no "who shops" answer to
    read (move_owner's docstring says where that was checked), and the one
    who cooks the roast is not necessarily the one who fetches it.
    """
    for move in moves:
        kind = move.get("kind")
        if kind == "cook":
            member_id, name = owners.for_meal({"date": move.get("date"), "is_leftovers": False})
        elif kind in ("fridge", "prep"):
            # _prep_moves copies prep_tasks.meal_plan_entry_id onto the
            # move as `entry_id`, so the link to the meal this is for is
            # already here and needs no second read of the task.
            member_id, name = owners.for_task({"meal_plan_entry_id": move.get("entry_id")})
        else:
            member_id, name = (None, None)
        move["owner"] = member_id
        move["owner_name"] = name
    return moves


# ---------- the timeline ----------

def moves_for_day(
    day: str | date | None = None,
    now: datetime | None = None,
    view: dict | None = None,
    owners: "_move_owner.MoveOwners | None" = None,
    rhythm: dict | None = None,
) -> list[dict]:
    """
    Every move for one day, earliest window first.

    `now` exists for tests and for the shop rule's 36-hour horizon; the UI
    always passes real time (i.e. omits it). Omitted, it is the
    HOUSEHOLD's now, not the server's — see _household_now. `view` lets a
    caller that already has the cooker view (today_moves, which needs it
    twice over) hand it in rather than paying for a second full read of
    the plan.

    `day` omitted is the household's today, taken from `now` so that a
    caller passing one clock can never be answered about another day's
    moves — which is exactly what "today" meant while this read the
    server's date.

    `owners` and `rhythm` are the same bargain as `view`: today_moves reads
    the household's answers ONCE and hands both down to both of its calls
    (today's and, when nothing is featured, tomorrow's), so a whole Today
    payload costs exactly the one rhythm read it cost before whose-move-is-it
    existed — and never one per move.
    """
    now = now or _household_now()
    target = _as_date(day) if day is not None else now.date()
    rhythm = rhythm if rhythm is not None else _household_rhythm()
    dinner_clock = _dinner_clock(rhythm)
    view = view if view is not None else _cooker.get_cooker_view()
    owners = owners if owners is not None else _move_owner.resolve(view, rhythm)

    prep = _prep_moves(view, target, now, dinner_clock)
    shop = _shop_move(view, target, now, dinner_clock)
    if any(m["kind"] == "shop" for m in prep):
        # A big meal's own shop is on this day (big_meal.spread_prep) —
        # it names the trip and what it's for, so the week's generic "Shop
        # before tomorrow" would be the same ask twice. Its count rides on
        # the named one instead.
        # Only off a TIMED one: the untimed list line's detail is a stop
        # count, not an item count (see _standing_list_move), so moving it
        # across would put "2 stops" where "3 items · by 6:05" belongs. The
        # named trip still stands on its own either way.
        generic = next((m for m in shop if m.get("timed", True)), None)
        for m in prep:
            if m["kind"] == "shop" and generic and generic["detail"]:
                m["detail"] = generic["detail"]
                m["time_label"] = generic["time_label"]
        shop = []
    moves = _cook_and_reheat_moves(view, target, dinner_clock) + prep + shop
    moves.sort(key=lambda m: (m["window_start"], -m["weight"], m["id"]))
    return _stamp_owners(moves, owners)


def featured_move_id(moves: list[dict], now: datetime | None = None) -> str | None:
    """
    Which move is the card. See the module docstring: open now or within
    four hours, highest weight, then earliest window. A reheat never
    features — Emily, 2026-09-08: made-ahead food is a line, never the card.

    An `overdue` move (an undone fridge/prep task whose window has already
    closed for the day — see _prep_moves) stays a candidate even though its
    own window_end is in the past: that flag exists precisely so a fridge
    move due today doesn't drop out of contention the moment the (wrong,
    dinner-shaped) deadline it used to carry passed.

    A move with `timed` false never features at all: "next up" is a
    question about time, and a move with no deadline has no claim on it.
    The one that carries the flag today is the untimed shopping list
    (_standing_list_move), which is also what keeps Today's one apricot
    off "Go shopping" for a list nothing today is waiting on.

    `now` omitted is the household's now: these windows are naive local
    times, so comparing them against a UTC server clock ranked the day
    from the wrong hour. today_moves always passes one, so the read costs
    nothing on the path the screen actually takes.
    """
    now = now or _household_now()
    horizon = (now + timedelta(hours=LOOKAHEAD_HOURS)).isoformat()
    now_iso = now.isoformat()
    candidates = [
        m for m in moves
        if not m["done"]
        and m["kind"] != "reheat"
        and m.get("timed", True)
        and m["window_start"] <= horizon
        and (m.get("overdue") or m["window_end"] >= now_iso)
    ]
    if not candidates:
        return None
    # Emily, 2026-09-08: importance wins, except that a deadline inside the
    # next hour wins over everything — the chicken that has to move to the
    # fridge by tonight beats a cook whose window only just opened.
    urgent_by = (now + timedelta(hours=URGENT_HOURS)).isoformat()
    def _rank(m):
        urgent = bool(m.get("overdue")) or m["window_end"] <= urgent_by
        return (0 if urgent else 1, -m["weight"], m["window_start"], m["id"])
    candidates.sort(key=_rank)
    return candidates[0]["id"]


def _week_state(view: dict, day: date) -> str:
    """
    'set' | 'draft' | 'ahead' | 'none' — the small badge under Today's date.

    'set' and 'draft' only when the plan's period actually covers `day`.
    get_cooker_view hands back a plan that hasn't STARTED yet as the
    current one on purpose (cook mode legitimately opens next week's
    draft when nothing covers today — see its docstring), and this used to
    read that plan's status straight through: on a Tuesday with an
    approved plan for a week in another year, Now wore "Week set" beside
    "Shall I put Sep 14–20 together?" (Loop Board, 2026-09-15). A plan
    that starts after `day` is 'ahead' — no chip, since the empty moment
    and the nudge already say the week needs planning; the value is kept
    distinct from 'none' so a reader can still tell "nothing planned" from
    "planned, just not this week". A period already behind `day` reads
    'none' (get_cooker_view blanks that plan before it reaches here; the
    check is kept whole rather than trusting that).
    """
    if not view.get("weekly_plan_id"):
        return "none"
    start = view.get("period_start_date")
    if start:
        first = date.fromisoformat(start)
        last = first + timedelta(days=max(1, view.get("day_count") or 1) - 1)
        if day < first:
            return "ahead"
        if day > last:
            return "none"
    return "set" if view.get("status") == "approved" else "draft"


def today_moves(day: str | date | None = None, now: datetime | None = None) -> dict:
    """
    The whole Today payload: the day's moves, which one is the card, the
    done/total counts (no longer shown on Now since 2026-09-24, kept for
    the API), the week-state badge, and — for the days with
    nothing left on them — tomorrow's first move.

    With neither argument this is the household's today and now — the
    clock is resolved once, here, and handed down to moves_for_day and
    featured_move_id, so one screen costs one read of the zone however
    many moves the day holds. /api/today/moves passes only whatever
    ?date= the caller sent (the shell sends none), which is why this
    function's default is the fix and app/main.py needed no change.
    """
    now = now or _household_now()
    target = _as_date(day) if day is not None else now.date()
    view = _cooker.get_cooker_view()
    # Who owns what is a property of the PLAN, not of the day being asked
    # about, so today's moves and tomorrow's share one answer — the same
    # bargain `view` already makes, and what keeps a Today payload at one
    # read of the rhythm and the members table however many moves it holds.
    rhythm = _household_rhythm()
    owners = _move_owner.resolve(view, rhythm)
    moves = moves_for_day(target, now=now, view=view, owners=owners, rhythm=rhythm)
    featured = featured_move_id(moves, now=now)
    # The day's meals, top of the screen (day_meals.for_day) — read off the
    # SAME cooker view the moves are, so Today's meals row and its Cook row
    # can never be built from two different reads of one week. Two reads of
    # their own: who is in the household, and which of today's slots deviate
    # from everyone's-home — both once for the whole payload, never per meal.
    people = _day_meals.household_people()
    day_meals = _day_meals.for_day(target, view, people=people, rhythm=rhythm)

    tomorrow = None
    if featured is None:
        ahead = moves_for_day(target + timedelta(days=1), now=now, view=view,
                              owners=owners, rhythm=rhythm)
        undone = [m for m in ahead if not m["done"]]
        tomorrow = undone[0] if undone else None

    return {
        "date": target.isoformat(),
        "moves": moves,
        "featured": featured,
        "done": sum(1 for m in moves if m["done"]),
        "total": len(moves),
        # The badge is about the day this payload is about — the household's
        # today unless a caller named one — never the plan's own status alone.
        "week_state": _week_state(view, target),
        # One row per planned meal, in eating order with snacks between
        # lunch and dinner (day_meals.SLOT_ORDER). [] on a day with nothing
        # planned, which is what Today reads as "Nothing planned today".
        "day_meals": day_meals,
        # Today's fourth section: the shopping-day line, or (on the
        # household's own shopping day) the fact that the Shop card belongs
        # there instead. See _shop_block.
        "shop": _shop_block(target, rhythm),
        "tomorrow": tomorrow,
        # The quiet label beside the date when today is a holiday — name
        # and answer, or None on an ordinary day (see holidays.py).
        "holiday": _today_holiday(target),
    }


def _today_holiday(target: date) -> dict | None:
    from . import holidays as _holidays

    try:
        h = _holidays.holiday_on(target.isoformat())
    except Exception:
        logger.exception("Today's holiday label could not be built")
        return None
    if not h:
        return None
    return {"name": h["name"], "answer": h["answer"]["answer"] if h["answer"] else None,
            "label": _holidays.holiday_day_label(h)}


def set_move_done(move_id: str, done: bool = True) -> dict:
    """
    Tick or untick one move, by dispatching to whichever tool owns the state
    behind it. Nothing is written here — see the module docstring for why
    there is no moves table.
    """
    kind, _, ref = (move_id or "").partition(":")
    status = "done" if done else "pending"
    if kind in ("cook", "reheat"):
        _cooker.check_off_meal(int(ref), status)
        return {"id": move_id, "done": done, "dispatched_to": "check_off_meal"}
    if kind in ("fridge", "prep"):
        _cooker.check_off_prep_step(int(ref), status)
        return {"id": move_id, "done": done, "dispatched_to": "check_off_prep_step"}
    if kind == "shop":
        # Shopping is done when the list says so — there is nothing here to
        # write, and inventing a flag would make Today disagree with Grocery.
        return {"id": move_id, "done": False, "dispatched_to": None}
    raise ValueError(f"Unknown move id: {move_id!r}")
