"""Check one part's specs, then draw a random 24 plus any flagged dishes to a preview.
Usage: python3 plates/check_part.py 03
Exit code 1 and a list of problems if any recipe is missing or any plate breaks a rule."""
import json, os, random, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import plate_parts as pp
n = sys.argv[1]
recs = json.load(open(f"{H}/recipes/part-{n}.json"))
specs = json.load(open(f"{H}/specs/part-{n}.json"))
ARITY = dict(well=1, pool=(2, 3), rice=7, mash=7, band=4, strips=(6, 8), slices=5, nuggets=(6, 8), cubes=(5, 7), dots=(5, 7), rings=(4, 6),
             leaves=5, frisee=5, cashews=4, poached=3, wontons=3, bokchoy=3, baguette=1, flautas=1, phyllo=1, loaf=1, patties=3,
             rounds=6, glass=3, eggplant=(3, 5), ribbons=5, shrimp=(5, 7), omelette=5, steak=7, bread=5, tortellini=5, fillet=8, bacon=4, chop=6, potatoes=4, wedgering=5, noodles=5, wrap=4, pizza=4, pancakes=3, flatbread=3, dipbowl=3, bowl=2, pastries=4, stuffed=3, cutlet=7, block=7, rolls=3, wedges=(5, 7), yolk=3, ringbread=1, skewers=(1, 4), tacos=(1, 5), sandwiches=1, toast=(1, 4), cups=(1, 5),
             drumstick=(7, 8), muffin=(3, 5), quesadilla=5, burger=(2, 7), mushroom=(5, 7), broccoli=(4, 6), chip=(6, 8), chipring=5, pretzel=(1, 9), waffle=(1, 3),
             shell=(5, 7), sando=(4, 5), onigiri=(1, 5), bao=3, spiral=(3, 4), ricecakes=(5, 7), sticks=5, ribs=(3, 4), smallfish=5, wholefish=7, pecans=5, okra=(3, 5), tamagoyaki=(1, 3), summerroll=1, madeleine=5, teaegg=3,
             cauliflower=(5, 7), calamari=(3, 7), orzo=(5, 6), shortpasta=(5, 7), scallops=(4, 6), fritters=(7, 9),
             sausages=(1, 7), tart=(1, 7), hotdog=(1, 4), avocado=(1, 7), leeks=(1, 6), sardines=(1, 5), porkbelly=(1, 6), volauvent=(1, 8),
             croissant=(1, 8), biscuits=(1, 8), braidbread=(1, 9), danish=(1, 8), banana=(1, 7), tamales=(1, 7), gimbap=(1, 8),
             pitapocket=(1, 8), kibbeh=(1, 8), lettucecups=(1, 9))
bad = []
for r in recs:
    name = r["dish_name"]
    if name not in specs:
        bad.append(f"{name}: missing"); continue
    parts = specs[name]
    if not 2 <= len(parts) + (0 if len(parts) != 1 else 1) <= 9:
        bad.append(f"{name}: needs 2 to 8 parts")
    if parts[0][0] not in ("well", "pool"):
        bad.append(f"{name}: must start with well or pool")
    for o in parts:
        k = o[0]
        if k not in ARITY:
            bad.append(f"{name}: unknown part {k}"); continue
        a = ARITY[k]; lo, hi = (a, a) if isinstance(a, int) else a
        if not lo <= len(o) <= hi:
            bad.append(f"{name}: {k} has {len(o)} values, needs {lo}-{hi}")
        if k == "strips" and len(o) >= 6 and o[5] + o[3] / 2 > 34:
            bad.append(f"{name}: strips spill past the plate (r + len/2 must be 34 or less)")
    try:
        pp.plate_svg(name, parts)
    except Exception as e:
        bad.append(f"{name}: draw error {e}")
import re, collections
EX = json.load(open(f"{H}/examples.json"))
LOCKED = json.load(open(f"{H}/locked.json"))
FOOD = set(ARITY) - {"well", "pool"}
WHOLE = {"tart", "hotdog", "baguette", "flautas", "phyllo", "loaf", "ringbread", "skewers", "tacos", "sandwiches", "toast", "cups", "omelette", "steak", "bread", "fillet", "chop", "potatoes", "wrap", "pizza", "pancakes", "flatbread", "pastries", "stuffed", "rolls", "patties", "glass", "wedgering", "noodles", "cutlet", "block", "bowl", "dipbowl", "drumstick", "muffin", "quesadilla", "burger", "pretzel", "waffle", "sando", "onigiri", "bao", "spiral", "ribs", "tamagoyaki", "summerroll", "madeleine", "smallfish", "wholefish", "sticks", "teaegg",
         "croissant", "biscuits", "braidbread", "danish", "tamales", "gimbap", "pitapocket", "kibbeh", "lettucecups"}
PASTA = re.compile(r"pasta|spaghetti|penne|rigatoni|bucatini|linguine|fettuccine|pappardelle|tagliatelle|carbonara|tortellini|ravioli|bolognese|macaroni|mac and cheese|ziti|orecchiette|fusilli|cacio|lasagn", re.I)
SOUPY = re.compile(r"soup|salad|e fagioli|e ceci|e piselli|lasagn|bake", re.I)
DRINK = re.compile(r"smoothie(?! bowl)|\\blassi|lemonade|horchata|milkshake", re.I)
for r in recs:
    name = r["dish_name"]; parts = specs.get(name)
    if not parts: continue
    kinds = [o[0] for o in parts]
    if "pool" in kinds[1:] and any(k in FOOD for k in kinds[:kinds.index("pool", 1)]):
        bad.append(f"{name}: a pool is drawn over food; move it first")
    if PASTA.search(name) and not SOUPY.search(name) and "strips" not in kinds and "noodles" not in kinds and "ribbons" not in kinds and "tortellini" not in kinds and "shortpasta" not in kinds and "orzo" not in kinds:
        bad.append(f"{name}: pasta needs a noodle nest of strips with sauce on top")
    if PASTA.search(name) and not SOUPY.search(name) and "strips" in kinds and parts[0][0] == "pool" and (len(parts[0]) == 2) and "rice" not in kinds and "mash" not in kinds:
        bad.append(f"{name}: pasta must be noodles with sauce on top, not noodles in a full pool of sauce")
    if DRINK.search(name) and "glass" not in kinds:
        bad.append(f"{name}: drinks use the glass part")
    if name not in EX and len(kinds) == 2 and kinds[0] == "well" and kinds[1] in ("sandwiches", "tacos", "toast", "flautas", "phyllo", "loaf", "skewers", "baguette", "cups", "ringbread"):
        bad.append(f"{name}: a bare whole-dish part; add a side or topping that identifies this dish, or use a better part")
STIR = re.compile(r"stir[- ]?fr|lo mein|chow mein|pad see|krapow|kaprao|bokkeum|jap ?chae|chop suey|kung pao|moo goo|bulgogi", re.I)
EGGISH = re.compile(r"soup|stew|dal\\b|lentil|alicha|wat\\b|curry", re.I)
SCATTER = {"cubes": (2, 3, 4), "dots": (2, 3, 4), "nuggets": (3, 4, 5), "rings": (None, None, 3), "leaves": (2, 3, 4), "wedges": (3, None, 4), "slices": (3, None, 4), "rounds": (3, 4, 5)}
MEATFREE = {"tofu", "vegetarian", "vegan", "none", "beans", "legumes", "lentils", "chickpeas", "paneer", "cheese", "egg", "eggs", "tempeh", "seitan"}
# a meat shape is fine on a meat-free main_protein only if a real ingredient has that meat (not a broth, sauce or vegan stand-in)
_ANYMEAT = r"bacon|pancetta|guanciale|lardon|ham\b|prosciutto|sausage|chorizo|pork|beef|steak|lamb|veal|chicken|turkey|duck|salmon|tuna|cod\b|tilapia|trout|halibut|sole\b|snapper|mackerel|sardine|anchov|fish|shrimp|prawn"
MEATSHAPES = {"bacon": r"bacon|pancetta|guanciale|lardon", "chop": r"pork|lamb|veal", "steak": r"steak|beef|sirloin|ribeye|flank", "shrimp": r"shrimp|prawn",
              "fillet": r"salmon|tuna|cod\b|tilapia|trout|halibut|sole\b|snapper|mackerel|sardine|fish|haddock|branzino|bass\b", "slices": _ANYMEAT,
              "drumstick": r"chicken|turkey|duck", "ribs": r"\bribs?\b|pork|beef", "shell": r"clam|mussel|cockle", "smallfish": r"anchov|sardine|smelt|fish",
              "calamari": r"squid|calamar|kalamar", "scallops": r"scallop",
              "sausages": r"sausage|chorizo|merguez|kielbasa|bratwurst|hot dog|frank|makanek|ground (beef|lamb|pork|chicken|turkey)|minced|kofta|kafta|lamb|beef",
              "hotdog": r"hot dog|frank|sausage|wiener|bratwurst", "sardines": r"sardine", "porkbelly": r"pork belly|pork|samgyeopsal",
              "kibbeh": r"lamb|beef|veal|goat|ground meat|minced meat|pumpkin|potato|squash"}  # kibbeh is meat unless it's the pumpkin or potato kind
NOTMEAT = re.compile(r"vegan|vegetarian|plant[- ]based|meatless|\bbroth\b|\bstock\b|bouillon|\bsauce\b|\bpaste\b|\bpowder\b", re.I)
def has_meat(r, pattern):
    return any(re.search(pattern, i, re.I) and not NOTMEAT.search(i) for i in r.get("ingredients", []))
for r in recs:
    name = r["dish_name"]; parts = specs.get(name)
    if not parts or name in EX: continue
    kinds = [o[0] for o in parts]
    # no more than five foods; the well and sauce pool don't count
    if sum(1 for k_ in kinds if k_ not in ("well", "pool")) > 5:
        bad.append(f"{name}: more than five foods; cut the least identifying ones")
    # nothing on the plate that isn't in the recipe: meat shapes on meat-free dishes
    if str(r.get("main_protein", "")).lower() in MEATFREE:
        for k_ in sorted(set(kinds) & set(MEATSHAPES)):
            if not has_meat(r, MEATSHAPES[k_]):
                bad.append(f"{name}: a {k_} shape on a {r.get('main_protein')} dish, and the recipe has no such meat; check it against the recipe")
    # rule 10: nothing leaves the plate
    for o in parts:
        k = o[0]
        if k in ("cubes", "dots", "rings", "nuggets", "strips", "leaves"):
            pos = {"cubes": 5, "dots": 5, "rings": 4, "nuggets": 6, "strips": 6}.get(k)
            cx = o[pos] if pos and len(o) > pos else 50
            cy = o[pos + 1] if pos and len(o) > pos + 1 else 50
            spread = {"cubes": o[4] if k == "cubes" else 0, "dots": o[4] if k == "dots" else 0, "rings": o[3] if k == "rings" else 0,
                      "nuggets": o[5] if k == "nuggets" else 0, "strips": o[5] if k == "strips" else 0, "leaves": o[4] if k == "leaves" else 0}[k]
            size = {"cubes": o[3] if k == "cubes" else 0, "dots": o[3] if k == "dots" else 0, "rings": 2, "nuggets": o[4] if k == "nuggets" else 0,
                    "strips": o[3] / 2 if k == "strips" else 0, "leaves": o[3] if k == "leaves" else 0}[k]
            off = ((cx - 50) ** 2 + (cy - 50) ** 2) ** 0.5
            if off + spread * 1.05 + size > 37.5:
                bad.append(f"{name}: {k} spills past the plate; reduce r or move it in")
    for o in parts:  # newer scattered parts: (size index or fixed size, r index, cx index, how far the drawing reaches past size)
        where = {"mushroom": (3, 4, 5, 1.3), "broccoli": (2, 3, 4, 1.45), "chip": (4, 5, 6, 1.15), "shell": (3, 4, 5, 1.7), "okra": (None, 2, 3, 13), "ricecakes": (None, 4, 5, 10),
                 "cauliflower": (3, 4, 5, 1.7), "calamari": (None, 2, 3, 9), "shortpasta": (None, 4, 5, 9.5), "fritters": (5, 6, 7, 1.5),
                 "scallops": (None, 3, 4, 14 if len(o) > 1 and o[1] == "gratin" else 8.5)}.get(o[0])
        if where:
            si, ri, ci, reach = where
            size = (o[si] * reach) if si is not None else reach
            cx = o[ci] if len(o) > ci else 50
            cy = o[ci + 1] if len(o) > ci + 1 else 50
            if ((cx - 50) ** 2 + (cy - 50) ** 2) ** 0.5 + o[ri] * 1.05 + size > 39:
                bad.append(f"{name}: {o[0]} spills past the plate; reduce r or move it in")
        def v(i, d):  # an optional value, "" meaning the default
            return o[i] if len(o) > i and o[i] != "" else d
        reach = None  # (centre x, centre y, how far the drawing reaches from it)
        if o[0] in ("sausages", "leeks", "sardines", "porkbelly"):  # links or lengths laid side by side in a row
            if o[0] == "sausages":
                fing = v(6, "links") == "fingers"
                n_, L_, ci, w_ = v(2, 4 if fing else 3), v(3, 22 if fing else 30), 4, 10 if fing else 8.6
                gap, half = w_ + 3.4, v(3, 22 if fing else 30) / 2 + 3.4
            elif o[0] == "leeks":
                n_, L_, ci = v(1, 4), v(2, 50), 3
                w_, gap, half = 9.6, 12, L_ / 2 + 2
            elif o[0] == "sardines":
                n_, L_, ci = v(1, 4), v(2, 40), 3
                w_, gap, half = 14.8 * L_ / 42, 12.4 * L_ / 42, 23 * L_ / 42 + 1.5
            else:
                n_, L_, ci = v(1, 4), v(2, 30), 3
                w_, gap, half = 13.4, 15, L_ / 2 + 3
            ext = (n_ - 1) / 2 * gap  # centre line of the outermost item
            if o[0] == "porkbelly":  # square-ended slices
                far = (half ** 2 + (ext + w_ / 2) ** 2) ** .5
            else:  # rounded or tapered ends
                far = ((half - w_ / 2) ** 2 + ext ** 2) ** .5 + w_ / 2
            reach = (v(ci, 50), v(ci + 1, 50), far)
        elif o[0] == "tart":
            reach = (v(5, 50), v(6, 50), v(4, 31) + 5)  # a tart edge may run to radius 34
        elif o[0] == "avocado":
            n_ = v(2, 2)
            reach = (v(5, 50), v(6, 50), v(4, 14 if n_ > 1 else 0) + 18.5 * v(3, 1))
        elif o[0] == "volauvent":
            n_ = v(3, 3)
            reach = (v(6, 50), v(7, 50), v(5, 15 if n_ > 1 else 0) + 1.5 + 14 * v(4, 1.25))
        elif o[0] in ("croissant", "biscuits", "danish", "gimbap", "kibbeh", "lettucecups"):  # pieces around a centre
            # (count index, default count, size index, default size, r index, default r when count > 1, reach of one piece at size 1)
            ci_, dn, si_, ds, ri_, dr, one = {"croissant": (3, 2 if v(1, "") else 3, 4, None, 5, None, 21.5 if v(1, "") or v(2, "") else 19.5), "biscuits": (3, 3, 4, 1, 5, 14, 14),
                                               "danish": (3, 3, 4, 1, 5, 14, 16.4), "gimbap": (2, 7, 3, 1, 4, 21, 9.2),
                                               "kibbeh": (3, 4, 4, 1.2, 5, 14, 12.8), "lettucecups": (4, 3, 5, 1, 6, 15, 13.8)}[o[0]]
            n_ = v(ci_, dn)
            if o[0] == "croissant":
                ds, dr = (1 if n_ > 2 else 1.05), (14 if n_ > 2 else (12 if n_ > 1 else 0))
            r_ = v(ri_, dr if n_ > 1 else 0)
            reach = (v(ri_ + 1, 50), v(ri_ + 2, 50), r_ * (1.05 if o[0] == "kibbeh" else 1) + one * v(si_, ds))
        elif o[0] == "braidbread":
            bun = v(3, "loaf") == "bun"
            n_ = v(4, 3 if bun else 1); sz_ = v(5, 1 if bun else 1.1)
            reach = (v(7, 50), v(8, 50), (v(6, 15 if n_ > 1 else 0) + 14.6 * sz_) if bun else 30.5 * sz_)
        elif o[0] == "banana":
            sz_ = v(3, 1)
            if v(1, "split") == "slices":
                n_, r_ = v(2, 7), v(4, 0)
                far = r_ + 6.4 * sz_ if r_ else (n_ - 1) / 2 * 7.5 * sz_ + 6.4 * sz_
            else:
                r_ = v(4, 14)
                far = max((24.5 ** 2 + (r_ + 3) ** 2) ** .5, r_ + 16.6) * sz_
            reach = (v(5, 50), v(6, 50), far)
        elif o[0] == "tamales":
            ext = (v(4, 3) - 1) / 2 * 15
            reach = (v(5, 50), v(6, 50), max((27 ** 2 + ext ** 2) ** .5, (10 ** 2 + (ext + 11) ** 2) ** .5) + 2)
        elif o[0] == "pitapocket":
            n_ = v(4, 2); sz_ = v(5, 1 if n_ < 3 else .8)
            ext = (n_ - 1) / 2 * 28 * sz_
            reach = (v(6, 50), v(7, 50), max(((ext + 15.4 * sz_) ** 2 + (8.4 + 4 * sz_) ** 2) ** .5, (ext ** 2 + (8.4 + 15 * sz_) ** 2) ** .5))
        if reach and ((reach[0] - 50) ** 2 + (reach[1] - 50) ** 2) ** .5 + reach[2] > 39:
            bad.append(f"{name}: {o[0]} spills past the plate; make it smaller or move it in")
        if o[0] == "orzo" and ((o[1] - 50) ** 2 + (o[2] - 50) ** 2) ** 0.5 + max(o[3], o[4]) * 1.08 > 37:
            bad.append(f"{name}: orzo spills past the plate; make it smaller or move it in")
    if "band" in kinds:
        bad.append(f"{name}: stripes look rigid; pile toppings on rice with soft edges")
    # rule 18: stir-fries are long strips
    if STIR.search(name) and "strips" not in kinds and "noodles" not in kinds and "ribbons" not in kinds and "tortellini" not in kinds:
        bad.append(f"{name}: a stir-fry needs long strips of its main ingredients")
    # rule 20: dips never cover what's dipped
    if "dipbowl" in kinds:
        i = kinds.index("dipbowl")
        for o in parts[:i]:
            if o[0] in ("wedges", "cubes", "strips", "rounds", "nuggets", "leaves"):
                bad.append(f"{name}: the dip is drawn over {o[0]}; draw dippers after it and around it")
        for o in parts[i + 1:]:
            if o[0] == "wedges":
                bad.append(f"{name}: dippers around a dip use wedgering, with a small dip in the middle")
    # rule 7: generous plates
    if kinds[0] == "well" and not (set(kinds) & WHOLE) and len(kinds) < 4:
        bad.append(f"{name}: sparse; add identifying food to fill the plate")
for r in recs:
    name = r["dish_name"]
    if name in LOCKED and specs.get(name) != LOCKED[name]:
        bad.append(f"{name}: approved plate was changed; restore it from locked.json")
dup = collections.defaultdict(list)
for k, v in specs.items():
    dup[json.dumps(v, sort_keys=True)].append(k)
for names_ in dup.values():
    if len(names_) > 1:
        bad.append(f"identical plates: {', '.join(names_[:5])}")
extra = [k for k in specs if k not in {r["dish_name"] for r in recs}]
if extra:
    bad.append(f"not in this part: {extra[:5]}")
names = [r["dish_name"] for r in recs if r["dish_name"] in specs]
pick = random.Random(int(n)).sample(names, min(24, len(names)))
W, CW, CH = 6 * 200 + 20, 200, 230
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}" font-family="Helvetica, Arial, sans-serif"><rect width="{W}" height="{W}" fill="#F7F7F4"/>']
for i, nm in enumerate(pick):
    x, y = 10 + (i % 6) * CW, 10 + (i // 6) * CH
    svg.append(f'<g transform="translate({x+15} {y}) scale(1.7)">{pp.plate_svg(nm, specs[nm])}</g>')
    svg.append(f'<text x="{x+100}" y="{y+192}" text-anchor="middle" font-size="13" font-weight="600" fill="#163025">{nm[:27].replace("&","and")}</text>')
out = f"/tmp/plates-part-{n}.svg"
open(out, "w").write("".join(svg + ["</svg>"]))
subprocess.run(["qlmanage", "-t", "-s", "1400", "-o", "/tmp", out], capture_output=True)
print(f"{len(names)} of {len(recs)} recipes have plates. Preview: {out}.png")
print("\n".join(bad[:60]) or "No problems.")
sys.exit(1 if bad else 0)
