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
`["wedges", colour, skinColour, count, r]` apple, orange, melon or potato wedges

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
