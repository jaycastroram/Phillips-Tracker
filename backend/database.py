from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from typing import Any

from config import (
    COMPANY_SCOPED_TABLES,
    DB_PATH,
    DATABASE_URL,
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
        "is_active": bool(row["is_active"]),
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


def survey_item_to_public(row: sqlite3.Row) -> dict:
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
        "survey_sort_order": row["survey_sort_order"],
        "response_width": row["response_width"] or DEFAULT_RESPONSE_WIDTH,
        "page_break_before": bool(row["page_break_before"]),
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
        "created_at": row["created_at"],
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

def normalize_filter_fields(fields: list[str]) -> list[str]:
    normalized = []
    for field in fields:
        if field in SURVEY_FILTER_FIELDS and field not in normalized:
            normalized.append(field)
    return normalized


def ensure_survey_layout(conn: Any, company_id: int) -> Any:
    existing = execute(
        conn,
        "SELECT * FROM survey_layouts WHERE company_id = ?",
        (company_id,),
    ).fetchone()
    if existing is not None:
        return existing

    filter_fields_json = json.dumps(DEFAULT_SURVEY_FILTER_FIELDS)
    if IS_POSTGRES:
        return execute(
            conn,
            "INSERT INTO survey_layouts (company_id, filter_fields_json) VALUES (?, ?) RETURNING *",
            (company_id, filter_fields_json),
        ).fetchone()

    cursor = execute(
        conn,
        "INSERT INTO survey_layouts (company_id, filter_fields_json) VALUES (?, ?)",
        (company_id, filter_fields_json),
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
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if item_count is not None:
        payload["item_count"] = item_count
    return payload


def period_item_to_public(row: Any) -> dict:
    """Shape period items like catalog survey items so the public UI can reuse them."""
    catalog_id = row["catalog_item_id"]
    return {
        "id": catalog_id,
        "period_item_id": row["id"],
        "catalog_item_id": catalog_id,
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
        "survey_sort_order": row["sort_order"],
        "is_active": True,
        "in_survey": True,
        "in_buy_book": False,
        "response_width": row["response_width"] or DEFAULT_RESPONSE_WIDTH,
        "page_break_before": bool(row["page_break_before"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_open_survey_period(conn: Any, company_id: int) -> Any:
    return execute(
        conn,
        """
        SELECT * FROM survey_periods
        WHERE company_id = ? AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (company_id,),
    ).fetchone()


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


def copy_catalog_items_into_period(
    conn: Any,
    period_id: int,
    company_id: int,
    *,
    only_in_survey: bool = True,
    source_period_id: int | None = None,
) -> int:
    """Populate period items from a prior period or the live catalog."""
    execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (period_id,))

    if source_period_id is not None:
        source_items = list_period_items(conn, source_period_id)
        inserted = 0
        for index, item in enumerate(source_items, start=1):
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
                    item["catalog_item_id"],
                    item["item_name"],
                    item["brand"],
                    item["channel"],
                    item["product_type"],
                    item["key_program"],
                    item["item_description"],
                    item["uom"],
                    item["price"],
                    item["image_url"],
                    item["image_display_size"] if "image_display_size" in item.keys() else "medium",
                    item["image_fit"] if "image_fit" in item.keys() else "contain",
                    index,
                    item["response_width"] or DEFAULT_RESPONSE_WIDTH,
                    1 if item["page_break_before"] and index > 1 else 0,
                ),
            )
            inserted += 1
        return inserted

    clauses = ["company_id = ?", "is_active = ?"]
    args: list[Any] = [company_id, True]
    if only_in_survey:
        clauses.append("in_survey = ?")
        args.append(1)

    rows = execute(
        conn,
        f"""
        SELECT * FROM survey_items
        WHERE {' AND '.join(clauses)}
        ORDER BY survey_sort_order ASC, sort_order ASC, id ASC
        """,
        args,
    ).fetchall()

    inserted = 0
    for index, item in enumerate(rows, start=1):
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
                item["id"],
                item["item_name"],
                item["brand"],
                item["channel"],
                item["product_type"],
                item["key_program"],
                item["item_description"],
                item["uom"],
                item["price"],
                item["image_url"],
                item["image_display_size"] if "image_display_size" in item.keys() else "medium",
                item["image_fit"] if "image_fit" in item.keys() else "contain",
                index,
                item["response_width"] or DEFAULT_RESPONSE_WIDTH,
                1 if item["page_break_before"] and index > 1 else 0,
            ),
        )
        inserted += 1
    return inserted


def ensure_legacy_survey_period(conn: Any, company_id: int) -> Any:
    """Create an open period from the current catalog if none exist yet."""
    existing = execute(
        conn,
        "SELECT * FROM survey_periods WHERE company_id = ? ORDER BY id ASC LIMIT 1",
        (company_id,),
    ).fetchone()
    if existing is not None:
        return existing

    filter_fields_json = json.dumps(survey_filter_fields(conn, company_id))
    values = (company_id, "Current Survey", "open", utc_now().isoformat(), filter_fields_json)
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

    copy_catalog_items_into_period(conn, period["id"], company_id, only_in_survey=True)
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
# Sheet config
# ---------------------------------------------------------------------------

def get_sheet_config(company_id: int | None = None) -> dict:
    from config import SHEETS
    sheets = {
        sheet_key: {
            "label": sheet_config["label"],
            "dateLabel": sheet_config["dateLabel"],
            "statuses": [],
        }
        for sheet_key, sheet_config in SHEETS.items()
    }
    scoped_company_id = default_company_id() if company_id is None else company_id
    with connect() as conn:
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
        if row["sheet"] in sheets:
            sheets[row["sheet"]]["statuses"].append(row["status"])

    return sheets
