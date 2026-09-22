"""
What a forced tool call actually hands back, made safe to read.

Every forced tool call promises an array of objects under one key; every
reader downstream treats each element as a dict. On 2026-09-21 the model
spent an evening returning those arrays JSON-encoded inside a string —
`days` on the week draft (a crash), `options` on Swap and on the plate's
sides (silently empty: "Nothing I'd put there instead" on every tap) — so
this is the one place the shape is checked, for every reader.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("home_manager")


def coerce_result_list(raw, result_key: str, label: str) -> list:
    """
    A list of dicts, whatever came back: a JSON string is parsed, an object
    of lists is flattened (a model keying the week by date, or wrapping the
    array in its own key again), a lone object becomes a one-item list, and
    anything that still isn't a dict is dropped — each with a warning that
    names the shape, so the log says what came back rather than where it
    fell over.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning("%s: %s came back as a string that isn't JSON (%d chars): %r",
                           label, result_key, len(raw), raw[:200])
            return []
        logger.warning("%s: %s came back JSON-encoded as a string; parsed it", label, result_key)
        return coerce_result_list(parsed, result_key, label)
    if isinstance(raw, dict):
        if raw and all(isinstance(v, list) for v in raw.values()):
            logger.warning("%s: %s came back as an object of %d lists (keys %r); flattened it",
                           label, result_key, len(raw), list(raw)[:4])
            return coerce_result_list([x for v in raw.values() for x in v], result_key, label)
        if len(raw) == 1 and isinstance(next(iter(raw.values())), (str, dict)):
            # The array wrapped once more in its own key ({"options": "..."}).
            inner = next(iter(raw.values()))
            logger.warning("%s: %s came back wrapped in key %r; unwrapped it", label, result_key, next(iter(raw)))
            return coerce_result_list(inner, result_key, label)
        logger.warning("%s: %s came back as one object, not a list; wrapped it", label, result_key)
        return [raw]
    if not isinstance(raw, list):
        if raw is not None:
            logger.warning("%s: %s came back as %s, not a list; treating as empty",
                           label, result_key, type(raw).__name__)
        return []
    kept = [x for x in raw if isinstance(x, dict)]
    if len(kept) != len(raw):
        odd = [x for x in raw if not isinstance(x, dict)]
        logger.warning("%s: dropped %d non-object element(s) from %s (first: %s %r)",
                       label, len(odd), result_key, type(odd[0]).__name__, str(odd[0])[:200])
    return kept


def tool_list(block_input, result_key: str, label: str) -> list:
    """The `result_key` array of a tool_use block's input, coerced."""
    return coerce_result_list((block_input or {}).get(result_key), result_key, label)
