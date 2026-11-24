from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from config import (
    BUY_BOOK_GROUP_FIELDS,
    COMPANY_SCOPED_TABLES,
    DB_PATH,
    DATABASE_URL,
    DEFAULT_BUY_BOOK_GROUP_BY,
    DEFAULT_COMPANY_NAME,
    DEFAULT_COMPANY_SLUG,
    DEFAULT_RESPONSE_WIDTH,
    DEFAULT_SURVEY_FILTER_FIELDS,
    IS_POSTGRES,
    SURVEY_FILTER_FIELDS,
)

# Re-export IS_POSTGRES so routes can import it from here
__all__ = ["IS_POSTGRES"]

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect() -> Any:
    if IS_POSTGRES:
        if psycopg is None or dict_row is None:
            raise RuntimeError("psycopg is required when DATABASE_URL points to PostgreSQL")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def db_sql(query: str) -> str:
    if IS_POSTGRES:
        return query.replace("?", "%s")
    return query


def execute(conn: Any, query: str, params: tuple | list = ()) -> Any:
    return conn.execute(db_sql(query), tuple(params))


def with_returning(sql: str) -> str:
    """Append RETURNING * for Postgres inserts without breaking column-list parentheses."""
    if not IS_POSTGRES:
        return sql
    cleaned = sql.rstrip().rstrip(";")
    if " returning " in cleaned.lower():
        return cleaned
    return f"{cleaned} RETURNING *"


def sql_group_concat_distinct(expression: str, alias: str) -> str:
    """SQLite GROUP_CONCAT vs Postgres STRING_AGG."""
    if IS_POSTGRES:
        return f"STRING_AGG(DISTINCT ({expression})::text, ',') AS {alias}"
    return f"GROUP_CONCAT(DISTINCT {expression}) AS {alias}"


def sql_round_avg(expression: str, alias: str, digits: int = 1) -> str:
    if IS_POSTGRES:
        return f"ROUND(AVG({expression})::numeric, {digits}) AS {alias}"
    return f"ROUND(AVG({expression}), {digits}) AS {alias}"


def ensure_column(conn: Any, table_name: str, column_name: str, column_definition: str) -> None:
    if IS_POSTGRES:
        execute(conn, f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_definition}")
        return

    existing_columns = execute(conn, f"PRAGMA table_info({table_name})").fetchall()
    if column_name not in {column["name"] for column in existing_columns}:
        execute(conn, f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        salt, _ = password_hash.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), password_hash)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_timestamp(value: Any) -> str | None:
    """A stored time is a real instant or NULL - never a blank string.

    ``""`` is what a cleared date field sends, and it is *not* a portable
    "unset": SQLite stores it as ordinary text, while Postgres cannot put it in
    a ``timestamptz`` at all - comparing or inserting it raises
    ``InvalidDatetimeFormat`` instead of reading as absent. Normalising at the
    write edge gives both engines one meaning for "no time set".
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def row_to_item(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["extra"] = json.loads(item.pop("extra_json") or "{}")
    return item


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        keys = row.keys()
    except Exception:
        keys = []
    if key in keys:
        value = row[key]
        return default if value is None else value
    return default


def row_value(row: Any, key: str, default: Any = None) -> Any:
    """One column from a sqlite3.Row or a psycopg dict row, without a KeyError."""
    return _row_get(row, key, default)


def company_to_public(row: Any) -> dict:
    from config import COMPANY_BANNER_URL, COMPANY_LOGO_URL, DEFAULT_COMPANY_SLUG

    logo_url = str(_row_get(row, "logo_url", "") or "").strip()
    banner_url = str(_row_get(row, "banner_url", "") or "").strip()
    slug = row["slug"]
    if slug == DEFAULT_COMPANY_SLUG:
        logo_url = logo_url or COMPANY_LOGO_URL
        banner_url = banner_url or COMPANY_BANNER_URL
    return {
        "id": row["id"],
        "company_slug": slug,
        "company_name": row["name"],
        "slug": slug,
        "name": row["name"],
        "logo_url": logo_url,
        "banner_url": banner_url,
        "market_label": str(_row_get(row, "market_label", "") or ""),
        "is_active": bool(row["is_active"]),
        # The two legacy public links, one flag each: the buy book and the
        # survey have different futures, so a single switch would flip both.
        # A row read before the column existed defaults to on, the same answer
        # the column's DEFAULT TRUE gives every existing company.
        "legacy_public_buy_book": bool(_row_get(row, "legacy_public_buy_book", True)),
        "legacy_public_survey": bool(_row_get(row, "legacy_public_survey", True)),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
    }


def user_to_public(row: sqlite3.Row, company: Any | None = None) -> dict:
    company_id = _row_get(row, "company_id")
    payload = {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "company_id": int(company_id) if company_id is not None else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if company is not None:
        payload["company"] = company_to_public(company)
    return payload


def serialize_user(conn: Any, row: sqlite3.Row) -> dict:
    company_id = _row_get(row, "company_id")
    company = fetch_company_by_id(conn, int(company_id)) if company_id is not None else None
    return user_to_public(row, company)


def audit_log_to_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "user_email": row["user_email"],
        "action": row["action"],
        "sheet": row["sheet"],
        "item_id": row["item_id"],
        "before": json.loads(row["before_json"] or "null"),
        "after": json.loads(row["after_json"] or "null"),
        "created_at": row["created_at"],
    }


def kanban_column_to_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "statuses": json.loads(row["statuses_json"] or "[]"),
        "sort_order": row["sort_order"],
        "is_visible": bool(row["is_visible"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sheet_status_to_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "sheet": row["sheet"],
        "status": row["status"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def survey_item_to_public(row: sqlite3.Row, *, surface: str | None = None) -> dict:
    buy_book_sort_order = _row_get(row, "buy_book_sort_order", 0) or 0
    buy_book_page_break = bool(_row_get(row, "buy_book_page_break_before", 0))
    page_break_before = bool(row["page_break_before"])
    survey_sort_order = row["survey_sort_order"]
    if surface == "buy-book":
        # Public buy-book consumers reuse page_break_before / survey_sort_order.
        page_break_before = buy_book_page_break
        survey_sort_order = buy_book_sort_order
    return {
        "id": row["id"],
        "item_name": row["item_name"],
        "brand": row["brand"],
        "channel": row["channel"],
        "product_type": row["product_type"],
        "key_program": row["key_program"],
        "item_description": row["item_description"],
        "uom": row["uom"],
        "price": row["price"],
        "image_url": row["image_url"],
        "image_display_size": row["image_display_size"] if "image_display_size" in row.keys() else "medium",
        "image_fit": row["image_fit"] if "image_fit" in row.keys() else "contain",
        "sort_order": row["sort_order"],
        "is_active": bool(row["is_active"]),
        "in_survey": bool(row["in_survey"]),
        "in_buy_book": bool(row["in_buy_book"]),
        "survey_sort_order": survey_sort_order,
        "buy_book_sort_order": buy_book_sort_order,
        "response_width": row["response_width"] or DEFAULT_RESPONSE_WIDTH,
        "page_break_before": page_break_before,
        "buy_book_page_break_before": buy_book_page_break,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def survey_response_to_public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "survey_item_id": row["survey_item_id"],
        "email": row["email"],
        "attention_effectiveness": row["attention_effectiveness"],
        "recommend_rollout": row["recommend_rollout"],
        "retail_engagement": row["retail_engagement"],
        "stands_out": row["stands_out"],
        "price_reasonable": row["price_reasonable"],
        "feedback": row["feedback"],
        "answers": parse_answers_json(_row_get(row, "answers_json")),
        "created_at": row["created_at"],
        "updated_at": _row_get(row, "updated_at"),
    }


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def write_audit_log(
    conn: Any,
    user: sqlite3.Row,
    action: str,
    sheet: str | None = None,
    item_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    company_id: int | None = None,
) -> None:
    if company_id is None:
        company_id = ensure_default_company(conn)["id"]
    execute(
        conn,
        """
        INSERT INTO audit_logs (
            company_id, user_id, user_email, action, sheet, item_id, before_json, after_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            user["id"],
            user["email"],
            action,
            sheet,
            item_id,
            json.dumps(before, default=str) if before is not None else None,
            json.dumps(after, default=str) if after is not None else None,
        ),
    )


# ---------------------------------------------------------------------------
# Company helpers
# ---------------------------------------------------------------------------

def fetch_company_by_slug(conn: Any, slug: str) -> Any:
    return execute(
        conn,
        "SELECT * FROM companies WHERE lower(slug) = lower(?)",
        (slug.strip(),),
    ).fetchone()


def fetch_company_by_id(conn: Any, company_id: int) -> Any:
    return execute(conn, "SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()


def ensure_default_company(conn: Any) -> Any:
    from config import COMPANY_BANNER_URL, COMPANY_LOGO_URL

    existing = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
    if existing is not None:
        return existing

    values = (
        DEFAULT_COMPANY_SLUG,
        DEFAULT_COMPANY_NAME,
        COMPANY_LOGO_URL,
        COMPANY_BANNER_URL,
        True,
    )
    if IS_POSTGRES:
        return execute(
            conn,
            """
            INSERT INTO companies (slug, name, logo_url, banner_url, is_active)
            VALUES (?, ?, ?, ?, ?)
            RETURNING *
            """,
            values,
        ).fetchone()

    cursor = execute(
        conn,
        """
        INSERT INTO companies (slug, name, logo_url, banner_url, is_active)
        VALUES (?, ?, ?, ?, ?)
        """,
        values,
    )
    return execute(conn, "SELECT * FROM companies WHERE id = ?", (cursor.lastrowid,)).fetchone()


def default_company_id() -> int:
    with connect() as conn:
        company = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
    if company is None:
        raise RuntimeError("Default company not found — run init_db() first")
    return company["id"]


def company_id_for_user(user: Any) -> int:
    company_id = _row_get(user, "company_id")
    if company_id is not None:
        return int(company_id)
    return default_company_id()


def resolve_scoped_company_id(user: Any, requested_company_id: int | None = None) -> int:
    """Admins may override company scope; other roles always use their own company."""
    if requested_company_id is None or _row_get(user, "role") != "admin":
        return company_id_for_user(user)

    with connect() as conn:
        company = fetch_company_by_id(conn, int(requested_company_id))
    if company is None:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return int(company["id"])


def resolve_optional_company_scope(user: Any, requested_company_id: int | None = None) -> int | None:
    """Admin may omit company_id to mean all companies; other roles stay home-scoped."""
    if _row_get(user, "role") != "admin":
        return company_id_for_user(user)
    if requested_company_id is None:
        return None
    return resolve_scoped_company_id(user, requested_company_id)


def get_default_company() -> Any:
    with connect() as conn:
        company = fetch_company_by_slug(conn, DEFAULT_COMPANY_SLUG)
    if company is None or not bool(company["is_active"]):
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    return company


# ---------------------------------------------------------------------------
# Survey layout helpers
# ---------------------------------------------------------------------------

SURVEY_QUESTION_TYPES = {"yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"}

# Types whose answer can carry "interested / not interested": one option.
INTEREST_QUESTION_TYPES = ("yes_no", "single_choice", "dropdown")

# Types that need at least two options to be answerable.
CHOICE_QUESTION_TYPES = {"single_choice", "dropdown", "checkbox"}

DEFAULT_SURVEY_QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "interest",
        "type": "yes_no",
        "label": "Are you interested in this item?",
        "required": True,
        "options": ["Yes", "No"],
    },
    {
        "id": "feedback",
        "type": "text",
        "label": "Please provide feedback about the design, size, color, etc. of this item",
        "required": False,
        "options": [],
    },
]


def normalize_filter_fields(fields: list[str]) -> list[str]:
    normalized = []
    for field in fields:
        if field in SURVEY_FILTER_FIELDS and field not in normalized:
            normalized.append(field)
    return normalized


def default_survey_questions() -> list[dict[str, Any]]:
    return [dict(question) for question in DEFAULT_SURVEY_QUESTIONS]


def normalize_survey_questions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        return default_survey_questions()

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        question_type = str(entry.get("type") or "").strip()
        if question_type not in SURVEY_QUESTION_TYPES:
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        question_id = str(entry.get("id") or f"q{index + 1}").strip() or f"q{index + 1}"
        question_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in question_id)[:64]
        if question_id in seen_ids:
            question_id = f"{question_id}_{index + 1}"
        seen_ids.add(question_id)

        options_raw = entry.get("options") or []
        options: list[str] = []
        if isinstance(options_raw, list):
            for option in options_raw:
                text = str(option).strip()
                if text and text not in options:
                    options.append(text)
        if question_type == "yes_no":
            options = options[:2] if len(options) >= 2 else ["Yes", "No"]
        elif question_type in CHOICE_QUESTION_TYPES:
            if len(options) < 2:
                continue
        else:
            options = []

        image_url = str(entry.get("image_url") or "").strip()
        if image_url and not image_url.lower().startswith(("http://", "https://")):
            image_url = ""

        normalized.append(
            {
                "id": question_id,
                "type": question_type,
                "label": label[:240],
                "required": bool(entry.get("required")),
                "options": options,
                "image_url": image_url[:500],
            }
        )

    return normalized or default_survey_questions()


def parse_survey_questions(questions_json: str | None) -> list[dict[str, Any]]:
    try:
        raw = json.loads(questions_json or "[]")
    except json.JSONDecodeError:
        return default_survey_questions()
    return normalize_survey_questions(raw)


def survey_questions_json(questions: list[dict[str, Any]] | None = None) -> str:
    return json.dumps(normalize_survey_questions(questions or default_survey_questions()))


def survey_layout_questions(conn: Any, company_id: int) -> list[dict[str, Any]]:
    layout = ensure_survey_layout(conn, company_id)
    return parse_survey_questions(_row_get(layout, "questions_json"))


COVER_FIELDS = (
    "title",
    "subtitle",
    "market",
    "buyer",
    "author",
    "date_label",
    "logo_url",
    "banner_url",
    "footer_note",
)


def parse_cover_json(cover_json: str | None) -> dict[str, str]:
    """Read a stored Buy Book cover, keeping only fields a cover can carry.

    Every value is a string; blank means "nothing overridden", so the render
    fills its own default. Anything unusable (bad JSON, a list, an unknown
    key) is dropped rather than raised: a cover must never 500 a book.
    """
    try:
        raw = json.loads(cover_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cover: dict[str, str] = {}
    for field in COVER_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if value is None:
            continue
        cover[field] = str(value).strip()
    return cover


def parse_answers_json(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


INTERESTED_LABELS = {
    "yes", "y", "interested", "yes please", "im interested", "i'm interested", "want it", "would buy",
}
NOT_INTERESTED_LABELS = {
    "no", "n", "not interested", "not interested.", "no thanks", "pass", "would not buy",
}


def resolve_interest_question(questions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The question a response's interest counts come from.

    The author names it by id when the app created it (``interest``); otherwise a
    renamed question is still the interest one when it is the first answerable
    yes/no, single-choice or dropdown question. Returns None when the survey has
    no such question — the caller then falls back to the stored legacy column.
    """
    for question in questions:
        if question.get("id") == "interest" and question.get("type") in INTEREST_QUESTION_TYPES:
            return question
    for question in questions:
        if question.get("type") in INTEREST_QUESTION_TYPES:
            return question
    return None


def classify_interest_answer(question: dict[str, Any] | None, answers: dict[str, Any]) -> str:
    """'interested', 'not_interested', 'maybe' or '' for one response.

    Labels are recognised first (so ``Yes``/``Not interested`` survive a rename),
    then position: the first option is interested, the second is not.
    """
    if not question:
        return ""
    raw = (answers or {}).get(question.get("id") or "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in INTERESTED_LABELS:
        return "interested"
    if lowered in NOT_INTERESTED_LABELS:
        return "not_interested"
    options = [str(option).strip() for option in question.get("options") or []]
    for index, option in enumerate(options):
        if option.lower() == lowered:
            return "interested" if index == 0 else "not_interested" if index == 1 else "maybe"
    return ""


def mirror_legacy_response_fields(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
) -> tuple[str, str, int, int, str, str]:
    """Map custom answers onto legacy survey_responses columns."""
    recommend = "Maybe"
    feedback = ""
    # 3 is the column default, not a judgement: the dashboard hides these figures
    # unless the period has a scale question, and only a scale answer sets them.
    attention = 3
    retail = 3
    stands_out = "Neutral"
    price_reasonable = "Yes"

    interest = classify_interest_answer(resolve_interest_question(questions), answers)
    if interest == "interested":
        recommend = "Yes"
        stands_out = "Yes"
    elif interest == "not_interested":
        recommend = "No"
        stands_out = "No"
        price_reasonable = "No"

    first_text = next((q for q in questions if q["type"] == "text"), None)
    if first_text is not None:
        feedback = str(answers.get(first_text["id"], "") or "").strip()

    # First scale question mirrors into attention, second into engagement; with a
    # single scale question the engagement column keeps its default instead of
    # repeating the attention answer under a different label.
    scale_questions = [q for q in questions if q["type"] == "scale"]
    for slot, question in zip(("attention", "retail"), scale_questions[:2]):
        try:
            scale_value = int(answers.get(question["id"]))
        except (TypeError, ValueError):
            continue
        if 1 <= scale_value <= 5:
            if slot == "attention":
                attention = scale_value
            else:
                retail = scale_value

    return recommend, feedback, attention, retail, stands_out, price_reasonable


def ensure_survey_layout(conn: Any, company_id: int) -> Any:
    existing = execute(
        conn,
        "SELECT * FROM survey_layouts WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if existing is not None:
        return existing

    filter_fields_json = json.dumps(DEFAULT_SURVEY_FILTER_FIELDS)
    questions_json = survey_questions_json()
    if IS_POSTGRES:
        return execute(
            conn,
            """
            INSERT INTO survey_layouts (company_id, filter_fields_json, questions_json)
            VALUES (?, ?, ?)
            RETURNING *
            """,
            (company_id, filter_fields_json, questions_json),
        ).fetchone()

    cursor = execute(
        conn,
        """
        INSERT INTO survey_layouts (company_id, filter_fields_json, questions_json)
        VALUES (?, ?, ?)
        """,
        (company_id, filter_fields_json, questions_json),
    )
    return execute(conn, "SELECT * FROM survey_layouts WHERE id = ?", (cursor.lastrowid,)).fetchone()


def survey_filter_fields(conn: Any, company_id: int) -> list[str]:
    layout = ensure_survey_layout(conn, company_id)
    try:
        stored_fields = json.loads(layout["filter_fields_json"] or "[]")
    except json.JSONDecodeError:
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    if not isinstance(stored_fields, list):
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    return normalize_filter_fields([str(f) for f in stored_fields])


def public_book_token_for(conn: Any, company_id: int) -> str:
    """The company's public Buy Book token, minted the first time it is asked for."""
    row = execute(
        conn,
        "SELECT public_book_token FROM companies WHERE id = ?",
        (company_id,),
    ).fetchone()
    token = str((row["public_book_token"] if row is not None else "") or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(18)
    execute(conn, "UPDATE companies SET public_book_token = ? WHERE id = ?", (token, company_id))
    return token


def company_by_public_book_token(conn: Any, token: str) -> Any:
    """The company a public book link names, or None — no hint about other tenants."""
    text = str(token or "").strip()
    if not text:
        return None
    return execute(
        conn,
        "SELECT * FROM companies WHERE public_book_token = ?",
        (text,),
    ).fetchone()


def rotate_public_book_token(conn: Any, company_id: int) -> str:
    token = secrets.token_urlsafe(18)
    execute(conn, "UPDATE companies SET public_book_token = ? WHERE id = ?", (token, company_id))
    return token


def ensure_buy_book_layout(conn: Any, company_id: int) -> Any:
    existing = execute(
        conn,
        "SELECT * FROM buy_book_layouts WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if existing is not None:
        return existing

    filter_fields_json = json.dumps(DEFAULT_SURVEY_FILTER_FIELDS)
    values = (company_id, filter_fields_json, DEFAULT_BUY_BOOK_GROUP_BY)
    if IS_POSTGRES:
        return execute(
            conn,
            """
            INSERT INTO buy_book_layouts (company_id, filter_fields_json, group_by)
            VALUES (?, ?, ?)
            RETURNING *
            """,
            values,
        ).fetchone()

    cursor = execute(
        conn,
        """
        INSERT INTO buy_book_layouts (company_id, filter_fields_json, group_by)
        VALUES (?, ?, ?)
        """,
        values,
    )
    return execute(conn, "SELECT * FROM buy_book_layouts WHERE id = ?", (cursor.lastrowid,)).fetchone()


def buy_book_filter_fields(conn: Any, company_id: int) -> list[str]:
    layout = ensure_buy_book_layout(conn, company_id)
    try:
        stored_fields = json.loads(layout["filter_fields_json"] or "[]")
    except json.JSONDecodeError:
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    if not isinstance(stored_fields, list):
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    return normalize_filter_fields([str(f) for f in stored_fields])


def normalize_buy_book_group_by(value: str | None) -> str:
    if value in BUY_BOOK_GROUP_FIELDS:
        return value or DEFAULT_BUY_BOOK_GROUP_BY
    return DEFAULT_BUY_BOOK_GROUP_BY


def buy_book_group_by(conn: Any, company_id: int) -> str:
    layout = ensure_buy_book_layout(conn, company_id)
    return normalize_buy_book_group_by(_row_get(layout, "group_by", DEFAULT_BUY_BOOK_GROUP_BY))


# ---------------------------------------------------------------------------
# Layered page breaks (Buy Book)
# ---------------------------------------------------------------------------
#
# A Buy Book layer is ``{"field": <facet>, "new_page": bool}``. The stack is
# ordered: items sort by the first layer, then the next, then the catalog order
# inside the innermost group. A layer with ``new_page`` starts a fresh page
# whenever its value changes; an unticked layer still groups and sorts, it just
# does not force a break.

MAX_PAGE_BREAK_LAYERS = 4


def _layer_key(value: Any) -> str:
    """Comparison key for a facet value: trimmed, inner spacing collapsed, case folded.

    NetSuite hands the same brand back as "Tito's", "TITO'S " and "tito's"; all
    three have to land in one group or the book grows a page for each spelling.
    """
    return " ".join(str(value or "").split()).casefold()


def normalize_page_break_layers(value: Any) -> list[dict[str, Any]]:
    """Coerce stored JSON into a usable stack: valid facets, one entry per facet.

    Returns an empty list for anything unusable - the caller decides what an
    empty stack means, because "never saved" and "saved empty" are different.
    A repeated facet keeps its first layer and drops the later ones, so a bad
    stored stack can never 500 a page load.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []

    layers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in value:
        if isinstance(entry, str):
            field, new_page = entry, True
        elif isinstance(entry, dict):
            field = str(entry.get("field") or "").strip()
            new_page = bool(entry.get("new_page", True))
        else:
            continue
        if field in seen or not BUY_BOOK_GROUP_FIELDS.get(field):
            continue
        seen.add(field)
        layers.append({"field": field, "new_page": new_page})
        if len(layers) >= MAX_PAGE_BREAK_LAYERS:
            break
    return layers


def _legacy_layers(layout: Any) -> list[dict[str, Any]]:
    """A layout saved before layers existed: its single Group by, or no grouping."""
    raw = str(_row_get(layout, "group_by", "") or "").strip()
    if raw == "none":
        return []
    return [{"field": normalize_buy_book_group_by(raw), "new_page": True}]


def buy_book_page_break_layers(conn: Any, company_id: int) -> list[dict[str, Any]]:
    layout = ensure_buy_book_layout(conn, company_id)
    stored = _row_get(layout, "page_break_layers_json", None)
    text = str(stored or "").strip()
    group_by = str(_row_get(layout, "group_by", "") or "").strip()
    if not text or (text == "[]" and group_by != "none"):
        # Never saved: read the legacy Group by, which is what the book rendered with.
        # "[]" with a real group_by is the old column default, not a choice - a
        # saved empty stack always writes group_by 'none' with it.
        return _legacy_layers(layout)
    layers = normalize_page_break_layers(stored)
    if layers:
        return layers
    return _legacy_layers(layout)


def _layer_value(item: dict[str, Any], field: str) -> str:
    return str(item.get(field) or "").strip()


def _layer_order(items: list[dict[str, Any]], layers: list[dict[str, Any]]) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, str]]]:
    """Rank each facet value by where it first appears in the item order.

    The book keeps the catalog's own order - the Buy In's Multi-Order Sort -
    so "Summer, Fall, Holiday" stays in that sequence instead of being sorted
    into "Fall, Holiday, Summer". The label is the first spelling seen for the
    value, so one group never changes name halfway down a page.
    """
    ranks: dict[str, dict[str, int]] = {}
    labels: dict[str, dict[str, str]] = {}
    for layer in layers:
        field = layer["field"]
        ranks[field] = {}
        labels[field] = {}
        for item in items:
            raw = _layer_value(item, field)
            key = _layer_key(raw)
            if key and key not in ranks[field]:
                ranks[field][key] = len(ranks[field])
                labels[field][key] = raw
    return ranks, labels


def apply_buy_book_layers(items: list[dict[str, Any]], layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by the layer stack and derive the page breaks it implies.

    Each item carries ``group_headers``: one heading per layer from the first
    layer whose value changed, so the renderer draws nested group headings
    without re-deriving them. ``page_break_before`` becomes derived rather than
    stored - true only when a layer with ``new_page`` changed above it. A blank
    facet sorts last inside its parent and forms its own group. With no layers
    the book keeps catalog order and starts no pages.
    """
    if not layers:
        plain: list[dict[str, Any]] = []
        for item in items:
            next_item = dict(item)
            next_item["group_headers"] = []
            # Every item carries its whole path, carried forward from the last
            # value change, so a renderer can take the longest common prefix of
            # a page's paths without replaying the stack itself.
            next_item["group_path"] = []
            next_item["group_field"] = None
            next_item["group_label"] = None
            next_item["page_break_before"] = False
            plain.append(next_item)
        return plain

    ranks, labels = _layer_order(items, layers)

    def sort_key(pair: tuple[int, dict[str, Any]]) -> tuple:
        index, item = pair
        values: list[tuple[int, int]] = []
        for layer in layers:
            key = _layer_key(item.get(layer["field"]))
            values.append((1, 0) if not key else (0, ranks[layer["field"]][key]))
        return (*values, index)

    indexed = sorted(enumerate(items), key=sort_key)

    grouped: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    previous: tuple[str, ...] | None = None
    for index, (_, item) in enumerate(indexed):
        keys = tuple(_layer_key(item.get(layer["field"])) for layer in layers)
        headers: list[dict[str, Any]] = []
        breaks = False
        if previous is None:
            first_level = 0
        else:
            changed = [position for position, key in enumerate(keys) if key != previous[position]]
            first_level = changed[0] if changed else len(layers)
        # A page that opens on a deeper layer still names its whole path, so a
        # reader who lands on page 3 sees Brand > Program, not a bare Program.
        if index > 0 and any(layer["new_page"] for layer in layers[first_level:]):
            first_level = 0
        if first_level < len(layers):
            for position, layer in enumerate(layers):
                if position < first_level:
                    continue
                key = keys[position]
                headers.append(
                    {
                        "field": layer["field"],
                        "label": labels[layer["field"]][key] if key else "Ungrouped",
                        "level": position,
                    }
                )
                if layer["new_page"] and index > 0:
                    breaks = True

        next_item = dict(item)
        next_item["group_headers"] = headers
        next_item["group_path"] = _carry_group_path(path, headers, len(layers))
        path = next_item["group_path"]
        next_item["group_field"] = layers[0]["field"]
        next_item["group_label"] = (
            labels[layers[0]["field"]][keys[0]] if keys[0] else "Ungrouped"
        )
        next_item["page_break_before"] = breaks
        grouped.append(next_item)
        previous = keys
    return grouped


BUY_BOOK_FACET_FIELDS = ("brand", "channel", "product_type")


def buy_book_period_facets(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The brand / channel / product-type values present among these items.

    The book's drop-downs are built from one buy-in period's items, so a value
    from another period - or from the catalog but not this period - is never
    offered. Folded case and spacing match the layer grouping's: "Tito's",
    "TITO'S " and "tito's" are one value, shown with the first spelling seen.
    """
    values: dict[str, dict[str, str]] = {field: {} for field in BUY_BOOK_FACET_FIELDS}
    for item in items:
        for field in BUY_BOOK_FACET_FIELDS:
            display = " ".join(str(item.get(field) or "").split())
            if not display:
                continue
            values[field].setdefault(_layer_key(display), display)
    return {
        field: [values[field][key] for key in sorted(values[field])]
        for field in BUY_BOOK_FACET_FIELDS
    }


def _carry_group_path(
    previous: list[dict[str, Any]], headers: list[dict[str, Any]], levels: int
) -> list[dict[str, Any]]:
    """Merge newly named levels into the path carried from earlier items.

    ``group_headers`` names only the levels that changed, so an item with no
    heading still needs the brand it sits under: the path keeps every level
    from the last item that named it.
    """
    by_level = {int(entry["level"]): dict(entry) for entry in previous}
    for header in headers:
        by_level[int(header["level"])] = dict(header)
    return [by_level[level] for level in sorted(by_level) if level < levels]


def apply_buy_book_grouping(items: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Single-facet grouping kept for older callers; the Buy Book now uses layers."""
    field = BUY_BOOK_GROUP_FIELDS.get(normalize_buy_book_group_by(group_by))
    if not field:
        return items

    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            (str(pair[1].get(field) or "").strip().lower() or "ungrouped"),
            pair[0],
        )
    )
    grouped: list[dict[str, Any]] = []
    previous_label: str | None = None
    for index, (_, item) in enumerate(indexed):
        next_item = dict(item)
        label = str(next_item.get(field) or "").strip() or "Ungrouped"
        next_item["group_field"] = field
        next_item["group_label"] = label
        next_item["group_headers"] = [{"field": field, "label": label, "level": 0}]
        if index == 0:
            next_item["page_break_before"] = False
        elif label != previous_label:
            next_item["page_break_before"] = True
        else:
            next_item["page_break_before"] = bool(next_item.get("page_break_before"))
        previous_label = label
        grouped.append(next_item)
    return grouped


def parse_period_filter_fields(filter_fields_json: str | None) -> list[str]:
    try:
        stored_fields = json.loads(filter_fields_json or "[]")
    except json.JSONDecodeError:
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    if not isinstance(stored_fields, list):
        return list(DEFAULT_SURVEY_FILTER_FIELDS)
    return normalize_filter_fields([str(f) for f in stored_fields])


def survey_period_to_public(row: Any, *, item_count: int | None = None) -> dict:
    payload = {
        "id": row["id"],
        "company_id": row["company_id"],
        "name": row["name"],
        "status": row["status"],
        "opens_at": row["opens_at"],
        "closes_at": row["closes_at"],
        "filter_fields": parse_period_filter_fields(row["filter_fields_json"]),
        "questions": parse_survey_questions(_row_get(row, "questions_json")),
        "cover": parse_cover_json(_row_get(row, "cover_json")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if item_count is not None:
        payload["item_count"] = item_count
    return payload


def period_item_to_public(row: Any, *, questions: list[dict[str, Any]] | None = None) -> dict:
    """One survey item as the builder and the public page read it.

    ``id`` is the item's own row id, which is what an answer points at: it
    survives a rename, a re-cost and a reorder, so nothing has to be renumbered
    when a survey is edited after responses exist. ``catalog_item_id`` is only
    there for items copied in before surveys owned their items.
    """
    item_id = int(row["id"])
    payload = {
        "id": item_id,
        "period_item_id": item_id,
        "catalog_item_id": row["catalog_item_id"],
        "item_name": row["item_name"],
        "name": row["item_name"],
        "item_number": _row_get(row, "item_number", "") or "",
        "cost": _row_get(row, "cost", "") or "",
        "brand": row["brand"],
        "channel": row["channel"],
        "product_type": row["product_type"],
        "key_program": row["key_program"],
        "item_description": row["item_description"],
        "uom": row["uom"],
        "price": row["price"],
        "image_url": row["image_url"],
        "image_display_size": row["image_display_size"] if "image_display_size" in row.keys() else "medium",
        "image_fit": row["image_fit"] if "image_fit" in row.keys() else "contain",
        "sort_order": row["sort_order"],
        "survey_sort_order": row["sort_order"],
        "is_active": True,
        "in_survey": True,
        "in_buy_book": False,
        "response_width": row["response_width"] or DEFAULT_RESPONSE_WIDTH,
        "page_break_before": bool(row["page_break_before"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "question_ids": [question["id"] for question in questions] if questions is not None else [],
        "questions": questions or [],
    }
    return payload


def mint_survey_token() -> str:
    """A survey link credential: 24 random bytes, url-safe, unique per period."""
    return secrets.token_urlsafe(24)


def get_open_survey_period(conn: Any, company_id: int) -> Any:
    """The period a shopper may submit into: open *and* not past its close time.

    Status alone is not enough — a period whose ``closes_at`` has passed is
    closed even if nobody flipped the status, and submission is the whole point
    of this lookup. "No close time" is NULL, and only NULL: a blank is never
    written (``normalize_timestamp``) and pre-existing blanks are cleaned on
    boot, because Postgres cannot hold one in a ``timestamptz``.
    """
    return execute(
        conn,
        """
        SELECT * FROM survey_periods
        WHERE company_id = ? AND status = 'open'
          AND (closes_at IS NULL OR closes_at > ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (company_id, utc_now().isoformat()),
    ).fetchone()


def get_survey_period_by_token(conn: Any, company_id: int, token: Any) -> Any:
    """The period this token belongs to inside this company, or None.

    Scoping by company is what makes another company's token a 404 rather than
    a cross-tenant read: the token is looked up, never trusted on its own.
    """
    text = str(token or "").strip()
    if not text:
        return None
    return execute(
        conn,
        "SELECT * FROM survey_periods WHERE company_id = ? AND survey_token = ?",
        (company_id, text),
    ).fetchone()


def survey_period_is_closed(period: Any) -> bool:
    """Closed means status closed OR ``closes_at`` already passed.

    An unreadable timestamp fails closed: a survey whose window cannot be
    evaluated is not one to accept answers into.
    """
    if period is None:
        return True
    if str(_row_get(period, "status", "") or "") != "open":
        return True
    closes_at = str(_row_get(period, "closes_at", "") or "").strip()
    if not closes_at:
        return False
    try:
        parsed = datetime.fromisoformat(closes_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= utc_now()


def get_survey_period(conn: Any, company_id: int, period_id: int) -> Any:
    return execute(
        conn,
        "SELECT * FROM survey_periods WHERE id = ? AND company_id = ?",
        (period_id, company_id),
    ).fetchone()


def list_period_items(conn: Any, period_id: int) -> list[Any]:
    return execute(
        conn,
        """
        SELECT * FROM survey_period_items
        WHERE survey_period_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (period_id,),
    ).fetchall()


def count_period_items(conn: Any, period_id: int) -> int:
    row = execute(
        conn,
        "SELECT COUNT(*) AS count FROM survey_period_items WHERE survey_period_id = ?",
        (period_id,),
    ).fetchone()
    return int(row["count"] if row else 0)


def period_item_question_map(conn: Any, period_id: int) -> dict[int, list[str]]:
    """Which pool questions each item of this survey asks, in tick order."""
    rows = execute(
        conn,
        """
        SELECT q.survey_period_item_id, q.question_id
        FROM survey_period_item_questions q
        JOIN survey_period_items i ON i.id = q.survey_period_item_id
        WHERE i.survey_period_id = ?
        ORDER BY q.id ASC
        """,
        (period_id,),
    ).fetchall()
    ticks: dict[int, list[str]] = {}
    for row in rows:
        ticks.setdefault(int(row["survey_period_item_id"]), []).append(str(row["question_id"]))
    return ticks


def questions_for_item(pool: list[dict[str, Any]], question_ids: list[str]) -> list[dict[str, Any]]:
    """The item's questions, defined by the survey's pool and in pool order.

    A tick that names no pool question is dropped: the pool is the only place a
    question is defined, so a question removed from the pool takes its ticks
    with it rather than leaving a blank card on the public page.
    """
    if not question_ids:
        return []
    wanted = set(question_ids)
    return [dict(question) for question in pool if question["id"] in wanted]


def period_items_public(conn: Any, period: Any) -> list[dict[str, Any]]:
    """Every item of a survey, with the questions that item asks."""
    pool = parse_survey_questions(_row_get(period, "questions_json"))
    ticks = period_item_question_map(conn, int(period["id"]))
    return [
        period_item_to_public(row, questions=questions_for_item(pool, ticks.get(int(row["id"]), [])))
        for row in list_period_items(conn, int(period["id"]))
    ]


def set_period_item_questions(conn: Any, item_id: int, question_ids: list[str]) -> None:
    execute(conn, "DELETE FROM survey_period_item_questions WHERE survey_period_item_id = ?", (item_id,))
    seen: set[str] = set()
    for question_id in question_ids:
        text = str(question_id or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        execute(
            conn,
            "INSERT INTO survey_period_item_questions (survey_period_item_id, question_id) VALUES (?, ?)",
            (item_id, text),
        )


def save_period_items(conn: Any, period_id: int, items: list[Any], pool_ids: list[str]) -> list[int]:
    """Upsert a survey's items by id, and drop the ones the save removed.

    Order is the array's order. An item that already exists is updated in
    place, so its id - and every answer that points at it - survives an edit;
    only an item the caller left out is deleted (with its own answers).
    """
    existing = {int(row["id"]): row for row in list_period_items(conn, period_id)}
    kept: list[int] = []
    for index, item in enumerate(items, start=1):
        ticked = [question_id for question_id in (getattr(item, "question_ids", None) or []) if question_id in pool_ids]
        item_id = getattr(item, "id", None)
        values = (
            str(getattr(item, "item_name", "") or "").strip(),
            str(getattr(item, "cost", "") or "").strip()[:120],
            str(getattr(item, "image_url", "") or "").strip()[:1000],
            str(getattr(item, "item_number", "") or "").strip()[:120],
            str(getattr(item, "item_description", "") or "").strip(),
            index,
        )
        if item_id is not None and int(item_id) in existing:
            execute(
                conn,
                """
                UPDATE survey_period_items
                SET item_name = ?, cost = ?, image_url = ?, item_number = ?,
                    item_description = ?, sort_order = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND survey_period_id = ?
                """,
                (*values, int(item_id), period_id),
            )
            kept_id = int(item_id)
        else:
            if IS_POSTGRES:
                row = execute(
                    conn,
                    """
                    INSERT INTO survey_period_items
                        (survey_period_id, catalog_item_id, item_name, cost, image_url,
                         item_number, item_description, sort_order)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (period_id, *values),
                ).fetchone()
                kept_id = int(row["id"])
            else:
                cursor = execute(
                    conn,
                    """
                    INSERT INTO survey_period_items
                        (survey_period_id, catalog_item_id, item_name, cost, image_url,
                         item_number, item_description, sort_order)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
                    """,
                    (period_id, *values),
                )
                kept_id = int(cursor.lastrowid)
        set_period_item_questions(conn, kept_id, ticked)
        kept.append(kept_id)

    for item_id in existing:
        if item_id in kept:
            continue
        execute(
            conn,
            "DELETE FROM survey_period_items WHERE id = ? AND survey_period_id = ?",
            (item_id, period_id),
        )
    return kept


def copy_period_items(conn: Any, source_period_id: int, target_period_id: int) -> int:
    """Copy a survey's items, their ticks and their order into another survey."""
    rows = list_period_items(conn, source_period_id)
    ticks = period_item_question_map(conn, source_period_id)
    for index, row in enumerate(rows, start=1):
        values = (
            target_period_id,
            row["catalog_item_id"],
            row["item_name"],
            _row_get(row, "cost", "") or "",
            row["image_url"],
            _row_get(row, "item_number", "") or "",
            row["item_description"],
            index,
        )
        if IS_POSTGRES:
            created = execute(
                conn,
                """
                INSERT INTO survey_period_items
                    (survey_period_id, catalog_item_id, item_name, cost, image_url,
                     item_number, item_description, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                """,
                values,
            ).fetchone()
            new_id = int(created["id"])
        else:
            cursor = execute(
                conn,
                """
                INSERT INTO survey_period_items
                    (survey_period_id, catalog_item_id, item_name, cost, image_url,
                     item_number, item_description, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            new_id = int(cursor.lastrowid)
        set_period_item_questions(conn, new_id, ticks.get(int(row["id"]), []))
    return len(rows)


def period_response_count(conn: Any, period_id: int) -> int:
    """Submissions received by one survey (a row per respondent, not per item)."""
    row = execute(
        conn,
        "SELECT COUNT(*) AS count FROM survey_responses"
        " WHERE survey_period_id = ? AND survey_item_id IS NULL",
        (period_id,),
    ).fetchone()
    return int(row["count"] if row else 0)


def find_submission(conn: Any, company_id: int, period_id: int, email: str) -> Any:
    return execute(
        conn,
        """
        SELECT * FROM survey_responses
        WHERE company_id = ? AND survey_period_id = ?
          AND survey_item_id IS NULL AND lower(email) = ?
        """,
        (company_id, period_id, str(email or "").lower()),
    ).fetchone()


def save_submission_answers(
    conn: Any,
    response_id: int,
    item_id: int,
    answers: dict[str, Any],
) -> int:
    """Replace one submission's answers for one item, one row per question.

    Only questions this item asks are written, and the whole set for that item
    is replaced: editing an answer removes the old row rather than leaving the
    two behind. The value is split by type - a scale goes in ``answer_number``
    so an average is a query, a checkbox in ``answer_options_json``, text and
    choices in ``answer_text``.
    """
    execute(
        conn,
        "DELETE FROM survey_answers WHERE response_id = ? AND survey_period_item_id = ?",
        (response_id, item_id),
    )
    written = 0
    for question_id, value in answers.items():
        if value is None:
            continue
        text = ""
        options: list[str] = []
        number: float | None = None
        if isinstance(value, list):
            options = [str(part) for part in value if str(part).strip()]
            if not options:
                continue
        elif isinstance(value, bool):
            text = "Yes" if value else "No"
        elif isinstance(value, (int, float)):
            number = float(value)
            text = str(value)
        else:
            text = str(value).strip()
            if not text:
                continue
        execute(
            conn,
            """
            INSERT INTO survey_answers
                (response_id, survey_period_item_id, question_id, answer_text,
                 answer_options_json, answer_number)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (response_id, item_id, str(question_id), text, json.dumps(options), number),
        )
        written += 1
    return written


def close_open_survey_periods(conn: Any, company_id: int) -> None:
    execute(
        conn,
        """
        UPDATE survey_periods
        SET status = 'closed',
            closes_at = COALESCE(closes_at, ?),
            updated_at = CURRENT_TIMESTAMP
        WHERE company_id = ? AND status = 'open'
        """,
        (utc_now().isoformat(), company_id),
    )


def ensure_legacy_survey_period(conn: Any, company_id: int) -> Any:
    """Create an empty open survey if the company has never had one.

    It used to be filled from the catalog; a survey owns its items now, so it
    starts empty and the admin adds the items it should show.
    """
    existing = execute(
        conn,
        "SELECT * FROM survey_periods WHERE company_id = ? ORDER BY id ASC LIMIT 1",
        (company_id,),
    ).fetchone()
    if existing is not None:
        return existing

    filter_fields_json = json.dumps(survey_filter_fields(conn, company_id))
    questions_json = survey_questions_json(survey_layout_questions(conn, company_id))
    values = (
        company_id,
        "Current Survey",
        "open",
        utc_now().isoformat(),
        mint_survey_token(),
        filter_fields_json,
        questions_json,
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

    execute(
        conn,
        """
        UPDATE survey_responses
        SET survey_period_id = ?
        WHERE company_id = ? AND survey_period_id IS NULL
        """,
        (period["id"], company_id),
    )
    return period


# ---------------------------------------------------------------------------
# Buy Book PDF
# ---------------------------------------------------------------------------

MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_book_date(value: datetime) -> str:
    """The cover's date label: "18 Nov 2026", the same in every locale."""
    return f"{value.day} {MONTH_LABELS[value.month - 1]} {value.year}"


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def buy_in_window(period: Any) -> str:
    """The buy-in window line printed in the page band under the heading."""
    opens = _parse_iso_datetime(_row_get(period, "opens_at", None))
    closes = _parse_iso_datetime(_row_get(period, "closes_at", None))
    if opens and closes:
        return f"Buy-in window: {format_book_date(opens)} – {format_book_date(closes)}"
    if opens:
        return f"Buy-in window opens {format_book_date(opens)}"
    if closes:
        return f"Buy-in window closes {format_book_date(closes)}"
    return ""


def resolve_buy_book_period(conn: Any, company_id: int, period_id: int | None = None) -> Any:
    """The period the book is labelled with: the chosen one, else the open one,
    else the latest, else the legacy period made from the catalog.

    It only labels the cover and the page band - the items are the company's
    whole Buy Book, so a closed period still prints a book.
    """
    if period_id is not None:
        return get_survey_period(conn, company_id, int(period_id))
    period = get_open_survey_period(conn, company_id)
    if period is not None:
        return period
    period = execute(
        conn,
        "SELECT * FROM survey_periods WHERE company_id = ? ORDER BY id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    if period is not None:
        return period
    return ensure_legacy_survey_period(conn, company_id)


def buy_book_pdf_rows(conn: Any, company_id: int, item_ids: list[int] | None = None) -> list[Any]:
    """The book's items, in catalog order.

    No selection means the whole Buy Book - the admin layout GET returns every
    active item, so the ``in_buy_book`` filter is applied here rather than
    inherited from the caller.
    """
    if item_ids is not None:
        ids = sorted({int(item_id) for item_id in item_ids})
        if not ids:
            return []
        placeholders = ", ".join("?" for _ in ids)
        return execute(
            conn,
            f"""
            SELECT * FROM survey_items
            WHERE company_id = ? AND is_active = ? AND id IN ({placeholders})
            ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC
            """,
            (company_id, True, *ids),
        ).fetchall()
    return execute(
        conn,
        """
        SELECT * FROM survey_items
        WHERE company_id = ? AND is_active = ? AND in_buy_book = ?
        ORDER BY buy_book_sort_order ASC, sort_order ASC, id ASC
        """,
        (company_id, True, 1),
    ).fetchall()


def resolve_buy_book_cover(
    company: Any,
    period: Any,
    user: Any,
    clock_at: datetime,
) -> dict[str, str]:
    """Fill a period's cover overrides out to the whole cover.

    A stored blank means "use the default", except where the default is blank
    too (the buyer line simply disappears). Only the render ever needs the
    defaults, so this lives here rather than in the public period payload.
    ``clock_at`` is the admin's local clock, which only the default date label
    reads; a stored ``date_label`` still wins.
    """
    period_name = str(_row_get(period, "name", "") or "").strip()
    company_name = str(_row_get(company, "name", "") or "").strip()
    stored = parse_cover_json(_row_get(period, "cover_json", None))
    defaults = {
        "title": f"Buy Book — {period_name}" if period_name else "Buy Book",
        "subtitle": "",
        "market": str(_row_get(company, "market_label", "") or "").strip(),
        "buyer": "",
        "author": str(_row_get(user, "name", "") or "").strip(),
        "date_label": format_book_date(clock_at),
        "logo_url": str(_row_get(company, "logo_url", "") or "").strip(),
        "banner_url": str(_row_get(company, "banner_url", "") or "").strip(),
        "footer_note": f"MRL Creative · {company_name}" if company_name else "MRL Creative",
    }
    cover = {field: stored.get(field, "") or defaults[field] for field in COVER_FIELDS}
    cover["period_name"] = period_name
    cover["window_line"] = buy_in_window(period)
    cover["company_name"] = company_name
    return cover


# ---------------------------------------------------------------------------
# Sheet config
# ---------------------------------------------------------------------------

def get_sheet_config(company_id: int | None = None, *, all_companies: bool = False) -> dict:
    from config import SHEETS
    sheets = {
        sheet_key: {
            "label": sheet_config["label"],
            "dateLabel": sheet_config["dateLabel"],
            "statuses": [],
        }
        for sheet_key, sheet_config in SHEETS.items()
    }
    with connect() as conn:
        if all_companies:
            rows = execute(
                conn,
                """
                SELECT sheet, status, MIN(sort_order) AS sort_order
                FROM sheet_statuses
                GROUP BY sheet, status
                ORDER BY sheet ASC, sort_order ASC, status ASC
                """,
            ).fetchall()
        else:
            scoped_company_id = default_company_id() if company_id is None else company_id
            rows = execute(
                conn,
                """
                SELECT * FROM sheet_statuses
                WHERE company_id = ?
                ORDER BY sheet ASC, sort_order ASC, id ASC
                """,
                (scoped_company_id,),
            ).fetchall()

    for row in rows:
        if row["sheet"] in sheets and row["status"] not in sheets[row["sheet"]]["statuses"]:
            sheets[row["sheet"]]["statuses"].append(row["status"])

    return sheets
