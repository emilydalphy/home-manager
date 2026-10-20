RECIPE LIBRARY RUN -- ONE-OFF (Emily, 2026-10-20, live session): "do the full recipe researching", "through my current credits ... not through the API", "do the run until it uses all that's available in the session limit". This run does ONLY recipe library work: no Loop Board cards, no app code changes, no main.

GOAL: research and write up as many of the 500 starter-list dishes as this session's usage limit allows, one checked JSON file per dish, pushed to the branch recipe-library-batches. Keep going until you are stopped. Commit and push often so nothing is lost when the limit hits.

HARD RULES
- Never touch, commit to, push to or merge into main. Work only on branch recipe-library-batches.
- Never file anything into a database, live or local. Filing into the live Shared Recipe Library happens later, only after Emily spot-checks.
- Make NO Anthropic API calls with the app's code: no seed_recipe_library.py runs, nothing that calls research_dish_llm or generate_recipe_details_llm. You do the research and the writing yourself, with your own WebSearch/WebFetch tools.
- Write Pomona's own version of each recipe from what you read. Never copy a site's wording or its steps verbatim. Credit the sites in "sources".
- If a site blocks you (403, a Cloudflare challenge, a paywall), skip it and read another version. Never go around a block through archive copies (Wayback Machine), print mirrors or a browser.
- Don't add an ingredient (like a lemon) only so a final "taste it" step has something to reach for, and don't invent a sauce no source has just to put something wet on the plate.
- Another worker (Emily's desktop session) works from the BOTTOM of the list. You work from the TOP. Always pick from `todo` (without --from-end), which skips dishes already on the branch.

SETUP
1. `git fetch origin && git checkout recipe-library-batches && git pull --rebase origin recipe-library-batches`
2. `python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt` (Python 3.10+).
3. Read `library_batch.py`'s docstring. Run `.venv/bin/python library_batch.py rules > /tmp/rules.txt` and READ ALL OF IT. It is the exact instruction text the app's recipe writer follows (servings, shopping-line amounts, categories, the step voice). Your recipes must follow it. The parts Emily wrote herself on 2026-10-19 matter most: no dashes in steps, no tagged-on reasons, food and pans aren't people, the serving line names what goes with it and stops, plain not clever, a temperature is the target with the thermometer optional plus a no-thermometer check, say the whole action ("Flip them and cook the other side for 2 minutes"), make-ahead steps first and start with when, every listed ingredient used and nothing extra, split amounts add up.
4. Look at the shape to produce: `data/recipe_library_batches/shaking-beef.json` and `data/recipe_library_samples.json` (the six Emily reviewed).
5. NETWORK CHECK, before anything else costs time: WebFetch https://natashaskitchen.com/meatloaf-recipe/ and one WebSearch. If either is blocked, stop, send a push notification saying recipe sites can't be reached from this environment, and end the run.

PER DISH (the format: one file `data/recipe_library_batches/<dish-key>.json`, where <dish-key> is the name lowercased with non-alphanumerics as "-")
a. Research: search for the dish, then READ 2 to 3 well-known versions (WebFetch). From each page's recipe data (JSON-LD "aggregateRating"), take ratingValue and ratingCount. Each source: {"name": site name, "title": recipe title, "url", "rating": number or null, "rating_count": int or null, "read": true}.
b. Lead: the app's rule is the top rating (to one decimal) among versions with 20+ ratings and a rating of at least 4.0, most ratings breaking a tie. Mark that source "lead": true and list it first. If none has 20+ ratings, read a version from the trusted cooks for the cuisine (`.venv/bin/python -c "from app.tools import recipe_research as r; print(r.trusted_cooks_for('<cuisine>'))"`), set "fallback_used": true, and lead with the earliest cook on that list.
c. Write it for 4 servings with no household in it (no names, no allergies; keep the base mild and put heat on the side). Fields: dish_name (exactly as in the list), cuisine, main_protein, default_servings: 4, prep_time_minutes, cook_time_minutes (honest), ingredients [{item, qty (the shopping line), category, and cook_qty where the cooking amount differs from what you buy}], instructions (list of steps), advance_prep_notes, advance_prep_step_indices, method_notes (2 to 4 sentences on where the sources agree and differ, as in the samples), sources, fallback_used, origin: "seed".
d. Check: `.venv/bin/python library_batch.py check data/recipe_library_batches/<file>.json`. Fix every FIX line. A "look" line is the amount check; it often misreads "the last clove" and the like, so fix it only if the sum really is off.

SPEED: use sub-agents. Run up to 6 at once, each handed 4 or 5 distinct dishes from one `todo --n 30` listing (never the same dish to two of them), the rules file path, and the per-dish steps above. As each one returns: run `check` on its files, fix what fails, then `git add data/recipe_library_batches && git commit -m "Recipe library: <dishes>" && git pull --rebase origin recipe-library-batches && git push origin recipe-library-batches`. Then hand out the next dishes. Keep this loop going until the session limit stops you. Never stop early because "a good amount" is done. Emily wants the whole allowance used.

QUALITY OVER COUNT: if you notice yourself copying a site's steps, writing dashes, or skipping the check to go faster, slow down. Emily will spot-check the first batch the way she reviewed the six samples, and the voice rules come from her comments.

AT THE END (or when you sense the limit is close): make sure everything is pushed, then send a push notification and write a final summary: how many dishes were added this run, how many are left (`todo` prints it), any dish you skipped and why, and any rule in the writer text that was hard to follow.
