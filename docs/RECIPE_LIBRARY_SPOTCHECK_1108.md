# Recipe library: 1,000 more (2026-11-08)

Emily, 2026-11-08: "Let's add 1000 more recipes to the database. Do an analysis of which areas that recipes should cover first. and make sure that it comes up with the drawings for it now with the new rules." Then: "make sure there are snakcs and breakfast options in that too."

The plan and the gap analysis are in docs/RECIPE_LIBRARY_1000_PLAN.md. The list was 2,104 dishes, and is 2,097 after the 2026-11-09 cuts below. Every dish on it has a recipe file and a drawn plate. Merged to main 2026-11-09 (1973ad8) on Emily's yes. **Not yet filed into the live library.**

## What was added

| | New |
|---|---|
| Dinners | 619 |
| Lunches | 203 |
| Breakfasts | 118 |
| Snacks | 60 |
| **Total** | **1,000** |

All 1,000 pass `library_batch.py check`. The "why" line for every new dish was rewritten against the recipe as written, because about a third of the list's original lines promised something the recipe doesn't have, such as a time, a sauce, a side or an ingredient. Every recipe that uses asafoetida now says to buy the gluten-free kind.

## What a household can pick from, before and after

Pomona aims for 40 dinners and 25 lunches for each kind of household.

| Household | Dinners, 30 min or less | Dinners, 45 min or less | Lunches, 30 min or less |
|---|---|---|---|
| Anyone | 77 → 318 | 283 → 739 | 52 → 165 |
| Vegetarian | **11 → 111** | 78 → 278 | 19 → 88 |
| Vegan | **2 → 59** | 39 → 159 | 9 → 51 |
| Gluten-free | 29 → 170 | 135 → 431 | 13 → 72 |
| Dairy-free | 37 → 220 | 167 → 521 | 28 → 113 |
| Vegetarian and gluten-free | **3 → 59** | 52 → 184 | 4 → 45 |
| Gluten-free and dairy-free | 10 → 116 | 84 → 318 | 8 → 53 |

Every household type now has at least 40 quick dinners and 25 quick lunches.

Other gaps:
- Quick Indian dinners went from 0 to 17.
- Quick Lebanese dinners went from 1 to 8. Lebanese is still the thinnest quick cuisine, because most Lebanese dinners are braises.
- Breakfasts of 15 minutes or less went from 23 to 45.
- Turkey dishes went from 10 to 36 and lamb from 23 to 46.

(Vegetarian, vegan and gluten-free are read from ingredient lists, so these are close counts rather than labels.)

**Fallbacks:** 589 of the 1,000 lead with a version that has fewer than 20 ratings. 262 of them lead with a page that shows no rating at all. The pattern is the same as before: Ethiopian, Turkish, Spanish, Lebanese and Vietnamese recipes are thinly rated online, and many big sites block the reader. About 160 picks were dropped along the way because they had no real recipe, ran too slow, or turned out to be a dish the library already had under another name. Each one was replaced.

## Drawings

Every new recipe has a plate in plates/specs parts 10 to 35, drawn under all 41 plate rules from the shared food parts. Three rounds of new parts were added so the plates could show dishes that were missing:
- **Round 1:** cauliflower, calamari, orzo, short pasta, scallops and fritters.
- **Round 2:** sausages, tarts, hot dogs, avocado, leeks, sardines, pork belly and vol-au-vent.
- **Round 3:** croissants, biscuits, braided bread, danish, bananas, tamales, gimbap, pita pockets, kibbeh and lettuce cups.

Existing plates draw exactly as before. Some plates read weakly and want a new part later: Bungeoppang, Palmiers, Pestinos, Egg Waffles, Banh Canh, Mi Quang, Flan, Daifuku, Elote en Vaso, Prosciutto e Melone, Sabich, Ensalada Campera, Bunuelos, Korean Corn Dog, Ca Kho Thom and Rice Krispie Treats. Parts asked for but not built yet include stuffed peppers, squash halves, braided pastry coils, whole roast chicken, stuffed squid, stromboli and raclette. Plates for 11 dishes that were cut from the round were removed.

## Decided (2026-11-09, on the spot-check page)

- **Call 2, weekend baking:** "Keep and add a weekend tag now". Every library breakfast of 2 hours or more carries `"weekend": true` on the starter list (11 dishes: the six above plus Cinnamon Rolls, Shokupan, Tamales de Rajas con Queso, Ambasha and Patjuk) and never lands on a Monday-to-Friday morning (breakfast_variety.weekend_only).
- **Call 3, special pans:** "Call out that it needs a real pan. If there is an alternative pan that works you can leave it. if not just cut them". Cut: Takoyaki, Bungeoppang, Khanom Krok, Egg Waffles, Murukku (a press), Paniyaram, and Thai Coconut Pancakes (an older, already-live twin of Khanom Krok; it must also be taken out of the live library when this round is filed). Kept because the recipe names an everyday stand-in: Financiers (mini muffin tin), Chinese Egg Tarts (muffin cups), Sopes (heavy dish instead of a press), Paletas de Fresa (paper cups, added). Tebasaki, Yakgwa, Vada Pav, Pizzette and Khanom Chan need no special pan and stay.

## Calls for Emily

1. **Raw fish:** Ceviche de Pescado is lime-cured raw fish, and Salmon Sushi Bowls use raw salmon. Keep them in a family library?
2. **Weekend baking listed as breakfasts:** Croissants (6 hours), Brioche (nearly 8), Tsoureki (7), Ensaimada (3 plus overnight), Conchas (3) and Beignets (3). All of them can be made ahead or frozen. Keep them, or move them to a "weekend project" tag later?
3. **Snacks that are real cooking:** Takoyaki and Bungeoppang need special pans, Tebasaki is fried twice, and Yakgwa, Vada Pav, Pizzette and Khanom Chan each take over an hour. Keep them?
4. **Lunches that are long braises:** Khao Kha Moo (nearly 4 hours), Rou Jia Mo (3 hours) and Dugun Corbasi (2 hours) all keep well, but they aren't lunch-quick.
5. **Sauces the sources don't have:**
   - Cumin Tofu adds a short soy sauce.
   - Steak Hache adds mustard and cream to the pan sauce.
   - Kakiage has a soy and mirin dip instead of the usual dashi one.
   - Hoy Tod adds sweet chili sauce.
   - Stromboli adds a dipping sauce.

   Each is in that recipe's method_notes. Keep them, or take them out under the no-invented-sauce rule?
6. **Diet changes that change the dish:**
   - Midia Saganaki was written without feta to fit a dairy-free tag, but saganaki is defined by the feta.
   - Havuc Tarator and Kabak Ezmesi lost their walnuts to stay nut-free.
   - Ensalada Campera dropped the tuna every source has, to stay vegetarian.

   Restore the defining ingredient and drop the tag?
7. **Light "dinners":** Aloo Methi, Cabbage Thoran, Espinacas a la Catalana, Escalivada, Polenta Concia, Nohutlu Pilav and Zeytinyagli Bakla are really sides served with rice. Gobi 65 reads as a starter. Keep them as dinners, or move them to lunch or snack?
8. **Close cousins kept side by side:**
   - Labneh Toast and Labneh with Zaatar
   - Reuben Sandwiches and Turkey Reuben
   - Salatet Adas and Greek Lentil Salad
   - Salmon Shioyaki and Saba Shioyaki

   Keep both in each pair?
9. **Salmon Shioyaki** needs a 2-day salt cure. It is 35 minutes on the night, but it has to be started two days before. Keep it?
10. **Recipes with very thin sources:**
    - Apple Slices with Sunflower Butter: no rated page, and the amounts are Pomona's own.
    - Cheese and Crackers: a snack-board page, not a recipe.
    - Ca Kho Thom: no rated version anywhere.
    - Zuppa di Farro: the lead page has no method.
    - Fatira: the egg step comes from a travel description.

    Keep them or cut them?
11. **Honest times over target:** 77 of the 300 quick dinners land over 30 minutes (18 of them over 35), and 42 of the 45-minute diet dinners land over 45 (6 over 50). The times written are the real ones. Should the planner treat 35 minutes as "quick"?
12. **Specialty shopping:** Kongbiji Jjigae (okara), Pad Cha Talay, Murukku (needs a press) and some Indian breakfasts (puttu maker, paniyaram pan). Flag these in the app?

Every adaptation is also written in that recipe's method_notes.

## Read these five first

1. **Chickpea Tikka Masala:** quick vegan dinner, Live Eat Learn 4.72 from 74, 30 minutes. The new vegan weeknight voice.
2. **Kadala Curry:** Swasthi 5.0 from 51. It shows how an Indian dish was brought under 50 minutes (canned black chickpeas).
3. **Chicken Enchilada Casserole:** a batch dinner, Five Heart Home 4.92 from 78. It includes the fridge, freezer and reheat steps.
4. **Ful Medames:** a 17-minute vegan breakfast, Silk Road Recipes 5.0 from 53.
5. **Yetakelt Wat:** Ethiopian with an unrated lead. This is what the thin-source fallbacks look like.
