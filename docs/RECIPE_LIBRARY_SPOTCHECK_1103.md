# Recipe library: 200 more (2026-11-03)

Emily asked on 2026-11-02 to "research more recipes to add into the recipe database", then "add snacks and breakfasts to that list, and make it 200", with "some that can be meal prepped ahead". This run picked 200 new dishes for the thinnest spots and wrote every one. The starter list is now 1,104, and every dish on it has a recipe file.

Written by 41 researcher agents from the run brief (docs/RECIPE_LIBRARY_RUN.md plus the lessons from the earlier spot-checks). All 200 pass `library_batch.py check`, and a sweep of the steps found no dashes, no "more" after a time, no "tip", no "because" tags and no "That's about". The leftover "look" lines are broth the dish makes itself or a misread split. **Nothing has been filed into the live library.** Going live needs Emily's yes: a merge to main in the 1 to 6am window, then filing on Railway.

## Coverage, before and after

| | Before (904) | After (1,104) |
|---|---|---|
| Dinners | 519 | 639 |
| Quick dinners, 30 minutes or less all in | 46 | 78 |
| Lunches | 139 | 169 |
| Breakfasts | 132 | 157 (13 new meal-prep ones) |
| Snacks | 114 | 139 |
| Vegetarian dinners | 155 | 184 |
| Vegetarian and gluten-free dinners | 100 | 118 |
| Fish and seafood dinners | 74 | 94 |
| Lamb dinners | 17 | 23 |
| Turkey dinners | 5 | 8 |
| Turkish dinners | 19 | 28 |
| Ethiopian dinners | 21 | 27 |
| Spanish dinners | 21 | 30 |
| Lebanese dinners | 23 | 33 |
| Spanish breakfasts | 3 | 5 |
| Vietnamese snacks | 3 | 5 |

(Vegetarian, vegan and gluten-free are read from the ingredient lists, so they're close counts rather than labels.)

**Fallbacks:** 111 of the 200 lead with a version that has fewer than 20 ratings, or none (the library total is now 346 of 1,104). It's the same pattern as before: Ethiopian, Turkish, Spanish, Lebanese and snack recipes are thinly rated online, and many big sites block the reader.

## Calls for Emily
1. **Quick dinners that aren't quick:** 28 of the 40 picked as quick dinners come in at 30 minutes or less. Twelve don't: Cheesy Beef and Rice Skillet 45, Chilli Chicken 45, Hashweh 46, Chicken Jalfrezi 37, Biftekia 34, Kielbasa and Cabbage 34, Minchet Abish 32, Pork Chops Charcutiere 32, Turkey Cutlets 32, Kai Look Kheuy 32, Spetsofai 31, Arayes 31. The honest times are written. Keep them, or should the planner treat them as normal weeknight dinners?
2. **Vegan labels and jarred curry paste:** Thai Pumpkin Curry and Vegetable Massaman use bought curry paste, and some brands contain shrimp paste. Should the list say "vegan red curry paste"?
3. **Near-dishes left out of the sources:** Djej bil Toum (RecipeTin's Al Aseel chicken), Vegetable Massaman (meat massamans), Chicken Katsu Sando (Just One Cookbook's pork katsu sando) and the hard-boiled eggs (a deviled egg page) would have won the lead rule but are different dishes, so they're credited in method_notes only. Same call as Tuna Mornay.
4. **Ye Siga Alicha** is close to the existing Yebeg Alicha (the only readable version was a lamb alicha, adapted to beef). Keep both or cut one?
5. **Kotopoulo me Hilopites** is a near-sibling of Kotopoulo Kokkinisto (noodles instead of rice). Keep both?
6. **Salmon Patties** gained a lemon mayonnaise on the side. Keep it, or take it out under the no-invented-sauce rule?
7. **Long weekend dishes in the "thinnest cuisines" set:** Boeuf Carottes 215 minutes, Lamb Ragu 230, Arni Lemonato 200 plus a night, Estofado 130, Caldo Gallego 130, Fasolia w Riz 125, Etli Bezelye 120. They're real braises, not weeknight food.
8. **Specialty shopping:** Mloukhieh (dried jute leaves), Samgyetang (ginseng), Turnip Cake (dried shrimp, Chinese sausage), Fruit Chaat (black salt), Miang Kham (palm sugar), Tofu Katsu (vegan Worcestershire). Fine for the library, or flag them in the app?
9. **"Taste it" on cold dishes:** Italian Chopped Salad ends with the cooked-dinner "Taste it. If it's flat, add a pinch more salt." It reads oddly on a salad. Keep it as the rule for everything, or only for cooked dishes?
10. **Visible ratings:** the reader showed most pages' visible star line, not the page data, so most ratings this round come from the visible line. Each file's method_notes says so where it matters.

## Read these five first
1. **Garlic Butter Steak Bites** (quick dinner, Spend With Pennies 4.94 from 46, 22 minutes): the voice on a plain weeknight dinner.
2. **Sheet Pan Pancakes** (meal-prep breakfast): make-ahead steps first, fridge and freezer times, reheating.
3. **Turnip Cake** (meal-prep breakfast, The Woks of Life 4.92 from 157): the most involved make-ahead recipe this round.
4. **Bap Xao** (vegetarian snack): every source uses dried shrimp and fish sauce, so this shows what a meatless adaptation reads like.
5. **Karides Guvec** (fish dinner): it was re-led after a researcher read a repost site, so it's a good test of whether a fallback lead holds up.

## Changes outside the recipe files
- **Banana Sushi** was the same snack as the existing Peanut Butter Banana Roll-Ups, so it was swapped for **Pan con Chocolate** (Spanish).
- Three list notes were reworded to match the recipes: Breakfast Enchiladas (baked on Sunday, not assembled the night before), Sincronizadas ("about 15 minutes", not 10), Taralli ("keep for days", not a week; the lead says 3).
- The seed test now expects 1,104.
- The researcher brief gained two rules: rice, pasta, broth and beans are written as real amounts (never "1 bag"), and a recipe is never read through a repost site (Slurrp and the like).

## Per batch
## G1 (Honey Mustard Chicken Cutlets, Cheesy Beef and Rice Skillet, Kielbasa and Cabbage Skillet, Garlic Butter Steak Bites, Turkey Cutlets with Cranberry Pan Sauce)
- Honey mustard: lead is RecipeTin's honey mustard chicken strips (4.82 from 103); written as cutlets. The one true cutlet version had only 15 ratings.
- Cheesy beef and rice: honest 45 minutes, not 30 (the rice needs 18 covered minutes). Plain diced tomatoes instead of Rotel.
- Kielbasa and cabbage: 34 minutes, not 25.
- Turkey cutlets: fallback, unrated lead (Foody Schmoody); 32 minutes.
## G2 (Chicken Pizzaiola, Sausage and Kale Gnocchi, Pasta alla Boscaiola, Tacos de Bistec, Alambre)
- Ratings read from the page's visible star line (the reader didn't show the JSON-LD).
- Gnocchi: no source crisps the gnocchi (all simmer); here they're browned first, so golden in spots rather than crisp.
- Boscaiola: the lead has no tomato; tomato paste added from two unrated versions to match "light tomato cream". Porcini left out for time.
- Tacos de Bistec: the lead's 30 to 60 minute lime marinade cut to 10 minutes.
- Alambre: Worcestershire in place of Maggi (the lead's own swap); Oaxaca cheese.
## G3 (Milanesa de Res, Hunan Beef, Chicken with Garlic Sauce, Beef and Tomato Stir-Fry, Stir-Fried Cabbage with Pork)
- Hunan Beef and Beef and Tomato: the leads' 30 to 60 minute marinades cut to 15 minutes to stay at 30 minutes or less.
- Chicken with Garlic Sauce and Stir-Fried Cabbage with Pork are fallbacks (8 and 6 ratings).
- The Chinese dishes put jasmine rice on the list (1 1/2 cups) so the timeline includes it.
## G4 (Yakiniku Don, Niku Udon, Dak Bulgogi, Dwaeji Kimchi Bokkeum, Pad Kana)
- Yakiniku Don, Niku Udon and Dwaeji Kimchi Bokkeum are fallbacks with weak leads (3, 5 and no ratings).
- Niku Udon: instant dashi and frozen udon to stay at 30 minutes; the sake left out.
- Dak Bulgogi: Carmyy (5.0 from 29) beats Korean Bapsang (4.55 from 349) under the rule (the ratings-tie question again).
- Pad Kana: one source only (Thai Cookbook, 4.5 from 6,211); pork loin, not crispy pork belly.
## G5 (Rad Na, Kai Look Kheuy, Bo Xao Dau Que, Com Chien Duong Chau, Biftekia)
- Kai Look Kheuy: 32 minutes (the eggs boil and cool first); fallback, 3 ratings; brown sugar for palm sugar.
- Biftekia: 34 minutes; the sources bake them on lemon potatoes, this is the pan-fried version the line asked for, with a tzatziki we wrote (no source gives amounts).
- Bo Xao Dau Que and Com Chien Duong Chau: no rated version anywhere; unrated fallback leads.
- Rad Na: chicken broth for pork stock, oyster sauce for Golden Mountain sauce.
- Com Chien Duong Chau lists "Cooked rice" (4 cups) as a shopping line, since it's a leftover-rice dish.
## G6 (Spetsofai, Greek Pork Chops, Hashweh, Arayes, Djej bil Toum)
- Hashweh: 46 minutes, well over a quick dinner (the rice simmers 15 and rests 5). Lead is a pressure-cooker recipe, adapted to the stovetop.
- Spetsofai and Arayes run 31 minutes.
- Greek Pork Chops: every source marinates 30+ minutes; here a 10 minute rub plus a lemon pan sauce.
- Djej bil Toum: fallback, unrated lead; a quick garlic-lemon-oil stand-in for blender toum. RecipeTin's Al Aseel lemon garlic chicken (4.76 from 49) was left out of sources as a near-dish (credited in method_notes).
## G7 (Sac Kavurma, Kiymali Ispanak, Solomillo al Whisky, Garbanzos con Chorizo, Lomo con Pimientos)
- Four of five are fallbacks with unrated leads (only Sac Kavurma has a 20+ lead).
- Kiymali Ispanak: no rice, to stay at 30 minutes (every Turkish version adds a little; with rice it's about 38).
- Lomo con Pimientos: the lead uses roasted red peppers and a 20 minute simmer; written with fried green peppers from a second version. Weakest lead.
- Solomillo al Whisky: served with bread instead of the sources' fried potatoes, no flambé.
## G8 (Minchet Abish, Poulet a l'Estragon, Pork Chops Charcutiere, Chilli Chicken, Chicken Jalfrezi)
- Chilli Chicken: 45 minutes, not quick (shallow-fried in two batches; the overnight marinade and second fry left out).
- Chicken Jalfrezi: 37 minutes; follows the second source's one-pan method rather than the lead's two pans.
- Minchet Abish: 32 minutes; 2 tsp berbere in the pot, 1 tsp at the table.
- Pork Chops Charcutiere: fallback, unrated lead (Food.com).
- Poulet a l'Estragon: buttered baby potatoes added as the side.
## G9 (Paneer Jalfrezi, Dum Aloo, Vegetable Biryani, Kala Chana Curry, Thai Pumpkin Curry)
- Vegetable Biryani: 95 minutes, a weekend dish (marinated dum biryani, as the lead).
- Dum Aloo 65 minutes, Kala Chana 65 minutes plus an overnight soak (pressure cooker; the no-cooker way is in method_notes), Paneer Jalfrezi 40.
- Kala Chana: Hebbar's (4.99 from 256) leads over Swasthi's (5.0 from 123) by the check's rule.
- Thai Pumpkin Curry: fallback (Vegan Richa, 11 ratings); butternut squash; jarred red curry paste, and some brands have shrimp paste (vegan label call).
## G10 (Vegetable Massaman Curry, Sweet Potato Black Bean Enchiladas, Roasted Cauliflower Tacos, Black Bean Tostadas, Mushroom Fajitas)
- Four of five are fallbacks with 0 to 3 ratings; only the cauliflower tacos (Love and Lemons, 5.0 from 25) qualify. Weakest round so far.
- Massaman: the rated pages are meat massamans and were left out; jarred massaman paste can contain shrimp paste (vegan label call).
- Enchiladas 65 minutes; cheese, so vegetarian not vegan.
- Cauliflower tacos: the red cabbage stays raw as a slaw and the crema is sour cream and lime (the lead's sauce is avocado).
## G11 (Eggplant Rollatini, Lentil Bolognese, Vegetable Lasagna, Lentil Shepherd's Pie, Mushroom Stroganoff)
- Long ones: Vegetable Lasagna 90 minutes, Eggplant Rollatini 80, Lentil Shepherd's Pie 80, Lentil Bolognese 50.
- Vegetable Lasagna: RecipeTin (4.97 from 169); bell pepper and mushrooms in place of pumpkin; 9x9 dish; no Parmesan.
- Lentil Shepherd's Pie: gluten-free (cornstarch, tamari), vegetarian but not vegan (butter and milk in the mash).
- Mushroom Stroganoff: fallback (8 ratings); soy plus Dijon in place of Worcestershire (anchovy).
## G12 (Arakas Latheros, Maghmour, Zeytinyagli Pirasa, Tofu Katsu, Mushroom Takikomi Gohan)
- Four of five are fallbacks (only Arakas Latheros has a 20+ lead: Not Quite Nigella, 5 from 54). Zeytinyagli Pirasa has no rated version.
- Tofu Katsu: needs vegan Worcestershire in the sauce (regular has anchovy).
- Takikomi Gohan: aburaage, dried shiitake and kombu are specialty items. Just One Cookbook's chicken version was not used.
## G13 (Tofu Bulgogi, Di San Xian, Ca Tim Kho To, Menestra de Verduras, Mushroom Bourguignon)
- Tofu Bulgogi: the lead is a shredded-tofu bulgogi bowl (4.91 from 76), pan-crisped instead of air-fried; cucumbers added for crunch.
- Ca Tim Kho To: fallback; the lead is a pork version with the pork, fish sauce and oyster sauce taken out. No extra protein.
- Menestra: fallback, unrated; ham and chicken broth left out, carrots and baby potatoes added so it makes a dinner (our own adaptation).
- Mushroom Bourguignon: 70 minutes; egg noodles added as the starch.
## G14 (Salmon Patties, Parmesan Crusted Tilapia, Cioppino, Cod alla Livornese, Camarones al Mojo de Ajo)
- Salmon Patties: two cans (lead uses one) so four people get three each; a lemon mayonnaise added on the side (check that a source has one, or take it out).
- Tilapia: 24 minutes, not 20; lead has exactly 20 ratings; panko added to the crust.
- Cioppino: 65 minutes; clams and crab left out (mussels, shrimp, cod kept); only two versions readable.
- Mojo de Ajo: rice added alongside; butter raised to 4 tbsp.
## G15 (Machher Jhol, Tandoori Salmon, Choo Chee Pla, Goong Pad Kratiem, Ebi Fry)
- Machher Jhol: lead just makes the bar (Hari Ghotra, 4.0 from 20); vegetable oil where mustard oil is traditional; 55 minutes.
- Tandoori Salmon: the lead (Cup of Yum, 4.8 from 141) has no yogurt, so the yogurt marinade comes from a second version; mild paprika for hot Kashmiri chili.
- Choo Chee Pla and Goong Pad Kratiem: fallbacks with weak leads (2 ratings, unrated).
- Ebi Fry: 50 minutes; shallow-fried with one coating instead of deep-fried with a double dredge.
## G16 (Saengseon Jeon, Honey Walnut Shrimp, Shrimp and Broccoli Stir-Fry, Mi Xao Hai San, Bakaliaros Skordalia)
- Saengseon Jeon, Honey Walnut Shrimp and Shrimp and Broccoli are fallbacks (unrated, 10 and 12 ratings).
- Honey Walnut Shrimp: pan-fried in cornstarch, not deep-fried, so the crunch is lighter; broccoli and rice added as sides.
- Mi Xao Hai San: every rated version is the crispy noodle kind; written as a soft noodle stir-fry with the lead's sauce. Scallops left out.
- Bakaliaros Skordalia: fresh cod with a 20 minute salting (the lead allows it) instead of 2 to 3 days of soaking salt cod; 60 minutes. Lemon wedges and pita on the list.
## G17 (Zarzuela de Mariscos, Bouillabaisse, Samak bi Tahini, Karides Guvec, Asa Wat)
- Zarzuela and Bouillabaisse use bought fish stock. Bouillabaisse's rouille is a mayonnaise one from a second version, and fennel seed plus wine stand in for Pernod; 65 minutes.
- Samak bi Tahini: fallback (4 ratings); caramelized onions and cumin from two other versions.
- Karides Guvec: the researcher read The Spruce Eats through a repost site (Slurrp). I took that source out; Cup of Yum (5 from 8) now leads as a fallback. Mozzarella for kashar.
- Asa Wat: fallback, unrated lead on an obscure site; berbere cut from 4 to 6 tbsp to 2, with more at the table.
## G18 (Southwest Quinoa Salad, Egg Roll in a Bowl, Tuna Pasta Salad, White Chicken Chili, Chicken Caesar Wraps)
- Egg Roll in a Bowl: Natasha's (4.97 from 407) leads but uses beef; written with pork as the line says (Once Upon a Chef's pork version is second). Bagged coleslaw mix.
- Tuna Pasta Salad and Chicken Caesar Wraps: fallbacks, unrated leads.
- White Chicken Chili: 50 minutes, long for a lunch, but hands-off and makes four lunches (rotisserie chicken).
## G19 (Italian Chopped Salad, Tortellini Pasta Salad, Pasta e Lenticchie, Coctel de Camaron, Caldo de Pollo)
- Italian Chopped Salad: fallback (Well Plated, 19 ratings, one short); kale, olives and sun-dried tomatoes left out. Ends with the cooked-dinner "Taste it" line, which reads oddly on a salad.
- Pasta e Lenticchie: vegetarian (no pancetta or cheese); 55 minutes.
- Coctel de Camaron: tomato juice plus ketchup instead of Clamato; keeps 1 day; 35 minutes plus a 30 minute chill.
- Caldo de Pollo: fallback (1 rating; the popular pages blocked); 95 minutes, long for a lunch but makes a week of them.
## G20 (Salpicon de Res, Pork Fried Rice, Tomato Egg Noodle Soup, Chicken Katsu Sando, Somen Salad)
- Salpicon de Res: fallback, unrated; 80 minutes (a long simmer, made up to 3 days ahead).
- Tomato Egg Noodle Soup: fallback, unrated; tomato paste added from a second version.
- Chicken Katsu Sando: fallback (3 ratings). Just One Cookbook's pork katsu sando (4.95 from 37) would win the rule but is the pork dish, so it's credited in method_notes only (the Tuna Mornay pattern).
- Somen Salad: ham in place of kamaboko.
## G21 (Curry Udon, Chicken Mayo Rice Bowl, Thai Peanut Noodle Salad, Mien Ga, Tomato Rice)
- Curry Udon, Chicken Mayo Rice Bowl and Mien Ga: fallbacks with weak leads (11, none, 6 ratings). Chicken Mayo leads with Kewpie's own unrated page.
- Long lunches: Mien Ga 70 minutes (bone-in thighs for a whole chicken), Tomato Rice 65 (a pressure-cooker lead adapted to the stove), Chicken Mayo 50.
- Thai Peanut Noodle Salad: the lead (Cup of Yum, 4.8 from 196) is more American than Thai; rice noodles for spaghetti.
## G22 (Puliyogare, Vegetable Hakka Noodles, Greek Orzo Salad, Salatet Batata, Fatayer bi Lahme)
- Puliyogare: Hebbar's leads (4.98 from 193) but makes a month's jar of tamarind paste, so the amounts follow Swasthi's single batch; 55 minutes.
- Fatayer bi Lahme: fallback (9 ratings); a from-scratch yeast dough, 120 minutes, so not lunch-sized on the day. Makes 16 pies (half the lead's 32); keeps 3 days, freezes 3 months.
- Greek Orzo Salad: 35 minutes plus a 1 hour chill.
## G23 (Yayla Corbasi, Tavuk Durum, Ensaladilla Rusa, Porrusalda, Salade de Lentilles)
- All five are fallbacks; none has a version with 20+ ratings. Tavuk Durum has no rated version at all.
- Tavuk Durum: the researcher led with Yemek.com's tavuklu durum manti, which is a baked cousin dish, so I re-led it with A Food Lover's Kitchen's chicken durum wrap. Paprika in place of pepper flakes; no heat at the table.
- Ensaladilla Rusa: no egg or olives; jarred mayonnaise.
- Salade de Lentilles: Dijon and red wine vinegar (the lead uses balsamic and nutritional yeast).
- Yayla Corbasi: its two sources look like the same author.
## G24 (Overnight French Toast Casserole, Sheet Pan Pancakes, Breakfast Cookies, Bran Muffins, Lemon Poppy Seed Muffins) — all meal-prep
- All five have 20+ leads. Make-ahead steps go first, with fridge/freezer times and reheating.
- French Toast Casserole: no source gives storage times; 4 days in the fridge is ours, no freezer line. Makes 8 pieces.
- Breakfast Cookies: the lead is titled "Oatmeal Banana Cookies" (same dish); half the lead's chocolate chips.
- Lemon Poppy Seed Muffins: no glaze, so they freeze plain; the 5 day fridge time is ours.
## G25 (Breakfast Enchiladas, Turnip Cake, Peynirli Tepsi Boregi, Magdalenas, Gobi Paratha) — all meal-prep
- Breakfast Enchiladas: fallback (8 ratings); baked on Sunday and reheated by the portion (list note updated to match).
- Turnip Cake: 115 minutes (50 of it steaming); keeps dried shrimp, Chinese sausage and oyster sauce; fridge 3 days, freezer 1 month (softer).
- Tepsi Boregi: fallback, unrated lead; thaw the phyllo the night before.
- Magdalenas: fallback (11 ratings); keep on the counter 3 days or freeze 2 months, not the fridge.
- Gobi Paratha: chilies out of the filling, pickle at the table; the 1 month freezer time is our cautious guess.
## G26 (Ciambellone, Rava Idli, Ambasha, Khai Krata, Spanish Migas)
- Ciambellone: fallback (7 ratings); neutral oil, no pearl sugar; fridge and freezer times ours.
- Rava Idli: baking soda and lemon for Eno; needs an idli stand; reheat and freezer times ours.
- Ambasha: 145 minutes counting about 90 minutes of rising in prep; makes two rounds (16 wedges).
- Khai Krata: fallback, unrated; seasoned minced pork for Vietnamese pate; Chinese sausage is a specialty item.
- Spanish Migas: 45 minutes, slow for a weekday breakfast; bacon left out. Different dish from the Mexican Migas already in the library.
## G27 (Kinche, Ejjeh, Pesarattu, Sincronizadas, Okayu)
- Kinche: no rated Ethiopian version exists; quick spiced butter (butter, cardamom, ginger, turmeric) for niter kibbeh.
- Ejjeh: the check made Cosette's (5.0 from 1) lead; the method follows Amy's Dish (3.9 from 60, under the 4.0 floor). Allspice for seven spice.
- Pesarattu: 50 minutes plus a 4 to 6 hour soak (step 1, advance prep).
- Sincronizadas: 12 minutes, not 10 (two fit in the pan at a time); list note changed to "about 15".
- Okayu: 80 minutes with the soak, long for a breakfast; the quick cooked-rice version (about 15 minutes) is in method_notes.
## G28 (Jumeokbap, Sweet Potato Toast, Banh Mi Chao, Banana Oat Pancakes, Huevos Motulenos)
- Four of five are fallbacks (only Banana Oat Pancakes has a 20+ lead: Ambitious Kitchen, 4.88 from 914).
- Jumeokbap: keeps only a few hours (the rice goes hard in the fridge); 35 minutes if the rice is cooked fresh.
- Banh Mi Chao: the lead gives few amounts, so we filled them in; beef, ham and fried potatoes of the longer versions left out.
- Huevos Motulenos: 45 minutes, long for a weekday breakfast; habanero, ham and plantain left out.
## G29 (Apple Nachos, Roasted Pumpkin Seeds, Banana Sushi → swapped, Everything Bagel Hard-Boiled Eggs, Caprese Skewers)
- All are fallbacks; no snack version had 20+ ratings.
- Banana Sushi was the same snack as the existing Peanut Butter Banana Roll-Ups, so it was swapped for Pan con Chocolate.
- Hard-boiled eggs: a 10 minute covered sit so the yolks are fully set for the week; season on the day. Tasting Table's deviled egg page was left out of sources as a different dish.
- Caprese skewers: bought balsamic glaze.
## G30 (Taralli, Corn Cheese, Mayak Eggs, Yaki Onigiri, Daigaku Imo)
- Taralli: 70 minutes plus a 4 to 5 hour dry; makes 32; keeps 3 days per the lead (the list note said a week).
- Corn Cheese: fallback (7 ratings).
- Mayak Eggs: at least 6 hours in a soy marinade; keep a week, best in 3 to 4 days.
- Yaki Onigiri: the lead (Tasting Table, 4.9 from 33) mixes the sauce into the rice and sears in butter rather than brushing a glaze.
- Daigaku Imo: deep-fried in 2 cups of oil, more cooking than most snacks; 50 minutes.
- Pan con Chocolate (the swap): fallback, no rated version anywhere; toasted baguette with chocolate softened in a low oven (list note changed from "no cooking").
## G31 (Korokke, Miang Kham, Kluay Tod, Bap Xao, Che Chuoi)
- All five are fallbacks with 0 to 16 ratings.
- Korokke: 70 minutes, long for a snack; shallow-fried.
- Miang Kham: butter lettuce for betel leaves; kept vegetarian (no dried shrimp, shrimp paste or fish sauce); palm sugar is a specialty item.
- Bap Xao: every source uses dried shrimp and fish sauce; taken out to keep it vegetarian, so it's buttery corn with scallion and shallot.
- Che Chuoi reads more like a dessert than a snack.
## G32 (Kolo, Acili Ezme, Cig Kofte, Manchego with Membrillo, Melitzanosalata)
- All five are fallbacks with 0 to 9 ratings.
- Kolo: oven-roasted with canned chickpeas and barley; berbere at the table; keeps about a week.
- Cig Kofte: uncooked, but 40 minutes with the soak and 15 minutes of kneading, not low-effort.
- Manchego with Membrillo: the lead is mostly a make-your-own-membrillo page; the recipe uses bought membrillo, with crackers added from the one rated version (3.13 from 23, under the floor).
- Melitzanosalata: 70 minutes (mostly unattended roasting); 2 garlic cloves instead of the lead's 5; no breadcrumbs.
## G33 (Crudites with Aioli, Black Bean Dip, Fruit Chaat, Chinese Egg Tarts, Khiyar bi Laban)
- Four of five are fallbacks; only the egg tarts have a strong lead (The Woks of Life, 4.81 from 146).
- Crudites: a mayonnaise, garlic and lemon aioli (no raw egg); Tabasco and sugar left out.
- Egg Tarts: about 120 minutes with the dough chilling (dough steps are advance prep, can be done a day ahead); makes 16.
- Fruit Chaat: black salt (kala namak) is a specialty item.
- Khiyar bi Laban: 4 cucumbers and a tub of yogurt is a lot for a snack (trim?).
## G34 (Firinda Kofte Patates, Tavuk Guvec, Tepsi Kebabi, Islim Kebabi, Etli Bezelye)
- Four of five are fallbacks with 1 to 7 ratings. All five are long: Etli Bezelye about 2 hours, Firinda Kofte 100 minutes, Islim Kebabi 95 (a project, 12 wraps), Tavuk Guvec and Tepsi Kebabi 70.
- Firinda Kofte: the lead keeps its meatballs on another page, so the mix is from a second version.
- Tavuk Guvec: thighs for breast; the 2-hour marinade cut to a seasoning step.
- Islim Kebabi: eggplant roasted instead of fried.
- Etli Bezelye: rice and dill added; not in the lead.
## G35 (Yebeg Wat, Ye Siga Alicha, Gomen be Siga, Ye Misir Alicha, Estofado de Ternera)
- All five are fallbacks with unrated or 1-rating leads; all are slow (Estofado 130, Yebeg Wat 115, Siga Alicha and Gomen be Siga 105, Misir Alicha 50).
- Ye Siga Alicha: no beef version could be read; the lead is a lamb alicha adapted to beef. That's close to the existing Yebeg Alicha (call: keep or cut).
- Yebeg Wat: 2 tbsp berbere in the pot (the lead is far hotter), more at the table; ghee for niter kibbeh.
- Estofado de Ternera: the lead shows 312 votes but no average, so it reads as unrated.
## G36 (Arroz al Horno, Caldo Gallego, Pollo a la Cerveza, Djej w Batata, Kousa Mahshi)
- Four of five are fallbacks; Djej w Batata qualifies (The Salt and Sweet, 5.0 from 68).
- All long: Caldo Gallego 130 minutes plus an overnight bean soak, Kousa Mahshi 105 (coring the zucchini), Pollo a la Cerveza 95, Djej w Batata 85, Arroz al Horno 75.
- Arroz al Horno: the lead is the Valencia tourism board's recipe; cured chorizo instead of hard-to-find morcilla.
- Caldo Gallego: collards for grelos. Pollo a la Cerveza: no rated version exists; potatoes and a flour dredge from a second version.
- Djej w Batata: thighs for drumsticks, 400°F, seven spice and cumin from the other versions.
## G37 (Freekeh with Chicken, Fasolia w Riz, Mloukhieh, Soutzoukakia, Arni Lemonato)
- Four of five are fallbacks (Soutzoukakia has no rating anywhere). Arni Lemonato leads with Not Quite Nigella (5.0 from 24).
- All long: Arni Lemonato 200 minutes plus an overnight marinade (the lead roasts 6.5 hours; cut to 2 hours covered plus 40 minutes at 425°F), Fasolia w Riz 125, Mloukhieh 100, Freekeh and Soutzoukakia 75.
- Mloukhieh: dried jute leaves from a Middle Eastern shop (or frozen whole leaves).
## G38 (Kreatopita, Tteok Galbi, Kimchi Jjim, Samgyetang, Boeuf Carottes)
- All five are fallbacks (unrated to 12 ratings).
- Boeuf Carottes: 215 minutes, a Sunday pot.
- Kreatopita: 90 minutes; thaw the phyllo the night before.
- Samgyetang: 75 minutes; fresh ginseng means a trip to a Korean grocer (dried root works); 2 Cornish hens for 4.
- Kimchi Jjim: needs aged, sour kimchi; chili at the table. Tteok Galbi marinates at least 2 hours.
## G39 (Endives au Jambon, Choucroute Garnie, Ga Ro Ti, Bo Bit Tet, Suon Ram Man)
- Four of five are fallbacks (1 to 6 ratings); Suon Ram Man qualifies (Feed the Pudge, 4.3 from 68).
- Long: Choucroute 110 minutes (a weak one-pot lead, with broth and wine from the Alsatian versions), Ga Ro Ti 95 plus a marinade, Suon Ram Man 85, Endives 65.
- Ga Ro Ti: 1 tbsp honey added from a second version for sticky skin.
- Bo Bit Tet: uses liver pate, as every source does.
## G40 (Turkey Stuffed Zucchini Boats, Turkey Enchiladas, Hayashi Rice, Lamb Ragu, Kotopoulo me Hilopites)
- Lamb Ragu: RecipeTin (5.0 from 13), a fallback; about 3.5 hours, a weekend dish.
- Hayashi Rice: canned demi-glace plus tomato juice in place of the lead's 3-hour homemade demi-glace.
- Turkey Enchiladas: 70 minutes; the lead's black beans left out; red sauce from scratch.
- Kotopoulo me Hilopites: 95 minutes; thighs browned first instead of a whole roast chicken; a near-sibling of the existing Kotopoulo Kokkinisto (with noodles instead of rice).
