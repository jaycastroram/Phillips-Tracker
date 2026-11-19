"""Reconcile one company's catalog against the NetSuite item master.

A catalog row is a *catalog entry*: a person decides what a survey asks and what
a book prints. NetSuite is where the item's identity, name, facets and order
come from. This module reconciles the two, in one direction only: NetSuite
never learns anything from here, and a run never creates, changes or deletes a
record there.

Three rules the whole file is built around:

* **The catalog is the universe, a buy-in is a selection from it.** This is not
  the per-customer Buy In pull. It reads the item master once, keyset-paged,
  and never touches a period or a buy-in record.
* **A person's edit wins, per field.** ``survey_items.netsuite_synced_json``
  holds what NetSuite said last run. A field still equal to that follows
  NetSuite; a field that differs was typed by someone and is left alone (and
  counted as overridden). "Reset to NetSuite" is the way back.
* **Retirement is one-way.** An item that went inactive, or whose facets went
  away, is archived once. No run ever reactivates a row — not even one a person
  restored while NetSuite still calls it inactive. The old boot seed's
  resurrect-everything behaviour is the failure this rule exists for.

Everything a run does happens in one transaction: a failure mid-scan leaves the
catalog exactly as it was, and a dry run rolls back on purpose so it can be
trusted as a preview.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from catalog_mapping import (
    FACET_FIELDS,
    MappedItem,
    WRITE_FIELDS,
    facet_ids,
    has_any_facet,
    map_item,
    text,
)
from config import IS_POSTGRES
from database import connect, execute, utc_now, with_returning
from netsuite import (
    CATEGORY_COLUMN,
    FACET_COLUMNS,
    SORT_COLUMN,
    FacetResolution,
    SourceError,
    SuiteQLClient,
    client_from_env,
    facet_value_ids,
    resolve_facets,
)

PAGE_SIZE = 1000
AUDIT_SHEET = "catalog"
AUDIT_RUN = "catalog_sync"
AUDIT_RETIRED = "catalog_item_archived"
AUDIT_RESET = "catalog_item_reset"
RETIRE_REASON = "netsuite_inactive"
SYSTEM_USER_EMAIL = "netsuite-sync"

@dataclass
class CatalogSyncReport:
    """What a run saw and did. The numbers the CLI prints and the route returns."""

    company: str = ""
    company_id: int = 0
    dry_run: bool = True
    scanned: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    overridden: int = 0
    retired: int = 0
    needs_name: int = 0
    label_misses: int = 0
    excluded: int = 0
    duration: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"{self.scanned} scanned, {self.created} created, {self.updated} updated,"
            f" {self.unchanged} unchanged, {self.overridden} overridden,"
            f" {self.retired} retired, {self.needs_name} need a name,"
            f" {self.label_misses} unlabelled facet values, {self.excluded} excluded,"
            f" {self.duration:.1f}s"
        )


# ---------------------------------------------------------------------------
# Reading the item master
# ---------------------------------------------------------------------------

def _select_columns(*, with_store_name: bool = True) -> str:
    columns = [
        "i.id AS id",
        "i.itemid AS itemid",
        "i.displayname AS displayname",
        "i.description AS description",
        "i.isinactive AS isinactive",
    ]
    if with_store_name:
        columns.insert(3, "i.storedisplayname AS storedisplayname")
    columns.extend(f"i.{column} AS {column}" for column in FACET_COLUMNS.values())
    columns.append(f"i.{SORT_COLUMN} AS {SORT_COLUMN}")
    columns.append(f"i.{CATEGORY_COLUMN} AS {CATEGORY_COLUMN}")
    return ", ".join(columns)


def _facets_present() -> str:
    return " OR ".join(f"i.{column} IS NOT NULL" for column in FACET_COLUMNS.values())


def read_item_rows(
    client: SuiteQLClient,
    *,
    page_size: int = PAGE_SIZE,
    limit: int | None = None,
    log: Any = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Every item worth looking at, one keyset page at a time.

    The scan covers every **active** item (that is how the excluded count is
    honest) plus any **inactive** item still carrying facets (that is how a
    deactivation gets seen). Never an ``IN`` list of ids: 500 of those already
    answered with a 500 from this account. Never ``OFFSET``: SuiteQL ignores it,
    and a page-2 request returns page 1 forever.

    Returns (rows, complete). ``complete`` is False when ``limit`` stopped the
    scan early, which is the one case where "the item did not appear" must not
    be read as "the item is gone".
    """
    with_store_name = True
    rows: list[dict[str, Any]] = []
    last = 0
    while True:
        statement = (
            f"SELECT {_select_columns(with_store_name=with_store_name)} FROM item i"
            f" WHERE i.id > {int(last)} AND (i.isinactive = 'F' OR {_facets_present()})"
            f" ORDER BY i.id FETCH FIRST {int(page_size)} ROWS ONLY"
        )
        try:
            page, _ = client.query(statement, limit=page_size)
        except SourceError as error:
            # A different NetSuite account (or an older one) may not expose the
            # store display name. Drop that one column and keep the ladder.
            if with_store_name and "storedisplayname" in str(error).lower():
                if log:
                    log("storedisplayname is not a field in this account; using the ladder")
                with_store_name = False
                continue
            raise
        if not page:
            break
        rows.extend(page)
        if log and len(rows) % 10000 < page_size:
            log(f"{len(rows)} items read")
        last = int(str(page[-1].get("id")))
        if limit is not None and len(rows) >= limit:
            rows = rows[:limit]
            return rows, False
        if len(page) < page_size:
            break
    return rows, True


def _scan(
    client: SuiteQLClient, limit: int | None, log: Any = None, page_size: int = PAGE_SIZE
) -> tuple[list[dict[str, Any]], set[str], int, int, bool]:
    """(catalog candidates, inactive ids, excluded count, rows read, complete).

    A candidate is an active item with at least one facet. An inactive item
    with facets is not written but is remembered, because a catalog row for it
    must be retired. An active item with no facet is excluded and counted.
    """
    rows, complete = read_item_rows(client, limit=limit, page_size=page_size, log=log)
    candidates: list[dict[str, Any]] = []
    inactive_ids: set[str] = set()
    excluded = 0
    for row in rows:
        internal_id = text(row.get("id"))
        faceted = has_any_facet(row)
        inactive = text(row.get("isinactive")).upper() in ("T", "TRUE", "1")
        if inactive:
            if faceted:
                inactive_ids.add(internal_id)
            continue
        if not faceted:
            excluded += 1
            continue
        candidates.append(row)
    return candidates, inactive_ids, excluded, len(rows), complete


# ---------------------------------------------------------------------------
# The per-field rule
# ---------------------------------------------------------------------------

def same_value(current: Any, incoming: Any) -> bool:
    """Stored and incoming compare as text; 0 and '' and False are the same nothing."""
    if isinstance(current, bool) or isinstance(incoming, bool):
        return bool(current) == bool(incoming)
    if isinstance(current, (int, float)) and isinstance(incoming, (int, float)):
        return float(current) == float(incoming)
    return text(current) == text(incoming)


def decide(field_name: str, incoming: Any, current: Any, snapshot: dict[str, Any] | None) -> str:
    """'write', 'unchanged' or 'overridden' for one field.

    No snapshot means the first run for this row: everything follows NetSuite.
    A stored value equal to the snapshot still follows NetSuite. Anything else
    was typed by a person and is left alone.
    """
    if snapshot is None or field_name not in snapshot:
        return "unchanged" if same_value(current, incoming) else "write"
    if same_value(current, snapshot[field_name]):
        return "unchanged" if same_value(current, incoming) else "write"
    return "unchanged" if same_value(current, incoming) else "overridden"


def parse_snapshot(raw: Any) -> dict[str, Any] | None:
    """The last-synced values, or None when this row has never been synced."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw if isinstance(raw, str) else str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _audit(
    conn: Any,
    company_id: int,
    action: str,
    *,
    item_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    user_email: str = SYSTEM_USER_EMAIL,
) -> None:
    """An audit row without a user: a run may be a cron job, not a person."""
    execute(
        conn,
        "INSERT INTO audit_logs (company_id, user_id, user_email, action, sheet, item_id,"
        " before_json, after_json) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)",
        (
            company_id,
            user_email,
            action,
            AUDIT_SHEET,
            item_id,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
        ),
    )


def _row_active(row: Any) -> bool:
    value = row["is_active"]
    return bool(value)


def _active_value(active: bool) -> Any:
    """``is_active`` is BOOLEAN on Postgres and INTEGER on SQLite."""
    return active if IS_POSTGRES else (1 if active else 0)


def _existing_rows(conn: Any, company_id: int) -> tuple[dict[str, Any], list[Any]]:
    rows = execute(conn, "SELECT * FROM survey_items WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()
    by_netsuite_id: dict[str, Any] = {}
    for row in rows:
        key = text(row["netsuite_item_id"])
        if key and key not in by_netsuite_id:
            by_netsuite_id[key] = row
    return by_netsuite_id, rows


def _create_row(conn: Any, company_id: int, item: MappedItem, now: Any) -> int:
    fields = item.fields
    sql = with_returning(
        """
        INSERT INTO survey_items (
            company_id, item_name, brand, channel, product_type, key_program,
            item_description, sort_order, netsuite_item_id, netsuite_synced_json,
            needs_name, in_survey, in_buy_book, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
        """
    )
    cursor = execute(
        conn,
        sql,
        (
            company_id,
            item.name,
            fields.get("brand", ""),
            fields.get("channel", ""),
            fields.get("product_type", ""),
            fields.get("key_program", ""),
            fields.get("item_description", ""),
            int(fields.get("sort_order", 0)),
            item.netsuite_item_id,
            json.dumps(item.snapshot, ensure_ascii=False, default=str),
            1 if item.needs_name else 0,
            _active_value(True),
            now,
            now,
        ),
    )
    if IS_POSTGRES:
        row = cursor.fetchone()
        return int(row["id"])
    return int(cursor.lastrowid)


def _apply_existing(
    conn: Any, row: Any, item: MappedItem, now: Any
) -> tuple[bool, int, bool]:
    """(wrote, overridden_fields, needs_name_after) for one existing row."""
    snapshot = parse_snapshot(row["netsuite_synced_json"])
    changes: dict[str, Any] = {}
    overridden = 0
    for name in WRITE_FIELDS:
        if name not in item.fields:
            continue
        decision = decide(name, item.fields[name], row[name], snapshot)
        if decision == "write":
            changes[name] = item.fields[name]
        elif decision == "overridden":
            overridden += 1

    # The flag follows the name that the row actually ends the run with: a
    # person who named the row themselves has a named row, whatever NetSuite
    # says, and a row still holding the ``Item <id>`` placeholder is flagged.
    if item.needs_name:
        final_name = changes.get("item_name", row["item_name"])
        needs_name = 1 if same_value(final_name, item.name) else 0
    else:
        needs_name = 0
    current_flag = int(row["needs_name"] or 0) if _has_column(row, "needs_name") else 0
    if needs_name != current_flag:
        changes["needs_name"] = needs_name

    wrote = bool(changes)
    if wrote:
        assignments = ", ".join(f"{name} = ?" for name in changes)
        execute(
            conn,
            f"UPDATE survey_items SET {assignments}, netsuite_synced_json = ?, updated_at = ? WHERE id = ?",
            (*changes.values(), json.dumps(item.snapshot, ensure_ascii=False, default=str), now, row["id"]),
        )
    else:
        # The snapshot moves even when nothing was written: it is what NetSuite
        # said last run, which is what the next run's override test compares to.
        execute(
            conn,
            "UPDATE survey_items SET netsuite_synced_json = ? WHERE id = ?",
            (json.dumps(item.snapshot, ensure_ascii=False, default=str), row["id"]),
        )
    return wrote, overridden, needs_name


def _has_column(row: Any, name: str) -> bool:
    try:
        return name in row.keys()
    except AttributeError:
        return name in row


def sync_catalog(
    company: Any,
    *,
    client: SuiteQLClient | None = None,
    apply: bool = False,
    limit: int | None = None,
    page_size: int = PAGE_SIZE,
    user_email: str = SYSTEM_USER_EMAIL,
    log: Any = None,
) -> CatalogSyncReport:
    """Read the item master once and reconcile the company's catalog.

    ``apply=False`` is a preview that runs the whole reconciliation inside a
    transaction and rolls it back, so the preview cannot drift from the real
    thing. ``limit`` caps how many item rows are read; it also disables
    retirement-on-vanishing, because a partial scan cannot prove an item is
    gone.
    """
    started = time.monotonic()
    company = dict(company)
    company_id = int(company["id"])
    report = CatalogSyncReport(
        company=str(company.get("slug") or company_id),
        company_id=company_id,
        dry_run=not apply,
    )
    source = client or client_from_env()
    now = utc_now()

    conn = connect()
    try:
        candidates, inactive_ids, excluded, rows_read, complete = _scan(
            source, limit, log=log, page_size=page_size
        )
        report.scanned = rows_read
        report.excluded = excluded
        if not candidates:
            raise SourceError(
                "NetSuite returned no facet-bearing active items; refusing to treat an empty"
                " read as an empty catalog"
            )

        resolution = resolve_facets(conn, source, company_id, facet_value_ids(candidates))
        report.label_misses = _label_misses(candidates, resolution)

        existing, _all_rows = _existing_rows(conn, company_id)
        synced_at = now.isoformat()
        for row in candidates:
            item = map_item(row, resolution, synced_at)
            current = existing.get(item.netsuite_item_id)
            if current is None:
                _create_row(conn, company_id, item, now)
                report.created += 1
                report.needs_name += 1 if item.needs_name else 0
                continue
            wrote, overridden, needs_name = _apply_existing(conn, current, item, now)
            report.overridden += overridden
            report.needs_name += needs_name
            if wrote:
                report.updated += 1
            else:
                report.unchanged += 1

        if not complete:
            report.notes.append("partial scan (--limit): item rows were not all read")
        report.duration = round(time.monotonic() - started, 2)
        _audit(conn, company_id, AUDIT_RUN, after=report.as_dict(), user_email=user_email)
        if apply:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return report


def _label_misses(candidates: Iterable[dict[str, Any]], resolution: FacetResolution) -> int:
    """Facet cells that carried a list id but resolved to no label.

    Counted, never printed as a number: a facet field blank in the catalog is
    honest, an id in a brand column is not.
    """
    misses = 0
    for row in candidates:
        ids = facet_ids(row)
        for facet in FACET_FIELDS:
            if ids.get(facet) and not resolution.label(facet, ids.get(facet)):
                misses += 1
    return misses


def reset_to_netsuite(conn: Any, row: Any, *, user_email: str = SYSTEM_USER_EMAIL) -> dict[str, Any]:
    """Write the last-synced values back onto one row. The way out of an override."""
    snapshot = parse_snapshot(row["netsuite_synced_json"])
    if snapshot is None:
        raise SourceError("this item has no NetSuite snapshot to restore")
    restored: dict[str, Any] = {}
    for name in WRITE_FIELDS:
        if name in snapshot:
            restored[name] = snapshot[name]
    if not restored:
        raise SourceError("the NetSuite snapshot holds no catalog fields")
    assignments = ", ".join(f"{name} = ?" for name in restored)
    now = utc_now()
    execute(
        conn,
        f"UPDATE survey_items SET {assignments}, updated_at = ? WHERE id = ?",
        (*restored.values(), now, row["id"]),
    )
    _audit(
        conn,
        int(row["company_id"]),
        AUDIT_RESET,
        item_id=int(row["id"]),
        before={name: row[name] for name in restored},
        after=restored,
        user_email=user_email,
    )
    return restored


def last_run(conn: Any, company_id: int) -> dict[str, Any] | None:
    """The newest sync audit row for this company, as the report it recorded."""
    row = execute(
        conn,
        "SELECT * FROM audit_logs WHERE company_id = ? AND action = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (company_id, AUDIT_RUN),
    ).fetchone()
    if row is None:
        return None
    try:
        report = json.loads(row["after_json"] or "{}")
    except (TypeError, ValueError):
        report = {}
    return {
        "created_at": row["created_at"],
        "user_email": row["user_email"],
        "report": report,
    }
