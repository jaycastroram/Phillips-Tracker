from __future__ import annotations

import json

from config import ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD, COMPANY_SCOPED_TABLES, IS_POSTGRES
from database import (
    connect,
    ensure_buy_book_layout,
    ensure_column,
    ensure_default_company,
    ensure_legacy_survey_period,
    ensure_survey_layout,
    execute,
    hash_password,
    mint_survey_token,
    utc_now,
)
from seed import (
    backfill_survey_sort_order,
    seed_demo_companies,
    seed_kanban_columns_if_empty,
    seed_sheet_statuses_if_empty,
    seed_tracker_items_if_empty,
    sync_default_survey_items,
    sync_seeded_tracker_item_catalog,
)


def _create_companies_table(conn) -> None:
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id SERIAL PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                logo_url TEXT NOT NULL DEFAULT '',
                banner_url TEXT NOT NULL DEFAULT '',
                netsuite_image_base TEXT NOT NULL DEFAULT '',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                logo_url TEXT NOT NULL DEFAULT '',
                banner_url TEXT NOT NULL DEFAULT '',
                netsuite_image_base TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _create_survey_layouts_table(conn) -> None:
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_layouts (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL UNIQUE,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buy_book_layouts (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL UNIQUE,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                group_by TEXT NOT NULL DEFAULT 'brand',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_layouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL UNIQUE,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buy_book_layouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL UNIQUE,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                group_by TEXT NOT NULL DEFAULT 'brand',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _create_survey_periods_tables(conn) -> None:
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_periods (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'open', 'closed')),
                opens_at TIMESTAMPTZ,
                closes_at TIMESTAMPTZ,
                survey_token TEXT,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_period_items (
                id SERIAL PRIMARY KEY,
                survey_period_id INTEGER NOT NULL REFERENCES survey_periods(id) ON DELETE CASCADE,
                catalog_item_id INTEGER NOT NULL REFERENCES survey_items(id) ON DELETE CASCADE,
                item_name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                product_type TEXT NOT NULL DEFAULT '',
                key_program TEXT NOT NULL DEFAULT '',
                item_description TEXT NOT NULL DEFAULT '',
                uom TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                image_display_size TEXT NOT NULL DEFAULT 'medium',
                image_fit TEXT NOT NULL DEFAULT 'contain',
                sort_order INTEGER NOT NULL DEFAULT 0,
                response_width INTEGER NOT NULL DEFAULT 36,
                page_break_before INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'open', 'closed')),
                opens_at TEXT,
                closes_at TEXT,
                survey_token TEXT,
                filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_period_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_period_id INTEGER NOT NULL,
                catalog_item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '',
                channel TEXT NOT NULL DEFAULT '',
                product_type TEXT NOT NULL DEFAULT '',
                key_program TEXT NOT NULL DEFAULT '',
                item_description TEXT NOT NULL DEFAULT '',
                uom TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                image_display_size TEXT NOT NULL DEFAULT 'medium',
                image_fit TEXT NOT NULL DEFAULT 'contain',
                sort_order INTEGER NOT NULL DEFAULT 0,
                response_width INTEGER NOT NULL DEFAULT 36,
                page_break_before INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (survey_period_id) REFERENCES survey_periods(id) ON DELETE CASCADE,
                FOREIGN KEY (catalog_item_id) REFERENCES survey_items(id) ON DELETE CASCADE
            )
        """)


def _create_core_tables(conn) -> None:
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracker_items (
                id SERIAL PRIMARY KEY, sheet TEXT NOT NULL, source_row INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0, date_or_buy TEXT, current_status TEXT,
                visual_reference TEXT, brand TEXT, program_name TEXT, item_name TEXT, qty TEXT,
                important_notes TEXT, mrl_order_number TEXT, estimated_ship_date TEXT,
                estimated_ihd TEXT, tracking TEXT, extra_json TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin', 'editor')),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                user_email TEXT NOT NULL, action TEXT NOT NULL, sheet TEXT, item_id INTEGER,
                before_json TEXT, after_json TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kanban_columns (
                id SERIAL PRIMARY KEY, title TEXT NOT NULL,
                statuses_json TEXT NOT NULL DEFAULT '[]', sort_order INTEGER NOT NULL DEFAULT 0,
                is_visible BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sheet_statuses (
                id SERIAL PRIMARY KEY, company_id INTEGER, sheet TEXT NOT NULL, status TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company_id, sheet, status)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_items (
                id SERIAL PRIMARY KEY, item_name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT '',
                product_type TEXT NOT NULL DEFAULT '', key_program TEXT NOT NULL DEFAULT '',
                item_description TEXT NOT NULL DEFAULT '', uom TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0, is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_responses (
                id SERIAL PRIMARY KEY,
                survey_item_id INTEGER NOT NULL REFERENCES survey_items(id) ON DELETE CASCADE,
                email TEXT NOT NULL, attention_effectiveness INTEGER NOT NULL,
                recommend_rollout TEXT NOT NULL, retail_engagement INTEGER NOT NULL,
                stands_out TEXT NOT NULL, price_reasonable TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracker_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sheet TEXT NOT NULL, source_row INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0, date_or_buy TEXT, current_status TEXT,
                visual_reference TEXT, brand TEXT, program_name TEXT, item_name TEXT, qty TEXT,
                important_notes TEXT, mrl_order_number TEXT, estimated_ship_date TEXT,
                estimated_ihd TEXT, tracking TEXT, extra_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'editor')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                user_email TEXT NOT NULL, action TEXT NOT NULL, sheet TEXT, item_id INTEGER,
                before_json TEXT, after_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kanban_columns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                statuses_json TEXT NOT NULL DEFAULT '[]', sort_order INTEGER NOT NULL DEFAULT 0,
                is_visible INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sheet_statuses (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sheet TEXT NOT NULL, status TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sheet, status)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '', channel TEXT NOT NULL DEFAULT '',
                product_type TEXT NOT NULL DEFAULT '', key_program TEXT NOT NULL DEFAULT '',
                item_description TEXT NOT NULL DEFAULT '', uom TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_item_id INTEGER NOT NULL, email TEXT NOT NULL,
                attention_effectiveness INTEGER NOT NULL, recommend_rollout TEXT NOT NULL,
                retail_engagement INTEGER NOT NULL, stands_out TEXT NOT NULL,
                price_reasonable TEXT NOT NULL, feedback TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (survey_item_id) REFERENCES survey_items(id) ON DELETE CASCADE
            )
        """)


def _create_netsuite_item_images_table(conn) -> None:
    """Records imported from NetSuite: internal id -> media file / image URL.

    The app never formats an image URL from an item id, so this table is where
    the URL comes from. One row per company dataset and NetSuite internal id.
    """
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS netsuite_item_images (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                internal_id TEXT NOT NULL,
                media_file TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, internal_id)
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS netsuite_item_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                internal_id TEXT NOT NULL,
                media_file TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, internal_id)
            )
        """)


def _ensure_company_columns(conn) -> None:
    for table_name in COMPANY_SCOPED_TABLES:
        ensure_column(conn, table_name, "company_id", "INTEGER")
    ensure_column(conn, "companies", "logo_url", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "companies", "banner_url", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "companies", "netsuite_image_base", "TEXT NOT NULL DEFAULT ''")
    # The market a company sells into; the Buy Book cover prints it, and a
    # period's cover_json can override it for one book.
    ensure_column(conn, "companies", "market_label", "TEXT")
    ensure_column(conn, "users", "company_id", "INTEGER")


def _ensure_company_indexes(conn) -> None:
    for table_name in COMPANY_SCOPED_TABLES:
        execute(
            conn,
            f"CREATE INDEX IF NOT EXISTS {table_name}_company_idx ON {table_name} (company_id)",
        )


def _backfill_company_ids(conn, company_id: int) -> None:
    for table_name in COMPANY_SCOPED_TABLES:
        execute(conn, f"UPDATE {table_name} SET company_id = ? WHERE company_id IS NULL", (company_id,))
    execute(conn, "UPDATE users SET company_id = ? WHERE company_id IS NULL", (company_id,))


def _scope_sheet_status_uniqueness(conn) -> None:
    """Migrate old UNIQUE(sheet, status) constraint to UNIQUE(company_id, sheet, status)."""
    if IS_POSTGRES:
        # Production DBs created before multi-company still have UNIQUE(sheet, status),
        # which blocks seeding statuses for additional companies.
        execute(
            conn,
            "ALTER TABLE sheet_statuses DROP CONSTRAINT IF EXISTS sheet_statuses_sheet_status_key",
        )
        execute(conn, "DROP INDEX IF EXISTS sheet_statuses_sheet_status_key")
        execute(conn, """
            CREATE UNIQUE INDEX IF NOT EXISTS sheet_statuses_company_sheet_status_idx
            ON sheet_statuses (company_id, sheet, status)
        """)
        return

    indexes = execute(conn, "PRAGMA index_list(sheet_statuses)").fetchall()
    has_company_unique = any(
        ["company_id", "sheet", "status"] == [c["name"] for c in execute(conn, f"PRAGMA index_info({idx['name']})").fetchall()]
        for idx in indexes if idx["unique"]
    )
    needs_rebuild = any(
        ["sheet", "status"] == [c["name"] for c in execute(conn, f"PRAGMA index_info({idx['name']})").fetchall()]
        for idx in indexes if idx["unique"]
    )

    if not needs_rebuild:
        if not has_company_unique:
            execute(conn, """
                CREATE UNIQUE INDEX IF NOT EXISTS sheet_statuses_company_sheet_status_idx
                ON sheet_statuses (company_id, sheet, status)
            """)
        return

    execute(conn, "PRAGMA foreign_keys = OFF")
    execute(conn, "DROP TABLE IF EXISTS sheet_statuses_company_scoped")
    execute(conn, """
        CREATE TABLE sheet_statuses_company_scoped (
            id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER,
            sheet TEXT NOT NULL, status TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company_id, sheet, status)
        )
    """)
    execute(conn, """
        INSERT INTO sheet_statuses_company_scoped (id, company_id, sheet, status, sort_order, created_at, updated_at)
        SELECT id, company_id, sheet, status, sort_order, created_at, updated_at FROM sheet_statuses
    """)
    execute(conn, "DROP TABLE sheet_statuses")
    execute(conn, "ALTER TABLE sheet_statuses_company_scoped RENAME TO sheet_statuses")
    execute(conn, "PRAGMA foreign_keys = ON")


def _allow_null_page_break_layers(conn) -> None:
    """Make page_break_layers_json nullable on databases that got it with a default.

    "Never saved" (NULL) and "saved empty" ('[]') are different books, so the
    column cannot default to '[]'. A database that ran an earlier build of the
    layer stack has it NOT NULL DEFAULT '[]'; relax it here so every install
    ends up with the same column.
    """
    if IS_POSTGRES:
        column = execute(
            conn,
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'buy_book_layouts' AND column_name = 'page_break_layers_json'
            """,
        ).fetchone()
        if column is not None and str(column["is_nullable"]).upper() == "NO":
            execute(conn, "ALTER TABLE buy_book_layouts ALTER COLUMN page_break_layers_json DROP NOT NULL")
        return

    columns = execute(conn, "PRAGMA table_info(buy_book_layouts)").fetchall()
    column = next((row for row in columns if row["name"] == "page_break_layers_json"), None)
    if column is None or not int(column["notnull"]):
        return
    execute(conn, "PRAGMA foreign_keys = OFF")
    execute(conn, "DROP TABLE IF EXISTS buy_book_layouts_layers_nullable")
    execute(conn, """
        CREATE TABLE buy_book_layouts_layers_nullable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL UNIQUE,
            filter_fields_json TEXT NOT NULL DEFAULT '["brand","channel","product_type"]',
            group_by TEXT NOT NULL DEFAULT 'brand',
            page_break_layers_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    execute(
        conn,
        """
        INSERT INTO buy_book_layouts_layers_nullable
            (id, company_id, filter_fields_json, group_by, page_break_layers_json, created_at, updated_at)
        SELECT id, company_id, filter_fields_json, group_by, page_break_layers_json, created_at, updated_at
        FROM buy_book_layouts
        """,
    )
    execute(conn, "DROP TABLE buy_book_layouts")
    execute(conn, "ALTER TABLE buy_book_layouts_layers_nullable RENAME TO buy_book_layouts")
    execute(conn, "PRAGMA foreign_keys = ON")


def _ensure_survey_layout_columns(conn) -> None:
    from config import DEFAULT_RESPONSE_WIDTH
    from database import survey_questions_json

    ensure_column(conn, "survey_items", "in_survey", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "survey_items", "in_buy_book", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "survey_items", "survey_sort_order", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "survey_items", "buy_book_sort_order", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "survey_items", "response_width", f"INTEGER NOT NULL DEFAULT {DEFAULT_RESPONSE_WIDTH}")
    ensure_column(conn, "survey_items", "page_break_before", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "survey_items", "buy_book_page_break_before", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "survey_items", "image_display_size", "TEXT NOT NULL DEFAULT 'medium'")
    ensure_column(conn, "survey_items", "image_fit", "TEXT NOT NULL DEFAULT 'contain'")
    ensure_column(conn, "buy_book_layouts", "group_by", "TEXT NOT NULL DEFAULT 'brand'")
    # No default: NULL means "saved before layers existed" and is backfilled
    # below, while a saved '[]' is a real choice (no grouping, no breaks).
    ensure_column(conn, "buy_book_layouts", "page_break_layers_json", "TEXT")
    _allow_null_page_break_layers(conn)
    ensure_column(conn, "survey_layouts", "questions_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "survey_periods", "questions_json", "TEXT NOT NULL DEFAULT '[]'")
    # The Buy Book cover, stored per period. NULL means "nothing overridden",
    # which is different from '{}' only in that the row was never saved.
    ensure_column(conn, "survey_periods", "cover_json", "TEXT")
    ensure_column(conn, "survey_responses", "answers_json", "TEXT NOT NULL DEFAULT '{}'")
    # A layout saved before layered page breaks keeps its single Group by as a
    # one-layer stack: same sorting, same break, so nothing regresses on upgrade.
    layouts = execute(
        conn,
        "SELECT id, group_by, page_break_layers_json FROM buy_book_layouts",
    ).fetchall()
    for layout in layouts:
        stored = layout["page_break_layers_json"]
        if stored is not None and str(stored).strip():
            continue
        group_by = str(layout["group_by"] or "").strip()
        if group_by == "none":
            # It was saved with no grouping; that is an empty stack, not Brand.
            migrated = "[]"
        else:
            migrated = json.dumps([{"field": group_by or "brand", "new_page": True}])
        execute(
            conn,
            "UPDATE buy_book_layouts SET page_break_layers_json = ? WHERE id = ?",
            (migrated, layout["id"]),
        )
    default_questions = survey_questions_json()
    execute(
        conn,
        """
        UPDATE survey_layouts
        SET questions_json = ?
        WHERE questions_json IS NULL OR questions_json = '' OR questions_json = '[]'
        """,
        (default_questions,),
    )
    execute(
        conn,
        """
        UPDATE survey_periods
        SET questions_json = ?
        WHERE questions_json IS NULL OR questions_json = '' OR questions_json = '[]'
        """,
        (default_questions,),
    )
    execute(
        conn,
        """
        UPDATE survey_items
        SET buy_book_sort_order = CASE
            WHEN buy_book_sort_order = 0 AND sort_order <> 0 THEN sort_order
            WHEN buy_book_sort_order = 0 THEN id
            ELSE buy_book_sort_order
        END
        """,
    )


def _migrate_survey_access(conn) -> None:
    """One token per period, one answer per shopper per item.

    Both halves are idempotent: a second run finds every period tokenised (so
    mints nothing) and no duplicate answer rows to drop. The unique indexes are
    what make rotation and last-write-wins holds at the database, not only in
    the route.
    """
    ensure_column(conn, "survey_periods", "survey_token", "TEXT")
    ensure_column(conn, "survey_responses", "updated_at", "TEXT")

    # Mint once per period. Existing rows keep the token they already have —
    # re-minting on boot would rotate every live link on every deploy.
    rows = execute(
        conn,
        "SELECT id FROM survey_periods WHERE survey_token IS NULL OR survey_token = ''",
    ).fetchall()
    for row in rows:
        execute(
            conn,
            "UPDATE survey_periods SET survey_token = ? WHERE id = ?",
            (mint_survey_token(), row["id"]),
        )
    # NULLs are distinct in both engines, so a period that predates the column
    # and has not been minted yet cannot block this index.
    execute(
        conn,
        "CREATE UNIQUE INDEX IF NOT EXISTS survey_periods_token_idx ON survey_periods (survey_token)",
    )

    # Last write wins: the highest id of a duplicate set is the answer the
    # shopper meant, so the older rows go before the index can exist.
    execute(
        conn,
        """
        DELETE FROM survey_responses
        WHERE id NOT IN (
            SELECT MAX(id) FROM survey_responses
            GROUP BY company_id, survey_period_id, survey_item_id, lower(email)
        )
        """,
    )
    execute(
        conn,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS survey_responses_unique_answer_idx
        ON survey_responses (company_id, survey_period_id, survey_item_id, lower(email))
        """,
    )


def _normalize_survey_period_times(conn) -> None:
    """Blanks in a period's window become NULL.

    An older admin form stored the cleared field as ``""``. SQLite accepts that;
    Postgres cannot hold it in a ``TIMESTAMPTZ``, so on Postgres there is
    nothing to clean. Idempotent: a second boot finds no blanks left.
    """
    if IS_POSTGRES:
        return
    execute(
        conn,
        "UPDATE survey_periods SET opens_at = NULL WHERE opens_at IS NOT NULL AND TRIM(opens_at) = ''",
    )
    execute(
        conn,
        "UPDATE survey_periods SET closes_at = NULL WHERE closes_at IS NOT NULL AND TRIM(closes_at) = ''",
    )


def init_db() -> None:
    with connect() as conn:
        _create_companies_table(conn)
        _create_survey_layouts_table(conn)
        _create_core_tables(conn)
        _create_survey_periods_tables(conn)
        _create_netsuite_item_images_table(conn)

        execute(conn, "DELETE FROM sessions WHERE expires_at <= ?", (utc_now().isoformat(),))
        ensure_column(conn, "survey_items", "product_type", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "survey_items", "key_program", "TEXT NOT NULL DEFAULT ''")
        # NetSuite internal id: the key an image record is looked up by. It is
        # never derived from an image URL, and never a SKU or an item name.
        ensure_column(conn, "tracker_items", "netsuite_item_id", "TEXT")
        ensure_column(conn, "survey_items", "netsuite_item_id", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "survey_responses", "survey_period_id", "INTEGER")
        _ensure_company_columns(conn)
        _ensure_survey_layout_columns(conn)

        company = ensure_default_company(conn)
        _backfill_company_ids(conn, company["id"])
        _migrate_survey_access(conn)
        _normalize_survey_period_times(conn)
        ensure_survey_layout(conn, company["id"])
        ensure_buy_book_layout(conn, company["id"])
        _scope_sheet_status_uniqueness(conn)
        _ensure_company_indexes(conn)
        execute(
            conn,
            "CREATE INDEX IF NOT EXISTS survey_periods_company_status_idx ON survey_periods (company_id, status)",
        )
        execute(
            conn,
            "CREATE INDEX IF NOT EXISTS survey_period_items_period_idx ON survey_period_items (survey_period_id, sort_order)",
        )
        execute(
            conn,
            "CREATE INDEX IF NOT EXISTS survey_responses_period_idx ON survey_responses (survey_period_id)",
        )

        existing_admin = execute(
            conn,
            "SELECT id FROM users WHERE lower(email) = lower(?)",
            (ADMIN_EMAIL,),
        ).fetchone()
        if existing_admin is None:
            execute(
                conn,
                """
                INSERT INTO users (email, name, password_hash, role, company_id)
                VALUES (?, ?, ?, 'admin', ?)
                """,
                (ADMIN_EMAIL, ADMIN_NAME, hash_password(ADMIN_PASSWORD), company["id"]),
            )
        else:
            execute(
                conn,
                "UPDATE users SET company_id = ? WHERE id = ? AND company_id IS NULL",
                (company["id"], existing_admin["id"]),
            )

        seed_tracker_items_if_empty(conn, company["id"])
        sync_seeded_tracker_item_catalog(conn, company["id"])
        seed_kanban_columns_if_empty(conn, company["id"])
        seed_sheet_statuses_if_empty(conn, company["id"])
        sync_default_survey_items(conn, company["id"])
        backfill_survey_sort_order(conn)
        ensure_legacy_survey_period(conn, company["id"])
        seed_demo_companies(conn)
