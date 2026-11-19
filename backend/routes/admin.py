from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl import Workbook
from openpyxl.styles import Font

from config import IS_POSTGRES, SHEETS
from database import (
    MAX_PAGE_BREAK_LAYERS,
    buy_book_filter_fields,
    buy_book_group_by,
    buy_book_page_break_layers,
    normalize_page_break_layers,
    classify_interest_answer,
    close_open_survey_periods,
    connect,
    copy_catalog_items_into_period,
    count_period_items,
    company_id_for_user,
    company_to_public,
    resolve_scoped_company_id,
    row_value,
    serialize_user,
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
    resolve_interest_question,
    period_item_to_public,
    sheet_status_to_public,
    survey_filter_fields,
    survey_item_to_public,
    survey_layout_questions,
    survey_period_to_public,
    survey_questions_json,
    utc_now,
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

@router.get("/survey-results")
def get_survey_results(
    period_id: int | None = None,
    company_id: int | None = Query(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
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

        period_clause = ""
        item_args: list[Any] = [company_id]
        total_args: list[Any] = [company_id]
        response_args: list[Any] = [company_id]
        if period is not None:
            period_clause = " AND sr.survey_period_id = ?"
            item_args.append(period["id"])
            total_args.append(period["id"])
            response_args.append(period["id"])

        # Counts come from the survey's own interest question whenever a response
        # carries answers; the legacy column is the fallback for responses written
        # before questions existed, or by an old client that sends no answers.
        questions_by_period: dict[int, list[dict[str, Any]]] = {
            int(row["id"]): parse_survey_questions(row["questions_json"])
            for row in execute(
                conn,
                "SELECT id, questions_json FROM survey_periods WHERE company_id = ?",
                (company_id,),
            ).fetchall()
        }
        period_questions = (
            questions_by_period.get(int(period["id"]), parse_survey_questions(period["questions_json"]))
            if period is not None
            else []
        )

        if period is not None:
            item_rows = execute(
                conn,
                """
                SELECT spi.catalog_item_id AS id, spi.item_name, spi.brand, spi.channel,
                       spi.product_type, spi.key_program, spi.image_url, spi.price, spi.uom,
                       spi.sort_order
                FROM survey_period_items spi
                WHERE spi.survey_period_id = ?
                ORDER BY spi.sort_order ASC
                """,
                (period["id"],),
            ).fetchall()
        else:
            item_rows = execute(
                conn,
                """
                SELECT si.id, si.item_name, si.brand, si.channel, si.product_type,
                       si.key_program, si.image_url, si.price, si.uom, si.sort_order
                FROM survey_items si
                WHERE si.company_id = ? AND si.is_active = ?
                ORDER BY si.sort_order ASC
                """,
                (company_id, True),
            ).fetchall()

        answer_rows = execute(
            conn,
            f"""
            SELECT sr.id, sr.survey_item_id, sr.survey_period_id, sr.email,
                   sr.recommend_rollout, sr.answers_json, sr.created_at
            FROM survey_responses sr
            WHERE sr.company_id = ?{period_clause}
            ORDER BY sr.created_at ASC
            """,
            tuple(total_args),
        ).fetchall()

        tally: dict[int, dict[str, Any]] = {}
        for row in answer_rows:
            answers = parse_answers_json(row["answers_json"])
            row_period_id = row["survey_period_id"]
            row_questions = questions_by_period.get(int(row_period_id)) if row_period_id else None
            if not row_questions:
                row_questions = period_questions
            interest = classify_interest_answer(resolve_interest_question(row_questions), answers)
            if not interest:
                interest = {"Yes": "interested", "No": "not_interested"}.get(
                    str(row["recommend_rollout"] or ""), ""
                )
            stats = tally.setdefault(
                int(row["survey_item_id"]),
                {"total": 0, "interested": 0, "not_interested": 0, "maybe": 0,
                 "emails": [], "attention": [], "engagement": []},
            )
            stats["total"] += 1
            if interest == "interested":
                stats["interested"] += 1
            elif interest == "not_interested":
                stats["not_interested"] += 1
            else:
                stats["maybe"] += 1
            email = str(row["email"] or "").strip()
            if email and email not in stats["emails"]:
                stats["emails"].append(email)

            # Two labelled figures, two questions: the first scale question feeds
            # attention, the second feeds engagement. A period with one scale
            # question leaves engagement null (the dashboard hides it) rather than
            # printing the same number under a second label.
            scale_questions = [q for q in row_questions if q["type"] == "scale"]
            for slot, question in zip(("attention", "engagement"), scale_questions[:2]):
                try:
                    scale_value = int(answers.get(question["id"]))
                except (TypeError, ValueError):
                    scale_value = 0
                if 1 <= scale_value <= 5:
                    stats[slot].append(scale_value)

        unique_respondents = len({str(row["email"] or "").strip().lower() for row in answer_rows if str(row["email"] or "").strip()})
        summary = {
            "unique_respondents": unique_respondents,
            "total_responses": len(answer_rows),
            "total_interested": sum(stats["interested"] for stats in tally.values()),
            "total_not_interested": sum(stats["not_interested"] for stats in tally.values()),
            "has_scale_question": any(q["type"] == "scale" for q in period_questions),
        }

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
    empty_stats = {"total": 0, "interested": 0, "not_interested": 0, "maybe": 0,
                   "emails": [], "attention": [], "engagement": []}
    for row in item_rows:
        stats = tally.get(int(row["id"]), empty_stats)
        total_responses = stats["total"]
        attention_average = (
            round(sum(stats["attention"]) / len(stats["attention"]), 2) if stats["attention"] else None
        )
        engagement_average = (
            round(sum(stats["engagement"]) / len(stats["engagement"]), 2) if stats["engagement"] else None
        )
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
            "sort_order": row["sort_order"] or 0,
            "total_responses": total_responses,
            "interested": stats["interested"],
            "not_interested": stats["not_interested"],
            "maybe": stats["maybe"],
            "avg_attention": attention_average,
            "avg_engagement": engagement_average,
            "interest_pct": round(stats["interested"] / total_responses * 100) if total_responses else 0,
            "respondent_emails": list(stats["emails"]),
        })
    items.sort(key=lambda item: (-item["interested"], -item["total_responses"], item["sort_order"]))

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
        "summary": summary,
        "items": items,
        "responses": responses,
    }


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
        "items": [survey_item_to_public(row) for row in rows],
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
    return {"filter_fields": filter_fields, "items": [survey_item_to_public(row) for row in rows]}


# ---------------------------------------------------------------------------
# Buy book layout
# ---------------------------------------------------------------------------

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
        "items": [survey_item_to_public(row, surface="buy-book") for row in rows],
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
        "items": [survey_item_to_public(row, surface="buy-book") for row in rows],
    }


# ---------------------------------------------------------------------------
# Survey periods
# ---------------------------------------------------------------------------

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
        periods = [
            survey_period_to_public(row, item_count=count_period_items(conn, row["id"]))
            for row in rows
        ]
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
            source_period = None

        # Explicit questions (the create form) win; otherwise a copied period keeps
        # its own questions, and a fresh period starts from the company template.
        if payload.questions is not None:
            questions_json = survey_questions_json(
                [question.model_dump() for question in payload.questions]
            )
        elif source_period is not None:
            questions_json = survey_questions_json(parse_survey_questions(source_period["questions_json"]))
        else:
            questions_json = survey_questions_json(survey_layout_questions(conn, company_id))

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

        copy_catalog_items_into_period(
            conn,
            period["id"],
            company_id,
            only_in_survey=True,
            source_period_id=source_period["id"] if source_period is not None else None,
        )
        item_count = count_period_items(conn, period["id"])

    created_public = survey_period_to_public(period, item_count=item_count)
    # An admin response, so the create screen can hand out the link at once.
    # survey_period_to_public() itself stays token-free for public payloads.
    created_public["survey_token"] = str(period["survey_token"] or "")
    return {"period": created_public}


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
    period_public = survey_period_to_public(period, item_count=len(items))
    # The token is read-only here: this is the screen an editor copies the
    # survey link from. It stays out of survey_period_to_public() so the public
    # payload can never carry it.
    period_public["survey_token"] = str(period["survey_token"] or "")
    return {
        "period": period_public,
        "filter_fields": period_public["filter_fields"],
        "questions": period_public["questions"],
        "items": editor_items,
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
    company_id = resolve_scoped_company_id(user, company_id)
    filter_fields = normalize_filter_fields(payload.filter_fields)
    questions = normalize_survey_questions([question.model_dump() for question in payload.questions])
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
            SET filter_fields_json = ?, questions_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND company_id = ?
            """,
            (json.dumps(filter_fields), survey_questions_json(questions), period_id, company_id),
        )
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
        items = list_period_items(conn, period_id)

    return {
        "period": survey_period_to_public(period, item_count=len(items)),
        "filter_fields": filter_fields,
        "questions": questions,
        "items": [period_item_to_public(row) for row in items],
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
            period_clause = " AND survey_responses.survey_period_id = ?"
            args.append(period_id)
            period_name = str(period["name"]).replace(" ", "-").lower()

        question_labels: list[tuple[str, str]] = []
        if period_id is not None:
            period_row = get_survey_period(conn, company_id, period_id)
            if period_row is not None:
                for question in parse_survey_questions(period_row["questions_json"]):
                    question_labels.append((question["id"], question["label"]))
        else:
            seen: set[str] = set()
            for period_row in execute(
                conn,
                "SELECT questions_json FROM survey_periods WHERE company_id = ?",
                (company_id,),
            ).fetchall():
                for question in parse_survey_questions(period_row["questions_json"]):
                    if question["id"] in seen:
                        continue
                    seen.add(question["id"])
                    question_labels.append((question["id"], question["label"]))
        if not question_labels:
            question_labels = [("interest", "Interest"), ("feedback", "Notes")]

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
                survey_responses.feedback,
                survey_responses.answers_json
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
               "Key Program", "UOM", "Price", "Email"] + [label for _, label in question_labels]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        answers = parse_answers_json(row["answers_json"])
        answer_cells = []
        for question_id, _label in question_labels:
            if question_id in answers and answers[question_id] not in (None, "", []):
                value = answers[question_id]
                answer_cells.append("; ".join(str(part) for part in value) if isinstance(value, list) else str(value))
            elif question_id == "interest":
                answer_cells.append("Interested" if row["recommend_rollout"] == "Yes" else "Not Interested")
            elif question_id == "feedback":
                answer_cells.append(str(row["feedback"] or ""))
            else:
                answer_cells.append("")
        worksheet.append([
            str(row["created_at"] or ""), row["item_name"], row["brand"],
            row["channel"], row["product_type"], row["key_program"], row["uom"], row["price"],
            row["email"],
            *answer_cells,
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
