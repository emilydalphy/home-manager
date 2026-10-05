"""
Tripwire for CLAUDE.md, the file every session reads first.

WHAT HAPPENED (2026-09-26). The orchestrating session of that night's
overnight run built a ten-branch merge tree with a script that resolved
CLAUDE.md conflicts by running `git checkout --ours CLAUDE.md` BEFORE its
keep-both resolver. `--ours` resolves the conflict by taking one side and
clears the markers, so the resolver's `git diff --diff-filter=U` then found
nothing to do and never ran. About a thousand lines of Decision log went
missing across nine merges.

Nothing failed. That tree was 7618 passed / 0 failed at the live clock, at
three weekday pins and under a genuine straddle. It was found by arithmetic
rather than by a test: the ten branches' individual additions summed to
+1328 lines and the merged tree showed +292.

WHY THE SUITE COULD NOT SEE IT. Exactly two test files read this file, and
between them they assert three strings, all anchored on entries dated 13 and
17 September — near the BOTTOM of a log that is written newest-first. Every
branch appends at the TOP. So those assertions were green whether the last
two weeks of history survived the merge or not: measured on the mangled
tree, 3/3 anchors present, suite fully green, a thousand lines gone.

THE PRECEDENT. This repo fixed this exact class once before, for
static/shell.js (merge 2d69951, 2026-09-08), and wrote the rule down: when a
merge conflicts there, resolve it hunk by hunk, keep both sides, and never
take one side wholesale. tests/test_frontend_restored_2026_09_08.py is that
tripwire — named markers plus a blunt line-count floor, "because a wholesale
resolution always shows up as a large sudden shrink". This file is the same
shape for CLAUDE.md, which had no equivalent.

WHAT THIS FILE CATCHES, precisely:
  1. The file getting SHORTER than it is now          -> the line floor.
  2. Decision log ENTRIES going missing, even when the
     file is still long                               -> the entry-count floor.
  3. The NEWEST end of the log being lost — the half a
     merge actually touches                           -> the newest-date floor
                                                         and the named anchors.
  4. The two existing readers being deleted rather
     than added to                                    -> the guard on the guard.

WHAT IT CANNOT CATCH, and this is not a hedge — it is the shape of the very
incident above. A merge that drops an entry which only ever existed on the
BRANCH being merged leaves a file that is longer than main was and carries
every one of main's own entries, so every floor and every anchor here passes.
test_the_floors_cannot_see_the_2026_09_26_shape demonstrates that by
construction rather than claiming it. The only thing that catches that case
is arithmetic at merge time: sum the branches' own additions and compare with
the merged tree's.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The Decision log moved out of CLAUDE.md into docs/DECISION_LOG.md on
# 2026-10-05 (CLAUDE.md had reached 24,000 lines, re-read by every session).
# Every guard below on the LOG reads LOG_MD; CLAUDE_MD is the short briefing.
LOG_PATH = "docs/DECISION_LOG.md"
LOG_MD = (REPO / "docs" / "DECISION_LOG.md").read_text(encoding="utf-8")
CLAUDE_MD = (REPO / "CLAUDE.md").read_text(encoding="utf-8")

# A Decision log entry heading. The log's own format, used unchanged since
# the file was started: a top-level bullet opening with a bold ISO date and
# an em dash. Anything indented under an entry is that entry's body, so the
# `^` anchor is what makes this a count of ENTRIES rather than of bullets.
ENTRY_HEADING = re.compile(r"^- \*\*(20\d\d-\d\d-\d\d) — ", re.M)

# --- the floors ----------------------------------------------------------
# All three were taken on docs/DECISION_LOG.md right after the move out of
# CLAUDE.md (2026-10-05, on top of main 8295894): 24543 lines, 269 entries,
# newest entry 2026-10-05. Earlier floors: ee741f8 (2026-09-27), 19030 lines. Raise them together when the log has
# grown well past them; only lower one for a deletion you can point at.

LINE_FLOOR = 24000
ENTRY_FLOOR = 264
NEWEST_ENTRY_ON_OR_AFTER = "2026-10-05"


def _entry_dates() -> list[str]:
    return ENTRY_HEADING.findall(LOG_MD)


# --- 1. the file as a whole ----------------------------------------------

def test_claude_md_did_not_suddenly_get_shorter():
    """A blunt size floor, shell.js's own guard one file over.

    A conflict resolved one-side-wholesale is always a large sudden shrink,
    and this catches the shape of it even for work that has no named anchor
    below. Raise the floor when the file grows well past it; only lower it
    for a deletion you can point at, and say which one in the same commit."""
    lines = LOG_MD.count("\n") + 1
    assert lines > LINE_FLOOR, (
        f"docs/DECISION_LOG.md is {lines} lines — suspiciously short (floor {LINE_FLOOR}, "
        "set from 19030 on origin/main at ee741f8). A merge that resolves "
        "this file by taking one side whole loses whatever the other side "
        "had. Check `git log -p --follow CLAUDE.md` for a merge that took "
        "one side before assuming this is fine."
    )


# --- 2. the Decision log ---------------------------------------------------

def test_the_decision_log_did_not_lose_entries():
    """The line floor alone can be satisfied by a long file whose middle has
    gone. This counts the dated entry headings instead, so losing a run of
    history fails even when the length is made up elsewhere.

    The log is append-only by its own stated rule — an entry that turns out
    to be wrong is corrected in place and says so, never quietly deleted — so
    a count that has gone DOWN wants explaining rather than adjusting for."""
    n = len(_entry_dates())
    assert n >= ENTRY_FLOOR, (
        f"docs/DECISION_LOG.md has {n} dated entries — fewer than the "
        f"floor of {ENTRY_FLOOR} (set from 219 on origin/main at ee741f8). "
        "Entries are never deleted here, only corrected in place, so this is "
        "a merge or an edit that dropped history. Do not lower the floor to "
        "make this pass."
    )


# Deliberately looser than ENTRY_HEADING: a bullet that opens with something
# date-shaped is meant to be an entry, however it is punctuated.
# Column 0 ONLY, and that is a measurement rather than an oversight. An
# INDENTED `- **YYYY-MM-DD — ` is a legitimate shape in this file: there is
# one today, the 2026-09-21 evening-cook-nudge note nested under the Morning
# text bullet in Current state, and it is deliberately not a Decision log
# entry. A review proposed `^\s*- \*\*` here to catch a reformat that indents
# real entries; run against the real file it reddens on that sub-bullet, so
# indentation cannot be the drift signal. The limit is real and is pinned by
# test_an_indented_sub_bullet_is_not_an_entry_and_cannot_be_told_apart below.
LOOSE_HEADING = re.compile(r"^- \*\*\s*(20\d\d[-/]\d\d?[-/]\d\d?)", re.M)


def test_no_entry_is_written_in_a_shape_the_counter_cannot_see():
    """The guard on the counter, and the one thing the floor above cannot do
    for itself.

    A heading written with an en dash, a slashed date or a stray space stops
    matching ENTRY_HEADING, so the count falls — and it falls in exactly the
    direction that looks like lost history. Worse, the reverse: format drift
    on twenty entries plus a genuine loss of twenty reads as a loss of forty,
    or a loss of twenty hides inside a drift that was 'expected'. This sweeps
    for bullets that are obviously entries and are not being counted.

    The first version of this test read every match back as a date instead,
    which cannot fail on drift by construction — it only ever validated the
    headings that had already matched. Measured: breaking the em dash on 30
    headings left it green."""
    strict = {m.start() for m in ENTRY_HEADING.finditer(LOG_MD)}
    missed = [
        LOG_MD[m.start() : LOG_MD.find("\n", m.start())][:90]
        for m in LOOSE_HEADING.finditer(LOG_MD)
        if m.start() not in strict
    ]
    assert not missed, (
        "these look like Decision log entries but do not match the heading "
        "format the entry count is taken from, so they are invisible to it:\n  "
        + "\n  ".join(missed)
        + "\nThe format is `- **YYYY-MM-DD — `, with an em dash. Fix the "
        "heading rather than the pattern."
    )


# --- 3. the newest end, which is the half a merge touches ------------------

def test_the_newest_entry_is_still_recent():
    """The 2026-09-26 lesson in one assertion. The two tests that already
    read this file anchor on 13 and 17 September — the BOTTOM of a
    newest-first log, i.e. the half no merge ever touches. This asserts on
    the top instead: if a resolution takes a stale side, the newest surviving
    entry jumps backwards in time and this goes red.

    It never goes red for a legitimate reason, because dates only move
    forward. It simply gets less useful as it ages, so raise it alongside the
    two floors above."""
    dates = _entry_dates()
    assert dates, "docs/DECISION_LOG.md has no entries at all"
    newest = max(dates)
    assert newest >= NEWEST_ENTRY_ON_OR_AFTER, (
        f"the newest Decision log entry is dated {newest}, but this file was "
        f"last known to reach {NEWEST_ENTRY_ON_OR_AFTER}. Entries are only "
        "ever added, so the log cannot get younger on its own — something "
        "took an older side of this file."
    )


# One phrase per entry, from the newest date on origin/main at ee741f8.
# Verbatim on purpose: if an entry is deliberately reworded, update the
# constant here in the same commit and say so — do not delete the anchor.
NEWEST_ANCHORS = (
    "2026-09-26 — `clock (monday)` was RED on `main`",
    "2026-09-26 — Rating a recipe takes one of two verdicts",
    "2026-09-26 — Two modules in `app/tools/` are shadowed by a function of",
    "2026-09-26 — The morning report's FOOD count counted discarded drafts",
    "2026-09-26 — 107 statements across `app/` reached a household-owned row",
    '2026-09-26 — "Short on time" is held to by CODE now',
)


def test_the_newest_entries_are_still_named_in_the_log():
    """Named anchors at the top of the log, which is what the two existing
    readers lack. A floor says how much is there; an anchor says WHICH work
    is written down, so a resolution that keeps the length and swaps the
    content still fails."""
    missing = [anchor for anchor in NEWEST_ANCHORS if anchor not in LOG_MD]
    assert not missing, (
        "these Decision log entries are gone from docs/DECISION_LOG.md:\n  "
        + "\n  ".join(missing)
        + "\nThey were on origin/main at ee741f8. If one was deliberately "
        "reworded, update NEWEST_ANCHORS in the same commit; if it is simply "
        "missing, a merge dropped it."
    )


# --- 4. the guard on the guard --------------------------------------------

# The honest sweep. The 2026-09-26 session looked for readers with
#   grep -rnE '(open|read_text|Path)\([^)]*CLAUDE' tests/*.py
# got nothing, and concluded no test read the file — but that pattern needs
# CLAUDE inside the parentheses of the call, and the real form is
#   (REPO / "CLAUDE.md").read_text(encoding="utf-8")
# where the filename is in the path division and the parens hold the
# encoding. Search on the assignment, or on the filename alone.
READER_ASSIGNMENT = re.compile(
    r'^\w+\s*=.*"DECISION_LOG\.md".*read_text', re.M
)

KNOWN_READERS = (
    "test_cook_shelf.py",
    "test_today_shop_cook.py",
    "test_claude_md_tripwire.py",
)


def _reader_files() -> set[str]:
    found = set()
    for path in sorted((REPO / "tests").glob("test_*.py")):
        if READER_ASSIGNMENT.search(path.read_text(encoding="utf-8")):
            found.add(path.name)
    return found


def test_the_existing_readers_still_read_the_file():
    """Their claim — that the shipped design is written down where the next
    session will read it — is real, and the card that asked for this file said
    to add to them rather than replace them. This fails if one is deleted."""
    found = _reader_files()
    for name in KNOWN_READERS:
        assert name in found, (
            f"tests/{name} no longer reads docs/DECISION_LOG.md. It is one of the "
            "tripwires on that file; if its anchor was genuinely superseded, "
            "move the anchor rather than dropping the reader."
        )


def _assert_sources(path):
    """Every `assert` statement in a file, as source text.

    Read with `ast` rather than grepped, because the claim is that a real
    assertion is still there — and a COMMENTED-OUT one satisfies a substring
    search perfectly. Measured on the first cut of this file: replacing
    test_cook_shelf.py's assertion with
    `pass  # assert "2026-09-13 — Cook's root is the shelf" in LOG_MD`
    left all sixteen tests green and that file green too, which is precisely
    the weakening this test is named for. It is the repo's own
    comment-stripping idiom, one level up: parse, don't match.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        ast.get_source_segment(source, node) or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Assert)
    ]


def test_the_two_older_readers_still_assert_on_what_they_read():
    """A file can read CLAUDE.md and assert nothing about it. These two are
    the repo's original anchors and their assertions are the thing worth
    keeping — so the anchor has to appear inside a real `assert`, not merely
    somewhere in the file."""
    for name, anchor in (
        ("test_cook_shelf.py", "2026-09-13 — Cook's root is the shelf"),
        ("test_today_shop_cook.py", "2026-09-13 — Now is one strip down the day"),
        (
            "test_today_shop_cook.py",
            "2026-09-17 — Today: Shop and Cook, tagged by part of the day",
        ),
    ):
        asserts = _assert_sources(REPO / "tests" / name)
        assert any(anchor in a for a in asserts), (
            f"tests/{name} no longer ASSERTS {anchor!r} against docs/DECISION_LOG.md. "
            "The string may still be in the file — in a comment, or in a "
            "docstring — but a commented-out assertion is not a tripwire."
        )
        assert anchor in LOG_MD, (
            f"{anchor!r} is gone from docs/DECISION_LOG.md — tests/{name} should be red "
            "too; if it is not, its assertion has been weakened"
        )


def test_the_sweep_that_missed_them_still_misses_them():
    """The lesson made executable. This is the pattern the 2026-09-26 session
    used to conclude that nothing read CLAUDE.md, and it is still wrong — kept
    here so the next person reaching for it sees, in one run, that it answers
    empty over files that demonstrably do read the file."""
    wrong = re.compile(r"(open|read_text|Path)\([^)]*DECISION_LOG")
    for name in KNOWN_READERS:
        source = (REPO / "tests" / name).read_text(encoding="utf-8")
        assert READER_ASSIGNMENT.search(source), f"{name} should be a reader"
        assert not wrong.search(source), (
            f"the bad sweep now matches tests/{name} — if the reading idiom "
            "changed, this test has stopped teaching anything and the comment "
            "above READER_ASSIGNMENT should be re-checked"
        )


# --- 5. what none of it catches, shown rather than claimed -----------------

def test_the_floors_cannot_see_the_2026_09_26_shape():
    """The honest limit, by construction.

    Rebuild the incident: a merge tree that ADDS to main (so it is longer and
    has more entries than main ever did) while silently dropping most of what
    the branches themselves brought. Every check in this file passes it,
    because every check is anchored on main's own content and that content is
    all still there.

    Nothing in tests/ can catch this, and no floor set higher would either —
    the merged tree is bigger than the baseline by construction. What catches
    it is arithmetic at merge time: the ten branches added +1328 lines between
    them and the merged tree showed +292."""
    lines = LOG_MD.count("\n") + 1
    entries = len(_entry_dates())

    # The mangled tree of 2026-09-26: main, plus 292 of the 1328 lines its
    # branches were owed, and one of the ten branch entries surviving.
    mangled = LOG_MD + "\n" + "\n".join(
        ["- **2026-09-27 — A branch entry that survived the merge.**"]
        + ["  padding that stands in for that entry's body."] * 290
    )
    assert mangled.count("\n") + 1 > lines
    assert len(ENTRY_HEADING.findall(mangled)) > entries

    # ...and it sails through every guard above.
    assert mangled.count("\n") + 1 > LINE_FLOOR
    assert len(ENTRY_HEADING.findall(mangled)) >= ENTRY_FLOOR
    assert max(ENTRY_HEADING.findall(mangled)) >= NEWEST_ENTRY_ON_OR_AFTER
    assert all(anchor in mangled for anchor in NEWEST_ANCHORS)


# --- 6. the rule is written down where a session will read it --------------
# CLAUDE.md is what a session reads first, so the merge rule lives THERE
# even though the log it protects moved to docs/DECISION_LOG.md (2026-10-05).

CLAUDE_MD_MAX_LINES = 1500


def test_the_hunk_by_hunk_rule_names_this_file_too():
    """The rule has to name the log file alongside static/shell.js, and point
    at both guards, in the file every session reads first."""
    assert "`static/shell.js` OR in\n  `docs/DECISION_LOG.md`" in CLAUDE_MD, (
        "CLAUDE.md's merge rule no longer names docs/DECISION_LOG.md alongside "
        "static/shell.js"
    )
    assert "tests/test_claude_md_tripwire.py" in CLAUDE_MD, (
        "CLAUDE.md no longer names this file as the tripwire, so a session "
        "reading the rule has no way to find the guard it depends on"
    )
    assert "check_merge_kept_the_log.py" in CLAUDE_MD, (
        "CLAUDE.md no longer names the merge-arithmetic check. It is the only "
        "thing that catches a merge dropping a BRANCH's own entry."
    )


def test_claude_md_stays_a_briefing_not_a_log():
    """2026-10-05: the log moved out because CLAUDE.md had reached 24,000
    lines and every session and sub-agent paid to read it. New entries belong
    in docs/DECISION_LOG.md; this fails if they start landing here again."""
    lines = CLAUDE_MD.count("\n") + 1
    assert lines < CLAUDE_MD_MAX_LINES, (
        f"CLAUDE.md is {lines} lines (ceiling {CLAUDE_MD_MAX_LINES}). Decision "
        "log entries go in docs/DECISION_LOG.md, not here."
    )
    assert "docs/DECISION_LOG.md" in CLAUDE_MD
    assert not ENTRY_HEADING.findall(CLAUDE_MD), (
        "CLAUDE.md has a dated Decision log entry in it again — move it to "
        "docs/DECISION_LOG.md"
    )


# --- 7. the arithmetic, which is the only thing that catches the real shape --
#
# MEASURED 2026-09-27, on that night's own four branches: resolving every
# CLAUDE.md conflict with `git checkout --ours` lost 493 lines and three of
# the four branches' Decision log entries, and ALL TEN tests above passed.
# An earlier ordering of the same four merges happened to redden exactly one
# of them — test_the_hunk_by_hunk_rule_names_this_file_too — and only because
# the lost entries included this branch's own prose anchor. That is luck, not
# a guard. check_merge_kept_the_log.py is the guard; these tests are its.

import subprocess
import sys

SCRIPT = REPO / "check_merge_kept_the_log.py"


def _run(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _git(cwd, *args) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _tiny_repo(tmp_path):
    """A throwaway repo shaped like this one: a CLAUDE.md with a Decision log,
    a base commit, and two branches that each append an entry at the TOP —
    which is where every real branch appends, and the half a merge touches."""
    repo = tmp_path / "tiny"
    repo.mkdir()
    (repo / "docs").mkdir()
    _git(repo, "init", "-q", "-b", "base")
    _git(repo, "config", "user.email", "x@y")
    _git(repo, "config", "user.name", "x")
    # Six body lines, not one: a branch that REWORDS existing prose is the
    # shape that broke the line arithmetic, and it needs something to reword.
    # One line is inside LINE_SLACK_PER_BRANCH, so the mutation that reads
    # insertions without deletions sailed through a one-line fixture.
    log = (
        "## Decision log\n\n- **2026-01-01 — The first thing.**\n"
        + "  Its body.\n" * 6
    )
    (repo / LOG_PATH).write_text(log, encoding="utf-8")
    _git(repo, "add", LOG_PATH)
    _git(repo, "commit", "-qm", "base")
    for name, date in (("one", "2026-02-01"), ("two", "2026-03-01")):
        _git(repo, "checkout", "-q", "-b", name, "base")
        (repo / LOG_PATH).write_text(
            log.replace(
                "## Decision log\n\n",
                f"## Decision log\n\n- **{date} — Branch {name}.**\n"
                + f"  Body of {name}.\n" * 5
                + "\n",
            ),
            encoding="utf-8",
        )
        _git(repo, "add", LOG_PATH)
        _git(repo, "commit", "-qm", name)
    _git(repo, "checkout", "-q", "base")
    return repo


def _keep_both(repo) -> None:
    """Resolve a CLAUDE.md conflict the way the rule says: keep both sides.

    One implementation, used by every fixture here — two copies of the rule
    under test is how a fixture quietly stops reproducing the thing it is
    named after."""
    text = (repo / LOG_PATH).read_text(encoding="utf-8")
    (repo / LOG_PATH).write_text(
        "\n".join(
            line
            for line in text.split("\n")
            if not line.startswith(("<<<<<<< ", ">>>>>>> ")) and line != "======="
        ),
        encoding="utf-8",
    )


def _merge(repo, resolve: str) -> None:
    # On a branch of its own, never on `base` itself: a merge made ONTO the
    # base branch moves it, and then the script has nothing to compare. That
    # is a real trap and it has its own test below.
    _git(repo, "checkout", "-q", "-B", "tree", "base")
    for name in ("one", "two"):
        out = subprocess.run(
            ["git", "merge", "--no-edit", name],
            cwd=str(repo), capture_output=True, text=True,
        )
        if out.returncode == 0:
            continue
        if resolve == "ours":
            _git(repo, "checkout", "--ours", LOG_PATH)
        else:  # keep both sides, which is the rule
            _keep_both(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"merge {name}")


def test_the_arithmetic_catches_a_merge_that_took_one_side(tmp_path):
    """The 2026-09-26 shape, end to end: two branches that each appended an
    entry, merged with `--ours` on the conflict. Exit 1, and the message says
    how many entries went rather than only that something is wrong."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "ours")
    result = _run(repo, "base", "one", "two")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DROPPED: 1 Decision log entry" in result.stdout
    assert "--ours" in result.stdout


def test_the_arithmetic_passes_a_keep_both_merge(tmp_path):
    """The control, and the half that matters most: a guard that cannot tell
    a good merge from a bad one gets switched off. Both entries survive a
    keep-both resolution, so it must exit 0."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "both")
    result = _run(repo, "base", "one", "two")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Every branch's entries are in the merged tree." in result.stdout


def test_the_arithmetic_says_it_could_not_look_rather_than_all_is_well(
    tmp_path,
):
    """Exit 2, deliberately not 0. `observability_report.py` makes the same
    distinction for the same reason: a check that reports a clean bill of
    health when it could not run is worse than one that is absent, because
    somebody believes it."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "both")

    # The MESSAGE, not only the code. CPython exits 2 for "can't open file",
    # so a test asserting the code alone passes with the script DELETED —
    # measured on the first cut of this file: moving the script aside left
    # this test green while the other five script tests went red. It could
    # not tell "the guard refused because it could not look" from "the guard
    # is not there", which is the same family as the two vacuous assertions
    # this branch already caught in itself.
    bad_ref = _run(repo, "base", "no-such-branch")
    assert bad_ref.returncode == 2, bad_ref.stdout + bad_ref.stderr
    assert "Could not look" in bad_ref.stderr, bad_ref.stderr

    no_args = _run(repo)
    assert no_args.returncode == 2, no_args.stdout + no_args.stderr
    assert "origin/main" in no_args.stderr, no_args.stderr


def test_the_script_and_the_tests_read_the_same_heading_format():
    """Two copies of one rule is this codebase's named recurring bug
    generator. They are deliberately separate files — a test cannot know
    which branches were merged, and a merge-time script cannot run in CI —
    so the one thing they share is asserted instead."""
    # The COMPILED patterns, in both directions. Asserting that the script's
    # source contains a literal is a one-way check: rewriting THIS file's
    # pattern to an equivalent spelling left it green while the two files
    # demonstrably no longer shared one — measured, and exactly the drift the
    # test is named for, invisible to it.
    spec = importlib.util.spec_from_file_location("_merge_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ENTRY_HEADING.pattern == ENTRY_HEADING.pattern, (
        "check_merge_kept_the_log.py and this file no longer read Decision "
        "log headings the same way, so the two can disagree about which "
        "entries a tree has.\n"
        f"  script: {module.ENTRY_HEADING.pattern!r}\n"
        f"  tests:  {ENTRY_HEADING.pattern!r}"
    )
    assert module.ENTRY_HEADING.flags == ENTRY_HEADING.flags, (
        "same pattern, different flags — re.M is what makes ^ mean 'start of "
        "a line', so without it the count is 0 or 1"
    )


def test_the_arithmetic_refuses_to_compare_a_tree_with_itself(tmp_path):
    """The trap this script could most easily fall into, found by writing the
    test above: merge onto `main` rather than onto a branch and `main` moves
    with the merge, so owed and got are read from one tree and every check
    passes whatever was dropped. Exit 2 and say which argument is wrong —
    a guard that answers "all fine" when it could not look is worse than no
    guard, because somebody believes it."""
    repo = _tiny_repo(tmp_path)
    _git(repo, "checkout", "-q", "base")
    for name in ("one", "two"):
        out = subprocess.run(
            ["git", "merge", "--no-edit", name],
            cwd=str(repo), capture_output=True, text=True,
        )
        if out.returncode != 0:
            _git(repo, "checkout", "--ours", LOG_PATH)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", f"merge {name}")
    result = _run(repo, "base", "one", "two")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "the same commit" in result.stderr


def test_the_arithmetic_does_not_cry_wolf_when_a_branch_is_named_twice(
    tmp_path,
):
    """A typo on a four-branch command line used to count that branch's
    entries twice, which on a PERFECTLY GOOD tree read as 'some entry lost its
    body' and exited 1. Measured on the real trees before the fix: naming one
    branch four times on a keep-both merge exited 1 with that message. A guard
    that goes red for the wrong reason is one somebody switches off, which is
    worse than not having it."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "both")
    doubled = _run(repo, "base", "one", "two", "one", "two")
    assert doubled.returncode == 0, doubled.stdout + doubled.stderr
    # ...and it is a dedupe, not a loosened threshold: the bad merge still fails
    second = tmp_path / "again"
    second.mkdir()
    bad = _tiny_repo(second)
    _merge(bad, "ours")
    assert _run(bad, "base", "one", "two", "one").returncode == 1


# --- 8. the round an adversarial review put this script through -------------
#
# Every test below reproduces something that was WRONG in the first version.
# Four of them are the same defect wearing different hats: the script compared
# TOTALS (base's entry count plus what each branch added, against the merged
# tree's count) rather than identities, so any entry gained from anywhere else
# cancelled, one for one, an entry the merge had dropped.


def _entry(date, name, body=3):
    return f"- **{date} — {name}.**\n" + f"  Body of {name}.\n" * body


def _prepend(repo, text):
    p = repo / LOG_PATH
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "## Decision log\n\n", f"## Decision log\n\n{text}\n", 1
        ),
        encoding="utf-8",
    )


def test_an_entry_gained_from_elsewhere_cannot_cancel_one_the_merge_dropped(
    tmp_path,
):
    """THE BLOCKER, and the reason this script compares headings rather than
    counts.

    Main moving under a long overnight run is not a corner case — it is the
    ordinary case, and it happened twice to this very branch while it was
    being reviewed. Land two other sessions' entries on the tree, then merge a
    branch with `--ours`: the branch's entry is definitively gone, and the old
    arithmetic said `owed 3, got 4` and printed "Every branch's entries are in
    the merged tree." That is the exact sentence this file exists to stop.

    Reproduced on the real repo too, with all four of the night's branches
    lost and four other-session entries standing in for them: exit 0."""
    repo = _tiny_repo(tmp_path)
    _git(repo, "checkout", "-q", "-B", "tree", "base")
    _prepend(repo, _entry("2026-09-25", "Another session A"))
    _prepend(repo, _entry("2026-09-26", "Another session B"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two other sessions")

    out = subprocess.run(
        ["git", "merge", "--no-edit", "one"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert out.returncode != 0, "the fixture needs a real conflict"
    _git(repo, "checkout", "--ours", LOG_PATH)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "merge one --ours")

    # the truth the guard has to reach
    assert "Branch one." not in (repo / LOG_PATH).read_text(encoding="utf-8")

    result = _run(repo, "base", "one")
    assert result.returncode == 1, (
        "the tree is LONGER than base and has MORE entries, and one branch's "
        "entry is gone:\n" + result.stdout + result.stderr
    )
    assert "Branch one." in result.stdout, (
        "it has to NAME what went, or the operator cannot tell this from a "
        "deliberate removal:\n" + result.stdout
    )


def test_a_stacked_branch_is_not_reported_as_a_double_loss(tmp_path):
    """This repo stacks branches routinely — the Decision log is full of "on
    top of X", "stacked on Y". A stacked branch carries its parent's entry, so
    counting what each branch adds relative to ITS OWN merge-base counted that
    shared entry twice and exited 1 over a perfectly good merge. A guard that
    cries wolf is one somebody switches off."""
    repo = _tiny_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "stacked", "one")
    _prepend(repo, _entry("2026-04-01", "Stacked on one"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stacked")

    _git(repo, "checkout", "-q", "-B", "tree", "base")
    for name in ("one", "stacked"):
        out = subprocess.run(
            ["git", "merge", "--no-edit", name],
            cwd=str(repo), capture_output=True, text=True,
        )
        if out.returncode != 0:
            _keep_both(repo)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", f"merge {name}")

    result = _run(repo, "base", "one", "stacked")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_branch_that_rewords_existing_lines_is_not_reported_as_a_loss(
    tmp_path,
):
    """`--numstat` insertions ALONE overstate what a branch owes, because this
    log's stated practice is to correct an entry IN PLACE — which git reports
    as an insertion and a deletion. A branch that rewords eight lines while
    adding an entry looked like it owed eight lines nobody promised, and a
    good keep-both merge then read as "some entry lost its body".

    Not hypothetical: this branch itself is an insert-and-delete on
    CLAUDE.md."""
    repo = _tiny_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "reword", "base")
    p = repo / LOG_PATH
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "  Its body.", "  Its CORRECTED body."
        ),
        encoding="utf-8",
    )
    _prepend(repo, _entry("2026-05-01", "Reworded", body=8))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "reword")

    _git(repo, "checkout", "-q", "-B", "tree", "base")
    out = subprocess.run(
        ["git", "merge", "--no-edit", "reword"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if out.returncode != 0:
        _keep_both(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "merge reword")

    result = _run(repo, "base", "reword")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_branch_left_off_the_command_line_is_could_not_look(tmp_path):
    """The script only knows about the branches it is given, so forgetting one
    on a four-branch command line used to be exit 0 over a tree that had
    dropped every entry that branch brought. Nothing else here can see it: a
    branch nobody names is a branch whose entries nobody is owed.

    Exit 2 rather than 1 — "I cannot answer the question you asked" is not the
    same as "something was dropped"."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "ours")
    result = _run(repo, "base", "one")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no branch on the command line explains" in result.stderr

    # ...and listing both really does reach the entries that went
    full = _run(repo, "base", "one", "two")
    assert full.returncode == 1, full.stdout + full.stderr


def test_the_arithmetic_catches_a_body_that_vanished_under_its_heading(
    tmp_path,
):
    """The line half of the script, which five separate mutations of used to
    leave every test green — so `LINE_SLACK_PER_BRANCH`, the `--numstat` read
    and the "lost its body" branch were all free to break.

    A resolution can keep every heading and still eat the prose under it,
    which is most of what a Decision log entry IS."""
    repo = _tiny_repo(tmp_path)
    _merge(repo, "both")
    p = repo / LOG_PATH
    kept = [
        line
        for line in p.read_text(encoding="utf-8").split("\n")
        if not line.startswith(("  Body of one.", "  Body of two."))
    ]
    p.write_text("\n".join(kept), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "lose a body")

    result = _run(repo, "base", "one", "two")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "lost its body" in result.stdout, result.stdout
    assert "Every entry heading is present" in result.stdout, result.stdout


def test_an_indented_sub_bullet_is_not_an_entry_and_cannot_be_told_apart():
    """A limit, pinned so nobody "fixes" it into a red suite.

    A review proposed allowing leading whitespace in LOOSE_HEADING, so that a
    markdown reformat indenting real entries would be caught. Run against the
    real file it goes red on a legitimate SUB-bullet — the 2026-09-21 evening
    cook nudge, nested under the Morning text item in Current state — which is
    deliberately not a Decision log entry. Indentation therefore cannot be the
    drift signal, and a reformat that indents real entries is a shape neither
    the tests nor the script can see."""
    indented = re.compile(r"^[ \t]+- \*\*(20\d\d-\d\d-\d\d) — ", re.M)
    # The sub-bullet sits in CLAUDE.md's Current state, which stayed in
    # CLAUDE.md when the log moved out (2026-10-05).
    found = indented.findall(CLAUDE_MD)
    assert found, (
        "the sub-bullet this limit is documented against is gone from "
        "CLAUDE.md. If sub-bullets in that shape are no longer written, "
        "LOOSE_HEADING can be widened to ^[ \\t]*- \\*\\* and this test "
        "deleted — check the whole file first."
    )
    assert not ENTRY_HEADING.search("  - **2026-09-21 — Nested.**"), (
        "the strict pattern now matches an indented line, so a nested "
        "sub-bullet is being counted as a Decision log entry"
    )


MAX_FLOOR_SLACK = 5000


def test_the_floors_have_not_rotted_into_uselessness():
    """A floor is only worth what `current - floor` is, and this file grows.

    Measured from git history on 2026-09-27: CLAUDE.md went 13681 lines on
    2026-09-20 to 19030 on 2026-09-27 — about 764 lines a day. An absolute
    floor therefore loosens by that much every day, and the precedent this one
    is modelled on is the proof that "raise it when you notice" does not
    happen by itself: tests/test_frontend_restored_2026_09_08.py asserts
    static/shell.js is over 8600 lines, that file is now about 24000, and the
    floor has been raised once in three weeks — it would no longer notice
    two thirds of the file being deleted.

    So this fails when the slack gets wide enough to be worth nothing, and
    says what to do. Raising the floor is the fix; deleting this test is not.
    """
    lines = LOG_MD.count("\n") + 1
    slack = lines - LINE_FLOOR
    assert slack < MAX_FLOOR_SLACK, (
        f"docs/DECISION_LOG.md is {lines} lines and LINE_FLOOR is {LINE_FLOOR}, so the "
        f"floor now only catches a loss bigger than {slack} lines — the "
        "2026-09-26 incident lost about a thousand. Raise LINE_FLOOR (and "
        "ENTRY_FLOOR, and NEWEST_ENTRY_ON_OR_AFTER) to just under the "
        "current values and say in the commit why."
    )


def test_running_it_mid_conflict_says_so_rather_than_blaming_the_merge(
    tmp_path,
):
    """Run it with a merge still unresolved and HEAD is the PRE-merge commit,
    so every branch looks dropped — and the message would tell you to redo a
    resolution you have not made yet. Exit 2 with the real reason, which for
    this script is two lines and the difference between a guard that helps
    and one that sends you the wrong way."""
    repo = _tiny_repo(tmp_path)
    _git(repo, "checkout", "-q", "-B", "tree", "base")
    _prepend(repo, _entry("2026-09-26", "Another session"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "other session")
    out = subprocess.run(
        ["git", "merge", "--no-edit", "one"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert out.returncode != 0, "the fixture needs a real conflict"

    result = _run(repo, "base", "one")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "merge in progress" in result.stderr, result.stderr
