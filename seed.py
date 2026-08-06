"""
Optional: seed some starter data so the app isn't empty on first run.
Run with: python seed.py
"""
from app.db import init_db
from app import tools

init_db()

tools.add_member("Alex")
tools.add_member("Sam")

tools.add_chore("Take out trash", frequency="weekly", category="cleaning", assignee_names=["Alex", "Sam"])
tools.add_chore("Vacuum living room", frequency="weekly", category="cleaning", assignee_names=["Alex", "Sam"])
tools.add_chore("Clean bathroom", frequency="biweekly", category="cleaning", assignee_names=["Sam"])
tools.add_chore("Wash dishes", frequency="daily", category="cleaning", assignee_names=["Alex", "Sam"])
tools.add_chore("Change HVAC filter", frequency="monthly", category="maintenance", assignee_names=["Alex"])

tools.generate_chore_schedule(days_ahead=14)

tools.add_recipe(
    "Chicken stir fry",
    ingredients=[
        {"item": "chicken breast", "qty": "1 lb"},
        {"item": "broccoli", "qty": "1 head"},
        {"item": "soy sauce", "qty": "1 bottle"},
        {"item": "rice", "qty": "2 cups"},
    ],
    notes="Quick weeknight dinner, ~25 min.",
)
tools.add_recipe(
    "Spaghetti and meatballs",
    ingredients=[
        {"item": "ground beef", "qty": "1 lb"},
        {"item": "spaghetti", "qty": "1 box"},
        {"item": "marinara sauce", "qty": "1 jar"},
        {"item": "parmesan", "qty": "1 block"},
    ],
)

tools.add_grocery_item("paper towels", category="household")
tools.add_grocery_item("milk", category="dairy")

print("Seeded starter chores, recipes, and grocery items.")
