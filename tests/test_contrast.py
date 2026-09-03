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

Deliberately NOT asserted, all measured on --ground and all short of 4.5:1:
--ink-inactive #948970 (3.21), --ink-done-soft #C6BCA9 (1.75),
--ink-secondary #7C7161 (4.41) and --ink-muted #7E7360 (4.33). They are on
the ticket for Emily, because forcing every one of the quiet greys to
4.5:1 collapses five deliberately-distinct values into nearly the same
colour -- a design decision, not a bug fix.
"""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "static"
THEME = (STATIC / "theme.css").read_text()

# The two backdrops every one of these colours is actually drawn on.
GROUND = "#FBF6EE"   # the app canvas
SURFACE = "#FFFDF8"  # cards and list containers
SPRUCE = "#1B3328"   # the sign-in screen and hero panels

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
    """The value of a custom property as theme.css defines it on :root."""
    match = re.search(rf"^\s*{re.escape(name)}:\s*([^;]+);", THEME, re.MULTILINE)
    assert match, f"{name} is not defined in theme.css any more"
    return match.group(1).strip()


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
    shell_js = (STATIC / "shell.js").read_text()
    tick = re.search(r"chore-checkbox.*?</svg>", shell_js, re.DOTALL)
    assert tick, "the chore checkbox no longer renders a tick — update this test"
    assert 'stroke="#fff"' not in tick.group(0), (
        "the completed-chore tick is hardcoded white again; ivory on a light "
        "accent is the one thing the palette forbids outright"
    )
    assert 'stroke="currentColor"' in tick.group(0)

    shell_css = (STATIC / "shell.css").read_text()
    done_checkbox = re.search(
        r"\.chore-row\.done \.chore-checkbox \{[^}]*\}", shell_css, re.DOTALL
    )
    assert done_checkbox and "--on-accent-ink" in done_checkbox.group(0), (
        "the checked chore checkbox must set a dark ink for its tick to inherit"
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


SPRUCE_RAISED = "#24402F"  # .signin-field's own fill, lighter than the page

# Each translucent-ivory line on the sign-in screen, with the colour it is
# ACTUALLY drawn on. These differ, and that is the whole point: an earlier
# version of this test measured every line against the page background and
# so certified a still-failing one as fixed. Light text on a *lighter*
# backdrop has LESS contrast, not more, so guessing the darker ground
# flatters the result instead of being conservative.
SIGN_IN_TEXT = [
    # (what it is, css rule it lives in, backdrop and why)
    ("passphrase placeholder", ".signin-field input::placeholder",
     SPRUCE_RAISED, "the input is transparent, so this sits on the field's fill"),
    ("helper line under the field", ".signin-helper",
     SPRUCE, "in .signin-bottom, straight on the page"),
]


def test_the_sign_in_screen_is_readable():
    """
    The first screen anyone ever sees, and the one screen a new tester has
    to read carefully -- it is where she types a passphrase she was texted.
    """
    login = (STATIC / "login.html").read_text()
    # Scoped to LIGHT mode only: stage 3 added its own
    # `@media (prefers-color-scheme: dark)` block to this same page, with
    # its own translucent-ivory lines, its own backdrop reasoning, and its
    # own numbers documented right there in a comment. That block is out of
    # scope for this (light-mode-only) ticket, so it is excluded here rather
    # than forcing this count to track an unrelated stage's future edits.
    light_only = re.sub(
        r"@media \(prefers-color-scheme:\s*dark\)\s*\{.*?\n  \}", "", login, flags=re.DOTALL
    )
    alphas = re.findall(r"color:\s*rgba\(246,\s*238,\s*225,\s*(0?\.\d+)\)", light_only)
    assert len(alphas) == len(SIGN_IN_TEXT) + 1, (
        f"the sign-in screen has {len(alphas)} translucent-ivory text colours in "
        f"light mode; this test knows the backdrop for {len(SIGN_IN_TEXT)} of them "
        f"plus the tagline. A new one needs its own backdrop looked up, not assumed."
    )

    for what, rule, backdrop, why in SIGN_IN_TEXT:
        block = re.search(rf"{re.escape(rule)}\s*\{{[^}}]*\}}", login, re.DOTALL)
        assert block, f"{rule} is gone — update this test"
        colour = re.search(r"color:\s*(rgba\([^)]*\))", block.group(0))
        assert colour, f"{rule} no longer sets a colour"
        ratio = contrast(colour.group(1), backdrop)
        assert ratio >= AA_NORMAL, (
            f"{what} is {ratio:.2f}:1 on {backdrop} ({why}) — under AA. "
            f"Raise the alpha rather than adding a new colour."
        )


def test_the_sign_in_field_fill_is_what_this_test_thinks_it_is():
    """
    The test above is only as good as its backdrop. If .signin-field is
    ever repainted, the placeholder's real contrast moves and nothing else
    would notice.
    """
    login = (STATIC / "login.html").read_text()
    field = re.search(r"\.signin-field \{[^}]*\}", login, re.DOTALL)
    assert field and "var(--spruce-raised)" in field.group(0), (
        ".signin-field's background changed — re-measure the placeholder "
        "against the new fill and update SPRUCE_RAISED"
    )
    assert token("--spruce-raised").lower() == SPRUCE_RAISED.lower()


# Every rule in the app that strikes text through, with the token it uses.
# A strikethrough means "done", and every one of these is real text a
# household still has to be able to read.
STRUCK_TEXT_RULES = [
    ("static/shell.css", r"\.chore-row\.done \.chore-name \{[^}]*\}", "completed chore"),
    ("static/shell.css", r"\.gro-row\.done \.gro-name \{[^}]*\}", "bought grocery item"),
    ("static/grocery.html", r"\.gl-row\.done \.gl-name \{[^}]*\}", "bought grocery item (standalone page)"),
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
