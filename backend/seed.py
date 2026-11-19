from __future__ import annotations

import json
from typing import Any

from config import DEFAULT_KANBAN_COLUMNS, DEFAULT_COMPANY_SLUG, SEED_ITEMS_PATH, SHEETS
from database import (
    IS_POSTGRES,
    ensure_buy_book_layout,
    ensure_legacy_survey_period,
    ensure_survey_layout,
    execute,
    fetch_company_by_slug,
    hash_password,
)

DEFAULT_SURVEY_ITEMS = [
    {
        "item_name": "Tito's Bar Mat",
        "brand": "Tito's",
        "channel": "On Premise",
        "product_type": "Barware",
        "key_program": "Evergreen",
        "item_description": "Custom 16\" x 16\" black PVC service bar mat featuring custom molded oval nibs and Tito's branding.",
        "uom": "1/EA",
        "price": "$7.45",
        "image_url": "https://cincoro.sb.myfsionline.com/images/4006_10001_1.png",
        "sort_order": 1,
    },
    {
        "item_name": "Tito's Round Bottle Presenter",
        "brand": "Tito's",
        "channel": "On Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "Illuminated metal bottle presenter featuring laser-cut Tito's branding, integrated LED lighting, and illuminated side panels.",
        "uom": "1/EA",
        "price": "$332.04",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10002_1.png",
        "sort_order": 2,
    },
    {
        "item_name": "Tito's Crop Top",
        "brand": "Tito's",
        "channel": "Off Premise",
        "product_type": "Wearables",
        "key_program": "Evergreen",
        "item_description": "Black athletic tank made from a nylon-spandex performance blend, featuring a Tito's logo chest imprint.",
        "uom": "1/EA",
        "price": "$19.75",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10003_1.png",
        "sort_order": 3,
    },
    {
        "item_name": "Tito's Short Denim Romper",
        "brand": "Tito's",
        "channel": "Off Premise",
        "product_type": "Wearables",
        "key_program": "Evergreen",
        "item_description": "Custom cream denim romper featuring a Tito's logo sleeve imprint.",
        "uom": "1/EA",
        "price": "$36.56",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10004_1.png",
        "sort_order": 4,
    },
    {
        "item_name": "Tito's Doghouse Display",
        "brand": "Tito's",
        "channel": "Off Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "Custom wood doghouse display with a functional chalkboard roof, Tito's branded graphics, and a built-in center divider.",
        "uom": "1/EA",
        "price": "$210.82",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10005_1.png",
        "sort_order": 5,
    },
    {
        "item_name": "Tito's Acrylic Top Garnish Caddy",
        "brand": "Tito's",
        "channel": "On Premise",
        "product_type": "Barware",
        "key_program": "Evergreen",
        "item_description": "Custom garnish caddy with Tito's branding, removable food-safe garnish trays, a clear lid, and copper accent detailing.",
        "uom": "1/EA",
        "price": "$41.72",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10006_1.png",
        "sort_order": 6,
    },
    {
        "item_name": "Tito's Napkin Caddy",
        "brand": "Tito's",
        "channel": "On Premise",
        "product_type": "Barware",
        "key_program": "Evergreen",
        "item_description": "Wood napkin caddy featuring Tito's branding, copper accent detailing, and a protective clear-coated finish.",
        "uom": "1/EA",
        "price": "$19.59",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10007_1.png",
        "sort_order": 7,
    },
    {
        "item_name": "Tito's Swimsuit",
        "brand": "Tito's",
        "channel": "Off Premise",
        "product_type": "Wearables",
        "key_program": "Evergreen",
        "item_description": "Custom orange swimsuit featuring Tito's branded graphics and a white belt.",
        "uom": "1/EA",
        "price": "$29.76",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_10008_1.png",
        "sort_order": 8,
    },
    {
        "item_name": "LALO Sales Booklet",
        "brand": "LALO",
        "channel": "Off Premise",
        "product_type": "Print",
        "key_program": "Evergreen",
        "item_description": "Folded Sales Book featuring imprint on all pages. Updated Lalo 2026 legal line.",
        "uom": "25/PK",
        "price": "$19.75",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_53553_1.jpg?fcts=20260519085204",
        "sort_order": 9,
    },
    {
        "item_name": "LALO Hat",
        "brand": "LALO",
        "channel": "Giveaways",
        "product_type": "Wearables",
        "key_program": "Sweeps",
        "item_description": "Custom Lalo branded hat with 3D embroidery. Includes tear away white labels.",
        "uom": "1/EA",
        "price": "$6.59",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_60045_1.png?fcts=20260519084157",
        "sort_order": 10,
    },
    {
        "item_name": "LALO Rail Mat",
        "brand": "LALO",
        "channel": "On Premise",
        "product_type": "Barware",
        "key_program": "Evergreen",
        "item_description": "PVC Rail mats with molded LALO Bottle nubs, 2026 Updated Art.",
        "uom": "1/EA",
        "price": "$6.02",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_60047-RAIL_3_1.png?fcts=20260519111137",
        "sort_order": 11,
    },
    {
        "item_name": "LALO Bar Mat",
        "brand": "LALO",
        "channel": "On Premise",
        "product_type": "Barware",
        "key_program": "Evergreen",
        "item_description": "PVC Square mats with molded LALO Bottle nubs, 2026 Updated Art.",
        "uom": "1/EA",
        "price": "$10.55",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_60047-SQUARE_1.png?fcts=20260519081711",
        "sort_order": 12,
    },
    {
        "item_name": "LALO Bottle Pin",
        "brand": "LALO",
        "channel": "Giveaways",
        "product_type": "Promo Items",
        "key_program": "Sweeps",
        "item_description": "Custom Lalo bottle pins.",
        "uom": "1/EA",
        "price": "$0.94",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_60221_1.png?fcts=20260519084911",
        "sort_order": 13,
    },
    {
        "item_name": "LALO TW&M OND Gift Box Display",
        "brand": "LALO",
        "channel": "Off Premise",
        "product_type": "Displays",
        "key_program": "Holiday",
        "item_description": "Holiday gift box display featuring a Lalo Tequila bottle and finished with a metallic ribbon.",
        "uom": "1/EA",
        "price": "$469.60",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_60664_1.jpg?fcts=20260518104631",
        "sort_order": 14,
    },
    {
        "item_name": "LALO Shelf Talker - 25/PK",
        "brand": "LALO",
        "channel": "Off Premise",
        "product_type": "Print",
        "key_program": "Evergreen",
        "item_description": "Shelf talker featuring CMYK printing on one side and scored. Includes double sided tape on the back.",
        "uom": "25/PK",
        "price": "$7.17",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_61123_1.jpg",
        "sort_order": 15,
    },
    {
        "item_name": "LALO A Frame Chalkboard Sign",
        "brand": "LALO",
        "channel": "Off Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "Lalo bottle silhouette chalkboard made from MDF.",
        "uom": "1/EA",
        "price": "$141.80",
        "image_url": "https://demo.mrlsmartbuy.com/images/4006_61291_1.jpg",
        "sort_order": 16,
    },
    {
        "item_name": "LALO 5-Case Display (Lifestyle Graphics)",
        "brand": "LALO",
        "channel": "On Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "The LALO 5-Case Display features an 18-gauge metallic gold metal rack with styrene graphics. Holds 5 cases of 750 mL bottles.",
        "uom": "",
        "price": "$265.58",
        "image_url": "https://phillips.pdccatalog.com/images/3185_35838_1.png",
        "sort_order": 17,
    },
    {
        "item_name": "LALO 5 Case (4 Shelf) Metal Rack",
        "brand": "LALO",
        "channel": "On Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "Powder-coated tubular metal display rack, ~72\" H x 13.5\" W x 7.75\" D. Ships fully assembled with double-sided printed header and two side graphic panels.",
        "uom": "",
        "price": "$395.68",
        "image_url": "https://demo.mrlsmartbuy.com/images/3185_35838-BlueGraphics_1.png",
        "sort_order": 18,
    },
    {
        "item_name": "LALO Wow Display",
        "brand": "LALO",
        "channel": "On Premise",
        "product_type": "Displays",
        "key_program": "Evergreen",
        "item_description": "The LALO Wow Display is a corrugated display, 64\" W x 12\" D x 60\" H, designed for eye-catching retail merchandising.",
        "uom": "",
        "price": "$361.45",
        "image_url": "https://phillips.pdccatalog.com/images/3185_38404_1.png",
        "sort_order": 19,
    },
]


def seed_tracker_items_if_empty(conn: Any, company_id: int) -> None:
    if not SEED_ITEMS_PATH.exists():
        return
    row_count = execute(
        conn,
        "SELECT COUNT(*) AS row_count FROM tracker_items WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row_count["row_count"] > 0:
        return

    seed_items = json.loads(SEED_ITEMS_PATH.read_text(encoding="utf-8"))
    for item in seed_items:
        execute(
            conn,
            """
            INSERT INTO tracker_items (
                company_id, sheet, source_row, sort_order, date_or_buy, current_status,
                visual_reference, brand, program_name, item_name, qty,
                important_notes, mrl_order_number, estimated_ship_date,
                estimated_ihd, tracking, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                item.get("sheet", "ad-hoc"),
                item.get("source_row"),
                item.get("sort_order", 0),
                item.get("date_or_buy", ""),
                item.get("current_status", ""),
                item.get("visual_reference", ""),
                item.get("brand", ""),
                item.get("program_name", ""),
                item.get("item_name", ""),
                item.get("qty", ""),
                item.get("important_notes", ""),
                item.get("mrl_order_number", ""),
                item.get("estimated_ship_date", ""),
                item.get("estimated_ihd", ""),
                item.get("tracking", ""),
                item.get("extra_json", "{}"),
            ),
        )


def sync_seeded_tracker_item_catalog(conn: Any, company_id: int) -> None:
    if not SEED_ITEMS_PATH.exists():
        return
    seed_items = json.loads(SEED_ITEMS_PATH.read_text(encoding="utf-8"))
    for item in seed_items:
        source_row = item.get("source_row")
        if source_row is None:
            continue
        execute(
            conn,
            """
            UPDATE tracker_items
            SET visual_reference = ?, brand = ?, program_name = ?, item_name = ?, extra_json = ?
            WHERE company_id = ? AND sheet = ? AND source_row = ?
            """,
            (
                item.get("visual_reference", ""),
                item.get("brand", ""),
                item.get("program_name", ""),
                item.get("item_name", ""),
                item.get("extra_json", "{}"),
                company_id,
                item.get("sheet", "ad-hoc"),
                source_row,
            ),
        )


def seed_kanban_columns_if_empty(conn: Any, company_id: int) -> None:
    row_count = execute(
        conn,
        "SELECT COUNT(*) AS row_count FROM kanban_columns WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row_count["row_count"] > 0:
        return

    for column in DEFAULT_KANBAN_COLUMNS:
        execute(
            conn,
            """
            INSERT INTO kanban_columns (company_id, title, statuses_json, sort_order, is_visible)
            VALUES (?, ?, ?, ?, ?)
            """,
            (company_id, column["title"], json.dumps(column["statuses"]), column["sort_order"], True),
        )


def seed_sheet_statuses_if_empty(conn: Any, company_id: int) -> None:
    row_count = execute(
        conn,
        "SELECT COUNT(*) AS row_count FROM sheet_statuses WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row_count["row_count"] > 0:
        return

    for sheet_key, sheet_config in SHEETS.items():
        for sort_order, status_label in enumerate(sheet_config["statuses"], start=1):
            execute(
                conn,
                """
                INSERT INTO sheet_statuses (company_id, sheet, status, sort_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (company_id, sheet, status) DO NOTHING
                """,
                (company_id, sheet_key, status_label, sort_order),
            )


def sync_default_survey_items(conn: Any, company_id: int) -> None:
    """Top up the demo catalog on boot — and stay out of the real one.

    These twenty hand-written rows exist so a fresh install has something to
    show. Any row carrying a NetSuite id belongs to the item master or to the
    tracker link, not to this seed: the seed neither rewrites it nor retires it.
    Before that rule, the blanket "deactivate everything whose name is not one
    of mine" ran on every boot and would have deactivated an entire synced
    catalog the first time the app restarted.
    """
    active_item_names = []

    for item in DEFAULT_SURVEY_ITEMS:
        active_item_names.append(item["item_name"].lower())
        existing_item = execute(
            conn,
            """
            SELECT id FROM survey_items
            WHERE company_id = ? AND lower(item_name) = lower(?)
              AND COALESCE(netsuite_item_id, '') = ''
            ORDER BY id ASC LIMIT 1
            """,
            (company_id, item["item_name"]),
        ).fetchone()

        values = (
            company_id,
            item["item_name"],
            item["brand"],
            item["channel"],
            item["product_type"],
            item["key_program"],
            item["item_description"],
            item["uom"],
            item["price"],
            item["image_url"],
            item["sort_order"],
            True,
        )
        if existing_item is None:
            execute(
                conn,
                """
                INSERT INTO survey_items (
                    company_id, item_name, brand, channel, product_type, key_program,
                    item_description, uom, price, image_url, sort_order, is_active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        else:
            execute(
                conn,
                """
                UPDATE survey_items
                SET company_id = ?, item_name = ?, brand = ?, channel = ?, product_type = ?, key_program = ?,
                    item_description = ?, uom = ?, price = ?, image_url = ?, sort_order = ?, is_active = ?
                WHERE id = ?
                """,
                (*values, existing_item["id"]),
            )

    placeholders = ", ".join("?" for _ in active_item_names)
    execute(
        conn,
        f"""
        UPDATE survey_items
        SET is_active = ?
        WHERE company_id = ? AND lower(item_name) NOT IN ({placeholders})
          AND COALESCE(netsuite_item_id, '') = ''
        """,
        (False, company_id, *active_item_names),
    )


def backfill_survey_sort_order(conn: Any) -> None:
    execute(
        conn,
        """
        UPDATE survey_items
        SET survey_sort_order = sort_order
        WHERE survey_sort_order = 0 AND sort_order <> 0
        """,
    )


DEMO_COMPANY_SEEDS = [
    {
        "slug": "demo-spirits",
        "name": "Demo Spirits Co",
        "logo_url": "https://placehold.co/220x64/1e3a5f/ffffff?text=Demo+Spirits",
        "banner_url": "https://placehold.co/960x120/1e3a5f/ffffff?text=Demo+Spirits+Tracker",
        "user": {
            "email": "demo@example.com",
            "name": "Demo Spirits User",
            "password": "Demo1234",
            "role": "editor",
        },
        "survey_items": [
            {
                "item_name": "Harbor Gin Bar Mat",
                "brand": "Harbor Gin",
                "channel": "On Premise",
                "product_type": "Barware",
                "key_program": "Launch",
                "item_description": "Navy PVC bar mat with Harbor Gin crest and non-slip nibs.",
                "uom": "1/EA",
                "price": "$8.25",
                "image_url": "https://placehold.co/400x400/1e3a5f/ffffff?text=Harbor+Mat",
                "sort_order": 1,
                "in_survey": True,
                "in_buy_book": True,
            },
            {
                "item_name": "Harbor Gin Neon Sign",
                "brand": "Harbor Gin",
                "channel": "On Premise",
                "product_type": "Displays",
                "key_program": "Launch",
                "item_description": "LED neon bottle silhouette for back-bar placement.",
                "uom": "1/EA",
                "price": "$145.00",
                "image_url": "https://placehold.co/400x400/0f2744/ffffff?text=Neon+Sign",
                "sort_order": 2,
                "in_survey": True,
                "in_buy_book": True,
            },
            {
                "item_name": "Coastal Rum Cap",
                "brand": "Coastal Rum",
                "channel": "Off Premise",
                "product_type": "Wearables",
                "key_program": "Summer",
                "item_description": "Structured cotton cap with embroidered Coastal Rum mark.",
                "uom": "1/EA",
                "price": "$14.50",
                "image_url": "https://placehold.co/400x400/245b7a/ffffff?text=Rum+Cap",
                "sort_order": 3,
                "in_survey": True,
                "in_buy_book": False,
            },
            {
                "item_name": "Coastal Rum Cooler Bag",
                "brand": "Coastal Rum",
                "channel": "Off Premise",
                "product_type": "Soft Goods",
                "key_program": "Summer",
                "item_description": "Insulated tote holding six bottles with Coastal Rum print.",
                "uom": "1/EA",
                "price": "$22.90",
                "image_url": "https://placehold.co/400x400/1c4d66/ffffff?text=Cooler+Bag",
                "sort_order": 4,
                "in_survey": False,
                "in_buy_book": True,
            },
        ],
        "tracker_items": [
            {
                "sheet": "ad-hoc",
                "date_or_buy": "2026-03",
                "current_status": "In Production",
                "brand": "Harbor Gin",
                "program_name": "Launch Kit",
                "item_name": "Harbor Gin Neon Sign",
                "qty": "250",
                "important_notes": "Demo Spirits inventory sample",
                "mrl_order_number": "DS-1001",
            },
            {
                "sheet": "buys",
                "date_or_buy": "Buy 1",
                "current_status": "Approved",
                "brand": "Coastal Rum",
                "program_name": "Summer",
                "item_name": "Coastal Rum Cooler Bag",
                "qty": "500",
                "important_notes": "Distinct from Phillips buys",
                "mrl_order_number": "DS-1002",
            },
        ],
    },
    {
        "slug": "acme",
        "name": "Acme Retail",
        "logo_url": "https://placehold.co/220x64/9a3412/ffffff?text=Acme+Retail",
        "banner_url": "https://placehold.co/960x120/9a3412/ffffff?text=Acme+Retail+Tracker",
        "user": {
            "email": "acme@example.com",
            "name": "Acme Retail User",
            "password": "Demo1234",
            "role": "editor",
        },
        "survey_items": [
            {
                "item_name": "Acme Floor Shipper",
                "brand": "Acme",
                "channel": "Off Premise",
                "product_type": "Displays",
                "key_program": "Q2 Push",
                "item_description": "Corrugated floor shipper with Acme header card.",
                "uom": "1/EA",
                "price": "$48.00",
                "image_url": "https://placehold.co/400x400/9a3412/ffffff?text=Floor+Shipper",
                "sort_order": 1,
                "in_survey": True,
                "in_buy_book": True,
            },
            {
                "item_name": "Acme Shelf Talker Pack",
                "brand": "Acme",
                "channel": "Off Premise",
                "product_type": "Print",
                "key_program": "Q2 Push",
                "item_description": "Pack of 25 shelf talkers with seasonal offer.",
                "uom": "25/PK",
                "price": "$12.40",
                "image_url": "https://placehold.co/400x400/c2410c/ffffff?text=Shelf+Talker",
                "sort_order": 2,
                "in_survey": True,
                "in_buy_book": True,
            },
            {
                "item_name": "Acme Apron",
                "brand": "Acme",
                "channel": "On Premise",
                "product_type": "Wearables",
                "key_program": "Staff Kit",
                "item_description": "Canvas apron with Acme logo chest print.",
                "uom": "1/EA",
                "price": "$18.75",
                "image_url": "https://placehold.co/400x400/7c2d12/ffffff?text=Acme+Apron",
                "sort_order": 3,
                "in_survey": True,
                "in_buy_book": False,
            },
            {
                "item_name": "Acme Window Cling",
                "brand": "Acme",
                "channel": "Off Premise",
                "product_type": "Print",
                "key_program": "Q2 Push",
                "item_description": "Static window cling for storefront promotions.",
                "uom": "10/PK",
                "price": "$9.95",
                "image_url": "https://placehold.co/400x400/b45309/ffffff?text=Window+Cling",
                "sort_order": 4,
                "in_survey": False,
                "in_buy_book": True,
            },
        ],
        "tracker_items": [
            {
                "sheet": "ad-hoc",
                "date_or_buy": "2026-04",
                "current_status": "Artwork Review",
                "brand": "Acme",
                "program_name": "Q2 Push",
                "item_name": "Acme Floor Shipper",
                "qty": "120",
                "important_notes": "Acme-only demo row",
                "mrl_order_number": "AC-2001",
            },
            {
                "sheet": "buys",
                "date_or_buy": "Buy A",
                "current_status": "Ordered",
                "brand": "Acme",
                "program_name": "Staff Kit",
                "item_name": "Acme Apron",
                "qty": "300",
                "important_notes": "Different inventory from Phillips/Demo",
                "mrl_order_number": "AC-2002",
            },
        ],
    },
]


def _ensure_company_record(conn: Any, seed: dict[str, Any]) -> Any:
    existing = fetch_company_by_slug(conn, seed["slug"])
    if existing is not None:
        execute(
            conn,
            """
            UPDATE companies
            SET name = ?, logo_url = ?, banner_url = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (seed["name"], seed["logo_url"], seed["banner_url"], True, existing["id"]),
        )
        return execute(conn, "SELECT * FROM companies WHERE id = ?", (existing["id"],)).fetchone()

    values = (seed["slug"], seed["name"], seed["logo_url"], seed["banner_url"], True)
    if IS_POSTGRES:
        return execute(
            conn,
            """
            INSERT INTO companies (slug, name, logo_url, banner_url, is_active)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            values,
        ).fetchone()

    cursor = execute(
        conn,
        """
        INSERT INTO companies (slug, name, logo_url, banner_url, is_active)
        VALUES (?, ?, ?, ?, ?)
        """,
        values,
    )
    return execute(conn, "SELECT * FROM companies WHERE id = ?", (cursor.lastrowid,)).fetchone()


def _ensure_demo_user(conn: Any, company_id: int, user_seed: dict[str, Any]) -> None:
    existing = execute(
        conn,
        "SELECT id FROM users WHERE lower(email) = lower(?)",
        (user_seed["email"],),
    ).fetchone()
    if existing is not None:
        execute(
            conn,
            """
            UPDATE users
            SET name = ?, role = ?, company_id = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (user_seed["name"], user_seed["role"], company_id, True, existing["id"]),
        )
        return

    execute(
        conn,
        """
        INSERT INTO users (email, name, password_hash, role, is_active, company_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_seed["email"].lower(),
            user_seed["name"],
            hash_password(user_seed["password"]),
            user_seed["role"],
            True,
            company_id,
        ),
    )


def _seed_company_survey_items(conn: Any, company_id: int, items: list[dict[str, Any]]) -> None:
    row_count = execute(
        conn,
        "SELECT COUNT(*) AS row_count FROM survey_items WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row_count["row_count"] > 0:
        return

    for item in items:
        execute(
            conn,
            """
            INSERT INTO survey_items (
                company_id, item_name, brand, channel, product_type, key_program,
                item_description, uom, price, image_url, sort_order, survey_sort_order,
                is_active, in_survey, in_buy_book
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                item["item_name"],
                item["brand"],
                item["channel"],
                item["product_type"],
                item["key_program"],
                item["item_description"],
                item["uom"],
                item["price"],
                item["image_url"],
                item["sort_order"],
                item["sort_order"],
                True,
                1 if item.get("in_survey", True) else 0,
                1 if item.get("in_buy_book", True) else 0,
            ),
        )


def _seed_company_tracker_items(conn: Any, company_id: int, items: list[dict[str, Any]]) -> None:
    row_count = execute(
        conn,
        "SELECT COUNT(*) AS row_count FROM tracker_items WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if row_count["row_count"] > 0:
        return

    for index, item in enumerate(items, start=1):
        execute(
            conn,
            """
            INSERT INTO tracker_items (
                company_id, sheet, source_row, sort_order, date_or_buy, current_status,
                visual_reference, brand, program_name, item_name, qty,
                important_notes, mrl_order_number, estimated_ship_date,
                estimated_ihd, tracking, extra_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                item.get("sheet", "ad-hoc"),
                index,
                index,
                item.get("date_or_buy", ""),
                item.get("current_status", ""),
                item.get("visual_reference", ""),
                item.get("brand", ""),
                item.get("program_name", ""),
                item.get("item_name", ""),
                item.get("qty", ""),
                item.get("important_notes", ""),
                item.get("mrl_order_number", ""),
                item.get("estimated_ship_date", ""),
                item.get("estimated_ihd", ""),
                item.get("tracking", ""),
                "{}",
            ),
        )


def seed_demo_companies(conn: Any) -> None:
    """Ensure demo companies, users, and distinct inventory exist for multi-tenant demos."""
    phillips = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
    if phillips is not None:
        _ensure_demo_user(
            conn,
            phillips["id"],
            {
                "email": "phillips@example.com",
                "name": "Phillips Demo User",
                "password": "Demo1234",
                "role": "editor",
            },
        )
        execute(
            conn,
            "UPDATE users SET company_id = ? WHERE company_id IS NULL",
            (phillips["id"],),
        )

    for seed in DEMO_COMPANY_SEEDS:
        company = _ensure_company_record(conn, seed)
        company_id = company["id"]
        ensure_survey_layout(conn, company_id)
        ensure_buy_book_layout(conn, company_id)
        seed_kanban_columns_if_empty(conn, company_id)
        seed_sheet_statuses_if_empty(conn, company_id)
        _seed_company_survey_items(conn, company_id, seed["survey_items"])
        _seed_company_tracker_items(conn, company_id, seed["tracker_items"])
        ensure_legacy_survey_period(conn, company_id)
        _ensure_demo_user(conn, company_id, seed["user"])
