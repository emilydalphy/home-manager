# Recipe library: the second 200 (2026-10-25)

Emily asked to "make another 200 recipes based on the recipe rules". She chose to finish the 100 paused on 2026-10-20 and add 100 new dishes, with more quick dinners and more for the thinner cuisines. The starter list is now 845, and every dish on it has a recipe file.

Written by 41 researcher agents from the run brief (docs/RECIPE_LIBRARY_RUN.md plus later lessons: a lead must be a version of the dish, and a site that blocks the reader is out). All 200 pass `library_batch.py check`. The 13 "look" lines are broth or stock the dish makes itself, or a misread split. A sweep found no "more" after a time, no "tip", no "because" tags and no household words. None of this has been filed into the live library.

## Calls for Emily
1. **Bistec a la Mexicana:** the lead rule picked a 45-minute covered simmer (Cup of Yum, 4.9 from 100, a user-submitted site), so it takes 90 minutes plus salting. The quick strip versions rate low (3.58 from 14). Keep it, or swap to the quick version?
2. **Siyadieh** has no sauce, because none of its three sources serves one. This sets the "something wet on the plate" rule against the "no invented sauce" rule.
3. **Tofu Steak:** RecipeTin's sesame-crusted teriyaki tofu steak (4.82 from 32) was treated as a different dish. If it counts as the same dish, it should lead.
4. **Weak leads:** about 90 of the 200 use the fallback, leading with versions that have fewer than 20 ratings or none. Spanish, Turkish, Ethiopian, Lebanese and Thai home dishes are thinly rated online, and the big sites (Allrecipes, Serious Eats, The Mediterranean Dish, Maangchi, Spanish Sabores) block the reader. For Ethiopian and Turkish, RecipeTin (the only trusted cook on the list) has nothing, so the trusted-cook list does no work there. This is the open "Ethiopian/Turkish trusted cooks" call.
5. **Leads that count reviews, not ratings:** My Greek Dish, A Family Feast, ATK and others, the same pattern as before.
6. **Older print-page source URLs:** about 8 files from earlier rounds (Baked Feta, Amatriciana, Gomen, Gozleme, Khai Jiao, Levrek Bugulama, Sausage and Lentil Stew, Shakshuka) point their sources at /print/ pages. In this round they turned out to be 404s, not blocks, but they're worth a look before filing.
7. **The run doc says to compare ratings to one decimal; the check compares them as written.** The check is the app's rule, so it won (Cantonese Lemon Chicken).

## Changes outside the recipe files
- Pad Prik Gaeng was the same dish as the existing Pad Prik King, so it was swapped for Pad Makheua Yao.
- "Buddha's Delight" (from the 45 gap-fillers) was refused by the library's possessive-name rule, so "buddha" was added to the shared possessives (like "shepherd"). Without it, the dish wouldn't file.
- Six list notes promised times the recipes don't meet ("done in 20 minutes" and the like) and were reworded.
- The seed test now expects 845.

## Per batch
## g6 (Ethiopian Spiced Lentil Soup, Doro Alicha, Ye'Duba Wat, Shimbra Asa, Injera Firfir)
- All five are fallbacks with unrated leads; RecipeTin (the only trusted cook for Ethiopian) has none of them.
- Doro Alicha: ghee plus cardamom stand in for niter kibbeh; 1/2 cup white wine; 95 minutes.
- Duba wat uses butternut squash; berbere kept to 1 tbsp.
- Shimbra Asa: deep-fried dumplings, 80 minutes; berbere cut from 1/2 cup to 2 tbsp.
## g3 (Breakfast Quesadillas, Ground Beef Stroganoff, Chicken Bacon Ranch Wraps, Pan-Seared Salmon, Chinese Breakfast Egg Pancake)
- Quesadillas: eggs scrambled with green onion and no meat. The lead cooks the egg flat and adds meat.
- Salmon: skin-on and seared, though the lead is skinless; 22 minutes, not 15.
- Ranch wraps: the lead is just over the line (4.8 from 25 ratings). A whole 8 oz bag of mozzarella for 4 wraps.
- Egg pancake: makes about 12 (3 each), white pepper in place of Sichuan pepper, chili oil on the side.
## g5 (Shredded Potato Stir-Fry, Shrimp and Egg Stir-Fry, Siga Wat, Atkilt Wat, Ye'Kik Wat)
- Potato stir-fry: a fallback (4.92 from 12 ratings); the dried chilies go on the side.
- Shrimp and egg: 10 eggs to 1/2 lb shrimp; 40 minutes counting the rice, though the stir-fry takes 10.
- Siga Wat: 2 h 15 min; ghee plus cardamom stand in for niter kibbeh; a third of the berbere moved to the side.
- Atkilt Wat: the lead uses no berbere (the other two sources do).
- Ye'Kik Wat: unrated fallback lead; yellow split peas rather than the lead's soaked green ones.
## g2 (Ham and Cheese Sliders, Turkey Sweet Potato Skillet, Southwest Chicken Salad, Corn Fritters, Dutch Baby)
- Turkey skillet: a fallback (5.0 from 7 ratings); the chipotle-honey sauce goes on the side; 40 minutes, not 30.
- Southwest salad: a fallback (10 ratings). Black beans instead of the lead's pinto beans and zucchini; no jalapeño.
- Sliders: 12, three each. Corn fritters: no jalapeño.
- Dutch baby scaled from 3 eggs to 4 for four people, in one 10-inch skillet.
## g4 (Sesame Balls, Moo Shu Pork, Moo Goo Gai Pan, Cantonese Lemon Chicken, Chicken and Sweet Corn Soup)
- Sesame balls: 90 minutes, slow two-stage frying, makes 8.
- Moo shu: the only lead with 20+ ratings has no egg, so the egg comes from Woks of Life. Uses store-bought mandarin pancakes.
- Lemon chicken: the checker compares ratings as written (4.95 beats 4.94), not to one decimal as the run doc says, so Woks of Life leads over RecipeTin. Double fry in 6 cups of oil.
- Corn soup: poaches its own chicken breast (35 minutes); leftover chicken makes it about 15.
## g8 (Sole Meuniere, Chicken Provencal, Lentils with Sausage, Soupe au Pistou, Pan Bagnat)
- Sole: 1.5 lb for 4 (the lead uses 8 oz for 2); cooked in two batches.
- Chicken Provencal: the lead's 49 may be reviews, not ratings.
- Lentils with sausage: RecipeTin's one-pan bake, with cumin seeds kept.
- Pistou: 50 minutes for a lunch; needs a food processor, or chop by hand.
- Pan bagnat: weak fallback (4.56 from 9). Pressed at least 1 hour ahead. No anchovies.
## g7 (Kategna, Dabo Kolo, Poulet Basquaise, Cassoulet, Blanquette de Veau)
- Four of the five are weak fallbacks (only blanquette has a lead with 20+ ratings).
- Cassoulet: about 4 h 15 min plus an overnight soak; no duck confit. A 5.0-from-29 page (Mashed) was dropped as a near-dish: a 15-minute stovetop stew with no bake.
- Basquaise: half the Espelette goes to the table; paprika with a pinch of cayenne as the swap.
- Blanquette: veal shoulder, 2 h 40 min; about 4 cups of broth left over for soup.
- Dabo kolo: makes a mild bowl and a spicy bowl.
## g1 (Garlic Butter Shrimp and Rice, Pork Chops with Mushroom Gravy, Crispy Chicken Sandwiches, Baked Potato Soup, Cajun Shrimp Pasta)
- Shrimp and rice: 40 minutes, not 20, because the rice cooks from scratch; stock in place of wine; a near-dish lead was dropped.
- Pork chops: an unrated fallback lead.
- Chicken sandwiches: buttermilk the night before (or 3 hours ahead); deep-fried in 3 cups of oil, which is a lot for a lunch.
- Potato soup: vegetarian (no bacon, vegetable broth); the potatoes are microwaved.
- Cajun pasta: Cajun seasoning cut to 4 tsp; 8 oz penne for 4, which may be light.
## g10 (Trout Amandine, Chicken Chasseur, Salade Lyonnaise, Tarte Flambee, Navarin d'Agneau)
- Trout, Lyonnaise and Tarte flambee are fallbacks (19, 4 and 7 ratings).
- Chasseur: uses brandy; the lead's mashed potato isn't on the list, so the serving line names no side.
- Tarte flambee: two thin tarts at 500°F, with a yeast-free dough.
- Navarin: about 3 h 10 min.
## g9 (Tartine with Ham and Butter, Pain Perdu, Clafoutis, Financiers, Steak au Poivre)
- Tartine: every source closes it into a baguette sandwich, so this one is closed too; a whole stick of butter.
- Pain perdu: yolks only, which leaves 4 whites.
- Financiers: whites only, which leaves 3 yolks; made in a mini muffin tin, about 16.
- Clafoutis: a whole dish, 8 wedges.
- Steak au poivre: filet mignon (expensive); cognac added off the heat; no side.
## g14 (Home-Style Chicken Curry, Egg Bhurji, Aloo Tikki, Chicken 65, Dal Palak)
- Five strong Swasthi leads (200 to 980 ratings).
- Chicken curry: basmati rice added; neither page read names a side. Kept as the usual pairing; flag if you'd rather it stay off.
- Chicken 65: deep-fried in 4 cups of oil; marinated at least 1 hour ahead.
- Dal palak: on the stove for 40 to 45 minutes (the sources use a pressure cooker).
## g15 (Mushroom Matar, Amritsari Fish, Sabudana Khichdi, Tuscan Chicken, Pasta al Limone)
- Amritsari fish: basa rather than the lead's salmon; mint yogurt chutney from the other two sources; fried in 2 cups of oil.
- Sabudana: soaks overnight.
- Tuscan chicken: over fettuccine; 40 minutes counting the pasta.
- Pasta al limone: Parmesan instead of Pecorino (the vegetarian/rennet call still applies).
## g13 (Chicken Gyros, Paidakia, Greek Chickpea Salad, Saganaki, Youvarlakia)
- Gyros: marinate 2 hours or overnight. Paidakia: marinate 3 hours or more; roast potatoes from the lead; 140°F (medium), though Greeks usually cook them through.
- Youvarlakia: chicken broth, not the lead's water.
- Chickpea salad and saganaki: re-pointed from print pages to the normal pages (they turned out to be 404s or untried pages, not blocks). The salad's lead is now Ambitious Kitchen, with 1 can of chickpeas for 4, which may be light for packed lunches.
- Not in this batch: about 8 older library files (Baked Feta, Amatriciana, Gomen, Gozleme and others) also have print-page source URLs. Some may be from 404s rather than blocks. Worth a look before filing.
## g12 (Spanakorizo, Horiatiki Pita Wraps, Greek Lentil Salad, Bougatsa, Koulourakia)
- Four of the five leads count reviews, not ratings (My Greek Dish, A Family Feast), the same pattern as ATK before.
- Pita wraps: weak fallback (1 review); uses store-bought tzatziki.
- Bougatsa: 90 minutes, phyllo thawed overnight; a weekend breakfast.
- Koulourakia: about 36 cookies, with baking powder in place of baking ammonia.
## g11 (Greek Chicken Thighs with Orzo, Youvetsi, Kotopoulo Kokkinisto, Garides Youvetsi, Lamb Souvlaki)
- Youvetsi: about 2 h 50 min with beef chuck; Parmesan for kefalotyri.
- Kokkinisto: weak fallback (2 ratings).
- Garides: the lead is ATK's shrimp and orzo bake ("4" as a whole number); saffron and peas left out.
- Lamb souvlaki: marinate 12 to 24 hours; 140°F medium-rare.
## g16 (Spaghetti al Tonno, Pasta e Piselli, Chicken Scarpariello, Fagioli all'Uccelletto, Pasta con Zucchine)
- Pasta e piselli: the lead (GialloZafferano) has no pancetta; it comes from two other sources.
- Scarpariello: an unrated Lidia fallback; hot cherry peppers on the side.
- Fagioli: an unrated fallback; dried beans soaked overnight, then 1 h 45 min. A canned-bean weeknight version might suit better.
- Pasta con zucchine: Parmesan in the sauce (the lead only suggests it).
## g18 (Katsudon, Japanese Fried Rice, Tantanmen, Chicken Soboro Don, Tuna Mayo Onigiri)
- Katsudon: fries its own cutlets (4 cups of oil); 75 minutes.
- Fried rice: the rice is cooked at least 2 hours ahead.
- Tantanmen: half the doubanjiang goes in; chili oil at the table; tahini for sesame paste.
- Onigiri: 8 from one can of tuna; eat within 6 hours.
## g17 (Salmon Teriyaki, Saba Shioyaki, Buta Kakuni, Chicken Yakitori, Tofu Steak)
- Buta kakuni: about 4 h 50 min; a weekend project.
- Yakitori: broiled; needs 24 skewers soaked 30 minutes.
- Tofu steak: an unrated fallback. RecipeTin's sesame-crusted teriyaki tofu steak (4.82 from 32) was treated as a different dish and left out. If you count it as the same dish, it should lead.
## g19 (Japanese Potato Salad, Rolled Omelette Sandwich, Natto Gohan, Dorayaki, Butadon)
- Omelette sandwich and natto gohan are weak fallbacks (3 and 11 ratings).
- Dorayaki: a whole batch of 6 (default_servings 6, like cornbread and gougeres).
- Butadon: the caramel step spatters, and the recipe warns about it.
## g23 (Ojingeo Bokkeum, Kongguksu, Dakjuk, Gyeran Mari, Dubu Kimchi)
- All five lead with Korean Bapsang (20+ ratings).
- Kongguksu: homemade soy milk; soak overnight, then chill 1.5 hours.
- Dakjuk: close to an hour on the morning, which is long for a breakfast.
- Dubu kimchi: no rice (neither source serves it).
## g21 (Dakgangjeong, Bossam, Kongnamul Bap, Godeungeo Jorim, Dubu Jorim)
- Dakgangjeong: 2 lb of thighs, double-fried in 4 cups of oil, 85 minutes, no rice.
- Bossam: puts a jar of instant coffee on the shopping list for 1 tsp in the brine.
- Godeungeo jorim: an unrated fallback; the sauce is much milder than the lead's.
- Dubu jorim: Tasting Table (5.0 from 30) beats Korean Bapsang (4.8 from 140) on rating.
## g20 (Ochazuke, Ebi Chili, Tsukune, Chicken Mizutaki, Hiyayakko)
- Ochazuke: the green tea version.
- Ebi chili: the heat is cut in the pan (no side chili in any source).
- Tsukune: no shiso; 60 minutes with the sauce simmer and the skewer soak.
- Mizutaki: cooked on the stove; napa cabbage, of which about a quarter is used.
- Hiyayakko: a weak RecipeTin Japan lead (2 ratings); no bonito flakes, to keep it vegetarian.
## g22 (Bulgogi Rice Bowls, Kimchi Pancakes, Gyeranbap, Hobak Jeon, Gyeran Ppang)
- Gyeranbap, hobak jeon and gyeran ppang are weak fallbacks (13, 7 and 10 ratings).
- Kimchi pancakes: most kimchi has fish in it (the notes say to buy vegan kimchi if that matters).
- Gyeran ppang: needs a mini loaf pan; American cheese.
## g25 (Batata Harra, Labneh Wraps, Kaak, Fatayer bi Sabanekh, Atayef)
- Batata harra and labneh wraps are weak fallbacks (3 and 6 ratings). Batata harra is oven-roasted, 3 lb as a lunch.
- Kaak: the dough starts about 3 hours before breakfast (2 hours of rising).
- Fatayer: 2.5 hours ahead for the dough; makes 10.
- Atayef: ricotta filling; syrup on the side.
## g24 (Sogogi Muguk, Chicken Fatteh, Lamb Kofta with Tahini, Siyadieh, Makloubeh)
- Fatteh, siyadieh and makloubeh are fallbacks (7 to 12 ratings).
- Siyadieh: no sauce, because none of the three sources serves one (the "something wet on the plate" rule vs. no invented sauce). Your call.
- Makloubeh: about 2 h 25 min, cauliflower deep-fried in 4 cups of oil.
- Kofta: the Aleppo pepper goes to the table.
## g30 (Huevos Rotos, Torrijas, Huevos a la Flamenca, Champinones al Ajillo, Pimientos de Padron)
- Four of the five are weak fallbacks (0 to 2 ratings). Spanish ratings are thin, and the big Spanish sites block the reader.
- Garlic mushrooms: RecipeTin's rating covers its whole "5 Easy Tapas" card.
- Flamenca: baked in the oven; no potatoes, no asparagus, no bread.
## g28 (Shrimp Fajitas, Entomatadas, Sopa de Lentejas, Pollo en Pepitoria, Bacalao a la Vizcaina)
- Pepitoria: the lead is a Thermomix (Cookidoo) page whose steps couldn't be seen; the method comes from Mission Food, and 1 cup of broth was added. Needs a blender. Weakest lead in this run.
- Bacalao: the salt cod soaks 24 to 36 hours ahead; weak fallback (1 rating).
- Entomatadas: weak fallback (14 votes), with vegetable broth.
## g26 (Daoud Basha, Moghrabieh, Fatteh Hummus, Balila, Bazella w Riz)
- Daoud basha and fatteh hummus are weak fallbacks (no ratings and 4).
- Moghrabieh: 90 minutes, with bone-in thighs.
- Balila: the lead has no garlic or lemon; both come from two other sources, since the dish is known for them.
- Bazella w riz: about 2 h 25 min on the stove (the lead uses a pressure cooker).
## g29 (Arroz con Pollo, Pork Tenderloin with Romesco, Espinacas con Garbanzos y Bacalao, Salmorejo, Bocadillo de Calamares)
- All five are weak fallbacks with 0 to 2 ratings (the Spanish sites with more ratings blocked the reader).
- Espinacas: canned chickpeas and fresh cod (the lead's own shortcuts) instead of a day of soaking.
- Salmorejo: chilled at least 2 hours ahead; no Serrano ham, so it stays vegetarian.
- Romesco: needs a food processor.
## g31 (Patatas a la Riojana, Sopa de Picadillo, Ensalada Mixta, Gaeng Som, Jungle Curry)
- All five are weak fallbacks (none had 20+ ratings; RecipeTin has none of them).
- Sopa de picadillo: a shortcut with bought broth and thighs, instead of the lead's hours-long bone broth.
- Gaeng som: central style with tamarind; galangal for fingerroot; heat cut, with bird's eye chilies on the side.
- Jungle curry: needs Thai eggplants and krachai from a jar; heat cut.
## g27 (Sabanekh bi Lahme, Tacos de Papa, Pollo Asado, Bistec a la Mexicana, Chorizo and Potato Tacos)
- Bistec: the lead rule picked Cup of Yum (4.9 from 100, a user-submitted site), a 45-minute covered simmer, so it's 90 minutes plus salting, not quick. The quick strip versions rated lower (3.58 from 14). Swap the lead or keep it?
- Tacos de papa: 16 tacos fried in four batches, 65 minutes; store-bought salsa.
- Pollo asado: marinate 1 to 4 hours; grill pan.
- Sabanekh and chorizo tacos are fallbacks.
## g32 (Pad Krapow Moo Saap, Thai Peanut Chicken, Thai Red Curry Salmon, Pad Pak Boong, Gai Pad Khing)
- Peanut chicken and red curry salmon are weak fallbacks (4 and 2 ratings); every better-rated version blocked the reader.
- Pad pak boong: vegetarian oyster sauce; a light dinner of greens and rice.
- Gai pad khing: thighs, plus wood ear mushrooms from Marion's Kitchen.
## g33 (Thai Pineapple Fried Rice, Yum Woon Sen, Jok, Kai Jeow Moo Saap, Thai Coconut Pancakes)
- Four weak fallbacks (3 to 19 ratings); only the fried rice has a strong lead (RecipeTin).
- Jok: 85 minutes, long for a breakfast. A blended-rice version gets it to about 20.
- Kai jeow: four omelettes fried in about 1 cup of oil in all.
- Coconut pancakes: need a takoyaki or aebleskiver pan.
## g35 (Etli Nohut, Hamsi Tava, Firinda Tavuk, Mercimek Koftesi, Coban Salatasi)
- Four weak fallbacks; only the mercimek köftesi lead has 20+ ratings.
- Etli nohut: 100 minutes. Hamsi: fresh anchovies are hard to find.
- Mercimek köftesi: a source read through a blocked site's shop mirror was removed.
## g36 (Simit, Kaygana, Revani, Ezogelin Corbasi, Kisir)
- All five are weak fallbacks (0 to 13 ratings). Turkish has the same trusted-cook gap as Ethiopian: RecipeTin has none of them.
- Simit: start the dough about 1.5 hours before breakfast; 8 rings.
- Revani: bake at least 4 hours ahead; 16 squares; no clotted cream.
- Ezogelin: vegetable broth; needs a stick blender and mild Turkish pepper paste.
## g34 (Pla Rad Prik, Moo Ping, Kai Palo, Kuay Teow Kua Gai, plus Pad Makheua Yao)
- All weak fallbacks (1 to 18 ratings).
- Pad Prik Gaeng was the same dish as the existing Pad Prik King with the same lead, so it was dropped and Pad Makheua Yao (Thai basil eggplant) took its place on the list.
- Moo ping: marinate overnight; pork shoulder; skewers; no dipping sauce, because no source gave one.
- Kai palo: 2 hours; the lead's kale left out.
## g37 (Piyaz, Adana Kebab, Kiymali Pide, Taze Fasulye, Patatesli Yumurta)
- Adana, taze fasulye and patatesli yumurta are weak fallbacks.
- Adana: chill at least 1 hour ahead; red pepper flakes at the table; needs flat skewers (or shape flat logs).
- Pide: RecipeTin's lead, made with beef (the lead's own swap); about 2 hours of rising, done ahead.
- Taze fasulye: 100 minutes, mostly unattended.
## g38 (Ca Ri Ga, Bo Luc Lac Rice Bowls, Ca Chien Sot Ca, Ga Xao Sa Ot, Bun Ga Nuong)
- Three Wok & Kin fallbacks (5 to 18 ratings).
- Ca ri ga: the lead's 10-person pot was cut down; sugar instead of condensed milk; baguette.
- Ca chien: fillets in place of a whole flounder.
- Bun ga nuong: RecipeTin (4.98 from 254); marinate 1 hour or overnight.
- Pad Makheua Yao: lead is Thai Cookbook TV ("4 stars from 5,605 reviews", plain page text with no recipe data); vegetarian oyster sauce; chilies on the side; 2 lb of eggplant over rice, with no protein.
## g39 (Suon Nuong, Goi Cuon Tom, Banh Mi Chay, Xoi Ga, Banh Chuoi Nuong)
- Banh mi chay, xoi ga and banh chuoi are weak fallbacks with unrated leads.
- Suon nuong: marinate overnight (or at least 3 hours); a full broken-rice plate.
- Xoi ga: a rotisserie chicken, about half of it used; needs a rice cooker; rice soaked ahead.
- Banh chuoi: 100 minutes; puts a bottle of white wine on the list for 1 tbsp.
## g40 (Bun Bo Xao, Pho Xao, Com Ga Hoi An, Canh Ga Chien Nuoc Mam, Canh Bi Do)
- Four weak fallbacks (3 to 11 ratings); bun bo xao is the only one with 20+.
- Com ga Hoi An: 1 h 45 min, a whole poached chicken with a bone soup.
- Wings: deep-fried in 5 cups of oil.
- Canh bi do: 8 oz of pork for 4, a light lunch.
