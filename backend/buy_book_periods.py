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
import os
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
ITEM_PERIOD_COLUMNS = ("period", "buy in period", "buyin period", "periodid", "period id",
                       "buy in period id", "buyin period id", "periodname", "period name")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").lower().split())


class _Row(dict):
    """A CSV row with case/space/underscore-insensitive lookup."""

    def __init__(self, raw: dict[str, Any]):
        super().__init__()
        self._folded = {_norm(key): value for key, value in raw.items()}

    def pick(self, columns_at: tuple[str, ...]) -> str:
        for column in columns_at:
            for key in self._folded:
                if key.replace(" ", "") == _norm(column).replace(" ", ""):
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


def list_periods(company_name: str = DEFAULT_COMPANY_NAME,
                 company_slug: str = DEFAULT_COMPANY_SLUG) -> list[dict[str, Any]]:
    """The company's buy-in periods, newest first.

    Newest is by end date when the export carries one, else by start date, else
    by the order the rows arrive in (last row first), so a period list with no
    dates is still stable and the most recent one still comes first.
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
    item_counts = _item_counts_by_period(periods, company_name, company_slug)
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
                           company_slug: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in read_rows("items"):
        if not _company_matches(row, company_name, company_slug):
            continue
        key = _period_key(row.pick(ITEM_PERIOD_COLUMNS))
        if key:
            counts[key] = counts.get(key, 0) + 1
    resolved: dict[str, int] = {}
    for period in periods:
        resolved[period["id"]] = counts.get(_period_key(period["id"]), 0) or counts.get(
            _period_key(period["name"]), 0
        )
    return resolved


def resolve_period_id(period_id: str | None,
                      company_name: str = DEFAULT_COMPANY_NAME,
                      company_slug: str = DEFAULT_COMPANY_SLUG) -> str | None:
    """The period a link asked for, or the newest one when it asked for none."""
    periods = list_periods(company_name, company_slug)
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
                 company_slug: str = DEFAULT_COMPANY_SLUG) -> dict[str, Any] | None:
    """The company's period with this id or name, or None.

    A period id from another company's export is None here, which the routes
    turn into a 404 rather than a silent fallback to the newest period.
    """
    requested = _norm(period_id or "")
    if not requested:
        return None
    for period in list_periods(company_name, company_slug):
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


def period_item_ids(period_id: str,
                    company_name: str = DEFAULT_COMPANY_NAME,
                    company_slug: str = DEFAULT_COMPANY_SLUG) -> list[str]:
    """Every item id the export places in this period, in file order."""
    wanted = {_period_key(period_id)}
    for period in list_periods(company_name, company_slug):
        if _norm(period["id"]) == _norm(period_id):
            wanted.add(_period_key(period["name"]))
    ids: list[str] = []
    for row in read_rows("items"):
        if not _company_matches(row, company_name, company_slug):
            continue
        if _period_key(row.pick(ITEM_PERIOD_COLUMNS)) not in wanted:
            continue
        item_id = row.pick(ITEM_ID_COLUMNS)
        if item_id and item_id not in ids:
            ids.append(item_id)
    return ids


def has_period_data(company_name: str = DEFAULT_COMPANY_NAME,
                    company_slug: str = DEFAULT_COMPANY_SLUG) -> bool:
    return bool(period_rows(company_name, company_slug))


def period_item_rows(conn: Any, company_id: int, period_id: str, *,
                     company_name: str = DEFAULT_COMPANY_NAME,
                     company_slug: str = DEFAULT_COMPANY_SLUG) -> list[Any]:
    """The catalog rows whose NetSuite id the export places in this period.

    Reads ``survey_items`` only; the item's fields are the catalog's own, so a
    period is a membership list, not a second copy of the item. One SELECT per
    900 ids keeps the parameter count under SQLite's default limit.
    """
    from database import execute  # local import keeps the module importable alone

    ids = period_item_ids(period_id, company_name, company_slug)
    rows: list[Any] = []
    for start in range(0, len(ids), 900):
        chunk = ids[start:start + 900]
        placeholders = ", ".join("?" for _ in chunk)
        rows.extend(
            execute(
                conn,
                f"""
                SELECT * FROM survey_items
                WHERE company_id = ? AND is_active = ?
                  AND netsuite_item_id IN ({placeholders})
                """,
                (company_id, True, *chunk),
            ).fetchall()
        )
    rows.sort(key=lambda row: (row["buy_book_sort_order"] or 0, row["sort_order"] or 0, row["id"]))
    return rows
