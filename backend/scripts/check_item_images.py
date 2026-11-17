"""End-to-end check for NetSuite item images, against a record-sourced URL.

Start the app first — on the port it binds (``PORT``, default 8000) — and run
the check against that same URL and the same database:

    python backend/app.py
    python backend/scripts/check_item_images.py --records records.json --host https://demo.mrlsmartbuy.com

What it proves, in order — the expected value in every assertion is a URL that
came from a NetSuite record, never one the app built:

1. import — the records file lands in ``netsuite_item_images``, and a second
   import of the same file changes nothing (inserted 0, updated 0)
2. no formatting — ``netsuite_image_url`` returns '' for a real id, and a
   SKU-shaped value such as ``60047-RAIL`` is not accepted as an internal id
3. tracker write hook — a row with a blank ``visual_reference`` and that
   internal id comes back from ``/api/items`` and ``/api/public/items`` carrying
   exactly the record's URL; an id with no record stays blank; an existing URL
   is never overwritten
4. catalog read path — the record's URL reaches both public survey surfaces
5. publish snapshot — a new period row carries the URL, not just the page
6. render gate — the record URL passes isImageReference, while the
   ``/core/media/media.nl?id=…&resizeid=11`` form and an extension-less URL fail
7. migrations — a second ``init_db()`` adds no column or table twice

Step 4 patches the database directly and is printed as DB-DIRECT: no app route
writes ``survey_items.image_url`` today (only the backfill and the period
snapshot do), so pretending otherwise would hide which half is unproven. Every
change is restored before exit; pass ``--keep-changes`` to inspect it.

``--records`` is optional. Without it the check reads whatever was imported
earlier and says so; with it, the check also proves the import is re-runnable.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from openpyxl import Workbook

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    DEFAULT_COMPANY_SLUG,
    IS_POSTGRES,
    NETSUITE_IMAGE_BASE,
)
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402
from item_images import (  # noqa: E402
    is_netsuite_media_url,
    media_file_from_url,
    media_reference,
    netsuite_image_url,
    normalize_item_id,
    passes_render_gate,
    record_image_url,
    verify_image_url,
)

FAILURES: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


def _load_import_script():
    path = Path(__file__).with_name("import_item_image_records.py")
    spec = importlib.util.spec_from_file_location("import_item_image_records", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_excel_import():
    path = Path(__file__).with_name("import_excel.py")
    spec = importlib.util.spec_from_file_location("import_excel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_backfill() -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "scripts" / "backfill_netsuite_ids.py"),
            "--apply",
            "--company",
            DEFAULT_COMPANY_SLUG,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def build_fixture_workbook() -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)
    header = [
        "Date/Buy", "Current Status", "Visual Reference", "Brand", "Program Name", "Item Name",
        "Qty", "Important Notes", "MRL Order #", "Est. Ship Date", "Est. IHD", "Tracking",
        "NetSuite ID",
    ]
    for name in ("Ad Hoc", "Buys", "Completed"):
        sheet = workbook.create_sheet(name)
        sheet.append(header)
    sheet = workbook["Ad Hoc"]
    sheet.append(["", "", "", "BrandX", "ProgX", "Importer Fixture Item", "1", "", "", "", "", "", "10001"])
    sheet.append(["", "", "", "BrandX", "ProgX", "Importer SKU Row", "1", "", "", "", "", "", "60047-RAIL"])
    path = Path(tempfile.mkdtemp()) / "check_item_images_fixture.xlsx"
    workbook.save(path)
    return path


def tracker_counts() -> dict[str, int]:
    with connect() as conn:
        rows = execute(conn, "SELECT company_id, COUNT(*) AS n FROM tracker_items GROUP BY company_id").fetchall()
    return {str(row["company_id"]): int(row["n"]) for row in rows}


def count_objects(table: str, column: str = "") -> int:
    with connect() as conn:
        if IS_POSTGRES:
            if column:
                rows = execute(
                    conn,
                    "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
                    (table,),
                ).fetchall()
            else:
                rows = execute(
                    conn, "SELECT table_name AS name FROM information_schema.tables WHERE table_name = ?", (table,)
                ).fetchall()
        elif column:
            rows = execute(conn, f"PRAGMA table_info({table})").fetchall()
        else:
            rows = execute(conn, "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchall()
    return sum(1 for row in rows if not column or row["name"] == column)


def store_count(company_id: int) -> int:
    with connect() as conn:
        row = execute(
            conn, "SELECT COUNT(*) AS n FROM netsuite_item_images WHERE company_id = ?", (company_id,)
        ).fetchone()
    return int(row["n"])


def admin_token(client: httpx.Client) -> str:
    response = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if response.status_code != 200:
        raise SystemExit(
            f"Login failed ({response.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD to this database's admin."
        )
    return response.json()["token"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000",
        help="the running app's URL — the port it binds (PORT, default 8000)",
    )
    parser.add_argument("--records", default=None, help="the records file that was imported (JSON or CSV)")
    parser.add_argument("--host", default="", help="SuiteCommerce host used at import, for a relative record path")
    parser.add_argument("--internal-id", default="", help="record to assert on (default: first imported with a URL)")
    parser.add_argument("--no-network", action="store_true", help="skip the HEAD 200 assertion on the record URL")
    parser.add_argument("--keep-changes", action="store_true", help="do not restore the test rows")
    args = parser.parse_args()

    init_db()

    client = httpx.Client(base_url=args.base_url, timeout=20)
    try:
        health = client.get("/api/health")
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"{args.base_url} is not answering ({exc.__class__.__name__}).\n"
            "Start the app first: python backend/app.py   (binds PORT, default 8000)"
        )
    if health.status_code != 200:
        raise SystemExit(f"{args.base_url}/api/health returned HTTP {health.status_code}")

    print(f"Target: {args.base_url}")
    headers = {"Authorization": f"Bearer {admin_token(client)}"}

    with connect() as conn:
        company = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
    if company is None:
        raise SystemExit(f"No company with slug '{DEFAULT_COMPANY_SLUG}' — nothing to check.")
    company_id = int(company["id"])
    host = (args.host or str(company["netsuite_image_base"] or "") or NETSUITE_IMAGE_BASE).strip()

    print("\n1. Import the record set (the only source of a URL)")
    imported: list[dict[str, str]] = []
    if args.records:
        import_script = _load_import_script()
        records = import_script.load_records(Path(args.records))
        before = store_count(company_id)
        with connect() as conn:
            first = import_script.ingest_records(
                conn, records, company_id, host,
                apply_changes=True, verify=False, source=Path(args.records).name,
            )
            second = import_script.ingest_records(
                conn, records, company_id, host,
                apply_changes=True, verify=False, source=Path(args.records).name,
            )
        after = store_count(company_id)
        print(f"  records: {len(records)}   rows {before} -> {after}"
              f"   run1 +{first['inserted']} ~{first['updated']}   run2 +{second['inserted']} ~{second['updated']}")
        check("import wrote rows", after >= before and (after - before) == first["inserted"], f"{before} -> {after}")
        check("re-import is a no-op", second["inserted"] == 0 and second["updated"] == 0,
              f"+{second['inserted']} ~{second['updated']}")
        for record in records:
            parsed = import_script.extract(record, host)
            if parsed["internal_id"] and parsed["image_url"]:
                imported.append(parsed)
    else:
        print("  no --records: reading what an earlier import stored (nothing re-imported)")

    with connect() as conn:
        store_rows = execute(
            conn,
            "SELECT internal_id, media_file, image_url FROM netsuite_item_images"
            " WHERE company_id = ? AND image_url <> '' ORDER BY id",
            (company_id,),
        ).fetchall()
    if not store_rows:
        raise SystemExit(
            "No imported record with an image URL for this company.\n"
            "Run: python backend/scripts/import_item_image_records.py <records> --apply --host <site>"
        )

    wanted = normalize_item_id(args.internal_id)
    chosen = next((row for row in store_rows if wanted and row["internal_id"] == wanted), None)
    if wanted and chosen is None:
        raise SystemExit(f"internal id {wanted} has no imported record with a URL.")
    chosen = chosen or store_rows[0]
    expected = str(chosen["image_url"]).strip()
    internal_id = str(chosen["internal_id"]).strip()
    print(f"  expected URL: {expected}")
    print(f"  internal id:  {internal_id}   media file: {chosen['media_file']}")

    check(
        "the store holds the record's own file name",
        media_file_from_url(expected) == chosen["media_file"] != "",
        f"{media_file_from_url(expected)} vs {chosen['media_file']}",
    )
    if imported:
        source = next((entry for entry in imported if entry["internal_id"] == internal_id), None)
        check(
            "stored URL is the one in the record set",
            source is not None and source["image_url"] == expected,
            f"{source['image_url'] if source else '<id not in file>'} vs {expected}",
        )
        check(
            "record id is numeric — a file name would not pass",
            normalize_item_id(source["internal_id"] if source else "") == internal_id,
        )
    check("record URL passes the render gate", passes_render_gate(expected), expected)
    if not args.no_network:
        check("record URL answers 200 with an image", verify_image_url(expected, timeout=8), expected)

    print("\n2. No URL is built from an id")
    check("netsuite_image_url is a stub", netsuite_image_url(company, internal_id) == "")
    check("stub returns '' for any id", netsuite_image_url(company, "999999999") == "")
    check("SKU-shaped value is not an internal id", normalize_item_id("60047-RAIL") == "", "60047-RAIL")
    check("file-name segment is not an internal id", normalize_item_id(media_file_from_url(expected)) == "",
          media_file_from_url(expected))

    print("\n3. Tracker write hook (through the app)")
    listing = client.get("/api/items", headers=headers, params={"sheet": "ad-hoc"}).json().get("items", [])
    candidates = [
        item for item in listing
        if int(item.get("company_id") or 0) == company_id
        and str(item.get("current_status") or "") != "Delivered"
        and item.get("sheet") in ("ad-hoc", "buys")
    ]
    if not candidates:
        raise SystemExit("No usable tracker row (ad-hoc/buys, not Delivered) — seed or import some data first.")
    tracker_item = candidates[0]
    original_tracker = {k: tracker_item.get(k, "") for k in ("visual_reference", "netsuite_item_id")}
    unknown_id = "999999999"
    with connect() as conn:
        clash = execute(
            conn, "SELECT 1 FROM netsuite_item_images WHERE company_id = ? AND internal_id = ?",
            (company_id, unknown_id),
        ).fetchone()
    if clash is not None:
        unknown_id = "999999998"

    try:
        response = client.patch(
            f"/api/items/{tracker_item['id']}",
            headers=headers,
            json={"visual_reference": "", "netsuite_item_id": internal_id},
        )
        check("PATCH /api/items succeeds", response.status_code == 200, f"HTTP {response.status_code}")
        item = response.json().get("item", {})
        check("response carries the record URL", str(item.get("visual_reference") or "") == expected,
              repr(item.get("visual_reference")))

        admin_view = client.get("/api/items", headers=headers, params={"sheet": "ad-hoc"}).json().get("items", [])
        admin_row = next((row for row in admin_view if row["id"] == tracker_item["id"]), None)
        check("/api/items carries the record URL",
              admin_row is not None and admin_row.get("visual_reference") == expected,
              repr(admin_row.get("visual_reference") if admin_row else None))

        public_view = client.get("/api/public/items").json().get("items", [])
        public_row = next((row for row in public_view if row["id"] == tracker_item["id"]), None)
        check("/api/public/items carries the record URL",
              public_row is not None and public_row.get("visual_reference") == expected,
              repr(public_row.get("visual_reference") if public_row else None))

        response = client.patch(
            f"/api/items/{tracker_item['id']}",
            headers=headers,
            json={"visual_reference": "", "netsuite_item_id": unknown_id},
        )
        item = response.json().get("item", {})
        check("an id with no record stays blank", str(item.get("visual_reference") or "") == "",
              repr(item.get("visual_reference")))

        manual = "https://res.cloudinary.com/demo/image/upload/v1/phillips-tracker/manual.png"
        response = client.patch(
            f"/api/items/{tracker_item['id']}",
            headers=headers,
            json={"visual_reference": manual, "netsuite_item_id": internal_id},
        )
        item = response.json().get("item", {})
        check("an existing URL is never overwritten", str(item.get("visual_reference") or "") == manual,
              repr(item.get("visual_reference")))
    finally:
        if not args.keep_changes:
            client.patch(f"/api/items/{tracker_item['id']}", headers=headers, json=original_tracker)
            print("  (tracker row restored)")

    print("\n4. Catalog read path (DB-DIRECT: no app route writes survey_items.image_url)")
    with connect() as conn:
        survey_row = execute(
            conn,
            """
            SELECT id, image_url, netsuite_item_id FROM survey_items
            WHERE company_id = ? AND is_active = ? AND in_survey = ? AND in_buy_book = ?
            ORDER BY id LIMIT 1
            """,
            (company_id, True, 1, 1),
        ).fetchone()
    if survey_row is None:
        raise SystemExit("No active catalog item in both survey and buy book — seed data first.")
    survey_item_id = int(survey_row["id"])
    original_survey = {"image_url": survey_row["image_url"] or "", "netsuite_item_id": survey_row["netsuite_item_id"] or ""}
    period_id: int | None = None
    try:
        with connect() as conn:
            execute(conn, "UPDATE survey_items SET image_url = '', netsuite_item_id = ? WHERE id = ?",
                    (internal_id, survey_item_id))
            from_record = record_image_url(conn, company_id, internal_id)
            check("record lookup returns the imported URL", from_record == expected, repr(from_record))
            # No app route writes survey_items.image_url, so the row is written the
            # way backfill_netsuite_ids.py writes it. Stated here so nobody reads the
            # next assertions as proof of an app write path that does not exist.
            execute(conn, "UPDATE survey_items SET image_url = ? WHERE id = ?", (from_record, survey_item_id))
        print("  (URL written DB-DIRECT, the same write the backfill script performs — not an app route)")

        default_view = client.get("/api/public/survey-items").json().get("items", [])
        default_row = next((row for row in default_view if row["id"] == survey_item_id), None)
        check("/api/public/survey-items carries the record URL",
              default_row is not None and default_row.get("image_url") == expected,
              repr(default_row.get("image_url") if default_row else None))

        buy_book_view = client.get("/api/public/survey-items", params={"surface": "buy-book"}).json().get("items", [])
        buy_book_row = next((row for row in buy_book_view if row["id"] == survey_item_id), None)
        check("/api/public/survey-items?surface=buy-book carries the record URL",
              buy_book_row is not None and buy_book_row.get("image_url") == expected,
              repr(buy_book_row.get("image_url") if buy_book_row else None))

        print("\n5. Publish snapshot (draft period row, not the page — copies the stored URL)")
        period_response = client.post(
            "/api/admin/survey-periods",
            headers=headers,
            json={"name": "check_item_images", "open_immediately": False},
        )
        check("create draft period", period_response.status_code == 201, f"HTTP {period_response.status_code}")
        if period_response.status_code == 201:
            period_id = int(period_response.json()["period"]["id"])
            with connect() as conn:
                snapshot = execute(
                    conn,
                    "SELECT image_url FROM survey_period_items WHERE survey_period_id = ? AND catalog_item_id = ?",
                    (period_id, survey_item_id),
                ).fetchone()
            check("period row carries the record URL",
                  snapshot is not None and snapshot["image_url"] == expected,
                  repr(snapshot["image_url"] if snapshot else None))
    finally:
        if not args.keep_changes:
            with connect() as conn:
                execute(
                    conn,
                    "UPDATE survey_items SET image_url = ?, netsuite_item_id = ? WHERE id = ?",
                    (original_survey["image_url"], original_survey["netsuite_item_id"], survey_item_id),
                )
            print("  (catalog row restored)")
            if period_id is not None:
                client.delete(f"/api/admin/survey-periods/{period_id}", headers=headers)
                print("  (draft period deleted)")

    print("\n6. Bulk path stores ids only, and the backfill fills them")
    marker = "check_item_images bulk row"
    bulk_row_id: int | None = None

    def bulk_row():
        with connect() as conn:
            return execute(
                conn,
                "SELECT id, visual_reference, netsuite_item_id FROM tracker_items"
                " WHERE company_id = ? AND item_name = ? ORDER BY id DESC LIMIT 1",
                (company_id, marker),
            ).fetchone()

    try:
        response = client.post(
            "/api/items/changes",
            headers=headers,
            params={"company_id": company_id},
            json={
                "creates": [
                    {"sheet": "ad-hoc", "item_name": marker, "netsuite_item_id": internal_id, "visual_reference": ""}
                ],
                "updates": [],
                "deletes": [],
            },
        )
        check(
            "batch create succeeds",
            response.status_code == 200 and response.json().get("created") == 1,
            f"HTTP {response.status_code} {response.text[:80]}",
        )
        row = bulk_row()
        bulk_row_id = int(row["id"]) if row is not None else None
        check(
            "batch create stores the id",
            row is not None and str(row["netsuite_item_id"] or "") == internal_id,
            repr(row["netsuite_item_id"] if row else None),
        )
        check(
            "batch create writes no URL (no guessed extension)",
            row is not None and str(row["visual_reference"] or "") == "",
            repr(row["visual_reference"] if row else None),
        )

        backfill = run_backfill()
        check("backfill runs", backfill.returncode == 0, (backfill.stderr or backfill.stdout or "")[-200:])
        row = bulk_row()
        check(
            "backfill fills the exact record URL",
            row is not None and str(row["visual_reference"] or "") == expected,
            repr(row["visual_reference"] if row else None),
        )

        response = client.post(
            "/api/items/changes",
            headers=headers,
            params={"company_id": company_id},
            json={
                "creates": [],
                "updates": [{"id": bulk_row_id, "changes": {"visual_reference": "", "netsuite_item_id": internal_id}}],
                "deletes": [],
            },
        )
        check(
            "batch update succeeds",
            response.status_code == 200 and response.json().get("updated") == 1,
            f"HTTP {response.status_code} {response.text[:80]}",
        )
        row = bulk_row()
        check(
            "batch update writes no URL either",
            row is not None and str(row["visual_reference"] or "") == "",
            repr(row["visual_reference"] if row else None),
        )
        run_backfill()
        row = bulk_row()
        check(
            "a second backfill fills it again",
            row is not None and str(row["visual_reference"] or "") == expected,
            repr(row["visual_reference"] if row else None),
        )
    finally:
        if bulk_row_id is not None and not args.keep_changes:
            client.delete(f"/api/items/{bulk_row_id}", headers=headers)
            print("  (bulk row deleted)")

    print("\n7. Workbook importer carries the id (fixture workbook)")
    fixture_slug = f"check-importer-{os.urandom(3).hex()}"
    before_counts = tracker_counts()
    with connect() as conn:
        execute(
            conn,
            "INSERT INTO companies (slug, name, logo_url, banner_url, is_active) VALUES (?, ?, ?, ?, ?)",
            (fixture_slug, "Check Importer", "", "", True),
        )
        fixture_company_id = int(
            execute(conn, "SELECT id FROM companies WHERE slug = ?", (fixture_slug,)).fetchone()["id"]
        )
    try:
        _load_excel_import().import_workbook(build_fixture_workbook(), fixture_slug)
        with connect() as conn:
            fixture_rows = execute(
                conn,
                "SELECT item_name, netsuite_item_id, visual_reference, extra_json"
                " FROM tracker_items WHERE company_id = ?",
                (fixture_company_id,),
            ).fetchall()
        fixture_row = next((row for row in fixture_rows if row["item_name"] == "Importer Fixture Item"), None)
        sku_row = next((row for row in fixture_rows if row["item_name"] == "Importer SKU Row"), None)
        check(
            "imported row carries the id",
            fixture_row is not None and str(fixture_row["netsuite_item_id"] or "") == "10001",
            repr(fixture_row["netsuite_item_id"] if fixture_row else None),
        )
        check(
            "a SKU in the id column is rejected, not stored",
            sku_row is not None and str(sku_row["netsuite_item_id"] or "") == "",
            repr(sku_row["netsuite_item_id"] if sku_row else None),
        )
        check(
            "the id is not left only in extra_json",
            all("NetSuite ID" not in str(row["extra_json"] or "") for row in fixture_rows),
        )
        check(
            "the import wrote no image URL of its own",
            all(str(row["visual_reference"] or "") == "" for row in fixture_rows),
        )
        after_counts = tracker_counts()
        check(
            "every other company's rows were left alone",
            all(after_counts.get(key, 0) == value for key, value in before_counts.items()),
            f"{before_counts} -> {after_counts}",
        )
    finally:
        with connect() as conn:
            execute(conn, "DELETE FROM tracker_items WHERE company_id = ?", (fixture_company_id,))
            execute(conn, "DELETE FROM companies WHERE id = ?", (fixture_company_id,))
        print("  (fixture company removed)")

    print("\n8. Render gate (mirrors isImageReference, frontend/src/utils.ts:51-63)")
    media_form = f"{expected.split('/images/')[0]}/core/media/media.nl?id={media_file_from_url(expected)}&resizeid=11&resizew=225&resizeh=255"
    check("the record URL passes the gate", passes_render_gate(expected), expected)
    check("the media.nl form fails the gate", not passes_render_gate(media_form), media_form)
    extensionless = expected.rsplit(".", 1)[0] if "." in expected.rsplit("/", 1)[-1] else expected + "-no-extension"
    check("an extension-less URL fails the gate", not passes_render_gate(extensionless), extensionless)
    check("the stored URL is recognised as a NetSuite media URL", is_netsuite_media_url(expected), expected)
    check(
        "a relative record path becomes an absolute URL only with a host",
        (media_reference(f"/core/media/media.nl?id={media_file_from_url(expected)}", "") or {}).get("image_url") == "",
    )

    print("\n9. Migrations are idempotent")
    init_db()
    check("tracker_items has exactly one netsuite_item_id", count_objects("tracker_items", "netsuite_item_id") == 1)
    check("survey_items has exactly one netsuite_item_id", count_objects("survey_items", "netsuite_item_id") == 1)
    check("netsuite_item_images exists once", count_objects("netsuite_item_images") == 1)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
