"""
Shop's foot for the week's "have it" / "don't need" / freezer decisions
reads "Already had on hand · N" (Loop Board 3e31f4c0-5231-8161-a7d2-
f338cb33f975, 2026-09-21). It was "Not needed this week" — a label that
read as a verdict about the week rather than what actually happened,
which is that the kitchen already had the thing. The rows under it and
the "· from the freezer" tag are unchanged.
"""
from __future__ import annotations

from pathlib import Path

from shop_harness import needs_node, run

REPO = Path(__file__).resolve().parent.parent

_FOOT = """
groceryState.alreadyHaveSummary = { already_have: [
  { id: 1, item: 'Chicken thighs', quantity: '2 lb', removed_by: 'freezer' },
  { id: 2, item: 'Rice', quantity: '', removed_by: 'user' }
] };
"""


@needs_node
def test_the_foot_reads_already_had_on_hand_with_its_count():
    out = run(_FOOT + """
groceryState.openFlagKey = null;
var closed = groNotNeededHtml();
groceryState.openFlagKey = 'already-have';
var open = groNotNeededHtml();
console.log(JSON.stringify({ closed: closed, open: open }));
""")
    assert "Already had on hand &middot; 2" in out["closed"]
    assert "Not needed" not in out["closed"] and "Not needed" not in out["open"]
    # The rows and the freezer tag are exactly as they were.
    assert "Chicken thighs &middot; 2 lb &middot; from the freezer" in out["open"]
    # Copy sweep finding 11 (2026-09-23): the way back is the action, not a phrase.
    assert out["open"].count("Put back on the list") == 2
    assert "Actually, I need it" not in out["open"]


def test_the_old_label_is_gone_from_the_app():
    for rel in ("static/shell.js", "static/shell.css", "app/tools/defrost.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "Not needed this week" not in text, rel
