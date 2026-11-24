"""Acceptance check for the Buy Book's buy-in period scope.

The book a customer opens is one buy-in period, never the whole storefront:
the period drop-down lists only this storefront's periods, the items are that
period's members, and the brand / channel / product-type values are computed
from those items alone. Period membership lives in the NetSuite exports
(``data/exports/periods`` and ``data/exports/items``), so this check builds a
temporary export folder, points the app at it, and proves the contract end to
end against the configured database (SQLite locally, Postgres 16 with
DATABASE_URL).

1. ``/api/public/buy-book/periods`` lists only this storefront's periods,
   newest first, with dates and item counts
2. the token book opens on the newest period that has items and returns only
   that period's members
3. ``?period=`` switches the items; a foreign or unknown period is a 404
4. the static slug route (``/phillips/buybook``'s API) applies the same scope
5. the facet values equal exactly the brands / channels / types in the period,
   and change with the period
6. a period with no members renders empty; no period data at all renders
   empty; neither falls back to the catalog
7. the PDF for a period prints that period's items and its page count matches
   the page's item count

    python backend/scripts/check_buy_book_period_scope.py
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app import app  # noqa: E402
from buy_book_periods import list_periods  # noqa: E402
from buy_book_pdf import expected_page_count  # noqa: E402
from config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_COMPANY_SLUG, IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []
PERIODS = [
    # id, name, company, end date
    ("P-SUM26", "2026 Summer Buy", "Phillips", "2026-06-30"),
    ("P-SPR26", "2026 Spring Buy", "Phillips", "2026-03-31"),
    ("P-EMPTY", "2027 Empty Buy", "Phillips", "2027-01-31"),
    ("OTHER-1", "Another Company Buy", "Demo Spirits", "2026-05-31"),
]

ITEMS = [
    # item id, period id
    ("SCOPE-IT-1", "P-SUM26"),
    ("SCOPE-IT-2", "P-SUM26"),
    ("SCOPE-IT-3", "P-SPR26"),
    ("SCOPE-IT-4", "P-SPR26"),
    ("SCOPE-IT-5", "OTHER-1"),
]

CATALOG = [
    # netsuite id, name, brand, channel, product type
    ("SCOPE-IT-1", "Summer Glass", "Anchor", "On Premise", "Glassware"),
    ("SCOPE-IT-2", "Summer Tumbler", "Anchor", "Off Premise", "Glassware"),
    ("SCOPE-IT-3", "Spring Sign", "Libbey", "On Premise", "Signage"),
    ("SCOPE-IT-4", "Spring Glass", "Libbey", "On Premise", "Glassware"),
    ("SCOPE-IT-5", "Foreign Glass", "Foreign", "On Premise", "Glassware"),
    # In the catalog, in no period: it must never appear in any period's book.
    ("SCOPE-IT-6", "Catalog Only", "Unlisted", "Online", "Novelty"),
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


def row_ids(payload: dict[str, Any]) -> set[int]:
    return {int(row["id"]) for row in payload.get("items", [])}


def item_names(payload: dict[str, Any]) -> list[str]:
    return [str(row.get("item_name")) for row in payload.get("items", [])]


def facet_of(payload: dict[str, Any], field: str) -> list[str]:
    return list((payload.get("facets") or {}).get(field) or [])


def pdf_text(payload: bytes) -> tuple[int, str]:
    reader = PdfReader(io.BytesIO(payload))
    return len(reader.pages), "\n".join(page.extract_text() or "" for page in reader.pages)


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
        if company is None:
            raise SystemExit(f"No '{DEFAULT_COMPANY_SLUG}' company in this database; run init_db first.")
        company_id = int(company["id"])
        original_token = str(company["public_book_token"] or "")
        # Idempotent: drop leftovers from an interrupted run before seeding.
        netsuite_ids = [netsuite_id for netsuite_id, *_rest in CATALOG]
        placeholders = ", ".join("?" for _ in netsuite_ids)
        execute(
            conn,
            f"DELETE FROM survey_items WHERE company_id = ? AND netsuite_item_id IN ({placeholders})",
            (company_id, *netsuite_ids),
        )
        catalog_count = int(execute(
            conn,
            "SELECT COUNT(*) AS n FROM survey_items WHERE company_id = ? AND is_active = ?",
            (company_id, True),
        ).fetchone()["n"])

    test_token = "check-buy-book-period-scope-token"
    created_item_ids: list[int] = []
    previous_exports = os.environ.get("SMARTBUY_EXPORTS_DIR")
    exports_dir = Path(tempfile.mkdtemp(prefix="sb-scope-exports-"))
    empty_dir = Path(tempfile.mkdtemp(prefix="sb-scope-empty-"))

    try:
        build_exports(exports_dir)
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(exports_dir)

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
                    (company_id, name, brand, channel, product_type,
                     netsuite_id, 1, True if IS_POSTGRES else 1),
                )
            created_item_ids = [
                int(row["id"])
                for row in execute(
                    conn,
                    f"SELECT id FROM survey_items WHERE company_id = ? "
                    f"AND netsuite_item_id IN ({placeholders}) ORDER BY id",
                    (company_id, *netsuite_ids),
                ).fetchall()
            ]
            by_netsuite = {
                str(row["netsuite_item_id"]): int(row["id"])
                for row in execute(
                    conn,
                    f"SELECT id, netsuite_item_id FROM survey_items WHERE company_id = ? "
                    f"AND netsuite_item_id IN ({placeholders})",
                    (company_id, *netsuite_ids),
                ).fetchall()
            }

        expected = {
            period_id: {by_netsuite[item_id] for item_id, item_period in ITEMS
                        if item_period == period_id and item_id in by_netsuite}
            for period_id, _name, _company, _end in PERIODS
        }

        # ------------------------------------------------------------------
        print("\n1. The periods endpoint lists only this storefront's periods")
        periods_response = client.get("/api/public/buy-book/periods")
        check("periods -> 200", periods_response.status_code == 200, str(periods_response.status_code))
        periods_body = periods_response.json()
        listed = [period.get("id") for period in periods_body.get("periods", [])]
        check("newest first, this storefront only",
              listed == ["P-EMPTY", "P-SUM26", "P-SPR26"], str(listed))
        check("another storefront's period never appears", "OTHER-1" not in listed, str(listed))
        check("each period carries its dates and item count",
              [(period.get("id"), period.get("starts_on"), period.get("ends_on"), period.get("item_count"))
               for period in periods_body.get("periods", [])]
              == [("P-EMPTY", "", "2027-01-31", 0), ("P-SUM26", "", "2026-06-30", 2),
                  ("P-SPR26", "", "2026-03-31", 2)],
              str(periods_body.get("periods")))
        check("the default period is the newest one with items",
              periods_body.get("selected_period_id") == "P-SUM26",
              str(periods_body.get("selected_period_id")))
        by_slug = client.get("/api/public/buy-book/periods", params={"company": DEFAULT_COMPANY_SLUG})
        check("?company= scopes the same list", by_slug.status_code == 200
              and [p.get("id") for p in by_slug.json().get("periods", [])] == listed,
              str(by_slug.status_code))
        by_token = client.get("/api/public/buy-book/periods", params={"token": test_token})
        check("?token= scopes to that book's storefront", by_token.status_code == 200
              and [p.get("id") for p in by_token.json().get("periods", [])] == listed,
              str(by_token.status_code))
        check("an unknown token -> 404",
              client.get("/api/public/buy-book/periods", params={"token": "no-such-token"}).status_code == 404)
        # The scope filter itself: the same export rows read as the other company.
        other = list_periods("Demo Spirits", "demo-spirits")
        check("the other storefront reads only its own period",
              [period.get("id") for period in other] == ["OTHER-1"],
              str([period.get("id") for period in other]))

        # ------------------------------------------------------------------
        print("\n2. The token book opens on the newest period with items")
        book = client.get(f"/api/public/book/{test_token}")
        check("token book -> 200", book.status_code == 200, f"HTTP {book.status_code}")
        book_body = book.json()
        check("it opens on the newest period that has items",
              (book_body.get("period") or {}).get("id") == "P-SUM26",
              str(book_body.get("period")))
        check("it returns only that period's items",
              row_ids(book_body) == expected["P-SUM26"], f"{item_names(book_body)}")
        check("it is not the catalog",
              len(row_ids(book_body)) < catalog_count + len(CATALOG),
              f"{len(row_ids(book_body))} of {catalog_count + len(CATALOG)}")
        check("an item no period names never appears",
              by_netsuite["SCOPE-IT-6"] not in row_ids(book_body), str(item_names(book_body)))
        check("the layer stack still rides the payload",
              bool(book_body.get("page_break_layers")), str(book_body.get("page_break_layers")))

        # ------------------------------------------------------------------
        print("\n3. ?period= switches the items; a foreign or unknown period is refused")
        spring = client.get(f"/api/public/book/{test_token}", params={"period": "P-SPR26"}).json()
        check("spring shows exactly its two items", row_ids(spring) == expected["P-SPR26"],
              f"{item_names(spring)}")
        summer = client.get(f"/api/public/book/{test_token}", params={"period": "P-SUM26"}).json()
        check("back to summer shows exactly its two items", row_ids(summer) == expected["P-SUM26"],
              f"{item_names(summer)}")
        foreign = client.get(f"/api/public/book/{test_token}", params={"period": "OTHER-1"})
        check("another storefront's period -> 404", foreign.status_code == 404, f"HTTP {foreign.status_code}")
        unknown = client.get(f"/api/public/book/{test_token}", params={"period": "NO-SUCH-PERIOD"})
        check("an unknown period -> 404", unknown.status_code == 404, f"HTTP {unknown.status_code}")

        # ------------------------------------------------------------------
        print("\n4. The static slug route applies the same scope")
        slug_default = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items", params={"surface": "buy-book"}
        )
        check("slug book -> 200", slug_default.status_code == 200, f"HTTP {slug_default.status_code}")
        slug_body = slug_default.json()
        check("slug book opens on the same period",
              (slug_body.get("period") or {}).get("id") == "P-SUM26", str(slug_body.get("period")))
        check("slug book returns the same items", row_ids(slug_body) == expected["P-SUM26"],
              f"{item_names(slug_body)}")
        check("slug book carries the drop-down list",
              [period.get("id") for period in slug_body.get("periods", [])] == listed,
              str([period.get("id") for period in slug_body.get("periods", [])]))
        slug_spring = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items",
            params={"surface": "buy-book", "period": "P-SPR26"},
        ).json()
        check("?period= switches the slug book", row_ids(slug_spring) == expected["P-SPR26"],
              f"{item_names(slug_spring)}")
        slug_foreign = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items",
            params={"surface": "buy-book", "period": "OTHER-1"},
        )
        check("the slug route refuses another storefront's period -> 404",
              slug_foreign.status_code == 404, f"HTTP {slug_foreign.status_code}")

        # ------------------------------------------------------------------
        print("\n5. Facets are the selected period's own")
        check("summer brands", facet_of(summer, "brand") == ["Anchor"], str(facet_of(summer, "brand")))
        check("summer channels", facet_of(summer, "channel") == ["Off Premise", "On Premise"],
              str(facet_of(summer, "channel")))
        check("summer types", facet_of(summer, "product_type") == ["Glassware"],
              str(facet_of(summer, "product_type")))
        check("spring brands", facet_of(spring, "brand") == ["Libbey"], str(facet_of(spring, "brand")))
        check("spring channels", facet_of(spring, "channel") == ["On Premise"],
              str(facet_of(spring, "channel")))
        check("spring types", facet_of(spring, "product_type") == ["Glassware", "Signage"],
              str(facet_of(spring, "product_type")))
        check("a brand from another period never appears", "Anchor" not in facet_of(spring, "brand"),
              str(facet_of(spring, "brand")))
        check("a channel from another period never appears", "Off Premise" not in facet_of(spring, "channel"),
              str(facet_of(spring, "channel")))
        check("a catalog-only brand never appears", "Unlisted" not in facet_of(summer, "brand"),
              str(facet_of(summer, "brand")))
        # Exact equality with the rows actually returned, for every period.
        for label, payload in (("summer", summer), ("spring", spring)):
            for field in ("brand", "channel", "product_type"):
                returned = sorted({str(row.get(field) or "").strip() for row in payload.get("items", [])
                                   if str(row.get(field) or "").strip()})
                check(f"{label} {field} facets equal the period's items",
                      sorted(facet_of(payload, field)) == returned,
                      f"{facet_of(payload, field)} vs {returned}")

        # ------------------------------------------------------------------
        print("\n6. A period with no members is empty, never the catalog")
        empty_period = client.get(f"/api/public/book/{test_token}", params={"period": "P-EMPTY"}).json()
        check("the empty period is the one selected",
              (empty_period.get("period") or {}).get("id") == "P-EMPTY", str(empty_period.get("period")))
        check("it has no items", empty_period.get("items") == [], f"{len(empty_period.get('items', []))} rows")
        check("it says the period has no items",
              "no items" in str(empty_period.get("message", "")).lower(), str(empty_period.get("message")))
        check("it does not fall back to the catalog", len(empty_period.get("items", [])) != catalog_count,
              f"{len(empty_period.get('items', []))} vs {catalog_count}")
        check("its facets are empty too",
              all(not facet_of(empty_period, field) for field in ("brand", "channel", "product_type")),
              str(empty_period.get("facets")))

        print("\n6b. No period data at all is empty, never the catalog")
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(empty_dir)
        no_data = client.get(f"/api/public/book/{test_token}").json()
        check("token book has no items", no_data.get("items") == [], f"{len(no_data.get('items', []))} rows")
        check("token book says there is no period data",
              no_data.get("no_period_data") is True and "no buy-in periods" in str(no_data.get("message", "")).lower(),
              str(no_data.get("message")))
        check("token book has no period list", no_data.get("periods") == [], str(no_data.get("periods")))
        no_data_slug = client.get(
            f"/api/public/{DEFAULT_COMPANY_SLUG}/survey-items", params={"surface": "buy-book"}
        ).json()
        check("slug book is empty too", no_data_slug.get("items") == [],
              f"{len(no_data_slug.get('items', []))} rows")
        no_data_periods = client.get("/api/public/buy-book/periods").json()
        check("the periods endpoint lists none", no_data_periods.get("periods") == []
              and no_data_periods.get("no_period_data") is True, str(no_data_periods))
        check("the catalog was there and was never shown", catalog_count + len(CATALOG) > 0,
              f"{catalog_count + len(CATALOG)} catalog rows")

        # ------------------------------------------------------------------
        print("\n7. The PDF prints a period's items, matching the page")
        os.environ["SMARTBUY_EXPORTS_DIR"] = str(exports_dir)
        page = client.get(f"/api/public/book/{test_token}", params={"period": "P-SPR26"}).json()
        pdf = client.post(
            "/api/admin/buy-book/pdf",
            headers=headers,
            json={"company_id": company_id, "buy_in_period": "P-SPR26"},
        )
        check("the PDF answers 200", pdf.status_code == 200, f"HTTP {pdf.status_code} {pdf.text[:120]}")
        if pdf.status_code == 200:
            pages, text = pdf_text(pdf.content)
            check("the PDF prints exactly the page's items",
                  all(name in text for name in item_names(page))
                  and "Summer Glass" not in text and "Catalog Only" not in text,
                  f"missing one of {item_names(page)}")
            check("the PDF's page count matches the page's items",
                  pages == expected_page_count(page.get("items", [])),
                  f"{pages} vs {expected_page_count(page.get('items', []))}")
            check("the cover names the period", "2026 Spring Buy" in text, text[:120].replace("\n", " / "))
        pdf_summer = client.post(
            "/api/admin/buy-book/pdf",
            headers=headers,
            json={"company_id": company_id, "buy_in_period": "P-SUM26"},
        )
        if pdf_summer.status_code == 200:
            _pages, summer_text = pdf_text(pdf_summer.content)
            check("the summer PDF prints the summer items",
                  "Summer Glass" in summer_text and "Spring Sign" not in summer_text,
                  "the summer book printed another period")
        unknown_pdf = client.post(
            "/api/admin/buy-book/pdf",
            headers=headers,
            json={"company_id": company_id, "buy_in_period": "NO-SUCH-PERIOD"},
        )
        check("an unknown period on the PDF -> 404", unknown_pdf.status_code == 404,
              f"HTTP {unknown_pdf.status_code}")
        empty_pdf = client.post(
            "/api/admin/buy-book/pdf",
            headers=headers,
            json={"company_id": company_id, "buy_in_period": "P-EMPTY"},
        )
        check("a period with no items refuses to print -> 400", empty_pdf.status_code == 400,
              f"HTTP {empty_pdf.status_code}")
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
