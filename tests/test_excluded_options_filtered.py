"""
An option the household can't have never reaches a sheet (Loop Board, "As
a household that doesn't eat pork, 'Change the protein' never offers
pork"). Tweak -> Change the protein on a beef stir-fry offered "Ground
pork" with the note "excluded, no pork": the model listed it and flagged
it, and nothing in code took it off. Now every option list the AI hands a
sheet — the plate's protein/veg/carb and Swap's three picks — goes through
swap_in_place.offer_gate first: the household's Won't eat list (a "we
don't eat pork" is saved there), allergies and hard facts, by the same
matcher the draft's allergen banner uses, plus an option whose own note
says it is excluded.
"""
from __future__ import annotations

import importlib

from app import tools
from app.tools import plate_parts as pp
from test_plate_parts import DAY2, _dinner, week  # noqa: F401

sop = importlib.import_module("app.tools.swap_options")
from test_week1_carousel import DAY1, _asker, _entry_id, _pick  # noqa: E402


def _protein_names(plan_id, raw):
    entry_id = _dinner(plan_id, DAY2)["entry_id"]
    return [o["name"] for o in pp.part_options(plan_id, entry_id, asker=lambda ctx: raw)["options"]]


def test_a_house_that_doesnt_eat_pork_is_never_offered_pork(week):
    # Said the way the chat saves it: "we don't eat pork" lands on Won't eat.
    tools.add_fact_from_chat("people", "We don't eat pork")
    raw = [{"name": "Ground chicken", "note": "lighter"}, {"name": "Ground pork", "note": "richer"},
           {"name": "Pork belly", "note": ""}, {"name": "Ground beef", "note": "classic"}]
    assert _protein_names(week, raw) == ["Ground chicken", "Ground beef"]


def test_an_option_the_model_itself_flags_as_excluded_is_dropped(week):
    # The card's own answer: listed, flagged, and shown anyway. Dropped on
    # its note alone, even with nothing on file that matches its name.
    # A note that only MENTIONS a food, or says why the option is safe, stays.
    raw = [{"name": "Ground chicken", "note": "milder than pork"}, {"name": "Ground lamb", "note": "excluded, no lamb"},
           {"name": "Tofu", "note": "not allowed here"}, {"name": "Ground beef", "note": "Emily can't have pork"}]
    assert _protein_names(week, raw) == ["Ground chicken", "Ground beef"]


def test_allergies_and_hard_facts_are_filtered_and_what_is_left_is_shown(week):
    tools.set_member_dietary_restrictions("Emily", ["shellfish"])
    tools.add_fact("people", "No beef in this house, ever", hard=True)
    raw = [{"name": "Shrimp", "note": ""}, {"name": "Ground beef", "note": ""}, {"name": "Ground chicken", "note": ""}]
    # One left is one shown — never padded with an excluded one.
    assert _protein_names(week, raw) == ["Ground chicken"]


def test_a_conditional_rule_keeps_its_exception(week):
    # "No cauliflower unless it's cauliflower rice": the allowed form is
    # still offered — the shared matcher's reading of the exception, not a
    # second, stricter one of this sheet's own.
    tools.add_fact("people", "No cauliflower unless it's cauliflower rice", hard=True)
    raw = [{"name": "Cauliflower rice", "note": ""}, {"name": "Ground chicken", "note": ""}]
    assert _protein_names(week, raw) == ["Cauliflower rice", "Ground chicken"]


def test_swap_picks_drop_the_won_t_eat_list_and_self_flagged_picks(week):
    tools.add_food_dislikes(["pork"])
    sop._OPTIONS_CACHE.clear()
    ask = _asker(_pick("Pulled Pork Tacos", protein="pork"), _pick("Lemon Chicken Traybake"),
                 _pick("Lamb Kofta", reason="Excluded, no lamb", protein="lamb"),
                 _pick("Fish Tacos", protein="cod"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [o["meal"] for o in out["options"]] == ["Lemon Chicken Traybake", "Fish Tacos"]


# ---------- review round, 2026-10-19 ----------

def test_bacon_ham_and_chorizo_are_pork_and_a_hamburger_is_not(week):
    tools.add_fact_from_chat("people", "We don't eat pork")
    raw = [{"name": "Bacon lardons", "note": ""}, {"name": "Ham", "note": ""}, {"name": "Chorizo", "note": ""},
           {"name": "Hamburger patties", "note": ""}, {"name": "Ground chicken", "note": ""}]
    assert _protein_names(week, raw) == ["Hamburger patties", "Ground chicken"]
    sop._OPTIONS_CACHE.clear()
    ask = _asker(_pick("Ham and Pea Pasta", protein="ham"), _pick("Bacon Carbonara", protein="bacon"),
                 _pick("Lemon Chicken Traybake"))
    out = tools.swap_options(week, _entry_id(week, DAY1), asker=ask)
    assert [o["meal"] for o in out["options"]] == ["Lemon Chicken Traybake"]


def test_a_note_that_only_mentions_excluded_stays(week):
    raw = [{"name": "Ground chicken", "note": "not excluded"},
           {"name": "Ground beef", "note": "excluded the cilantro"},
           {"name": "Chickpeas", "note": "nothing off-limits here"},
           {"name": "Tofu", "note": "Keeps clear of the excluded pork"},
           {"name": "Ground lamb", "note": "Lamb — excluded"}]
    assert _protein_names(week, raw) == ["Ground chicken", "Ground beef", "Chickpeas", "Tofu"]


def test_a_cached_sheet_is_checked_again_when_served(week):
    entry_id = _dinner(week, DAY2)["entry_id"]
    raw = [{"name": "Ground pork", "note": ""}, {"name": "Ground chicken", "note": ""}]
    assert [o["name"] for o in pp.part_options(week, entry_id, asker=lambda c: raw)["options"]] == \
        ["Ground pork", "Ground chicken"]
    tools.add_food_dislikes(["pork"])
    again = pp.part_options(week, entry_id, asker=lambda c: (_ for _ in ()).throw(AssertionError("asked again")))
    assert [o["name"] for o in again["options"]] == ["Ground chicken"]

    sop._OPTIONS_CACHE.clear()
    swap_entry = _entry_id(week, DAY1)
    first = tools.swap_options(week, swap_entry, asker=_asker(_pick("Lemon Chicken Traybake"),
                                                              _pick("Fish Tacos", protein="cod")))
    assert [o["meal"] for o in first["options"]] == ["Lemon Chicken Traybake", "Fish Tacos"]
    tools.add_food_dislikes(["chicken"])
    served = tools.swap_options(week, swap_entry, asker=lambda c: (_ for _ in ()).throw(AssertionError("asked again")))
    assert [o["meal"] for o in served["options"]] == ["Fish Tacos"]
    # The index a tap sends names the pick the sheet drew.
    assert served["options"][0]["index"] == 0
    assert [p["meal_name"] for p in sop._OPTIONS_CACHE[(1, swap_entry)]["options"]] == ["Fish Tacos"]


def test_a_check_that_cannot_run_shows_nothing_says_so_and_caches_nothing(week, monkeypatch):
    from app.tools import coordination
    entry_id = _dinner(week, DAY2)["entry_id"]

    def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(coordination, "_avoidances", broken)
    out = pp.part_options(week, entry_id, asker=lambda c: [{"name": "Ground chicken", "note": ""}])
    assert out["options"] == [] and out["options_unavailable"] is True
    monkeypatch.undo()
    asked = []
    again = pp.part_options(week, entry_id, asker=lambda c: asked.append(1) or [{"name": "Ground chicken", "note": ""}])
    assert asked == [1], "nothing was cached, so the next open asks again"
    assert [o["name"] for o in again["options"]] == ["Ground chicken"]


def _card_cand(name, protein):
    return {"meal_name": name, "reason": "Quick.", "main_protein": protein,
            "ingredients": [{"item": protein.title(), "qty": "1 lb", "category": "meat/seafood"}],
            "instructions": ["Cook it."], "food_groups": ["protein"],
            "prep_time_minutes": 5, "cook_time_minutes": 20}


def test_the_change_card_keeps_won_t_eat_off_unless_they_asked_for_it(week):
    from app.tools import allergen_gate, proposals
    tools.add_fact_from_chat("people", "We don't eat pork")
    rows = [{"date": DAY2, "slot": "dinner", "action": "change",
             "candidates": [_card_cand("Pulled Pork Tacos", "pork"), _card_cand("Chicken Tacos", "chicken")]}]
    allergen_gate.begin_chat_turn()
    allergen_gate.set_chat_message("something else for that night")
    card = proposals.propose_plan_changes(week, rows)
    assert [c["meal_name"] for c in card["rows"][0]["candidates"]] == ["Chicken Tacos"]
    # Their own words naming it: theirs to have.
    allergen_gate.begin_chat_turn()
    allergen_gate.set_chat_message("make it pulled pork tacos")
    card = proposals.propose_plan_changes(week, rows)
    assert [c["meal_name"] for c in card["rows"][0]["candidates"]] == ["Pulled Pork Tacos", "Chicken Tacos"]
    # Another (a tap, no words) never offers it.
    allergen_gate.begin_chat_turn()
    picks = iter([dict(_card_cand("Bacon Mac", "bacon"), is_new_recipe=True),
                  dict(_card_cand("Fish Tacos", "cod"), is_new_recipe=True)])
    out = proposals.another_for_row(card["proposal_id"], 0, picker=lambda ctx: next(picks))
    assert out["status"] == "picked"
    row = out["proposal"]["rows"][0]
    assert row["candidates"][row["chosen"]]["meal_name"] == "Fish Tacos"
