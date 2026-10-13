"""
Onboarding asks "Who usually cooks dinner?" — and only that, of the
Settings-only questions (Loop Board "Decide: which Settings questions should
onboarding also ask?", Emily 2026-10-12: "add only 'Who usually cooks?'").

It is the cooking_role rhythm fact Preferences' "Who cooks" row edits, saved
at the end of setup with the rest of the rhythm, and skippable: nothing is
ever assumed about who cooks (rhythm.set_cooking_role's docstring).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("test_onboarding_go_back", _HERE / "test_onboarding_go_back.py")
_go_back = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_go_back)
_needs_node, _fn, _const, _run = _go_back._needs_node, _go_back._fn, _go_back._const, _go_back._run
_step_markup = _go_back._step_markup


def test_it_is_the_last_household_question():
    steps = json.loads(_const("ALL_STEPS").split("=", 1)[1].strip().rstrip(";").replace("'", '"'))
    assert steps[steps.index("restrictions") + 1] == "who-cooks"
    assert steps[steps.index("who-cooks") + 1] == "meals-days"
    markup = _step_markup("step-who-cooks")
    assert '<h1 class="q-title">Who usually cooks dinner?</h1>' in markup
    assert 'id="who-cooks-skip">Skip</span>' in markup


@_needs_node
def test_the_answers_are_the_adults_then_turns_then_whoevers_free_and_only_an_answer_is_sent():
    out = _run(_fn("cookAdultNames") + _fn("whoCooksOptions") + """
let MEMBERS = [{ name: 'Emily', age_group: 'adult' }, { name: 'Vineeth', age_group: 'adult' },
               { name: 'Leo', age_group: 'child' }];
function currentMembers() { return MEMBERS; }
const two = whoCooksOptions(cookAdultNames()).map(o => o.label);
MEMBERS = [{ name: 'Emily', age_group: 'adult' }, { name: 'Leo', age_group: 'child' }];
const one = whoCooksOptions(cookAdultNames()).map(o => o.label);
console.log(JSON.stringify({ two: two, one: one }));
""")
    # A child is never offered as the cook; "We take turns" needs two adults.
    assert out["two"] == ["Emily", "Vineeth", "We take turns", "Whoever’s free"]
    assert out["one"] == ["Emily", "Whoever’s free"]

    save = _fn("saveRhythmAnswers")
    # Unanswered (or skipped) sends no cooking_role at all, which the route
    # reads as "leave the fact alone"; a named cook must still be in the
    # household when setup ends.
    assert "if (cookRole === 'one_person' && cookAdultNames().indexOf(cookWho) !== -1) {" in save
    assert "body.cooking_role_who = cookWho;" in save


def test_the_route_saves_it_as_the_cooking_role(signed_in):
    from app import tools

    tools.add_member("Emily")
    tools.add_member("Vineeth")
    res = signed_in.post("/api/onboarding/rhythm",
                         json={"cooking_role": "one_person", "cooking_role_who": "Vineeth"})
    assert res.status_code == 200, res.text
    assert tools.get_household_rhythm()["cooking_role"] == {"value": "one_person", "who": "Vineeth"}
