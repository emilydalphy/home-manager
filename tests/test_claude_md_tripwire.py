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

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLAUDE_MD = (REPO / "CLAUDE.md").read_text(encoding="utf-8")

# A Decision log entry heading. The log's own format, used unchanged since
# the file was started: a top-level bullet opening with a bold ISO date and
# an em dash. Anything indented under an entry is that entry's body, so the
# `^` anchor is what makes this a count of ENTRIES rather than of bullets.
ENTRY_HEADING = re.compile(r"^- \*\*(20\d\d-\d\d-\d\d) — ", re.M)

# --- the floors ----------------------------------------------------------
# All three were taken on origin/main at ee741f8 (2026-09-27): 19030 lines,
# 219 entries, newest entry 2026-09-26. Raise them together when the log has
# grown well past them; only lower one for a deletion you can point at.

LINE_FLOOR = 18900
ENTRY_FLOOR = 215
NEWEST_ENTRY_ON_OR_AFTER = "2026-09-26"


def _entry_dates() -> list[str]:
    return ENTRY_HEADING.findall(CLAUDE_MD)


# --- 1. the file as a whole ----------------------------------------------

def test_claude_md_did_not_suddenly_get_shorter():
    """A blunt size floor, shell.js's own guard one file over.

    A conflict resolved one-side-wholesale is always a large sudden shrink,
    and this catches the shape of it even for work that has no named anchor
    below. Raise the floor when the file grows well past it; only lower it
    for a deletion you can point at, and say which one in the same commit."""
    lines = CLAUDE_MD.count("\n") + 1
    assert lines > LINE_FLOOR, (
        f"CLAUDE.md is {lines} lines — suspiciously short (floor {LINE_FLOOR}, "
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
        f"CLAUDE.md's Decision log has {n} dated entries — fewer than the "
        f"floor of {ENTRY_FLOOR} (set from 219 on origin/main at ee741f8). "
        "Entries are never deleted here, only corrected in place, so this is "
        "a merge or an edit that dropped history. Do not lower the floor to "
        "make this pass."
    )


# Deliberately looser than ENTRY_HEADING: a bullet that opens with something
# date-shaped is meant to be an entry, however it is punctuated.
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
    strict = {m.start() for m in ENTRY_HEADING.finditer(CLAUDE_MD)}
    missed = [
        CLAUDE_MD[m.start() : CLAUDE_MD.find("\n", m.start())][:90]
        for m in LOOSE_HEADING.finditer(CLAUDE_MD)
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
    assert dates, "CLAUDE.md has no Decision log entries at all"
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
    missing = [anchor for anchor in NEWEST_ANCHORS if anchor not in CLAUDE_MD]
    assert not missing, (
        "these Decision log entries are gone from CLAUDE.md:\n  "
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
    r'^CLAUDE_MD\s*=.*"CLAUDE\.md".*read_text', re.M
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
            f"tests/{name} no longer reads CLAUDE.md. It is one of the "
            "tripwires on that file; if its anchor was genuinely superseded, "
            "move the anchor rather than dropping the reader."
        )


def test_the_two_older_readers_still_assert_on_what_they_read():
    """A file can read CLAUDE.md and assert nothing about it. These two are
    the repo's original anchors and their assertions are the thing worth
    keeping."""
    for name, anchor in (
        ("test_cook_shelf.py", "2026-09-13 — Cook's root is the shelf"),
        ("test_today_shop_cook.py", "2026-09-13 — Now is one strip down the day"),
        (
            "test_today_shop_cook.py",
            "2026-09-17 — Today: Shop and Cook, tagged by part of the day",
        ),
    ):
        source = (REPO / "tests" / name).read_text(encoding="utf-8")
        assert anchor in source, (
            f"tests/{name} no longer asserts {anchor!r} against CLAUDE.md"
        )
        assert anchor in CLAUDE_MD, (
            f"{anchor!r} is gone from CLAUDE.md — tests/{name} should be red "
            "too; if it is not, its assertion has been weakened"
        )


def test_the_sweep_that_missed_them_still_misses_them():
    """The lesson made executable. This is the pattern the 2026-09-26 session
    used to conclude that nothing read CLAUDE.md, and it is still wrong — kept
    here so the next person reaching for it sees, in one run, that it answers
    empty over files that demonstrably do read the file."""
    wrong = re.compile(r"(open|read_text|Path)\([^)]*CLAUDE")
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
    lines = CLAUDE_MD.count("\n") + 1
    entries = len(_entry_dates())

    # The mangled tree of 2026-09-26: main, plus 292 of the 1328 lines its
    # branches were owed, and one of the ten branch entries surviving.
    mangled = CLAUDE_MD + "\n" + "\n".join(
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

def test_the_hunk_by_hunk_rule_names_this_file_too():
    """The shell.js entry states the rule for shell.js alone. 2026-09-26 shows
    the accident is not specific to one file — it is specific to resolving a
    conflict with --ours/--theirs inside a script that then believes it did
    something else — so the rule has to name CLAUDE.md as well, or the next
    session reads a rule that does not cover the file it is about to mangle."""
    assert "`static/shell.js` AND in `CLAUDE.md`" in CLAUDE_MD, (
        "the hunk-by-hunk merge rule no longer names CLAUDE.md alongside "
        "static/shell.js — see the 2026-09-08 entry"
    )
    assert "tests/test_claude_md_tripwire.py" in CLAUDE_MD, (
        "CLAUDE.md no longer names this file as the tripwire, so a session "
        "reading the rule has no way to find the guard it depends on"
    )
    assert "check_merge_kept_the_log.py" in CLAUDE_MD, (
        "CLAUDE.md no longer names the merge-arithmetic check. It is the only "
        "thing that catches a merge dropping a BRANCH's own entry — measured, "
        "every test in this file passes that shape — so a rule that does not "
        "name it sends the next session to a guard that cannot see the bug."
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
    _git(repo, "init", "-q", "-b", "base")
    _git(repo, "config", "user.email", "x@y")
    _git(repo, "config", "user.name", "x")
    log = "## Decision log\n\n- **2026-01-01 — The first thing.**\n  Its body.\n"
    (repo / "CLAUDE.md").write_text(log, encoding="utf-8")
    _git(repo, "add", "CLAUDE.md")
    _git(repo, "commit", "-qm", "base")
    for name, date in (("one", "2026-02-01"), ("two", "2026-03-01")):
        _git(repo, "checkout", "-q", "-b", name, "base")
        (repo / "CLAUDE.md").write_text(
            log.replace(
                "## Decision log\n\n",
                f"## Decision log\n\n- **{date} — Branch {name}.**\n"
                + f"  Body of {name}.\n" * 5
                + "\n",
            ),
            encoding="utf-8",
        )
        _git(repo, "add", "CLAUDE.md")
        _git(repo, "commit", "-qm", name)
    _git(repo, "checkout", "-q", "base")
    return repo


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
            _git(repo, "checkout", "--ours", "CLAUDE.md")
        else:  # keep both sides, which is the rule
            text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
            (repo / "CLAUDE.md").write_text(
                "\n".join(
                    line
                    for line in text.split("\n")
                    if not line.startswith(("<<<<<<< ", ">>>>>>> "))
                    and line != "======="
                ),
                encoding="utf-8",
            )
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
    assert _run(repo, "base", "no-such-branch").returncode == 2
    assert _run(repo).returncode == 2


def test_the_script_and_the_tests_read_the_same_heading_format():
    """Two copies of one rule is this codebase's named recurring bug
    generator. They are deliberately separate files — a test cannot know
    which branches were merged, and a merge-time script cannot run in CI —
    so the one thing they share is asserted instead."""
    script = SCRIPT.read_text(encoding="utf-8")
    assert r'ENTRY_HEADING = re.compile(r"^- \*\*(20\d\d-\d\d-\d\d) — ", re.M)' in script, (
        "check_merge_kept_the_log.py no longer reads Decision log headings the "
        "same way this file does, so the two can disagree about how many "
        "entries a tree has"
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
            _git(repo, "checkout", "--ours", "CLAUDE.md")
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
