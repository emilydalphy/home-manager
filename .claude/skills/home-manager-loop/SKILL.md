---
name: home-manager-loop
description: How the Home Manager project's "loop" works — the Notion Loop Board (ticket backlog), dev environment, ticket workflow, and Emily's role. Load this whenever working on tickets, fixes, or features for the Home Manager app, when the user (Emily) asks about "the loop," "the board," or how this project's workflow is set up, or when she says something like "run the loop," "work through the tickets," or "pick up the next ticket."
---

# The Home Manager Loop

This project uses a "loop engineering" setup (based on Addy Osmani's *Loop Engineering*
article): instead of Emily re-explaining context every session, a Notion board holds the
backlog of work, and this file holds the conventions so any session — hers or an automated
one — can pick up work without rediscovering the basics.

## Emily's role — read this first

Emily is **not a technical person, and is learning as we go**. Keep that true throughout
every interaction this skill covers, not just onboarding:

- **Explain in plain language.** No unexplained jargon. If a technical term is necessary
  (e.g. "regex," "venv," "API route"), define it in a half-sentence the first time it comes
  up in a given conversation.
- **She describes problems, not solutions.** Her job is to say what's wrong or what she
  wants ("this screen is stuck," "I want a reset button") — not to specify file paths,
  function names, or implementation approach. Translating that into a real, well-scoped
  ticket or fix is the assistant's job.
- **She makes the product/design calls; the assistant does not invent them.** Anything that's
  a genuine judgment call (wording, priority, which of several valid designs to build, how
  something should look/feel) gets flagged as an open question for her — never guessed at
  silently, even when the "obvious" answer seems clear.
- **She approves before anything ships.** Code can be written, tested, and committed locally
  as part of working a ticket, but **pushing to GitHub needs her explicit go-ahead** each
  time (per her 2026-08-30 decision — see Automation rules below for the stricter
  unattended-run version of this).
- **Teach along the way.** When something technical comes up (why a bug happened, what a
  piece of the stack does), a short, concrete explanation is welcome — that's part of the
  point of this project for her. Don't over-explain unprompted, but don't skip the "why"
  either.

## The Loop Board (memory)

A Notion database called **"Loop Board"** is the external memory / ticket backlog — it's
what makes this a loop instead of a one-off conversation, since the AI forgets everything
between sessions but the board doesn't.

- URL: https://app.notion.com/p/dab9c22522484cac8c67764b9d4eb8fe
- Data source ID (for Notion MCP tools): `628d4a88-c15e-4a2c-9975-c25a64d28ec5`
- Schema: Task name, Status (Not started / In progress / Done / Archived), Type (Bug /
  Feature / Improvement), Priority (High / Medium / Low), Piece (which loop component, for
  meta-tickets about the loop itself), Assignee, Due, Notes, plus free-form page content for
  the actual writeup.

**When the session is working in the Home Manager Project folder, assume any ticket
described is for this app** — don't ask which app/project.

### Adding a ticket (ticket intake)

When Emily describes a bug, feature, or improvement, create a card rather than just
discussing it in chat. Required: what app (usually inferred, see above) and what's
wrong/wanted. Ask a short follow-up only for whichever of these weren't given — don't demand
all of them, and don't block on optional ones if she brushes past them ("just add it, medium
priority is fine"):

- **Type**: Bug (something's broken/behaving incorrectly) vs. Improvement (works, but could
  be better) vs. Feature (new capability)
- **Priority**: High / Medium / Low
- **Why it matters** (optional but helps whoever builds it)
- **Specifics** (optional): screenshots, exact screen, steps to reproduce

### Working a ticket

1. **Investigate before proposing anything.** Read the actual code, find the real root
   cause, cite file:line. Don't guess at causes.
2. **Separate fact from decision.** If a real product/design decision is needed to finish
   the ticket (wording, exact UX flow, which of several valid approaches), write it as an
   explicit open question in the ticket rather than picking one. If it's a pure technical
   fix with no real judgment call, it's fine to just fix it.
3. **When actually fixing code**: first check `git worktree list` / recent activity for signs
   another session is active in this same project folder (multiple Claude Code sessions on
   this app is normal, not a hypothetical — see Worktrees below). If so, or as a general
   habit for any real code change, use `EnterWorktree` first so this session's edits can't
   collide with another session's — this project hit exactly that collision on 2026-08-30
   (`Repos/.claude/launch.json` got overwritten mid-edit by a concurrent session). Then write
   the fix, run the existing test suite (`.venv/bin/python -m pytest -q`), and verify live in
   the browser when the change is visually/behaviorally observable (see Dev environment
   below). Don't claim something works without having checked, and per "Sub-agents" below,
   have a fresh sub-agent independently verify the fix and test results before calling it
   done.
4. **Clean up after yourself.** The local dev database (`app/home_manager.db`) holds Emily's
   real household data — if you add test data to verify something, remove it afterward
   (direct SQL delete of just what you added; the local dev DB is git-ignored so it's safe to
   touch, but it's still real data, not a throwaway fixture). Never run `reset_household.py`
   non-interactively — it wipes everything and needs a typed "RESET" confirmation for a
   reason.
5. **Commit with a clear message**; don't push without asking (see Automation rules for the
   stricter unattended version).
6. **Update the ticket**: append findings/fix details to the page content, and set Status to
   "In progress" once a fix is written and tested — leave "Done" for Emily to set herself
   after she's reviewed it, don't mark it Done on the assistant's own authority.
7. **Cross-link related tickets** when a new one shares a theme with an existing one (e.g.
   several tone/wording tickets, several onboarding-data tickets) — add a short "related
   tickets" or "broader goal" note on each rather than leaving the connection implicit.

## Dev environment

The app (`app/main.py`, FastAPI + SQLite) needs **Python 3.10+** — this Mac's system
`python3` is only 3.9, and there's no Homebrew or Docker available as a workaround. A proper
environment is already set up:

```
cd "/Users/emilydalphy/Home Manager Project"
.venv/bin/python -m pytest -q                          # run tests
.venv/bin/python -m uvicorn app.main:app --port 8010    # run the server manually
```

(`.venv` was created via `uv` — `uv python install 3.11 && uv venv --python 3.11 .venv` — if
it's ever missing/broken, `uv` itself lives at `~/.local/bin`, no Homebrew needed.)

To preview the app live in the Browser pane, use `preview_start` with the config name
**`"home-manager"`** — its `launch.json` lives at `/Users/emilydalphy/Repos/.claude/launch.json`
(the *primary* working directory's config, not one inside this project folder — the preview
tool only reads the primary directory's launch.json, so don't recreate one here).

## Worktrees (working in parallel without collisions)

Multiple Claude Code sessions can be — and have been — working on this project at the same
time (Emily running more than one session, or a scheduled/automated run happening alongside
a live one). Without isolation, two sessions editing files at once can silently clobber each
other's work, which is exactly what happened on 2026-08-30: one session's edit to
`Repos/.claude/launch.json` was overwritten mid-task by another session.

**Use `EnterWorktree` before making any real code change** (not needed for read-only
investigation, like a "run the loop" pass that only updates a Notion ticket). This project
qualifies as an explicit project instruction to do so, per `EnterWorktree`'s own rule that
it should only be used when the user or project instructions say to. Concretely:

1. Call `EnterWorktree` (a name like the ticket's topic is fine, e.g. `fix-grocery-menu-bug`)
   before editing any file for a real fix.
2. Do the work, test it, commit inside the worktree as normal.
3. When done, use `ExitWorktree` — `action: "keep"` if Emily still needs to review/merge it,
   `"remove"` for a clean throwaway (e.g. an experiment that didn't pan out).
4. Getting the change into `main` still follows the same rule as everything else here:
   Emily approves before anything gets pushed/merged.

Shared config that lives outside any single worktree (like `Repos/.claude/launch.json`,
which isn't part of this project's own git repo) is still a shared-collision risk even with
worktrees — treat edits to it as a quick, careful, one-shot change, not something to leave
half-edited.

## Sub-agents (verifying findings before Emily sees them)

Emily isn't technical and is trusting the loop's conclusions somewhat on faith — so a wrong
root cause or a claimed-but-untested fix is worse here than in a normal dev workflow, since
she has less ability to independently sanity-check it herself. Don't let the same
investigation that produced a conclusion also be the only check on that conclusion.

**Before writing a root-cause conclusion to a ticket, or before calling a code fix
done:** spawn a fresh sub-agent (the `Agent` tool, `subagent_type: "Explore"` for
read-only verification of a root cause, or `general-purpose` if it also needs to re-run
tests) with the specific claim and ask it to independently verify — not "was Claude right"
in the abstract, but a concrete falsifiable check: "confirm this exact file:line causes this
exact symptom" or "run the test suite and confirm it actually passes." A fresh sub-agent
has no attachment to the original theory being right, which is the point — it's the same
reason the loop-engineering article calls this out as a separate piece, not just "be more
careful."

This adds a step, not a replacement, to the existing workflow above — still do the real
investigation and testing yourself first; the sub-agent is a second check, not a shortcut
past doing the work.

## Plugins/Connectors

Already covered for this project — the Notion connection *is* the plugin/connector that
makes the Loop Board work. No separate piece needed here unless a new external tool (e.g.
GitHub issue sync) comes up later.

## Roadmap (current priority)

**Meal planning** is the current focus — nail down its features and experience before moving
on. **Chores** is the defined next major module after that (it has a working backend already,
just no first-class screen yet). Don't jump ahead to Chores work unless Emily says meal
planning has wrapped up.

## Running the loop

When Emily says something like "run the loop," "work through the tickets," or "pick up the
next ticket," this is a manual trigger — do the following, and **stop there** rather than
continuing on to a fix without her:

1. Query the Loop Board for cards with Status = "Not started." Check the "Work Tonight"
   checkbox property first (added 2026-08-30) — any card Emily checked the day before goes
   ahead of normal priority order.
2. Pick one: a "Work Tonight"-checked card first if any exist, otherwise the
   highest-priority one (High > Medium > Low) — unless she named a specific ticket. If
   several tie, pick the oldest/least-recently-touched one. Uncheck "Work Tonight" once
   that card's been handled, so it doesn't linger as still-queued.
3. Investigate it per the "Working a ticket" steps 1-2 above: read the real code, find the
   actual root cause or the real current state, cite file:line. Do not write or change any
   code in this pass, even if the fix looks obvious.
4. Before writing up the conclusion, verify it with a fresh sub-agent per "Sub-agents"
   below — don't skip this because the finding feels obvious.
5. Update the ticket's page content with what was found — root cause, relevant code, and
   either a concrete fix direction or an explicit open question if it's a real decision.
6. Report back to Emily in plain language: what ticket, what was found, and what (if
   anything) needs her input next.

This is deliberately a stop-and-report pattern, not a chain through the whole board
unprompted — she stays in the loop about what's happening rather than finding a pile of
changes after the fact. If she wants an actual fix built after seeing the findings, that's
a separate, explicit next step (like the "fix the high priority tickets" request on
2026-08-30) — don't treat "run the loop" itself as authorization to also implement.

## Automation rules (unattended/scheduled runs only)

If this project is ever worked by a **scheduled or unattended** run (not a live conversation
with Emily) — per her 2026-08-30 decision, that run must **only investigate tickets and
update them with findings on the Notion board. No code changes, no commits, no pushes.**
Leave implementation for a live session where she's present to make the calls this skill
says are hers to make. This restriction is specifically about *unattended* runs — a live
conversation with Emily can write and commit code as described above.

**The actual overnight routine** ("Home Manager Loop - overnight," runs nightly at 2am
America/Toronto, created 2026-08-30 via `RemoteTrigger`/the `schedule` skill) is a cloud
agent, not a process on Emily's Mac — it clones `https://github.com/emilydalphy/home-manager`
fresh each run and has Notion MCP access attached directly, so it doesn't depend on this
machine being on. It works through **up to 5 cards a night**: any "Work Tonight"-checked
cards first (in priority order), then it fills remaining slots by priority among the rest,
skipping anything already carrying a "## Investigated overnight" (or equivalent) section so
it doesn't redo work. Its `allowed_tools` are `Bash`/`Read`/`Glob`/`Grep` only — Write/Edit
are structurally absent, not just disallowed by instruction, so it cannot modify repo files
even if it tried. Manage it (pause, change time/count, check recent runs) via `RemoteTrigger`
using its id `trig_017u9zjSpCY18QezqHpi1Lma`, or point Emily to
https://claude.ai/code/routines.
