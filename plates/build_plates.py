"""Turn the plate specs into what the app ships. No model calls.

    python3 plates/build_plates.py

Writes, from plates/specs + plates/recipes:
  static/plates/<key>.svg   one drawing per dish, plus neutral.svg
  data/plate_index.json     what app/tools/plate_drawings.py matches against

Run it after any change to a spec. tests/test_plate_drawings.py fails if
what is committed has drifted from the specs."""
import glob, json, os, re, sys

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, H)
import plate_parts as pp

NEUTRAL = "neutral"
# A clean empty plate: for a dish nothing in the library is close to. Food on
# it would be a promise the recipe might not keep (PLATE_RULES rule 6).
NEUTRAL_PARTS = [["well"]]


def plate_key(name):
    """The same slug as data/recipe_library_batches/<slug>.json."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def svg_document(name, parts):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{pp.plate_svg(name, parts)}</svg>'


def load():
    specs, recipes = {}, []
    for path in sorted(glob.glob(f"{H}/specs/part-*.json")):
        specs.update(json.load(open(path)))
    for path in sorted(glob.glob(f"{H}/recipes/part-*.json")):
        recipes.extend(json.load(open(path)))
    meals = {d["dish_name"]: d["meal"] for d in json.load(open(f"{ROOT}/data/recipe_library_seed.json"))["dishes"]}
    return specs, recipes, meals


def drawings():
    """{file name: svg text}, the neutral plate included."""
    specs, _, _ = load()
    out = {f"{plate_key(n)}.svg": svg_document(n, p) for n, p in specs.items()}
    out[f"{NEUTRAL}.svg"] = svg_document(NEUTRAL, NEUTRAL_PARTS)
    return out


def index():
    specs, recipes, meals = load()
    rows = [{
        "key": plate_key(r["dish_name"]),
        "name": r["dish_name"],
        "main_protein": r.get("main_protein") or "",
        "cuisine": r.get("cuisine") or "",
        "meal": meals.get(r["dish_name"], ""),
        "ingredients": r.get("ingredients") or [],
        "parts": sorted({o[0] for o in specs[r["dish_name"]]} - {"well", "pool"}),
    } for r in sorted(recipes, key=lambda r: plate_key(r["dish_name"]))]
    return {"neutral": NEUTRAL, "plates": rows}


def main():
    out_dir = f"{ROOT}/static/plates"
    os.makedirs(out_dir, exist_ok=True)
    made = drawings()
    if len({k.lower() for k in made}) != len(made):
        sys.exit("Two dishes share a plate key.")
    for old in glob.glob(f"{out_dir}/*.svg"):
        if os.path.basename(old) not in made:
            os.remove(old)
    for name, text in made.items():
        with open(f"{out_dir}/{name}", "w") as f:
            f.write(text)
    with open(f"{ROOT}/data/plate_index.json", "w") as f:
        json.dump(index(), f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"{len(made)} drawings, {sum(len(t) for t in made.values()) // 1024} KB")


if __name__ == "__main__":
    main()
