from __future__ import annotations

import io
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from openpyxl import Workbook
from openpyxl.styles import Font

from config import IS_POSTGRES, SHEETS
from database import (
    close_open_survey_periods,
    connect,
    copy_catalog_items_into_period,
    count_period_items,
    company_id_for_user,
    company_to_public,
    serialize_user,
    ensure_legacy_survey_period,
    ensure_survey_layout,
    execute,
    get_survey_period,
    hash_password,
    kanban_column_to_public,
    list_period_items,
    normalize_filter_fields,
    period_item_to_public,
    sheet_status_to_public,
    sql_group_concat_distinct,
    sql_round_avg,
    survey_filter_fields,
    survey_item_to_public,
    survey_period_to_public,
    utc_now,
    with_returning,
    audit_log_to_public,
)
from models import (
    CompanyUpdate,
    KanbanColumnCreate,
    KanbanColumnUpdate,
    PasswordReset,
    SheetStatusCreate,
    SurveyLayoutUpdate,
    SurveyPeriodCreate,
    SurveyPeriodLayoutUpdate,
    SurveyPeriodUpdate,
    UserCreate,
    UserUpdate,
)
from routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Survey results dashboard
# ---------------------------------------------------------------------------

@router.get("/survey-results")
def get_survey_results(period_id: int | None = None, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        period = None
        if period_id is not None:
            period = get_survey_period(conn, company_id, period_id)
            if period is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        else:
            period = execute(
                conn,
                """
                SELECT * FROM survey_periods
                WHERE company_id = ?
                ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END, id DESC
                LIMIT 1
                """,
                (company_id,),
            ).fetchone()

        period_clause = ""
        item_args: list[Any] = [company_id]
        total_args: list[Any] = [company_id]
        response_args: list[Any] = [company_id]
        if period is not None:
            period_clause = " AND sr.survey_period_id = ?"
            item_args.append(period["id"])
            total_args.append(period["id"])
            response_args.append(period["id"])

        email_agg = sql_group_concat_distinct("sr.email", "respondent_emails")
        avg_attention = sql_round_avg(
            "CASE WHEN sr.id IS NOT NULL THEN sr.attention_effectiveness END",
            "avg_attention",
        )
        avg_engagement = sql_round_avg(
            "CASE WHEN sr.id IS NOT NULL THEN sr.retail_engagement END",
            "avg_engagement",
        )

        # Prefer period snapshot items when a period is selected; fall back to catalog.
        if period is not None:
            item_rows = execute(
                conn,
                f"""
                SELECT
                    spi.catalog_item_id AS id,
                    spi.item_name,
                    spi.brand,
                    spi.channel,
                    spi.product_type,
                    spi.key_program,
                    spi.image_url,
                    spi.price,
                    spi.uom,
                    COUNT(sr.id) AS total_responses,
                    SUM(CASE WHEN sr.recommend_rollout = 'Yes' THEN 1 ELSE 0 END) AS interested,
                    SUM(CASE WHEN sr.recommend_rollout = 'No' THEN 1 ELSE 0 END) AS not_interested,
                    SUM(CASE WHEN sr.recommend_rollout = 'Maybe' THEN 1 ELSE 0 END) AS maybe,
                    {avg_attention},
                    {avg_engagement},
                    {email_agg}
                FROM survey_period_items spi
                LEFT JOIN survey_responses sr
                    ON sr.survey_item_id = spi.catalog_item_id
                    AND sr.company_id = ?
                    AND sr.survey_period_id = spi.survey_period_id
                WHERE spi.survey_period_id = ?
                GROUP BY
                    spi.id, spi.catalog_item_id, spi.item_name, spi.brand, spi.channel,
                    spi.product_type, spi.key_program, spi.image_url, spi.price, spi.uom, spi.sort_order
                ORDER BY interested DESC, total_responses DESC, spi.sort_order ASC
                """,
                (company_id, period["id"]),
            ).fetchall()
        else:
            item_rows = execute(
                conn,
                f"""
                SELECT
                    si.id,
                    si.item_name,
                    si.brand,
                    si.channel,
                    si.product_type,
                    si.key_program,
                    si.image_url,
                    si.price,
                    si.uom,
                    COUNT(sr.id) AS total_responses,
                    SUM(CASE WHEN sr.recommend_rollout = 'Yes' THEN 1 ELSE 0 END) AS interested,
                    SUM(CASE WHEN sr.recommend_rollout = 'No' THEN 1 ELSE 0 END) AS not_interested,
                    SUM(CASE WHEN sr.recommend_rollout = 'Maybe' THEN 1 ELSE 0 END) AS maybe,
                    {avg_attention},
                    {avg_engagement},
                    {email_agg}
                FROM survey_items si
                LEFT JOIN survey_responses sr ON sr.survey_item_id = si.id AND sr.company_id = ?{period_clause}
                WHERE si.company_id = ? AND si.is_active = ?
                GROUP BY
                    si.id, si.item_name, si.brand, si.channel, si.product_type, si.key_program,
                    si.image_url, si.price, si.uom, si.sort_order
                ORDER BY interested DESC, total_responses DESC, si.sort_order ASC
                """,
                (*item_args, company_id, True),
            ).fetchall()

        totals = execute(
            conn,
            f"""
            SELECT
                COUNT(DISTINCT email) AS unique_respondents,
                COUNT(*) AS total_responses,
                SUM(CASE WHEN recommend_rollout = 'Yes' THEN 1 ELSE 0 END) AS total_interested,
                SUM(CASE WHEN recommend_rollout = 'No' THEN 1 ELSE 0 END) AS total_not_interested
            FROM survey_responses
            WHERE company_id = ?{" AND survey_period_id = ?" if period is not None else ""}
            """,
            tuple(total_args),
        ).fetchone()

        response_rows = execute(
            conn,
            f"""
            SELECT
                sr.id,
                sr.email,
                sr.recommend_rollout,
                sr.feedback,
                sr.created_at,
                si.item_name,
                si.brand
            FROM survey_responses sr
            LEFT JOIN survey_items si ON si.id = sr.survey_item_id
            WHERE sr.company_id = ?{" AND sr.survey_period_id = ?" if period is not None else ""}
            ORDER BY sr.created_at DESC
            LIMIT 500
            """,
            tuple(response_args),
        ).fetchall()

        periods = execute(
            conn,
            """
            SELECT * FROM survey_periods
            WHERE company_id = ?
            ORDER BY id DESC
            """,
            (company_id,),
        ).fetchall()

    items = []
    for row in item_rows:
        emails = [e for e in (row["respondent_emails"] or "").split(",") if e.strip()] if row["respondent_emails"] else []
        items.append({
            "id": row["id"],
            "item_name": row["item_name"],
            "brand": row["brand"],
            "channel": row["channel"],
            "product_type": row["product_type"],
            "key_program": row["key_program"],
            "image_url": row["image_url"],
            "price": row["price"],
            "uom": row["uom"],
            "total_responses": row["total_responses"] or 0,
            "interested": row["interested"] or 0,
            "not_interested": row["not_interested"] or 0,
            "maybe": row["maybe"] or 0,
            "avg_attention": float(row["avg_attention"]) if row["avg_attention"] is not None else None,
            "avg_engagement": float(row["avg_engagement"]) if row["avg_engagement"] is not None else None,
            "interest_pct": round((row["interested"] or 0) / row["total_responses"] * 100) if row["total_responses"] else 0,
            "respondent_emails": emails,
        })

    responses = [
        {
            "id": r["id"],
            "email": r["email"],
            "recommend_rollout": r["recommend_rollout"],
            "feedback": r["feedback"],
            "created_at": r["created_at"],
            "item_name": r["item_name"] or "",
            "brand": r["brand"] or "",
        }
        for r in response_rows
    ]

    return {
        "period": survey_period_to_public(period) if period is not None else None,
        "periods": [survey_period_to_public(row) for row in periods],
        "summary": {
            "unique_respondents": totals["unique_respondents"] or 0,
            "total_responses": totals["total_responses"] or 0,
            "total_interested": totals["total_interested"] or 0,
            "total_not_interested": totals["total_not_interested"] or 0,
        },
        "items": items,
        "responses": responses,
    }


# ---------------------------------------------------------------------------
# Survey layout
# ---------------------------------------------------------------------------

@router.get("/survey-layout")
def get_survey_layout(user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM survey_items WHERE company_id = ? AND is_active = ? ORDER BY survey_sort_order ASC, sort_order ASC, id ASC",
            (company_id, True),
        ).fetchall()
        fields = survey_filter_fields(conn, company_id)
    return {"filter_fields": fields, "items": [survey_item_to_public(row) for row in rows]}


@router.put("/survey-layout")
def update_survey_layout(payload: SurveyLayoutUpdate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    filter_fields = normalize_filter_fields(payload.filter_fields)
    with connect() as conn:
        ensure_survey_layout(conn, company_id)
        execute(
            conn,
            "UPDATE survey_layouts SET filter_fields_json = ?, updated_at = CURRENT_TIMESTAMP WHERE company_id = ?",
            (json.dumps(filter_fields), company_id),
        )
        for index, item in enumerate(payload.items, start=1):
            cursor = execute(
                conn,
                """
                UPDATE survey_items
                SET in_survey = ?, in_buy_book = ?, survey_sort_order = ?, response_width = ?,
                    page_break_before = ?, image_display_size = ?, image_fit = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND company_id = ? AND is_active = ?
                """,
                (
                    1 if item.in_survey else 0,
                    1 if item.in_buy_book else 0,
                    index,
                    item.response_width,
                    1 if item.page_break_before and index > 1 else 0,
                    item.image_display_size,
                    item.image_fit,
                    item.id,
                    company_id,
                    True,
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey item not found")
        rows = execute(
            conn,
            "SELECT * FROM survey_items WHERE company_id = ? AND is_active = ? ORDER BY survey_sort_order ASC, sort_order ASC, id ASC",
            (company_id, True),
        ).fetchall()
    return {"filter_fields": filter_fields, "items": [survey_item_to_public(row) for row in rows]}


# ---------------------------------------------------------------------------
# Survey periods
# ---------------------------------------------------------------------------

@router.get("/survey-periods")
def list_survey_periods(user: sqlite3.Row = Depends(get_current_user)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        rows = execute(
            conn,
            """
            SELECT * FROM survey_periods
            WHERE company_id = ?
            ORDER BY id DESC
            """,
            (company_id,),
        ).fetchall()
        periods = [
            survey_period_to_public(row, item_count=count_period_items(conn, row["id"]))
            for row in rows
        ]
    return {"periods": periods}


@router.post("/survey-periods", status_code=status.HTTP_201_CREATED)
def create_survey_period(payload: SurveyPeriodCreate, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    company_id = company_id_for_user(user)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Survey name is required")

    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        source_period = None
        if payload.copy_from_period_id is not None:
            source_period = get_survey_period(conn, company_id, payload.copy_from_period_id)
            if source_period is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Source survey period not found")
            filter_fields_json = source_period["filter_fields_json"]
        else:
            filter_fields_json = json.dumps(survey_filter_fields(conn, company_id))

        status_value = "open" if payload.open_immediately else "draft"
        opens_at = utc_now().isoformat() if payload.open_immediately else None
        if payload.open_immediately:
            close_open_survey_periods(conn, company_id)

        values = (company_id, name, status_value, opens_at, filter_fields_json)
        if IS_POSTGRES:
            period = execute(
                conn,
                """
                INSERT INTO survey_periods (company_id, name, status, opens_at, filter_fields_json)
                VALUES (?, ?, ?, ?, ?)
                RETURNING *
                """,
                values,
            ).fetchone()
        else:
            cursor = execute(
                conn,
                """
                INSERT INTO survey_periods (company_id, name, status, opens_at, filter_fields_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )
            period = execute(conn, "SELECT * FROM survey_periods WHERE id = ?", (cursor.lastrowid,)).fetchone()

        copy_catalog_items_into_period(
            conn,
            period["id"],
            company_id,
            only_in_survey=True,
            source_period_id=source_period["id"] if source_period is not None else None,
        )
        item_count = count_period_items(conn, period["id"])

    return {"period": survey_period_to_public(period, item_count=item_count)}


@router.get("/survey-periods/{period_id}")
def get_survey_period_detail(period_id: int, user: sqlite3.Row = Depends(get_current_user)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        items = list_period_items(conn, period_id)
        catalog_rows = execute(
            conn,
            """
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ?
            ORDER BY survey_sort_order ASC, sort_order ASC, id ASC
            """,
            (company_id, True),
        ).fetchall()

    period_by_catalog = {row["catalog_item_id"]: row for row in items}
    editor_items = []
    for catalog in catalog_rows:
        period_item = period_by_catalog.get(catalog["id"])
        public = survey_item_to_public(catalog)
        if period_item is not None:
            public.update({
                "in_survey": True,
                "survey_sort_order": period_item["sort_order"],
                "response_width": period_item["response_width"],
                "page_break_before": bool(period_item["page_break_before"]),
                "image_display_size": period_item["image_display_size"],
                "image_fit": period_item["image_fit"],
                "period_item_id": period_item["id"],
            })
        else:
            public.update({
                "in_survey": False,
                "page_break_before": False,
                "period_item_id": None,
            })
        editor_items.append(public)

    editor_items.sort(key=lambda item: (0 if item["in_survey"] else 1, item["survey_sort_order"], item["id"]))
    return {
        "period": survey_period_to_public(period, item_count=len(items)),
        "filter_fields": survey_period_to_public(period)["filter_fields"],
        "items": editor_items,
    }


@router.patch("/survey-periods/{period_id}")
def update_survey_period(
    period_id: int,
    payload: SurveyPeriodUpdate,
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")

        name = payload.name.strip() if payload.name is not None else period["name"]
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Survey name is required")

        next_status = payload.status if payload.status is not None else period["status"]
        opens_at = payload.opens_at if payload.opens_at is not None else period["opens_at"]
        closes_at = payload.closes_at if payload.closes_at is not None else period["closes_at"]

        if next_status == "open" and period["status"] != "open":
            close_open_survey_periods(conn, company_id)
            opens_at = opens_at or utc_now().isoformat()
            closes_at = None
        elif next_status == "closed" and period["status"] != "closed":
            closes_at = closes_at or utc_now().isoformat()

        execute(
            conn,
            """
            UPDATE survey_periods
            SET name = ?, status = ?, opens_at = ?, closes_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (name, next_status, opens_at, closes_at, period_id, company_id),
        )
        period = get_survey_period(conn, company_id, period_id)
        item_count = count_period_items(conn, period_id)

    return {"period": survey_period_to_public(period, item_count=item_count)}


@router.put("/survey-periods/{period_id}/layout")
def update_survey_period_layout(
    period_id: int,
    payload: SurveyPeriodLayoutUpdate,
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = company_id_for_user(user)
    filter_fields = normalize_filter_fields(payload.filter_fields)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        if period["status"] == "closed":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Closed surveys cannot be edited")

        selected = [item for item in payload.items if item.in_survey]
        if not selected:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select at least one survey item")

        catalog_ids = [item.catalog_item_id for item in selected]
        placeholders = ", ".join("?" for _ in catalog_ids)
        catalog_rows = execute(
            conn,
            f"""
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ? AND id IN ({placeholders})
            """,
            (company_id, True, *catalog_ids),
        ).fetchall()
        catalog_by_id = {row["id"]: row for row in catalog_rows}
        if len(catalog_by_id) != len(set(catalog_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more catalog items were not found")

        execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (period_id,))
        for index, item in enumerate(selected, start=1):
            catalog = catalog_by_id[item.catalog_item_id]
            execute(
                conn,
                """
                INSERT INTO survey_period_items (
                    survey_period_id, catalog_item_id, item_name, brand, channel, product_type,
                    key_program, item_description, uom, price, image_url, image_display_size,
                    image_fit, sort_order, response_width, page_break_before
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id,
                    catalog["id"],
                    catalog["item_name"],
                    catalog["brand"],
                    catalog["channel"],
                    catalog["product_type"],
                    catalog["key_program"],
                    catalog["item_description"],
                    catalog["uom"],
                    catalog["price"],
                    catalog["image_url"],
                    item.image_display_size,
                    item.image_fit,
                    index,
                    item.response_width,
                    1 if item.page_break_before and index > 1 else 0,
                ),
            )

        # Keep buy-book / catalog survey flags in sync for items touched in this save.
        for item in payload.items:
            execute(
                conn,
                """
                UPDATE survey_items
                SET in_survey = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND company_id = ?
                """,
                (1 if item.in_survey else 0, item.catalog_item_id, company_id),
            )

        execute(
            conn,
            """
            UPDATE survey_periods
            SET filter_fields_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (json.dumps(filter_fields), period_id, company_id),
        )
        period = get_survey_period(conn, company_id, period_id)
        items = list_period_items(conn, period_id)

    return {
        "period": survey_period_to_public(period, item_count=len(items)),
        "filter_fields": filter_fields,
        "items": [period_item_to_public(row) for row in items],
    }


@router.delete("/survey-periods/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_survey_period(period_id: int, user: sqlite3.Row = Depends(get_current_user)) -> Response:
    company_id = company_id_for_user(user)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        if period["status"] == "open":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Close the survey before deleting it")
        response_count = execute(
            conn,
            "SELECT COUNT(*) AS count FROM survey_responses WHERE survey_period_id = ?",
            (period_id,),
        ).fetchone()["count"]
        if response_count:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Surveys with responses cannot be deleted. Close them instead.",
            )
        execute(conn, "DELETE FROM survey_periods WHERE id = ? AND company_id = ?", (period_id, company_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Survey responses export
# ---------------------------------------------------------------------------

@router.get("/survey-responses/export")
def export_survey_responses(period_id: int | None = None, user: sqlite3.Row = Depends(get_current_user)) -> Response:
    company_id = company_id_for_user(user)
    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        args: list[Any] = [company_id]
        period_clause = ""
        period_name = "all"
        if period_id is not None:
            period = get_survey_period(conn, company_id, period_id)
            if period is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
            period_clause = " AND survey_responses.survey_period_id = ?"
            args.append(period_id)
            period_name = str(period["name"]).replace(" ", "-").lower()

        rows = execute(
            conn,
            f"""
            SELECT
                survey_responses.created_at,
                COALESCE(survey_items.item_name, '') AS item_name,
                COALESCE(survey_items.brand, '') AS brand,
                COALESCE(survey_items.channel, '') AS channel,
                COALESCE(survey_items.product_type, '') AS product_type,
                COALESCE(survey_items.key_program, '') AS key_program,
                COALESCE(survey_items.uom, '') AS uom,
                COALESCE(survey_items.price, '') AS price,
                survey_responses.email,
                survey_responses.recommend_rollout,
                survey_responses.feedback
            FROM survey_responses
            LEFT JOIN survey_items ON survey_items.id = survey_responses.survey_item_id
            WHERE survey_responses.company_id = ?{period_clause}
            ORDER BY survey_responses.created_at DESC, survey_responses.id DESC
            """,
            tuple(args),
        ).fetchall()

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Survey Responses"
    headers = ["Submitted At", "Item Name", "Brand", "Channel", "Product Type",
               "Key Program", "UOM", "Price", "Email", "Interest", "Notes"]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        worksheet.append([
            str(row["created_at"] or ""), row["item_name"], row["brand"],
            row["channel"], row["product_type"], row["key_program"], row["uom"], row["price"],
            row["email"],
            "Interested" if row["recommend_rollout"] == "Yes" else "Not Interested",
            row["feedback"],
        ])
    for column_cells in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 55)

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"survey-responses-{period_name}-{utc_now().date().isoformat()}.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

@router.get("/companies")
def list_companies(user: sqlite3.Row = Depends(require_admin)) -> dict:
    del user
    with connect() as conn:
        rows = execute(conn, "SELECT * FROM companies ORDER BY name ASC, id ASC").fetchall()
    return {"companies": [company_to_public(row) for row in rows]}


@router.patch("/companies/{company_id}")
def update_company(company_id: int, payload: CompanyUpdate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    del user
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        updates["name"] = updates["name"].strip()
    if "logo_url" in updates and updates["logo_url"] is not None:
        updates["logo_url"] = updates["logo_url"].strip()
    if "banner_url" in updates and updates["banner_url"] is not None:
        updates["banner_url"] = updates["banner_url"].strip()
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No company fields supplied")

    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [company_id]
    with connect() as conn:
        cursor = execute(
            conn,
            f"UPDATE companies SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
        row = execute(conn, "SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return {"company": company_to_public(row)}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@router.get("/users")
def list_users(user: sqlite3.Row = Depends(require_admin)) -> dict:
    del user
    with connect() as conn:
        rows = execute(conn, "SELECT * FROM users ORDER BY created_at DESC, id DESC").fetchall()
        return {"users": [serialize_user(conn, row) for row in rows]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = payload.company_id if payload.company_id is not None else company_id_for_user(user)
    try:
        with connect() as conn:
            company = execute(conn, "SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
            if company is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Company not found")
            values = (
                str(payload.email).lower(),
                payload.name,
                hash_password(payload.password),
                payload.role,
                payload.is_active,
                company_id,
            )
            sql = (
                "INSERT INTO users (email, name, password_hash, role, is_active, company_id) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            if IS_POSTGRES:
                row = execute(conn, with_returning(sql), values).fetchone()
            else:
                cursor = execute(conn, sql, values)
                row = execute(conn, "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return {"user": serialize_user(conn, row)}
    except HTTPException:
        raise
    except Exception as exc:
        if "UNIQUE" in str(exc) or "unique" in str(exc) or "duplicate" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")
        raise


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    if user_id == user["id"] and payload.is_active is False:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account")

    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates and updates["email"] is not None:
        updates["email"] = str(updates["email"]).lower()
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No user fields supplied")

    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [user_id]
    try:
        with connect() as conn:
            if "company_id" in updates and updates["company_id"] is not None:
                company = execute(
                    conn,
                    "SELECT id FROM companies WHERE id = ?",
                    (updates["company_id"],),
                ).fetchone()
                if company is None:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Company not found")
            cursor = execute(
                conn,
                f"UPDATE users SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
            row = execute(conn, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return {"user": serialize_user(conn, row)}
    except HTTPException:
        raise
    except Exception as exc:
        if "UNIQUE" in str(exc) or "unique" in str(exc) or "duplicate" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "A user with that email already exists")
        raise


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, payload: PasswordReset, user: sqlite3.Row = Depends(require_admin)) -> dict:
    del user
    with connect() as conn:
        cursor = execute(
            conn,
            "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hash_password(payload.password), user_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        execute(conn, "DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Kanban columns
# ---------------------------------------------------------------------------

@router.get("/kanban-columns")
def list_admin_kanban_columns(user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM kanban_columns WHERE company_id = ? ORDER BY sort_order ASC, id ASC",
            (company_id,),
        ).fetchall()
    return {"columns": [kanban_column_to_public(row) for row in rows]}


@router.post("/kanban-columns", status_code=status.HTTP_201_CREATED)
def create_kanban_column(payload: KanbanColumnCreate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    values = (company_id, payload.title.strip(), json.dumps(payload.statuses), payload.sort_order, payload.is_visible)
    sql = "INSERT INTO kanban_columns (company_id, title, statuses_json, sort_order, is_visible) VALUES (?, ?, ?, ?, ?)"
    with connect() as conn:
        if IS_POSTGRES:
            row = execute(conn, with_returning(sql), values).fetchone()
        else:
            cursor = execute(conn, sql, values)
            row = execute(conn, "SELECT * FROM kanban_columns WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {"column": kanban_column_to_public(row)}


@router.patch("/kanban-columns/{column_id}")
def update_kanban_column(column_id: int, payload: KanbanColumnUpdate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    if "title" in updates and updates["title"] is not None:
        updates["title"] = updates["title"].strip()
    if "statuses" in updates:
        updates["statuses_json"] = json.dumps(updates.pop("statuses") or [])
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No kanban column fields supplied")

    company_id = company_id_for_user(user)
    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [column_id, company_id]
    with connect() as conn:
        cursor = execute(
            conn,
            f"UPDATE kanban_columns SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanban column not found")
        row = execute(conn, "SELECT * FROM kanban_columns WHERE id = ? AND company_id = ?",
                      (column_id, company_id)).fetchone()
    return {"column": kanban_column_to_public(row)}


@router.delete("/kanban-columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kanban_column(column_id: int, response: Response, user: sqlite3.Row = Depends(require_admin)) -> Response:
    company_id = company_id_for_user(user)
    with connect() as conn:
        cursor = execute(conn, "DELETE FROM kanban_columns WHERE id = ? AND company_id = ?",
                         (column_id, company_id))
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanban column not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# Sheet statuses
# ---------------------------------------------------------------------------

@router.get("/sheet-statuses")
def list_sheet_statuses(user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM sheet_statuses WHERE company_id = ? ORDER BY sheet ASC, sort_order ASC, id ASC",
            (company_id,),
        ).fetchall()
    return {"statuses": [sheet_status_to_public(row) for row in rows]}


@router.post("/sheet-statuses", status_code=status.HTTP_201_CREATED)
def create_sheet_status(payload: SheetStatusCreate, user: sqlite3.Row = Depends(require_admin)) -> dict:
    if payload.sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")
    status_label = payload.status.strip()
    if not status_label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Status is required")

    company_id = company_id_for_user(user)
    try:
        with connect() as conn:
            max_sort = execute(
                conn,
                "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM sheet_statuses WHERE company_id = ? AND sheet = ?",
                (company_id, payload.sheet),
            ).fetchone()["max_sort"]
            values = (company_id, payload.sheet, status_label, max_sort + 1)
            sql = "INSERT INTO sheet_statuses (company_id, sheet, status, sort_order) VALUES (?, ?, ?, ?)"
            if IS_POSTGRES:
                row = execute(conn, with_returning(sql), values).fetchone()
            else:
                cursor = execute(conn, sql, values)
                row = execute(conn, "SELECT * FROM sheet_statuses WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except Exception as exc:
        if "UNIQUE" in str(exc) or "unique" in str(exc) or "duplicate" in str(exc).lower():
            raise HTTPException(status.HTTP_409_CONFLICT, "That status already exists for this sheet")
        raise
    return {"status": sheet_status_to_public(row)}


@router.delete("/sheet-statuses/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sheet_status(status_id: int, response: Response, user: sqlite3.Row = Depends(require_admin)) -> Response:
    company_id = company_id_for_user(user)
    with connect() as conn:
        cursor = execute(conn, "DELETE FROM sheet_statuses WHERE id = ? AND company_id = ?",
                         (status_id, company_id))
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Status not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs")
def list_audit_logs(user: sqlite3.Row = Depends(require_admin)) -> dict:
    company_id = company_id_for_user(user)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM audit_logs WHERE company_id = ? ORDER BY created_at DESC, id DESC LIMIT 250",
            (company_id,),
        ).fetchall()
    return {"logs": [audit_log_to_public(row) for row in rows]}
