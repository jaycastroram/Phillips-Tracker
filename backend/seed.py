from __future__ import annotations

import json
from typing import Any

from config import DEFAULT_KANBAN_COLUMNS, SEED_ITEMS_PATH, SHEETS
from database import execute, IS_POSTGRES

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
                "INSERT INTO sheet_statuses (company_id, sheet, status, sort_order) VALUES (?, ?, ?, ?)",
                (company_id, sheet_key, status_label, sort_order),
            )


def sync_default_survey_items(conn: Any, company_id: int) -> None:
    active_item_names = []

    for item in DEFAULT_SURVEY_ITEMS:
        active_item_names.append(item["item_name"].lower())
        existing_item = execute(
            conn,
            """
            SELECT id FROM survey_items
            WHERE company_id = ? AND lower(item_name) = lower(?)
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
