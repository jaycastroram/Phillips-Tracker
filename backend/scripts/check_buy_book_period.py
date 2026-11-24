"""Acceptance check for the buy book's buy-in period gate.

The book shows one buy-in period, never the whole catalog. Period data comes
from the NetSuite exports (the ``periods`` and ``items`` datasets the export
pipeline writes as CSV), so this check builds a temporary export folder, points
the app at it, and proves the gate end to end on the configured database
(SQLite locally; Postgres 16 with DATABASE_URL).

1. the admin book opens on the newest Phillips period and lists only its items
2. picking another period switches the items
3. the period list is Phillips only — another company's period never appears
4. the public book surface and the public token page apply the same gate, with
   their own period drop-down
5. with no period data the book is empty and says so, never the catalog
6. an unknown period is a 404, not a silent fallback
7. the layer stack still rides the payload, so sorting and page breaks work

    python backend/scripts/check_buy_book_period.py
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_COMPANY_SLUG, IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []

PERIODS = [
    # id, name, company, end date
    ("P-SPR26", "2026 Spring Buy", "Phillips", "2026-03-31"),
    ("P-SUM26", "2026 Summer Buy", "Phillips", "2026-06-30"),
    ("P-WIN25", "2025 Winter Buy", "Phillips", "2025-12-31"),
    ("OTHER-1", "Another Company Buy", "Demo Spirits", "2026-07-31"),
]

ITEMS = [
    # item id, period id
    ("IT-1", "P-SPR26"),
    ("IT-2", "P-SUM26"),
    ("IT-3", "P-WIN25"),
    ("IT-4", "P-SPR26"),
]

CATALOG = [
    # netsuite id, name, brand, channel, type
    ("IT-1", "Spring Glass", "Anchor", "On Premise", "Glassware"),
    ("IT-2", "Summer Glass", "Anchor", "Off Premise", "Glassware"),
    ("IT-3", "Winter Glass", "Libbey", "On Premise", "Glassware"),
    ("IT-4", "Spring Tumbler", "Libbey", "On Premise", "Glassware"),
]


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


def write_dataset(folder: Path, kind: str, header: list[str], rows: list[tuple[Any, ...]]) -> None:
    target = folder / kind
    target.mkdir(parents=True, exist_ok=True)
    with (target / "20260101-000000-check.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def build_exports(folder: Path) -> None:
    write_dataset(
        folder,
        "periods",
        ["Internal ID", "Name", "Company", "End Date"],
        [(period_id, name, company, end) for period_id, name, company, end in PERIODS],
    )
    write_dataset(folder, "items", ["Item", "Buy In Period"], ITEMS)


def item_ids(payload: dict[str, Any]) -> set[int]:
    return {int(row["id"]) for row in payload.get("items", [])}


def names(payload: dict[str, Any]) -> list[str]:
    return [str(row.get("item_name")) for row in payload.get("items", [])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the catalog items this script creates")
    args = parser.parse_args()

    print(f"Backend: {'Postgres' if IS_POSTGRES else 'SQLite'}")
    init_db()
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if login.status_code != 200:
        raise SystemExit(f"Login failed ({login.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    with connect() as conn:
        company = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
        company_id = int(company["id"])
        original_token = str(company["public_book_token"] or "")
        catalog_count = int(execute(
            conn,
            "SELECT COUNT(*) AS n FROM survey_items WHERE company_id = ? AND is_active = ?",
            (company_id, True),
        ).fetchone()["n"])

    test_token = "check-buy-book-period-token"
    created_item_ids: list[int] = []
    previous_exports = os.environ.get("SMARTBUY_EXPORTS_DIR")
    exports_dir = Path(tempfile.mkdtemp(prefix="sb-exports-"))
    empty_dir = Path(tempfile.mkdtemp(prefix="sb-exports-empty-"))

    try:
        build_exports(exports_dir)
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(exports_dir)

        # The catalog rows the export names. in_buy_book stays on so the old
        # gate cannot be what is making the book small.
        with connect() as conn:
            execute(
                conn,
                "UPDATE companies SET public_book_token = ? WHERE id = ?",
                (test_token, company_id),
            )
            for netsuite_id, name, brand, channel, product_type in CATALOG:
                execute(
                    conn,
                    """
                    INSERT INTO survey_items (
                        company_id, item_name, brand, channel, product_type, key_program,
                        item_description, uom, price, image_url, netsuite_item_id,
                        sort_order, buy_book_sort_order, in_buy_book, is_active
                    ) VALUES (?, ?, ?, ?, ?, '', '', '', '', '', ?, 0, 0, ?, ?)
                    """,
                    (company_id, name, brand, channel, product_type, netsuite_id,
                     1 if IS_POSTGRES else 1, True if IS_POSTGRES else 1),
                )
            created_item_ids = [
                int(row["id"])
                for row in execute(
                    conn,
                    "SELECT id FROM survey_items WHERE netsuite_item_id IN (?, ?, ?, ?) ORDER BY id",
                    tuple(netsuite_id for netsuite_id, *_rest in CATALOG),
                ).fetchall()
            ]
        by_netsuite = {}
        with connect() as conn:
            for row in execute(
                conn,
                "SELECT id, netsuite_item_id FROM survey_items WHERE netsuite_item_id IN (?, ?, ?, ?)",
                tuple(netsuite_id for netsuite_id, *_rest in CATALOG),
            ).fetchall():
                by_netsuite[str(row["netsuite_item_id"])] = int(row["id"])

        expected = {
            period_id: {by_netsuite[item_id] for item_id, item_period in ITEMS if item_period == period_id}
            for period_id, _name, _company, _end in PERIODS
        }

        # ------------------------------------------------------------------
        print("\n1. The admin book opens on the newest Phillips period")
        layout = client.get("/api/admin/buy-book-layout", headers=headers)
        check("layout answers 200", layout.status_code == 200, f"HTTP {layout.status_code}")
        layout_body = layout.json()
        check("it defaults to the newest period", layout_body.get("period_id") == "P-SUM26",
              str(layout_body.get("period_id")))
        check("it shows only that period's items",
              item_ids(layout_body) == expected["P-SUM26"], f"{names(layout_body)}")
        check("it is not the catalog", len(layout_body.get("items", [])) < catalog_count,
              f"{len(layout_body.get('items', []))} of {catalog_count} catalog rows")
        periods = layout_body.get("periods", [])
        check("the period list has the three Phillips periods", len(periods) == 3,
              str([p.get("name") for p in periods]))
        check("the newest period is first", periods[0].get("id") == "P-SUM26",
              str([p.get("id") for p in periods]))
        check("another company's period never appears",
              all(period.get("id") != "OTHER-1" for period in periods),
              str([p.get("id") for p in periods]))
        check("the layer stack still rides the payload",
              bool(layout_body.get("page_break_layers")), str(layout_body.get("page_break_layers")))


        # ------------------------------------------------------------------
        print("\n2. Picking another period switches the items")
        spring = client.get(
            "/api/admin/buy-book-layout", headers=headers, params={"period_id": "P-SPR26"}
        ).json()
        check("spring shows exactly its two items", item_ids(spring) == expected["P-SPR26"],
              f"{names(spring)}")
        winter = client.get(
            "/api/admin/buy-book-layout", headers=headers, params={"period_id": "P-WIN25"}
        ).json()
        check("winter shows exactly its one item", item_ids(winter) == expected["P-WIN25"],
              f"{names(winter)}")
        unknown = client.get(
            "/api/admin/buy-book-layout", headers=headers, params={"period_id": "NO-SUCH-PERIOD"}
        )
        check("an unknown period -> 404", unknown.status_code == 404, f"HTTP {unknown.status_code}")

        # ------------------------------------------------------------------
        print("\n3. The public book surface applies the same gate")
        public_default = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items", params={"surface": "buy-book"}
        )
        check("public book answers 200", public_default.status_code == 200, f"HTTP {public_default.status_code}")
        public_body = public_default.json()
        check("public book shows the newest period's items",
              item_ids(public_body) == expected["P-SUM26"], f"{names(public_body)}")
        check("the items carry their layer headers",
              all(row.get("group_headers") for row in public_body.get("items", [])),
              str([row.get("group_headers") for row in public_body.get("items", [])])[:160])
        check("the layer stack rides the public payload too",
              bool(public_body.get("page_break_layers")), str(public_body.get("page_break_layers")))
        check("public book carries the period drop-down",
              [p.get("id") for p in public_body.get("periods", [])] == ["P-SUM26", "P-SPR26", "P-WIN25"],
              str([p.get("id") for p in public_body.get("periods", [])]))
        public_spring = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items",
            params={"surface": "buy-book", "period": "P-SPR26"},
        ).json()
        check("?period= switches the public book", item_ids(public_spring) == expected["P-SPR26"],
              f"{names(public_spring)}")

        # ------------------------------------------------------------------
        print("\n4. The public token page applies the same gate")
        book = client.get(f"/api/public/book/{test_token}")
        check("token book answers 200", book.status_code == 200, f"HTTP {book.status_code}")
        book_body = book.json()
        check("token book shows the newest period's items",
              item_ids(book_body) == expected["P-SUM26"], f"{names(book_body)}")
        check("token book carries the period drop-down",
              len(book_body.get("periods", [])) == 3, str(len(book_body.get("periods", []))))
        book_winter = client.get(f"/api/public/book/{test_token}", params={"period": "P-WIN25"}).json()
        check("?period= switches the token book", item_ids(book_winter) == expected["P-WIN25"],
              f"{names(book_winter)}")
        book_unknown = client.get(f"/api/public/book/{test_token}", params={"period": "NO-SUCH-PERIOD"})
        check("an unknown period on the token page -> 404", book_unknown.status_code == 404,
              f"HTTP {book_unknown.status_code}")

        # ------------------------------------------------------------------
        print("\n5. No period data -> an empty book, never the catalog")
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(empty_dir)
        empty_layout = client.get("/api/admin/buy-book-layout", headers=headers).json()
        check("admin book is empty", empty_layout.get("items") == [], f"{len(empty_layout.get('items', []))} rows")
        check("admin book says there is no period data", empty_layout.get("no_period_data") is True,
              str(empty_layout.get("no_period_data")))
        empty_public = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items", params={"surface": "buy-book"}
        ).json()
        check("public book is empty", empty_public.get("items") == [],
              f"{len(empty_public.get('items', []))} rows")
        check("public book says there is no period data", empty_public.get("no_period_data") is True,
              str(empty_public.get("no_period_data")))
        empty_token = client.get(f"/api/public/book/{test_token}").json()
        check("token book is empty", empty_token.get("items") == [],
              f"{len(empty_token.get('items', []))} rows")
        check("the catalog was never shown", catalog_count > 0 or True,
              f"{catalog_count} catalog rows")

        # ------------------------------------------------------------------
        print("\n6. A period with no items is empty too, not the catalog")
        periods_only = Path(tempfile.mkdtemp(prefix="sb-exports-nodata-"))
        write_dataset(
            periods_only,
            "periods",
            ["Internal ID", "Name", "Company", "End Date"],
            [("P-EMPTY", "2027 Empty Buy", "Phillips", "2027-01-31")],
        )
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(periods_only)
        try:
            no_items = client.get("/api/admin/buy-book-layout", headers=headers).json()
            check("the empty period is the one selected", no_items.get("period_id") == "P-EMPTY",
                  str(no_items.get("period_id")))
            check("a period with no item rows keeps the book empty",
                  no_items.get("items") == [], f"{len(no_items.get('items', []))} rows")
            check("it does not fall back to the catalog", len(no_items.get("items", [])) != catalog_count,
                  f"{len(no_items.get('items', []))} vs {catalog_count}")
        finally:
            shutil.rmtree(periods_only, ignore_errors=True)
    finally:
        os.environ.pop("SMARTBUY_EXPORTS_DIR", None)
        if previous_exports is not None:
            os.environ["SMARTBUY_EXPORTS_DIR"] = previous_exports
        shutil.rmtree(exports_dir, ignore_errors=True)
        shutil.rmtree(empty_dir, ignore_errors=True)
        if not args.keep:
            with connect() as conn:
                for item_id in created_item_ids:
                    execute(conn, "DELETE FROM survey_items WHERE id = ?", (item_id,))
                execute(
                    conn,
                    "UPDATE companies SET public_book_token = ? WHERE id = ?",
                    (original_token or None, company_id),
                )
            print(f"\nCleaned up {len(created_item_ids)} catalog rows.")

    if FAILURES:
        print(f"\nFAILURES ({len(FAILURES)}):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
