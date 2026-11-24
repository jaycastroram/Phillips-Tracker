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
                legacy_public_buy_book BOOLEAN NOT NULL DEFAULT TRUE,
                legacy_public_survey BOOLEAN NOT NULL DEFAULT TRUE,
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
                legacy_public_buy_book INTEGER NOT NULL DEFAULT 1,
                legacy_public_survey INTEGER NOT NULL DEFAULT 1,
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
                -- The survey owns its items. Catalog items copied in before the
                -- split keep their id here for reference; a mocked-up item has
                -- none, and nothing in the survey reads the catalog.
                catalog_item_id INTEGER,
                item_number TEXT NOT NULL DEFAULT '',
                cost TEXT NOT NULL DEFAULT '',
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
                catalog_item_id INTEGER,
                item_number TEXT NOT NULL DEFAULT '',
                cost TEXT NOT NULL DEFAULT '',
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
                FOREIGN KEY (survey_period_id) REFERENCES survey_periods(id) ON DELETE CASCADE
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


def _create_netsuite_facet_values_table(conn) -> None:
    """Cached facet labels: one list id -> its name, per company dataset.

    The four facet fields hold list ids. Reading them means one query per list
    table, so the answer is cached here and a second run only asks for ids it
    has not seen. Only found labels are stored; an id that resolves to nothing
    is reported every run instead of being cached as blank forever.
    """
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS netsuite_facet_values (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                value_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, field, value_id)
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS netsuite_facet_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                value_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, field, value_id)
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
    # The public Buy Book link: one opaque token per company, minted on demand.
    ensure_column(conn, "companies", "public_book_token", "TEXT")
    # The two pre-0013 open routes, each behind its own switch so one can be
    # retired without shutting the other. DEFAULT TRUE backfills every existing
    # company: the links were open before the change, so they stay open after it.
    flag_type = "INTEGER NOT NULL DEFAULT 1" if not IS_POSTGRES else "BOOLEAN NOT NULL DEFAULT TRUE"
    ensure_column(conn, "companies", "legacy_public_buy_book", flag_type)
    ensure_column(conn, "companies", "legacy_public_survey", flag_type)
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

    # One submission per respondent per survey. Only rows that are submissions
    # (no single item) and carry an email are in the index: an anonymous
    # shopper cannot be recognised twice, and a legacy per-item row keeps the
    # index above instead. Duplicates from a pre-index race go first, highest
    # id kept, so the index can be created on an upgraded database.
    execute(
        conn,
        """
        DELETE FROM survey_responses
        WHERE survey_item_id IS NULL AND email <> ''
          AND id NOT IN (
              SELECT MAX(id) FROM survey_responses
              WHERE survey_item_id IS NULL AND email <> ''
              GROUP BY company_id, survey_period_id, lower(email)
          )
        """,
    )
    execute(
        conn,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS survey_responses_unique_respondent_idx
        ON survey_responses (company_id, survey_period_id, lower(email))
        WHERE survey_item_id IS NULL AND email <> ''
        """,
    )


def _allow_null_catalog_item_id(conn) -> None:
    """A survey item no longer has to come from the catalog.

    Older databases were built with ``catalog_item_id INTEGER NOT NULL
    REFERENCES survey_items(id)``; a mocked-up item has no catalog row, so the
    column becomes nullable and the foreign key goes. Rows copied in before the
    split keep their catalog id, which is what lets an upgrade keep today's
    surveys exactly as they look.
    """
    if IS_POSTGRES:
        column = execute(
            conn,
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'survey_period_items' AND column_name = 'catalog_item_id'
            """,
        ).fetchone()
        if column is not None and str(column["is_nullable"]).upper() == "NO":
            execute(conn,
                    "ALTER TABLE survey_period_items ALTER COLUMN catalog_item_id DROP NOT NULL")
        execute(conn,
                "ALTER TABLE survey_period_items DROP CONSTRAINT IF EXISTS"
                " survey_period_items_catalog_item_id_fkey")
        return

    columns = execute(conn, "PRAGMA table_info(survey_period_items)").fetchall()
    column = next((row for row in columns if row["name"] == "catalog_item_id"), None)
    if column is None or not int(column["notnull"]):
        return
    execute(conn, "PRAGMA foreign_keys = OFF")
    execute(conn, "DROP TABLE IF EXISTS survey_period_items_owned")
    execute(conn, """
        CREATE TABLE survey_period_items_owned (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_period_id INTEGER NOT NULL,
            catalog_item_id INTEGER,
            item_number TEXT NOT NULL DEFAULT '',
            cost TEXT NOT NULL DEFAULT '',
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
            FOREIGN KEY (survey_period_id) REFERENCES survey_periods(id) ON DELETE CASCADE
        )
    """)
    execute(conn, """
        INSERT INTO survey_period_items_owned (
            id, survey_period_id, catalog_item_id, item_number, cost, item_name, brand, channel,
            product_type, key_program, item_description, uom, price, image_url, image_display_size,
            image_fit, sort_order, response_width, page_break_before, created_at, updated_at
        )
        SELECT id, survey_period_id, catalog_item_id, item_number, cost, item_name, brand, channel,
            product_type, key_program, item_description, uom, price, image_url, image_display_size,
            image_fit, sort_order, response_width, page_break_before, created_at, updated_at
        FROM survey_period_items
    """)
    execute(conn, "DROP TABLE survey_period_items")
    execute(conn, "ALTER TABLE survey_period_items_owned RENAME TO survey_period_items")
    execute(conn, "PRAGMA foreign_keys = ON")


def _allow_null_response_item_id(conn) -> None:
    """A submission covers every item, so the response row names no single one.

    ``survey_item_id`` was ``NOT NULL`` because each row was one shopper's
    answer for one catalog item. Rows written before the split keep their item
    (and are still exported/ignored as before); a submission now leaves it NULL
    and carries its per-item answers in survey_answers.
    """
    if IS_POSTGRES:
        column = execute(
            conn,
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'survey_responses' AND column_name = 'survey_item_id'
            """,
        ).fetchone()
        if column is not None and str(column["is_nullable"]).upper() == "NO":
            execute(conn,
                    "ALTER TABLE survey_responses ALTER COLUMN survey_item_id DROP NOT NULL")
        execute(conn,
                "ALTER TABLE survey_responses DROP CONSTRAINT IF EXISTS survey_responses_survey_item_id_fkey")
        return

    columns = execute(conn, "PRAGMA table_info(survey_responses)").fetchall()
    column = next((row for row in columns if row["name"] == "survey_item_id"), None)
    if column is None or not int(column["notnull"]):
        return
    execute(conn, "PRAGMA foreign_keys = OFF")
    execute(conn, "DROP TABLE IF EXISTS survey_responses_submissions")
    execute(conn, """
        CREATE TABLE survey_responses_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_item_id INTEGER,
            email TEXT NOT NULL,
            attention_effectiveness INTEGER NOT NULL,
            recommend_rollout TEXT NOT NULL,
            retail_engagement INTEGER NOT NULL,
            stands_out TEXT NOT NULL,
            price_reasonable TEXT NOT NULL,
            feedback TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            company_id INTEGER,
            survey_period_id INTEGER,
            answers_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT
        )
    """)
    execute(conn, """
        INSERT INTO survey_responses_submissions (
            id, survey_item_id, email, attention_effectiveness, recommend_rollout,
            retail_engagement, stands_out, price_reasonable, feedback, created_at,
            company_id, survey_period_id, answers_json, updated_at
        )
        SELECT id, survey_item_id, email, attention_effectiveness, recommend_rollout,
            retail_engagement, stands_out, price_reasonable, feedback, created_at,
            company_id, survey_period_id, answers_json, updated_at
        FROM survey_responses
    """)
    execute(conn, "DROP TABLE survey_responses")
    execute(conn, "ALTER TABLE survey_responses_submissions RENAME TO survey_responses")
    execute(conn, "PRAGMA foreign_keys = ON")


def _create_survey_answer_tables(conn) -> None:
    """The ticks, and the answers, that make a survey item-based.

    ``survey_period_item_questions`` is which of the survey's pool questions an
    item asks. ``survey_answers`` is one row per submission x item x question,
    keyed by the pool's own question id, so editing a question's wording leaves
    every stored answer attached to it.
    """
    if IS_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_period_item_questions (
                id SERIAL PRIMARY KEY,
                survey_period_item_id INTEGER NOT NULL
                    REFERENCES survey_period_items(id) ON DELETE CASCADE,
                question_id TEXT NOT NULL,
                UNIQUE (survey_period_item_id, question_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_answers (
                id SERIAL PRIMARY KEY,
                response_id INTEGER NOT NULL REFERENCES survey_responses(id) ON DELETE CASCADE,
                survey_period_item_id INTEGER NOT NULL
                    REFERENCES survey_period_items(id) ON DELETE CASCADE,
                question_id TEXT NOT NULL,
                answer_text TEXT NOT NULL DEFAULT '',
                answer_options_json TEXT NOT NULL DEFAULT '[]',
                answer_number DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (response_id, survey_period_item_id, question_id)
            )
        """)
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_period_item_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_period_item_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                UNIQUE (survey_period_item_id, question_id),
                FOREIGN KEY (survey_period_item_id) REFERENCES survey_period_items(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                response_id INTEGER NOT NULL,
                survey_period_item_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                answer_text TEXT NOT NULL DEFAULT '',
                answer_options_json TEXT NOT NULL DEFAULT '[]',
                answer_number REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (response_id, survey_period_item_id, question_id),
                FOREIGN KEY (response_id) REFERENCES survey_responses(id) ON DELETE CASCADE,
                FOREIGN KEY (survey_period_item_id) REFERENCES survey_period_items(id) ON DELETE CASCADE
            )
        """)
    execute(conn,
            "CREATE INDEX IF NOT EXISTS survey_answers_response_idx"
            " ON survey_answers (response_id)")
    execute(conn,
            "CREATE INDEX IF NOT EXISTS survey_answers_item_idx"
            " ON survey_answers (survey_period_item_id, question_id)")


def _migrate_survey_owned_items(conn) -> None:
    """Every survey owns its items, so the item row and its answers move in.

    Idempotent on both engines: the columns are added once, the nullable
    rebuild only runs on a table that still has the NOT NULL, and the backfills
    below write the same values a second time. Existing surveys keep their
    items (and their images), cost falls back to the old price field, and every
    item ticks the survey's whole pool, which is what it asked before the pool
    became per item.
    """
    ensure_column(conn, "survey_period_items", "cost", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "survey_period_items", "item_number", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "survey_responses", "updated_at", "TEXT")
    ensure_column(conn, "survey_responses", "answers_json", "TEXT NOT NULL DEFAULT '{}'")
    _allow_null_catalog_item_id(conn)
    _allow_null_response_item_id(conn)
    _create_survey_answer_tables(conn)

    execute(conn, "UPDATE survey_period_items SET cost = price WHERE cost = '' AND price <> ''")

    # A survey that existed before items carried their own ticks asked every
    # question for every item, so that is what those items' ticks say after the
    # upgrade. Only items with no ticks at all are filled: re-ticking an item
    # that already has its set would quietly undo "this item asks only these".
    from database import parse_survey_questions

    periods = execute(conn, "SELECT id, questions_json FROM survey_periods").fetchall()
    for period in periods:
        question_ids = [q["id"] for q in parse_survey_questions(period["questions_json"])]
        if not question_ids:
            continue
        unticked = execute(
            conn,
            """
            SELECT i.id FROM survey_period_items i
            WHERE i.survey_period_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM survey_period_item_questions q
                  WHERE q.survey_period_item_id = i.id
              )
            """,
            (period["id"],),
        ).fetchall()
        for item in unticked:
            for question_id in question_ids:
                execute(
                    conn,
                    "INSERT INTO survey_period_item_questions (survey_period_item_id, question_id)"
                    " VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (item["id"], question_id),
                )


def _backfill_netsuite_item_numbers(conn) -> None:
    """Give existing rows their item number from the last-sync snapshot.

    The number lives in the snapshot the sync already wrote (``itemid``), so a
    table that has been synced before this column existed gets its numbers
    without a NetSuite re-sync. Only blank numbers are filled: a row that
    already carries one keeps it, so this is idempotent and cannot overwrite a
    later sync's value. A row with no snapshot, or a snapshot without the
    field, is left alone.
    """
    rows = execute(
        conn,
        "SELECT id, netsuite_synced_json FROM survey_items"
        " WHERE netsuite_item_number = '' AND netsuite_synced_json NOT IN ('', '{}')",
    ).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["netsuite_synced_json"] or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(snapshot, dict):
            continue
        number = str(snapshot.get("netsuite_item_number") or snapshot.get("itemid") or "").strip()
        if not number:
            continue
        execute(
            conn,
            "UPDATE survey_items SET netsuite_item_number = ?"
            " WHERE id = ? AND netsuite_item_number = ''",
            (number, row["id"]),
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
        _create_netsuite_facet_values_table(conn)

        execute(conn, "DELETE FROM sessions WHERE expires_at <= ?", (utc_now().isoformat(),))
        ensure_column(conn, "survey_items", "product_type", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "survey_items", "key_program", "TEXT NOT NULL DEFAULT ''")
        # NetSuite internal id: the key an image record is looked up by. It is
        # never derived from an image URL, and never a SKU or an item name.
        ensure_column(conn, "tracker_items", "netsuite_item_id", "TEXT")
        ensure_column(conn, "survey_items", "netsuite_item_id", "TEXT NOT NULL DEFAULT ''")
        # The item number — the SKU a saved search calls "Item ID", e.g.
        # ``3184_58212`` — as distinct from the internal id above. A buy-in
        # export names its items by number, so period membership looks a row up
        # by either; keeping both means it works whichever one an export carries.
        ensure_column(conn, "survey_items", "netsuite_item_number", "TEXT NOT NULL DEFAULT ''")
        # What NetSuite said last run. The per-field override test compares the
        # live value against this; a person's edit differs from it and survives.
        ensure_column(conn, "survey_items", "netsuite_synced_json", "TEXT NOT NULL DEFAULT ''")
        # A row created without a usable NetSuite name: named ``Item <id>`` and
        # kept out of every book until someone names it.
        ensure_column(conn, "survey_items", "needs_name", "INTEGER NOT NULL DEFAULT 0")
        # When a run retired the row. One-way: nothing ever clears it back to an
        # active row except a person, and the next run retires it again.
        ensure_column(conn, "survey_items", "archived_at", "TEXT")
        ensure_column(conn, "survey_responses", "survey_period_id", "INTEGER")
        _ensure_company_columns(conn)
        _ensure_survey_layout_columns(conn)

        company = ensure_default_company(conn)
        _backfill_company_ids(conn, company["id"])
        _migrate_survey_owned_items(conn)
        _migrate_survey_access(conn)
        _normalize_survey_period_times(conn)
        _backfill_netsuite_item_numbers(conn)
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

        execute(
            conn,
            "CREATE INDEX IF NOT EXISTS survey_items_netsuite_id_idx"
            " ON survey_items (company_id, netsuite_item_id)",
        )
        # The same lookup, per company, for the number a buy-in export names.
        execute(
            conn,
            "CREATE INDEX IF NOT EXISTS survey_items_netsuite_number_idx"
            " ON survey_items (company_id, netsuite_item_number)",
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
