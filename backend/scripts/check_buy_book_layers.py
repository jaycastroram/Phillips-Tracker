"""Acceptance check for the Buy Book's layered page breaks.

Runs against the app's configured database and a temporary client. It writes a
small set of catalog rows with known facets, so the expected grouping is
unambiguous, and removes them again at the end.

    python backend/scripts/check_buy_book_layers.py
    python backend/scripts/check_buy_book_layers.py --company phillips --keep

What it proves:

1. a layout with nothing saved behaves as one Brand layer that breaks pages
2. Brand -> Key Program, both ticked, gives one page per brand/program pair
3. unticking New page on the second layer removes the extra breaks
4. layer values keep the catalog's order, with blank facets sorted last
5. a blank facet forms its own group and still breaks when its layer breaks
6. the stack round-trips through PUT and GET
7. a layout saved before layers existed (group_by only) reads as one layer
8. the public Buy Book carries the derived breaks and nested headings
9. the manual per-item page break no longer affects the public Buy Book
10. spellings of one value ("Tito's" / "TITO'S ") stay in one group
11. a repeated facet and a fifth layer are refused with 400
12. a layout saved with no grouping stays ungrouped
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_COMPANY_SLUG  # noqa: E402
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []
MARK = "zz-layer-check"


def check(label: str, ok: bool, detail: Any = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILURES.append(label)


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if response.status_code != 200:
        raise SystemExit(f"login failed: {response.status_code} {response.text[:200]}")
    return {"Authorization": f"Bearer {response.json()['token']}"}


ITEMS = [
    # brand, channel, key_program, product_type, name
    ("Alpha", "On Premise", "Spring", "Displays", "A1"),
    ("Alpha", "On Premise", "Spring", "Displays", "A2"),
    ("Alpha", "Off Premise", "Fall", "Print", "A3"),
    ("Beta", "On Premise", "", "Print", "B1"),
    ("Beta", "Off Premise", "Fall", "Wearables", "B2"),
    ("", "On Premise", "Spring", "Print", "Unbranded"),
]


def make_items(conn: Any, company_id: int) -> list[int]:
    ids = []
    for index, (brand, channel, program, product_type, name) in enumerate(ITEMS):
        cursor = execute(
            conn,
            """
            INSERT INTO survey_items (
                company_id, item_name, brand, channel, key_program, product_type,
                item_description, uom, price, image_url, is_active, in_survey, in_buy_book,
                survey_sort_order, buy_book_sort_order, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', 1, 1, 1, ?, ?, ?)
            """,
            (company_id, f"{MARK} {name}", brand, channel, program, product_type, index, index, index),
        )
        ids.append(int(cursor.lastrowid))
    return ids


def pages_of(items: list[dict[str, Any]]) -> list[list[str]]:
    pages: list[list[str]] = []
    current: list[str] = []
    for item in items:
        if item["page_break_before"] and current:
            pages.append(current)
            current = []
        current.append(item["item_name"].replace(f"{MARK} ", ""))
    if current:
        pages.append(current)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default=DEFAULT_COMPANY_SLUG)
    parser.add_argument("--keep", action="store_true", help="leave the test rows and layout in place")
    args = parser.parse_args()

    init_db()
    client = TestClient(app)
    headers = login(client)

    with connect() as conn:
        company = fetch_company_by_slug(conn, args.company)
        if company is None:
            raise SystemExit(f"No company '{args.company}'")
        company_id = int(company["id"])
        original_layout = execute(
            conn, "SELECT * FROM buy_book_layouts WHERE company_id = ?", (company_id,)
        ).fetchone()
        item_ids = make_items(conn, company_id)

    params = {"company_id": company_id}
    try:
        items = [f"{MARK} {name}" for *_, name in ITEMS]

        print("\n1. Default: nothing saved is one Brand layer that breaks")
        client.put("/api/admin/buy-book-layout", headers=headers, params=params,
                   json={"filter_fields": ["brand", "channel", "product_type"], "page_break_layers": [], "items": []})
        with connect() as conn:
            execute(conn, "UPDATE buy_book_layouts SET page_break_layers_json = NULL, group_by = 'brand' WHERE company_id = ?", (company_id,))
        layout = client.get("/api/admin/buy-book-layout", headers=headers, params=params).json()
        check("layers read as one Brand layer", layout["page_break_layers"] == [{"field": "brand", "new_page": True}], layout["page_break_layers"])

        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        mine = [item for item in book["items"] if item["item_name"] in items]
        check("public payload carries the stack", book.get("page_break_layers") == [{"field": "brand", "new_page": True}], book.get("page_break_layers"))
        check("a page per brand", pages_of(mine) == [["A1", "A2", "A3"], ["B1", "B2"], ["Unbranded"]], pages_of(mine))

        print("\n2. Brand -> Key Program, both breaking")
        client.put("/api/admin/buy-book-layout", headers=headers, params=params, json={
            "filter_fields": ["brand", "channel", "product_type"],
            "page_break_layers": [{"field": "brand", "new_page": True}, {"field": "key_program", "new_page": True}],
            "items": [],
        })
        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        mine = [item for item in book["items"] if item["item_name"] in items]
        # Within a brand the program values keep the catalog's order - Spring
        # before Fall, because A1 comes first - and a blank sorts last, so
        # Beta is B2 then B1.
        expected = [["A1", "A2"], ["A3"], ["B2"], ["B1"], ["Unbranded"]]
        check("one page per brand/program pair", pages_of(mine) == expected, pages_of(mine))
        first = next(item for item in mine if item["item_name"].endswith("A3"))
        check("a changed child layer emits its heading", [h["label"] for h in first["group_headers"]] == ["Alpha", "Fall"], first["group_headers"])

        print("\n3. Untick New page on the second layer")
        client.put("/api/admin/buy-book-layout", headers=headers, params=params, json={
            "filter_fields": ["brand", "channel", "product_type"],
            "page_break_layers": [{"field": "brand", "new_page": True}, {"field": "key_program", "new_page": False}],
            "items": [],
        })
        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        mine = [item for item in book["items"] if item["item_name"] in items]
        check("no extra break inside a brand", pages_of(mine) == [["A1", "A2", "A3"], ["B2", "B1"], ["Unbranded"]], pages_of(mine))
        check("the unticked layer still sorts", [item["item_name"].replace(f"{MARK} ", "") for item in mine][:3] == ["A1", "A2", "A3"], [item["item_name"] for item in mine][:3])

        print("\n4. Stack round-trips and legacy layouts still read")
        saved = client.get("/api/admin/buy-book-layout", headers=headers, params=params).json()
        check("PUT then GET returns the same stack", saved["page_break_layers"] == [{"field": "brand", "new_page": True}, {"field": "key_program", "new_page": False}], saved["page_break_layers"])
        with connect() as conn:
            execute(conn, "UPDATE buy_book_layouts SET page_break_layers_json = NULL, group_by = 'product_type' WHERE company_id = ?", (company_id,))
        legacy = client.get("/api/admin/buy-book-layout", headers=headers, params=params).json()
        check("old single facet loads as one layer", legacy["page_break_layers"] == [{"field": "product_type", "new_page": True}], legacy["page_break_layers"])

        print("\n5. The manual per-item break no longer drives the Buy Book")
        with connect() as conn:
            execute(conn, "UPDATE survey_items SET buy_book_page_break_before = 1 WHERE id = ?", (item_ids[1],))
            execute(conn, "UPDATE buy_book_layouts SET page_break_layers_json = ?, group_by = 'brand' WHERE company_id = ?", (json.dumps([{"field": "brand", "new_page": False}]), company_id))
        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        mine = [item for item in book["items"] if item["item_name"] in items]
        check("a stored manual flag does not break a page", len(pages_of(mine)) == 1, pages_of(mine))

        print("\n6. Two spellings of one value are one group")
        client.put("/api/admin/buy-book-layout", headers=headers, params=params, json={
            "page_break_layers": [{"field": "brand", "new_page": True}, {"field": "key_program", "new_page": True}],
        })
        with connect() as conn:
            extra = []
            for brand, program, name in [("ALPHA ", "Spring", "A4"), ("alpha", "Fall", "A5")]:
                cursor = execute(conn,
                    """
                    INSERT INTO survey_items (
                        company_id, item_name, brand, channel, key_program, product_type,
                        item_description, uom, price, image_url, is_active, in_survey, in_buy_book,
                        survey_sort_order, buy_book_sort_order, sort_order
                    ) VALUES (?, ?, ?, 'On Premise', ?, 'Print', '', '', '', '', 1, 1, 1, ?, ?, ?)
                    """,
                    (company_id, f"{MARK} {name}", brand, program, 90, 90, 90))
                extra.append(int(cursor.lastrowid))
        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        mine = [item for item in book["items"] if item["item_name"] in items or item["item_name"].endswith(("A4", "A5"))]
        pages = pages_of(mine)
        spring_page = next((page for page in pages if "A1" in page), [])
        fall_page = next((page for page in pages if "A3" in page), [])
        check("a case/spacing variant adds no page", sorted(spring_page) == ["A1", "A2", "A4"] and sorted(fall_page) == ["A3", "A5"], pages)
        labels = {item["group_label"] for item in mine}
        check("the group keeps the first spelling", labels == {"Alpha", "Beta", "Ungrouped"}, labels)
        with connect() as conn:
            for item_id in extra:
                execute(conn, "DELETE FROM survey_items WHERE id = ?", (item_id,))

        print("\n7. A repeated facet and a fifth layer are refused")
        repeated = client.put("/api/admin/buy-book-layout", headers=headers, params=params, json={
            "page_break_layers": [{"field": "brand", "new_page": True}, {"field": "brand", "new_page": False}],
        })
        check("a repeated facet is a 400 naming the owner", repeated.status_code == 400 and "layer 1" in repeated.text, repeated.text[:120])
        too_many = client.put("/api/admin/buy-book-layout", headers=headers, params=params, json={
            "page_break_layers": [
                {"field": "brand", "new_page": True}, {"field": "channel", "new_page": True},
                {"field": "key_program", "new_page": True}, {"field": "product_type", "new_page": True},
                {"field": "brand", "new_page": True},
            ],
        })
        check("a fifth layer is a 400", too_many.status_code == 400, too_many.text[:120])

        print("\n8. A layout saved with no grouping stays ungrouped")
        with connect() as conn:
            execute(conn, "UPDATE buy_book_layouts SET page_break_layers_json = NULL, group_by = 'none' WHERE company_id = ?", (company_id,))
        ungrouped = client.get("/api/admin/buy-book-layout", headers=headers, params=params).json()
        check("no grouping loads as an empty stack", ungrouped["page_break_layers"] == [], ungrouped["page_break_layers"])
        init_db()
        book = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "buy-book"}).json()
        check(
            "an ungrouped book starts no pages",
            bool(book["items"]) and not any(i["page_break_before"] for i in book["items"]) and not any(i["group_headers"] for i in book["items"]),
            [(i["item_name"], i["page_break_before"]) for i in book["items"] if i["page_break_before"]],
        )
        print("\n9. Two migrations keep the stored stack")
        with connect() as conn:
            execute(conn, "UPDATE buy_book_layouts SET page_break_layers_json = ? WHERE company_id = ?",
                    (json.dumps([{"field": "brand", "new_page": True}, {"field": "channel", "new_page": False}]), company_id))
        init_db()
        init_db()
        kept = client.get("/api/admin/buy-book-layout", headers=headers, params=params).json()
        check("existing stack survives migrations", kept["page_break_layers"] == [{"field": "brand", "new_page": True}, {"field": "channel", "new_page": False}], kept["page_break_layers"])
    finally:
        if not args.keep:
            with connect() as conn:
                for item_id in item_ids:
                    execute(conn, "DELETE FROM survey_items WHERE id = ?", (item_id,))
                if original_layout is None:
                    execute(conn, "DELETE FROM buy_book_layouts WHERE company_id = ?", (company_id,))
                else:
                    execute(
                        conn,
                        "UPDATE buy_book_layouts SET filter_fields_json = ?, group_by = ?, page_break_layers_json = ? WHERE company_id = ?",
                        (original_layout["filter_fields_json"], original_layout["group_by"], original_layout["page_break_layers_json"], company_id),
                    )
            print("\n(test rows and the saved layout restored)")

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES)))
    sys.exit(0 if not FAILURES else 1)


if __name__ == "__main__":
    main()
