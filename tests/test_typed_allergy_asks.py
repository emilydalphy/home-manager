"""
An allergy typed as a What we know note is asked about, then enforced
(Loop Board, Emily 2026-10-31: "ask, then enforce").

"Sam is allergic to sesame" typed into What we know was saved as an
ordinary note — a hint to the planner the allergy check never read. Now the
save answers with one question, "Save sesame as Sam’s allergy?"; the yes
writes the restriction Who's here keeps, and from then on sesame is held
back like any other allergy. The note is saved either way, and nothing is
ever saved as an allergy without the tap — including at the next restart.
"""
import pytest

from app import db, tools
from app.tools import allergen_gate, coordination


@pytest.fixture
def sam():
    tools.add_member("Sam")


def _sam():
    return next(m for m in tools.list_members() if m["name"] == "Sam")


def _add(client, text):
    res = client.post("/api/facts/add", json={"category": "people", "text": text})
    assert res.status_code == 200, res.text
    return res.json()


def test_the_note_is_saved_and_answered_with_one_question(signed_in, sam):
    out = _add(signed_in, "Sam is allergic to sesame")

    assert out["added"] is True
    assert out["allergy_offer"]["question"] == "Save sesame as Sam’s allergy?"
    assert out["allergy_offer"]["foods"] == ["sesame"]
    assert [m["name"] for m in out["allergy_offer"]["members"]] == ["Sam"]
    # The note is on What we know, and nothing has been enforced yet.
    assert any(f["text"] == "Sam is allergic to sesame" for f in tools.get_facts())
    assert _sam()["dietary_restrictions"] == []
    assert allergen_gate.hard_clashes("Sesame Noodles", [{"item": "toasted sesame oil", "qty": "1 tbsp"}]) == []


def test_the_yes_writes_sams_allergy_and_the_gate_holds_sesame_back(signed_in, sam):
    out = _add(signed_in, "Sam is allergic to sesame")

    res = signed_in.post(f"/api/facts/{out['id']}/allergy", json={"foods": out["allergy_offer"]["foods"]})

    assert res.status_code == 200, res.text
    assert _sam()["dietary_restrictions"] == ["allergy: sesame"]
    # The same reader the draft, the swaps and the approval check use.
    clashes = allergen_gate.hard_clashes("Sesame Noodles", [{"item": "toasted sesame oil", "qty": "1 tbsp"}])
    assert clashes and clashes[0]["member"] == "Sam"
    assert any(a["source"] == "dietary_restriction" and a["member"] == "Sam"
               for a in coordination._avoidances())
    # Asked and answered: the same note offers nothing more.
    assert tools.allergy_offer_for_fact(out["id"]) is None


def test_a_restart_still_saves_a_typed_allergy_nobody_tapped_yes_to(sam):
    """The safety net stays (review, 2026-11-01): the question is asked only
    when a note is added or edited on the screen, so a note already on
    file, one filed by chat, or a question nobody tapped must still become
    the allergy at the next start — exactly as on main. Whether a note
    should EVER be enforced without the tap is Emily's call (on the card)."""
    tools.add_fact("people", "Sam is allergic to sesame")

    db.init_db()

    assert "allergy: sesame" in _sam()["dietary_restrictions"]


def test_a_client_cannot_save_a_food_the_note_never_offered(signed_in, sam):
    out = _add(signed_in, "Sam is allergic to sesame")

    res = signed_in.post(f"/api/facts/{out['id']}/allergy", json={"foods": ["sesame", "wheat"]})

    assert res.status_code == 409
    assert _sam()["dietary_restrictions"] == []


def test_a_note_edited_since_the_question_saves_nothing(signed_in, sam):
    out = _add(signed_in, "Sam is allergic to sesame")
    signed_in.post(f"/api/facts/{out['id']}/update", json={"text": "Sam is allergic to peanuts"})

    res = signed_in.post(f"/api/facts/{out['id']}/allergy", json={"foods": ["sesame"]})

    assert res.status_code == 409
    assert _sam()["dietary_restrictions"] == []


@pytest.mark.parametrize("text", [
    "Sam has no school on Fridays",     # "no" with no allergy wording
    "No shellfish in this house",       # names nobody: not one person's allergy
    "Sam loves sesame noodles",
])
def test_no_question_where_there_is_no_persons_allergy(signed_in, sam, text):
    assert _add(signed_in, text).get("allergy_offer") is None


def test_the_cards_shapes_are_all_asked(sam):
    tools.add_member("Mia")
    assert tools.allergy_offer("Sam can't have dairy")["foods"] == ["dairy"]
    assert tools.allergy_offer("Sam: no shellfish ever")["foods"] == ["shellfish"]
    two = tools.allergy_offer("Sam and Mia are allergic to sesame and peanuts")
    assert two["question"] == "Save sesame and peanuts as Sam’s and Mia’s allergies?"


def test_an_allergy_already_on_sams_chips_is_not_asked_again(signed_in, sam):
    tools.set_member_dietary_restrictions("Sam", ["Sesame"])
    assert _add(signed_in, "Sam is allergic to sesame").get("allergy_offer") is None


def test_no_shellfish_ever_as_a_must_avoid_note_holds_shrimp_back(sam):
    tools.add_fact("people", "Sam: no shellfish ever", hard=True)
    assert allergen_gate.hard_clashes("Shrimp Tacos", [{"item": "shrimp", "qty": "1 lb"}])


def test_what_we_know_asks_the_question_and_posts_the_yes():
    """The screen half (2026-11-01): the note's save answers with
    allergy_offer, and What we know shows its question with one Save tap
    that posts the offered foods to /api/facts/{id}/allergy — on an add
    and on an edit. Nothing is posted without the tap."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text(encoding="utf-8")
    ask = js.split("function wwkAskAllergy(", 1)[1].split("\n  }\n", 1)[0]
    assert "showToast(offer.question" in ask and "label: 'Save'" in ask
    assert "wwkPost('/api/facts/' + factId + '/allergy', { foods: offer.foods || [] })" in ask
    add = js.split("async function wwkAddFact(", 1)[1].split("\n  }\n", 1)[0]
    assert "result.allergy_offer" in add and "wwkAskAllergy(offer.id, offer.offer)" in add
    upd = js.split("function wwkUpdateFact(", 1)[1].split("\n  }\n", 1)[0]
    assert "wwkAskAllergy(id, result.allergy_offer)" in upd
