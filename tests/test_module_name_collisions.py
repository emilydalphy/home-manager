"""
No module in app/tools/ shares its name with a name the package re-exports.

There are none, and this proves it. There used to be two —
swap_options.py and yesterday_check.py, each shadowed by the function it
exports — held on a shrink-only allowlist until 2026-10-25, when both
modules were renamed (swap_option_picks.py, yesterday_ask.py) and the
allowlist went with them. Neither was ever live: nothing inside app/ did
`from . import` either as a module.

What makes it worth a guard is that the class has already been paid for
once, and the payment was a SILENT failure rather than a loud one. From
the Decision log, 2026-09-24 (the chat-can-unbatch entry):

    THE MODULE IS batch_undo.py AND NOT unbatch.py, and the reason is worth
    a line because it cost a run to find. app/tools/__init__.py re-exports
    the FUNCTION unbatch, which hangs it off the package under the module's
    own name — so `from . import unbatch as _unbatch` inside cook_ahead.py
    got the function, and apply_prep_day_batches died on AttributeError
    inside approve_weekly_plan's own except, i.e. silently, with the week
    approved and nothing batched.

The answer then was to rename the module. The two above re-introduced
the collision AFTER that lesson was written down, which is the evidence
that a note in a file nobody greps before naming a module does not
prevent the next one. A test does. There is deliberately no allowlist any
more: a new collision is fixed by naming the module differently, never by
waving it through.

The sweep reads __init__.py with `ast` rather than grepping it, because a
text sweep in this repo has already been defeated by a pure reformat — see
tests/test_leftover_chain_household_filter.py's own account of that.
"""

import ast
import pathlib

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "app" / "tools"


def _reexported_names(tools: pathlib.Path = TOOLS) -> set[str]:
    """Every name app/tools/__init__.py binds — the package's public face."""
    tree = ast.parse((tools / "__init__.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _module_names(tools: pathlib.Path = TOOLS) -> set[str]:
    return {p.stem for p in tools.glob("*.py") if p.stem != "__init__"}


def _collisions(tools: pathlib.Path = TOOLS) -> set[str]:
    return _module_names(tools) & _reexported_names(tools)


def test_no_module_is_shadowed_by_a_name_the_package_reexports():
    """
    The rule itself. A module whose name is also a re-exported name cannot
    be reached as a module from inside the package: the re-export wins, so
    `from . import <name>` hands back the function and the first attribute
    access on it raises AttributeError — inside whatever except block
    happens to be open.
    """
    found = _collisions()
    assert not found, (
        "These modules in app/tools/ are shadowed by a name __init__.py "
        f"re-exports: {sorted(found)}. Rename the MODULE (the precedents "
        "are unbatch.py -> batch_undo.py, 2026-09-24, and swap_options.py "
        "-> swap_option_picks.py / yesterday_check.py -> yesterday_ask.py, "
        "2026-10-25). A collision means `from . import <name>` inside the "
        "package returns the function, not the module."
    )


def test_the_sweep_can_actually_see_a_collision(tmp_path):
    """
    Non-vacuity, proved rather than asserted. With nothing real left to
    point at, the sweep is run over a two-file package built for the
    purpose — a module that re-exports a function under its own name, the
    exact shape swap_options.py had — and must report it. Without this, a
    sweep that had quietly stopped matching would pass for ever, which is
    the failure this repo has recorded twice.
    """
    (tmp_path / "picks.py").write_text("def picks():\n    return []\n")
    (tmp_path / "other.py").write_text("def unrelated():\n    return 1\n")
    (tmp_path / "__init__.py").write_text(
        "from .picks import (  # noqa: F401\n    picks,\n)\n"
        "from .other import unrelated  # noqa: F401\n"
    )
    assert _collisions(tmp_path) == {"picks"}


def test_the_sweep_does_not_flag_a_module_whose_functions_are_named_differently():
    """
    The other direction, so the rule cannot be satisfied by flagging
    everything. batch_undo.py is the module the 2026-09-24 rename produced:
    it exports `unbatch`, which is deliberately NOT its own name, and it
    must come back clean.
    """
    assert "batch_undo" in _module_names()
    assert "unbatch" in _reexported_names()
    assert "batch_undo" not in _collisions()


def test_the_renamed_modules_are_modules_and_the_functions_kept_their_names():
    """
    What the 2026-10-25 rename promised, at runtime rather than in the ast:
    the modules are reachable as modules from the package, and the names
    routes, tests and the chat know — tools.swap_options,
    tools.yesterday_check — are still the functions they always were.
    """
    import types

    from app import tools
    from app.tools import swap_option_picks, yesterday_ask

    assert isinstance(swap_option_picks, types.ModuleType)
    assert isinstance(yesterday_ask, types.ModuleType)
    assert tools.swap_options is swap_option_picks.swap_options
    assert tools.yesterday_check is yesterday_ask.yesterday_check
    assert callable(tools.swap_options) and callable(tools.yesterday_check)
