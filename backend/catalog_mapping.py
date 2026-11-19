"""Which column holds an item's real name? The rule, written once.

The catalog's name cannot be guessed from a field's name. On this account
``displayname`` is the numeric item code for most facet-bearing items
(``21821``, ``22829``), while NetSuite's "Store Display Name" reads
``818 Tequila Pina Dad Hat``. ``storedisplayname`` is therefore the chosen
field — a constant, not a heuristic.

The fallback ladder is fixed, and a row that exhausts it is still a row:

    storedisplayname -> non-numeric displayname -> non-numeric itemid -> none

A name that is only a number is not a name. ``itemid`` usually carries a site
prefix here (``3148_21821``), so the numeric trap is almost always
``displayname``.

Commit 1 of the catalog sync uses this module only to report which column wins;
the rest of the item map arrives with the sync.
"""

from __future__ import annotations

import re
from typing import Any

# One constant, one place. The ladder below still catches a blank.
NAME_FIELD = "storedisplayname"

NEEDS_NAME_PREFIX = "Item "

NUMBER_ONLY = re.compile(r"^[0-9][0-9.,\-/ ]*$")

NAME_SOURCE_CHOSEN = NAME_FIELD
NAME_SOURCE_DISPLAY = "displayname"
NAME_SOURCE_ITEMID = "itemid"
NAME_SOURCE_NONE = ""


def text(value: Any) -> str:
    return str(value or "").strip()


def is_numeric_code(value: Any) -> bool:
    """A string that is only digits/punctuation — an item code, not a name."""
    cleaned = text(value)
    return bool(cleaned) and bool(NUMBER_ONLY.match(cleaned))


def resolve_name(row: dict[str, Any]) -> tuple[str, str]:
    """(name, source). Source is the field the name came from, or '' for none.

    ``NAME_FIELD`` is tried first by name, so changing that constant moves the
    first rung and nothing else.
    """
    chosen = text(row.get(NAME_FIELD))
    if chosen:
        return chosen, NAME_SOURCE_CHOSEN
    for candidate, source in (
        ("storedisplayname", NAME_SOURCE_CHOSEN),
        ("displayname", NAME_SOURCE_DISPLAY),
        ("itemid", NAME_SOURCE_ITEMID),
    ):
        value = text(row.get(candidate))
        if value and not is_numeric_code(value):
            return value, source
    return "", NAME_SOURCE_NONE


def placeholder_name(internal_id: str) -> str:
    """What an item with no usable name is called until a person names it."""
    return f"{NEEDS_NAME_PREFIX}{internal_id}".strip()
