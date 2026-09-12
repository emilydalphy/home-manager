"""
Chores questions moved out of first-run onboarding onto their own page.

Emily decided 2026-09-05 (Loop Board item 20a) that the chores
questionnaire — pets, home type, upkeep standard, chore rotation, and so
on — should no longer be a required stop inside onboarding. The meal loop
is what a brand-new household actually came here for, and the reveal (the
first sample week) is the payoff for the questions that came before it;
making them sit through an unrelated module's setup right after seeing
their first plan interrupted that loop for no reason tied to meal planning
at all.

The fix has two halves, and both need covering:

1. Onboarding itself must complete end to end — household, rhythm, the
   minimum-viable answer set, and the first-plan generation — without ever
   touching a chores endpoint. If it silently needed one of those calls to
   succeed, removing the chores steps from the wizard would have broken
   onboarding rather than shortened it.
2. The chores questions must still exist and still save, just somewhere
   else: a standalone GET /chores-setup page, saving through the exact
   same /api/onboarding/household and /api/onboarding/chores-profile
   routes onboarding always used (nothing about the backend changed, only
   who calls it and when).

See also tests/test_onboarding_inputs.py, which pins onboarding.html's
JavaScript directly (that step-chores-q/step-chores-review no longer exist,
and that 'reveal' is the last entry in ALL_STEPS) — this file only exercises
the server side.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import tools

REPO = Path(__file__).resolve().parent.parent
ONBOARDING = (REPO / "static" / "onboarding.html").read_text()
CHORES_SETUP = (REPO / "static" / "chores-setup.html").read_text()
SHELL_JS = (REPO / "static" / "shell.js").read_text()


@pytest.fixture
def stub_model(monkeypatch):
    """A fixed, minimal week from the model — no real API call."""
    from app import agent
    import datetime

    def _this_monday():
        today = datetime.date.today()
        return (today - datetime.timedelta(days=today.weekday())).isoformat()

    monkeypatch.setattr(
        agent,
        "generate_weekly_plan_llm",
        lambda ctx: [
            {"date": _this_monday(), "slot": "dinner", "meal_name": "Chili", "food_groups": []}
        ],
    )


def test_onboarding_completes_without_any_chores_fields(signed_in, stub_model):
    """
    The full onboarding sequence the wizard actually drives — household,
    rhythm, the minimum-viable answers, then the first-plan generation —
    succeeds without a single request touching a chores endpoint. This is
    the literal claim behind "the meal loop is uninterrupted": onboarding
    was never depending on chores data to finish.
    """
    res = signed_in.post(
        "/api/onboarding/household",
        json={"members": [{"name": "Jamie"}], "pets": [], "goals": ""},
    )
    assert res.status_code == 200

    res = signed_in.post(
        "/api/onboarding/rhythm",
        json={
            "lunch_location": {"Jamie": "home"},
            "meals_together": "dinner_only",
            "cooking_role": "turns",
            "dinner_window": "6_8",
            "planning_anchor": "as_we_go",
            "leftovers_stance": "fine_sometimes",
        },
    )
    assert res.status_code == 200

    res = signed_in.post(
        "/api/onboarding/answers",
        json={
            "member_names": ["Jamie"],
            "household_restrictions": {},
            "eating_style": "omnivore",
            "wont_eat": [],
            "excited_about": [],
            "dinners_per_week": 5,
            "breakfasts_per_week": 0,
            "lunches_per_week": 0,
            "snacks_per_week": 0,
        },
    )
    assert res.status_code == 200

    res = signed_in.post("/api/onboarding/generate-first-plan")
    assert res.status_code == 200, res.text

    # Nothing about chores was ever asked or saved along the way.
    status = tools.get_household_setup_status()
    assert status["has_chores"] is False
    profile = tools.get_chores_profile()
    assert profile["has_profile"] is False


def test_chores_setup_route_serves_the_standalone_page(signed_in):
    """GET /chores-setup exists and serves the relocated questionnaire, not onboarding.html or a 404."""
    res = signed_in.get("/chores-setup")
    assert res.status_code == 200
    assert "rotation-chips" in res.text
    assert "home-type-chips" in res.text


def test_chores_setup_profile_route_still_saves(signed_in):
    """
    The backend route the standalone page posts to is unchanged and still
    works — "do not rebuild the questions" cuts both ways: the save path
    that already worked must keep working untouched.
    """
    res = signed_in.post(
        "/api/onboarding/chores-profile",
        json={
            "home_type": "House",
            "bedrooms": 3,
            "bathrooms": 2,
            "has_yard": True,
            "standard": "standard",
            "rotation_members": ["Jamie"],
            "existing_help": "",
            "existing_help_frequency": "",
            "include_notes": "",
            "exclude_notes": "",
        },
    )
    assert res.status_code == 200
    assert res.json()["saved"] is True

    profile = tools.get_chores_profile()
    assert profile["has_profile"] is True
    assert profile["home_type"] == "House"
    assert profile["bedrooms"] == 3
    assert profile["rotation_members"] == ["Jamie"]


def test_chores_today_reports_whether_setup_has_happened(signed_in):
    """
    /api/chores/today carries a chores_set_up flag so Today's chores card
    can decide whether to offer the "Want help with chores too?" link
    without a second round trip. False until a profile is saved or a chore
    exists; true afterward, even with nothing due today.
    """
    tools.set_chores_enabled(True)
    res = signed_in.get("/api/chores/today")
    assert res.status_code == 200
    assert res.json()["chores_set_up"] is False

    signed_in.post(
        "/api/onboarding/chores-profile",
        json={"home_type": "House", "standard": "standard"},
    )

    res = signed_in.get("/api/chores/today")
    assert res.status_code == 200
    assert res.json()["chores_set_up"] is True


def test_onboarding_html_no_longer_has_the_chores_steps():
    """The two chores step divs, and the button that led to them, are gone from onboarding.html."""
    assert 'id="step-chores-q"' not in ONBOARDING
    assert 'id="step-chores-review"' not in ONBOARDING
    assert 'id="reveal-chores-btn"' not in ONBOARDING
    assert "OFFER_CHORES_AFTER_REVEAL" not in ONBOARDING


def test_reveal_is_the_last_step_in_onboarding():
    """
    ALL_STEPS drives showStep's visibility toggling — 'reveal' being its
    last entry is what makes the first-week reveal onboarding's actual
    last screen, with nothing left for its two actions to navigate into
    except away from onboarding entirely.
    """
    m = re.search(r"const ALL_STEPS = \[([^\]]*)\]", ONBOARDING)
    assert m, "ALL_STEPS not found in onboarding.html"
    steps = [s.strip().strip("'") for s in m.group(1).split(",")]
    assert steps[-1] == "reveal", f"'reveal' must be the last step; got {steps}"
    assert "chores-q" not in steps and "chores-review" not in steps


def test_progress_dots_still_match_the_question_step_count():
    """
    Item C: the progress cue must cover the steps it represents.

    UPDATED 2026-09-12 (the setup-luxury branch): the ten-segment strip is
    gone. Each question screen carries the welcome screens' four-dot pager
    instead, drawn by renderProgress from QUESTION_SECTIONS — one entry per
    question step, naming which of the four stops it belongs to. What is
    worth pinning is the same property as before, in the new shape: every
    step between the intro and 'reveal' has a section, nothing else does,
    and the long dot only ever moves forward.
    """
    all_steps_m = re.search(r"const ALL_STEPS = \[([^\]]*)\]", ONBOARDING)
    assert all_steps_m
    all_steps = [s.strip().strip("'") for s in all_steps_m.group(1).split(",")]
    intro_m = re.search(r"const INTRO_STEPS = \[([^\]]*)\]", ONBOARDING)
    assert intro_m
    intro_steps = [s.strip().strip("'") for s in intro_m.group(1).split(",")]
    sections_m = re.search(r"const QUESTION_SECTIONS = \{([^}]*)\}", ONBOARDING)
    assert sections_m, "QUESTION_SECTIONS is gone — see renderProgress"
    sections = dict(re.findall(r"'([\w-]+)':\s*(\d+)", sections_m.group(1)))

    assert all_steps[: len(intro_steps)] == intro_steps, "the intro isn't at the front"
    assert all_steps[-1] == "reveal"
    question_steps = all_steps[len(intro_steps): all_steps.index("reveal")]
    assert list(sections) == question_steps, (
        "QUESTION_SECTIONS and the question steps of ALL_STEPS disagree — a "
        "question with no section shows no pager"
    )
    numbers = [int(sections[k]) for k in question_steps]
    assert numbers == sorted(numbers), "the long dot would move backwards"
    assert numbers[0] == 1 and numbers[-1] == 4 and set(numbers) == {1, 2, 3, 4}
    assert "const SECTION_COUNT = 4;" in ONBOARDING


def test_chores_setup_page_reuses_the_same_save_route():
    """
    Not rebuilt: the standalone page posts to the same two endpoints
    onboarding always used for this data.
    """
    assert "/api/onboarding/household" in CHORES_SETUP
    assert "/api/onboarding/chores-profile" in CHORES_SETUP


def test_today_chores_card_is_gated_by_the_household_switch():
    """
    Emily decided 2026-09-08 (option 1b on the Chores ticket) that the
    beta is meals-only: Today's "Your chores" card must not render, and
    must not fetch /api/chores/today, while Chores is unvalidated. Until
    2026-09-12 that was a single constant in static/shell.js, false for
    every household; it is a per-household switch now (Loop Board
    "Chores v1: Who sees it — a per-household switch"): households.
    chores_enabled, read off /api/whoami into shellWho and consulted
    through one function, choresEnabled().

    This pins three things at the source level: the constant is gone (a
    reintroduced global would put the tester's house back in step with
    Emily's), the chores card markup is only emitted behind
    choresEnabled() (not merely hidden via CSS), and the loadChores()
    call — the one that hits /api/chores/today — is behind the same
    function, so a house with the switch off makes zero chores requests.
    The behaviour itself (built vs. not built, request vs. no request)
    is exercised under node in tests/test_chores_switch.py.
    """
    assert "SHOW_CHORES_ON_TODAY" not in SHELL_JS.replace(
        "SHOW_CHORES_ON_TODAY = false sat in this slot", ""
    ), "the global chores constant is back — the switch is per household now"
    assert "function choresEnabled()" in SHELL_JS
    assert "shellWho.chores_enabled" in SHELL_JS

    # The chores card markup (the div that loadChores/renderChores fill
    # in) is only built when the switch is on — not present unconditionally.
    chores_card_idx = SHELL_JS.index('class="shell-card chores-card"')
    guard_idx = SHELL_JS.rindex("choresEnabled() ?", 0, chores_card_idx)
    assert guard_idx != -1, "chores card markup must be gated by choresEnabled()"

    # loadChores() — the fetch('/api/chores/today') caller — is only
    # invoked when the switch is on, so an off house skips the request.
    load_chores_call_idx = SHELL_JS.index("loadChores(panel)", SHELL_JS.index("await Promise.all"))
    call_guard_idx = SHELL_JS.rindex("choresEnabled() ?", 0, load_chores_call_idx)
    assert call_guard_idx != -1, "the loadChores(panel) call site must be gated by choresEnabled()"
