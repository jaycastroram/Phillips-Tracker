from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, status

from config import IS_POSTGRES
from database import (
    apply_buy_book_layers,
    buy_book_filter_fields,
    buy_book_page_break_layers,
    company_by_public_book_token,
    company_to_public,
    connect,
    ensure_default_company,
    execute,
    fetch_company_by_slug,
    get_default_company,
    get_survey_period_by_token,
    kanban_column_to_public,
    list_period_items,
    mirror_legacy_response_fields,
    parse_period_filter_fields,
    parse_survey_questions,
    resolve_interest_question,
    period_item_to_public,
    row_to_item,
    survey_filter_fields,
    survey_item_to_public,
    survey_period_is_closed,
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

def _resolve_survey_period(conn: Any, company_id: int, token: str) -> Any:
    """The period this token names inside this company, or the right refusal.

    No token is 401 — the caller has no credential at all. A token that names
    nothing here is 404, and unknown, rotated and another company's token get
    the same answer so a bad guess learns nothing about other tenants.
    """
    text = str(token or "").strip()
    if not text:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "survey_token_required")
    period = get_survey_period_by_token(conn, company_id, text)
    if period is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey not found")
    return period


def _survey_item_matches(item: dict, needle: str) -> bool:
    return any(
        needle in str(item.get(key) or "").lower()
        for key in ("item_name", "brand", "channel", "product_type", "key_program", "item_description")
    )


def _survey_items_response(company_id: int, q: str, surface: str, token: str = "") -> dict:
    if surface not in {"", "survey"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown survey surface")

    with connect() as conn:
        # A page view never creates a period: nothing a stranger opens mints a
        # live token. The admin Survey Periods read is where a legacy period
        # comes from now.
        period = _resolve_survey_period(conn, company_id, token)
        questions = parse_survey_questions(period["questions_json"])
        if survey_period_is_closed(period):
            return {
                "items": [],
                "filter_fields": [],
                "questions": questions,
                "period": survey_period_to_public(period),
                "survey_open": False,
                "message": "This survey is closed.",
            }
        if surface == "survey":
            rows = list_period_items(conn, period["id"])
            fields = parse_period_filter_fields(period["filter_fields_json"])
            items = [period_item_to_public(row) for row in rows]
        else:
            # The bare form stays, behind the same token: the SPA does not call
            # it, and a caller that does should fail loudly rather than read
            # the whole active catalog anonymously.
            rows = execute(
                conn,
                """
                SELECT * FROM survey_items
                WHERE company_id = ? AND is_active = ?
                ORDER BY survey_sort_order ASC, sort_order ASC, id ASC
                """,
                (company_id, True),
            ).fetchall()
            fields = survey_filter_fields(conn, company_id)
            items = [survey_item_to_public(row) for row in rows]

    needle = q.strip().lower()
    if needle:
        items = [item for item in items if _survey_item_matches(item, needle)]
    return {
        "items": items,
        "filter_fields": fields,
        "questions": questions,
        "period": survey_period_to_public(period),
        "survey_open": True,
    }


# ---------------------------------------------------------------------------
# Public Buy Book (read-only, one token per company)
# ---------------------------------------------------------------------------

def _public_book_payload(company_id: int) -> dict:
    """The book as a stranger may see it: items, order, layer breaks. Read only."""
    with connect() as conn:
        rows = execute(
            conn,
            """
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ? AND in_buy_book = ?
            ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC
            """,
            (company_id, True, True),
        ).fetchall()
        fields = buy_book_filter_fields(conn, company_id)
        layers = buy_book_page_break_layers(conn, company_id)
        company = execute(
            conn,
            "SELECT * FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
    items = apply_buy_book_layers([survey_item_to_public(row, surface="buy-book") for row in rows], layers)
    return {
        "company": company_to_public(company),
        "items": items,
        "filter_fields": fields,
        "page_break_layers": layers,
    }


@router.get("/book/{token}")
def get_public_buy_book(token: str) -> dict:
    with connect() as conn:
        company = company_by_public_book_token(conn, token)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Book not found")
    return _public_book_payload(int(company["id"]))


@router.get("/survey-items")
def list_public_survey_items(q: str = "", surface: str = "", t: str = "") -> dict:
    return _survey_items_response(_default_company_id(), q, surface, t)


@router.get("/{company_slug}/survey-items")
def list_public_company_survey_items(
    company_slug: str,
    q: str = "",
    surface: str = "",
    t: str = "",
) -> dict:
    return _survey_items_response(_get_company(company_slug)["id"], q, surface, t)


# ---------------------------------------------------------------------------
# Survey responses
# ---------------------------------------------------------------------------

def _create_survey_response(company_id: int, payload: SurveyResponseCreate, token: str) -> dict:
    with connect() as conn:
        period = _resolve_survey_period(conn, company_id, token)
        if survey_period_is_closed(period):
            raise HTTPException(status.HTTP_410_GONE, "survey_closed")

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

        # Back-compat: an older client sends interest/notes as legacy fields only.
        # Its Yes/No is translated into the survey's own interest question (first
        # option = yes, second = no), so a renamed or relabelled question still
        # accepts it instead of rejecting a value it never offered.
        if not answers:
            interest_question = resolve_interest_question(questions)
            if payload.recommend_rollout in {"Yes", "No"}:
                if interest_question is not None:
                    options = interest_question.get("options") or []
                    answers[interest_question["id"]] = (
                        options[0] if payload.recommend_rollout == "Yes" and options
                        else options[1] if payload.recommend_rollout == "No" and len(options) > 1
                        else payload.recommend_rollout
                    )
                else:
                    answers["interest"] = payload.recommend_rollout
            if payload.feedback.strip():
                text_question = next((q for q in questions if q["type"] == "text"), None)
                answers[(text_question or {"id": "feedback"})["id"]] = payload.feedback.strip()

        for question in questions:
            raw = answers.get(question["id"])

            if question["type"] == "checkbox":
                # Multi-select: a list, validated element by element.
                picked = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
                selected: list[str] = []
                for value in picked:
                    value_text = str(value).strip()
                    if not value_text:
                        continue
                    if question["options"] and value_text not in question["options"]:
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            f"Invalid option for {question['label']}: {value_text}",
                        )
                    if value_text not in selected:
                        selected.append(value_text)
                if question["required"] and not selected:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Please answer: {question['label']}",
                    )
                if selected:
                    answers[question["id"]] = selected
                else:
                    answers.pop(question["id"], None)
                continue

            unanswered = raw is None or isinstance(raw, list) or str(raw).strip() == ""
            if question["required"] and unanswered:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Please answer: {question['label']}",
                )
            if unanswered:
                continue

            if question["type"] == "scale":
                try:
                    scale = int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid scale for {question['label']}")
                if scale < 1 or scale > 5:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Scale must be 1-5 for {question['label']}")
                answers[question["id"]] = scale
            elif question["type"] in {"yes_no", "single_choice", "dropdown"}:
                option_text = str(raw).strip()
                if question["options"] and option_text not in question["options"]:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Invalid option for {question['label']}",
                    )
                answers[question["id"]] = option_text
            elif question["type"] == "text":
                answers[question["id"]] = str(raw).strip()

        recommend, feedback, attention, retail, stands_out, price_reasonable = mirror_legacy_response_fields(
            questions,
            answers,
        )
        if payload.feedback.strip() and not feedback:
            feedback = payload.feedback.strip()

        email = str(payload.email).lower()
        answers_json = json.dumps(answers)
        # Last write wins: the unique index is on (company, period, item,
        # lower(email)), so a second answer for the same shopper is an update
        # of the one row, never a second row.
        key_args = (company_id, period["id"], payload.survey_item_id, email)
        existing = execute(
            conn,
            """
            SELECT id FROM survey_responses
            WHERE company_id = ? AND survey_period_id = ? AND survey_item_id = ? AND lower(email) = ?
            """,
            key_args,
        ).fetchone()
        if existing is not None:
            execute(
                conn,
                """
                UPDATE survey_responses
                SET email = ?, attention_effectiveness = ?, recommend_rollout = ?,
                    retail_engagement = ?, stands_out = ?, price_reasonable = ?,
                    feedback = ?, answers_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (email, attention, recommend, retail, stands_out, price_reasonable, feedback, answers_json, existing["id"]),
            )
            row = execute(conn, "SELECT * FROM survey_responses WHERE id = ?", (existing["id"],)).fetchone()
        else:
            values = (
                company_id,
                period["id"],
                payload.survey_item_id,
                email,
                attention,
                recommend,
                retail,
                stands_out,
                price_reasonable,
                feedback,
                answers_json,
            )
            sql = """
                INSERT INTO survey_responses (
                    company_id, survey_period_id, survey_item_id, email, attention_effectiveness, recommend_rollout,
                    retail_engagement, stands_out, price_reasonable, feedback, answers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            try:
                if IS_POSTGRES:
                    row = execute(conn, with_returning(sql), values).fetchone()
                else:
                    cursor = execute(conn, sql, values)
                    row = execute(conn, "SELECT * FROM survey_responses WHERE id = ?", (cursor.lastrowid,)).fetchone()
            except Exception:
                # A parallel submit from the same shopper won the insert; take
                # it as an update rather than a 500.
                row = execute(
                    conn,
                    """
                    SELECT * FROM survey_responses
                    WHERE company_id = ? AND survey_period_id = ? AND survey_item_id = ? AND lower(email) = ?
                    """,
                    key_args,
                ).fetchone()
                if row is None:
                    raise
    return {"response": survey_response_to_public(row)}


@router.post("/survey-responses")
def create_public_survey_response(payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_default_company_id(), payload, t)


@router.post("/{company_slug}/survey-responses")
def create_public_company_survey_response(company_slug: str, payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_get_company(company_slug)["id"], payload, t)
