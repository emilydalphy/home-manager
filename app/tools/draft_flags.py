"""
When the draft has to bend, it says so — one line, with the fix already
worked out.

Emily's decided snag rules (2026-09-23, the "When your requests and your
week don't line up" page). Her own words for the shape of it: "Whenever
Pomona bends a rule or moves something, it adds one line to the top of the
draft. You don't get a pop-up, and you don't have to answer anything."

THE RULE THIS FILE BUILDS TODAY IS HER SCENARIO 3 — a dish the household
asked for BY NAME on a night that hasn't the time for it:

    Lasagna takes 60 minutes, and Wednesday is short on time.
       [ Prep it Tuesday night ]   [ Move it to Saturday ]

The KEEPING half already worked and is not this file's doing:
`cap_enforce` deliberately leaves a night alone when the household spoke
for it (`meal_variety.theirs`), because a cap is not a reason to overrule
a choice. What was missing was that nothing SAID so. plan_quality measured
the breach into the morning report — a file Emily reads and the household
never sees — and the draft showed the over-cap dinner without a word. So
the flag is the missing half, not a new rule.

WHERE THE FLAGS LIVE. `weekly_plans.draft_flags_json`, its own column and
deliberately NOT a key inside `requests_json`: `record_plan_requests`
rewrites that blob wholesale after generation, so a flag written during
`_finish_week_slots` would be silently clobbered by it.

WHAT A FLAG IS, and what it is not. It is a record of something the app
did (or declined to do) to one entry, plus the fixes it has already worked
out for the household — never a question, never blocking, never a pop-up.
`fixes` may be empty: a flag with nothing to offer is still worth saying.

THE TWO FIXES ARE EXISTING WRITES, not new ones:
  * "Prep it Tuesday night" → `prep_sessions.add_prep_cut`, the prep-cut
    row the Cook tab already draws and ticks. WHERE it draws it depends on
    the household, and the shorter sentence was wrong: `prep_sessions`
    only gathers a session on a declared `rhythm.prep_days` weekday (or a
    prepped-lunch batch date), and `cookFocusPrepTasks` deliberately
    excludes `prep_cut` rows because they "belong to their prep session".
    So on a household with prep days it lands in the session; on one
    without — which is the default — it lands in Cook's loose get-ready
    rows and on Today's own timeline on the day itself. It surfaces
    either way, which is what the fix needs; it is not always a session.
    It does not
    move the dish or touch the plan; it splits the work, which is what
    the household is being offered.
  * "Move it to Saturday" → `weekly_plan.swap_dinner_nights`, which
    TRADES the two nights' dinners in place (ids kept, so groceries, the
    cooked tick and the plate's sides ride along, and the shopping list
    is untouched). Because it is a trade and not a move, the night we
    offer must be one whose own dinner can take the short night — see
    `move_target`, which is the whole care in this file.

READ-TIME HONESTY. A flag is about one entry on one night. If that entry
has left the plan, or its dish has changed, or it is no longer on the date
the flag names, the flag is about nothing and `plan_flags` drops it —
the same read-time guard `cooker.get_prep_schedule` puts on a dangling
prep row, and for the same reason: `meal_plan_entries` rows are deleted
and re-dated by half a dozen paths, and a read-time rule is the one place
that covers every one of them, past and future.

DELIBERATELY NOT BUILT HERE, so nobody reports them as missing:
  * Scenario 1 — a request with no meal and no day goes at the meal the
    model put it at (dinner, or a lunch it is already at: the app keeps no
    record of which meal a dish suits) on a day with the time for it, and
    `NO_MEAL_NO_DAY` says where it went (2026-10-25,
    cap_enforce.place_meal_requests). Scenario 2 — a request with a meal
    and no day goes at that meal with everyone home and the time for it —
    is built (2026-10-25, cap_enforce.place_meal_requests) and has no line:
    none was decided. Scenario 5 — a requested dish that fits NO night goes on the
    night with the most time, and Pomona says so — is built
    (2026-10-25): cap_enforce.place_fits_nowhere places it, and
    `FITS_NOWHERE` below is the line, with no fixes. So is rule 8 — a
    dish asked for on a night nobody is home moves to the nearest night
    that fits its time (cap_enforce.place_off_nights_out), and `DAY_OUT`
    is the line. And rule 9 — a dish they asked for that clashes with an
    allergy or a hard no is replaced by the allergen gate, and
    `ALLERGY_REQUEST` is the line saying why (2026-10-25).
  * A chain end — a cook that feeds a later night, or the reheat itself —
    is never flagged. cap_enforce's own docstring names the over-cap
    chain source as its biggest open hole and Emily's call; widening into
    it from here would be deciding it unmeasured.
"""
from __future__ import annotations

import datetime
import json
import logging
import re

from ..db import get_conn
from ._shared import household_id

logger = logging.getLogger("home_manager")

# The one kind this file writes today. A kind is what the flag is ABOUT,
# so the screen can group or style them later without parsing the words.
OVER_CAP_REQUEST = "over_cap_request"

# A meal their own words named ("tonight", "lunch today") whose time had
# already gone by when the week was drafted: planned anyway, and said
# (review of the "today means from now" card, 2026-09-27). No fixes.
LATE_REQUEST = "late_request"

# A dish left on a third lunch-or-dinner in a row because nothing else could
# go there — the household's dish count was met, nothing was in reach, or
# nothing fit the meal's time (dinner_gaps.break_long_runs). Said, never
# silent (review, 2026-09-27). No fixes: the row's own Swap is the way out.
RUN_LEFT = "run_left"

# Emily's rule 5 (2026-09-23): a dish they asked for, naming no day, that
# fits no night of the week — cap_enforce.place_fits_nowhere has put it on
# the longest night, and this is the line that says so. No fixes: none
# were decided, and the row's own Swap is the way out.
FITS_NOWHERE = "fits_nowhere"

# Emily's rule 8 (2026-09-23): a dish they asked for on a night nobody is
# home — cap_enforce.place_off_nights_out has moved it to the nearest night
# that fits its time, and this is the line that says so. No fixes: none
# were decided, and the row's own Move or Swap is the way out.
DAY_OUT = "day_out"

# Emily's rule 9 (2026-09-23): a dish they asked for that clashes with an
# allergy or a hard no — the allergen gate replaced it with a safe dish
# (allergen_gate.repick_held), and this is the one line that says why their
# request isn't there. No fixes: the allergy wins, and there is nothing to
# choose.
ALLERGY_REQUEST = "allergy_request"

# Emily's rule 1 (Decision A, 2026-09-23): a dish they asked for naming no
# meal and no day — "Pomona says where it went", whether or not
# cap_enforce.place_meal_requests had to move it for time. No fixes: the
# row's own Move or Swap is the way out.
NO_MEAL_NO_DAY = "no_meal_no_day"
# ...and, past NO_MEAL_NO_DAY_LINES of them, the one line that stands for
# the rest (review, 2026-10-25: one sentence naming six dishes gave six
# lines). Hangs on the first dish it stands for, and lives while that dish
# is still on the plan uncooked — it names no day, so a move can't make it
# false.
NO_MEAL_NO_DAY_MORE = "no_meal_no_day_more"
# Up to three are each said; four or more, the first two and one line for
# the rest — a summary that replaced only one line would save nothing.
NO_MEAL_NO_DAY_LINES = 3

# What a fix does. Each is an existing write; see the module docstring.
FIX_PREP_AHEAD = "prep_ahead"
FIX_MOVE = "move"

# Emily, 2026-09-28, reading the Move-only card: "there should be a 'Keep
# on Monday' option as well next to the move to Saturday in case I want to
# keep it there." Unlike the other two, this fix WRITES NOTHING to the
# plan — the dish is already exactly where the flag says it is. It only
# takes the flag itself off, through the same `dismiss` the other two
# fixes already call once their own write lands. It is offered wherever a
# move is (fixes_for), never on its own: keeping is the default state a
# flag that offers nothing already has, so a "keep" button only earns its
# place beside a button that would change something.
FIX_KEEP = "keep"

_WEEKDAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _weekday(meal_date: str) -> str:
    try:
        return _WEEKDAY_LONG[datetime.date.fromisoformat(meal_date).weekday()]
    except (TypeError, ValueError, IndexError):
        return meal_date


def over_cap_text(dish: str, minutes: int, meal_date: str) -> str:
    """
    Emily's own sentence, word for word off the decided page: "Lasagna
    takes 60 minutes, and Wednesday is short on time."
    """
    return f"{dish} takes {int(minutes)} minutes, and {_weekday(meal_date)} is short on time."


def fits_nowhere_text(dish: str, minutes: int, meal_date: str) -> str:
    """
    Emily's own sentence, word for word off the decided page: "The braise
    takes 90 minutes, and your longest night is Sunday, so it's there."
    """
    return f"{dish} takes {int(minutes)} minutes, and your longest night is {_weekday(meal_date)}, so it's there."


def day_out_text(dish: str, from_date: str, to_date: str, away: bool = False) -> str:
    """
    Emily's own sentence off the decided page: "You're out Friday, so
    pizza moved to Saturday." A night nobody is home because of a trip
    (an away need, not the Out tag) says "away" — the word the day sheet
    and set_away_stretch use for it.
    """
    return f"You're {'away' if away else 'out'} {_weekday(from_date)}, so {dish} moved to {_weekday(to_date)}."


def no_meal_no_day_text(dish: str, meal_date: str, slot: str, minutes: int | None) -> str:
    """
    Emily's own line, off Decision A: "Korean chicken pancake is Saturday
    dinner. It takes about 30 minutes." With no minutes on record the second
    sentence would be a guess, so it is left off.
    """
    where = f"{dish} is {_weekday(meal_date)} {slot}."
    return f"{where} It takes about {int(minutes)} minutes." if minutes else where


def no_meal_no_day_more_text(count: int) -> str:
    """"Your other four asks are on the plan too, each on a day with the
    time for it." Pomona's sentence, not Emily's — hers to approve."""
    from .draft_opener import number_word  # lazy: keeps this module's imports light
    if count == 1:
        return "Your other ask is on the plan too, on a day with the time for it."
    return f"Your other {number_word(count)} asks are on the plan too, each on a day with the time for it."


def allergy_request_text(asked: str, food: str, who: list[str], dish: str,
                         meal_date: str, slot: str = "dinner") -> str:
    """
    "You asked for peanut noodles, but Sam can't have peanuts, so Tuesday is
    Sesame Noodles instead." Their own words for the dish, the person and
    the food the allergen gate matched (`who` empty: a hard no for the whole
    house — "…but this house doesn't eat pork, …", which needs no is/are),
    and what went there instead.
    No sentence was decided for rule 9; this one is Pomona's, for Emily to
    approve.
    """
    cannot = f"{' and '.join(who)} can’t have {food}" if who else f"this house doesn’t eat {food}"
    when = _weekday(meal_date) if slot == "dinner" else f"{_weekday(meal_date)} {slot}"
    return f"You asked for {asked}, but {cannot}, so {when} is {dish} instead."


def late_text(dish: str, meal_date: str, slot: str, moved: bool = False) -> str:
    """"Japanese Curry stays on Sunday dinner, as you asked — its usual time
    had already gone by." One plain line, like every flag. When Pomona also
    had to MOVE the dish there, the move is said in the same line ("I moved
    Japanese Curry to Sunday dinner, as you asked — …") and nowhere else."""
    when = f"{_weekday(meal_date)} {slot}"
    if moved:
        return f"I moved {dish} to {when}, as you asked — its usual time had already gone by."
    return f"{dish} stays on {when}, as you asked — its usual time had already gone by."


def prep_fix(prep_date: str) -> dict:
    return {"action": FIX_PREP_AHEAD, "label": f"Prep it {_weekday(prep_date)} night", "date": prep_date}


def move_fix(to_date: str) -> dict:
    return {"action": FIX_MOVE, "label": f"Move it to {_weekday(to_date)}", "date": to_date}


def keep_fix(here_date: str) -> dict:
    """
    "Keep on Monday" — the day the dish is ALREADY on, not the day it
    could move to. `date` is only carried so the flag-fix route's
    generic "is this fix still live" check (it requires a truthy
    `date` on every fix) has something to read; the route does not use
    it to write anywhere, because keeping writes nothing.
    """
    return {"action": FIX_KEEP, "label": f"Keep on {_weekday(here_date)}", "date": here_date}


PREP_CUT_TEXT = "Get {dish} ready for {weekday}"


def prep_cut_description(dish: str, meal_date: str) -> str:
    """What the prep-cut row says on the Cook tab once the household taps it."""
    return PREP_CUT_TEXT.format(dish=dish, weekday=_weekday(meal_date))


# ---------- choosing the two nights ----------

def prep_target(meal_date: str, nights: list[dict]) -> str | None:
    """
    The night before, when there is one to prep on.

    Only the day immediately before — "prep it the night before" is the
    household's own phrase and means that night, not "some earlier night
    we picked".

    Whether that night is usable is read off the plan's own dinners rather
    than from a separately-computed list of days written off. Every way a
    night can be unavailable — out, away, a skipped day, a meal category
    the household asked none of — is written as the SAME thing, a
    `planned_empty` row (see agent._finish_week_slots), so "the night
    before has a dinner row that isn't planned_empty" is one test that
    covers all of them and cannot drift from them. No row at all means the
    day is outside the period: the first day of a plan has no night before
    it that the plan may write on.
    """
    try:
        before = (datetime.date.fromisoformat(meal_date) - datetime.timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None
    if _has_gone(before):
        return None
    for night in nights:
        if night.get("date") == before:
            return None if night.get("slot_state") == "planned_empty" else before
    return None


def move_target(night: dict, others: list[dict], caps: dict[str, int | None]) -> str | None:
    """
    The nearest night whose dinner can trade places with this one.

    `swap_dinner_nights` TRADES, so a move is only honest when BOTH ends
    come out within their caps: the flagged dish must fit the night we
    send it to, and that night's own dish must fit the short night it
    comes back to. Offering a move that would simply hand the same problem
    to another night is worse than offering nothing.

    Nearest by absolute day distance; a tie goes to the LATER night,
    because a week reads forwards and "move it to Saturday" is a smaller
    thing to absorb than "move it to Monday" on a Wednesday. Deterministic
    either way, so a test can name the answer.
    """
    minutes = night.get("minutes")
    if not minutes:
        return None
    try:
        here = datetime.date.fromisoformat(night["date"])
    except (TypeError, ValueError):
        return None

    def distance(row: dict) -> tuple[int, int]:
        try:
            gap = (datetime.date.fromisoformat(row["date"]) - here).days
        except (TypeError, ValueError):
            return (99, 0)
        # Later first on a tie: -1 sorts before 0 for the same abs().
        return (abs(gap), 0 if gap > 0 else 1)

    for row in sorted(others, key=distance):
        if row["date"] == night["date"] or not row.get("movable"):
            continue
        their_minutes = row.get("minutes")
        if not their_minutes:
            continue  # an unjudgeable dish can't be shown to fit anything
        there = caps.get(row["date"])
        back = caps.get(night["date"])
        if there and minutes > there:
            continue  # our dish still doesn't fit over there
        if back and their_minutes > back:
            continue  # theirs would land on the short night and break it
        if _has_gone(row["date"]):
            continue  # a night that is over is not somewhere to send a dinner
        # The one gate a move can break, and cap_enforce refuses its own
        # trades on it: who is at the table is per night, so a dish nobody
        # here minds can be one somebody there has said no to. Asked of
        # BOTH ends, because a trade moves two dinners.
        if _offends(row.get("meal"), night["date"]) or _offends(night.get("meal"), row["date"]):
            continue
        return row["date"]
    return None


def _has_gone(meal_date: str) -> bool:
    """
    Whether that night is behind the household's own today.

    Imported here rather than at module scope: `weekly_plan` reaches this
    module through `cap_enforce`, so a top-level edge would be a cycle for
    one predicate. It is `weekly_plan.night_has_gone` and NOT a date
    comparison of this module's own, for the reason that function's own
    docstring gives at length — the household's clock, never the server's,
    which from 8pm local are different days. A failure to read it answers
    False: an offer that turns out to be stale is a smaller wrong than a
    draft with no fixes on it at all, and the write behind the move asks
    its own questions.
    """
    if not meal_date:
        return True
    try:
        from . import weekly_plan as _weekly_plan
        return bool(_weekly_plan.night_has_gone(meal_date))
    except Exception:
        logger.exception("Reading the household's clock for %s failed", meal_date)
        return False


def _offends(meal: str | None, meal_date: str) -> str | None:
    """
    cap_enforce's own veto check, asked at this door too — see
    `cap_enforce.would_offend`, which is public for exactly this. Lazily
    imported because that module imports this one. A failure answers None:
    the taste record is advisory and a missing read must not take every
    fix off the draft.
    """
    if not (meal or "").strip() or not meal_date:
        return None
    try:
        from . import cap_enforce as _cap_enforce
        return _cap_enforce.would_offend(meal, meal_date)
    except Exception:
        logger.exception("Reading the taste verdict for %s on %s failed", meal, meal_date)
        return None


# ---------- the store ----------

def _clean(flag: dict) -> dict | None:
    """One flag, trimmed to the fields that are stored. Anything else is dropped."""
    entry_id = flag.get("entry_id")
    text = str(flag.get("text") or "").strip()
    if not entry_id or not text:
        return None
    # THE FIXES ARE DELIBERATELY NOT STORED, and dropping them here is what
    # makes that true of every caller rather than of the one that remembers.
    # An offer is a promise about a PAIR of nights and only one of them is
    # named by the flag, so a stored offer is a promise nothing revalidates
    # — see `fixes_for`. `plan_flags` derives them from the live week.
    return {
        "kind": str(flag.get("kind") or OVER_CAP_REQUEST),
        "text": text,
        "entry_id": int(entry_id),
        "date": str(flag.get("date") or "").strip(),
        "slot": str(flag.get("slot") or "").strip(),
        "dish": str(flag.get("dish") or "").strip(),
    }


def record(weekly_plan_id: int, flags: list[dict]) -> None:
    """
    Store this generation's flags, replacing whatever was there.

    Replacing rather than appending is deliberate: generation writes the
    whole set once, at the end, so a re-draft of the same plan describes
    the week as it now stands rather than accumulating the ghosts of
    earlier attempts.
    """
    cleaned = [f for f in (_clean(flag) for flag in flags or []) if f]
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(cleaned) if cleaned else "", weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def _stored(weekly_plan_id: int) -> list[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT draft_flags_json FROM weekly_plans WHERE id = ? AND household_id = ?",
        (weekly_plan_id, household_id()),
    ).fetchone()
    conn.close()
    try:
        data = json.loads(row["draft_flags_json"]) if row and row["draft_flags_json"] else []
    except (TypeError, ValueError):
        data = []
    return [f for f in data if isinstance(f, dict)]


def plan_flags(weekly_plan_id: int, intake: dict | None = None,
               memory: dict | None = None) -> list[dict]:
    """
    The flags that are still about something, each carrying the offers that
    are still true.

    A flag names one entry on one date carrying one dish. If the row has
    gone, moved, become a different meal, been cooked, or stopped being
    over its cap, the flag is stale and is dropped here rather than shown —
    see the module docstring on why this is a read-time rule and not a
    sweep at every write site.

    AND THE FIXES ARE RE-DERIVED, not read back. Nothing about an offer is
    stored; see `fixes_for` for why, which is the most important paragraph
    in this file. The short version: a fix is a promise about a PAIR of
    nights, the second of which this flag does not name, and the week moves
    under a draft constantly — including because of the flag's own sibling.
    A flag whose offers have all gone stale keeps its sentence and loses
    its buttons, which is the honest answer and is also what the card asks
    for ("doing nothing leaves it as asked").

    It reads the week through `cap_enforce.nights`, the same one reading of
    the plan and of the time rules the producer used, so the two can never
    disagree about a night. `intake` and `memory` are the caps' own inputs;
    a caller holding them should pass them, and one that isn't pays for two
    small reads.
    """
    flags = _stored(weekly_plan_id)
    if not flags:
        return []
    nights = _week_nights(weekly_plan_id, intake, memory)
    by_id = {n["id"]: n for n in nights}
    live = []
    planned = _planned_rows(weekly_plan_id)
    for flag in flags:
        if flag.get("kind") == NO_MEAL_NO_DAY_MORE:
            row = planned.get(flag.get("entry_id"))
            if row and (row["meal"] or "") == flag.get("dish") and (row["cooked_status"] or "") != "done":
                live.append({**flag, "fixes": []})
            continue
        if flag.get("kind") in (LATE_REQUEST, RUN_LEFT, DAY_OUT, ALLERGY_REQUEST, NO_MEAL_NO_DAY):
            # Not about a cap, so not judged by one: still about something
            # while the same dish is on the same meal, and not yet cooked.
            row = planned.get(flag.get("entry_id"))
            if row and row["date"] == flag.get("date") and (row["meal"] or "") == flag.get("dish") \
                    and (row["cooked_status"] or "") != "done":
                live.append({**flag, "fixes": []})
            continue
        night = by_id.get(flag.get("entry_id"))
        if night is None:
            continue
        if flag.get("date") and night["date"] != flag["date"]:
            continue  # the night moved — the sentence names a weekday that is no longer true
        if flag.get("dish") and (night.get("meal") or "") != flag["dish"]:
            continue  # a different dinner is there now
        if (night.get("cooked_status") or "") == "done":
            continue  # the work is done; the producer's own rule, asked again here
        if not night.get("over"):
            continue  # it fits now — somebody solved it another way
        if flag.get("kind") == FITS_NOWHERE:
            if _on_longest_night(night, nights):
                live.append({**flag, "fixes": []})
                continue
            # The week changed under it (a night freed up, a tag came
            # off): "your longest night is Sunday" is no longer true, but
            # the dish is still over the night it is on — so it is said
            # the way scenario 3 says it, with that scenario's fixes.
            flag = {**flag, "kind": OVER_CAP_REQUEST,
                    "text": over_cap_text(flag.get("dish") or night.get("meal") or "", night["minutes"], night["date"])}
        live.append({**flag, "fixes": fixes_for(night, nights)})
    return live


def _planned_rows(weekly_plan_id: int) -> dict[int, dict]:
    """Every planned day-slot entry of the plan, by id — what a flag that
    is not about a cap is checked against."""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT mpe.id, mpe.date, mpe.slot, mpe.cooked_status, mpe.derived_from_json, "
            "       COALESCE(r.name, mpe.freeform_meal) AS meal "
            "FROM meal_plan_entries mpe LEFT JOIN recipes r ON r.id = mpe.recipe_id "
            "WHERE mpe.weekly_plan_id = ? AND mpe.household_id = ? AND mpe.slot_state = 'planned' "
            "  AND mpe.component_category IS NULL",
            (weekly_plan_id, household_id()),
        ).fetchall()
        conn.close()
        return {r["id"]: dict(r) for r in rows}
    except Exception:
        logger.exception("Reading plan %s for its flags failed", weekly_plan_id)
        return {}


def _late_rows(weekly_plan_id: int) -> dict[int, dict]:
    """Every planned entry carrying the late-request mark, by id."""
    from . import typed_requests as _typed_requests
    return {i: r for i, r in _planned_rows(weekly_plan_id).items()
            if _derived_of(r).get(_typed_requests.LATE_KEY)}


def add(weekly_plan_id: int, flags: list[dict]) -> None:
    """
    Add flags to what this generation has already recorded — for a pass
    that runs AFTER cap_enforce wrote the set (dinner_gaps.break_long_runs).
    One flag per entry and kind: a second for the same meal replaces the first.
    """
    cleaned = [f for f in (_clean(flag) for flag in flags or []) if f]
    if not cleaned:
        return
    new_keys = {(f["entry_id"], f["kind"]) for f in cleaned}
    kept = [f for f in _stored(weekly_plan_id) if (f.get("entry_id"), f.get("kind")) not in new_keys]
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(kept + cleaned), weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def run_left_text(meal_date: str, slot: str, dish: str, since_date: str) -> str:
    """"Friday dinner is Thursday’s Beef Bowls again — nothing else fit
    Friday." One plain line; the row's own Swap is the fix. `since_date` is
    the day the run began."""
    day = _weekday(meal_date)
    whose = f"{_weekday(since_date)}’s " if since_date != meal_date else ""
    return f"{day} {slot} is {whose}{dish} again — nothing else fit {day}."


def _derived_of(row: dict) -> dict:
    try:
        return json.loads(row.get("derived_from_json") or "{}") or {}
    except (TypeError, ValueError):
        return {}


def flags_for_late_requests(weekly_plan_id: int) -> list[dict]:
    """One flag per meal their words named that was already past when the
    week was drafted — the flag says it; there is nothing to fix."""
    return [
        {"kind": LATE_REQUEST,
         "text": late_text(r["meal"], r["date"], r["slot"], moved=bool(_derived_of(r).get("moved_for"))),
         "entry_id": r["id"],
         "date": r["date"], "slot": r["slot"], "dish": r["meal"], "fixes": []}
        for r in _late_rows(weekly_plan_id).values() if (r["meal"] or "").strip()
    ]


def flags_for_days_out(weekly_plan_id: int) -> list[dict]:
    """
    One line per dish they asked for on a night nobody is home that was
    moved off it (cap_enforce.place_off_nights_out stamps the row). Said
    only when their words named that night — a dish the model happened to
    put there was rescued, but nobody asked for that night.
    """
    from . import cap_enforce as _cap_enforce  # lazy: it imports this module
    flags = []
    for r in _planned_rows(weekly_plan_id).values():
        stamp = _derived_of(r).get(_cap_enforce.DAY_OUT_KEY)
        if not isinstance(stamp, dict) or not stamp.get("said") or not (r["meal"] or "").strip():
            continue
        if r["slot"] != "dinner" or not stamp.get("from") or stamp["from"] == r["date"]:
            continue
        flags.append({"kind": DAY_OUT,
                      "text": day_out_text(r["meal"], stamp["from"], r["date"], bool(stamp.get("away"))),
                      "entry_id": r["id"], "date": r["date"], "slot": "dinner", "dish": r["meal"], "fixes": []})
    return sorted(flags, key=lambda f: f["date"])


def _their_phrase(clauses: list[str], stems: set[str]) -> str:
    """The longest run of the dish's own name words in their clauses about
    it — "peanut noodles" out of "peanut noodles one night please", "pad
    thai" out of "pad thai on tuesday with shrimp". Their words, as typed
    (lower case, the way they type them)."""
    from . import cap_enforce as _cap_enforce  # lazy: it imports this module
    best: list[str] = []
    for clause in clauses:
        run: list[str] = []
        for word in re.findall(r"[a-z]+(?:[-'’][a-z]+)*", clause.lower()) + [""]:
            if word and _cap_enforce._stem(word) in stems:
                run.append(word)
                continue
            if len(run) > len(best):
                best = run
            run = []
    return " ".join(best)


# Their words turning a dish DOWN, not asking for it: "No pad thai this
# week, we had it last week". week_intake's own negation words, plus the
# ways a household says "not again". Leans to silence — a wrong "You asked
# for…" is worse than no line.
_TURNED_DOWN_RE = re.compile(
    r"\b(?:no|not|nothing|never|none|without|skip|avoid|don't|dont|do not|instead of|rather than"
    r"|had (?:it|that|them|this|those)|last week|sick of|tired of"
    r"|hate|hated|hates|dislike|dislikes|loathe|can't stand|enough of)\b|n't\b",
    re.IGNORECASE,
)


def _turned_down(phrase: str, clauses: list[str], asks: tuple[str | None, ...]) -> bool:
    """Whether a clause about the dish, or any sentence of theirs that
    names it, says no to it."""
    from . import cap_enforce as _cap_enforce  # lazy: it imports this module
    texts = list(clauses) + [
        sentence for text in asks or () for sentence in _cap_enforce._SENTENCE_SPLIT_RE.split(text or "")
        if phrase in " ".join(re.findall(r"[a-z]+(?:[-'’][a-z]+)*", sentence.lower()))
    ]
    return any(_TURNED_DOWN_RE.search(t.replace("’", "'")) for t in texts)


def flags_for_allergy_requests(weekly_plan_id: int, asks: tuple[str | None, ...] = ()) -> list[dict]:
    """
    Rule 9: one line per dish they ASKED FOR that the allergen gate took
    off the draft and replaced (the row carries allergen_repick's stamp,
    allergen_gate.repick_stamp). Built from the gate's own record — the
    dish it dropped, the food it matched, who can't have it — and from
    their own words about that dish, never from the model's prose.

    "Asked for" is read off their words (derived_from.freeform and the
    sentences it came from, cap_enforce._clauses_about strictly: only a
    clause that names the dish), and is one of two things:
      * their words for the dish themselves have the food ("peanut
        noodles" for Sam's peanuts) — the request is what clashes;
      * they named the whole dish ("pad thai", the gate's Pad Thai had
        peanuts in its list) and what replaced it is not that dish any
        more. A Chicken Pad Thai still IS their pad thai: nothing to say.
    A dish the gate swapped that they only gestured at ("something
    Mexican") is not a request for that dish, and gets no line.
    """
    from . import allergen_gate as _allergen_gate  # lazy: keeps this module's imports light
    from . import cap_enforce as _cap_enforce  # lazy: it imports this module
    flags, said = [], set()
    rows = sorted(_planned_rows(weekly_plan_id).values(), key=lambda r: (r["date"], r["slot"]))
    for r in rows:
        derived = _derived_of(r)
        stamp = derived.get("allergen_repick")
        dish = (r["meal"] or "").strip()
        if not isinstance(stamp, dict) or "who" not in stamp or not dish:
            continue
        # `food` is only there when the matcher named one: a line that
        # can't name the food is no line at all.
        dropped, food = str(stamp.get("dropped") or "").strip(), str(stamp.get("food") or "").strip()
        if not dropped or not food or not str(derived.get("freeform") or "").strip():
            continue
        clauses, stems = _cap_enforce._clauses_about(derived, asks, dropped, strict=True)
        phrase = _their_phrase(clauses, stems)
        if not phrase or phrase in said or _turned_down(phrase, clauses, asks):
            continue
        theirs = {_cap_enforce._stem(w) for w in phrase.split()}
        clashes = [c for c in _allergen_gate.hard_clashes(phrase, draft=True)
                   if _allergen_gate.plain_food(c.get("matched") or "") == food]
        whole_dish = _cap_enforce._name_stems(re.split(r"\bwith\b", dropped, flags=re.IGNORECASE)[0]) <= theirs
        if not clashes and not (whole_dish and not theirs <= _cap_enforce._name_stems(dish)):
            continue
        said.add(phrase)
        who = [str(w) for w in stamp.get("who") or [] if str(w).strip()]
        flags.append({"kind": ALLERGY_REQUEST,
                      "text": allergy_request_text(phrase, food, who, dish, r["date"], r["slot"]),
                      "entry_id": r["id"], "date": r["date"], "slot": r["slot"], "dish": dish, "fixes": []})
    return flags


def flags_for_no_meal_no_day(meals: list[dict], asks: tuple[str | None, ...] = ()) -> list[dict]:
    """
    Rule 1: one line per dish they asked for naming no meal and no day
    (cap_enforce.asked_no_meal_no_day), saying where it is — off the
    dinners and lunches cap_enforce has just read back, so the line and
    the placement read one week.

    Said only where no other line is about that dish, so nothing is said
    twice: not over its meal's time (scenario 3's line, or rule 5's, says
    where it is and why), not a dish the allergen gate swapped in (rule 9's
    line), not one moved off a night nobody is home on their word (rule
    8's). Not a reheat, which is a line of its own on the screen. Once per
    dish.
    """
    from . import cap_enforce as _cap_enforce  # lazy: it imports this module
    flags, said = [], set()
    for m in sorted(meals or [], key=lambda m: (m.get("date") or "", m.get("slot") != "lunch")):
        derived = m.get("derived") or {}
        dish = (m.get("meal") or "").strip()
        if (not dish or dish.lower() in said or m.get("slot_state") != "planned"
                or (m.get("cooked_status") or "") == "done" or m.get("over") or m.get("reheat")
                or not str(derived.get("freeform") or "").strip()
                or "allergen_repick" in derived or (derived.get(_cap_enforce.DAY_OUT_KEY) or {}).get("said")
                or not _cap_enforce.asked_no_meal_no_day(derived, asks, dish)):
            continue
        said.add(dish.lower())
        flags.append({"kind": NO_MEAL_NO_DAY,
                      "text": no_meal_no_day_text(dish, m["date"], m["slot"], m.get("minutes")),
                      "entry_id": m["id"], "date": m["date"], "slot": m["slot"], "dish": dish, "fixes": []})
    if len(flags) > NO_MEAL_NO_DAY_LINES:
        rest = flags[2:]
        flags = flags[:2] + [{**rest[0], "kind": NO_MEAL_NO_DAY_MORE, "text": no_meal_no_day_more_text(len(rest))}]
    return flags


def _week_nights(weekly_plan_id: int, intake: dict | None,
                 memory: dict | None) -> list[dict]:
    """
    The plan's dinners as `cap_enforce` judges them — one reading of the
    rows and of the time caps, shared with the pass that wrote the flags.

    Lazy import: `cap_enforce` imports this module. A failure answers an
    empty list, which takes every flag off the draft rather than showing
    one whose offers nobody could check — the safe direction, since a flag
    is a remark and a wrong button is a write.
    """
    try:
        from . import cap_enforce as _cap_enforce
        if intake is None:
            from . import week_intake as _week_intake
            conn = get_conn()
            row = conn.execute(
                "SELECT week_start_date FROM weekly_plans WHERE id = ? AND household_id = ?",
                (weekly_plan_id, household_id()),
            ).fetchone()
            conn.close()
            # The intake is what carries `night_tags`, and a night's tag is
            # what makes its cap a rush cap — so reading the flags without
            # it would judge `over` against a different rule from the one
            # that wrote them. The filing key, not the content start date:
            # that is how the intake is addressed everywhere.
            intake = (_week_intake.get_week_intake(row["week_start_date"]) if row else None) or {}
        if memory is None:
            from . import memory as _memory
            memory = _memory.get_household_memory() or {}
        return _cap_enforce.nights(weekly_plan_id, intake, memory)
    except Exception:
        logger.exception("Re-reading plan %s to check its draft flags failed", weekly_plan_id)
        return []


def dismiss(weekly_plan_id: int, entry_id: int) -> bool:
    """
    Take one entry's flag off, because its fix has been applied.

    The move fix does not need this — re-dating the row makes `plan_flags`
    drop the flag on its own — but the prep fix leaves the entry exactly
    where it was, so nothing else would ever clear it.
    """
    flags = _stored(weekly_plan_id)
    kept = [f for f in flags if f.get("entry_id") != entry_id]
    if len(kept) == len(flags):
        return False
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(kept) if kept else "", weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()
    return True


# ---------- the producer ----------

def flags_for_kept_over_cap(nights: list[dict]) -> list[dict]:
    """
    Scenario 3, off the nights cap_enforce has already judged.

    A night qualifies when it is over its own cap AND the household spoke
    for it. Those two, in that order, are the whole rule — deliberately
    NOT "over cap and cap_enforce could not move it", which is a bigger
    set and would flag things nobody chose:

      * an unrequested dish too slow for its night is put right before the
        draft is shown (Emily's scenario 4), so it is normally gone by the
        time this runs — and when no repair could reach it, it is still
        not flagged: nobody chose it, and a line the household can do
        nothing about is noise. It stays in the morning report, which is
        where an app's own failure to place a dinner belongs.
      * a reheat, and the cook that feeds it — see the module docstring.
      * a night already cooked — the work is done, so there is nothing to
        offer and nothing to move.
      * a dish whose recipe records no minutes — an unjudgeable dish is
        not a violation anywhere else in this app either, and `over` is
        already False for it.

    `theirs` also implies `not movable` in cap_enforce's own terms, so a
    flag can never contradict what that pass did: every night named here
    is one it deliberately left standing. There is a test on exactly that,
    because the two are separate readings of one rule and nothing else
    would notice them drifting apart.
    """
    flags = []
    for night in nights:
        if not night.get("over"):
            continue
        derived = night.get("derived") or {}
        if not _theirs(derived):
            continue
        if night.get("reheat") or (night.get("source") and not derived.get("moved_for")):
            # A dish Pomona MOVED onto this night because their words said
            # "today"/"tonight"/"tomorrow" is said even when it feeds a
            # later meal (review, 2026-09-27): the move is Pomona's, so the
            # snag it made is Pomona's to say.
            continue
        if (night.get("cooked_status") or "") == "done":
            continue
        dish = (night.get("meal") or "").strip()
        minutes = night.get("minutes")
        if not dish or not minutes:
            continue
        if derived.get(_FITS_NOWHERE_KEY) and _on_longest_night(night, nights):
            # Rule 5, decided off the live week like everything else here:
            # placed by cap_enforce.place_fits_nowhere, and still on a
            # longest night of a week it fits nowhere in.
            flags.append({
                "kind": FITS_NOWHERE,
                "text": fits_nowhere_text(dish, minutes, night["date"]),
                "entry_id": night["id"],
                "date": night["date"],
                "slot": "dinner",
                "dish": dish,
                "fixes": [],
            })
            continue
        flags.append({
            "kind": OVER_CAP_REQUEST,
            "text": over_cap_text(dish, minutes, night["date"]),
            "entry_id": night["id"],
            "date": night["date"],
            "slot": "dinner",
            "dish": dish,
            "fixes": fixes_for(night, nights),
        })
    return flags


def fixes_for(night: dict, nights: list[dict]) -> list[dict]:
    """
    The two offers for one flagged night, worked out against the week as it
    stands RIGHT NOW.

    THIS IS ASKED AT READ TIME AND NOT ONLY WHEN THE FLAG IS WRITTEN, and
    that is the whole reason it is a function rather than four lines inside
    the producer. `move_target` promises something about a pair of nights —
    both ends come out within their caps — and a promise made once at
    generation is not the promise the button makes when it is tapped. The
    week moves underneath a draft constantly: a chat swap, the Plan strip's
    own Swap, the Review "+", the other adult's phone, and — the shape that
    makes this unarguable — the flag's own SIBLING, since two flagged
    nights are routinely offered the same free Saturday and taking both
    offers hands the second flag's dish straight back onto a short night.
    Taking the app's own two fixes in a row could leave the week worse than
    it started, with no flag left saying so.

    So nothing about a fix is stored. `record` keeps the SNAG — which night,
    which dish, the sentence — and every offer is re-derived here from the
    live week, by the same two functions that chose it the first time. A
    stale target is then impossible by construction rather than by
    diligence, which is the same read-time rule `plan_flags` already
    applied to the flagged entry itself and should always have applied to
    the half that writes.
    """
    fixes = []
    prep_on = prep_target(night["date"], nights)
    if prep_on:
        fixes.append(prep_fix(prep_on))
    move_to = move_target(night, nights, {n["date"]: n.get("cap") for n in nights})
    if move_to:
        fixes.append(move_fix(move_to))
        # Emily, 2026-09-28: a "Keep on <day it's already on>" beside every
        # move offer, for a household that would rather absorb the short
        # night than trade a dinner across the week. Tied to the move
        # offer rather than offered alone — see FIX_KEEP's own docstring.
        fixes.append(keep_fix(night["date"]))
    return fixes


# cap_enforce.FITS_NOWHERE_KEY, spelled here: that module imports this one.
_FITS_NOWHERE_KEY = "fits_nowhere"


def _on_longest_night(night: dict, nights: list[dict]) -> bool:
    """Whether this dish fits no night of the week and sits on one of the
    longest — cap_enforce's own reading (lazily imported; it imports this
    module), so the line and the placement can't disagree."""
    try:
        from . import cap_enforce as _cap_enforce
        return night["date"] in (_cap_enforce.longest_nights(night, nights) or [])
    except Exception:
        logger.exception("Reading the longest night for %s failed", night.get("meal"))
        return False


def _theirs(derived: dict) -> bool:
    # Lazy, and through meal_variety rather than a second reading of the
    # same keys: "the household spoke for this night" is one rule, and two
    # copies of it would let this file flag a night cap_enforce had
    # quietly moved (or leave one it had quietly kept).
    from . import meal_variety as _meal_variety
    return bool(_meal_variety.theirs(derived))


# ---------- after a hand-made move ----------

def refresh(weekly_plan_id: int, moved_ids: list[int] | None = None) -> None:
    """
    Re-say the draft's lines for the meals a Move just re-dated
    (meal_move, Emily 2026-09-28) — the producer's own rules, asked again
    of the week as it now stands, for those meals only.

    * An over-cap line for a moved meal is re-derived: a dish the household
      asked for that has just landed on a short-on-time night gets its
      "Lasagna takes 60 minutes, and Thursday is short on time." with the
      fixes worked out afresh; one that has moved off a short night loses
      its line (plan_flags would drop it on the date anyway — this also
      stops the old line coming back if it moves home again later without
      being over). Only the moved meals: a line the household already
      acted on elsewhere ("Prep it Tuesday night" dismisses its flag) is
      not re-raised by somebody moving a different meal.
    * A "nothing else fit" line whose run the move broke is taken off.

    A draft's control only: an approved week never shows these.
    """
    moved = set(moved_ids or [])
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT status FROM weekly_plans WHERE id = ? AND household_id = ?",
            (weekly_plan_id, household_id()),
        ).fetchone()
        conn.close()
        if row is None or row["status"] == "approved":
            return
    except Exception:
        logger.exception("Reading plan %s before refreshing its flags failed", weekly_plan_id)
        return
    stored = _stored(weekly_plan_id)
    kept = []
    for flag in stored:
        if flag.get("kind") in (OVER_CAP_REQUEST, FITS_NOWHERE) and flag.get("entry_id") in moved:
            continue  # re-derived below
        if flag.get("kind") == RUN_LEFT and not _still_in_a_long_run(weekly_plan_id, flag):
            continue
        kept.append(flag)
    fresh = []
    if moved:
        nights = _week_nights(weekly_plan_id, None, None)
        fresh = [f for f in flags_for_kept_over_cap(nights) if f.get("entry_id") in moved]
    cleaned = [f for f in (_clean(flag) for flag in kept + fresh) if f]
    if cleaned == [f for f in (_clean(flag) for flag in stored) if f]:
        return
    conn = get_conn()
    conn.execute(
        "UPDATE weekly_plans SET draft_flags_json = ? WHERE id = ? AND household_id = ?",
        (json.dumps(cleaned) if cleaned else "", weekly_plan_id, household_id()),
    )
    conn.commit()
    conn.close()


def _still_in_a_long_run(weekly_plan_id: int, flag: dict) -> bool:
    """Whether a run-left line's meal is still part of a run past the rule."""
    try:
        from . import leftovers as _leftovers
        keys = _leftovers.run_keys(weekly_plan_id)
        pos = (flag.get("date"), flag.get("slot"))
        return any(pos in run for run in _leftovers.long_runs(keys))
    except Exception:
        logger.exception("Checking a run-left line on plan %s failed", weekly_plan_id)
        return True
