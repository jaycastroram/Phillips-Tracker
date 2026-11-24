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
    get_open_survey_period,
    get_survey_period_by_token,
    kanban_column_to_public,
    find_submission,
    list_period_items,
    mirror_legacy_response_fields,
    period_item_question_map,
    parse_period_filter_fields,
    parse_survey_questions,
    questions_for_item,
    resolve_interest_question,
    row_value,
    period_item_to_public,
    row_to_item,
    save_submission_answers,
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


def _default_company() -> Any:
    with connect() as conn:
        return ensure_default_company(conn)


def _default_company_id() -> int:
    return _default_company()["id"]


def _legacy_public_link(company: Any, column: str) -> bool:
    """Whether this company still serves the pre-0013 open link.

    The column defaults to on for every company that existed before it was
    added; a row read before the migration also answers on, so the flag can
    never close a link by accident.
    """
    return bool(row_value(company, column, True))


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


def _period_survey_payload(conn: Any, period: Any) -> dict:
    """One survey as the public page reads it: each item with its own questions.

    The pool is the survey's own definition of its questions; the item carries
    the ones it asks, in pool order, so the page never has to work out which
    tick belongs where.
    """
    pool = parse_survey_questions(period["questions_json"])
    if survey_period_is_closed(period):
        return {
            "items": [],
            "filter_fields": [],
            "questions": pool,
            "period": survey_period_to_public(period),
            "survey_open": False,
            "message": "This survey is closed.",
        }
    ticks = period_item_question_map(conn, int(period["id"]))
    items = [
        period_item_to_public(row, questions=questions_for_item(pool, ticks.get(int(row["id"]), [])))
        for row in list_period_items(conn, int(period["id"]))
    ]
    fields = parse_period_filter_fields(period["filter_fields_json"])
    return {
        "items": items,
        "filter_fields": fields,
        "questions": pool,
        "period": survey_period_to_public(period),
        "survey_open": True,
    }


def _bare_catalog_payload(conn: Any, company_id: int) -> dict:
    """The bare token-gated form: the whole active catalog, as 128aa1e read it.

    Nothing embeds this and the SPA never calls it; it stays behind the same
    token so an existing caller keeps the answer it had, no wider.
    """
    rows = execute(
        conn,
        """
        SELECT * FROM survey_items
        WHERE company_id = ? AND is_active = ?
        ORDER BY survey_sort_order ASC, sort_order ASC, id ASC
        """,
        (company_id, True),
    ).fetchall()
    return {
        "items": [survey_item_to_public(row) for row in rows],
        "filter_fields": survey_filter_fields(conn, company_id),
    }


def _no_open_survey_payload() -> dict:
    """The pre-change answer when nothing is open: a closed page, not an error."""
    return {
        "items": [],
        "filter_fields": [],
        "questions": [],
        "period": None,
        "survey_open": False,
        "message": "No survey is open right now.",
    }


def _legacy_buy_book_payload(company_id: int, q: str) -> dict:
    """The buy book exactly as 128aa1e served it, behind the company flag.

    Restored from the pre-0013 branch: active items marked for the buy book, in
    buy-book order, with the filtered fields and the layer stack the page draws
    its headings and breaks from. No token is read — the key never gated it.
    """
    clauses = ["company_id = ?", "is_active = ?", "in_buy_book = ?"]
    args: list[Any] = [company_id, True, 1]
    if q.strip():
        like = f"%{q.strip().lower()}%"
        clauses.append(
            "(lower(item_name) LIKE ? OR lower(brand) LIKE ? OR lower(channel) LIKE ? OR "
            "lower(product_type) LIKE ? OR lower(key_program) LIKE ? OR lower(item_description) LIKE ?)"
        )
        args.extend([like] * 6)
    with connect() as conn:
        rows = execute(
            conn,
            f"SELECT * FROM survey_items WHERE {' AND '.join(clauses)} "
            "ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC",
            args,
        ).fetchall()
        fields = buy_book_filter_fields(conn, company_id)
        layers = buy_book_page_break_layers(conn, company_id)
    group_by = layers[0]["field"] if layers else "brand"
    items = apply_buy_book_layers(
        [survey_item_to_public(row, surface="buy-book") for row in rows],
        layers,
    )
    return {
        "items": items,
        "filter_fields": fields,
        "group_by": group_by,
        "page_break_layers": layers,
    }


def _survey_items_response(company: Any, q: str, surface: str, token: str = "") -> dict:
    if surface not in {"", "survey", "buy-book"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown survey surface")

    company_id = int(company["id"])

    if surface == "buy-book":
        if not _legacy_public_link(company, "legacy_public_buy_book"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Buy book not found")
        return _legacy_buy_book_payload(company_id, q)

    text = str(token or "").strip()
    if text:
        # A supplied token always decides: bogus, rotated and another company's
        # tokens 404, and never fall back to the open period. The anonymous
        # link below is only for the caller who presents no token at all.
        with connect() as conn:
            period = _resolve_survey_period(conn, company_id, text)
            if surface == "" and not survey_period_is_closed(period):
                payload = _bare_catalog_payload(conn, company_id)
            else:
                payload = _period_survey_payload(conn, period)
    elif surface == "survey" and _legacy_public_link(company, "legacy_public_survey"):
        # The restored legacy link: no token needed, but only an open period
        # that has not passed its close time — get_open_survey_period is the
        # one resolver that enforces both. A page view never mints a period.
        with connect() as conn:
            period = get_open_survey_period(conn, company_id)
            payload = _no_open_survey_payload() if period is None else _period_survey_payload(conn, period)
    else:
        # The bare catalog form stays behind the token: nothing embeds it, the
        # SPA never calls it, and it returns the whole active catalog.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "survey_token_required")

    needle = q.strip().lower()
    if needle:
        payload["items"] = [item for item in payload["items"] if _survey_item_matches(item, needle)]
    return payload


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
            # in_buy_book is an INTEGER column on Postgres; a bool here is a type error.
            (company_id, True, 1),
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
    # k= is accepted and ignored: no column on this side ever gated the book.
    return _survey_items_response(_default_company(), q, surface, t)


@router.get("/{company_slug}/survey-items")
def list_public_company_survey_items(
    company_slug: str,
    q: str = "",
    surface: str = "",
    t: str = "",
) -> dict:
    return _survey_items_response(_get_company(company_slug), q, surface, t)


# ---------------------------------------------------------------------------
# Survey responses
# ---------------------------------------------------------------------------

def _clean_answer(question: dict[str, Any], raw: Any) -> Any:
    """One answer, validated against the question that asked it.

    Returns None for "not answered" (an optional question left blank), so the
    caller can drop it instead of storing an empty row.
    """
    if question["type"] == "checkbox":
        picked = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
        selected: list[str] = []
        for value in picked:
            text = str(value).strip()
            if not text:
                continue
            if question["options"] and text not in question["options"]:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Invalid option for {question['label']}: {text}",
                )
            if text not in selected:
                selected.append(text)
        if question["required"] and not selected:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Please answer: {question['label']}")
        return selected or None

    if raw is None or isinstance(raw, list) or str(raw).strip() == "":
        if question["required"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Please answer: {question['label']}")
        return None

    if question["type"] == "scale":
        try:
            scale = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid scale for {question['label']}") from None
        if scale < 1 or scale > 5:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Scale must be 1-5 for {question['label']}"
            )
        return scale
    if question["type"] in {"yes_no", "single_choice", "dropdown"}:
        option_text = str(raw).strip()
        if question["options"] and option_text not in question["options"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid option for {question['label']}")
        return option_text
    return str(raw).strip()


def _submission_answers(payload: SurveyResponseCreate) -> dict[int, dict[str, Any]]:
    """The payload as ``{item_id: {question_id: value}}``, in either body shape.

    The current body keys answers by item. The pre-item-form body names one
    item and keys its answers by question; that is read as a one-item
    submission, so an old embedded form keeps working.
    """
    if payload.survey_item_id is not None:
        return {int(payload.survey_item_id): dict(payload.answers or {})}
    answers: dict[int, dict[str, Any]] = {}
    for key, value in (payload.answers or {}).items():
        try:
            item_id = int(key)
        except (TypeError, ValueError):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Invalid survey item id: {key}"
            ) from None
        if not isinstance(value, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Answers must be keyed by item then question")
        answers[item_id] = dict(value)
    return answers


def _create_survey_response(company: Any, payload: SurveyResponseCreate, token: str) -> dict:
    """One respondent, every item they answered, one submission row.

    Answers land one row per item x question, so a survey whose items ask
    different questions needs no per-item form posts and no shared answer blob.
    A second submission from the same email updates the first: the unique index
    on (company, survey, lower(email)) is the rule, and the route reads it
    rather than relying on a client to behave.
    """
    company_id = int(company["id"])
    text = str(token or "").strip()
    with connect() as conn:
        if text:
            # A supplied token always wins: a bad one is refused here, never
            # quietly answered from the company's open period.
            period = _resolve_survey_period(conn, company_id, text)
            if survey_period_is_closed(period):
                raise HTTPException(status.HTTP_410_GONE, "survey_closed")
        elif _legacy_public_link(company, "legacy_public_survey"):
            # The legacy anonymous form: the same open-and-not-past-close
            # resolver as the read, so a window that closed collects nothing.
            period = get_open_survey_period(conn, company_id)
            if period is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "No survey is open right now")
        else:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "survey_token_required")

        pool = parse_survey_questions(period["questions_json"])
        ticks = period_item_question_map(conn, int(period["id"]))
        items = {
            int(row["id"]): {
                "row": row,
                "questions": questions_for_item(pool, ticks.get(int(row["id"]), [])),
            }
            for row in list_period_items(conn, int(period["id"]))
        }

        submitted = _submission_answers(payload)
        if not submitted:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No answers were submitted")
        by_question: dict[int, dict[str, Any]] = {}
        for item_id, raw_answers in submitted.items():
            item = items.get(item_id)
            if item is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey item not found in the open survey")
            asked = {question["id"]: question for question in item["questions"]}
            cleaned: dict[str, Any] = {}
            for question_id, value in raw_answers.items():
                question = asked.get(str(question_id))
                if question is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "This survey changed while it was open. Reload the page and answer again.",
                    )
                answer = _clean_answer(question, value)
                if answer is not None:
                    cleaned[str(question_id)] = answer
            for question in item["questions"]:
                if question["required"] and question["id"] not in cleaned:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, f"Please answer: {question['label']}"
                    )
            by_question[item_id] = cleaned

        email = str(payload.email or "").strip().lower()
        # The legacy columns are per response and the header now covers many
        # items, so they mirror the first item that was answered - nothing reads
        # them for a new survey, and the (item, question) answers are the record.
        first_item_id = next(iter(by_question))
        interest, feedback, attention, retail, stands_out, price_reasonable = mirror_legacy_response_fields(
            items[first_item_id]["questions"],
            by_question[first_item_id],
        )
        if payload.feedback.strip() and not feedback:
            feedback = payload.feedback.strip()

        existing = find_submission(conn, company_id, int(period["id"]), email) if email else None
        if existing is not None:
            execute(
                conn,
                """
                UPDATE survey_responses
                SET attention_effectiveness = ?, recommend_rollout = ?, retail_engagement = ?,
                    stands_out = ?, price_reasonable = ?, feedback = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (attention, interest, retail, stands_out, price_reasonable, feedback, existing["id"]),
            )
            response_id = int(existing["id"])
        else:
            values = (
                company_id,
                period["id"],
                email,
                attention,
                interest,
                retail,
                stands_out,
                price_reasonable,
                feedback,
            )
            sql = """
                INSERT INTO survey_responses (
                    company_id, survey_period_id, survey_item_id, email, attention_effectiveness,
                    recommend_rollout, retail_engagement, stands_out, price_reasonable, feedback, answers_json
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, '{}')
            """
            try:
                if IS_POSTGRES:
                    row = execute(conn, with_returning(sql), values).fetchone()
                    response_id = int(row["id"])
                else:
                    cursor = execute(conn, sql, values)
                    response_id = int(cursor.lastrowid)
            except Exception:
                # A parallel submit from the same shopper won the insert; take
                # it as an update rather than a 500.
                row = find_submission(conn, company_id, int(period["id"]), email)
                if row is None:
                    raise
                response_id = int(row["id"])

        answered = 0
        for item_id, answers in by_question.items():
            answered += save_submission_answers(conn, response_id, item_id, answers)

        row = execute(conn, "SELECT * FROM survey_responses WHERE id = ?", (response_id,)).fetchone()
    result = survey_response_to_public(row)
    result["item_count"] = len(by_question)
    result["answer_count"] = answered
    return {"response": result}


@router.post("/survey-responses")
def create_public_survey_response(payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_default_company(), payload, t)


@router.post("/{company_slug}/survey-responses")
def create_public_company_survey_response(company_slug: str, payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_get_company(company_slug), payload, t)
