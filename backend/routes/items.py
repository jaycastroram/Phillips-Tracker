from __future__ import annotations

import io
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Font

from config import IS_POSTGRES, SHEETS
from database import (
    connect,
    company_id_for_user,
    execute,
    resolve_optional_company_scope,
    resolve_scoped_company_id,
    row_to_item,
    utc_now,
    with_returning,
    write_audit_log,
)
from item_images import normalize_item_id, record_image_url
from models import ItemChangeBatch, ItemCreate, ItemUpdate
from routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/items", tags=["items"])

EXPORT_COLUMNS = [
    ("company_name", "Company"),
    ("date_or_buy", "Date/Buy"),
    ("current_status", "Status"),
    ("visual_reference", "Visual Reference"),
    ("netsuite_item_id", "NetSuite ID"),
    ("brand", "Brand"),
    ("program_name", "Program Name"),
    ("item_name", "Item Name"),
    ("qty", "Qty"),
    ("important_notes", "Notes"),
    ("mrl_order_number", "MRL Order #"),
    ("estimated_ship_date", "Est. Ship Date"),
    ("estimated_ihd", "Est. IHD"),
    ("tracking", "Tracking"),
]


def _serialize_item(row: Any) -> dict:
    item = row_to_item(row)
    if "company_name" not in item:
        item["company_name"] = ""
    if "company_slug" not in item:
        item["company_slug"] = ""
    return item


def _apply_netsuite_image(conn: Any, row: Any, *, resolve: bool = True) -> Any:
    """Fill a blank ``visual_reference`` from this row's imported item record.

    No network call and no URL building: the URL is whatever
    ``netsuite_item_images`` holds for this company and internal id (imported
    from a NetSuite record set). A row whose id has no record stays blank rather
    than holding a guess, and an existing URL — a Cloudinary upload or a
    hand-pasted one — is never overwritten.

    ``resolve=False`` stores nothing but the id — that is the bulk path. A paste
    of many rows should not fill image fields from a store that may still be
    partial, and one script (``scripts/backfill_netsuite_ids.py``) is the only
    writer of image URLs from records. ``resolve=True`` is for a single-row save,
    where the person can see the result immediately.

    Returns the refreshed row so the audit entry and the response carry what the
    public pages will read.
    """
    if row is None:
        return row
    try:
        keys = row.keys()
    except AttributeError:
        return row
    if "visual_reference" not in keys or "netsuite_item_id" not in keys:
        return row
    if str(row["visual_reference"] or "").strip():
        return row
    if not resolve:
        return row
    item_id = normalize_item_id(row["netsuite_item_id"])
    if not item_id or row["company_id"] in (None, ""):
        return row

    url = record_image_url(conn, row["company_id"], item_id)
    if not url:
        return row

    execute(conn, "UPDATE tracker_items SET visual_reference = ? WHERE id = ?", (url, row["id"]))
    return execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (row["id"],)).fetchone()


def _item_query(
    sheet: str,
    q: str,
    status_filter: str,
    company_id: int | None,
) -> tuple[str, list[Any]]:
    clauses = ["tracker_items.sheet = ?"]
    args: list[Any] = [sheet]
    if company_id is not None:
        clauses.append("tracker_items.company_id = ?")
        args.append(company_id)
    if status_filter:
        clauses.append("tracker_items.current_status = ?")
        args.append(status_filter)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(tracker_items.brand) LIKE ? OR lower(tracker_items.program_name) LIKE ? OR "
            "lower(tracker_items.item_name) LIKE ? OR lower(tracker_items.important_notes) LIKE ? OR "
            "lower(tracker_items.mrl_order_number) LIKE ? OR lower(tracker_items.tracking) LIKE ? OR "
            "lower(COALESCE(companies.name, '')) LIKE ?)"
        )
        args.extend([like] * 7)
    sql = f"""
        SELECT tracker_items.*, companies.name AS company_name, companies.slug AS company_slug
        FROM tracker_items
        LEFT JOIN companies ON companies.id = tracker_items.company_id
        WHERE {' AND '.join(clauses)}
        ORDER BY companies.name ASC, tracker_items.sort_order ASC, tracker_items.id ASC
    """
    return sql, args


def _resolve_write_company_id(user: sqlite3.Row, requested_company_id: int | None) -> int:
    """Creates need a concrete company; admins may target one, others stay home-scoped."""
    if requested_company_id is not None:
        return resolve_scoped_company_id(user, requested_company_id)
    return company_id_for_user(user)


@router.get("")
def list_items(
    sheet: str = "ad-hoc",
    q: str = "",
    status_filter: str = Query("", alias="status"),
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    if sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    scoped_company_id = resolve_optional_company_scope(user, company_id)
    sql, args = _item_query(sheet, q, status_filter, scoped_company_id)
    with connect() as conn:
        rows = execute(conn, sql, args).fetchall()
    return {"items": [_serialize_item(row) for row in rows]}


@router.get("/export")
def export_items(
    sheet: str = "ad-hoc",
    q: str = "",
    status_filter: str = Query("", alias="status"),
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> Response:
    if sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    scoped_company_id = resolve_optional_company_scope(user, company_id)
    sql, args = _item_query(sheet, q, status_filter, scoped_company_id)
    with connect() as conn:
        rows = execute(conn, sql, args).fetchall()

    workbook = Workbook()
    worksheet = workbook.active
    sheet_label = SHEETS.get(sheet, {}).get("label", sheet)
    worksheet.title = sheet_label
    include_company = scoped_company_id is None
    columns = EXPORT_COLUMNS if include_company else [col for col in EXPORT_COLUMNS if col[0] != "company_name"]
    headers = [label for _, label in columns]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        item = _serialize_item(row)
        worksheet.append([str(item.get(field, "") or "") for field, _ in columns])

    for column_cells in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 55)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"tracker-{sheet}-{utc_now().date().isoformat()}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    if payload.sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    write_company_id = _resolve_write_company_id(user, company_id)
    with connect() as conn:
        max_sort_row = execute(
            conn,
            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM tracker_items WHERE company_id = ? AND sheet = ?",
            (write_company_id, payload.sheet),
        ).fetchone()
        values = (
            write_company_id, payload.sheet, max_sort_row["max_sort"] + 1,
            payload.date_or_buy, payload.current_status, payload.visual_reference,
            payload.netsuite_item_id, payload.brand, payload.program_name, payload.item_name, payload.qty,
            payload.important_notes, payload.mrl_order_number, payload.estimated_ship_date,
            payload.estimated_ihd, payload.tracking,
        )
        sql = """
            INSERT INTO tracker_items (
                company_id, sheet, sort_order, date_or_buy, current_status, visual_reference,
                netsuite_item_id, brand, program_name, item_name, qty, important_notes,
                mrl_order_number, estimated_ship_date, estimated_ihd, tracking
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if IS_POSTGRES:
            row = execute(conn, with_returning(sql), values).fetchone()
        else:
            cursor = execute(conn, sql, values)
            row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        row = _apply_netsuite_image(conn, row)
        write_audit_log(conn, user, "create", sheet=payload.sheet, item_id=row["id"],
                        after=row_to_item(row), company_id=write_company_id)
        enriched = execute(
            conn,
            """
            SELECT tracker_items.*, companies.name AS company_name, companies.slug AS company_slug
            FROM tracker_items
            LEFT JOIN companies ON companies.id = tracker_items.company_id
            WHERE tracker_items.id = ?
            """,
            (row["id"],),
        ).fetchone()
    return {"item": _serialize_item(enriched)}


@router.patch("/{item_id}")
def update_item(
    item_id: int,
    payload: ItemUpdate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    updates = {k: "" if v is None else str(v) for k, v in updates.items()}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")

    scoped_company_id = resolve_optional_company_scope(user, company_id)
    assignments = ", ".join(f"{field} = ?" for field in updates)

    with connect() as conn:
        if scoped_company_id is None:
            before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (item_id,)).fetchone()
        else:
            before_row = execute(
                conn,
                "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                (item_id, scoped_company_id),
            ).fetchone()
        if before_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

        item_company_id = int(before_row["company_id"])
        values = list(updates.values()) + [item_id, item_company_id]
        cursor = execute(
            conn,
            f"UPDATE tracker_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        _apply_netsuite_image(conn, execute(
            conn, "SELECT * FROM tracker_items WHERE id = ?", (item_id,)
        ).fetchone())
        row = execute(
            conn,
            """
            SELECT tracker_items.*, companies.name AS company_name, companies.slug AS company_slug
            FROM tracker_items
            LEFT JOIN companies ON companies.id = tracker_items.company_id
            WHERE tracker_items.id = ? AND tracker_items.company_id = ?
            """,
            (item_id, item_company_id),
        ).fetchone()
        write_audit_log(
            conn,
            user,
            "update",
            sheet=row["sheet"],
            item_id=item_id,
            before=row_to_item(before_row),
            after=row_to_item(row),
            company_id=item_company_id,
        )
    return {"item": _serialize_item(row)}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: int,
    response: Response,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> Response:
    scoped_company_id = resolve_optional_company_scope(user, company_id)
    with connect() as conn:
        if scoped_company_id is None:
            before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (item_id,)).fetchone()
        else:
            before_row = execute(
                conn,
                "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                (item_id, scoped_company_id),
            ).fetchone()
        if before_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        item_company_id = int(before_row["company_id"])
        cursor = execute(
            conn,
            "DELETE FROM tracker_items WHERE id = ? AND company_id = ?",
            (item_id, item_company_id),
        )
        write_audit_log(
            conn,
            user,
            "delete",
            sheet=before_row["sheet"],
            item_id=item_id,
            before=row_to_item(before_row),
            company_id=item_company_id,
        )
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/changes")
def submit_item_changes(
    payload: ItemChangeBatch,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    created = updated = deleted = 0
    write_company_id = _resolve_write_company_id(user, company_id)
    scoped_company_id = resolve_optional_company_scope(user, company_id)
    is_admin = user["role"] == "admin"

    with connect() as conn:
        for cp in payload.creates:
            if cp.sheet not in SHEETS:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")
            if scoped_company_id is None and is_admin and company_id is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Select a company dataset before adding tracker rows.",
                )
            max_sort = execute(
                conn,
                "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM tracker_items WHERE company_id = ? AND sheet = ?",
                (write_company_id, cp.sheet),
            ).fetchone()["max_sort"]
            values = (
                write_company_id, cp.sheet, max_sort + 1, cp.date_or_buy, cp.current_status,
                cp.visual_reference, cp.netsuite_item_id, cp.brand, cp.program_name, cp.item_name, cp.qty,
                cp.important_notes, cp.mrl_order_number, cp.estimated_ship_date,
                cp.estimated_ihd, cp.tracking,
            )
            sql = """
                INSERT INTO tracker_items (
                    company_id, sheet, sort_order, date_or_buy, current_status, visual_reference,
                    netsuite_item_id, brand, program_name, item_name, qty, important_notes,
                    mrl_order_number, estimated_ship_date, estimated_ihd, tracking
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            if IS_POSTGRES:
                row = execute(conn, with_returning(sql), values).fetchone()
            else:
                cursor = execute(conn, sql, values)
                row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
            row = _apply_netsuite_image(conn, row, resolve=False)
            created += 1
            write_audit_log(conn, user, "create", sheet=cp.sheet, item_id=row["id"],
                            after=row_to_item(row), company_id=write_company_id)

        for up in payload.updates:
            updates = up.changes.model_dump(exclude_unset=True)
            updates = {k: "" if v is None else str(v) for k, v in updates.items()}
            if not updates:
                continue
            if scoped_company_id is None:
                before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (up.id,)).fetchone()
            else:
                before_row = execute(
                    conn,
                    "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                    (up.id, scoped_company_id),
                ).fetchone()
            if before_row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
            item_company_id = int(before_row["company_id"])
            assignments = ", ".join(f"{field} = ?" for field in updates)
            execute(
                conn,
                f"UPDATE tracker_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
                list(updates.values()) + [up.id, item_company_id],
            )
            after_row = execute(
                conn,
                "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                (up.id, item_company_id),
            ).fetchone()
            after_row = _apply_netsuite_image(conn, after_row, resolve=False)
            updated += 1
            write_audit_log(conn, user, "update", sheet=after_row["sheet"], item_id=up.id,
                            before=row_to_item(before_row), after=row_to_item(after_row),
                            company_id=item_company_id)

        for item_id in payload.deletes:
            if scoped_company_id is None:
                before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (item_id,)).fetchone()
            else:
                before_row = execute(
                    conn,
                    "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                    (item_id, scoped_company_id),
                ).fetchone()
            if before_row is None:
                continue
            item_company_id = int(before_row["company_id"])
            execute(conn, "DELETE FROM tracker_items WHERE id = ? AND company_id = ?", (item_id, item_company_id))
            deleted += 1
            write_audit_log(conn, user, "delete", sheet=before_row["sheet"], item_id=item_id,
                            before=row_to_item(before_row), company_id=item_company_id)

    return {"ok": True, "created": created, "updated": updated, "deleted": deleted}
