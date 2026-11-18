from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, status

from config import IS_POSTGRES
from database import (
    apply_buy_book_grouping,
    buy_book_filter_fields,
    buy_book_group_by,
    company_to_public,
    connect,
    ensure_default_company,
    ensure_legacy_survey_period,
    execute,
    fetch_company_by_slug,
    get_default_company,
    get_open_survey_period,
    kanban_column_to_public,
    list_period_items,
    mirror_legacy_response_fields,
    parse_period_filter_fields,
    parse_survey_questions,
    period_item_to_public,
    row_to_item,
    survey_filter_fields,
    survey_item_to_public,
    survey_period_to_public,
    survey_response_to_public,
    with_returning,
)
from models import SurveyResponseCreate

router = APIRouter(prefix="/api/public", tags=["public"])


def _get_company(slug: str) -> Any:
    with connect() as conn:
        company = fetch_company_by_slug(conn, slug)
    if company is None or not bool(company["is_active"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company


def _default_company_id() -> int:
    with connect() as conn:
        company = ensure_default_company(conn)
    return company["id"]


# ---------------------------------------------------------------------------
# Company config (branding)
# ---------------------------------------------------------------------------

@router.get("/config")
def get_public_config() -> dict:
    return company_to_public(get_default_company())


@router.get("/{company_slug}/config")
def get_company_config(company_slug: str) -> dict:
    return company_to_public(_get_company(company_slug))


# ---------------------------------------------------------------------------
# Items (viewer)
# ---------------------------------------------------------------------------

def _public_items_response(company_id: int, q: str) -> dict:
    clauses = ["company_id = ?", "sheet IN (?, ?)", "COALESCE(current_status, '') != ?"]
    args: list[Any] = [company_id, "ad-hoc", "buys", "Delivered"]
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(brand) LIKE ? OR lower(program_name) LIKE ? OR "
            "lower(item_name) LIKE ? OR lower(important_notes) LIKE ? OR "
            "lower(mrl_order_number) LIKE ? OR lower(tracking) LIKE ? OR lower(extra_json) LIKE ?)"
        )
        args.extend([like] * 7)
    with connect() as conn:
        rows = execute(
            conn,
            f"SELECT * FROM tracker_items WHERE {' AND '.join(clauses)} ORDER BY sheet ASC, sort_order ASC, id ASC",
            args,
        ).fetchall()
    return {"items": [row_to_item(row) for row in rows]}


@router.get("/items")
def list_public_items(q: str = "") -> dict:
    return _public_items_response(_default_company_id(), q)


@router.get("/{company_slug}/items")
def list_public_company_items(company_slug: str, q: str = "") -> dict:
    return _public_items_response(_get_company(company_slug)["id"], q)


# ---------------------------------------------------------------------------
# Kanban columns
# ---------------------------------------------------------------------------

def _kanban_response(company_id: int) -> dict:
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM kanban_columns WHERE company_id = ? AND is_visible = ? ORDER BY sort_order ASC, id ASC",
            (company_id, True),
        ).fetchall()
    return {"columns": [kanban_column_to_public(row) for row in rows]}


@router.get("/kanban-columns")
def list_public_kanban_columns() -> dict:
    return _kanban_response(_default_company_id())


@router.get("/{company_slug}/kanban-columns")
def list_public_company_kanban_columns(company_slug: str) -> dict:
    return _kanban_response(_get_company(company_slug)["id"])


# ---------------------------------------------------------------------------
# Survey items
# ---------------------------------------------------------------------------

def _survey_items_response(company_id: int, q: str, surface: str) -> dict:
    if surface not in {"", "survey", "buy-book"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown survey surface")

    # Public survey surface reads the currently open period snapshot.
    if surface == "survey":
        with connect() as conn:
            ensure_legacy_survey_period(conn, company_id)
            period = get_open_survey_period(conn, company_id)
            if period is None:
                return {
                    "items": [],
                    "filter_fields": [],
                    "questions": [],
                    "period": None,
                    "survey_open": False,
                    "message": "No survey is open right now.",
                }
            rows = list_period_items(conn, period["id"])
            fields = parse_period_filter_fields(period["filter_fields_json"])
            questions = parse_survey_questions(period["questions_json"])

        items = [period_item_to_public(row) for row in rows]
        if q.strip():
            like = q.strip().lower()
            items = [
                item
                for item in items
                if like in item["item_name"].lower()
                or like in item["brand"].lower()
                or like in item["channel"].lower()
                or like in item["product_type"].lower()
                or like in item["key_program"].lower()
                or like in item["item_description"].lower()
            ]
        return {
            "items": items,
            "filter_fields": fields,
            "questions": questions,
            "period": survey_period_to_public(period),
            "survey_open": True,
        }

    clauses = ["company_id = ?", "is_active = ?"]
    args: list[Any] = [company_id, True]
    if surface == "buy-book":
        clauses.append("in_buy_book = ?")
        args.append(1)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(item_name) LIKE ? OR lower(brand) LIKE ? OR lower(channel) LIKE ? OR "
            "lower(product_type) LIKE ? OR lower(key_program) LIKE ? OR lower(item_description) LIKE ?)"
        )
        args.extend([like] * 6)

    order_by = (
        "buy_book_sort_order ASC, sort_order ASC, id ASC"
        if surface == "buy-book"
        else "sort_order ASC, id ASC"
    )
    with connect() as conn:
        rows = execute(
            conn,
            f"SELECT * FROM survey_items WHERE {' AND '.join(clauses)} ORDER BY {order_by}",
            args,
        ).fetchall()
        if surface == "buy-book":
            fields = buy_book_filter_fields(conn, company_id)
            group_by = buy_book_group_by(conn, company_id)
            items = apply_buy_book_grouping(
                [survey_item_to_public(row, surface="buy-book") for row in rows],
                group_by,
            )
            return {"items": items, "filter_fields": fields, "group_by": group_by}

        fields = survey_filter_fields(conn, company_id)
    return {
        "items": [survey_item_to_public(row, surface=surface or None) for row in rows],
        "filter_fields": fields,
    }


@router.get("/survey-items")
def list_public_survey_items(q: str = "", surface: str = "") -> dict:
    return _survey_items_response(_default_company_id(), q, surface)


@router.get("/{company_slug}/survey-items")
def list_public_company_survey_items(company_slug: str, q: str = "", surface: str = "") -> dict:
    return _survey_items_response(_get_company(company_slug)["id"], q, surface)


# ---------------------------------------------------------------------------
# Survey responses
# ---------------------------------------------------------------------------

def _create_survey_response(company_id: int, payload: SurveyResponseCreate) -> dict:
    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        period = get_open_survey_period(conn, company_id)
        if period is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No survey is open right now")

        period_item = execute(
            conn,
            """
            SELECT id, catalog_item_id
            FROM survey_period_items
            WHERE survey_period_id = ? AND catalog_item_id = ?
            """,
            (period["id"], payload.survey_item_id),
        ).fetchone()
        if period_item is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey item not found in the open survey")

        questions = parse_survey_questions(period["questions_json"])
        answers = {str(key): value for key, value in (payload.answers or {}).items()}

        # Back-compat: older clients only send interest/notes via legacy fields.
        if not answers:
            if payload.recommend_rollout == "Yes":
                answers["interest"] = "Yes"
            elif payload.recommend_rollout == "No":
                answers["interest"] = "No"
            if payload.feedback.strip():
                answers["feedback"] = payload.feedback.strip()

        for question in questions:
            raw = answers.get(question["id"])
            if question["required"] and (raw is None or str(raw).strip() == ""):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Please answer: {question['label']}",
                )
            if question["type"] == "scale" and raw not in (None, ""):
                try:
                    scale = int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid scale for {question['label']}")
                if scale < 1 or scale > 5:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Scale must be 1-5 for {question['label']}")
                answers[question["id"]] = scale
            elif question["type"] in {"yes_no", "single_choice"} and raw not in (None, ""):
                text = str(raw).strip()
                if question["options"] and text not in question["options"]:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Invalid option for {question['label']}",
                    )
                answers[question["id"]] = text
            elif question["type"] == "text" and raw not in (None, ""):
                answers[question["id"]] = str(raw).strip()

        recommend, feedback, attention, retail, stands_out, price_reasonable = mirror_legacy_response_fields(
            questions,
            answers,
        )
        if payload.feedback.strip() and not feedback:
            feedback = payload.feedback.strip()

        values = (
            company_id,
            period["id"],
            payload.survey_item_id,
            str(payload.email).lower(),
            attention,
            recommend,
            retail,
            stands_out,
            price_reasonable,
            feedback,
            json.dumps(answers),
        )
        sql = """
            INSERT INTO survey_responses (
                company_id, survey_period_id, survey_item_id, email, attention_effectiveness, recommend_rollout,
                retail_engagement, stands_out, price_reasonable, feedback, answers_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if IS_POSTGRES:
            row = execute(conn, with_returning(sql), values).fetchone()
        else:
            cursor = execute(conn, sql, values)
            row = execute(conn, "SELECT * FROM survey_responses WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {"response": survey_response_to_public(row)}


@router.post("/survey-responses", status_code=status.HTTP_201_CREATED)
def create_public_survey_response(payload: SurveyResponseCreate) -> dict:
    return _create_survey_response(_default_company_id(), payload)


@router.post("/{company_slug}/survey-responses", status_code=status.HTTP_201_CREATED)
def create_public_company_survey_response(company_slug: str, payload: SurveyResponseCreate) -> dict:
    return _create_survey_response(_get_company(company_slug)["id"], payload)
