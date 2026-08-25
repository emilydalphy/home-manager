# Paste-into-terminal handoff — app shell + weekly menu

I can't push to your GitHub directly (read-only access), so this is the two-step version.

## 1. Put the handoff folder in the repo

Download `design_handoff_shell/` from this project, then:

```bash
cd ~/path/to/home-manager
git checkout -b app-shell-redesign
mkdir -p design_handoff_shell
cp -R ~/Downloads/design_handoff_shell/* design_handoff_shell/
```

`Home Manager Prototype.dc.html` in that folder opens in a browser and is clickable — tell your
agent to actually open it, not just read it.

## 2. Paste this to your coding agent

```bash
claude "Implement the app shell + weekly menu redesign in design_handoff_shell/README.md.

Read these first, in this order:
1. design_handoff_shell/README.md — the full spec: the six flow problems being fixed, the four-route IA, tokens, mobile layout per screen, the weekly menu (mobile + desktop), derived state, the desktop shell, and a build order in section 9. Follow the build order; do not do it all at once.
2. design_handoff_shell/Home Manager Prototype.dc.html — open this in a browser. It is the clickable visual source of truth for the mobile experience. Anything the prose leaves ambiguous, match the prototype.
3. static/index.html — the page being replaced. Inventory every behavior it has today (chat, quick actions, the nav links, onboarding entry) so nothing is silently dropped.
4. static/theme.css — brand tokens. Add --menu-paper: #fffdf6, --menu-rule: #e7dcc4, --menu-ink: #a8825c to :root; do not hardcode them per-rule.
5. static/grocery.html, static/cooker.html, static/memory.html, static/onboarding.html — these stop being standalone pages and start rendering inside the shell. Keep their routes alive as redirects for one release.

Then, in this order:
- Step 1: build the shell only (persistent bottom tabs on mobile, 230px plum left rail at >=1024px, a flex scroll area, and the docked ask bar). Grocery and Kitchen initially just render their existing markup inside it. Tab switching must NOT reload the page.
- Step 2: build Today (derived heading, plum dinner card, my chores, grocery summary).
- Step 3: move chat into the ask sheet, reachable from every route, and make every assistant reply that changes something emit an action card that navigates to the tab it changed. No answer may dead-end.
- Step 4: the weekly menu. This needs the one backend change: the meal plan goes from one dinner per day to three nullable slots per day (breakfast, lunch, dinner), each { title, meta, source }. Nullable is what drives the 'Pick' row and the needs-you card. Build the mobile paper-menu stack first, then the desktop 7-column x 3-row grid.
- Step 5: the needs-you band, with two hardcoded rules to start: an empty dinner slot within 48 hours, and a shop run whose items are needed before the next planned meal.

Constraints:
- Same stack as the rest of static/: plain HTML, vanilla JS, fetch, no build step, no framework.
- Every card inside the flex scroll column needs flex-shrink:0 — without it the menu day cards collapse and clip their own content. See section 8.
- Minimum tap target 44px, minimum body text 15px. Used one-handed while cooking.
- All counts and headings are derived from data (section 6 has the table). Never hardcode a count.
- Optimistic updates with rollback for checkboxes, picks and adds.

When done, show me a diff summary and list anything in the spec you deliberately deviated from and why."
```

## If you'd rather do it yourself

`README.md` is written to be implementable straight off. Section 9 is the order that keeps the app
shippable at every step — step 1 alone fixes half the problems.
