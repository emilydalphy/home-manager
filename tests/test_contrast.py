"""
Text has to be readable, and "it looks fine" is not a measurement.

Four light-mode contrast failures were found by measuring the rendered app
during the dark-mode work and deliberately left for their own ticket. They
had all survived a full brand repaint, because nothing in the project could
state a contrast requirement -- the palette was reviewed by eye, and an eye
adapts to what it has already seen.

So the requirement is stated here instead, against theme.css itself. These
are ratios, not opinions: WCAG AA asks for 4.5:1 for normal-size text, and
every colour below is used at normal size. A future palette change that
takes a value back under the line fails this file rather than reaching a
household.

Bright Dusk (2026-11-05) folded The Inn's five quiet greys into one, Slate,
so the gap this file used to carry is closed: every text colour clears 4.5:1
on every surface it sits on (test_every_bright_dusk_pair_clears_aa).
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
THEME = (STATIC / "theme.css").read_text()

# The two backdrops every one of these colours is actually drawn on.
GROUND = "#FFFAF3"   # Warm Cream, the app canvas
SURFACE = "#FFFFFF"  # tiles and cards
BLUSH = "#FBE9D9"    # the switcher, round buttons

AA_NORMAL = 4.5


def _parse(colour: str) -> tuple[float, float, float, float]:
    colour = colour.strip()
    rgba = re.match(r"rgba?\(([^)]+)\)", colour)
    if rgba:
        parts = [p.strip() for p in rgba.group(1).split(",")]
        r, g, b = (float(p) for p in parts[:3])
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        return r, g, b, alpha
    hexed = colour.lstrip("#")
    if len(hexed) == 3:
        hexed = "".join(c * 2 for c in hexed)
    return int(hexed[0:2], 16), int(hexed[2:4], 16), int(hexed[4:6], 16), 1.0


def _relative_luminance(rgb) -> float:
    def channel(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    """
    WCAG 2.1 contrast ratio, compositing a translucent foreground over its
    backdrop first -- several of these colours are ivory at an alpha, and
    ignoring the alpha would report them as far more readable than they are.
    """
    fg, bg = _parse(foreground), _parse(background)
    alpha = fg[3]
    composited = tuple(fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))
    light, dark = sorted((_relative_luminance(composited), _relative_luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def token(name: str) -> str:
    """The value of a custom property as theme.css defines it on :root,
    following a legacy name to the Bright Dusk value it forwards to."""
    match = re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", THEME, re.MULTILINE)
    assert match, f"{name} is not defined in theme.css any more"
    value = match.group(1).strip()
    forwarded = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return token(forwarded.group(1)) if forwarded else value


@pytest.mark.parametrize("text, backdrop, floor", [
    ("--night-ink", GROUND, 14.3), ("--night-ink", SURFACE, 14.9), ("--night-ink", BLUSH, 12.6),
    ("--night-ink", "#FFA45C", 7.6),
    ("--slate", GROUND, 6.0), ("--slate", SURFACE, 6.2), ("--slate", BLUSH, 5.3),
    ("--clay", GROUND, 5.6), ("--clay", SURFACE, 5.9), ("--clay", BLUSH, 4.9),
    ("--on-dusk", "#1B4392", 8.6), ("--on-dusk-soft", "#1B4392", 6.0), ("--soft-apricot", "#1B4392", 5.8),
    ("--soft-apricot", "#14264A", 9.3), ("--on-dusk", "#14264A", 13.9),
])
def test_every_bright_dusk_pair_clears_aa(text, backdrop, floor):
    """The pairs DESIGN_SYSTEM.md records, measured against the live tokens."""
    ratio = contrast(token(text), backdrop)
    assert ratio >= AA_NORMAL and ratio >= floor, f"{text} on {backdrop} is {ratio:.2f}:1"


def test_the_contrast_maths_matches_a_known_reference():
    """
    A checker that always returns a big number would make every other test
    here pass while measuring nothing. Black on white is exactly 21:1, and
    a colour against itself is exactly 1:1.
    """
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast("#777777", "#777777") == pytest.approx(1.0, abs=0.01)
    # Half-transparent black over white lands midway, not at full strength.
    assert contrast("rgba(0, 0, 0, 0.5)", "#FFFFFF") < 21.0


@pytest.mark.parametrize("name", ["--ink-done", "--ink-placeholder"])
@pytest.mark.parametrize("backdrop", [GROUND, SURFACE])
def test_quiet_ink_is_still_readable(name, backdrop):
    """
    Struck-out list items and input placeholders are the two quietest kinds
    of real text in the app. Quiet is a design intention; illegible is not.
    """
    ratio = contrast(token(name), backdrop)
    assert ratio >= AA_NORMAL, (
        f"{name} = {token(name)} is only {ratio:.2f}:1 on {backdrop}, "
        f"under the {AA_NORMAL}:1 WCAG AA needs for normal text"
    )


def test_ink_on_a_light_accent_fill_is_dark():
    """
    Rule One of the palette: ivory on apricot or celadon fails outright.
    A brand sweep fixed nine instances and missed the completed-chore tick;
    this pins the rule to the token rather than to anyone remembering it.
    """
    for accent in ("--celadon", "--apricot"):
        ratio = contrast(token("--on-accent-ink"), token(accent))
        assert ratio >= AA_NORMAL, (
            f"--on-accent-ink on {accent} is {ratio:.2f}:1"
        )


def test_the_completed_chore_tick_is_not_hardcoded_white():
    """
    The specific bug: a white tick drawn on a celadon checkbox, at 1.87:1.
    It was invisible rather than subtle. The tick now inherits the
    checkbox's colour, so it can never disagree with the palette again.
    """
    # Since 2026-09-12 the chores rows draw the shared .tick (the same
    # control as the rest-of-today rows), whose glyph is TICK_ICON — so the
    # rule is pinned on that icon and on .tick.is-done's ink.
    shell_js = (STATIC / "shell.js").read_text()
    assert 'class="tick chore-tick' in shell_js, "the chore row no longer renders the shared tick — update this test"
    tick = re.search(r"var TICK_ICON =.*?</svg>", shell_js, re.DOTALL)
    assert tick, "TICK_ICON no longer renders an svg — update this test"
    assert 'stroke="#fff"' not in tick.group(0), (
        "the tick is hardcoded white again; ivory on a light "
        "accent is the one thing the palette forbids outright"
    )
    assert 'stroke="currentColor"' in tick.group(0)

    shell_css = (STATIC / "shell.css").read_text()
    done_tick = re.search(
        r"\.tick\.is-done \.tick-box \{[^}]*\}", shell_css, re.DOTALL
    )
    assert done_tick and "--on-accent-ink" in done_tick.group(0), (
        "the done tick must set a dark ink for its glyph to inherit"
    )


def test_placeholders_have_a_rule_of_their_own():
    """
    With no ::placeholder rule in the shared stylesheet, every field that
    didn't style its own inherited Chrome's default grey -- a colour picked
    against a white page, not against this palette's ivory.
    """
    # Matched as a *rule*, not as a substring: theme.css explains itself in
    # a comment that also contains the word "::placeholder", so an `in`
    # check passes even with the rule deleted.
    rule = re.search(r"::placeholder\s*\{[^}]*\}", THEME, re.DOTALL)
    assert rule, (
        "theme.css defines no ::placeholder rule, so placeholder colour "
        "falls back to the browser's default again"
    )
    assert "var(--ink-placeholder)" in rule.group(0)
    # Firefox dims placeholders unless opacity is restored, which would
    # quietly undo the value chosen above.
    assert "opacity: 1" in rule.group(0)


def test_the_sign_in_screen_is_readable():
    """
    The first screen anyone ever sees, and the one screen a new tester has
    to read carefully. Z-SignIn: slate and night ink on cream and white, every
    pair a solid token, so the numbers are the tokens' own.
    """
    login = (STATIC / "login.html").read_text()
    assert "rgba(" not in login, "no see-through ink: its contrast depends on what's behind it"
    assert contrast(token("--slate"), token("--cream")) >= AA_NORMAL
    assert contrast(token("--slate"), token("--white")) >= AA_NORMAL
    assert contrast(token("--night-ink"), token("--apricot")) >= AA_NORMAL
    assert contrast(token("--clay"), token("--cream")) >= AA_NORMAL


# Every rule in the app that strikes text through, with the token it uses.
# A strikethrough means "done", and every one of these is real text a
# household still has to be able to read.
STRUCK_TEXT_RULES = [
    ("static/shell.css", r"\.chore-row\.done \.chore-name \{[^}]*\}", "completed chore"),
    ("static/shell.css", r"\.gro-row\.done \.gro-name \{[^}]*\}", "bought grocery item"),
]


@pytest.mark.parametrize("path,pattern,what", STRUCK_TEXT_RULES)
def test_struck_through_text_uses_a_readable_token(path, pattern, what):
    """
    The specific miss this test exists for: the completed-chore *tick* was
    fixed to 7.2:1 while the chore *name* beside it stayed on --faded at
    3.21:1 -- one row, two verdicts. Checking the token values alone could
    not catch that, because --ink-done was correct; the call site was not.
    """
    source = (STATIC.parent / path).read_text()
    rule = re.search(pattern, source, re.DOTALL)
    assert rule, f"the rule for {what} moved — update STRUCK_TEXT_RULES"

    used = re.search(r"color:\s*var\((--[\w-]+)\)", rule.group(0))
    assert used, f"{what} no longer takes its colour from a token"

    for backdrop in (GROUND, SURFACE):
        ratio = contrast(token(used.group(1)), backdrop)
        assert ratio >= AA_NORMAL, (
            f"{what} uses {used.group(1)}, which is {ratio:.2f}:1 on "
            f"{backdrop} — struck through is not the same as unreadable"
        )
