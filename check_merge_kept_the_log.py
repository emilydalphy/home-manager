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

USAGE, from the merged tree, before you push it:

    python check_merge_kept_the_log.py main branch-a branch-b branch-c

Exit 0 = every branch's entries are present. Exit 1 = something was dropped;
the output names how many entries are missing. Exit 2 = it could not look
(bad ref, not a git tree), which is deliberately NOT the same as "all fine".
"""

from __future__ import annotations

import re
import subprocess
import sys

LOG_FILE = "CLAUDE.md"

# The Decision log's own heading format, unchanged since the file was
# started. Kept identical to ENTRY_HEADING in tests/test_claude_md_tripwire.py
# — if one moves, move the other.
ENTRY_HEADING = re.compile(r"^- \*\*(20\d\d-\d\d-\d\d) — ", re.M)

# Lines auto-merge: git resolves shared context by itself, so the merged tree
# is legitimately a few lines short of the naive sum (the 2026-09-26 rebuild
# was +1319 against +1328 owed, a 9-line gap over ten branches). ENTRIES do
# not — each heading is its own line and no two are alike — so the entry
# count is the exact check and the line count is the soft one.
LINE_SLACK_PER_BRANCH = 3


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def _entries_at(ref: str) -> list[str]:
    return ENTRY_HEADING.findall(_git("show", f"{ref}:{LOG_FILE}"))


def _lines_at(ref: str) -> int:
    return _git("show", f"{ref}:{LOG_FILE}").count("\n") + 1


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().rsplit("USAGE", 1)[-1], file=sys.stderr)
        return 2
    base = argv[1]
    # Naming a branch twice is an ordinary typo on a four-branch command line,
    # and left alone it counts that branch's entries twice — which on a
    # PERFECTLY GOOD tree reads as "some entry lost its body" and is a red
    # test for the wrong reason. Measured before the dedupe: naming one branch
    # four times on a keep-both tree exited 1. A guard that cries wolf is one
    # somebody switches off, so the list is de-duplicated, in the order given.
    branches: list[str] = []
    for name in argv[2:]:
        if name not in branches:
            branches.append(name)

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
        base_entries = set(_entries_at(base))
        base_all = _entries_at(base)
        base_lines = _lines_at(base)
        merged_all = _entries_at("HEAD")
        merged_lines = _lines_at("HEAD")
    except RuntimeError as exc:
        print(f"Could not look: {exc}", file=sys.stderr)
        return 2

    print(f"{LOG_FILE} on {base}: {base_lines} lines, {len(base_all)} entries")

    owed_lines = 0
    owed_entries = 0
    for branch in branches:
        try:
            added = _entries_at(branch)
            merge_base = _git("merge-base", base, branch).strip()
            branch_base = _entries_at(merge_base)
            new = len(added) - len(branch_base)
            stat = _git(
                "diff", "--numstat", merge_base, branch, "--", LOG_FILE
            ).split()
            lines = int(stat[0]) if stat and stat[0].isdigit() else 0
        except RuntimeError as exc:
            print(f"Could not look at {branch}: {exc}", file=sys.stderr)
            return 2
        owed_lines += lines
        owed_entries += max(new, 0)
        print(f"  {branch}: +{lines} lines, +{max(new, 0)} entries")

    want_entries = len(base_all) + owed_entries
    got_entries = len(merged_all)
    print(
        f"\nowed: {base_lines + owed_lines} lines, {want_entries} entries\n"
        f"got:  {merged_lines} lines, {got_entries} entries"
    )

    if got_entries < want_entries:
        missing = want_entries - got_entries
        print(
            f"\nDROPPED: {missing} Decision log "
            f"{'entry' if missing == 1 else 'entries'} and "
            f"{base_lines + owed_lines - merged_lines} lines.\n"
            "A merge resolved CLAUDE.md by taking one side. Redo it hunk by "
            "hunk, keeping both sides — see the 2026-09-08 entry in the log "
            "itself. Do NOT resolve this file with --ours or --theirs."
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
