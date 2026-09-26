"""
No module in app/tools/ shares its name with a name the package re-exports.

READ THE SEVERITY HONESTLY: neither of the two current collisions is live.
Measured on 6f6a5b3 — nothing inside app/ does `from . import swap_options`
or `from . import yesterday_check`, so nothing is broken today. This is a
landmine, not a leak, and the allowlist below says so.

What makes it worth a guard is that the class has already been paid for
once, and the payment was a SILENT failure rather than a loud one. From
CLAUDE.md, 2026-09-24 (the chat-can-unbatch entry):

    THE MODULE IS batch_undo.py AND NOT unbatch.py, and the reason is worth
    a line because it cost a run to find. app/tools/__init__.py re-exports
    the FUNCTION unbatch, which hangs it off the package under the module's
    own name — so `from . import unbatch as _unbatch` inside cook_ahead.py
    got the function, and apply_prep_day_batches died on AttributeError
    inside approve_weekly_plan's own except, i.e. silently, with the week
    approved and nothing batched.

The answer then was to rename the module. Two modules have since
re-introduced the collision — BOTH ADDED AFTER that lesson was written
down, which is the evidence that a note in a file nobody greps before
naming a module does not prevent the next one. A test does.

Why this is a guard with an allowlist rather than a fix: renaming the two
modules changes import paths in app/ and tests/, and that is a separate,
mechanical change with its own review. The allowlist is the shape this repo
already uses for a sweep that lands module by module (see the id-scoping
card). It SHRINKS; it must never grow.

The sweep reads __init__.py with `ast` rather than grepping it, because a
text sweep in this repo has already been defeated by a pure reformat — see
tests/test_leftover_chain_household_filter.py's own account of that.
"""

import ast
import pathlib

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "app" / "tools"

# The two collisions present on 6f6a5b3. Neither is reachable: nothing in
# app/ imports either as a module. This list is allowed to shrink and never
# to grow — a new entry here is somebody silencing the guard instead of
# naming their module differently.
KNOWN_COLLISIONS = {"swap_options", "yesterday_check"}


def _reexported_names() -> set[str]:
    """Every name app/tools/__init__.py binds — the package's public face."""
    tree = ast.parse((TOOLS / "__init__.py").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _module_names() -> set[str]:
    return {p.stem for p in TOOLS.glob("*.py") if p.stem != "__init__"}


def _collisions() -> set[str]:
    return _module_names() & _reexported_names()


def test_no_module_is_shadowed_by_a_name_the_package_reexports():
    """
    The rule itself. A module whose name is also a re-exported name cannot
    be reached as a module from inside the package: the re-export wins, so
    `from . import <name>` hands back the function and the first attribute
    access on it raises AttributeError — inside whatever except block
    happens to be open.
    """
    unexpected = _collisions() - KNOWN_COLLISIONS
    assert not unexpected, (
        "These modules in app/tools/ are shadowed by a name __init__.py "
        f"re-exports: {sorted(unexpected)}. Rename the MODULE (the "
        "precedent is unbatch.py -> batch_undo.py, 2026-09-24) rather than "
        "adding it to KNOWN_COLLISIONS — that list is only allowed to "
        "shrink. A collision means `from . import <name>` inside the "
        "package returns the function, not the module."
    )


def test_the_allowlist_still_describes_the_real_collisions():
    """
    The guard on the guard. A stale allowlist is how a sweep quietly stops
    meaning anything: if one of these is renamed and the entry is left
    behind, the next module to take that name is waved through. So the
    allowlist has to be exactly the collisions that are really there.
    """
    assert _collisions() == KNOWN_COLLISIONS, (
        "KNOWN_COLLISIONS is out of date. Really colliding: "
        f"{sorted(_collisions())}; allowlisted: {sorted(KNOWN_COLLISIONS)}. "
        "If a collision was fixed, delete its entry."
    )


def test_the_sweep_can_actually_see_a_collision():
    """
    Non-vacuity, proved rather than asserted: the sweep is run against a
    name that IS both a module and a re-exported name, and must report it.
    Without this, a sweep that had quietly stopped matching would pass for
    ever — which is the failure this repo has recorded twice.
    """
    assert "swap_options" in _module_names()
    assert "swap_options" in _reexported_names()
    assert "swap_options" in _collisions()


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


def test_neither_known_collision_is_reachable_today():
    """
    The severity claim, kept honest in code rather than only in prose: no
    module inside app/ imports either colliding name AS A MODULE. The day
    one does, it breaks, and this test is where the reason is written down.
    """
    offenders = []
    for path in sorted((TOOLS.parent).rglob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                for alias in node.names:
                    if alias.name in KNOWN_COLLISIONS:
                        offenders.append(f"{path}:{node.lineno} imports {alias.name}")
    assert not offenders, (
        "A module is importing a shadowed name as a module, which returns "
        f"the function instead: {offenders}. This is the live version of "
        "the bug — rename the module now."
    )
