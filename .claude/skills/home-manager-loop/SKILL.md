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
  as part of working a ticket — but **always on its own branch, never on `main`** (her
  2026-09-01 decision, applying to every session type; see "Working a ticket" step 3) — and
  **pushing to GitHub needs her explicit go-ahead** each time (per her 2026-08-30 decision —
  see Automation rules below for the stricter unattended-run version of this). Her approval
  to merge *is* the approval: once something is on `main`, close its ticket rather than
  asking her to confirm the same decision twice (see "Working a ticket" step 6).
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

**Any UI ticket requires reading `DESIGN_SYSTEM.md` first** — tokens, hard
rules, components, nav rules, voice, and who's allowed to change what all
live there; don't invent visual decisions it already answers.

1. **Investigate before proposing anything.** Read the actual code, find the real root
   cause, cite file:line. Don't guess at causes.
2. **Separate fact from decision.** If a real product/design decision is needed to finish
   the ticket (wording, exact UX flow, which of several valid approaches), write it as an
   explicit open question in the ticket rather than picking one. If it's a pure technical
   fix with no real judgment call, it's fine to just fix it.
3. **When actually fixing code — always on a branch, never on `main`.** Per Emily's
   2026-09-01 decision, **every** code change is built on its own branch, in every kind of
   session: a live conversation with her, an unattended overnight run, all of it. Nothing
   gets written or committed directly on `main`, ever. `main` changes only when Emily merges
   it, which is the same approval gate as everything else here — this just makes it
   structural instead of something a session has to remember not to do. Concretely: branch
   first (`git checkout -b <ticket-topic-slug>`, e.g. `fix-grocery-menu-bug`), then work.

   Also check `git worktree list` / recent activity for signs another session is active in
   this same project folder (multiple Claude Code sessions on this app is normal, not a
   hypothetical — see Worktrees below). If so, or as a general habit for any real code
   change, use `EnterWorktree` — which gives the branch *and* its own working directory, so
   this session's edits can't collide with another session's. This project hit exactly that
   collision on 2026-08-30 (`Repos/.claude/launch.json` got overwritten mid-edit by a
   concurrent session).

   Then write the fix, run the existing test suite (`.venv/bin/python -m pytest -q`), and
   verify live in the browser when the change is visually/behaviorally observable (see Dev
   environment below). Don't claim something works without having checked, and per
   "Sub-agents" below, have a fresh sub-agent independently verify the fix and test results
   before calling it done.
4. **Clean up after yourself.** The local dev database (`app/home_manager.db`) holds Emily's
   real household data — if you add test data to verify something, remove it afterward
   (direct SQL delete of just what you added; the local dev DB is git-ignored so it's safe to
   touch, but it's still real data, not a throwaway fixture). Never run `reset_household.py`
   non-interactively — it wipes everything and needs a typed "RESET" confirmation for a
   reason.
5. **Commit with a clear message — to the branch from step 3, never to `main`.** Don't push
   without asking, and don't merge at all: merging is Emily's, every time (see Automation
   rules for the stricter unattended version). If a change somehow got made on `main` by
   mistake, move it onto a branch before committing rather than committing it there.
6. **Update the ticket**: append findings/fix details to the page content, and set Status by
   where the work has actually got to (Emily's call, 2026-08-31):
   - **Done** — the fix is merged to `main`. Merging is already Emily's own decision, so by
     the time code is on `main` she has approved it; making her then go and tick a second box
     is bookkeeping, not a check. Mark it Done yourself in the same pass as the write-up.
   - **In progress** — written and tested but sitting on a branch or in an open PR, i.e.
     anything an unattended run produced (which never merges — see Automation rules), or a
     live session's branch Emily hasn't merged yet.
   - Anything genuinely still open, or investigate-only, stays as it was.
   Don't mark a *related* ticket Done because the work brushed against it. If a ticket is
   only advanced rather than closed, leave its status alone and add a "Related" note (step 7).
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

A branch is the floor here, not the ceiling: every code change goes on one ("Working a
ticket" step 3), but a branch alone still means two sessions sharing one working directory,
which is what actually got clobbered on 2026-08-30. A worktree gives each session its own
copy of the files as well as its own branch.

**Use `EnterWorktree` before making any real code change** (not needed for read-only
investigation, like a "run the loop" pass that only updates a Notion ticket). This project
qualifies as an explicit project instruction to do so, per `EnterWorktree`'s own rule that
it should only be used when the user or project instructions say to. Concretely:

1. Call `EnterWorktree` (a name like the ticket's topic is fine, e.g. `fix-grocery-menu-bug`)
   before editing any file for a real fix. It creates the branch too, so this satisfies the
   never-work-on-`main` rule as well as the isolation one — if you're working without a
   worktree for some reason, you still branch first.
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

A full product roadmap was set with Emily on 2026-09-01 (artifact:
https://claude.ai/code/artifact/712fb549-1355-42cc-88b7-7bf25e7128a3). The Loop Board now
has a **Phase** select property mirroring it — use it when picking work:

- **Phase 0 — Beta-ready** is the active focus: core-loop bugs, the tools.py split →
  multi-household chain, API cost, and the beta-safety pass. This is what unblocks the
  friend beta (which ships on the PWA, not the App Store).
- **Phase 1 — Beta** (first-impression polish + the brand/redesign program) and
  **Phase 2 — Chores** run next; per Emily's explicit call, Chores scoping/building may
  proceed **in parallel with** the beta — the old "don't touch Chores until meal planning
  wraps" rule is superseded. The beta tester's app stays meals-only until Chores is
  validated.
- **Phase 3 — App Store** and **Phase 4 — Flywheel** are later; **Parked** tickets are
  deliberately out of rotation — don't pick them up without Emily asking.

Two standing product decisions (2026-09-01): the core job is meals **on the table** (plan →
grocery list → prep → cook — all core), and **inventory is deferred as policy** — it stays
lightweight background, gets no new investment, and no user should ever have to do
inventory work to complete the core loop.

## Running the loop

When Emily says something like "run the loop," "work through the tickets," or "pick up the
next ticket," this is a manual trigger — do the following, and **stop there** rather than
continuing on to a fix without her:

1. Query the Loop Board for cards with Status = "Not started." Check the "Work Tonight"
   checkbox property first (added 2026-08-30) — any card Emily checked the day before goes
   ahead of normal priority order.
2. Pick one: a "Work Tonight"-checked card first if any exist, otherwise the
   highest-priority one (High > Medium > Low), preferring an earlier-Phase card when
   priorities tie (Phase 0 before Phase 1, etc.; never auto-pick a "Parked" card) — unless
   she named a specific ticket. If still tied, pick the oldest/least-recently-touched one. Uncheck "Work Tonight" once
   that card's been handled, so it doesn't linger as still-queued.
3. Investigate it per the "Working a ticket" steps 1-2 above: read the real code, find the
   actual root cause or the real current state, cite file:line. Do not write or change any
   code in this pass, even if the fix looks obvious.
4. Before writing up the conclusion, verify it with a fresh sub-agent per "Sub-agents"
   below — don't skip this because the finding feels obvious.
5. Update the ticket's page content with what was found — root cause, relevant code, and
   either a concrete fix direction or an explicit open question if it's a real decision. If
   it's a real open question, also check "Needs Your Call" (added 2026-08-31) so it surfaces
   consistently whether this ticket was worked live or overnight.
6. Report back to Emily in plain language: what ticket, what was found, and what (if
   anything) needs her input next.

This is deliberately a stop-and-report pattern, not a chain through the whole board
unprompted — she stays in the loop about what's happening rather than finding a pile of
changes after the fact. If she wants an actual fix built after seeing the findings, that's
a separate, explicit next step (like the "fix the high priority tickets" request on
2026-08-30) — don't treat "run the loop" itself as authorization to also implement.

## Automation rules (unattended/scheduled runs only)

Per Emily's 2026-08-30 decision, a **scheduled or unattended** run (not a live conversation
with her) started out investigate-only: findings written to Notion, no code touched. On
**2026-08-31**, after seeing that first night's investigations hold up, she asked for the
next step: the overnight routine may now also **write and test small fixes**, but on an
isolated side branch it creates itself, and it must never touch `main` directly, never
commit or push to `main`, and never merge — that stays Emily's call every time, exactly like
a live session's push/merge approval above. **As of 2026-09-01 the branch part is no longer
what separates the two** — every session type builds on its own branch and leaves `main`
alone ("Working a ticket" step 3). What still differs is only how the human decision gets
made: a live session works *with* Emily in the room, so it can ask her directly; an
unattended run has nobody to ask, so the branch + "Ready to merge" report is how it gets a
human decision without being able to wait for one mid-run.

**The actual overnight routine** ("Home Manager Loop - overnight," runs nightly at 2am
America/Toronto, created 2026-08-30 via `RemoteTrigger`/the `schedule` skill) is a cloud
agent, not a process on Emily's Mac — it clones `https://github.com/emilydalphy/home-manager`
fresh each run (starting detached from `main`'s current commit; it runs `git checkout main`
first) and has Notion MCP access attached directly, so it doesn't depend on this machine
being on. As of 2026-08-31 its `allowed_tools` include `Write`/`Edit` (previously absent by
design, when it was investigate-only) — the safety boundary is no longer "physically cannot
touch files," it's the branch-isolation + never-touch-main rule in its prompt instead, which
is a real but softer guarantee than a missing tool. Manage it (pause, change time/count,
check recent runs, read the exact current prompt) via `RemoteTrigger` using its id
`trig_017u9zjSpCY18QezqHpi1Lma`, or point Emily to https://claude.ai/code/routines.

**Queue logic (fixed 2026-08-31 — the first version had two real bugs):**
1. If any cards have "Work Tonight" checked, **that's the queue, regardless of Status**
   (except Done/Archived, which just get the checkbox cleared as an assumed mistake) —
   uncapped, ordered by Priority then oldest createdTime. The original version filtered to
   `Status = 'Not started'` only, which **silently dropped** a checked box on an `In
   progress` card with no explanation — exactly the confusing case where Emily flags an
   already-investigated ticket wanting an actual fix attempt now. Fixed to include every
   checked card regardless of Status.
2. "Work Tonight" is unchecked for **every** card the moment it's pulled into the queue, up
   front — not conditionally deep inside per-card handling. The original version only
   unchecked cards it actually took final action on, so a card skipped for already being
   investigated kept its box checked forever, looking permanently "still queued" on the
   board. Fixed to always clear it immediately.
3. Every remaining ordinary "Not started" card goes on the end of the queue, same ordering —
   whether or not anything was checked. **Updated 2026-09-02: this is no longer a capped
   fallback.** It was "only when zero cards are checked, capped at 5"; the queue is now the
   whole backlog, and the run works through as much of it as it can rather than a sample,
   stopping only when it genuinely runs low on room and saying in its summary how much is
   left. A card is skipped only if it already has an `## Overnight fix attempt` section or
   is Done — a prior `## Investigated overnight` no longer disqualifies it, since an
   investigated ticket is often exactly the one now ready for a real fix attempt.
4. A card already carrying investigation findings is **not** automatically skipped anymore —
   it may now be eligible for an actual fix attempt it didn't get before. Skip (no action) is
   now reserved for a card that already has a "## Overnight fix attempt" section (a fix was
   already tried) or Status = 'Done'.

**Reconciling merged fixes (added 2026-09-01):** before building tonight's queue, the routine
should also check every ticket currently Status = "In progress" that carries an "## Overnight
fix attempt" or "## Fix built" section naming a branch — if that branch's commit is now
reachable from `main` (`git merge-base --is-ancestor <branch> main`, or `git log main
--oneline | grep <branch-name-or-slug>`), Emily has merged it herself. Mark that ticket
**Done**, add a one-line "merged into main, closing out" note, and uncheck "Needs Your Call"
if still set. This is the routine's side of the same rule live sessions already follow
(step 6 above) — a ticket shouldn't need her to remember to come back and manually close it
after the fact.

**Known gap (found 2026-09-01): GitHub push can fail with a 403.** A run hit `"Claude
doesn't have GitHub access to emilydalphy/home-manager for your organization"` on `git push`
— both the git CLI and the `mcp__github` tool refused with the same permission error. When
this happens, the branch/commit are NOT lost (they exist in that run's own cloud checkout),
but they never reach GitHub and vanish when the sandbox is torn down at the end of the run —
unless someone happens to reopen that exact session and retry once access is fixed. **Any
run whose `git push` fails must not report the fix as "Ready to merge"** — say plainly that
it was built and tested but couldn't be published, and point Emily to
https://claude.ai/customize/connectors?auth_start=github&auth_start_force=1 (reconnect) or
https://github.com/apps/claude/installations/select_target (org admin installs the Claude
GitHub App) to fix it, same as this doc tells any session to do.

**Deciding whether to attempt a fix, per card:** only when it's Type Bug or a small
well-contained Improvement (never a Feature), the fix is small/well-scoped (no new screen, no
redesign, no multi-file architecture change), and nothing requires a product/design/wording
judgment call. Otherwise it stays investigate-only. When it does attempt one: fresh branch off
main (`overnight/<slug>`), set up a throwaway venv (`.venv` is git-ignored so a fresh clone
never has one — `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r
requirements-dev.txt`, needs Python 3.10+), write the smallest correct fix, run the real test
suite, and — same as everywhere else in this skill — verify with a fresh sub-agent against
the actual diff before trusting it. Any failure at any step (no Python 3.10+, tests fail,
sub-agent flags a real concern) aborts the fix attempt and falls back to investigate-only
with the reason noted, rather than pushing something shaky. Only on success: commit, push
just that branch (never main, never a merge, never even a PR — Emily handles the merge
herself), mark the ticket "In progress" with a "## Overnight fix attempt" write-up, and set
"Needs Your Call" (merging is inherently her call).

**Needs Your Call (added 2026-08-31):** a checkbox property, separate from "Work Tonight,"
for the *output* side of the loop — set (by the overnight routine, or by a live session per
"Running the loop" above) on any ticket whose investigation surfaced a real open
question/decision only Emily can make, not just "a fix exists and could be built." Leave
unchecked when a finding is fully resolved or just confirms an existing plan. Emily (or
whoever addresses the decision) unchecks it once handled — same self-clearing pattern as
"Work Tonight." The overnight routine's summary/push notification leads with a "Needs your
call" section listing these explicitly, separate from a "no action needed" list, so Emily
can scan just the decisions rather than reading every ticket. She confirmed (2026-08-31) she
does receive the push notification each morning — that's the working "how do I know what to
review" mechanism; a routine can't wait overnight for her reply, so the loop is: it surfaces
clearly, she reads/replies, building starts the moment she does.

### The morning report — check whether anything broke, before anything else

Per Emily's 2026-09-01 decision, errors reach her through the push notification she already
reads each morning rather than by email. From 2026-09-02 there is something to read:

```
python observability_report.py --days 1     # exit code 1 if anything broke
```

Run it near the **start** of an overnight pass, and if it reports anything under `BROKEN`,
**lead the summary with that** — ahead of tickets, ahead of "needs your call". A beta tester
hitting errors matters more than backlog progress, and she has no other way to find out.

**Exit codes: 0 nothing broke, 1 something broke, 2 it could not look at all.** The third
matters more than it sounds. This run has a fresh clone and no Railway volume, so unless the
two variables below are set the script has nothing to read — and an earlier version answered
that by reporting on an empty local database and printing "Nothing broke" every morning
regardless. **A report that reads like good news is worse than no report.** If you get exit 2,
say so in the summary as a broken routine, not as a clean bill of health.

It reads the live app over the web, signing in exactly as a browser does, when these are set in
this environment:

```
HOME_MANAGER_URL=https://home-manager-production-4949.up.railway.app
HOME_MANAGER_PASSPHRASES=<household 1's passphrase>[,<household 2's>,...]
```

One passphrase per household, because a household's data is reachable only by signing into it —
there is deliberately no all-households view in the app. It reports every household separately
(a tester's bad day is invisible if you only look at Emily's). Failing that it reads a database
file at `DB_PATH`, which is the local/dev case only.

What it surfaces: crashed tools (invisible from outside — the assistant apologises smoothly
and the request records as a success), server errors, browser errors, and rate-limit
rejections. It also flags a household as `QUIET` when nothing happened at all that week,
which is the beta signal easiest to skim past.
