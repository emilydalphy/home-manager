"""
Emily's answers to the small calls on four cards, 2026-10-26 ("go with your
picks"), for allergies, diet and the recipe library checks:

  62. Soba (unless it says 100% buckwheat), oyster sauce and injera (teff
      is often cut with wheat) are held for a gluten or celiac household.
  63. The allergy line Today shows after approval leads the row in the
      allergy-clash style, not the quiet grey after "Start by".
  68. A "Serve with rice" line is not a step reaching for something the
      list forgot: the morning report stops flagging it.
  69. Green Papaya Salad has fish sauce, so it is not tagged vegetarian —
      in the batch files and on the rows already filed.
  70. The recipe style checks are not run on the shared library's dishes.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import library_batch
import nodeharness
from app import agent, db, tools
from app.db import get_conn
from app.tools import allergen_gate, coordination, plan_quality, recipes
from app.tools import recipe_library as lib

REPO = Path(__file__).resolve().parents[1]
BATCHES = REPO / "data" / "recipe_library_batches"
SHELL_JS = (REPO / "static" / "shell.js").read_text(encoding="utf-8")
SHELL_CSS = (REPO / "static" / "shell.css").read_text(encoding="utf-8")


def _dish(key: str) -> dict:
    return json.loads((BATCHES / f"{key}.json").read_text())


def _file(key: str) -> str:
    e = _dish(key)
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"}, "origin": "seed"})
    return e["dish_name"]


def _copied(name: str):
    tools.add_recipe(name=name, ingredients=[], details_pending=True, cuisine="")
    recipe = {**tools.get_recipe(name), "slot": "dinner"}
    return agent._copy_from_library(recipe, name, name, allergen_gate.hard_avoidances(), {})


def _held(label: str, line: str, name: str = "Dinner") -> bool:
    avoid = [{"member": "Sam", "label": label, "source": "dietary_restriction", "severity": "hard",
              "terms": coordination._match_terms(coordination._conflict_phrases(label))}]
    hits = coordination.check_meal_conflicts(name, ingredients=[{"item": line}], avoidances=avoid)
    return any(h.get("severity") == "hard" for h in hits)


# ---------- 62. soba, oyster sauce and injera for gluten ----------

@pytest.mark.parametrize("label", ["celiac", "gluten free", "wheat allergy"])
@pytest.mark.parametrize("line,name", [
    ("Soba", "Dinner"),
    ("8 oz dried soba", "Dinner"),
    ("Buckwheat soba noodles", "Dinner"),
    ("Buckwheat noodles", "Dinner"),
    ("Cucumber", "Zaru Soba"),
    ("Cabbage", "Yakisoba"),
    ("Oyster sauce", "Dinner"),
    ("Injera", "Dinner"),
    ("Teff injera", "Dinner"),
    ("Chicken", "Doro Wat with Injera"),
])
def test_soba_oyster_sauce_and_injera_are_held_for_gluten(label, line, name):
    """CATCH (all but oyster sauce): soba was believed whenever it said
    buckwheat or didn't say noodles, and injera was no wheat word at all."""
    assert _held(label, line, name), f"{line!r} / {name!r} went through {label}"


@pytest.mark.parametrize("line", [
    "100% buckwheat soba noodles",
    "100% buckwheat soba",
    "Juwari soba",
    "Gluten-free soba noodles",
    "100% teff injera",
    "Gluten-free injera",
    "Teff flour",
    "Buckwheat flour",
])
def test_the_all_buckwheat_and_all_teff_kinds_are_believed(line):
    """GUARD: the line that says it is the gluten-free kind still passes."""
    assert not _held("celiac", line)


def test_the_library_s_soba_and_injera_dishes_are_not_copied_for_a_celiac_household():
    """CATCH for Shiro: injera on the list went through, so a celiac
    household was copied the library's Ethiopian dishes as written."""
    tools.add_member("Maya")
    tools.set_member_dietary_restrictions("Maya", ["celiac"])
    for key in ("zaru-soba", "shiro", "doro-wat"):
        copied, clash = _copied(_file(key))
        assert not copied and clash, key


# ---------- 63. Today's allergy line leads the row, in the warning style ----------

def test_a_cook_row_carries_its_allergy_line_on_its_own():
    from test_allergy_after_approval import _tonight as tonight
    from test_allergy_after_approval import _week_with_peanut_noodles_tonight
    plan_id = _week_with_peanut_noodles_tonight()
    tools.approve_weekly_plan(plan_id, approved_by="Emily")
    assert tonight()["allergy_line"] == ""
    tools.set_member_dietary_restrictions("Theo", ["peanut allergy"])
    row = tonight()
    assert row["allergy_line"] == "Has peanut, which Theo can’t have — swap it on Plan"
    # The reason still carries it first, for every other reader.
    assert row["reason"].startswith(row["allergy_line"])


def _why_html(move: dict, state: str = "upcoming", meta: str = "") -> str:
    i = SHELL_JS.index("  function dayNodeWhyHtml(")
    fn = SHELL_JS[i:SHELL_JS.index("\n  }\n", i)] + "\n  }\n"
    script = (
        "function escapeHtml(s){return String(s == null ? '' : s).replace(/&/g,'&amp;')"
        ".replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}\n"
        + fn
        + f"console.log(JSON.stringify(dayNodeWhyHtml({json.dumps(move)}, {json.dumps(state)}, {json.dumps(meta)})));"
    )
    res = nodeharness.run_node(script, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the screen's own builder")
LINE = "Has peanut, which Theo can’t have — swap it on Plan"


@_needs_node
def test_the_allergy_line_leads_the_row_in_its_own_style():
    """CATCH: it sat after "Start by 7:35" in the row's quiet grey."""
    html = _why_html({"kind": "cook", "chips": ["Start by 7:35"], "allergy_line": LINE,
                      "reason": LINE + " · Cooking for 6 — covers Thursday"})
    assert html.startswith('<span class="day-node-meta day-node-why day-node-allergy">' + LINE + "</span>")
    rest = html.split("</span>", 1)[1]
    assert rest == '<span class="day-node-meta day-node-why">Start by 7:35 · Cooking for 6 — covers Thursday</span>'
    assert html.count("Has peanut") == 1, "said once, not twice"


@_needs_node
def test_a_row_with_only_the_allergy_line_and_one_without_it():
    """GUARD: an allergy on its own still shows; no allergy, no change."""
    alone = _why_html({"kind": "reheat", "allergy_line": LINE, "reason": LINE})
    assert alone == '<span class="day-node-meta day-node-why day-node-allergy">' + LINE + "</span>"
    plain = _why_html({"kind": "cook", "chips": ["Start by 7:35"], "allergy_line": "", "reason": ""})
    assert plain == '<span class="day-node-meta day-node-why">Start by 7:35</span>'


def test_the_allergy_style_is_the_existing_clash_style_and_reads_on_the_tint():
    """The draft's allergy clash (.week-conflict-note) is --warn-ink at 600;
    Today's line uses the same pair, and --ink on the tinted Now row, where
    --warn-ink is under AA."""
    assert ".day-node-allergy { color: var(--warn-ink); font-weight: 600; }" in SHELL_CSS
    assert ".day-node.is-now .day-node-allergy { color: var(--ink); }" in SHELL_CSS


# ---------- 68. "Serve with rice" is not flagged ----------

def test_a_serving_line_is_not_a_step_reaching_for_something_off_the_list():
    """CATCH: about 100 library dishes ending "Serve with rice/naan" each
    got "step(s) use rice, which isn't on the ingredient list"."""
    r = recipes.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "2 lb"}, {"item": "Onion", "qty": "1"}],
        ["Brown the chicken thighs with the onion.", "Serve with rice or naan."], 4,
    )
    assert r["missing_from_list"] == []


def test_a_step_that_uses_something_before_its_serving_clause_is_still_flagged():
    """GUARD: only from the serving word on — the broth is still used."""
    r = recipes.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "2 lb"}],
        ["Brown the chicken thighs, pour in the broth and serve with rice."], 4,
    )
    assert r["missing_from_list"] == ["broth"]


@pytest.mark.parametrize("step", [
    "Serve with roti, naan or rice, with the sliced green chili on the side.",
    "Serve the salmon with the sauce spooned over the top, with rice or roast potatoes.",
    "Taste the sauce. If it's flat, add a pinch more salt. Serve with mashed potatoes, pasta or rice.",
])
def test_every_choice_on_a_serving_line_goes_with_it(step):
    """CATCH: split on commas, "naan or rice" after "Serve with roti," and
    a sentence opening "Serve the salmon with" were still flagged."""
    r = recipes.check_steps_ingredients_consistency(
        [{"item": "Chicken thighs", "qty": "2 lb"}], ["Brown the chicken thighs.", step], 4,
    )
    assert r["missing_from_list"] == []


def test_no_library_dish_is_flagged_for_a_side_it_serves():
    """CATCH: rice 44, naan 16, bread 5, potatoes 4 on the 840 batch files.
    What's left is the soups that make their own broth (the batch check's
    own "look") and a few words that aren't the food ("the hot sauce from
    the pan"), none of them a serving line."""
    sides = {"rice", "naan", "bread", "potatoes", "pasta", "spaghetti", "green beans", "roti"}
    flagged = []
    for path in sorted(BATCHES.glob("*.json")):
        d = json.loads(path.read_text())
        r = recipes.check_steps_ingredients_consistency(d["ingredients"], d["instructions"], 4)
        flagged += [(path.stem, m) for m in r["missing_from_list"] if m in sides]
    assert flagged == []


# ---------- 69. Green Papaya Salad is not vegetarian ----------

def test_green_papaya_salad_is_tagged_fish_in_the_library_files():
    assert _dish("green-papaya-salad")["main_protein"] == "fish"
    seed = json.loads((REPO / "data" / "recipe_library_seed.json").read_text())["dishes"]
    assert next(d for d in seed if d["dish_name"] == "Green Papaya Salad")["main_protein"] == "fish"


def test_no_library_dish_tagged_vegetarian_or_vegan_has_meat_or_fish():
    """GUARD over the whole library, read by the batch check's own matcher."""
    found = [(p.stem, item) for p in sorted(BATCHES.glob("*.json"))
             for item in library_batch.meat_in_a_meatless_dish(json.loads(p.read_text()))]
    assert found == []


def test_a_row_already_filed_as_vegetarian_is_retagged_at_startup():
    """CATCH: file_entry never overwrites a filled column, so the live
    library's row kept "vegetarian" however often it was refiled."""
    e = _dish("green-papaya-salad")
    lib.file_entry(e["dish_name"], **{**{k: v for k, v in e.items() if k != "dish_name"},
                                      "origin": "seed", "main_protein": "vegetarian"})
    conn = get_conn()
    conn.execute("UPDATE recipe_library SET main_protein = 'vegetarian' WHERE dish_key = 'green papaya salad'")
    conn.commit()
    conn.close()
    db.init_db()
    conn = get_conn()
    tag = conn.execute("SELECT main_protein FROM recipe_library WHERE dish_key = 'green papaya salad'").fetchone()[0]
    conn.close()
    assert tag == "fish"


# ---------- 70. no style checks on library dishes ----------

def _entry(d: dict, from_library: bool) -> dict:
    return {"date": "2026-10-27", "slot": "dinner", "slot_state": "planned", "meal_name": d["dish_name"],
            "ingredients": d["ingredients"], "instructions": d["instructions"],
            "default_servings": d["default_servings"], "prep_time_minutes": d["prep_time_minutes"],
            "cook_time_minutes": d["cook_time_minutes"], "source": "", "reasoning": "", "food_groups": [],
            "main_protein": d["main_protein"], "is_new_recipe": True, "from_library": from_library}


def test_the_style_checks_skip_library_dishes_and_still_read_everything_else():
    """CATCH: on the 840 batch files longest_thing_not_first fired 137 times
    and method_is_assembly 78. GUARD: the same dishes, not from the library,
    still get them — a household's own written recipe is still checked."""
    dishes = [json.loads(p.read_text()) for p in sorted(BATCHES.glob("*.json"))]
    style = {r.__name__.lstrip("_") for r in plan_quality._STYLE_RULES}
    ctx = {"household_asks": "", "avoided": []}
    library = plan_quality.check_week([_entry(d, True) for d in dishes], ctx)
    written = plan_quality.check_week([_entry(d, False) for d in dishes], ctx)
    assert not [v for v in library if v.rule in style]
    assert [v for v in written if v.rule in style]


def test_a_dish_copied_from_the_library_loads_as_a_library_dish():
    """The flag the skip reads comes from the recipe's library entry (a
    researched "seed" dish), not from anything the household typed."""
    import datetime
    name = _file("butter-chicken") if (BATCHES / "butter-chicken.json").exists() else _file("doro-wat")
    copied, _ = _copied(name)
    assert copied
    tools.add_recipe("Home Stew", ingredients=[{"item": "beef", "qty": "1 lb"}], instructions=["Cook it."])
    monday = datetime.date(2026, 10, 26)
    pid = tools.create_weekly_plan(monday.isoformat())["weekly_plan_id"]
    tools.plan_meal(monday.isoformat(), name, slot="dinner", weekly_plan_id=pid)
    tools.plan_meal((monday + datetime.timedelta(days=1)).isoformat(), "Home Stew", slot="dinner",
                    weekly_plan_id=pid)
    flags = {e["meal_name"]: e["from_library"] for e in plan_quality._load_plan_entries(pid)}
    assert flags == {name: True, "Home Stew": False}
