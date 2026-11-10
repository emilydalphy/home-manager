# Plate parts

A plate is an ordered JSON list of parts, drawn bottom to top, on a 100 by 100 grid. The plate's well is a circle
of radius 37 at (50, 50). Keep food inside it. Start every plate with `well` or `pool`. Use 3 to 8 parts.

Base: `["well"]`  `["pool", colour]`  `["pool", colour, radius]` (sauce, broth or a filled dish)

Mounds: `["rice", cx, cy, rx, ry, colour, grainColour]`  `["mash", cx, cy, rx, ry, colour, speckColour]`
`["band", y, colour, grainColour]` (a stripe of topping, y 33, 50 or 67)

Scattered (r = spread from centre; cx, cy optional, default 50, 50):
`["strips", colour, count, length, width, r, cx, cy]` shredded or sliced food. r + length/2 must be 34 or less.
`["slices", colour, edgeColour, count, r]` thin meat slices
`["nuggets", colour, highlightColour, count, size, r, cx, cy]` glazed pieces, meatballs, beans, chunks
`["cubes", colour, count, size, r, cx, cy]`  `["dots", colour, count, size, r, cx, cy]`
`["rings", colour, count, r, cx, cy]` sliced scallion or chili
`["leaves", colour, count, size, r]`  `["frisee", colour, count, size, r]`  `["cashews", colour, count, r]`
`["rounds", colour, innerColour, count, size, r]` sliced potato, tomato, cucumber

Single items: `["poached", x, y]`  `["yolk", x, y]`  `["wontons", count, r]`  `["bokchoy", count, r]`
`["patties", colour, ridgeColour]`

Whole dishes: `["baguette"]` `["flautas"]` `["phyllo"]` `["loaf"]` `["ringbread"]` `["sandwiches"]`

Whole dishes with options. The bare form is the approved look. Every extra value is optional, and `""` drops that item.
`["skewers"]` golden chicken pieces with a small yogurt dollop.
`["skewers", colour, ridgeColour, shape]` your own meat colour, no dollop. Colour can be a list, repeated along the sticks, for fruit or caprese. Shape is `"block"` (default), `"ball"` (meatballs, fruit) or `"long"` (one pressed kofta on each stick). Lamb and beef: #6B3A25, #4A2415. Pork: #C9885A, #96592F.
`["tacos"]` dark beef filling, red and yellow flecks, lettuce.
`["tacos", filling, fleck1, fleck2, lettuceColour]` your own filling. Flecks are left out unless you name them. Lettuce stays unless you pass `""`, or pass a colour such as red cabbage #8E3A8E. These sit slightly further in than the bare form. Put toppings at (32, 44), (51, 36), (63, 48).
`["toast"]` yellow topping with pale specks and green flecks.
`["toast", topping, speck, fleck]` your own topping. `["toast", "", "", ""]` is plain toast to build on.
`["cups"]` egg cups with red and green flecks.
`["cups", fleck1, fleck2, fill, crust]` your own flecks and colours, for oatmeal cups or small cupped pancakes.

Drinks: `["glass", colour, foamColour]` on a `["well"]`

Newer whole foods (add sides or toppings around them, using the small parts above):
`["wrap", tortillaColour, filling1, filling2]` wraps, burritos, rolled sandwiches
`["pizza", sauceColour, topping1, topping2]`
`["pancakes", colour, syrupColour]`
`["flatbread", colour, charColour]` paratha, naan, crepes, quesadilla, injera, pita
`["dipbowl", colour, oilColour]` hummus and dips; put dippers around it with cubes, strips or wedges at r about 31
`["bowl", colour]` a small bowl of loose food such as snacks or cereal; add parts with r up to 20
`["pastries", colour, shadeColour, "tri"]` or `"half"` samosas, fatayer, empanadas, dumplings, calzones
`["stuffed", skinColour, fillingColour]` stuffed zucchini, peppers, eggplant
`["cutlet", colour, crumbColour, cx, cy, rx, ry]` a large breaded or seared piece: schnitzel, parmesan, chicken breast, fish fillet
`["block", colour, shadeColour, x, y, width, height]` tofu, cheese, bark, cake slab
`["rolls", colour, shadeColour]` spring rolls, egg rolls, croquettes
`["wedges", colour, skinColour, count, r]` apple, orange, melon or potato wedges. Optional cx, cy place them off-centre; count 1, r 0 puts one slice exactly where you want it. Apple: #F4E7A0, #C9402A.

`["noodles", colour, shadeColour, width, r]` a twirled nest of long strands. Width 2 to 3 for spaghetti and thin noodles, 5 for pappardelle and udon. r about 26.
`["wedgering", colour, skinColour, count, r]` slices fanned evenly around the plate, leaving the centre clear for a small dip. Use r 25.
`["potatoes", skinColour, fleshColour, toppingColour]` two whole baked potatoes, oval, split and filled. Add chives, bacon or cheese with small parts.
`["chop", colour, fatColour, x, y, rotation]` a bone-in pork or lamb chop with a fat edge, bone and grill marks.
`["bacon", x, y, rotation]` two wavy rashers of bacon with fat stripes.
`["fillet", colour, flakeColour, searColour, x, y, rotation, scale]` a fish fillet with flaky lines, seared edge and glossy highlight. Salmon: #F0825A, #F9C3A6, #B8602A.
`["tortellini", colour, shadeColour, count, r]` little folded rings of filled pasta.
`["omelette", colour, shadeColour, filling1, filling2]` a folded omelette with filling peeking out.
`["steak", colour, fatColour, x, y, rotation, scale]` a seared steak with grill marks.
`["bread", crustColour, crumbColour, filling1, filling2]` a split crusty roll with your own filling colours. Use this instead of `baguette` when the filling is not banh mi. For chocolate: filling1 #5A3320.
`["shrimp", colour, stripeColour, count, r]` curled prawns with stripes.
`["ribbons", colour, shadeColour, width, count]` wide flat noodles folding back and forth: chow fun, pappardelle, pad see ew. Width 6 to 7, count 4 to 6. Noodles must contrast with what is under them.
Shrimp also takes optional cx, cy: `["shrimp", colour, stripeColour, count, r, cx, cy]`. Use count 4 to 5 and r about 19. Use it for every shrimp or prawn dish.
`["eggplant", count, r, cx, cy]` batons of eggplant with glossy purple skin and pale flesh.

Newest parts. Use these whenever the food is in the recipe, and keep their colours so the same food looks the same on every plate.
`["mushroom", capColour, count, size, r, cx, cy]` sliced mushrooms with cap, gills and stem. Size 5.5 to 8.5. Cap: cremini #9C7452, button #BFA27E, shiitake #6B4A33.
`["broccoli", count, size, r, cx, cy]` florets with a pale stalk and bumpy crown. Size 5.5 to 9. Colours are fixed.
`["drumstick", colour, shadeColour, x, y, rotation, scale, crumbColour]` one chicken leg, bone to the right at rotation 0. Crumb is optional, for breaded legs. Use scale about .8 and three legs. BBQ: #A8481F, #6E2A14. Keep the bone inside the well.
`["muffin", topColour, caseColour, speck1, speck2]` three muffins from the side in pleated paper cases. Specks are optional. Use a soft case colour (#C9D6E3, #C9D9B0, #E8B4A0, #DDC69A) so a plain brown muffin doesn't read as a mushroom.
`["quesadilla", tortillaColour, charColour, fillingColour, fillingBitColour]` two folded half-moons with melted filling at the open edge. Bit colour can be `""`. Free corners for a side: (70, 32) and (30, 68).
`["burger", patty, cheese, greens, tomato, seedColour, bunColour]` a burger from the side. Only patty is required, and `""` leaves a layer out. Seeds are off unless named. Also use it for sandwiches on a soft round bun.
`["sando", breadColour, crustColour, filling1, filling2]` two halves of a sandwich on sliced bread, from the side. Filling 2 is an optional wavy layer. White bread: #FBEFD2, #CFAE78.
`["chip", colour, shadeColour, count, size, r, cx, cy]` tortilla or pita triangles, scattered. Size about 10.
`["chipring", colour, shadeColour, count, r]` chips around a dip. Draw it after `dipbowl`. Use r 27.
`["pretzel", colour, shadeColour, saltColour]` one big knot. All values optional. Salt `""` for none.
`["pretzel", colour, shadeColour, saltColour, count, scale, r, cx, cy]` small pretzel twists, for snack plates where pretzels are one part among several. Scale about .25, count 3, r about 18. Default colours: #A9682A, #7A4A1E, #FFFFFF.
`["waffle", colour, pocketColour]` a square waffle with a deep grid. Values optional.
`["shell", "clam" or "mussel", count, size, r, cx, cy]` open shells with the meat showing. Size 7 to 9.
`["onigiri", riceColour, shadeColour, noriColour, fillingColour]` three triangle rice balls with nori. All values optional.
`["bao", colour, pleatColour]` three round pleated steamed buns. Sit them on a `pool` so white buns stand out.
`["spiral", doughColour, swirlColour, icingColour]` three round rolls with a spiral. Icing optional.
`["ricecakes", colour, endColour, count, r, cx, cy]` short chewy cylinders, for tteokbokki.
`["sticks", [[colour, shade], ...], x, y, rotation]` a fan of long straight vegetable sticks. Put a small `mash` dip beside them.
`["ribs", colour, glazeColour, boneColour]` a glazed rack with bones showing. Bone optional.
`["wholefish", colour, shadeColour, x, y, rotation, scale]` one whole fish with scored skin and a forked tail, no eye. Scale about 0.85 keeps it inside the well. Use for dishes served as a whole fish.
`["smallfish", colour, bellyColour, count, gap]` small whole fried fish side by side, no eyes. Count 5, gap about 12.5.
`["pecans", colour, grooveColour, count, r]` pecan halves.
`["okra", count, r, cx, cy]` whole baby okra pods.
`["tamagoyaki", colour, lineColour]` three slices of rolled omelette showing their layers. Values optional.
`["summerroll"]` three rice paper rolls with shrimp and herbs showing through.
`["madeleine", colour, ridgeColour, count, r]` small shell-shaped cakes around the plate. Use count 5, r 20.
`["teaegg", count, r]` whole eggs marbled with tea cracks. Use a dark `pool`.
`["cauliflower", colour, count, size, r, cx, cy]` florets with a bumpy curd crown, a pale stalk and a little green leaf. Size 6 to 8. Colour `""` is plain cream #F1E2BA. Roasted: #E2B865. Shawarma or tandoori: #D98A2E. Gobi 65 or Manchurian: #C8461E. The shape still says cauliflower in any colour.
`["calamari", count, r, cx, cy, tentacles, mode]` fried squid rings, golden with a hole. Tentacles is how many curled tentacle bunches to mix in (0 to 3, default 0); pass cx, cy (50, 50) before it. Use count 8 to 10, r about 22. Mode is optional: leave it out for fried calamari; `"braised"` gives smooth, pale, tender rings with a pink-brown edge for boiled, grilled or stewed squid (squid salads, squid in tomato or wine), and a colour coats them in a sauce (gochujang or chili #C8402A). Put braised rings on a `pool` that contrasts, or on the well.
`["orzo", cx, cy, rx, ry, sauceColour]` a mound of rice-shaped pasta, bigger and more yellow than rice. Sauce is optional and tints the mound: tomato #D9583A, saffron #E8A93A. Keep rx and ry 34 or less when centred so it stays inside the well.
`["shortpasta", shape, colour, count, r, cx, cy]` short pasta pieces. Shape is `"penne"`, `"rotini"`, `"shells"`, `"elbow"` (curved macaroni with a dark opening: macaroni, coquillettes, mac and cheese) or `"ditalini"` (short stubby tubes with open ends: ditalini, tubetti, pasta e patate, minestrone). Colour `""` is plain pasta #EDC46A; whole wheat #C9934E. Use count 9 to 12 and r about 22. In a soup, use a broth that contrasts with the pasta, such as #E9C77A for chicken and #C9572E for tomato.
`["scallops", mode, count, r, cx, cy]` mode `"seared"`: round scallops with a golden-brown top and pale side; use count 4 to 5, r about 14. Mode `"gratin"`: scallop shells holding a browned topping, set evenly around the centre; use count 3, r 15 (or 2, r 13).
`["fritters", colour, shred1, shred2, count, size, r, cx, cy]` lacy fried clusters with shreds showing: kakiage, pakora, onion bhaji. Colour `""` is golden #DE9F42; bhaji and pakora #C9802E. Shreds `""` for none: carrot #E8742A, green #6E9A45 or spinach #3F7A34, onion #EBD7A4. Use count 3 to 6, size 7 to 11, r 15 to 21.
`["sausages", colour, count, length, cx, cy, shape]` browned links laid side by side with a gloss and a slight curve. Every value is optional (`""` keeps the default). Shape `"links"` (default; count 3, length 30) or `"fingers"` (oval minced-meat kofta or kafta with char marks; count 4, length 22). Link colours: pork or bratwurst #B5683A, beef #7A3E22, merguez #A33F22, kielbasa #9C4A30, chicken #D19A5A, lamb makanek #9A4A2A. Fingers: lamb or beef kofta #7A4226. Use count 3 to 5, length 22 to 32.
`["tart", fillingColour, slicesColour, bitsColour, r, cx, cy]` a round tart or quiche from above with a crimped golden pastry edge. Slices are round toppings (tomato #D9402A, leek rings #A9C76A, zucchini #8DB35A); bits are small flecks (herbs #5F8445, lardons #C98466, cheese #F6DF9A). `""` leaves either out. Fillings: quiche #F2CF6A, mustard-tomato #E3B840, leek flamiche #F2DFA6, spinach #6E9A45, onion #E8C77A. r defaults to 31 (the hero); use r 22 to 24 with cx, cy off-centre to leave room for a salad. Keep r 34 or less.
`["hotdog", sausageColour, mustardColour, ketchupColour]` two hot dogs in split buns. All optional; mustard and ketchup lines are off unless named. Frank #B4502E (default), bratwurst #B5683A. Mustard #F2C230, ketchup #C8302A. Add onions or relish with small parts at r 6.
`["avocado", fillingColour, count, size, r, cx, cy]` avocado halves with dark skin and green-to-yellow flesh. Filling `""` shows the brown pit; a colour heaps a stuffing in the hollow (chicken salad #F1E4B8, tuna #E6CFA6, crab #F2C7A6, egg #F6D36A). Two stuffed halves: count 2, size 1 to 1.1, r 14 to 15. One half as a side: count 1, size .8, r 0 and cx, cy where it goes.
`["leeks", count, length, cx, cy, searColour]` halved leek lengths side by side, white root end to pale green to split dark green tops. Use count 4 to 5, length 44 to 52. Sear is optional golden streaks for braised or grilled leeks: #C98A3A.
`["sardines", count, length, cx, cy]` small whole grilled sardines side by side: silver-blue, char lines, forked tail, no eyes. Use count 3 to 4, length 36 to 42. Colours are fixed.
`["porkbelly", count, length, cx, cy, glazeColour]` slices of pork belly side by side: crisp golden top, then clear pale fat and pink-brown meat layers. Use count 3 to 4, length 26 to 30. Glaze optional: crispy or roast #B8602A, char siu or hong shao rou #9A3A22, gochujang #B8402A.
`["volauvent", fillingColour, bitColour, count, size, r, cx, cy]` puff pastry shells with a tall flaky golden side, a creamy filling heaped and spilling over, and a little pastry lid. Filling `""` is cream #F1E2BC; bits are the chunks in it (mushroom #9C7452 or #6B4A33, chicken #F6E3C0, shrimp #F0896A, peas #6E9A45). Use count 3 (size 1.25, r 15) or 4 (size 1, r 16).
`["croissant", filling1, filling2, count, size, r, cx, cy]` fat golden crescents with curved layer lines. All optional. No fillings: 3 plain croissants. With fillings it is a croissant sandwich: 2 croissants with the fillings ruffling out of the cut (ham #E8A09A, cheese #F5D45E, turkey #EBC8A8, egg #F6D36A, lettuce #8CC152). Use size 1 to 1.1.
`["biscuits", butterColour, jamColour, count, size, r, cx, cy]` tall buttermilk biscuits: craggy golden-brown tops and pale flaky sides pulling apart. All optional. With butter (#F8DE7A) or jam (strawberry #B0283A, apricot #E8902A) the front one is split open to show it. Use count 3 (r 14) or 4 (size .9, r 15). For biscuits and gravy, use the plain form with a `pool` of gravy under or a `mash` on top.
`["braidbread", colour, speckColour, shape, count, size, r, cx, cy]` shape `"loaf"` (default): one braided loaf with a glossy egg-wash top, for challah, tsoureki and brioche braids. Shape `"bun"`: brioche buns with a fluted base and a topknot, count 3. All optional. Colours: challah or brioche #C0722A (default), tsoureki #A8561E, pale sweet bread #D9963E. Specks: sesame #F4E6C0, pearl sugar #FFFFFF, poppy #3A3A40, almond flakes #F2E2BE.
`["danish", fillingColour, icingColour, count, size, r, cx, cy]` square pastries with the corners folded in over a glossy filling. All optional. Filling: cream cheese #F5DFA0 (default #F4E3BC), cherry #A3202E, raspberry #C2304A, apricot #E8902A, blueberry #4A3A78, apple #D9A845. Icing drizzle #FFFFFF is off unless named. Use count 3 (r 14) or 4 (size .9, r 15).
`["banana", shape, count, size, r, cx, cy]` shape `"split"` (default): two banana halves cut lengthwise, flesh up with a yellow peel edge, curving round whatever sits between them; r is how far apart they are (default 14); put scoops or yogurt in the middle with `mash` at r 0 to 8. Shape `"slices"`: round slices, count 7; r 0 lays a tilted shingled row (toast, pancakes); r 20 to 26 fans them round the edge of a bowl. Colours are fixed.
`["tamales", filling1, filling2, masaColour, count, cx, cy]` tamales in corn husks side by side, the front one opened to show the masa and a shredded filling. All optional. Filling: red pork or chicken in red chile #B8442A (default), green chile or rajas #6E9A45, chicken verde #C9B07A, mole #5A2E1E, sweet raisin #8E4A5A. Filling2 is chunks: cheese #F8F4E6, raisins #5A2E2A. Masa #F2CF73 (default), sweet pink #F2B0B8. Use count 3.
`["gimbap", fillings, count, size, r, cx, cy, riceColour]` round slices: a dark nori ring, white rice and a centre of coloured filling. Fillings is a list of up to 6 colours: egg #F2C230, carrot #E8742A, spinach #3F7A34, pickled radish #F6D23A, ham or crab #E87A90, beef #7A3E22, tuna #E6CFA6, cucumber #7FB069. An empty list `[]` is plain rice rolls (chungmu). Slices sit evenly round a circle; use count 6 to 7 and r about 21, which leaves the middle free for a side (spicy squid, kimchi). Maki uses the same part.
`["pitapocket", fillingColour, greensColour, filling2Colour, count, size, cx, cy]` pita halves standing cut side up, stuffed so the filling and greens show at the top. All optional. Filling: tzatziki #F1EEE2, falafel #A8742E, chicken #E3B878, lamb or gyro #8A4A2A, egg salad #F6E09A, hummus #E3C48A. Greens: lettuce #7DB04A. Filling2 is chunks: cucumber #7FB069, tomato #D9402A, feta #FAF6EC. Count 2 (default) or 3 (size .8).
`["kibbeh", colour, fillingColour, count, size, r, cx, cy]` fried kibbeh: deep brown footballs with pointed ends and a crisp shine. All optional. Colour: fried #94501F (default), baked #A8642E, pumpkin kibbeh #C8742A. With a filling colour, one is cut open to show a bulgur shell round the filling: spiced lamb or beef #6B3A25, spinach #3F7A34. Use count 4 to 5, size 1.1 to 1.2, r 14 to 16. It counts as meat in the checks unless the recipe is the pumpkin or potato kind.
`["lettucecups", fillingColour, bit1, bit2, count, size, r, cx, cy]` crisp lettuce leaf cups holding a filling heap: larb, ssam, lettuce wraps. All optional. Filling: larb or minced chicken or turkey #C9A27A, pork #B07A4E (default), beef #8A4A2A, tofu #E8D3A0, shrimp #F0896A. Bit1 is small leaves (mint or cilantro #3F7A34, carrot #E8742A); bit2 is chili rings #C8302A. Use count 3 (r 15) or 4 (size .9, r 16).
