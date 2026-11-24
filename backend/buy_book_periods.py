"""Buy-in periods and the items that belong to one.

The Buy Book shows only the items of a chosen buy-in period, not the whole
catalog. Today that period data exists only as NetSuite exports: the
``periods`` and ``items`` datasets the export pipeline writes as CSV under
``data/exports/``. There are no period tables yet, so every read goes through
this module and swapping to tables later is a change to this file alone.

CSV shapes
----------
Header names are matched case-, space- and underscore-insensitively, so a
NetSuite saved search can be uploaded as exported.

``periods/*.csv`` — one row per buy-in period. Recognised columns:

    id / internalid / internal id / recordid      period id (falls back to name)
    name / period / buy in period / buyin period  the label a person reads
    startdate / start date                        optional
    enddate / end date                            optional
    status / state                                optional
    company / companyname / subsidiary            optional; scoping

``items/*.csv`` — one row per item in a period. Recognised columns:

    id / internalid / internal id / itemid / item / sku
    period / buy in period / buyin period / periodid / periodname /
    buy in period id / buyin period id
    price / buy in item price              optional; the price the book prints
    description / buy in item description  optional; its HTML tags are stripped

The item column is whatever that export calls the item: a saved search's "Item
ID" is the SKU (``3184_58212``), while some exports carry the internal id
(``210``). Which one it is cannot be told from the value, so membership
matches a catalog row whose ``netsuite_item_id`` *or*
``netsuite_item_number`` equals it — one comparison per identifier, so a row
that is named by both is still one member. A cell naming two ids (``A : A-5``,
how NetSuite writes a kit) contributes both, deduplicated.

The catalog still supplies the name and the picture. A period that states a
price or description overrides the catalog's, because a buy-in list is the
wording the owner wants printed; absent columns leave the catalog's own alone.

A period whose id column is missing is identified by its name, and an item row
that names its period by name resolves to the same id.

Scope
-----
Only one company is built today (Phillips). When the period rows carry a
company column, rows that name another company are dropped; rows with no
company column are Phillips's.
"""

from __future__ import annotations

import csv
import html
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPANY_SLUG = "phillips"
DEFAULT_COMPANY_NAME = "Phillips"

PERIOD_ID_COLUMNS = ("id", "internalid", "internal id", "recordid", "record id")
PERIOD_NAME_COLUMNS = ("name", "period", "buy in period", "buyin period", "period name")
PERIOD_START_COLUMNS = ("startdate", "start date", "period start", "start")
PERIOD_END_COLUMNS = ("enddate", "end date", "period end", "end")
PERIOD_STATUS_COLUMNS = ("status", "state")
COMPANY_COLUMNS = ("company", "companyname", "company name", "subsidiary")

ITEM_ID_COLUMNS = ("netsuiteitemid", "netsuite item id", "itemid", "item id", "internalid",
                   "internal id", "id", "item", "sku")
# The wording the book prints, when the period itself carries it: a buy-in item
# list states its own price and description, and that is what the owner wants
# shown. Both are optional — a dataset without them leaves the catalog's own.
ITEM_PRICE_COLUMNS = ("buyinitemprice", "buy in item price", "item price", "price")
ITEM_DESCRIPTION_COLUMNS = ("buyinitemdescription", "buy in item description", "item description",
                            "description")
ITEM_PERIOD_COLUMNS = ("period", "buy in period", "buyin period", "periodid", "period id",
                       "buy in period id", "buyin period id", "periodname", "period name")


# A description is HTML in NetSuite ("<p>Brand: Item</p><ul><li>Size: ...") and
# plain text in the book. Block ends become spaces before tags are dropped, so
# the words do not run together.
_BLOCK_END = re.compile(r"<\s*(?:br|/p|/li|/div|/tr|/ul|/ol)\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def plain_text(value: Any) -> str:
    """A NetSuite description as one readable line: tags dropped, entities read."""
    raw = str(value or "")
    if "<" in raw or "&" in raw:
        raw = html.unescape(_TAG.sub(" ", _BLOCK_END.sub(" ", raw)))
    return " ".join(raw.split())


def split_item_ids(value: Any) -> list[str]:
    """Every id one cell names.

    A NetSuite buy-in writes a kit as ``3206_FX-72231 : 3206_FX-72231-5`` — two
    items in one cell. Both halves are real ids; the combined string is a
    display label and matches no catalog row.
    """
    return [part.strip() for part in str(value or "").split(":") if part.strip()]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").lower().split())


def _col_key(value: Any) -> str:
    """A header reduced to letters and digits, so "Buy-In Period" matches too.

    NetSuite writes its column titles with hyphens and slashes; the export
    datasets list the same columns with spaces. Folding both to alphanumerics
    means a saved search can be uploaded exactly as it downloads.
    """
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


class _Row(dict):
    """A CSV row with case/space/underscore-insensitive lookup."""

    def __init__(self, raw: dict[str, Any]):
        super().__init__()
        self._folded = {_col_key(key): value for key, value in raw.items()}

    def pick(self, columns_at: tuple[str, ...]) -> str:
        for column in columns_at:
            for key in self._folded:
                if key == _col_key(column):
                    value = str(self._folded[key] or "").strip()
                    if value:
                        return value
        return ""


def exports_root() -> Path:
    """The export folder, honouring SMARTBUY_EXPORTS_DIR like the pipeline does."""
    configured = str(os.environ.get("SMARTBUY_EXPORTS_DIR") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else REPO_ROOT / path
    return REPO_ROOT / "data" / "exports"


def latest_csv(kind: str) -> Path | None:
    """The newest file in a dataset folder — the same rule the export reader uses."""
    folder = exports_root() / kind
    if not folder.is_dir():
        return None
    files = sorted((path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".csv"),
                   key=lambda path: path.name)
    return files[-1] if files else None


def read_rows(kind: str) -> list[_Row]:
    path = latest_csv(kind)
    if path is None:
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [_Row(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def _company_matches(row: _Row, company_name: str, company_slug: str) -> bool:
    """Keep a period row when it names this company, or names none at all."""
    named = row.pick(COMPANY_COLUMNS)
    if not named:
        return True
    folded = _norm(named)
    return folded in {_norm(company_name), _norm(company_slug)} or _norm(company_slug) in folded


def period_rows(company_name: str = DEFAULT_COMPANY_NAME,
                company_slug: str = DEFAULT_COMPANY_SLUG) -> list[_Row]:
    return [row for row in read_rows("periods") if _company_matches(row, company_name, company_slug)]


def catalog_item_ids(conn: Any, company_id: int) -> set[str]:
    """Every identifier an active catalog row answers to, for counting members.

    A period's count is read against this: an export row naming an item the
    catalog does not hold is not a member — the book cannot print it — so it
    must not inflate the number the drop-down shows.
    """
    from database import execute  # local import keeps the module importable alone

    ids: set[str] = set()
    for row in execute(
        conn,
        "SELECT netsuite_item_id, netsuite_item_number FROM survey_items"
        " WHERE company_id = ? AND is_active = ?",
        (company_id, True),
    ).fetchall():
        for value in (row["netsuite_item_id"], row["netsuite_item_number"]):
            folded = _norm(value)
            if folded:
                ids.add(folded)
    return ids


def list_periods(company_name: str = DEFAULT_COMPANY_NAME,
                 company_slug: str = DEFAULT_COMPANY_SLUG,
                 catalog_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """The company's buy-in periods, newest first.

    Newest is by end date when the export carries one, else by start date, else
    by the order the rows arrive in (last row first), so a period list with no
    dates is still stable and the most recent one still comes first.

    ``catalog_ids`` is what ``catalog_item_ids`` returns: with it,
    ``item_count`` is the number of members the book can print, so the
    drop-down never promises an item the catalog has not got.
    """
    rows = period_rows(company_name, company_slug)
    periods: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        name = row.pick(PERIOD_NAME_COLUMNS)
        period_id = row.pick(PERIOD_ID_COLUMNS) or name
        if not period_id and not name:
            continue
        periods.append({
            "id": period_id or name,
            "name": name or period_id,
            "starts_on": row.pick(PERIOD_START_COLUMNS),
            "ends_on": row.pick(PERIOD_END_COLUMNS),
            "status": row.pick(PERIOD_STATUS_COLUMNS),
            "source_index": index,
        })
    item_counts = _item_counts_by_period(periods, company_name, company_slug, catalog_ids)
    for period in periods:
        period["item_count"] = item_counts.get(period["id"], 0)
    periods.sort(
        key=lambda period: (
            period["ends_on"] or period["starts_on"] or "",
            -period["source_index"],
        ),
        reverse=True,
    )
    return periods


def _period_key(value: str) -> str:
    return _norm(value)


def _item_counts_by_period(periods: list[dict[str, Any]],
                           company_name: str,
                           company_slug: str,
                           catalog_ids: set[str] | None = None) -> dict[str, int]:
    # Unique items, not rows: a buy-in export repeats a row per order line
    # (the Phillips file lists 63 items across 294 rows), and the drop-down must
    # say 63 because that is what the book prints.
    members: dict[str, set[str]] = {}
    for row in read_rows("items"):
        if not _company_matches(row, company_name, company_slug):
            continue
        key = _period_key(row.pick(ITEM_PERIOD_COLUMNS))
        if key:
            members.setdefault(key, set()).update(_norm(item_id) for item_id in _row_item_ids(row))
    if catalog_ids is not None:
        members = {key: ids & catalog_ids for key, ids in members.items()}
    counts: dict[str, int] = {key: len(ids) for key, ids in members.items()}
    resolved: dict[str, int] = {}
    for period in periods:
        resolved[period["id"]] = counts.get(_period_key(period["id"]), 0) or counts.get(
            _period_key(period["name"]), 0
        )
    return resolved


def resolve_period_id(period_id: str | None,
                      company_name: str = DEFAULT_COMPANY_NAME,
                      company_slug: str = DEFAULT_COMPANY_SLUG,
                      catalog_ids: set[str] | None = None) -> str | None:
    """The period a link asked for, or the newest one when it asked for none."""
    periods = list_periods(company_name, company_slug, catalog_ids)
    if not periods:
        return None
    requested = str(period_id or "").strip()
    if not requested:
        return periods[0]["id"]
    for period in periods:
        if _norm(period["id"]) == _norm(requested) or _norm(period["name"]) == _norm(requested):
            return period["id"]
    return None


def period_by_id(period_id: str | None,
                 company_name: str = DEFAULT_COMPANY_NAME,
                 company_slug: str = DEFAULT_COMPANY_SLUG,
                 catalog_ids: set[str] | None = None) -> dict[str, Any] | None:
    """The company's period with this id or name, or None.

    A period id from another company's export is None here, which the routes
    turn into a 404 rather than a silent fallback to the newest period.
    """
    requested = _norm(period_id or "")
    if not requested:
        return None
    for period in list_periods(company_name, company_slug, catalog_ids):
        if _norm(period["id"]) == requested or _norm(period["name"]) == requested:
            return period
    return None


def default_period_id(periods: list[dict[str, Any]] | None = None,
                      company_name: str = DEFAULT_COMPANY_NAME,
                      company_slug: str = DEFAULT_COMPANY_SLUG) -> str | None:
    """The period a link opens on: the newest one that has items.

    A period with no members is still selectable by id (it renders an empty
    book that says so), but it never becomes the page someone lands on while a
    period with items exists.
    """
    if periods is None:
        periods = list_periods(company_name, company_slug)
    for period in periods:
        if period.get("item_count"):
            return period["id"]
    return periods[0]["id"] if periods else None


def _row_item_ids(row: _Row) -> list[str]:
    """Every item id one export row names, in cell order."""
    return split_item_ids(row.pick(ITEM_ID_COLUMNS))


def _period_keys(period_id: str,
                 company_name: str = DEFAULT_COMPANY_NAME,
                 company_slug: str = DEFAULT_COMPANY_SLUG) -> set[str]:
    """The folded keys a period is named by: its id and its label."""
    wanted = {_period_key(period_id)}
    for period in list_periods(company_name, company_slug):
        if _norm(period["id"]) == _norm(period_id):
            wanted.add(_period_key(period["name"]))
    return wanted


def period_item_details(period_id: str,
                        company_name: str = DEFAULT_COMPANY_NAME,
                        company_slug: str = DEFAULT_COMPANY_SLUG) -> dict[str, dict[str, str]]:
    """The price and description the period itself states, by item id.

    A buy-in item list carries the wording the owner wants printed, which the
    catalog's own description may not match. Only non-empty values are kept, so
    an export without them changes nothing; the first row that states one wins.
    """
    wanted = _period_keys(period_id, company_name, company_slug)
    details: dict[str, dict[str, str]] = {}
    for row in read_rows("items"):
        if not _company_matches(row, company_name, company_slug):
            continue
        if _period_key(row.pick(ITEM_PERIOD_COLUMNS)) not in wanted:
            continue
        price = row.pick(ITEM_PRICE_COLUMNS)
        description = plain_text(row.pick(ITEM_DESCRIPTION_COLUMNS))
        if not price and not description:
            continue
        for item_id in _row_item_ids(row):
            entry = details.setdefault(item_id, {"price": "", "item_description": ""})
            if price and not entry["price"]:
                entry["price"] = price
            if description and not entry["item_description"]:
                entry["item_description"] = description
    return details


def period_item_ids(period_id: str,
                    company_name: str = DEFAULT_COMPANY_NAME,
                    company_slug: str = DEFAULT_COMPANY_SLUG) -> list[str]:
    """Every item id the export places in this period, in file order.

    A compound cell contributes each id it names, deduplicated here: the
    Phillips file's 294 rows over 63 items give 63 ids, and a row written
    ``A : A-5`` gives ``A`` and ``A-5``.
    """
    wanted = _period_keys(period_id, company_name, company_slug)
    ids: list[str] = []
    for row in read_rows("items"):
        if not _company_matches(row, company_name, company_slug):
            continue
        if _period_key(row.pick(ITEM_PERIOD_COLUMNS)) not in wanted:
            continue
        for item_id in _row_item_ids(row):
            if item_id not in ids:
                ids.append(item_id)
    return ids


def has_period_data(company_name: str = DEFAULT_COMPANY_NAME,
                    company_slug: str = DEFAULT_COMPANY_SLUG) -> bool:
    return bool(period_rows(company_name, company_slug))


def period_item_rows(conn: Any, company_id: int, period_id: str, *,
                     company_name: str = DEFAULT_COMPANY_NAME,
                     company_slug: str = DEFAULT_COMPANY_SLUG) -> list[Any]:
    """The catalog rows whose NetSuite id *or* number the export names.

    Membership comes from the period export, one row per member. A row is a
    member when the export's identifier equals its ``netsuite_item_id`` (what a
    saved search calls ``Internal ID``) or its ``netsuite_item_number`` (what it
    calls ``Item ID``); the export cannot say which it carries, and either one
    identifies the same item.

    The catalog supplies the item's name and picture. Where the period states
    its own price and description, those replace the catalog's: a buy-in list
    is the wording the owner wants printed, and the catalog's copy may be a
    placeholder. Returned as plain dicts for that reason — a ``sqlite3.Row``
    would not carry the override.

    One SELECT per chunk keeps the parameter count under SQLite's 999 limit —
    the chunk is halved for the second ``IN`` list.
    """
    from database import execute  # local import keeps the module importable alone

    ids = period_item_ids(period_id, company_name, company_slug)
    rows: list[Any] = []
    chunk_size = 450
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            execute(
                conn,
                f"""
                SELECT * FROM survey_items
                WHERE company_id = ? AND is_active = ?
                  AND (netsuite_item_id IN ({placeholders})
                       OR netsuite_item_number IN ({placeholders}))
                """,
                (company_id, True, *chunk, *chunk),
            ).fetchall()
        )
    details = period_item_details(period_id, company_name, company_slug)
    members: list[dict[str, Any]] = []
    for row in rows:
        member = dict(row)
        stated = (details.get(str(row["netsuite_item_number"] or ""))
                  or details.get(str(row["netsuite_item_id"] or ""))
                  or {})
        if stated.get("price"):
            member["price"] = stated["price"]
        if stated.get("item_description"):
            member["item_description"] = stated["item_description"]
        members.append(member)
    members.sort(key=lambda row: (row["buy_book_sort_order"] or 0, row["sort_order"] or 0, row["id"]))
    return members
