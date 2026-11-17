"""Fill imported NetSuite ids and image URLs into tracker and catalog rows.

Run ``scripts/import_item_image_records.py`` first: this script reads
``netsuite_item_images`` and writes rows — it never calls NetSuite and never
builds a URL.

It also never derives an internal id from an image URL. The file-name segment
of a media URL is the upload's name, not the item id (``4006_60047-RAIL_3_1.png``
next to ``4006_10001_1.png``), so a URL cannot say which internal id owns it.

Everything here is blank-only and re-runnable; nothing is guessed:

1. **id** — only with ``--match-item-names``. A row with no ``netsuite_item_id``
   whose ``item_name`` matches exactly one imported record's ``item_name``
   (case-insensitive, trimmed) takes that record's id. Rows that match nothing,
   or match more than one record, are counted and left blank.
2. **url** — a row with an id whose image field is blank takes the URL imported
   for that id. A row whose id has no record stays blank.
3. **--refresh-urls** — rewrite an image field whose stored value is a NetSuite
   media URL that no longer matches the record (the escape hatch for a host
   answered after the first import). A Cloudinary upload or any other URL is
   never touched, so a manual image always survives.

Usage (from the repo root, same env as the app):
    python backend/scripts/backfill_netsuite_ids.py                        # dry run
    python backend/scripts/backfill_netsuite_ids.py --apply
    python backend/scripts/backfill_netsuite_ids.py --apply --match-item-names
    python backend/scripts/backfill_netsuite_ids.py --apply --refresh-urls --company phillips
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402
from item_images import is_netsuite_media_url, normalize_item_id  # noqa: E402

TARGETS: tuple[tuple[str, str], ...] = (
    ("tracker_items", "visual_reference"),
    ("survey_items", "image_url"),
)


def _columns(conn: Any, table: str) -> set[str]:
    if IS_POSTGRES:
        rows = execute(
            conn,
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    else:
        rows = execute(conn, f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _company_filter(conn: Any, slug: str | None) -> int | None:
    if not slug:
        return None
    company = fetch_company_by_slug(conn, slug)
    if company is None:
        raise SystemExit(f"No company with slug '{slug}'.")
    return int(company["id"])


def _records(conn: Any, company_id: int | None) -> tuple[dict[int, dict[str, str]], dict[tuple[int, str], list[str]]]:
    """Index the imported records by (company, id) and by (company, lower name)."""
    where = "WHERE company_id = ?" if company_id is not None else ""
    args: tuple[Any, ...] = (company_id,) if where else ()
    rows = execute(
        conn,
        f"SELECT company_id, internal_id, image_url, item_name FROM netsuite_item_images {where}",
        args,
    ).fetchall()

    by_id: dict[int, dict[str, str]] = {}
    by_name: dict[tuple[int, str], list[str]] = {}
    for row in rows:
        row_company = int(row["company_id"])
        internal_id = normalize_item_id(row["internal_id"])
        if not internal_id:
            continue
        by_id[(row_company, internal_id)] = {
            "image_url": str(row["image_url"] or "").strip(),
            "item_name": str(row["item_name"] or "").strip(),
        }
        name = str(row["item_name"] or "").strip().lower()
        if name:
            by_name.setdefault((row_company, name), []).append(internal_id)
    return by_id, by_name


def _plan_table(
    conn: Any,
    table: str,
    url_column: str,
    company_id: int | None,
    records_by_id: dict[tuple[int, str], dict[str, str]],
    records_by_name: dict[tuple[int, str], list[str]],
    *,
    match_item_names: bool,
    refresh_urls: bool,
) -> tuple[list[tuple[str, Any, Any, Any]], dict[str, int]]:
    """Return (updates, counts); updates are (kind, row id, field, value)."""
    columns = _columns(conn, table)
    if "netsuite_item_id" not in columns:
        raise SystemExit(f"{table} has no netsuite_item_id column — start the app once so init_db() migrates it.")
    has_company = "company_id" in columns
    where = "WHERE company_id = ?" if company_id is not None and has_company else ""
    args: tuple[Any, ...] = (company_id,) if where else ()
    rows = execute(
        conn,
        f"SELECT id, {url_column} AS stored_url, netsuite_item_id, item_name"
        f"{', company_id' if has_company else ''} FROM {table} {where} ORDER BY id",
        args,
    ).fetchall()

    updates: list[tuple[str, Any, Any, Any]] = []
    counts = {
        "scanned": 0, "ids_present": 0, "ids_from_name": 0, "id_ambiguous": 0,
        "id_unmatched": 0, "urls_filled": 0, "urls_refreshed": 0, "no_record": 0,
        "manual_url": 0,
    }

    for row in rows:
        counts["scanned"] += 1
        row_company = int(row["company_id"]) if has_company and row["company_id"] is not None else None
        stored_url = str(row["stored_url"] or "").strip()

        item_id = normalize_item_id(row["netsuite_item_id"])
        if item_id:
            counts["ids_present"] += 1
        elif match_item_names:
            name = str(row["item_name"] or "").strip().lower()
            matches = records_by_name.get((row_company, name), []) if name and row_company is not None else []
            if len(matches) == 1:
                item_id = matches[0]
                counts["ids_from_name"] += 1
                updates.append(("id", row["id"], "netsuite_item_id", item_id))
            elif len(matches) > 1:
                counts["id_ambiguous"] += 1
            else:
                counts["id_unmatched"] += 1

        if not item_id or row_company is None:
            continue
        record = records_by_id.get((row_company, item_id))
        if record is None:
            if not stored_url:
                counts["no_record"] += 1
            continue
        record_url = record["image_url"]

        if not stored_url:
            if record_url:
                counts["urls_filled"] += 1
                updates.append(("url", row["id"], url_column, record_url))
            else:
                counts["no_record"] += 1
            continue

        if not refresh_urls or not record_url or record_url == stored_url:
            continue
        if not is_netsuite_media_url(stored_url):
            # A Cloudinary upload or someone's hand-pasted URL: not ours to change.
            counts["manual_url"] += 1
            continue
        counts["urls_refreshed"] += 1
        updates.append(("url", row["id"], url_column, record_url))

    return updates, counts


def _report_unresolved_companies(conn: Any, table: str) -> None:
    """One line per company whose ids resolve to nothing — says "check the host", not per row.

    The usual cause is an import that ran without ``--host``: every record then
    holds a file name and an empty URL, so every row looks like a data miss when
    the actual fault is one missing argument. Printed once per company so a
    misconfigured host never looks like thousands of bad rows.
    """
    rows = execute(
        conn,
        f"""
        SELECT c.slug AS slug, ti.company_id AS company_id,
               COUNT(*) AS rows_with_ids,
               SUM(CASE WHEN EXISTS (
                        SELECT 1 FROM netsuite_item_images r
                        WHERE r.company_id = ti.company_id
                          AND r.internal_id = ti.netsuite_item_id
                          AND r.image_url <> ''
                    ) THEN 1 ELSE 0 END) AS resolvable
        FROM {table} ti
        JOIN companies c ON c.id = ti.company_id
        WHERE ti.netsuite_item_id IS NOT NULL AND ti.netsuite_item_id <> ''
        GROUP BY ti.company_id, c.slug
        """,
    ).fetchall()
    for row in rows:
        if int(row["resolvable"] or 0) == 0:
            print(
                f"  ! company '{row['slug']}' (id {row['company_id']}): none of its"
                f" {row['rows_with_ids']} id(s) resolve to an imported image URL."
                " No record carries a URL — re-run scripts/import_item_image_records.py"
                " with --host <site> rather than treating this as missing data."
            )


def _apply(conn: Any, table: str, updates: list[tuple[str, Any, Any, Any]], apply_changes: bool) -> None:
    if not apply_changes:
        return
    for _kind, row_id, field, value in updates:
        execute(conn, f"UPDATE {table} SET {field} = ? WHERE id = ?", (value, row_id))


def _report(table: str, updates: list[tuple[str, Any, Any, Any]], counts: dict[str, int], applied: bool) -> None:
    print(f"\n{table}")
    print(f"  scanned              {counts['scanned']}")
    print(f"  ids already set      {counts['ids_present']}")
    print(f"  ids from item name   {counts['ids_from_name']}")
    print(f"  name matched 0 rows  {counts['id_unmatched']}")
    print(f"  name matched >1 row  {counts['id_ambiguous']}")
    print(f"  urls filled          {counts['urls_filled']}")
    print(f"  urls refreshed       {counts['urls_refreshed']}")
    print(f"  id with no record    {counts['no_record']}")
    if counts["manual_url"]:
        print(f"  manual urls kept     {counts['manual_url']}")
    if updates:
        print(f"  {'WRITTEN' if applied else 'DRY RUN (use --apply)'}:")
        for kind, row_id, field, value in updates[:5]:
            print(f"    {kind} id={row_id} {field}={value}")
        if len(updates) > 5:
            print(f"    … {len(updates) - 5} more")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    parser.add_argument(
        "--match-item-names",
        action="store_true",
        help="also fill a blank id on rows whose item_name matches exactly one imported record",
    )
    parser.add_argument(
        "--refresh-urls",
        action="store_true",
        help="rewrite stored NetSuite media URLs that differ from the record (wrong host answered later)",
    )
    parser.add_argument("--company", default=None, help="limit to one company slug")
    args = parser.parse_args()

    init_db()

    with connect() as conn:
        company_id = _company_filter(conn, args.company)
        if args.company:
            print(f"Company scope: {args.company} (id {company_id})")

        records_by_id, records_by_name = _records(conn, company_id)
        if not records_by_id:
            print("No imported NetSuite item records yet — run scripts/import_item_image_records.py first.")
            return 1
        print(f"Imported records: {len(records_by_id)}"
              + (f" for company id {company_id}" if company_id is not None else ""))

        for table, url_column in TARGETS:
            updates, counts = _plan_table(
                conn, table, url_column, company_id, records_by_id, records_by_name,
                match_item_names=args.match_item_names, refresh_urls=args.refresh_urls,
            )
            _report(table, updates, counts, args.apply)
            _report_unresolved_companies(conn, table)
            _apply(conn, table, updates, args.apply)

    print("\nDone." + ("" if args.apply else " No changes written."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
