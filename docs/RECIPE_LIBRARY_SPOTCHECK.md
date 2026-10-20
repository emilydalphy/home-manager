- Many Ethiopian and Turkish dishes have no version with 20+ ratings, and RecipeTin Eats (the only trusted cook for those cuisines) has none of them, so the researcher's best-read version leads (fallback_used true). Worth widening the trusted-cooks list for Ethiopian and Turkish.
- Butter written as a measure ("4 tbsp") instead of a package in the Turkish breakfasts. Check it's consistent with how the app buys butter.
- Menemen follows a sujuk version's base without the sujuk (that's Sucuklu Yumurta's dish).
- Sigara Boregi lists yufka, with spring roll wrappers as the stand-in in a step.
- Yebeg Tibs leads with a venison recipe (Hank Shaw) that lists lamb as an option. It's the only rated source.
- Key Wat keeps 2 tbsp berbere in the pot (not fully mild).
- Lahmacun runs 70 minutes for a lunch (four flatbreads bake one at a time).
- Some researchers added a lemon to the list just so the last 'Taste it, add salt then lemon' step works (Turkish stews). Possibly the writer guide's example is pushing lemons into every dish.
- Pinchos Morunos gained a garlic mayo no source has, to meet the 'something wet on every dinner plate' rule. That rule may push sauces onto dishes that don't have one.
- Many big recipe sites (Allrecipes, The Mediterranean Dish, Food Network, BBC Good Food, Bon Appetit) block fetching, so better-rated versions there couldn't be read or lead.
- Imam Bayildi runs about 2h20m in all (mostly oven time). Long for a weeknight.
- Two dishes (Pollo al Chilindron, Lentejas con Chorizo) took ratings from Wayback Machine copies of sites that block bots. I've told later researchers to skip blocked sites rather than go around the block.
- Shopping lines for oils and butter are mixed across files: some '1 bottle' with a cooking amount, some just '1/4 cup'. Worth one rule, plus a quick normalising pass before filing.
- Lemons added only for the last taste step (my wording slip, fixed at 12:30am): Fabada Asturiana, Albondigas en Salsa, Tas Kebabi, Hunkar Begendi, Nohut Yemegi, Kuru Fasulye. A cleanup pass should take those lemons out.
- Feel Good Foodie pages (Lebanese) were read through Wayback copies too (that agent started before the skip-blocked rule).
- Gougeres and Gateau au Yaourt are full batches (about 28 puffs, 8 slices) filed as 4 servings, so the shopping list buys the whole batch.
- The checker makes a dish that makes its own broth (Pot-au-Feu) say 'the cooking liquid' to avoid buying broth. It's a checker false alarm worth fixing; the wording is a little stiff.
- Hachis Parmentier lists 'Cooked beef, 1 lb' (it's a leftovers dish), so the grocery list would buy cooked beef.
- Several 'twenty minutes' dishes run longer in the honest version (Lemongrass Chicken needs a 1-hour marinade; Turmeric Fish about 50 minutes; Croque Monsieur 35). The starter list's one-liners may need updating to match.
- Canh Chua follows a 2h15m pork-rib broth lead, which sits oddly with 'light for summer'. A rating-led lead can pick the slow version.
- The lead rule favours a 5.0 from 23 ratings over a 4.7 from 500 (Japchae). Worth thinking about weighting by number of ratings.
- Some starter-list protein labels don't fit the lead (Tiganites is labelled 'eggs' but the classic batter has none).
- Salt brand: Edamame and Shiozake use Diamond Crystal kosher salt amounts (Shiozake 2 1/4 tbsp for 1 lb of salmon). With table or Morton salt that's far too salty, and the warning is only in method_notes. Fix before filing.
- Gyoza cooks a bag of frozen gyoza (the starter line says 'from the freezer'). No homemade filling.
- 'Vegetarian' labels with fish sauce in the dressing (Green Papaya Salad, Lemongrass Tofu Bowl). The label or the sauce needs a call.
- Jianbing's lead is rated only 3.7. Leads with low ratings could get a floor (for example, at least 4.0) before they can lead.
- The lead rule's one-decimal rounding lets a 5.0 from 301 ratings beat a 4.9 from 5,889 (Poha).
- Vegetable Pulao has a raita the researcher wrote (the source just says 'serve with raita').
- Vegetarian Italian dishes use Parmesan, which traditionally has animal rennet. The app's vegetarian check may or may not treat that as a conflict.
- reviewCount vs ratingCount: several leads won using reviewCount because the page had no ratingCount (Italian Wedding Soup, My Greek Dish pages). It's worth one rule.
- Pepperoni Pizza leads with a 3.3-rated source (the only readable one with 20+ ratings). Another case for a minimum rating.

## Group 91 (Tuna Melt, Caesar, Cobb, Chicken Salad Sandwiches, Grilled Cheese + Tomato Soup)
- Grilled Cheese + Tomato Soup: lead is Tasting Table (4.7, 35). A soup-only recipe would have won the rule, but it was left out because it doesn't make the sandwich.
- Tuna Melt: no version with 20+ ratings could be read (big sites blocked), so it's a fallback: Well Plated, 16 ratings. It uses Greek yogurt instead of mayo.
- Swaps: Cobb uses paprika (not Cajun); Grilled Cheese uses sharp cheddar (not Comté) and no chile; Caesar leaves out the optional bacon/egg.
- Storage times written by us: Caesar dressing 5 days, chicken salad 3 days.

## Group 95 (Gumbo, Red Beans and Rice, Jambalaya, Shrimp and Grits, Clam Chowder)
- Shrimp and Grits: honest time is about 45 min, not the starter line's 30.
- Gumbo: about 2.5 hours (the roux is slow). Made with rotisserie chicken.
- Jambalaya: the lead's shrimp and okra are left out to match "rice, sausage and chicken".
- Red Beans: soak overnight; uses 12 oz of dried beans.
- Clam Chowder uses chicken bouillon (from the lead). One source only showed a reviewCount.
- In all the Cajun dishes the hot sauce is served on the side.

## Group 93 (Ribs, Cheeseburger Pasta, Tuna Noodle Casserole, Chicken and Rice Casserole, Chicken Tenders)
- Tuna: RecipeTin's "Tuna Mornay" would win the rule but it's a different dish, so it was left out. Spend With Pennies leads.
- Ribs + Tuna: the leads say they serve 6, but each full batch is written as dinner for 4.
- Chicken and rice is the dump-and-bake version (no browning step). Water isn't on the ingredient list.
- Cheeseburger pasta: the second source has only 2 ratings, because most sites blocked us.

## Group 94 (Stuffed Peppers, Pork Tenderloin, Pork Chops with Applesauce, Fish Sticks, Baked Salmon)
- Pork Chops: the weakest lead (Taste of Home shows only a reviewCount of 77). Its apples are mashed into a chunky sauce.
- Stuffed Peppers: all beef (the lead uses half sausage).
- Added by us: a pan sauce for the tenderloin, and tartar sauce for the fish sticks.
- Salmon doneness: this one says 140°F, but Salmon en Papillote says 130°F. Pick one number for salmon?
- The serving-line check is inconsistent: it flags sides like "potatoes" in some lines and not in others.

## Group 98 (Mac and Cheese, Chicken Pot Pie, Turkey Chili, Classic Beef Chili)
- Sizing: the leads call these 6–8 servings, but here they're 4 dinner bowls (12 oz pasta, 1 lb meat). The pot pie is cut down from a deep dish to a regular 9-inch pie.
- Mac and Cheese: the sauce is evaporated milk plus eggs (no flour), with a panko-Parmesan top.
- Beef chili: uses a lot of cumin and 1 1/2 tbsp sugar, as the lead does. Onion and garlic were added.
- Turkey chili: plain tomatoes instead of Rotel; jalapeño on the side.
- Pot pie: store-bought crusts. The taste step comes before the pie is sealed.

## Group 97 (Drumsticks, Pulled Pork, Sloppy Joes, Turkey Burgers, Smash Burgers)
- Pulled pork is sized for 4 (2 lb shoulder), though the starter line says "feeds a crowd". The slaw is our own simple one.
- Turkey burgers are made in a skillet (the lead grills), with sweet Italian turkey sausage in the mix.
- Pulled pork and sloppy joes have only 2 sources each, because others were blocked.

## Group 96 (Chicken Noodle Soup, Beef Stew, Pot Roast, Chicken and Dumplings, Oven-Fried Chicken)
- Pot roast: potatoes taken from a second source (the lead has none). They go in for the last hour.
- Chicken and dumplings: made with bone-in thighs, browned first (our addition).
- Oven-fried chicken: the lead has just 25 ratings. Hot sauce on the side; there's no taste step. The list says "1 tbsp" of milk.
