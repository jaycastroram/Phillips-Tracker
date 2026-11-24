from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Font

from config import IS_POSTGRES, NETSUITE_SYNC_COMPANY_SLUG, SHEETS
from database import (
    public_book_token_for,
    rotate_public_book_token,
    MAX_PAGE_BREAK_LAYERS,
    fetch_company_by_id,
    buy_book_filter_fields,
    buy_book_group_by,
    buy_book_page_break_layers,
    normalize_page_break_layers,
    classify_interest_answer,
    close_open_survey_periods,
    company_id_for_user,
    company_to_public,
    connect,
    copy_period_items,
    count_period_items,
    ensure_buy_book_layout,
    ensure_legacy_survey_period,
    ensure_survey_layout,
    execute,
    get_survey_period,
    hash_password,
    kanban_column_to_public,
    list_period_items,
    mint_survey_token,
    normalize_buy_book_group_by,
    normalize_filter_fields,
    normalize_survey_questions,
    normalize_timestamp,
    parse_answers_json,
    parse_cover_json,
    parse_survey_questions,
    period_item_question_map,
    period_items_public,
    period_response_count,
    questions_for_item,
    resolve_interest_question,
    resolve_scoped_company_id,
    save_period_items,
    sheet_status_to_public,
    survey_filter_fields,
    survey_item_to_public,
    survey_layout_questions,
    survey_period_to_public,
    survey_questions_json,
    utc_now,
    row_value,
    serialize_user,
    with_returning,
    audit_log_to_public,
)
from models import (
    BuyBookLayoutUpdate,
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
from catalog_sync import last_run, reset_to_netsuite, sync_catalog
from netsuite import SourceError
from routes.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


def normalize_period_timestamp(field: str, value: Any) -> str | None:
    """What a client sent for a period's window, or None for "unset".

    A blank means cleared (see ``normalize_timestamp``); anything else has to be
    a real ISO-8601 instant. Without the parse test a typo lands in the column
    and only Postgres complains - later, at read time, as a 500 on a page that
    has nothing to do with the edit.
    """
    text = normalize_timestamp(value)
    if text is None:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{field} must be an ISO-8601 timestamp"
        ) from None
    return text


# ---------------------------------------------------------------------------
# Survey results dashboard
# ---------------------------------------------------------------------------

CHOICE_QUESTION_KINDS = {"yes_no", "single_choice", "dropdown"}


def _answer_display(question: dict[str, Any], row: Any) -> Any:
    """One stored answer as the reader of a result wants it."""
    if question["type"] == "scale":
        return row["answer_number"]
    if question["type"] == "checkbox":
        try:
            stored = json.loads(row["answer_options_json"] or "[]")
        except (TypeError, ValueError):
            stored = []
        return stored if isinstance(stored, list) else []
    return str(row["answer_text"] or "")


def _question_tally(question: dict[str, Any], rows: list[Any], emails: dict[int, str]) -> dict[str, Any]:
    """One question's tallies for one item: counts, an average, or the answers."""
    tally: dict[str, Any] = {
        "question_id": question["id"],
        "label": question["label"],
        "type": question["type"],
        "answered": len(rows),
        "choices": [],
        "average": None,
        "answers": [],
    }
    if question["type"] in CHOICE_QUESTION_KINDS:
        counts = {option: 0 for option in question.get("options") or []}
        for row in rows:
            value = str(row["answer_text"] or "").strip()
            if not value:
                continue
            counts[value] = counts.get(value, 0) + 1
        tally["choices"] = [{"option": option, "count": count} for option, count in counts.items()]
    elif question["type"] == "checkbox":
        counts = {option: 0 for option in question.get("options") or []}
        for row in rows:
            values = _answer_display(question, row)
            for value in values:
                text = str(value).strip()
                if text:
                    counts[text] = counts.get(text, 0) + 1
        tally["choices"] = [{"option": option, "count": count} for option, count in counts.items()]
    elif question["type"] == "scale":
        numbers = [row["answer_number"] for row in rows if row["answer_number"] is not None]
        if numbers:
            tally["average"] = round(sum(numbers) / len(numbers), 2)
        distribution = {str(value): 0 for value in range(1, 6)}
        for number in numbers:
            key = str(int(number))
            if key in distribution:
                distribution[key] += 1
        tally["choices"] = [{"option": value, "count": count} for value, count in distribution.items()]
    else:
        tally["answers"] = [
            {
                "email": emails.get(int(row["response_id"]), ""),
                "answer": _answer_display(question, row),
                "submitted_at": row["submitted_at"],
            }
            for row in rows
        ]
    return tally


@router.get("/survey-results")
def get_survey_results(
    period_id: int | None = None,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """Interest per item: each item's response count, then its questions' tallies.

    Nothing here is a single tally for the whole survey: two items ask different
    questions, so a survey-wide number would average answers that were never
    asked of the same item.
    """
    company_id = resolve_scoped_company_id(user, company_id)
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

        periods = [
            survey_period_to_public(row)
            for row in execute(
                conn,
                "SELECT * FROM survey_periods WHERE company_id = ? ORDER BY id DESC",
                (company_id,),
            ).fetchall()
        ]

        result: dict[str, Any] = {
            "period": survey_period_to_public(period) if period is not None else None,
            "periods": periods,
            "summary": {
                "submissions": 0,
                "unique_respondents": 0,
                "items": 0,
                "answered_items": 0,
                "total_answers": 0,
            },
            "items": [],
            "respondents": [],
        }
        if period is None:
            return result

        items = period_items_public(conn, period)
        submissions = execute(
            conn,
            """
            SELECT id, email, created_at FROM survey_responses
            WHERE company_id = ? AND survey_period_id = ? AND survey_item_id IS NULL
            ORDER BY created_at ASC, id ASC
            """,
            (company_id, period["id"]),
        ).fetchall()
        answer_rows = execute(
            conn,
            """
            SELECT a.response_id, a.survey_period_item_id, a.question_id, a.answer_text,
                   a.answer_options_json, a.answer_number, r.email, r.created_at AS submitted_at
            FROM survey_answers a
            JOIN survey_responses r ON r.id = a.response_id
            WHERE r.company_id = ? AND r.survey_period_id = ? AND r.survey_item_id IS NULL
            ORDER BY r.created_at ASC, r.id ASC, a.id ASC
            """,
            (company_id, period["id"]),
        ).fetchall()

    emails = {int(row["id"]): str(row["email"] or "") for row in submissions}
    grouped: dict[int, dict[str, list[Any]]] = {}
    counted: dict[int, set[int]] = {}
    for row in answer_rows:
        item_id = int(row["survey_period_item_id"])
        grouped.setdefault(item_id, {}).setdefault(str(row["question_id"]), []).append(row)
        counted.setdefault(item_id, set()).add(int(row["response_id"]))

    item_results = []
    for item in items:
        item_answers = grouped.get(int(item["id"]), {})
        response_ids = counted.get(int(item["id"]), set())
        item_results.append({
            "id": item["id"],
            "item_name": item["item_name"],
            "item_number": item["item_number"],
            "cost": item["cost"],
            "image_url": item["image_url"],
            "item_description": item["item_description"],
            "sort_order": item["sort_order"],
            "response_count": len(response_ids),
            "questions": [
                _question_tally(question, item_answers.get(question["id"], []), emails)
                for question in item["questions"]
            ],
        })

    result["items"] = item_results
    result["respondents"] = [
        {
            "id": row["id"],
            "email": str(row["email"] or ""),
            "created_at": row["created_at"],
            "items_answered": len({
                int(answer["survey_period_item_id"])
                for answer in answer_rows
                if int(answer["response_id"]) == int(row["id"])
            }),
        }
        for row in submissions
    ]
    result["summary"] = {
        "submissions": len(submissions),
        "unique_respondents": len({email.lower() for email in emails.values() if email}),
        "items": len(item_results),
        "answered_items": sum(1 for item in item_results if item["response_count"] > 0),
        "total_answers": len(answer_rows),
    }
    return result


# ---------------------------------------------------------------------------
# Survey layout
# ---------------------------------------------------------------------------

@router.get("/survey-layout")
def get_survey_layout(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM survey_items WHERE company_id = ? AND is_active = ? ORDER BY survey_sort_order ASC, sort_order ASC, id ASC",
            (company_id, True),
        ).fetchall()
        fields = survey_filter_fields(conn, company_id)
        questions = survey_layout_questions(conn, company_id)
    return {
        "filter_fields": fields,
        "questions": questions,
        "items": [catalog_item(row) for row in rows],
    }


@router.put("/survey-layout")
def update_survey_layout(
    payload: SurveyLayoutUpdate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
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
    return {"filter_fields": filter_fields, "items": [catalog_item(row) for row in rows]}


# ---------------------------------------------------------------------------
# Buy book layout
# ---------------------------------------------------------------------------

@router.get("/buy-book-link")
def get_buy_book_link(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """The public, read-only Buy Book link for this company. Mints it on first ask."""
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        token = public_book_token_for(conn, company_id)
    return {"token": token, "path": f"/book/{token}"}


@router.post("/buy-book-link/rotate")
def rotate_buy_book_link(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """Replace the link. The old one stops working — that is the point of rotating."""
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        token = rotate_public_book_token(conn, company_id)
    return {"token": token, "path": f"/book/{token}"}


@router.get("/buy-book-layout")
def get_buy_book_layout(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        ensure_buy_book_layout(conn, company_id)
        rows = execute(
            conn,
            """
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ?
            ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC
            """,
            (company_id, True),
        ).fetchall()
        fields = buy_book_filter_fields(conn, company_id)
        layers = buy_book_page_break_layers(conn, company_id)
    # group_by stays for older clients; the stack is the source of truth.
    group_by = layers[0]["field"] if layers else "none"
    return {
        "filter_fields": fields,
        "group_by": group_by,
        "page_break_layers": layers,
        "items": [catalog_item(row, surface="buy-book") for row in rows],
    }


def validate_page_break_layers(entries: list[dict]) -> list[dict]:
    """Accept a stack only when it can be rendered: known facets, no repeats, at most four.

    A repeat is refused rather than silently dropped, because dropping it after
    a save would change the book without telling the person who saved it.
    """
    if len(entries) > MAX_PAGE_BREAK_LAYERS:
        raise HTTPException(
            status_code=400,
            detail=f"A Buy Book has at most {MAX_PAGE_BREAK_LAYERS} layers.",
        )
    layers: list[dict] = []
    owner: dict[str, int] = {}
    for position, entry in enumerate(entries, start=1):
        field = str(entry.get("field") or "")
        if field in owner:
            raise HTTPException(
                status_code=400,
                detail=f"{field} is already used in layer {owner[field]}; a facet can only be used once.",
            )
        owner[field] = position
        layers.append({"field": field, "new_page": bool(entry.get("new_page", True))})
    return layers


@router.put("/buy-book-layout")
def update_buy_book_layout(
    payload: BuyBookLayoutUpdate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
    filter_fields = normalize_filter_fields(payload.filter_fields)
    if payload.page_break_layers is None:
        # Older clients still send the single Group by field.
        group_by = normalize_buy_book_group_by(payload.group_by)
        layers = [] if payload.group_by == "none" else [{"field": group_by, "new_page": True}]
    else:
        layers = validate_page_break_layers([layer.model_dump() for layer in payload.page_break_layers])
    group_by = layers[0]["field"] if layers else "none"
    with connect() as conn:
        ensure_buy_book_layout(conn, company_id)
        execute(
            conn,
            """
            UPDATE buy_book_layouts
            SET filter_fields_json = ?, group_by = ?, page_break_layers_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE company_id = ?
            """,
            (json.dumps(filter_fields), group_by, json.dumps(layers), company_id),
        )
        for index, item in enumerate(payload.items, start=1):
            # The page break is derived from the layer stack at render time, so a
            # layout save leaves the retired manual column alone: writing the
            # derived flag back into it would revive a column nothing reads.
            cursor = execute(
                conn,
                """
                UPDATE survey_items
                SET in_buy_book = ?, buy_book_sort_order = ?,
                    image_display_size = ?, image_fit = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND company_id = ? AND is_active = ?
                """,
                (
                    1 if item.in_buy_book else 0,
                    index,
                    item.image_display_size,
                    item.image_fit,
                    item.id,
                    company_id,
                    True,
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog item not found")
        rows = execute(
            conn,
            """
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ?
            ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC
            """,
            (company_id, True),
        ).fetchall()
    return {
        "filter_fields": filter_fields,
        "group_by": group_by,
        "page_break_layers": layers,
        "items": [catalog_item(row, surface="buy-book") for row in rows],
    }


# ---------------------------------------------------------------------------
# Catalog sync (a read-only reconciliation against the NetSuite item master)
# ---------------------------------------------------------------------------

def catalog_item(row: Any, *, surface: str | None = None) -> dict:
    """A catalog row for the admin screen, plus the two sync facts it shows."""
    payload = survey_item_to_public(row, surface=surface)
    payload["needs_name"] = bool(row_value(row, "needs_name", 0))
    payload["netsuite_synced"] = bool(row_value(row, "netsuite_synced_json", ""))
    return payload


def catalog_sync_company(conn: Any, user: Any, company_id: int | None) -> Any:
    """The one company this call syncs.

    Explicit id, else ``NETSUITE_SYNC_COMPANY_SLUG`` (the scheduler's setting),
    else the caller's own scope — which for an admin is the app's default
    company. One company per run, always: the item master read is per account,
    but the rows it writes belong to one dataset.
    """
    if company_id is not None:
        return fetch_company_by_id(conn, resolve_scoped_company_id(user, company_id))
    slug = NETSUITE_SYNC_COMPANY_SLUG
    if slug:
        company = fetch_company_by_slug(conn, slug)
        if company is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"NETSUITE_SYNC_COMPANY_SLUG names '{slug}', which is not a company",
            )
        return company
    return fetch_company_by_id(conn, resolve_scoped_company_id(user, None))


@router.post("/catalog/sync")
def run_catalog_sync(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    """Reconcile the catalog now and return the report the run produced."""
    with connect() as conn:
        company = catalog_sync_company(conn, user, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    try:
        report = sync_catalog(dict(company), apply=True, user_email=user["email"])
    except SourceError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
    return {"company": company["slug"], "report": report.as_dict()}


@router.get("/catalog/sync")
def get_catalog_sync(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    """The last run recorded for this company — the line above the item list."""
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        run = last_run(conn, company_id)
    return {"last_run": run}


@router.post("/catalog/items/{item_id}/reset")
def reset_catalog_item(
    item_id: int,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    """Write the last-synced NetSuite values back onto one catalog row.

    The way out of a per-field override. It touches only the mapped fields:
    price, image, uom, the book flags and ``is_active`` are not NetSuite's to
    change, and a reset never reactivates a retired row.
    """
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        row = execute(
            conn,
            "SELECT * FROM survey_items WHERE id = ? AND company_id = ?",
            (item_id, company_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalog item not found")
        try:
            restored = reset_to_netsuite(conn, row, user_email=user["email"])
        except SourceError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from None
        fresh = execute(conn, "SELECT * FROM survey_items WHERE id = ?", (item_id,)).fetchone()
    return {"item": catalog_item(fresh), "restored": restored}


# ---------------------------------------------------------------------------
# Survey periods
# ---------------------------------------------------------------------------

def survey_pool_ids(questions: list[dict[str, Any]]) -> list[str]:
    return [question["id"] for question in questions]


def prepare_survey_items(payload_items: list[Any] | None, pool_ids: list[str]) -> list[Any]:
    """Validate the dialog's items and default each new one to the whole pool.

    An item with no name is refused rather than saved: it would be a blank card
    on the public page. ``question_ids=None`` means "the whole pool" (a new
    item ticks everything by default); an empty list is a deliberate "asks
    nothing", and a tick naming a question the pool does not have is dropped.
    """
    prepared: list[Any] = []
    for index, item in enumerate(payload_items or [], start=1):
        name = str(item.item_name or "").strip()
        if not name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"Every item needs a name (item {index})"
            )
        item.item_name = name
        if item.question_ids is None:
            item.question_ids = list(pool_ids)
        else:
            item.question_ids = [qid for qid in item.question_ids if qid in pool_ids]
        prepared.append(item)
    return prepared


def survey_period_summary(conn: Any, row: Any) -> dict[str, Any]:
    items = count_period_items(conn, int(row["id"]))
    payload = survey_period_to_public(row, item_count=items)
    payload["question_count"] = len(parse_survey_questions(row["questions_json"]))
    payload["response_count"] = period_response_count(conn, int(row["id"]))
    return payload


@router.get("/survey-periods")
def list_survey_periods(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
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
        periods = []
        for row in rows:
            payload = survey_period_summary(conn, row)
            # The list is the screen that hands out the link, so the admin copy
            # of it carries the token; the public serializer never does.
            payload["survey_token"] = str(row["survey_token"] or "")
            periods.append(payload)
    return {"periods": periods}


@router.post("/survey-periods", status_code=status.HTTP_201_CREATED)
def create_survey_period(
    payload: SurveyPeriodCreate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
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

        # Explicit questions (the dialog) win; otherwise a copied survey keeps
        # its source's pool, and a fresh survey starts from the company template.
        if payload.questions is not None:
            questions = normalize_survey_questions([question.model_dump() for question in payload.questions])
        elif source_period is not None:
            questions = parse_survey_questions(source_period["questions_json"])
        else:
            questions = survey_layout_questions(conn, company_id)
        questions_json = survey_questions_json(questions)

        items = prepare_survey_items(payload.items, survey_pool_ids(questions))

        status_value = "open" if payload.open_immediately else "draft"
        opens_at = utc_now().isoformat() if payload.open_immediately else None
        if payload.open_immediately:
            close_open_survey_periods(conn, company_id)

        values = (
            company_id, name, status_value, opens_at, mint_survey_token(), filter_fields_json, questions_json,
        )
        if IS_POSTGRES:
            period = execute(
                conn,
                """
                INSERT INTO survey_periods
                    (company_id, name, status, opens_at, survey_token, filter_fields_json, questions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING *
                """,
                values,
            ).fetchone()
        else:
            cursor = execute(
                conn,
                """
                INSERT INTO survey_periods
                    (company_id, name, status, opens_at, survey_token, filter_fields_json, questions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            period = execute(conn, "SELECT * FROM survey_periods WHERE id = ?", (cursor.lastrowid,)).fetchone()

        if items:
            save_period_items(conn, int(period["id"]), items, survey_pool_ids(questions))
        elif source_period is not None:
            copy_period_items(conn, int(source_period["id"]), int(period["id"]))
        item_count = count_period_items(conn, int(period["id"]))

    created_public = survey_period_to_public(period, item_count=item_count)
    # An admin response, so the create screen can hand out the link at once.
    # survey_period_to_public() itself stays token-free for public payloads.
    created_public["survey_token"] = str(period["survey_token"] or "")
    return {"period": created_public}


@router.get("/survey-catalog")
def search_survey_catalog(
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """Search the catalog. The survey builder no longer reads it; the buy book does."""
    company_id = resolve_scoped_company_id(user, company_id)
    needle = q.strip().lower()
    if not needle:
        return {"items": [], "total": 0, "requires_search": True}

    like = f"%{needle}%"
    where = """
        company_id = ? AND is_active = ?
        AND (lower(item_name) LIKE ? OR lower(brand) LIKE ? OR lower(channel) LIKE ?
             OR lower(product_type) LIKE ? OR lower(key_program) LIKE ?)
    """
    args = (company_id, True, like, like, like, like, like)
    with connect() as conn:
        total = execute(conn, f"SELECT COUNT(*) AS n FROM survey_items WHERE {where}", args).fetchone()["n"]
        rows = execute(
            conn,
            f"""
            SELECT id, item_name, brand, channel, product_type, key_program, price, uom
            FROM survey_items
            WHERE {where}
            ORDER BY item_name ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (*args, limit, offset),
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "item_name": row["item_name"],
                "brand": row["brand"],
                "channel": row["channel"],
                "product_type": row["product_type"],
                "key_program": row["key_program"],
                "price": row["price"],
                "uom": row["uom"],
            }
            for row in rows
        ],
        "total": int(total),
        "requires_search": False,
    }


@router.get("/survey-periods/{period_id}")
def get_survey_period_detail(
    period_id: int,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        items = period_items_public(conn, period)
        responses = period_response_count(conn, period_id)

    period_public = survey_period_to_public(period, item_count=len(items))
    # The token is read-only here: this is the screen an editor copies the
    # survey link from. It stays out of survey_period_to_public() so the public
    # payload can never carry it.
    period_public["survey_token"] = str(period["survey_token"] or "")
    period_public["response_count"] = responses
    return {
        "period": period_public,
        "filter_fields": period_public["filter_fields"],
        "questions": period_public["questions"],
        "items": items,
        "catalog_total": 0,
    }


@router.post("/survey-periods/{period_id}/rotate-token")
def rotate_survey_period_token(
    period_id: int,
    user: sqlite3.Row = Depends(get_current_user),
    company_id: int | None = Query(default=None),
) -> dict:
    """Replace the period's link credential. The old token stops resolving at once."""
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        token = mint_survey_token()
        execute(
            conn,
            "UPDATE survey_periods SET survey_token = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            (token, period_id, company_id),
        )
        period = get_survey_period(conn, company_id, period_id)
        item_count = count_period_items(conn, period_id)
    period_public = survey_period_to_public(period, item_count=item_count)
    period_public["survey_token"] = token
    return {"period": period_public}


@router.patch("/survey-periods/{period_id}")
def update_survey_period(
    period_id: int,
    payload: SurveyPeriodUpdate,
    user: sqlite3.Row = Depends(get_current_user),
    company_id: int | None = Query(default=None),
) -> dict:
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")

        name = payload.name.strip() if payload.name is not None else period["name"]
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Survey name is required")

        next_status = payload.status if payload.status is not None else period["status"]
        # A cleared date field sends ""; storing it is what broke the open-period
        # read on Postgres (a blank is not a timestamptz). Normalise both the
        # incoming value and the row it falls back to, so a save also heals a
        # row written before the normaliser existed.
        opens_at = (
            normalize_period_timestamp("opens_at", payload.opens_at)
            if payload.opens_at is not None
            else normalize_timestamp(period["opens_at"])
        )
        closes_at = (
            normalize_period_timestamp("closes_at", payload.closes_at)
            if payload.closes_at is not None
            else normalize_timestamp(period["closes_at"])
        )

        if next_status == "open" and period["status"] != "open":
            close_open_survey_periods(conn, company_id)
            opens_at = opens_at or utc_now().isoformat()
            closes_at = None
        elif next_status == "closed" and period["status"] != "closed":
            closes_at = closes_at or utc_now().isoformat()

        # The cover is stored as overrides only: a field the client did not
        # send keeps what was stored, and a blank it did send means "use the
        # default" at render time.
        cover_json = row_value(period, "cover_json", None)
        if payload.cover is not None:
            merged = parse_cover_json(cover_json)
            for field, value in payload.cover.model_dump(exclude_unset=True).items():
                if value is None:
                    continue
                merged[field] = str(value).strip()
            cover_json = json.dumps(merged)

        execute(
            conn,
            """
            UPDATE survey_periods
            SET name = ?, status = ?, opens_at = ?, closes_at = ?, cover_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (name, next_status, opens_at, closes_at, cover_json, period_id, company_id),
        )
        period = get_survey_period(conn, company_id, period_id)
        item_count = count_period_items(conn, period_id)

    return {"period": survey_period_to_public(period, item_count=item_count)}


@router.put("/survey-periods/{period_id}/layout")
def update_survey_period_layout(
    period_id: int,
    payload: SurveyPeriodLayoutUpdate,
    user: sqlite3.Row = Depends(get_current_user),
    company_id: int | None = Query(default=None),
) -> dict:
    """Save the whole dialog: the pool, then the items that tick from it."""
    company_id = resolve_scoped_company_id(user, company_id)
    filter_fields = normalize_filter_fields(payload.filter_fields)
    questions = normalize_survey_questions([question.model_dump() for question in payload.questions])
    pool_ids = survey_pool_ids(questions)
    items = prepare_survey_items(payload.items, pool_ids)
    with connect() as conn:
        period = get_survey_period(conn, company_id, period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
        if period["status"] == "closed":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Closed surveys cannot be edited")

        name = payload.name.strip() if payload.name is not None else period["name"]
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Survey name is required")

        # A question added to the pool applies to every item at once. Without
        # this, a new question is ticked nowhere and the survey silently skips
        # it - including when a stale dialog (or a direct API save) sends an
        # item without it. Questions that were already in the pool are left
        # exactly as the items had them: a question unticked on one item stays
        # unticked there.
        previous_pool_ids = {question["id"] for question in parse_survey_questions(period["questions_json"])}
        added_ids = [question_id for question_id in pool_ids if question_id not in previous_pool_ids]
        if added_ids:
            for item in items:
                ticked = list(item.question_ids or [])
                item.question_ids = ticked + [qid for qid in added_ids if qid not in ticked]

        execute(
            conn,
            """
            UPDATE survey_periods
            SET name = ?, filter_fields_json = ?, questions_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (name, json.dumps(filter_fields), survey_questions_json(questions), period_id, company_id),
        )
        save_period_items(conn, period_id, items, pool_ids)
        # Keep company template in sync with the latest edited period questions.
        ensure_survey_layout(conn, company_id)
        execute(
            conn,
            """
            UPDATE survey_layouts
            SET questions_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE company_id = ?
            """,
            (survey_questions_json(questions), company_id),
        )
        period = get_survey_period(conn, company_id, period_id)
        saved_items = period_items_public(conn, period)

    return {
        "period": survey_period_to_public(period, item_count=len(saved_items)),
        "filter_fields": filter_fields,
        "questions": questions,
        "items": saved_items,
    }


@router.delete("/survey-periods/{period_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_survey_period(
    period_id: int,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> Response:
    company_id = resolve_scoped_company_id(user, company_id)
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
def export_survey_responses(
    period_id: int | None = None,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> Response:
    """One row per respondent x item x question, with the item printed alongside.

    The item's own columns (image, number, description, name, cost) ride on
    every answer row, so an account manager reads one line per answer without
    joining anything back to a survey screen.
    """
    company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        ensure_legacy_survey_period(conn, company_id)
        args: list[Any] = [company_id]
        period_clause = ""
        period_name = "all"
        if period_id is not None:
            period = get_survey_period(conn, company_id, period_id)
            if period is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Survey period not found")
            period_clause = " AND r.survey_period_id = ?"
            args.append(period_id)
            period_name = str(period["name"]).replace(" ", "-").lower()

        labels: dict[str, str] = {}
        for period_row in execute(
            conn,
            f"SELECT questions_json FROM survey_periods WHERE company_id = ?{' AND id = ?' if period_id is not None else ''}",
            (company_id, period_id) if period_id is not None else (company_id,),
        ).fetchall():
            for question in parse_survey_questions(period_row["questions_json"]):
                labels.setdefault(question["id"], question["label"])

        rows = execute(
            conn,
            f"""
            SELECT
                r.created_at,
                r.email,
                i.item_name,
                i.item_number,
                i.cost,
                i.item_description,
                i.image_url,
                a.question_id,
                a.answer_text,
                a.answer_options_json,
                a.answer_number
            FROM survey_answers a
            JOIN survey_responses r ON r.id = a.response_id
            JOIN survey_period_items i ON i.id = a.survey_period_item_id
            WHERE r.company_id = ? AND r.survey_item_id IS NULL{period_clause}
            ORDER BY r.created_at DESC, r.id DESC, i.sort_order ASC, i.id ASC, a.id ASC
            """,
            tuple(args),
        ).fetchall()

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Survey Responses"
    headers = ["Submitted At", "Email", "Item Name", "Item Number", "Cost",
               "Item Description", "Item Image URL", "Question", "Answer"]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        if row["answer_options_json"] and str(row["answer_options_json"]).strip() not in {"", "[]"}:
            try:
                stored = json.loads(row["answer_options_json"])
            except (TypeError, ValueError):
                stored = []
            answer = "; ".join(str(part) for part in stored) if isinstance(stored, list) else str(row["answer_text"] or "")
        elif row["answer_number"] is not None:
            answer = row["answer_text"] or str(row["answer_number"])
        else:
            answer = str(row["answer_text"] or "")
        worksheet.append([
            str(row["created_at"] or ""),
            str(row["email"] or ""),
            row["item_name"],
            row["item_number"] or "",
            row["cost"] or "",
            row["item_description"] or "",
            row["image_url"] or "",
            labels.get(str(row["question_id"]), str(row["question_id"])),
            answer,
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
def list_users(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    del user
    with connect() as conn:
        if company_id is None:
            rows = execute(conn, "SELECT * FROM users ORDER BY created_at DESC, id DESC").fetchall()
        else:
            rows = execute(
                conn,
                """
                SELECT * FROM users
                WHERE company_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (company_id,),
            ).fetchall()
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
def list_admin_kanban_columns(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    scoped_company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM kanban_columns WHERE company_id = ? ORDER BY sort_order ASC, id ASC",
            (scoped_company_id,),
        ).fetchall()
    return {"columns": [kanban_column_to_public(row) for row in rows]}


@router.post("/kanban-columns", status_code=status.HTTP_201_CREATED)
def create_kanban_column(
    payload: KanbanColumnCreate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    scoped_company_id = resolve_scoped_company_id(user, company_id)
    values = (
        scoped_company_id,
        payload.title.strip(),
        json.dumps(payload.statuses),
        payload.sort_order,
        payload.is_visible,
    )
    sql = "INSERT INTO kanban_columns (company_id, title, statuses_json, sort_order, is_visible) VALUES (?, ?, ?, ?, ?)"
    with connect() as conn:
        if IS_POSTGRES:
            row = execute(conn, with_returning(sql), values).fetchone()
        else:
            cursor = execute(conn, sql, values)
            row = execute(conn, "SELECT * FROM kanban_columns WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {"column": kanban_column_to_public(row)}


@router.patch("/kanban-columns/{column_id}")
def update_kanban_column(
    column_id: int,
    payload: KanbanColumnUpdate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    if "title" in updates and updates["title"] is not None:
        updates["title"] = updates["title"].strip()
    if "statuses" in updates:
        updates["statuses_json"] = json.dumps(updates.pop("statuses") or [])
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No kanban column fields supplied")

    scoped_company_id = resolve_scoped_company_id(user, company_id)
    assignments = ", ".join(f"{field} = ?" for field in updates)
    values = list(updates.values()) + [column_id, scoped_company_id]
    with connect() as conn:
        cursor = execute(
            conn,
            f"UPDATE kanban_columns SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND company_id = ?",
            values,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanban column not found")
        row = execute(
            conn,
            "SELECT * FROM kanban_columns WHERE id = ? AND company_id = ?",
            (column_id, scoped_company_id),
        ).fetchone()
    return {"column": kanban_column_to_public(row)}


@router.delete("/kanban-columns/{column_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kanban_column(
    column_id: int,
    response: Response,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> Response:
    scoped_company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        cursor = execute(
            conn,
            "DELETE FROM kanban_columns WHERE id = ? AND company_id = ?",
            (column_id, scoped_company_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kanban column not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# Sheet statuses
# ---------------------------------------------------------------------------

@router.get("/sheet-statuses")
def list_sheet_statuses(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    scoped_company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM sheet_statuses WHERE company_id = ? ORDER BY sheet ASC, sort_order ASC, id ASC",
            (scoped_company_id,),
        ).fetchall()
    return {"statuses": [sheet_status_to_public(row) for row in rows]}


@router.post("/sheet-statuses", status_code=status.HTTP_201_CREATED)
def create_sheet_status(
    payload: SheetStatusCreate,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    if payload.sheet not in SHEETS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown sheet")
    status_label = payload.status.strip()
    if not status_label:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Status is required")

    scoped_company_id = resolve_scoped_company_id(user, company_id)
    try:
        with connect() as conn:
            max_sort = execute(
                conn,
                "SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM sheet_statuses WHERE company_id = ? AND sheet = ?",
                (scoped_company_id, payload.sheet),
            ).fetchone()["max_sort"]
            values = (scoped_company_id, payload.sheet, status_label, max_sort + 1)
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
def delete_sheet_status(
    status_id: int,
    response: Response,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> Response:
    scoped_company_id = resolve_scoped_company_id(user, company_id)
    with connect() as conn:
        cursor = execute(
            conn,
            "DELETE FROM sheet_statuses WHERE id = ? AND company_id = ?",
            (status_id, scoped_company_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Status not found")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs")
def list_audit_logs(
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> dict:
    with connect() as conn:
        if company_id is None:
            rows = execute(
                conn,
                "SELECT * FROM audit_logs ORDER BY created_at DESC, id DESC LIMIT 250",
            ).fetchall()
        else:
            scoped_company_id = resolve_scoped_company_id(user, company_id)
            rows = execute(
                conn,
                """
                SELECT * FROM audit_logs
                WHERE company_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 250
                """,
                (scoped_company_id,),
            ).fetchall()
    return {"logs": [audit_log_to_public(row) for row in rows]}
