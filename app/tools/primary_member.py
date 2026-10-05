"""
The household's MAIN PERSON — one per household (Loop Board "onboarding
asks 'What's your name?' first, then 'Who else lives with you?'",
2026-10-05).

WHAT THIS REPLACES, and it is the whole reason the module exists.
Onboarding's first question was "Who are we planning for?" — a list of
names, the first row defaulting to Adult — and from that moment
`members[0]` was SILENTLY TREATED AS THE USER. Two readers leaned on it:
`helperAdults` in static/onboarding.html built "does anyone else help run
the house?" as `members.slice(1)`, i.e. everyone but the first row, and
`record_setup_adult` (tools/first_open.py) takes the first ADULT among the
members onboarding just saved. Neither had anywhere to read "who is this
household's main person" from, because nothing recorded it. The order of
a list was doing the job of a field.

Since the split into "What's your name?" and "Who else lives with you?"
there IS a main person — the one who typed their own name on the first
screen, pinned at the top of the second with a "You" badge — so it is a
real field: `households.primary_member_id`, an INTEGER member id.

WHY AN ID AND NOT A NAME. A member's only identity in this app is the
name (two people called Sam are indistinguishable to the backend — see
the 2026-09-01 multi-household entry in CLAUDE.md, which records that as a
known limit). A name-keyed primary would inherit that limit and, worse,
would move the moment somebody was renamed. The wire still speaks names,
because that is the only identity onboarding has to offer; the route
resolves the name to an id among the members it has just saved, and the
id is what is stored.

WHY THIS IS NOT `households.set_up_by_member_id`, which already exists and
looks like the same thing. Two different questions:

  - `set_up_by_member_id` = WHO SET THE HOUSEHOLD UP. It is read by the
    first-open welcome to say "Emily's set up your household", it is
    stamped at the moment setup finishes, and its own module says in as
    many words that "a second pass through onboarding never moves it" —
    the welcome's correctness rests on it being a fact about the past.
  - `primary_member_id` = WHO THE MAIN PERSON IS. The card asks for it to
    be shown in Settings -> Who's here AND MOVED to another adult. A field
    that must move cannot be the one whose whole job is not moving.

Two patterns answering two different questions is correct; one name
serving both is not (CLAUDE.md's standing rule). They will usually name
the same person, and nothing requires them to.

HOW AN EXISTING HOUSEHOLD RESOLVES. The card: "primary = the member
already pinned on the setup device, or the first adult." Being pinned on a
device lives in the signed session COOKIE, so it cannot be read back from
the database — but `set_up_by_member_id` is exactly the member that pin is
written for (POST /api/onboarding/household sets the cookie to the id
`record_setup_adult` returns), so it is the database-readable form of the
same answer. So: the setter-up, else the household's first adult by
creation order. Resolved lazily here AND by an idempotent backfill in
db.py, so a household made by a script heals at the next startup.

NOT BUILT HERE, on purpose: a second device-pin mechanism.
`overnight/device-remembers-member` (card 3) adds `pomona_device_member`
and `picked_on_this_device`, which would be a more direct answer to "the
member pinned on the setup device" than `set_up_by_member_id` is. That
branch is not merged, and inventing a second pin beside the cookie one
that already works would be two implementations of one rule. When it
lands, the one line to revisit is `_resolve` below.
"""
from __future__ import annotations

from ..db import get_conn
from ._shared import household_id

# Said to the household when the main person can't be moved to whoever was
# named. Sentences rather than codes: Who's here shows them as they are.
PRIMARY_NOT_A_MEMBER = "I don’t have that person down as living here."
PRIMARY_NOT_AN_ADULT = "The main person needs to be one of the adults."

_ADULT_SQL = "LOWER(TRIM(age_group)) = 'adult'"
# A helper who signs in but doesn't eat here ("Someone not eating here" at
# setup) is an adult of the household and is NOT a candidate for its main
# person: the main person is whose home this is, not whoever can sign in.
_EATS_HERE_SQL = "COALESCE(eats_here, 1) = 1"


def _first_adult(conn) -> int | None:
    row = conn.execute(
        f"SELECT id FROM members WHERE household_id = ? AND {_ADULT_SQL} "
        f"AND {_EATS_HERE_SQL} ORDER BY id ASC LIMIT 1",
        (household_id(),),
    ).fetchone()
    return row["id"] if row else None


def _resolve(conn) -> int | None:
    """
    Who the main person is for a household that has never recorded one —
    the setter-up (the member this device's pin is written for), else the
    first adult. Returns None for a household with no adult eating here
    (one mid-onboarding, before its people are written): nothing to
    record, and it is asked again next time.
    """
    hh = conn.execute(
        "SELECT set_up_by_member_id FROM households WHERE id = ?", (household_id(),)
    ).fetchone()
    set_up_by = hh["set_up_by_member_id"] if hh else None
    if set_up_by is not None:
        # Still here, still an adult who eats here: a member re-marked a
        # child, or added as a helper, is not the main person.
        row = conn.execute(
            f"SELECT id FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL} "
            f"AND {_EATS_HERE_SQL}",
            (set_up_by, household_id()),
        ).fetchone()
        if row is not None:
            return row["id"]
    return _first_adult(conn)


def primary_member_id(conn=None) -> int | None:
    """
    This household's main person, as a member id, or None when there is no
    adult to be one yet.

    Has one side effect, deliberately, and it is the `first_open_state`
    shape: a household with nothing recorded has its answer RESOLVED AND
    RECORDED here, by a conditional UPDATE, so two readers at the same
    instant cannot settle on different people. That is what makes "an
    existing household's primary resolves to the setup member, else the
    first adult" true of a household made between two startups (by
    create_household.py, or by a test) rather than only of one the
    idempotent backfill in db.py has seen.

    A recorded primary who has since stopped being an adult eating here is
    treated as no answer and re-resolved — the one case the stored value is
    not simply believed.

    `conn` is a caller's open connection when it has one; this neither
    commits nor closes it then.
    """
    own = conn is None
    conn = get_conn() if own else conn
    try:
        row = conn.execute(
            "SELECT primary_member_id FROM households WHERE id = ?", (household_id(),)
        ).fetchone()
        stored = row["primary_member_id"] if row else None
        if stored is not None:
            ok = conn.execute(
                f"SELECT id FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL} "
                f"AND {_EATS_HERE_SQL}",
                (stored, household_id()),
            ).fetchone()
            if ok is not None:
                return ok["id"]
        chosen = _resolve(conn)
        if chosen is None:
            return None
        # Claim it only while nothing is recorded, so the resolve above
        # cannot overwrite a deliberate Settings choice. A stored value
        # that failed the check above is re-resolved for the ANSWER and
        # left on the row — moving it is the household's to do, and
        # quietly re-pointing a field somebody set is worse than reading
        # past it.
        conn.execute(
            "UPDATE households SET primary_member_id = ? "
            "WHERE id = ? AND primary_member_id IS NULL",
            (chosen, household_id()),
        )
        if own:
            conn.commit()
        return chosen
    finally:
        if own:
            conn.close()


def record_primary_member(member_id: int | None, conn=None) -> int | None:
    """
    Record the main person at the moment setup finishes — the person who
    typed their own name on onboarding's first screen. Returns the id
    recorded, or None when nothing was.

    Only while nothing is recorded yet, and that is the same rule
    `record_setup_adult` states for its own field: a second pass through
    onboarding never moves it. The reason here is not invite-link safety,
    it is that MOVING the main person is Settings' job by the card's own
    wording — so a re-run of setup must not silently overrule a choice
    somebody made there. It is one tap in Who's here either way.

    Refuses a member who isn't an adult of this household eating here,
    silently (returns None): the resolver's fallback then names somebody,
    which is better than recording a child as the main person because a
    screen let their chip be changed.
    """
    if member_id is None:
        return None
    own = conn is None
    conn = get_conn() if own else conn
    try:
        row = conn.execute(
            f"SELECT id FROM members WHERE id = ? AND household_id = ? AND {_ADULT_SQL} "
            f"AND {_EATS_HERE_SQL}",
            (member_id, household_id()),
        ).fetchone()
        if row is None:
            return None
        claimed = conn.execute(
            "UPDATE households SET primary_member_id = ? "
            "WHERE id = ? AND primary_member_id IS NULL",
            (row["id"], household_id()),
        ).rowcount
        if own:
            conn.commit()
        return row["id"] if claimed else None
    finally:
        if own:
            conn.close()


def set_primary_member(member_id: int) -> dict:
    """
    Move the main person to another adult — Settings -> Who's here (the
    card: "shows 'Main person' next to them, and lets it be moved to
    another adult").

    Raises ValueError with a sentence the screen can show as it stands:
    somebody who isn't in this household, or who is not an adult eating
    here. Scoped by household_id() in every statement, so another
    household's member id is "I don't have that person down" rather than
    a move.
    """
    try:
        member_id = int(member_id)
    except (TypeError, ValueError):
        raise ValueError(PRIMARY_NOT_A_MEMBER)
    conn = get_conn()
    try:
        row = conn.execute(
            f"SELECT id, name, age_group, {_EATS_HERE_SQL} AS eats_here "
            "FROM members WHERE id = ? AND household_id = ?",
            (member_id, household_id()),
        ).fetchone()
        if row is None:
            raise ValueError(PRIMARY_NOT_A_MEMBER)
        if str(row["age_group"] or "").strip().lower() != "adult" or not row["eats_here"]:
            raise ValueError(PRIMARY_NOT_AN_ADULT)
        conn.execute(
            "UPDATE households SET primary_member_id = ? WHERE id = ?",
            (row["id"], household_id()),
        )
        conn.commit()
        return {"primary_member_id": row["id"], "name": row["name"]}
    finally:
        conn.close()
