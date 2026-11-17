from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH, DEFAULT_COMPANY_SLUG, IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402
from item_images import normalize_item_id  # noqa: E402


DEFAULT_WORKBOOK = Path(
    r"C:\Users\FSIJonathanCastro-Ra\Downloads\Phillips Project Tracker - Copy for FSI Dev.xlsx"
)

SHEET_MAP = {
    "Ad Hoc": "ad-hoc",
    "Buys": "buys",
    "Completed": "completed",
}

# A workbook can carry the NetSuite internal id in its own column; match the
# header rather than the position, so "NetSuite ID" / "NETSUITE ID" /
# "NetSuite Item ID" all land in the column the resolver reads. Columns 13+ are
# otherwise collected into extra_json, and that key is the fallback.
NETSUITE_ID_HEADERS = {"netsuiteid", "netsuiteitemid", "netsuiteinternalid", "internalid"}


def normalize_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def find_netsuite_id_column(ws: Any) -> int | None:
    for col in range(1, ws.max_column + 1):
        if normalize_header(ws.cell(1, col).value) in NETSUITE_ID_HEADERS:
            return col
    return None


FIELD_BY_COLUMN = {
    1: "date_or_buy",
    2: "current_status",
    3: "visual_reference",
    4: "brand",
    5: "program_name",
    6: "item_name",
    7: "qty",
    8: "important_notes",
    9: "mrl_order_number",
    10: "estimated_ship_date",
    11: "estimated_ihd",
    12: "tracking",
}


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def import_workbook(workbook_path: Path, company_slug: str = DEFAULT_COMPANY_SLUG) -> None:
    """Replace the tracker rows of ONE company dataset with the workbook's rows.

    The import owns a single dataset: it deletes and inserts inside that
    company_id only. An unscoped delete plus an insert without company_id
    leaves the other datasets' rows NULL-owned, and every public viewer filters
    on company_id, so a re-run would blank them until the next startup backfill.
    """
    init_db()
    workbook = load_workbook(workbook_path, data_only=True)

    with connect() as conn:
        company = fetch_company_by_slug(conn, company_slug)
        if company is None:
            raise SystemExit(f"No company with slug '{company_slug}'.")
        company_id = int(company["id"])
        execute(conn, "DELETE FROM tracker_items WHERE company_id = ?", (company_id,))

        imported = 0
        rejected_ids: list[str] = []
        sheets_without_id: list[str] = []
        for sheet_name, sheet_key in SHEET_MAP.items():
            ws = workbook[sheet_name]
            headers = {
                col: normalize(ws.cell(1, col).value)
                for col in range(13, ws.max_column + 1)
            }
            id_column = find_netsuite_id_column(ws)
            if id_column is None:
                sheets_without_id.append(sheet_name)

            for row_idx in range(2, ws.max_row + 1):
                row_values = {
                    field: normalize(ws.cell(row_idx, col).value)
                    for col, field in FIELD_BY_COLUMN.items()
                }
                extra = {
                    headers[col] or f"Column {col}": normalize(ws.cell(row_idx, col).value)
                    for col in range(13, ws.max_column + 1)
                    if normalize(ws.cell(row_idx, col).value) and col != id_column
                }

                raw_id = normalize(ws.cell(row_idx, id_column).value) if id_column else ""
                if not raw_id:
                    for header, value in extra.items():
                        if normalize_header(header) in NETSUITE_ID_HEADERS:
                            raw_id = value
                            break
                netsuite_item_id = normalize_item_id(raw_id)
                if raw_id and not netsuite_item_id:
                    rejected_ids.append(raw_id)

                # The header wins over the position: an id column that happens to
                # sit inside the known field range must not be stored as a URL.
                if id_column in FIELD_BY_COLUMN:
                    row_values[FIELD_BY_COLUMN[id_column]] = ""

                if not any(row_values.values()) and not extra and not netsuite_item_id:
                    continue

                execute(
                    conn,
                    """
                    INSERT INTO tracker_items (
                        company_id, sheet, source_row, sort_order, date_or_buy, current_status,
                        visual_reference, netsuite_item_id, brand, program_name, item_name, qty,
                        important_notes, mrl_order_number, estimated_ship_date,
                        estimated_ihd, tracking, extra_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        sheet_key,
                        row_idx,
                        row_idx - 1,
                        row_values["date_or_buy"],
                        row_values["current_status"],
                        row_values["visual_reference"],
                        netsuite_item_id,
                        row_values["brand"],
                        row_values["program_name"],
                        row_values["item_name"],
                        row_values["qty"],
                        row_values["important_notes"],
                        row_values["mrl_order_number"],
                        row_values["estimated_ship_date"],
                        row_values["estimated_ihd"],
                        row_values["tracking"],
                        json.dumps(extra),
                    ),
                )
                imported += 1

    target = "PostgreSQL DATABASE_URL" if IS_POSTGRES else str(DB_PATH)
    print(f"Imported {imported} tracker rows into {target} for company '{company_slug}'")
    if sheets_without_id:
        print(
            "No NetSuite ID column on sheet(s) "
            + ", ".join(sheets_without_id)
            + " — those rows imported without ids (the backfill can match by item name)."
        )
    if rejected_ids:
        print(
            f"Skipped {len(rejected_ids)} NetSuite ID value(s) that are not numeric internal ids"
            f" (e.g. {rejected_ids[0]!r}) — those are SKUs or file names."
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", nargs="?", default=None, help="path to the tracker workbook")
    parser.add_argument(
        "--company",
        default=DEFAULT_COMPANY_SLUG,
        help=f"company slug whose dataset this workbook replaces (default {DEFAULT_COMPANY_SLUG})",
    )
    cli_args = parser.parse_args()
    import_workbook(Path(cli_args.workbook) if cli_args.workbook else DEFAULT_WORKBOOK, cli_args.company)
