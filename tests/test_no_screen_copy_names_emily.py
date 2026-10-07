"""
Every household saw "Tell Emily what happened" on the feedback sheet
(defect hunt 2026-10-07, Medium). Emily built Pomona; most households who
sign up have never met her, and "text her too" was advice they couldn't
take. The words are neutral now — "Tell us what happened", "we read every
one of these".

The guard: no screen copy under static/ names Emily. Comments may (the
code's history is full of "Emily, 2026-09-12" and should stay so); only
text a person could see counts — JS strings and HTML markup outside
comments.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"

# Not screen copy for a household to read, each for its reason:
#  - legal/: unsigned drafts whose [bracketed] fills name who must confirm a
#    clause before launch — Emily's and the lawyer's to settle, not wording.
_SKIP_DIRS = {"legal"}


def _strip_js(src: str) -> str:
    """JS with its comments taken out and its strings kept. Strings are
    walked so a '//' inside one (a URL) is not read as a comment."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif c != "`" and src[j] == "\n":
                    break
                j += 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_html(src: str) -> str:
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"(<script[^>]*>)(.*?)(</script>)",
                 lambda m: m.group(1) + _strip_js(m.group(2)) + m.group(3), src, flags=re.S)
    return re.sub(r"(<style[^>]*>)(.*?)(</style>)",
                  lambda m: m.group(1) + re.sub(r"/\*.*?\*/", "", m.group(2), flags=re.S) + m.group(3),
                  src, flags=re.S)


def _screen_text(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    return _strip_js(src) if path.suffix == ".js" else _strip_html(src)


def _naming_emily() -> list[str]:
    found = []
    for path in sorted(STATIC.rglob("*")):
        if path.suffix not in (".js", ".html") or _SKIP_DIRS & set(path.relative_to(STATIC).parts):
            continue
        for n, line in enumerate(_screen_text(path).splitlines(), 1):
            if "Emily" in line:
                found.append(f"{path.relative_to(STATIC)}: {line.strip()[:120]}")
    return found


def test_no_screen_copy_in_static_names_emily():
    assert _naming_emily() == []


def test_the_scanner_sees_strings_and_markup_but_not_comments():
    """GUARD on the guard: it must catch the shape that shipped."""
    js = "// Emily, 2026-09-12\n/* Emily's call */\nvar a = 'Tell Emily what happened';\nvar u = 'https://x//y';"
    assert "Tell Emily" in _strip_js(js) and "2026-09-12" not in _strip_js(js) and "call" not in _strip_js(js)
    assert "https://x//y" in _strip_js(js)
    html = "<!-- Emily -->\n<p>Ask Emily</p>\n<script>// Emily\nvar b = \"Emily\";</script>"
    out = _strip_html(html)
    assert out.count("Emily") == 2


def test_the_feedback_sheet_reads_the_neutral_words():
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert "Tell us what happened" in shell
    assert "Something not working? Tell us<" in shell
