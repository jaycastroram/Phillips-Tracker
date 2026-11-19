"""How one NetSuite item becomes one catalog row. The map lives here, once.

The chosen name column
----------------------
``storedisplayname``. Chosen from data, not from a field's name: on the 3,746
active brand-facet items, ``displayname`` is the numeric code 779 times
(``21821``) while ``storedisplayname`` is filled for every one of them and reads
``818 Tequila Pina Dad Hat``. NetSuite's "Store Display Name" is what a buyer
sees, so it is the catalog's name. ``scripts/report_catalog_names.py`` prints
the sample this was decided from.

The fallback ladder is fixed, and a row that exhausts it is still created —
named ``Item <internalid>``, flagged ``needs_name``, and kept out of every book
(``in_survey = 0``, ``in_buy_book = 0``) so an unnamed item cannot be published
by accident:

    storedisplayname -> non-numeric displayname -> non-numeric itemid -> none

A name that is only a number is not a name. ``itemid`` usually carries a site
prefix here (``3148_21821``), so the numeric trap is almost always
``displayname``.

What is deliberately absent
---------------------------
Price, ``image_url`` and ``uom`` are not mapped.

* Price is not on the item: ``item.baseprice`` is not a SuiteQL identifier in
  this account and every price read so far is 0. Writing it would replace a
  real display price with zero.
* Images come from the record import, blank-only, and this read has no media
  column at all.
* ``uom`` has never appeared in a projection of this account, and a name is not
  guessed here.

``itemid`` and the Multi-Order Category are kept in ``netsuite_synced_json``
only: they identify the source row, they are not catalog fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from netsuite import CATEGORY_COLUMN, FACET_COLUMNS, SORT_COLUMN, FacetResolution

# One constant, one place. The ladder below still catches a blank.
NAME_FIELD = "storedisplayname"

FACET_FIELDS = ("brand", "channel", "key_program", "product_type")

# Fields a run may write.
WRITE_FIELDS = (
    "item_name",
    "item_description",
    "brand",
    "channel",
    "key_program",
    "product_type",
    "sort_order",
)

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


def sort_order_value(row: dict[str, Any]) -> int:
    """Numeric Multi-Order Sort; anything unreadable is 0 (blank means unsorted)."""
    raw = text(row.get(SORT_COLUMN) or row.get("multi_order_sort") or row.get("sort_order"))
    if not raw:
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def facet_ids(row: dict[str, Any]) -> dict[str, str]:
    """{facet: list id} as the item record stores them (blank when absent)."""
    return {
        facet: text(row.get(column) or row.get(facet))
        for facet, column in FACET_COLUMNS.items()
    }


def has_any_facet(row: dict[str, Any]) -> bool:
    """The catalog boundary: an item is in it when it carries at least one facet.

    One predicate, because this is the boundary a person may want to move. It
    is the only place the rule is written down.
    """
    return any(facet_ids(row).values())


def is_inactive(row: dict[str, Any]) -> bool:
    value = row.get("isinactive")
    if isinstance(value, bool):
        return value
    return text(value).upper() in ("T", "TRUE", "1", "YES")


@dataclass
class MappedItem:
    """One item row ready for the catalog, plus the snapshot it came from."""

    netsuite_item_id: str
    name: str
    name_source: str
    needs_name: bool
    inactive: bool
    fields: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)


def map_item(row: dict[str, Any], labels: FacetResolution, synced_at: str) -> MappedItem:
    """The whole map for one item row. No I/O, no database."""
    internal_id = text(row.get("id") or row.get("internalid") or row.get("netsuite_item_id"))
    if not internal_id:
        raise ValueError("an item row without an internal id cannot be mapped")

    name, source = resolve_name(row)
    needs_name = not name
    if needs_name:
        name = placeholder_name(internal_id)

    ids = facet_ids(row)
    resolved = {facet: labels.label(facet, ids.get(facet, "")) for facet in FACET_FIELDS}
    order = sort_order_value(row)

    fields: dict[str, Any] = {
        "item_name": name,
        "brand": resolved["brand"],
        "channel": resolved["channel"],
        "key_program": resolved["key_program"],
        "product_type": resolved["product_type"],
        "sort_order": order,
    }
    # The description is written whenever NetSuite returns one — the buy book
    # prints it, so every synced row is worth having one on. A blank from
    # NetSuite leaves the stored value alone: the key is left out here and out
    # of the snapshot, so a blank can never read as an override or as an edit.
    description = text(row.get("description"))
    if description:
        fields["item_description"] = description

    snapshot = {
        "netsuite_item_id": internal_id,
        "itemid": text(row.get("itemid")),
        "category": text(row.get(CATEGORY_COLUMN) or row.get("multi_order_cat")),
        "name": name,
        "name_source": source or "none",
        "needs_name": needs_name,
        "facet_ids": ids,
        "item_name": name,
        "brand": resolved["brand"],
        "channel": resolved["channel"],
        "key_program": resolved["key_program"],
        "product_type": resolved["product_type"],
        "sort_order": order,
        "multi_order_sort_raw": text(row.get(SORT_COLUMN) or row.get("multi_order_sort")),
        "synced_at": synced_at,
    }
    if description:
        snapshot["item_description"] = description
    return MappedItem(
        netsuite_item_id=internal_id,
        name=name,
        name_source=source,
        needs_name=needs_name,
        inactive=is_inactive(row),
        fields=fields,
        snapshot=snapshot,
    )
