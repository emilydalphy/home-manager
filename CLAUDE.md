# Home Manager — project context for Claude Code

This file is read automatically at the start of every Claude Code session in
this repo. It's a status briefing, not the full history — for the detailed
decision log (every design tradeoff, every bug root-caused, every judgment
call made and why), read `design_handoff_home_manager/README.md` before
making non-trivial changes. That file has been kept current section-by-section
through the whole build and is the real memory of this project.

## What this is

A household assistant app: FastAPI + SQLite backend (`app/`), vanilla
HTML/CSS/JS frontend (`static/`), no build step, no framework. An
Anthropic-Claude chat agent (`app/agent.py`, tool-calling against
`app/tools.py`) plans meals, manages a grocery list, tracks kitchen
inventory, and answers "what we know" about the household. Deployed to
Railway, auto-deploying from `main` on push. Live at
`home-manager-production-4949.up.railway.app`.

## Branch state (as of this handoff)

- **`hardening` has been merged into `main`** (fast-forward, both branches
  point at `cc23802`) and pushed to `origin/main` — so it's deployed, or
  about to be, on Railway. The household confirmed `HOME_MANAGER_PASSWORD`,
  `SESSION_SECRET`, and `DB_PATH` (with its volume) were all set in Railway
  *before* this merge happened, so the password gate going live should be
  safe rather than a lockout/data-loss incident — but if the live site is
  behaving unexpectedly after this point, start by checking those three
  Railway variables are actually in effect, not just saved.
- The security/quality pass that's now live: household password gate + rate
  limiting (`app/security.py`, `app/ratelimit.py`), pinned dependencies, a
  smoke test suite + GitHub Actions CI, accessibility fixes (focus
  visibility, reduced motion, accessible names), and a `robots.txt` +
  `X-Robots-Tag` (this also means external fetch tools that respect
  `robots.txt` — including this session's own web-fetch tool — can no
  longer read the live site directly; that's expected, not a bug).
- If you're picking this repo up fresh: run `git log --oneline -10` to
  confirm this is still accurate — there may be new branches or commits by
  the time you read this.

## Working style established so far

- **Sandbox-verify everything before calling it done.** Fresh/reset SQLite
  DB, real `uvicorn` process, Playwright with `/opt/pw-browsers/chromium`
  for anything UI-facing. Don't trust a fix until it's actually been run.
- **Keep `design_handoff_home_manager/README.md` updated** section-by-section
  documenting every scope deviation, bug root-caused, and judgment call —
  honestly, including things that were tried and didn't work. This is what
  makes the project legible to the next session (human or Claude).
- A recurring local sandbox quirk (may or may not reproduce in your
  environment): `pkill -f uvicorn` and combined multi-step heredoc bash
  commands intermittently return exit 144 while silently not completing.
  Split DB-reset steps into separate, individually-verified commands rather
  than chaining them.

## Known architectural gotchas (don't re-discover these the hard way)

- **Tab panels build once per page load.** `static/shell.js`'s
  `buildWeekPanel`/`buildTodayPanel`/etc. are guarded by
  `panel.dataset.built`, so switching tabs away and back does NOT refetch
  data. Any change made via chat (or otherwise) to an already-built panel's
  data goes stale until reload, unless something explicitly refreshes it —
  see `refreshStaleTabsFromActions()` in shell.js, which now does this for
  chat-driven changes by reading each turn's `actions[].tab` field.
- **"Current week" plan resolution** is centralized in
  `tools._current_weekly_plan_row()` — it prefers the plan whose week
  actually contains today, falling back to most-recently-created only if
  none does. This used to be "just pick whichever plan has the latest
  `created_at`" everywhere, which silently drifted from "this week" the
  moment any other plan existed (a leftover old plan, a pre-planned future
  week). If you add a new "give me the current plan" call site, use this
  helper — don't re-derive the old raw query.
- **`generate_weekly_plan` (agent.py) generates before it persists.** It
  used to create the `weekly_plans` row first and fill it in after, which
  meant a failed/truncated LLM call left a permanently empty "current week"
  plan with no error shown anywhere. Now it only writes anything once it
  has real content, and raises a clear error otherwise (which the chat
  loop's existing per-tool `try/except` surfaces back to the model as a
  normal tool error).
- **Grocery quantity merging/display** goes through
  `_humanize_grocery_quantity()` (tools.py) — discrete items round up to a
  whole number (can't buy 1.5 onions), measurable units (tsp/tbsp/cup,
  oz/lb, g/kg, ml/l) roll up to the largest sensible unit. `scale_recipe`'s
  own scaling is intentionally NOT run through this — that's for cooking,
  not shopping, and wants precise amounts in the recipe's original unit.
- Service worker (`static/service-worker.js`) is network-first for
  navigations/`.html`/`.js`/`.css`, cache-first only for icons/manifest —
  this was a real stale-cache bug once (`CACHE_NAME` bumped to `v3` to
  force-clear it). If a fix "isn't showing up" on a real device, suspect
  this before suspecting the code — especially on an installed iOS
  Home-Screen PWA, which is slower to pick up service worker updates than a
  normal browser tab.

## Deploying

Push to `main` on GitHub; Railway auto-deploys from there. CI now runs the
smoke test suite on every push (added in the `hardening` work, now on `main`).

**Required Railway variables — confirmed set as of the `hardening` merge, but
worth re-checking if the live site ever misbehaves:**

- `HOME_MANAGER_PASSWORD` and `SESSION_SECRET` — required, or the app fails
  closed to all remote traffic by design.
- `DB_PATH=/data/home_manager.db` **with an actual volume mounted at `/data`.**
  If `DB_PATH` isn't set (or the volume isn't mounted), every redeploy
  silently wipes the household's database. This is a real data-loss risk,
  not a theoretical one.
- `PUBLIC_BASE_URL` — used for share links.
- A **spend cap on the Anthropic API key** in the console — worth
  double-checking this is actually in place, since it can't be verified from
  the repo. Rate limiting (`app/ratelimit.py`) caps the request rate, not
  total spend — they're not the same protection.

## A full code review already exists — read it before doing more hardening work

A 17-finding review of the whole codebase was done alongside the `hardening`
work (findings ranked, with which are fixed and which aren't):
https://claude.ai/code/artifact/0d42e9f3-4e71-401d-a4c0-1a1f1983dbf2

Findings 1/2/3/5/6/7/12/13/17 are fixed and now live on `main`.
**Still open, in case you're deciding what to work on next:**

- **#8** — `app/tools.py` is 5,143 lines with 188 `HOUSEHOLD_ID` references.
  Split it into a package by domain *before* doing real auth/multi-tenancy
  work — the smoke test suite (now running in CI on every push) makes this
  safe to do now.
- **#9** — the shell's tabs are iframes; navigation workarounds (see the
  Kitchen back-link bug fixed earlier) are accumulating as a result.
- **#10** — no shared `api.js`; ~26 hand-written `fetch('/api/...')` call
  sites, and pages carry 30–60 KB of inline CSS/JS each that can't be
  cached separately from the page.
- **#11** — three overlapping color vocabularies in `theme.css`.
- **#14** — "What we know" effectively asks the household to do data entry;
  autosave and collapse would help.
- **#15** — loading states are bare "Loading…" against a design system that
  otherwise commits to warm, first-person copy.
- **#16** — two navigation systems (client-side tabs vs. full page loads)
  that look identical but behave differently.

That review's own notes also flag: it verified the backend against a real
uvicorn server, but **never actually saw the frontend rendered** — the
Railway domain wasn't reachable from that sandbox. This Cowork session *did*
verify frontend behavior visually (via screenshots the household sent
directly, and one direct fetch of the live `/api/week-menu` JSON) for the
bugs in the sections above — but broad visual/screen-reader verification of
the whole app is still an open gap either way.

## Immediate open items

1. Voice dictation, the current-week plan fix, the grocery quantity
   humanization fix, and the full `hardening` security/quality pass are all
   on `main` and deployed (or deploying). If dictation still doesn't appear
   on a device, it's very likely the PWA cache issue above, not a missing
   deploy.
2. Consider tackling review finding #8 (splitting `tools.py`) next, now that
   it's safely testable — before layering more auth/multi-tenancy work on
   top of the current 5,143-line file.
3. The still-open findings (#9–#11, #14–#16 above) are real but not urgent —
   good candidates for "what should we work on next" rather than anything
   blocking.
