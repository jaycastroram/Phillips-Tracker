"""Acceptance check for the Buy Book PDF.

Builds its own company, period and thirteen items, serves the images from a
local HTTP server so the real fetch path runs, renders through the real route
and reads the bytes back with pypdf. Everything it creates is deleted at the
start and at the end.

    python backend/scripts/check_buy_book_pdf.py
    SMARTBUY_DB_PATH=/tmp/scratch.db python backend/scripts/check_buy_book_pdf.py
    DATABASE_URL=postgresql://... python backend/scripts/check_buy_book_pdf.py

On SQLite it writes to its own throwaway database unless SMARTBUY_DB_PATH says
otherwise, so running it never touches the configured one.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import threading
import warnings
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_SCRATCH_DB = str(Path(tempfile.gettempdir()) / "smartbuy-buy-book-pdf-check.db")
_OWNS_SCRATCH = not os.environ.get("DATABASE_URL") and not os.environ.get("SMARTBUY_DB_PATH")
if _OWNS_SCRATCH:
    os.environ["SMARTBUY_DB_PATH"] = _SCRATCH_DB

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app import app  # noqa: E402
from config import IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug, hash_password  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []
SLUG = "check-pdf"
PERIOD_NAME = "Fall 2026"
ADMIN_EMAIL = "dana@check-pdf.example"
EDITOR_EMAIL = "editor@check-pdf.example"
PASSWORD = "BuyBook123"
COMPANY_TABLES = (
    "survey_items",
    "survey_periods",
    "survey_responses",
    "buy_book_layouts",
    "survey_layouts",
    "audit_logs",
    "kanban_columns",
    "sheet_statuses",
)
BRANDS = ["High Noon"] * 5 + ["LALO"] * 4 + ["Tito's"] * 4
NAMES = (
    [f"High Noon {index:02d}" for index in range(1, 6)]
    + [f"LALO {index:02d}" for index in range(1, 5)]
    + ["Tito's — Jalapeño", "Tito's 02", "Tito's 03", "Tito's 04"]
)
HEADINGS = ["Brand: High Noon", "Brand: LALO", "Brand: Tito's"]
PAGE_WIDTH, PAGE_HEIGHT = 792, 612


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def check(label: str, ok: bool, detail: object = "") -> bool:
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        print(f"      {detail!r}")
        FAILURES.append(label)
    return ok


def insert(conn, sql: str, params: tuple, table: str) -> int:
    if IS_POSTGRES:
        row = execute(conn, sql + " RETURNING id", params).fetchone()
        return int(row["id"])
    return int(execute(conn, sql, params).lastrowid)


def purge(conn) -> None:
    company = fetch_company_by_slug(conn, SLUG)
    if company is not None:
        company_id = int(company["id"])
        execute(
            conn,
            "DELETE FROM survey_period_items WHERE survey_period_id IN "
            "(SELECT id FROM survey_periods WHERE company_id = ?)",
            (company_id,),
        )
        for table in COMPANY_TABLES:
            execute(conn, f"DELETE FROM {table} WHERE company_id = ?", (company_id,))
        execute(conn, "DELETE FROM companies WHERE id = ?", (company_id,))
    execute(
        conn,
        "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email IN (?, ?))",
        (ADMIN_EMAIL, EDITOR_EMAIL),
    )
    execute(conn, "DELETE FROM users WHERE email IN (?, ?)", (ADMIN_EMAIL, EDITOR_EMAIL))


def make_png(color: tuple[int, int, int], size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, size[0] - 20, 60], fill=(255, 255, 255))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ImageHandler(BaseHTTPRequestHandler):
    """Two pictures, one HTML page and a missing logo - the real fetch cases."""

    png_a = make_png((196, 64, 48), (240, 200))
    png_b = make_png((40, 96, 168), (120, 240))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/image-a.png":
            body, kind = self.png_a, "image/png"
        elif self.path == "/image-b.png":
            body, kind = self.png_b, "image/png"
        elif self.path == "/image-html.png":
            body, kind = b"<!doctype html><html><body>Sign in</body></html>", "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401
        return


def start_image_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def render(client: TestClient, headers: dict[str, str] | None, payload: dict | None = None):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        response = client.post("/api/admin/buy-book/pdf", headers=headers or {}, json=payload or {})
    return response, list(caught)


def set_layers(client: TestClient, headers: dict[str, str], company_id: int, layers: list[dict]) -> None:
    response = client.put(
        "/api/admin/buy-book-layout",
        headers=headers,
        params={"company_id": company_id},
        json={"filter_fields": ["brand", "channel", "product_type"], "page_break_layers": layers, "items": []},
    )
    assert response.status_code == 200, response.text


def read_pdf(payload: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(payload))


def page_texts(reader: PdfReader) -> list[str]:
    return [page.extract_text() for page in reader.pages]


def fragments(page) -> list[tuple[str, float]]:
    collected: list[tuple[str, float]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            collected.append((text.strip(), float(tm[5])))

    page.extract_text(visitor_text=visitor)
    return collected


def name_positions(reader: PdfReader) -> dict[str, tuple[int, float]]:
    positions: dict[str, tuple[int, float]] = {}
    for index, page in enumerate(reader.pages):
        for text, y in fragments(page):
            for name in NAMES:
                if name in text and name not in positions:
                    positions[name] = (index, y)
    return positions


def names_on_page(text: str) -> list[str]:
    return [name for name in NAMES if name in text]


def image_xobjects(page) -> list[object]:
    resources = page.get("/Resources")
    if resources is None:
        return []
    xobjects = resources.get_object().get("/XObject")
    if xobjects is None:
        return []
    found = []
    for _name, ref in xobjects.get_object().items():
        obj = ref.get_object()
        if str(obj.get("/Subtype")) == "/Image":
            found.append(obj)
    return found


def table_rows(conn, table: str) -> list[dict]:
    rows = execute(conn, f"SELECT * FROM {table} ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def audit_rows(conn, company_id: int) -> list[dict]:
    rows = execute(
        conn,
        "SELECT * FROM audit_logs WHERE company_id = ? ORDER BY id",
        (company_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def build_fixture(conn, base_url: str) -> tuple[int, int]:
    purge(conn)
    company_id = insert(
        conn,
        """
        INSERT INTO companies (slug, name, logo_url, banner_url, netsuite_image_base, market_label, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (SLUG, "MRL Creative", "", "", "", "New York Metro", True),
        "companies",
    )
    cover = json.dumps({
        "title": "Fall 2026 Buy Book",
        "footer_note": "MRL Creative · SmartBuy",
        "logo_url": f"{base_url}/logo-missing.png",
    })
    period_id = insert(
        conn,
        """
        INSERT INTO survey_periods (company_id, name, status, opens_at, closes_at, filter_fields_json, questions_json, cover_json)
        VALUES (?, ?, 'open', ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            PERIOD_NAME,
            "2026-09-01T09:00:00+00:00",
            "2026-11-30T17:00:00+00:00",
            json.dumps(["brand", "channel", "product_type"]),
            json.dumps([
                {"id": "interest", "type": "yes_no", "label": "Interested?", "required": True, "options": ["Yes", "No"]}
            ]),
            cover,
        ),
        "survey_periods",
    )
    for index, (brand, name) in enumerate(zip(BRANDS, NAMES), start=1):
        if index == 1:
            image_url = f"{base_url}/image-a.png"
        elif index == 2:
            image_url = f"{base_url}/image-b.png"
        elif index == 3:
            image_url = f"{base_url}/image-html.png"
        else:
            image_url = ""
        program = "Holiday" if index >= 12 else "Evergreen"
        description = (
            "Tito's® Handmade Vodka, infused."
            if name.startswith("Tito's —")
            else f"Catalog description for {name}."
        )
        insert(
            conn,
            """
            INSERT INTO survey_items (
                company_id, item_name, brand, channel, product_type, key_program,
                item_description, uom, price, image_url, image_display_size, image_fit,
                is_active, in_survey, in_buy_book, survey_sort_order, buy_book_sort_order, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id, name, brand, "On Premise", "Displays", program,
                description, "12/750ml", "$24.99", image_url, "medium", "contain",
                True, 1, 1, index, index, index,
            ),
            "survey_items",
        )
    for email, name, role in (
        (ADMIN_EMAIL, "Dana Ruiz", "admin"),
        (EDITOR_EMAIL, "Ed Editor", "editor"),
    ):
        insert(
            conn,
            "INSERT INTO users (email, name, password_hash, role, is_active, company_id) VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, hash_password(PASSWORD), role, True, company_id),
            "users",
        )
    return company_id, period_id


def restore_brands(conn, company_id: int) -> None:
    for name, brand in zip(NAMES, BRANDS):
        execute(
            conn,
            "UPDATE survey_items SET brand = ? WHERE company_id = ? AND item_name = ?",
            (brand, company_id, name),
        )


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()
    server, base_url = start_image_server()
    client = TestClient(app)
    db_file = os.environ.get("SMARTBUY_DB_PATH", "")

    with connect() as conn:
        company_id, period_id = build_fixture(conn, base_url)
    headers = login(client, ADMIN_EMAIL, PASSWORD)
    editor_headers = login(client, EDITOR_EMAIL, PASSWORD)

    try:
        print("\n1. The PDF itself")
        set_layers(client, headers, company_id, [])
        response, caught = render(client, headers)
        check("200 with application/pdf", response.status_code == 200
              and response.headers.get("content-type", "").startswith("application/pdf"),
              (response.status_code, response.headers.get("content-type")))
        payload = response.content
        check("%PDF- header", payload.startswith(b"%PDF-"), payload[:12])
        check("no warnings raised", not caught, [str(item.message) for item in caught])
        check("Cache-Control: no-store", response.headers.get("cache-control") == "no-store",
              response.headers.get("cache-control"))
        filename = response.headers.get("content-disposition", "")
        check(
            "attachment filename",
            re.fullmatch(
                r'attachment; filename="buy-book-check-pdf-fall-2026-\d{4}-\d{2}-\d{2}\.pdf"', filename
            ) is not None,
            filename,
        )

        reader = read_pdf(payload)
        check("4 pages", len(reader.pages) == 4, len(reader.pages))
        sizes = [(round(float(page.mediabox.width), 2), round(float(page.mediabox.height), 2)) for page in reader.pages]
        check("every page is 792 x 612", all(
            abs(width - PAGE_WIDTH) <= 0.5 and abs(height - PAGE_HEIGHT) <= 0.5 for width, height in sizes
        ), sizes)

        print("\n2. No layers: 6 to a page, in catalog order")
        texts = page_texts(reader)
        check("page 2 holds six names", len(names_on_page(texts[1])) == 6, names_on_page(texts[1]))
        check("page 4 holds one name", len(names_on_page(texts[3])) == 1, names_on_page(texts[3]))
        check("the cover holds no item", names_on_page(texts[0]) == [], names_on_page(texts[0]))

        print("\n3. One brand layer, breaking: a page per brand")
        set_layers(client, headers, company_id, [{"field": "brand", "new_page": True}])
        response, _ = render(client, headers)
        reader = read_pdf(response.content)
        texts = page_texts(reader)
        check("4 pages", len(reader.pages) == 4, len(reader.pages))
        for heading in HEADINGS:
            pages_with = [index for index, text in enumerate(texts[1:], start=1) if heading in text]
            check(f"{heading} on exactly one page", len(pages_with) == 1, pages_with)

        print("\n4. One brand: the same heading repeats on every continuation page")
        with connect() as conn:
            execute(conn, "UPDATE survey_items SET brand = ? WHERE company_id = ?", ("High Noon", company_id))
        response, _ = render(client, headers)
        reader = read_pdf(response.content)
        texts = page_texts(reader)
        check("4 pages", len(reader.pages) == 4, len(reader.pages))
        repeated = [heading_present for heading_present in (
            "Brand: High Noon" in text for text in texts[1:]
        )]
        check("the heading repeats on pages 2, 3 and 4", repeated == [True, True, True], repeated)
        with connect() as conn:
            restore_brands(conn, company_id)

        print("\n5. Brand (no break) then Program (break)")
        set_layers(client, headers, company_id, [
            {"field": "brand", "new_page": False},
            {"field": "key_program", "new_page": True},
        ])
        response, _ = render(client, headers)
        reader = read_pdf(response.content)
        texts = page_texts(reader)
        tito_pages = [text for text in texts[1:] if "Brand: Tito's" in text]
        check("a Tito's page exists", len(tito_pages) >= 1, len(tito_pages))
        first_tito = tito_pages[0]
        check("it names brand and program", "Brand: Tito's" in first_tito and "Program: Evergreen" in first_tito,
              first_tito[:160])
        order = [first_tito.find("Brand: Tito's"), first_tito.find("Program: Evergreen"), first_tito.find("Tito's — Jalapeño")]
        check("order brand -> program -> item", order[0] < order[1] < order[2] and order[0] >= 0, order)

        print("\n6. Name order is strictly increasing by page, then descending y")
        set_layers(client, headers, company_id, [])
        response, _ = render(client, headers)
        reader = read_pdf(response.content)
        positions = name_positions(reader)
        check("every name was found", len(positions) == len(NAMES), sorted(set(NAMES) - set(positions)))
        ordered = [name for name, _ in sorted(positions.items(), key=lambda pair: (pair[1][0], -pair[1][1]))]
        check("names come out in catalog order", ordered == NAMES, ordered)

        print("\n7. Images: two pictures, initials everywhere else")
        item_pages = reader.pages[1:]
        images = [obj for page in item_pages for obj in image_xobjects(page)]
        check("exactly 2 image XObjects on item pages", len(images) == 2, len(images))
        check("the HTML and empty-URL items show initials", "HI" in page_texts(reader)[1],
              page_texts(reader)[1][:160])
        check("the file still parses", len(reader.pages) == 4, len(reader.pages))

        print("\n8. The cover")
        cover_text = page_texts(reader)[0]
        today = datetime.now(UTC)
        date_label = f"{today.day} {('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[today.month - 1]} {today.year}"
        for expected in ("MRL Creative", "New York Metro", "Fall 2026 Buy Book", "Dana Ruiz", date_label,
                         "MRL Creative · SmartBuy"):
            check(f"cover text contains {expected!r}", expected in cover_text, cover_text[:400])
        check("a broken logo_url still renders the book", reader.pages[0] is not None, "")

        print("\n9. Unicode round-trips")
        jalapeno_page = next(text for text in page_texts(reader) if "Jalapeño" in text)
        check("Tito's — Jalapeño extracts exactly", "Tito's — Jalapeño" in jalapeno_page, jalapeno_page[:200])
        check("® extracts exactly", "Tito's® Handmade Vodka" in jalapeno_page, jalapeno_page[:200])

        print("\n9b. A period's cover is stored and beats the company default")
        patched = client.patch(
            f"/api/admin/survey-periods/{period_id}",
            headers=headers,
            params={"company_id": company_id},
            json={"cover": {"market": "Chicago Metro", "buyer": "Dana Ruiz"}},
        )
        check("PATCH accepts a cover", patched.status_code == 200, patched.status_code)
        returned = patched.json()["period"]["cover"]
        check(
            "the period hands the cover back",
            returned.get("market") == "Chicago Metro" and returned.get("buyer") == "Dana Ruiz"
            and returned.get("title") == "Fall 2026 Buy Book",
            returned,
        )
        response, _ = render(client, headers)
        stored_cover = page_texts(read_pdf(response.content))[0]
        check("the render uses the stored cover", "Chicago Metro" in stored_cover and "PREPARED FOR" in stored_cover,
              stored_cover[:400])
        cleared = client.patch(
            f"/api/admin/survey-periods/{period_id}",
            headers=headers,
            params={"company_id": company_id},
            json={"cover": {"market": ""}},
        )
        check("a blank field is stored blank", cleared.json()["period"]["cover"].get("market") == "",
              cleared.json()["period"]["cover"])
        response, _ = render(client, headers)
        check("and the render falls back to the company default",
              "New York Metro" in page_texts(read_pdf(response.content))[0], "")

        print("\n10. Refusals")
        empty, _ = render(client, headers, {"item_ids": []})
        check("empty selection is 400", empty.status_code == 400, empty.status_code)
        check("with the right message", empty.json().get("detail") == "Nothing selected", empty.text[:200])
        unknown_items, _ = render(client, headers, {"item_ids": [999999]})
        check("all-unknown selection is 400", unknown_items.status_code == 400, unknown_items.status_code)
        anonymous, _ = render(client, None)
        check("anonymous is 401", anonymous.status_code == 401, anonymous.status_code)
        editor, _ = render(client, editor_headers)
        check("an editor is 403", editor.status_code == 403, editor.status_code)
        unknown_period, _ = render(client, headers, {"period_id": 999999})
        check("unknown period is 404", unknown_period.status_code == 404, unknown_period.status_code)
        unknown_company, _ = render(client, headers, {"company_id": 999999})
        check("unknown company is 404", unknown_company.status_code == 404, unknown_company.status_code)

        print("\n11. Two renders in a row agree")
        first_response, _ = render(client, headers)
        second_response, _ = render(client, headers)
        first_reader = read_pdf(first_response.content)
        second_reader = read_pdf(second_response.content)
        first_pages = [names_on_page(text) for text in page_texts(first_reader)]
        second_pages = [names_on_page(text) for text in page_texts(second_reader)]
        check("same page count", len(first_reader.pages) == len(second_reader.pages),
              (len(first_reader.pages), len(second_reader.pages)))
        check("same names in the same order per page", first_pages == second_pages, (first_pages, second_pages))

        print("\n12. A render writes one audit row and touches nothing else")
        tables = ("survey_items", "survey_periods", "survey_period_items", "companies",
                  "buy_book_layouts", "survey_layouts", "users", "kanban_columns", "sheet_statuses")
        with connect() as conn:
            before = {table: table_rows(conn, table) for table in tables}
            audits_before = audit_rows(conn, company_id)
        render(client, headers)
        with connect() as conn:
            after = {table: table_rows(conn, table) for table in tables}
            audits_after = audit_rows(conn, company_id)
        changed = [table for table in tables if before[table] != after[table]]
        check("no row outside the audit log changed", changed == [], changed)
        check("exactly one new audit row", len(audits_after) == len(audits_before) + 1,
              (len(audits_before), len(audits_after)))
        check("it is a buy_book_pdf row", audits_after[-1]["action"] == "buy_book_pdf", audits_after[-1])
    finally:
        with connect() as conn:
            purge(conn)
        server.shutdown()
        server.server_close()
        if _OWNS_SCRATCH and db_file:
            try:
                Path(db_file).unlink(missing_ok=True)
            except OSError:
                pass

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
