from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, status

from buy_book_periods import (
    DEFAULT_COMPANY_NAME,
    DEFAULT_COMPANY_SLUG,
    list_periods as list_buy_in_periods,
    period_item_rows,
    resolve_period_id,
)
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
    insert_survey_submission,
    kanban_column_to_public,
    mirror_legacy_response_fields,
    parse_survey_questions,
    resolve_interest_question,
    row_value,
    row_to_item,
    survey_filter_fields,
    survey_item_to_public,
    survey_period_is_closed,
    survey_period_to_public,
    survey_response_to_public,
    survey_submission_to_public,
    with_returning,
)
from models import SurveyResponseCreate, SurveySubmissionCreate

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
    """The survey as the page reads it: its question list and its state.

    A survey no longer carries catalog items — a respondent answers the
    question list once. ``items`` stays in the payload as an empty list so an
    older client reading the key does not break; nothing fills it in.
    """
    del conn  # the payload no longer needs a database read
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
    return {
        "items": [],
        "filter_fields": [],
        "questions": questions,
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


def _period_book_payload(conn: Any, company_id: int, period_id: str, q: str = "") -> dict:
    """One buy-in period's book: its items, the layer stack, and the period list.

    This is the one place a period becomes items, so both the standalone book
    page and the legacy surface read the same rows in the same order.
    """
    rows = period_item_rows(conn, company_id, period_id)
    fields = buy_book_filter_fields(conn, company_id)
    layers = buy_book_page_break_layers(conn, company_id)
    items = apply_buy_book_layers(
        [survey_item_to_public(row, surface="buy-book") for row in rows],
        layers,
    )
    needle = q.strip().lower()
    if needle:
        items = [
            item for item in items
            if _survey_item_matches(item, needle)
        ]
    group_by = layers[0]["field"] if layers else "brand"
    return {
        "items": items,
        "filter_fields": fields,
        "group_by": group_by,
        "page_break_layers": layers,
        "period_id": period_id,
    }


def _book_period_list() -> list[dict[str, Any]]:
    return list_buy_in_periods(DEFAULT_COMPANY_NAME, DEFAULT_COMPANY_SLUG)


def _legacy_buy_book_payload(company_id: int, q: str, period_id: str | None = None) -> dict:
    """The buy book behind the company's legacy flag, gated to one buy-in period.

    No token is read — the key never gated it. The period is the drop-down on
    the page: with no period data the book is empty rather than the catalog.
    """
    periods = _book_period_list()
    selected = resolve_period_id(period_id, DEFAULT_COMPANY_NAME, DEFAULT_COMPANY_SLUG)
    if selected is None and str(period_id or "").strip():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Buy-in period not found")
    if selected is None:
        with connect() as conn:
            fields = buy_book_filter_fields(conn, company_id)
            layers = buy_book_page_break_layers(conn, company_id)
        return {
            "items": [],
            "filter_fields": fields,
            "group_by": layers[0]["field"] if layers else "brand",
            "page_break_layers": layers,
            "periods": periods,
            "period_id": None,
            "no_period_data": True,
        }
    with connect() as conn:
        payload = _period_book_payload(conn, company_id, selected, q)
    payload["periods"] = periods
    payload["no_period_data"] = not periods
    return payload


def _survey_items_response(
    company: Any,
    q: str,
    surface: str,
    token: str = "",
    period_id: str | None = None,
) -> dict:
    if surface not in {"", "survey", "buy-book"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown survey surface")

    company_id = int(company["id"])

    if surface == "buy-book":
        if not _legacy_public_link(company, "legacy_public_buy_book"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Buy book not found")
        return _legacy_buy_book_payload(company_id, q, period_id)

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

def _public_book_payload(company_id: int, period_id: str | None = None) -> dict:
    """The book as a stranger may see it: one period's items, order, layer breaks."""
    periods = _book_period_list()
    selected = resolve_period_id(period_id, DEFAULT_COMPANY_NAME, DEFAULT_COMPANY_SLUG)
    if selected is None and str(period_id or "").strip():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Buy-in period not found")
    with connect() as conn:
        company = execute(
            conn,
            "SELECT * FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        if selected is None:
            payload = {
                "items": [],
                "filter_fields": buy_book_filter_fields(conn, company_id),
                "page_break_layers": buy_book_page_break_layers(conn, company_id),
            }
        else:
            payload = _period_book_payload(conn, company_id, selected)
    payload["company"] = company_to_public(company)
    payload["periods"] = periods
    payload["period_id"] = selected
    payload["no_period_data"] = not periods
    return payload


@router.get("/book/{token}")
def get_public_buy_book(token: str, period: str = "") -> dict:
    with connect() as conn:
        company = company_by_public_book_token(conn, token)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Book not found")
    return _public_book_payload(int(company["id"]), period)


@router.get("/survey-items")
def list_public_survey_items(q: str = "", surface: str = "", t: str = "", period: str = "") -> dict:
    # k= is accepted and ignored: no column on this side ever gated the book.
    return _survey_items_response(_default_company(), q, surface, t, period)


@router.get("/{company_slug}/survey-items")
def list_public_company_survey_items(
    company_slug: str,
    q: str = "",
    surface: str = "",
    t: str = "",
    period: str = "",
) -> dict:
    return _survey_items_response(_get_company(company_slug), q, surface, t, period)


# ---------------------------------------------------------------------------
# Survey responses
# ---------------------------------------------------------------------------

def _resolve_writable_period(conn: Any, company: Any, token: str) -> Any:
    """The period a submission may be written to, or the right refusal.

    A supplied token always decides: a bad one is refused here, never quietly
    answered from the company's open period. With no token the flagged legacy
    link writes to the company's open, not-yet-closed survey — the same
    resolver as the read, so a window that closed collects nothing.
    """
    company_id = int(company["id"])
    text = str(token or "").strip()
    if text:
        period = _resolve_survey_period(conn, company_id, text)
        if survey_period_is_closed(period):
            raise HTTPException(status.HTTP_410_GONE, "survey_closed")
        return period
    if _legacy_public_link(company, "legacy_public_survey"):
        period = get_open_survey_period(conn, company_id)
        if period is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No survey is open right now")
        return period
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "survey_token_required")


def _clean_answers(
    questions: list[dict[str, Any]],
    payload: Any,
) -> tuple[dict[str, Any], str, str, int, int, str, str]:
    """Validate one submission against the survey's own question list.

    Every rule the survey declares is enforced here once, so the standalone
    submission and the older per-item answer accept exactly the same shapes.
    """
    answers = {str(key): value for key, value in (payload.answers or {}).items()}

    # Back-compat: an older client sends interest/notes as legacy fields only.
    # Its Yes/No is translated into the survey's own interest question (first
    # option = yes, second = no), so a renamed or relabelled question still
    # accepts it instead of rejecting a value it never offered.
    legacy_recommend = str(getattr(payload, "recommend_rollout", "Maybe") or "Maybe")
    legacy_feedback = str(getattr(payload, "feedback", "") or "")
    if not answers:
        interest_question = resolve_interest_question(questions)
        if legacy_recommend in {"Yes", "No"}:
            if interest_question is not None:
                options = interest_question.get("options") or []
                answers[interest_question["id"]] = (
                    options[0] if legacy_recommend == "Yes" and options
                    else options[1] if legacy_recommend == "No" and len(options) > 1
                    else legacy_recommend
                )
            else:
                answers["interest"] = legacy_recommend
        if legacy_feedback.strip():
            text_question = next((q for q in questions if q["type"] == "text"), None)
            answers[(text_question or {"id": "feedback"})["id"]] = legacy_feedback.strip()

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
    if legacy_feedback.strip() and not feedback:
        feedback = legacy_feedback.strip()
    return answers, recommend, feedback, attention, retail, stands_out, price_reasonable


def _create_survey_response(company: Any, payload: SurveyResponseCreate, token: str) -> dict:
    company_id = int(company["id"])
    with connect() as conn:
        period = _resolve_writable_period(conn, company, token)

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
        answers, recommend, feedback, attention, retail, stands_out, price_reasonable = _clean_answers(
            questions, payload
        )

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


def _create_survey_submission(company: Any, payload: SurveySubmissionCreate, token: str) -> dict:
    """Store one filled-in survey — the standalone flow behind the static link."""
    company_id = int(company["id"])
    with connect() as conn:
        period = _resolve_writable_period(conn, company, token)
        questions = parse_survey_questions(period["questions_json"])
        answers, *_legacy = _clean_answers(questions, payload)
        email = str(payload.email or "").strip().lower()
        row = insert_survey_submission(conn, company_id, int(period["id"]), email, answers)
    return {"submission": survey_submission_to_public(row)}


@router.post("/survey-submissions", status_code=status.HTTP_201_CREATED)
def create_public_survey_submission(payload: SurveySubmissionCreate, t: str = "") -> dict:
    return _create_survey_submission(_default_company(), payload, t)


@router.post("/{company_slug}/survey-submissions", status_code=status.HTTP_201_CREATED)
def create_public_company_survey_submission(company_slug: str, payload: SurveySubmissionCreate, t: str = "") -> dict:
    return _create_survey_submission(_get_company(company_slug), payload, t)


@router.post("/survey-responses")
def create_public_survey_response(payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_default_company(), payload, t)


@router.post("/{company_slug}/survey-responses")
def create_public_company_survey_response(company_slug: str, payload: SurveyResponseCreate, t: str = "") -> dict:
    return _create_survey_response(_get_company(company_slug), payload, t)
