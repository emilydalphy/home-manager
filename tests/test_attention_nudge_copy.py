"""
"core loop handoffs, slice 3" item 5: the feedback-nudge summary now talks
TO the cook ("How did X go?") instead of describing them to an agent
("...hasn't been rated yet — worth asking how it went") — it's shown
verbatim on the Cook screen's attention card (static/shell.js's
cookAttentionHtml), not just read by the assistant.
"""
import datetime

from app import tools


def test_feedback_nudge_summary_asks_the_cook_directly():
    tools.add_recipe("Chicken Skewers", ingredients=[{"item": "chicken thigh", "qty": "1 lb"}])
    today = datetime.date.today().isoformat()
    entry = tools.plan_meal(today, "Chicken Skewers", slot="dinner")
    tools.check_off_meal(entry["entry_id"])

    items = tools.get_attention_items()
    nudges = [i for i in items if i["kind"] == "feedback_nudge"]
    assert len(nudges) == 1
    assert nudges[0]["summary"] == "How did Chicken Skewers go?"
    assert "hasn't been rated" not in nudges[0]["summary"]
