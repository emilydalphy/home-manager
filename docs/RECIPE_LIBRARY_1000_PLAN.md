# 1,000 more recipes: what to cover first (2026-11-08)

Emily, 2026-11-08: "Let's add 1000 more recipes to the database. Do an analysis of which areas that recipes should cover first. and make sure that it comes up with the drawings for it now with the new rules."

## Why gaps matter now

Since library-first (2026-11-03), Pomona drafts the week FROM the library. It hands the planner about 40 dinners and 25 lunches that fit the household's rules (diet, allergies, dislikes, time cap). When fewer than that fit, the week repeats or Pomona researches and writes a new dish, which costs money and time. So a gap is now a household-shaped gap: what does a vegetarian family with a 30-minute weeknight cap actually get to pick from?

## What a household can pick from today (1,098 written recipes)

Target: 40 dinners and 25 lunches per kind of household.

| Household | Dinners, any length | Dinners, 45 min or less | Dinners, 30 min or less | Lunches, 30 min or less |
|---|---|---|---|---|
| Anyone | 633 | 283 | 77 | 52 |
| Vegetarian | 159 | 78 | **11** | **19** |
| Vegan | 67 | 39 | **2** | **9** |
| Pescatarian | 253 | 141 | 32 | 26 |
| Gluten-free | 301 | 135 | 29 | **13** |
| Dairy-free | 359 | 167 | 37 | 28 |
| Vegetarian and gluten-free | 103 | 52 | **3** | **4** |
| Gluten-free and dairy-free | 169 | 84 | **10** | **8** |

Other gaps:
- Half of all dinners take 50 minutes or more. Only 77 of 633 are 30 minutes or less.
- Quick dinners by cuisine: Indian has **0**, Lebanese 1, Vietnamese, Ethiopian and Greek 2 each.
- Breakfasts: only 23 take 15 minutes or less, and 11 are vegan.
- Turkey (10 dishes) and lamb (23) are thin.
- Snacks lean American (34 of 139).

(Diet labels here are read from ingredient lists, so they are close, not exact.)

## The 1,000, in the order they get written

| # | Area | How many | What's in it |
|---|---|---|---|
| A | Weeknight dinners, 30 minutes or less | 300 | 20 per cuisine. At least 120 vegetarian (60 of them vegan), 120 gluten-free (40 vegetarian and gluten-free), 80 dairy-free. The rest: chicken, fish and shrimp, beef, pork, turkey. |
| B | Lunches | 200 | Quick (30 min or less) or made ahead and packed. 100 vegetarian (40 vegan), 80 gluten-free. |
| C | 45-minute dinners for diets | 180 | 50 vegan, 50 vegetarian and gluten-free, 80 gluten-free and dairy-free with meat or fish (chicken, turkey, fish, lamb). |
| D | Breakfasts | 120 | 50 in 15 minutes or less, 40 made ahead, 30 egg-free or vegan. At least 50 gluten-free. |
| E | Family favourites and batch cooking | 140 | 70 familiar, mild dinners children eat; 70 that make a big batch or freeze well (soups, stews, bakes, casseroles). |
| F | Snacks | 60 | Nut-free, across the cuisines, half savoury. |

All within the 15 cuisines a household can pick today. New cuisines (Caribbean, Filipino, Persian, Moroccan and others) would also change onboarding, so they're a separate call for Emily.

## Drawings

Every new recipe gets a drawn plate under the plate rules (plates/PLATE_RULES.md, all 42), built from the shared food parts, in plates/recipes and plates/specs parts 10 onward, then `python3 plates/build_plates.py`. A random sample goes to Emily to review, and her comments become rules.

## Going live

Same as every round: written on branch recipe-library-1000-2026-11-08, never main. Merging (1 to 6am window) and filing into the live library both need Emily's yes.
