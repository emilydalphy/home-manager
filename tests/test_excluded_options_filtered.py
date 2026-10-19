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
