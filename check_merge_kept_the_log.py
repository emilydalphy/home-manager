#!/usr/bin/env python3
"""Did this merge keep every branch's Decision log entries?

WHY THIS EXISTS. On 2026-09-26 an overnight merge script resolved CLAUDE.md
conflicts with `git checkout --ours` before its keep-both resolver. `--ours`
clears the conflict markers, so the resolver's `git diff --diff-filter=U`
found nothing to do and never ran. About a thousand lines of Decision log
went missing across nine merges and the suite stayed fully green. It was
found by ARITHMETIC: the ten branches added +1328 lines between them and the
merged tree showed +292.

`tests/test_claude_md_tripwire.py` is the guard for everything a test CAN
see — the file shrinking, entries going missing, the newest end of the log
being lost. It cannot see this shape, and that is not a hedge:

    a merge that drops an entry which only ever existed on a BRANCH leaves a
    tree LONGER than main was, carrying every one of main's own entries.

Measured on 2026-09-27, on that night's own four branches: resolving every
CLAUDE.md conflict with `--ours` lost 493 lines and three of the four
branches' entries, and all ten of those tests passed. No higher floor helps;
the merged tree is bigger than the baseline by construction. This script is
what catches it, and it is arithmetic rather than a test because only the
person doing the merge knows which branches went into it.

IT COMPARES IDENTITIES, NOT COUNTS, AND THAT IS THE WHOLE CORRECTNESS OF IT.
The first version of this script compared totals — base's entry count plus
what each branch added, against the merged tree's count. An adversarial
review broke it in the one direction that matters: main gaining an entry
from another session CANCELS, one for one, an entry the merge dropped.
Reproduced on the real repo — four other-session entries land on main, the
merge then loses all four of the night's branch entries with `--ours`, and
the old script printed "Every branch's entries are in the merged tree" and
exited 0. That is the exact sentence this file exists to stop. So every
heading LINE is carried through and the comparison is a multiset difference:
the output names what is missing rather than counting it, which is also what
makes a stacked branch (this repo stacks routinely) and a duplicate heading
on main behave correctly for free.

WHAT IT CANNOT DO. It only knows about the branches you name. A branch left
off the command line is a branch whose entries nobody asked about — so the
merge commits in `base..HEAD` are checked against the list, and anything
merged that no listed branch explains is exit 2 ("could not look"), never
exit 0.

USAGE, from the merged tree, before you push it. The first argument is THE
COMMIT THE MERGE STARTED FROM — usually origin/main, or whatever main was
before you merged onto it, never the merged tree itself:

    python check_merge_kept_the_log.py origin/main branch-a branch-b

Exit 0 = every branch's entries are present. Exit 1 = something is missing;
the output NAMES each heading. Exit 2 = it could not look (bad ref, not a
git tree, a branch left off the list), which is deliberately NOT the same as
"all fine".
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys

LOG_FILE = "CLAUDE.md"

# The Decision log's own heading format, unchanged since the file was
# started. Kept IDENTICAL to ENTRY_HEADING in
# tests/test_claude_md_tripwire.py — if one moves, move the other. There is a
# test comparing the two patterns in both directions; a rewrite that means
# the same thing but is spelled differently still fails it, because the point
# is that one file's idea of a heading cannot drift from the other's.
ENTRY_HEADING = re.compile(r"^- \*\*(20\d\d-\d\d-\d\d) — ", re.M)

# Lines auto-merge: git resolves shared context by itself, so the merged tree
# can be legitimately a line or two short of the naive sum. Entry headings do
# NOT auto-merge away in any way this script has to tolerate, because the
# comparison below is by identity — so the heading check is exact and this
# number only softens the "an entry kept its heading and lost its body" case.
#
# Sized deliberately loose. The 2026-09-26 rebuild was +1319 against +1328
# owed, but almost all of that 9-line gap was DELETIONS (entries corrected in
# place), which `_delta_lines` now subtracts properly; the genuine
# auto-merge-shared-context contribution measured about one line over four
# branches. Three per branch is room to spare for a check whose only job here
# is catching a body that vanished under a heading that survived.
LINE_SLACK_PER_BRANCH = 3


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _log_at(ref: str) -> str:
    return _git("show", f"{ref}:{LOG_FILE}")


def _headings_at(ref: str) -> list[str]:
    """Every Decision log heading at `ref`, as whole LINES.

    Whole lines rather than the dates ENTRY_HEADING captures: a date is not
    an identity (this log has had seven entries on one day), and the point of
    this script is to say WHICH entry went missing.
    """
    text = _log_at(ref)
    out = []
    for m in ENTRY_HEADING.finditer(text):
        end = text.find("\n", m.start())
        out.append(text[m.start(): end if end != -1 else len(text)])
    return out


def _lines_at(ref: str) -> int:
    return _log_at(ref).count("\n") + 1


def _delta_lines(merge_base: str, branch: str) -> int:
    """Net lines this branch adds to the log: insertions MINUS deletions.

    Insertions alone is wrong and was wrong in the first version: this log's
    stated practice is to correct an entry IN PLACE, which git reports as an
    insertion and a deletion, so a branch that rewords eight lines while
    adding an entry looked like it owed eight lines nobody had promised. On a
    good keep-both merge that read as "some entry lost its body" — a red
    answer over a correct tree, which is how a guard gets switched off.
    """
    stat = _git("diff", "--numstat", merge_base, branch, "--", LOG_FILE).split()
    if len(stat) < 2 or not stat[0].isdigit() or not stat[1].isdigit():
        return 0
    return int(stat[0]) - int(stat[1])


def _is_ancestor(a: str, b: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", a, b], capture_output=True
        ).returncode
        == 0
    )


def _lines_measured_from(base: str, branch: str, branches: list[str]) -> str:
    """Where to measure this branch's line contribution FROM.

    Normally the merge-base with `base`. But this repo stacks branches, and a
    stacked branch carries its parent's lines as well as its own — so summing
    every branch's delta against `base` counts the parent's twice and the
    merged tree reads as "some entry lost its body" over a perfectly good
    merge. The entry comparison dedupes that for free (it is a max-wise union
    of identities); the line arithmetic has to be told.

    Measured from the DEEPEST listed branch that is an ancestor of this one,
    so a two-deep stack contributes each set of lines exactly once.
    """
    ancestors = [
        other
        for other in branches
        if other != branch and _is_ancestor(other, branch)
    ]
    if not ancestors:
        return _git("merge-base", base, branch).strip()
    deepest = [
        a
        for a in ancestors
        if not any(b != a and _is_ancestor(a, b) for b in ancestors)
    ]
    return deepest[0] if deepest else ancestors[0]


def _unexplained_merges(base: str, branches: list[str]) -> list[str]:
    """Commits merged into HEAD since `base` that no listed branch explains.

    Forgetting a branch on the command line is an ordinary four-branch typo,
    and nothing else here can see it: a branch nobody names is a branch whose
    entries nobody is owed, so dropping every one of them exits 0. Each merge
    commit's second-and-later parents are checked for reachability from the
    base or from something in the list.
    """
    known = [base, *branches]
    unexplained = []
    for commit in _git("rev-list", "--merges", f"{base}..HEAD").split():
        # `rev-list --parents -n 1 C` prints "C p1 p2 ...", so [2:] is
        # already the second-and-later parents. Slicing that AGAIN was this
        # function's own first bug: it iterated over nothing, and a tree that
        # had quietly dropped two branches' entries exited 0.
        for parent in _git("rev-list", "--parents", "-n", "1", commit).split()[2:]:
            if any(
                subprocess.run(
                    ["git", "merge-base", "--is-ancestor", parent, ref],
                    capture_output=True,
                ).returncode
                == 0
                for ref in known
            ):
                continue
            unexplained.append(parent[:12])
    return unexplained


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().rsplit("USAGE", 1)[-1], file=sys.stderr)
        return 2
    base = argv[1]
    # Naming a branch twice is an ordinary typo on a four-branch command line.
    # It is harmless to the identity comparison below, but it doubles the line
    # arithmetic, which on a PERFECTLY GOOD tree read as "some entry lost its
    # body" — a red answer for the wrong reason. Measured before the dedupe:
    # naming one branch four times on a keep-both tree exited 1.
    branches: list[str] = []
    for name in argv[2:]:
        if name not in branches:
            branches.append(name)

    # Run mid-conflict and HEAD is still the pre-merge commit, so every branch
    # looks dropped. Say which it is rather than blaming the resolution.
    if _git("rev-parse", "--git-dir").strip():
        import os

        git_dir = _git("rev-parse", "--git-dir").strip()
        if os.path.exists(os.path.join(git_dir, "MERGE_HEAD")):
            print(
                "Could not look: there is a merge in progress. Finish it "
                "(resolve every conflict hunk by hunk, keeping both sides, "
                "then commit) and run this again.",
                file=sys.stderr,
            )
            return 2

    # If <base> has itself advanced to the merge — which is what happens when
    # you merge ONTO main rather than onto a branch or a detached HEAD — then
    # "owed" and "got" are read from the same tree and every check below
    # passes whatever was dropped. Found by writing the test for this script:
    # a guard that quietly answers "all fine" is the exact failure this whole
    # file exists to stop, so it is exit 2 ("could not look"), never exit 0.
    try:
        same = _git("rev-parse", base).strip() == _git("rev-parse", "HEAD").strip()
    except RuntimeError as exc:
        print(f"Could not look: {exc}", file=sys.stderr)
        return 2
    if same:
        print(
            f"Could not look: {base} and HEAD are the same commit, so there "
            "is nothing to compare. Point the first argument at the commit "
            "the merge STARTED from — e.g. origin/main, or the commit main "
            "was at before you merged onto it.",
            file=sys.stderr,
        )
        return 2

    try:
        unexplained = _unexplained_merges(base, branches)
    except RuntimeError as exc:
        print(f"Could not look: {exc}", file=sys.stderr)
        return 2
    if unexplained:
        print(
            "Could not look: this tree merged "
            f"{', '.join(sorted(set(unexplained)))}, which no branch on the "
            "command line explains. List every branch that went into the "
            "merge — one left off is one whose entries nobody checks.",
            file=sys.stderr,
        )
        return 2

    try:
        base_headings = _headings_at(base)
        base_lines = _lines_at(base)
        got = collections.Counter(_headings_at("HEAD"))
        merged_lines = _lines_at("HEAD")
    except RuntimeError as exc:
        print(f"Could not look: {exc}", file=sys.stderr)
        return 2

    print(f"{LOG_FILE} on {base}: {base_lines} lines, {len(base_headings)} entries")

    # A multiset, not a set: origin/main already carries one heading line
    # twice (the 2026-09-11 holidays entry), so a set would quietly forgive
    # losing one of the pair. `|` is Counter's max-wise union, which is what
    # makes a STACKED branch — one cut from another, so it carries the
    # parent's entry too — count that shared entry once rather than twice.
    owed = collections.Counter(base_headings)
    owed_lines = 0
    for branch in branches:
        try:
            merge_base = _git("merge-base", base, branch).strip()
            branch_headings = _headings_at(branch)
            added = collections.Counter(branch_headings) - collections.Counter(
                _headings_at(merge_base)
            )
            lines = _delta_lines(
                _lines_measured_from(base, branch, branches), branch
            )
        except RuntimeError as exc:
            print(f"Could not look at {branch}: {exc}", file=sys.stderr)
            return 2
        owed |= collections.Counter(branch_headings)
        owed_lines += lines
        print(f"  {branch}: {lines:+} lines, +{sum(added.values())} entries")

    missing = owed - got
    n_missing = sum(missing.values())
    print(
        f"\nowed: {base_lines + owed_lines} lines, {sum(owed.values())} entries\n"
        f"got:  {merged_lines} lines, {sum(got.values())} entries"
    )

    if missing:
        print(
            f"\nDROPPED: {n_missing} Decision log "
            f"{'entry' if n_missing == 1 else 'entries'} — every one of these "
            "is on the base or on a branch you named, and is not in the "
            "merged tree:\n"
        )
        for heading in sorted(missing.elements()):
            print(f"   {heading[:96]}")
        print(
            "\nAlmost always this is a merge that resolved CLAUDE.md by "
            "taking one side. Redo it hunk by hunk, keeping both sides — see "
            "the 2026-09-08 entry in the log itself. Do NOT resolve this "
            "file with --ours or --theirs.\n"
            "If instead a branch deliberately REMOVED one of these, that is "
            "worth a second pair of eyes and this is the right place to "
            "notice it."
        )
        return 1

    slack = LINE_SLACK_PER_BRANCH * len(branches)
    if merged_lines < base_lines + owed_lines - slack:
        print(
            f"\nEvery entry heading is present, but the merged file is "
            f"{base_lines + owed_lines - merged_lines} lines short of the sum "
            f"(more than the {slack} git can legitimately auto-merge away). "
            "Some entry lost its body. Check the conflict resolution."
        )
        return 1

    print("\nEvery branch's entries are in the merged tree.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
