from __future__ import annotations

import io
import json
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
    row_to_item,
    utc_now,
    with_returning,
    write_audit_log,
)
from models import ItemChangeBatch, ItemCreate, ItemUpdate
from routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/items", tags=["items"])

EXPORT_COLUMNS = [
    ("date_or_buy", "Date/Buy"),
    ("current_status", "Status"),
    ("visual_reference", "Visual Reference"),
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


@router.get("")
def list_items(
    sheet: str = "ad-hoc",
    q: str = "",
    status_filter: str = Query("", alias="status"),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    if sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    company_id = company_id_for_user(user)
    clauses = ["company_id = ?", "sheet = ?"]
    args: list[Any] = [company_id, sheet]
    if status_filter:
        clauses.append("current_status = ?")
        args.append(status_filter)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(brand) LIKE ? OR lower(program_name) LIKE ? OR "
            "lower(item_name) LIKE ? OR lower(important_notes) LIKE ? OR "
            "lower(mrl_order_number) LIKE ? OR lower(tracking) LIKE ?)"
        )
        args.extend([like] * 6)

    with connect() as conn:
        rows = execute(
            conn,
            f"SELECT * FROM tracker_items WHERE {' AND '.join(clauses)} ORDER BY sort_order ASC, id ASC",
            args,
        ).fetchall()
    return {"items": [row_to_item(row) for row in rows]}


@router.get("/export")
def export_items(
    sheet: str = "ad-hoc",
    q: str = "",
    status_filter: str = Query("", alias="status"),
    user: sqlite3.Row = Depends(get_current_user),
) -> Response:
    if sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    company_id = company_id_for_user(user)
    clauses = ["company_id = ?", "sheet = ?"]
    args: list[Any] = [company_id, sheet]
    if status_filter:
        clauses.append("current_status = ?")
        args.append(status_filter)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(brand) LIKE ? OR lower(program_name) LIKE ? OR "
            "lower(item_name) LIKE ? OR lower(important_notes) LIKE ? OR "
            "lower(mrl_order_number) LIKE ? OR lower(tracking) LIKE ?)"
        )
        args.extend([like] * 6)

    with connect() as conn:
        rows = execute(
            conn,
            f"SELECT * FROM tracker_items WHERE {' AND '.join(clauses)} ORDER BY sort_order ASC, id ASC",
            args,
        ).fetchall()

    workbook = Workbook()
    worksheet = workbook.active
    sheet_label = SHEETS.get(sheet, {}).get("label", sheet)
    worksheet.title = sheet_label
    headers = [label for _, label in EXPORT_COLUMNS]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)

    for row in rows:
        item = row_to_item(row)
        worksheet.append([str(item.get(field, "") or "") for field, _ in EXPORT_COLUMNS])

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
def create_item(payload: ItemCreate, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    if payload.sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")

    company_id = company_id_for_user(user)
    with connect() as conn:
        max_sort_row = execute(
            conn,
            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM tracker_items WHERE company_id = ? AND sheet = ?",
            (company_id, payload.sheet),
        ).fetchone()
        values = (
            company_id, payload.sheet, max_sort_row["max_sort"] + 1,
            payload.date_or_buy, payload.current_status, payload.visual_reference,
            payload.brand, payload.program_name, payload.item_name, payload.qty,
            payload.important_notes, payload.mrl_order_number, payload.estimated_ship_date,
            payload.estimated_ihd, payload.tracking,
        )
        sql = """
            INSERT INTO tracker_items (
                company_id, sheet, sort_order, date_or_buy, current_status, visual_reference,
                brand, program_name, item_name, qty, important_notes,
                mrl_order_number, estimated_ship_date, estimated_ihd, tracking
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if IS_POSTGRES:
            row = execute(conn, with_returning(sql), values).fetchone()
        else:
            cursor = execute(conn, sql, values)
            row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        write_audit_log(conn, user, "create", sheet=payload.sheet, item_id=row["id"],
                        after=row_to_item(row), company_id=company_id)
    return {"item": row_to_item(row)}


@router.patch("/{item_id}")
def update_item(item_id: int, payload: ItemUpdate, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    updates = {k: "" if v is None else str(v) for k, v in updates.items()}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No editable fields supplied")

    company_id = company_id_for_user(user)
    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [item_id, company_id]

    with connect() as conn:
        before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                             (item_id, company_id)).fetchone()
        if before_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        cursor = execute(
            conn,
            f"UPDATE tracker_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                      (item_id, company_id)).fetchone()
        write_audit_log(conn, user, "update", sheet=row["sheet"], item_id=item_id,
                        before=row_to_item(before_row), after=row_to_item(row), company_id=company_id)
    return {"item": row_to_item(row)}


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, response: Response, user: sqlite3.Row = Depends(require_admin)) -> Response:
    company_id = company_id_for_user(user)
    with connect() as conn:
        before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                             (item_id, company_id)).fetchone()
        if before_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
        cursor = execute(conn, "DELETE FROM tracker_items WHERE id = ? AND company_id = ?",
                         (item_id, company_id))
        write_audit_log(conn, user, "delete", sheet=before_row["sheet"], item_id=item_id,
                        before=row_to_item(before_row), company_id=company_id)
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/changes")
def submit_item_changes(payload: ItemChangeBatch, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    created = updated = deleted = 0
    company_id = company_id_for_user(user)

    with connect() as conn:
        for cp in payload.creates:
            if cp.sheet not in SHEETS:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")
            max_sort = execute(
                conn,
                "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM tracker_items WHERE company_id = ? AND sheet = ?",
                (company_id, cp.sheet),
            ).fetchone()["max_sort"]
            values = (
                company_id, cp.sheet, max_sort + 1, cp.date_or_buy, cp.current_status,
                cp.visual_reference, cp.brand, cp.program_name, cp.item_name, cp.qty,
                cp.important_notes, cp.mrl_order_number, cp.estimated_ship_date,
                cp.estimated_ihd, cp.tracking,
            )
            sql = """
                INSERT INTO tracker_items (
                    company_id, sheet, sort_order, date_or_buy, current_status, visual_reference,
                    brand, program_name, item_name, qty, important_notes,
                    mrl_order_number, estimated_ship_date, estimated_ihd, tracking
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            if IS_POSTGRES:
                row = execute(conn, with_returning(sql), values).fetchone()
            else:
                cursor = execute(conn, sql, values)
                row = execute(conn, "SELECT * FROM tracker_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
            created += 1
            write_audit_log(conn, user, "create", sheet=cp.sheet, item_id=row["id"],
                            after=row_to_item(row), company_id=company_id)

        for up in payload.updates:
            updates = up.changes.model_dump(exclude_unset=True)
            updates = {k: "" if v is None else str(v) for k, v in updates.items()}
            if not updates:
                continue
            before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                                 (up.id, company_id)).fetchone()
            if before_row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")
            assignments = ", ".join(f"{field} = ?" for field in updates)
            execute(
                conn,
                f"UPDATE tracker_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
                list(updates.values()) + [up.id, company_id],
            )
            after_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                                (up.id, company_id)).fetchone()
            updated += 1
            write_audit_log(conn, user, "update", sheet=after_row["sheet"], item_id=up.id,
                            before=row_to_item(before_row), after=row_to_item(after_row), company_id=company_id)

        for item_id in payload.deletes:
            before_row = execute(conn, "SELECT * FROM tracker_items WHERE id = ? AND company_id = ?",
                                 (item_id, company_id)).fetchone()
            if before_row is None:
                continue
            execute(conn, "DELETE FROM tracker_items WHERE id = ? AND company_id = ?", (item_id, company_id))
            deleted += 1
            write_audit_log(conn, user, "delete", sheet=before_row["sheet"], item_id=item_id,
                            before=row_to_item(before_row), company_id=company_id)

    return {"ok": True, "created": created, "updated": updated, "deleted": deleted}
