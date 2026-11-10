# Recipe library: 40 more snacks (2026-11-09)

Emily, 2026-11-09: "can you add another 40 snacks to the recipe database. make sure to run the recipe adding process for i[t]".

Branch `recipe-library-snacks-40-2026-11-09`, from main f1ec75e. The list is now 2,137 dishes, with 233 snacks (was 193). Every new snack has a recipe file and a drawn plate. **Not merged and not filed into the live library yet.**

## How they were picked

- Every pick was checked against all 2,097 dishes on the list, including under translated names. Two were swapped out during the round:
  - **Fandisha** (Ethiopian coffee-ceremony popcorn): every source made it as plain popcorn, the same as the Stovetop Popcorn already on the list.
  - **Kita**: already on the list as a breakfast.
  - **Potato Skins** took the place.
- The thinner snack cuisines went first: Vietnamese, Turkish, Greek, Thai, French and Spanish. American, the biggest, got only 3.
- 28 savoury and 12 sweet.
- Nut-free throughout. Where a source used nuts, they were left out and the method_notes say so: pine nuts in Irmik Helvasi, walnuts in Patzarosalata, walnut oil in Cervelle de Canut, peanuts in Shakarkandi Chaat, a nut garnish in Tteok Kkochi, and peanut filling in Tang Yuan. Coconut stays in Khandvi (garnish) and Tang Yuan (filling fat).
- No special pans. Everyday stand-ins are written into the steps: a skillet for a grill, a colander in a covered pot for a steamer, a glass for a cutter, and the broiler for a grill.

| Cuisine | New snacks |
|---|---|
| Vietnamese | Che Bap, Banh Tieu, Bap Nuong Mo Hanh, Khoai Lang Chien |
| Turkish | Atom, Pisi, Patates Koftesi, Irmik Helvasi |
| Indian | Masala Papad, Khandvi, Shakarkandi Chaat, Paneer Pakora |
| Japanese | Isobe Yaki, Imo Mochi, Purin, Hiyashi Kyuri |
| Greek | Patzarosalata, Kolokithakia Tiganita, Feta me Meli |
| French | Cervelle de Canut, Rillettes de Thon, Sables |
| Chinese | Di Gua Qiu, Liang Ban Mu Er, Tang Yuan |
| Italian | Panelle, Fett'unta, Grissini |
| American | Ham and Cheese Pinwheels, Broccoli Cheddar Tots, Potato Skins |
| Thai | Kluay Buat Chi, Mamuang Nam Pla Wan |
| Spanish | Berenjenas con Miel, Piquillos Rellenos de Queso |
| Korean | Eomuk Kkochi, Tteok Kkochi |
| Ethiopian | Suf Fitfit |
| Mexican | Taquitos de Papa |
| Lebanese | Ka'ak |

## The process

1. Added to `data/recipe_library_seed.json`, with the seed test count raised to 2,137.
2. Researched and written by sub-agents under the writer rules (`library_batch.py rules`). They read 2 to 3 versions of each dish and wrote Pomona's own version for 4 servings.
3. All 40 pass `library_batch.py check`. One "look" note is correct as written: Eomuk Kkochi's broth is made in the recipe.
4. Each "why" line was rewritten against the recipe as written.
5. Plates were drawn in `plates/specs/part-36.json` under all 42 plate rules, then built with `build_plates.py`, giving 2,138 drawings. Existing drawings are unchanged.
6. The full test suite passed. The run caught: goat cheese bought as "1 log", which the shopping list can't read, so it is now "1 package (4 oz)"; and a plate-matcher bug that read "goat cheese" as goat meat and drew Patzarosalata as Lamb Kleftiko. The matcher is fixed, with a test. Mamuang Nam Pla Wan is filed under shrimp, since dried shrimp is its protein.

**Fallbacks:** 25 of the 40 lead with a version that has fewer than 20 ratings. As before, these are mostly Vietnamese, Turkish, Thai, Spanish and Korean snacks, which are thinly rated online. RecipeTin Eats, the trusted cook for most of those cuisines, has none of them.

## Calls for Emily

1. **Eomuk Kkochi vs Odeng Tang:** the list already has Odeng Tang, a fish cake soup. Eomuk Kkochi is the street-snack version: skewers in a small cup of broth. Keep both?
2. **Long snacks:** Potato Skins (95 min, mostly the potatoes baking), Patates Koftesi (90), Ka'ak (90 plus a rise) and Patzarosalata (70 plus a 3-hour chill). Keep them?
3. **Hiyashi Kyuri:** no recipe uses this exact title. It is the Japanese festival dish of whole cucumbers chilled in brine on a stick, written from the versions of that dish.
4. **Coconut:** it stays in Khandvi (garnish) and Tang Yuan (filling fat). Fine for nut-free?
