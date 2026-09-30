"""
The morning report's FOOD section prints one line per distinct finding.

WHY THIS EXISTS. `get_recent_plan_quality` already dedupes by
(rule, date, slot, message), so every row it returns is a genuinely different
finding — but a RECIPE-level rule (quantities_plausible,
steps_match_ingredients) writes a message that names the recipe and not the
night. The same recipe planned on three nights therefore arrives as three
rows whose printed text is character-identical, and the section is capped at
six lines.

FOUND BY READING THE LIVE APP, 2026-09-30, not by reading the code. Household
1's six lines were: one snack warning, the same butter sentence three times,
and the same carrots sentence twice — three pieces of news in six slots,
while the other 24 findings that week, nine of them dinner_repeat_in_history,
printed nothing at all.

Measured again with the fix, same data, same six slots: the snack warning,
butter (3 nights), carrots (3 nights), and THREE reasoning_is_specific
warnings that had never been printed. Twice the news in the same space.

COLLAPSING RATHER THAN DATING EACH LINE is the deliberate choice, and it is
the one thing here worth arguing with: three nights of one recipe is ONE
thing to fix — the recipe — and the count keeps what the dates were there to
say. Printing three dates would have been honest and would have spent three
slots saying it.
"""
import observability_report as o


def _rows(*pairs):
    return [{"severity": s, "message": m} for s, m in pairs]


def test_the_same_sentence_on_three_nights_is_one_line_and_says_three():
    """The reported bug, in its own shape."""
    lines = o._food_lines(_rows(
        ("info", "Lentil Soup: Butter '1 stick' is more than 2 would use."),
        ("info", "Lentil Soup: Butter '1 stick' is more than 2 would use."),
        ("info", "Lentil Soup: Butter '1 stick' is more than 2 would use."),
    ))
    assert lines == [("info", "Lentil Soup: Butter '1 stick' is more than 2 would use.", 3)]


def test_a_finding_seen_once_carries_no_count():
    """A "(1 nights)" on every other line would be noise, and ungrammatical."""
    lines = o._food_lines(_rows(("warn", "2026-10-03 snack has no reasoning at all.")))
    assert lines == [("warn", "2026-10-03 snack has no reasoning at all.", 1)]


def test_messages_that_name_their_own_night_never_collapse():
    """
    The rules whose message embeds a date — snack_echoes_a_meal,
    reasoning_is_specific — are unaffected by construction, because their
    sentences differ. Asserted rather than assumed: this is the half that
    would silently lose information if the key were ever loosened to the
    rule name.
    """
    lines = o._food_lines(_rows(
        ("warn", "2026-10-03 snack ('Roasted Chickpeas') has no reasoning at all."),
        ("warn", "2026-10-02 snack ('Roasted Chickpeas') has no reasoning at all."),
    ))
    assert [n for _s, _m, n in lines] == [1, 1]
    assert len(lines) == 2


def test_severity_is_part_of_what_makes_a_line_the_same_line():
    """One sentence at two severities is two facts, not one said twice."""
    lines = o._food_lines(_rows(("info", "same words"), ("warn", "same words")))
    assert len(lines) == 2


def test_the_order_is_the_order_they_arrived_in():
    """
    `recent` is newest first (MAX(e.id) per key, ordered), and the section is
    capped, so re-ordering here would silently change WHICH findings print.
    """
    lines = o._food_lines(_rows(
        ("warn", "first"), ("info", "second"), ("warn", "first"), ("info", "third"),
    ))
    assert [m for _s, m, _n in lines] == ["first", "second", "third"]
    assert [n for _s, _m, n in lines] == [2, 1, 1]


def test_the_six_line_cap_now_counts_DISTINCT_findings():
    """
    The point of the change, stated as behaviour: eight rows that are three
    findings fill three of the six slots, not six. Before, the same eight
    rows filled all six and said three things.
    """
    rows = _rows(
        ("info", "A"), ("info", "A"), ("info", "A"),
        ("info", "B"), ("info", "B"), ("info", "B"),
        ("warn", "C"), ("warn", "C"),
    )
    assert len(o._food_lines(rows)[:6]) == 3


def test_an_empty_or_missing_field_never_raises():
    """
    The report reads a live deployment that may be older than any given
    field, and this file's siblings all use `.get` for that reason. A
    KeyError here would take the whole morning report down over a cosmetic
    section.
    """
    assert o._food_lines([]) == []
    assert o._food_lines([{}]) == [("", "", 1)]


# --------------------------------------------------------------------------
# The line as it is PRINTED. _food_lines decides what is one finding; this
# decides what the reader sees, and the `nights > 1` rule is the whole of
# the fix — so it gets its own assertions rather than riding on the helper's.
# --------------------------------------------------------------------------

def test_one_night_prints_no_suffix_at_all():
    assert o._food_line_text("warn", "Monday has no reasoning.", 1) == (
        "      warn  Monday has no reasoning."
    )


def test_several_nights_print_the_count():
    assert o._food_line_text("info", "Soup: Butter is a lot.", 3) == (
        "      info  Soup: Butter is a lot.  (3 nights)"
    )


def test_the_severity_column_still_lines_up():
    """
    `warn` and `info` are four characters, `error` is five — the section has
    always padded to five so the sentences align. Pinned because the padding
    is the kind of thing a later f-string tidy-up drops without noticing.
    """
    a = o._food_line_text("warn", "x", 1)
    b = o._food_line_text("error", "x", 1)
    assert a.index("x") == b.index("x")
