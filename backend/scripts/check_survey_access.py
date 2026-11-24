"""Acceptance check for survey access control (token, buy book, duplicates).

Runs against the app's own TestClient and the database it is configured with
(SQLite locally, Postgres 16 with DATABASE_URL set). It creates its own survey
periods and responses and removes them afterwards.

    python backend/scripts/check_survey_access.py
    python backend/scripts/check_survey_access.py --keep

What it proves:

1.  the bare catalog form (no surface, no token) -> 401, and a stranger's page
    view still creates no period row
2.  the restored legacy survey link: no token + nothing open -> 200 with the
    pre-change closed shape, and an anonymous POST -> 403, writing nothing
3.  the same link follows an open period (200, survey_open true) and follows
    its close time: past-close -> the closed shape again on the anonymous path
    while a token on that period still says "This survey is closed."
4.  a supplied token always wins: a bogus token 404s even while a period is
    open, and a valid token returns the period's items and questions
5.  another company's token under this company's slug -> 404
6.  a closed period with a valid token -> GET 200 survey_open false and no
    items, POST 410
7.  closes_at in the past while the status still says open -> same as 6
8.  rotation kills the old token at once and the new one works
9.  init_db() twice mints no second token and leaves the unique indexes in place
10. two answers for one shopper and item -> one row, the second answer stored,
    results counting one respondent with that answer
11. duplicate rows seeded before the index -> migration keeps the highest id
12. the public buy book is back at 128aa1e's shape behind its flag: active,
    in-buy-book items, layer payload, ?k= accepted and ignored, and no key
    column read anywhere
13. admin buy-book layout: anonymous 401 (another company's editor is #2's route)
14. the token is on the admin period detail and never in the public payload
15. a public read against a company with no period creates no period row
16. a cleared date field is stored as NULL, never as an empty string
17. a legacy blank row is cleaned on boot
18. the two legacy flags switch one link each, from one authenticated PUT,
    and a non-admin PUT still reads 403
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_COMPANY_SLUG, IS_POSTGRES  # noqa: E402
from database import connect, execute, fetch_company_by_slug, get_open_survey_period  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []
PENDING: list[str] = []

INTEREST = [{
    "id": "interest",
    "type": "yes_no",
    "label": "Are you interested in this item?",
    "required": True,
    "options": ["Yes", "No"],
}]


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


def pending(label: str, note: str) -> None:
    print(f"  PENDING  {label}  [{note}]")
    PENDING.append(label)


def login(client: TestClient, email: str, password: str) -> dict[str, str] | None:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    if response.status_code != 200:
        return None
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_period(client: TestClient, headers: dict[str, str], name: str, company_id: int | None = None) -> int:
    payload: dict[str, Any] = {"name": name, "open_immediately": True, "questions": INTEREST}
    params = {"company_id": company_id} if company_id is not None else None
    response = client.post("/api/admin/survey-periods", headers=headers, json=payload, params=params)
    if response.status_code != 201:
        raise SystemExit(f"Could not create period {name}: {response.status_code} {response.text[:300]}")
    return int(response.json()["period"]["id"])


def period_detail(
    client: TestClient,
    headers: dict[str, str],
    period_id: int,
    company_id: int | None = None,
) -> dict[str, Any]:
    params = {"company_id": company_id} if company_id is not None else None
    response = client.get(f"/api/admin/survey-periods/{period_id}", headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def token_of(
    client: TestClient,
    headers: dict[str, str],
    period_id: int,
    company_id: int | None = None,
) -> str:
    return str(period_detail(client, headers, period_id, company_id)["period"].get("survey_token") or "")


def survey_get(client: TestClient, slug: str, token: str | None, *, surface: str = "survey") -> Any:
    params: dict[str, Any] = {"surface": surface}
    if token:
        params["t"] = token
    return client.get(f"/api/public/{slug}/survey-items", params=params)


def submit(client: TestClient, slug: str, token: str | None, item_id: int, email: str, answer: str) -> Any:
    params = {"t": token} if token else None
    return client.post(
        f"/api/public/{slug}/survey-responses",
        params=params,
        json={"survey_item_id": item_id, "email": email, "answers": {"interest": answer}},
    )


def submit_submission(client: TestClient, slug: str, answer: str, email: str = "upsert@example.com") -> Any:
    """One filled-in survey through the static link — no token, no item id."""
    return client.post(
        f"/api/public/{slug}/survey-submissions",
        json={"email": email, "answers": {"interest": answer}},
    )


def submission_rows(period_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT id, email, answers_json FROM survey_submissions WHERE survey_period_id = ? ORDER BY id ASC",
            (period_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def answer_rows(period_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = execute(
            conn,
            """
            SELECT id, email, answers_json FROM survey_responses
            WHERE survey_period_id = ? ORDER BY id ASC
            """,
            (period_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def response_count() -> int:
    with connect() as conn:
        row = execute(conn, "SELECT COUNT(*) AS n FROM survey_responses").fetchone()
    return int(row["n"])


def index_present(name: str) -> bool:
    with connect() as conn:
        if IS_POSTGRES:
            row = execute(
                conn,
                "SELECT 1 AS ok FROM pg_indexes WHERE indexname = ?",
                (name,),
            ).fetchone()
        else:
            row = execute(
                conn,
                "SELECT 1 AS ok FROM sqlite_master WHERE type = 'index' AND name = ?",
                (name,),
            ).fetchone()
    return row is not None


def table_columns(table: str) -> list[str]:
    with connect() as conn:
        if IS_POSTGRES:
            rows = execute(
                conn,
                "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
                (table,),
            ).fetchall()
        else:
            rows = execute(conn, f"PRAGMA table_info({table})").fetchall()
    return [row["name"] for row in rows]


def period_count(company_id: int) -> int:
    with connect() as conn:
        row = execute(
            conn,
            "SELECT COUNT(*) AS n FROM survey_periods WHERE company_id = ?",
            (company_id,),
        ).fetchone()
    return int(row["n"])


def all_period_tokens() -> dict[int, str]:
    with connect() as conn:
        rows = execute(conn, "SELECT id, survey_token FROM survey_periods").fetchall()
    return {int(row["id"]): str(row["survey_token"] or "") for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the periods and responses this script creates")
    args = parser.parse_args()

    init_db()
    client = TestClient(app)

    headers = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    if headers is None:
        raise SystemExit("Admin login failed. Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    slug = DEFAULT_COMPANY_SLUG

    with connect() as conn:
        company = fetch_company_by_slug(conn, slug)
    if company is None:
        raise SystemExit(f"Company {slug} is missing. Boot the app once so init_db seeds it.")
    company_id = int(company["id"])

    with connect() as conn:
        other = fetch_company_by_slug(conn, "demo-spirits")
        if other is None:
            raise SystemExit("demo-spirits is missing, so the cross-company token check cannot run.")
        other_company_id = int(other["id"])
        item = execute(
            conn,
            "SELECT id FROM survey_items WHERE company_id = ? AND is_active = ? ORDER BY id ASC LIMIT 1",
            (company_id, True),
        ).fetchone()
    if item is None:
        raise SystemExit("No catalog item in the default company — seed the database first.")
    item_id = int(item["id"])

    # A company with no survey period at all, for check 15.
    with connect() as conn:
        empty_slug = "access-check-empty"
        execute(conn, "DELETE FROM companies WHERE slug = ?", (empty_slug,))
        if IS_POSTGRES:
            row = execute(
                conn,
                "INSERT INTO companies (slug, name, is_active) VALUES (?, ?, ?) RETURNING id",
                (empty_slug, "Access Check Empty", True),
            ).fetchone()
        else:
            cursor = execute(
                conn,
                "INSERT INTO companies (slug, name, is_active) VALUES (?, ?, ?)",
                (empty_slug, "Access Check Empty", 1),
            )
            row = execute(conn, "SELECT id FROM companies WHERE id = ?", (cursor.lastrowid,)).fetchone()
        empty_company_id = int(row["id"])

    created_periods: list[int] = []
    test_email = "access-check@example.com"

    try:
        # --------------------------------------------------------------
        print("\n1. The bare catalog form stays token-gated")
        response = survey_get(client, slug, None)
        check("survey surface without t -> 200 (the legacy link)", response.status_code == 200,
              str(response.status_code))
        bare = survey_get(client, slug, None, surface="")
        check("bare catalog form without t -> 401", bare.status_code == 401, str(bare.status_code))
        check("the 401 names the missing credential",
              "survey_token" in str(bare.json().get("detail", "")), str(bare.json())[:120])
        no_params = client.get(f"/api/public/{slug}/survey-items")
        check("no surface and no token -> 401", no_params.status_code == 401, str(no_params.status_code))

        # --------------------------------------------------------------
        print("\n2. The legacy anonymous link, with nothing open")
        before_periods = period_count(empty_company_id)
        no_open = survey_get(client, empty_slug, None)
        closed_body = no_open.json()
        check("no token + no open period -> 200", no_open.status_code == 200, str(no_open.status_code))
        check("the pre-change closed shape comes back",
              closed_body.get("items") == [] and closed_body.get("survey_open") is False
              and closed_body.get("period") is None
              and closed_body.get("message") == "No survey is open right now.",
              str(closed_body)[:180])
        before_rows = response_count()
        no_period_post = submit(client, empty_slug, None, item_id, test_email, "Yes")
        check("anonymous POST with nothing open -> 403", no_period_post.status_code == 403,
              str(no_period_post.status_code))
        check("the 403 is the pre-change message",
              no_period_post.json().get("detail") == "No survey is open right now",
              str(no_period_post.json())[:120])
        check("neither the read nor the POST created a period",
              period_count(empty_company_id) == before_periods,
              f"{before_periods} -> {period_count(empty_company_id)}")
        check("the refused POST wrote nothing", response_count() == before_rows)

        # --------------------------------------------------------------
        print("\n3. The legacy link follows an open period, and its close time")
        anon_period = create_period(client, headers, "access-check-anon", empty_company_id)
        created_periods.append(anon_period)
        anon_read = survey_get(client, empty_slug, None)
        anon_body = anon_read.json()
        check("no token + an open period -> 200 survey_open true",
              anon_read.status_code == 200 and anon_body.get("survey_open") is True,
              str(anon_body)[:180])
        check("the open period is the period block", isinstance(anon_body.get("period"), dict),
              str(anon_body.get("period"))[:120])
        with connect() as conn:
            execute(
                conn,
                "UPDATE survey_periods SET closes_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", anon_period),
            )
        past_read = survey_get(client, empty_slug, None)
        past_body = past_read.json()
        check("past close on the anonymous path -> the closed shape, not items",
              past_read.status_code == 200 and past_body.get("survey_open") is False
              and past_body.get("message") == "No survey is open right now."
              and past_body.get("period") is None, str(past_body)[:180])
        past_post = submit(client, empty_slug, None, item_id, test_email, "Yes")
        check("past close + anonymous POST -> 403", past_post.status_code == 403,
              str(past_post.status_code))
        anon_token = token_of(client, headers, anon_period, empty_company_id)
        closed_token_read = survey_get(client, empty_slug, anon_token)
        check("the token path still says this survey is closed",
              closed_token_read.status_code == 200
              and closed_token_read.json().get("message") == "This survey is closed.",
              str(closed_token_read.json())[:160])

        # --------------------------------------------------------------
        print("\n4. A supplied token always wins: valid reads, bogus never falls back")
        period_id = create_period(client, headers, "access-check-open")
        created_periods.append(period_id)
        token = token_of(client, headers, period_id)
        check("admin detail carries a 32-char url-safe token", len(token) >= 24 and " " not in token, token[:8])
        public = survey_get(client, slug, token)
        check("valid token -> 200", public.status_code == 200, str(public.status_code))
        payload = public.json()
        check("payload is open and carries the question list",
              payload.get("survey_open") is True and bool(payload.get("questions")),
              str(payload)[:160])
        check("a survey carries no item list", payload.get("items") == [], str(payload.get("items"))[:80])
        check("payload carries the period's questions",
              [q["id"] for q in payload.get("questions", [])] == ["interest"],
              str(payload.get("questions"))[:120])
        with connect() as conn:
            catalog_count = int(execute(
                conn,
                "SELECT COUNT(*) AS n FROM survey_items WHERE company_id = ? AND is_active = ?",
                (company_id, True),
            ).fetchone()["n"])
        bare_token = survey_get(client, slug, token, surface="")
        check("the bare form behind the same token still reads the catalog",
              bare_token.status_code == 200
              and len(bare_token.json().get("items") or []) == catalog_count,
              f"{bare_token.status_code}: {len(bare_token.json().get('items') or []) if bare_token.status_code == 200 else ''} of {catalog_count}")

        unknown = survey_get(client, slug, "not-a-real-token")
        check("bogus token -> 404 while a period is open (no fallback)",
              unknown.status_code == 404, str(unknown.status_code))
        check("the 404 is the same answer an unknown survey gets",
              "not found" in str(unknown.json().get("detail", "")).lower(), str(unknown.json())[:120])
        anon_default = survey_get(client, slug, None).json()
        check("the anonymous link reads the same open period on the real company",
              anon_default.get("survey_open") is True
              and (anon_default.get("period") or {}).get("id") == period_id,
              str(anon_default)[:160])

        # --------------------------------------------------------------
        print("\n5. Another company's token under this slug -> 404")
        other_period = create_period(client, headers, "access-check-other", other_company_id)
        created_periods.append(other_period)
        other_token = token_of(client, headers, other_period, other_company_id)
        check("the other company's period carries a token", bool(other_token))
        cross = survey_get(client, slug, other_token)
        check("cross-company token -> 404", cross.status_code == 404, str(cross.status_code))

        # --------------------------------------------------------------
        print("\n6. Closed period with a valid token: read-only, no submissions")
        close = client.patch(f"/api/admin/survey-periods/{period_id}", headers=headers, json={"status": "closed"})
        check("period closed by the admin", close.status_code == 200, str(close.status_code))
        closed_read = survey_get(client, slug, token)
        closed_payload = closed_read.json()
        check("closed read -> 200 with survey_open false", closed_read.status_code == 200
              and closed_payload.get("survey_open") is False, str(closed_read.status_code))
        check("closed read returns no items", closed_payload.get("items") == [], str(closed_payload.get("items"))[:80])
        closed_post = submit(client, slug, token, item_id, test_email, "Yes")
        check("closed POST -> 410", closed_post.status_code == 410, str(closed_post.status_code))

        # --------------------------------------------------------------
        print("\n7. closes_at in the past, status still open -> closed behaviour")
        timed_id = create_period(client, headers, "access-check-timed")
        created_periods.append(timed_id)
        timed_token = token_of(client, headers, timed_id)
        with connect() as conn:
            execute(
                conn,
                "UPDATE survey_periods SET closes_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", timed_id),
            )
        timed_read = survey_get(client, slug, timed_token).json()
        check("past closes_at read -> survey_open false", timed_read.get("survey_open") is False, str(timed_read)[:120])
        timed_post = submit(client, slug, timed_token, item_id, test_email, "Yes")
        check("past closes_at POST -> 410", timed_post.status_code == 410, str(timed_post.status_code))

        # --------------------------------------------------------------
        print("\n8. Rotation")
        rotate_id = create_period(client, headers, "access-check-rotate")
        created_periods.append(rotate_id)
        old_token = token_of(client, headers, rotate_id)
        rotated = client.post(f"/api/admin/survey-periods/{rotate_id}/rotate-token", headers=headers)
        check("rotate -> 200", rotated.status_code == 200, str(rotated.status_code))
        new_token = str(rotated.json()["period"].get("survey_token") or "")
        check("rotate minted a different token", bool(new_token) and new_token != old_token)
        check("old token -> 404 at once", survey_get(client, slug, old_token).status_code == 404)
        check("new token -> 200", survey_get(client, slug, new_token).status_code == 200)

        # --------------------------------------------------------------
        print("\n9. init_db() twice: no re-mint, indexes hold")
        before_tokens = all_period_tokens()
        init_db()
        init_db()
        after_tokens = all_period_tokens()
        check("every period has a token", all(after_tokens.values()), str(after_tokens)[:120])
        check("no token changed on a repeat run", before_tokens == after_tokens, "tokens differ")
        check("every period has its own token", len(set(after_tokens.values())) == len(after_tokens))
        check("unique token index present", index_present("survey_periods_token_idx"))
        check("unique answer index present", index_present("survey_responses_unique_answer_idx"))

        # --------------------------------------------------------------
        print("\n10. Two submissions from one shopper: two rows, both counted")
        upsert_id = create_period(client, headers, "access-check-upsert")
        created_periods.append(upsert_id)
        first = submit_submission(client, slug, "Yes")
        check("first submission -> 201", first.status_code == 201, str(first.status_code))
        second = submit_submission(client, slug, "No")
        check("second submission -> 201", second.status_code == 201, str(second.status_code))
        rows = submission_rows(upsert_id)
        check("one row per submission", len(rows) == 2, f"{len(rows)} rows")
        results = client.get("/api/admin/survey-results", headers=headers, params={"period_id": upsert_id}).json()
        summary = results.get("summary", {})
        check("results count both submissions", summary.get("total_responses") == 2, str(summary)[:120])
        check("both share one respondent", summary.get("unique_respondents") == 1, str(summary)[:120])
        check("the tallies read the stored answers", summary.get("total_not_interested") == 1
              and summary.get("total_interested") == 1, str(summary)[:160])

        # --------------------------------------------------------------
        print("\n11. Duplicate rows seeded before the index -> highest id survives")
        dup_id = create_period(client, headers, "access-check-dupes")
        created_periods.append(dup_id)
        with connect() as conn:
            execute(conn, "DROP INDEX IF EXISTS survey_responses_unique_answer_idx")
            for answer in ("Yes", "No"):
                execute(
                    conn,
                    """
                    INSERT INTO survey_responses (
                        company_id, survey_period_id, survey_item_id, email, attention_effectiveness,
                        recommend_rollout, retail_engagement, stands_out, price_reasonable, feedback, answers_json
                    ) VALUES (?, ?, ?, ?, 0, ?, 0, '', '', '', ?)
                    """,
                    (company_id, dup_id, item_id, test_email, answer, f'{{"interest": "{answer}"}}'),
                )
            seeded = execute(
                conn,
                "SELECT id FROM survey_responses WHERE survey_period_id = ? ORDER BY id ASC",
                (dup_id,),
            ).fetchall()
        highest = int(seeded[-1]["id"])
        init_db()
        kept = answer_rows(dup_id)
        check("one duplicate row left", len(kept) == 1, f"{len(kept)} rows")
        check("the highest id is the one kept", bool(kept) and int(kept[0]["id"]) == highest,
              f"kept {kept[0]['id'] if kept else '-'} of {highest}")
        check("the unique answer index came back", index_present("survey_responses_unique_answer_idx"))

        # --------------------------------------------------------------
        print("\n12. The public buy book is back at 128aa1e's shape, behind its flag")
        columns = table_columns("companies")
        public_config = client.get(f"/api/public/{slug}/config").json()
        companies = client.get("/api/admin/companies", headers=headers).json().get("companies", [])
        exposed = [key for key in ("buy_book_key", "public_key", "buy_book_slug") if key in public_config]
        exposed += [key for row in companies for key in ("buy_book_key", "public_key", "buy_book_slug") if key in row]
        check("no key field in the public config or company list", exposed == [], str(exposed))
        check("no buy_book_key column was added", "buy_book_key" not in columns, str(columns)[:160])
        with connect() as conn:
            buy_book_rows = int(execute(
                conn,
                "SELECT COUNT(*) AS n FROM survey_items WHERE company_id = ? AND is_active = ? AND in_buy_book = ?",
                (company_id, True, 1),
            ).fetchone()["n"])
        book = client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"})
        check("surface=buy-book -> 200", book.status_code == 200, str(book.status_code))
        book_body = book.json()
        # The book is now one buy-in period, not the catalog. With no export in
        # place it is empty and says so — never all 3,700 rows.
        check("no buy-in period data -> an empty book, not the catalog",
              (book_body.get("items") or []) == [] and book_body.get("no_period_data") is True,
              f"{len(book_body.get('items') or [])} of {buy_book_rows} catalog rows")
        check("the pre-0013 payload keys are back",
              {"items", "filter_fields", "group_by", "page_break_layers"} <= set(book_body),
              sorted(book_body.keys()))
        check("group_by names a layer, defaulting to brand",
              bool(book_body.get("group_by")), str(book_body.get("group_by")))
        book_key = client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book", "k": "abc"})
        check("?k= is accepted and ignored", book_key.status_code == 200
              and (book_key.json().get("items") or []) == (book_body.get("items") or []),
              str(book_key.status_code))
        unprefixed = client.get("/api/public/survey-items", params={"surface": "buy-book", "k": "abc"})
        check("the unprefixed API form serves the default company's book",
              unprefixed.status_code == 200
              and "items" in unprefixed.json(),
              str(unprefixed.status_code))

        # --------------------------------------------------------------
        print("\n13. Admin buy-book endpoints require a session")
        anonymous = client.get("/api/admin/buy-book-layout")
        check("buy-book layout without a session -> 401", anonymous.status_code == 401, str(anonymous.status_code))
        other_editor = login(client, "demo@example.com", "Demo1234")
        if editor_headers := other_editor:
            scoped = client.get(
                "/api/admin/buy-book-layout",
                headers=editor_headers,
                params={"company_id": company_id},
            )
            if scoped.status_code == 404:
                check("another company's editor -> 404", True)
            else:
                pending(
                    "another company's editor gets 404 on the admin buy-book route",
                    f"currently {scoped.status_code}: #2 owns the admin buy-book/PDF routes",
                )
        else:
            pending(
                "another company's editor gets 404 on the admin buy-book route",
                "no demo editor login in this database; #2 owns the admin buy-book/PDF routes",
            )

        # --------------------------------------------------------------
        print("\n14. The token: on the admin detail, never in the public payload")
        detail = period_detail(client, headers, period_id)["period"]
        check("admin period detail carries survey_token", bool(detail.get("survey_token")))
        fresh = survey_get(client, slug, token_of(client, headers, rotate_id)).json()
        check("public payload has no survey_token key", "survey_token" not in fresh,
              str(list(fresh.keys()))[:140])
        check("public period block has no survey_token", "survey_token" not in fresh.get("period", {}),
              str(fresh.get("period"))[:140])
        check("public items carry no survey_token",
              all("survey_token" not in item for item in fresh.get("items", [])))

        # --------------------------------------------------------------
        print("\n15. A public read against a company with no open period creates no period")
        fresh_slug = "access-check-fresh"
        with connect() as conn:
            execute(conn, "DELETE FROM companies WHERE slug = ?", (fresh_slug,))
            if IS_POSTGRES:
                row = execute(
                    conn,
                    "INSERT INTO companies (slug, name, is_active) VALUES (?, ?, ?) RETURNING id",
                    (fresh_slug, "Access Check Fresh", True),
                ).fetchone()
            else:
                cursor = execute(
                    conn,
                    "INSERT INTO companies (slug, name, is_active) VALUES (?, ?, ?)",
                    (fresh_slug, "Access Check Fresh", 1),
                )
                row = execute(conn, "SELECT id FROM companies WHERE id = ?", (cursor.lastrowid,)).fetchone()
            fresh_id = int(row["id"])
        try:
            before_periods = period_count(fresh_id)
            # The legacy link controls these companies at their DEFAULT: a row
            # created after the migration carries TRUE without an UPDATE.
            with connect() as conn:
                created_flags = execute(
                    conn,
                    "SELECT legacy_public_buy_book, legacy_public_survey FROM companies WHERE id = ?",
                    (fresh_id,),
                ).fetchone()
            check("a new company's flags default to on",
                  bool(created_flags["legacy_public_buy_book"])
                  and bool(created_flags["legacy_public_survey"]),
                  str(dict(created_flags)))
            anon = survey_get(client, fresh_slug, None)
            check("the anonymous read answers the closed shape", anon.status_code == 200
                  and anon.json().get("survey_open") is False, str(anon.status_code))
            empty_read = survey_get(client, fresh_slug, "irrelevant-token")
            check("a token for a company with no periods 404s", empty_read.status_code == 404,
                  str(empty_read.status_code))
            client.get(f"/api/public/{fresh_slug}/survey-items", params={"surface": "buy-book"})
            check("no period row was created",
                  period_count(fresh_id) == before_periods,
                  f"{before_periods} -> {period_count(fresh_id)}")
        finally:
            with connect() as conn:
                execute(conn, "DELETE FROM companies WHERE id = ?", (fresh_id,))

        # --------------------------------------------------------------
        print("\n16. A cleared date field is stored as NULL, never as an empty string")
        blank_id = create_period(client, headers, "access-check-blank")
        created_periods.append(blank_id)
        blank_token = token_of(client, headers, blank_id)
        cleared = client.patch(
            f"/api/admin/survey-periods/{blank_id}",
            headers=headers,
            json={"closes_at": "", "opens_at": "   "},
        )
        check(
            "clearing both date fields is accepted",
            cleared.status_code == 200,
            f"{cleared.status_code} {cleared.text[:120]}",
        )
        with connect() as conn:
            stored = execute(
                conn,
                "SELECT opens_at, closes_at FROM survey_periods WHERE id = ?",
                (blank_id,),
            ).fetchone()
        check("cleared closes_at stored as NULL", stored["closes_at"] is None, repr(stored["closes_at"]))
        check("blank opens_at stored as NULL", stored["opens_at"] is None, repr(stored["opens_at"]))

        # The open-period lookup is the buy-book's period resolver: it compares
        # closes_at to a timestamp, which is where a blank raised
        # InvalidDatetimeFormat on Postgres and 500ed every PDF render.
        with connect() as conn:
            open_no_close = get_open_survey_period(conn, company_id)
        check(
            "the open-period lookup runs with no close time",
            open_no_close is not None and int(open_no_close["id"]) == blank_id,
            repr(open_no_close["id"] if open_no_close is not None else None),
        )
        with_close = client.patch(
            f"/api/admin/survey-periods/{blank_id}",
            headers=headers,
            json={"closes_at": "2099-01-01T00:00:00+00:00"},
        )
        check("a real close time is accepted", with_close.status_code == 200, str(with_close.status_code))
        with connect() as conn:
            open_with_close = get_open_survey_period(conn, company_id)
        check(
            "the open-period lookup runs with a real close time",
            open_with_close is not None and int(open_with_close["id"]) == blank_id,
            repr(open_with_close["id"] if open_with_close is not None else None),
        )
        blank_read = survey_get(client, slug, blank_token)
        check(
            "the survey still reads open with a future close time",
            blank_read.status_code == 200,
            f"{blank_read.status_code} {str(blank_read.text)[:100]}",
        )
        garbage = client.patch(
            f"/api/admin/survey-periods/{blank_id}",
            headers=headers,
            json={"closes_at": "not-a-date"},
        )
        check(
            "a non-timestamp closes_at -> 400, not stored",
            garbage.status_code == 400,
            f"{garbage.status_code} {garbage.text[:100]}",
        )

        # --------------------------------------------------------------
        print("\n17. A legacy blank row is cleaned on boot")
        if not IS_POSTGRES:
            with connect() as conn:
                execute(
                    conn,
                    "UPDATE survey_periods SET closes_at = '' WHERE id = ?",
                    (blank_id,),
                )
            init_db()
            with connect() as conn:
                healed = execute(
                    conn,
                    "SELECT closes_at FROM survey_periods WHERE id = ?",
                    (blank_id,),
                ).fetchone()["closes_at"]
            check("the blank is NULL after init_db", healed is None, repr(healed))
        else:
            check("Postgres cannot store a blank in timestamptz (no cleanup needed)", True)

        # --------------------------------------------------------------
        print("\n18. The two legacy flags: one PUT each, one link each")
        listed = client.get("/api/admin/companies", headers=headers).json().get("companies", [])
        subject = next((row for row in listed if int(row.get("id", 0)) == company_id), None)
        check("the admin company list carries both flags",
              subject is not None
              and "legacy_public_buy_book" in subject
              and "legacy_public_survey" in subject,
              str(subject)[:160] if subject else "company row missing")
        check("both flags default on for an existing company",
              bool(subject and subject.get("legacy_public_buy_book"))
              and bool(subject and subject.get("legacy_public_survey")),
              str(subject)[:160] if subject else "")
        reopened = client.patch(
            f"/api/admin/survey-periods/{period_id}",
            headers=headers,
            json={"status": "open"},
        )
        check("an open period is in place for the flag comparison", reopened.status_code == 200,
              str(reopened.status_code))
        demo_headers = login(client, "demo@example.com", "Demo1234")
        try:
            off_book = client.patch(
                f"/api/admin/companies/{company_id}",
                headers=headers,
                json={"legacy_public_buy_book": False},
            )
            off_payload = (off_book.json().get("company") or {}) if off_book.status_code == 200 else {}
            check("one PUT turns the buy book off", off_book.status_code == 200,
                  f"{off_book.status_code} {off_book.text[:120]}")
            check("the PUT response carries both flags",
                  off_payload.get("legacy_public_buy_book") is False
                  and off_payload.get("legacy_public_survey") is True, str(off_payload)[:160])
            book_off = client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"})
            check("buy book off -> 404", book_off.status_code == 404, str(book_off.status_code))
            check("the survey link keeps answering while the book is off",
                  survey_get(client, slug, None).status_code == 200,
                  str(survey_get(client, slug, None).status_code))
            if demo_headers is not None:
                non_admin = client.patch(
                    f"/api/admin/companies/{company_id}",
                    headers=demo_headers,
                    json={"legacy_public_buy_book": True},
                )
                check("a non-admin PUT -> 403", non_admin.status_code == 403, str(non_admin.status_code))
                check("the non-admin PUT changed nothing",
                      client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"}).status_code == 404,
                      "the flag flipped")
            else:
                pending("a non-admin PUT still 403s", "no demo editor login in this database")

            survey_off = client.patch(
                f"/api/admin/companies/{company_id}",
                headers=headers,
                json={"legacy_public_survey": False},
            )
            check("one PUT turns the anonymous survey link off", survey_off.status_code == 200,
                  str(survey_off.status_code))
            anon_off = survey_get(client, slug, None)
            check("anonymous survey off -> 401", anon_off.status_code == 401, str(anon_off.status_code))
            check("its 401 names the missing credential",
                  "survey_token" in str(anon_off.json().get("detail", "")), str(anon_off.json())[:120])
            check("the token link still answers with the anonymous link off",
                  survey_get(client, slug, token).status_code == 200,
                  str(survey_get(client, slug, token).status_code))
            check("the buy book is untouched by the survey flag",
                  client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"}).status_code == 404,
                  "the book came back with the survey flag")
        finally:
            restored = client.patch(
                f"/api/admin/companies/{company_id}",
                headers=headers,
                json={"legacy_public_buy_book": True, "legacy_public_survey": True},
            )
            check("both flags can be turned back on from one PUT", restored.status_code == 200,
                  str(restored.status_code))
            client.patch(
                f"/api/admin/survey-periods/{period_id}",
                headers=headers,
                json={"status": "closed"},
            )
        check("both links answer again after the restore",
              client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"}).status_code == 200
              and survey_get(client, slug, None).status_code == 200,
              "the restore did not re-open both links")

    finally:
        if not args.keep:
            with connect() as conn:
                for period_id in created_periods:
                    execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
                # The admin create mints a legacy period for a company that has
                # none, so the scratch company owns periods this script never
                # listed; they go with it.
                execute(conn, "DELETE FROM survey_responses WHERE company_id = ?", (empty_company_id,))
                execute(conn, "DELETE FROM survey_periods WHERE company_id = ?", (empty_company_id,))
                execute(conn, "DELETE FROM companies WHERE id = ?", (empty_company_id,))

    print()
    if FAILURES:
        print(f"FAILURES ({len(FAILURES)}):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print(f"ALL CHECKS PASSED ({len(PENDING)} pending)" if PENDING else "ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
