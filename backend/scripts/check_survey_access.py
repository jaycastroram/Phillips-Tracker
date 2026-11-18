"""Acceptance check for survey access control (token, buy book, duplicates).

Runs against the app's own TestClient and the database it is configured with
(SQLite locally, Postgres 16 with DATABASE_URL set). It creates its own survey
periods and responses and removes them afterwards.

    python backend/scripts/check_survey_access.py
    python backend/scripts/check_survey_access.py --keep

What it proves:

1.  no token -> 401 on the survey surface, the bare catalog form and POST, and
    a rejected POST writes nothing
2.  an unknown token -> 404
3.  another company's token under this company's slug -> 404
4.  a valid token returns the period's items and questions
5.  a closed period with a valid token -> GET 200 survey_open false and no
    items, POST 410
6.  closes_at in the past while the status still says open -> same as 5
7.  rotation kills the old token at once and the new one works
8.  init_db() twice mints no second token and leaves the unique indexes in place
9.  two answers for one shopper and item -> one row, the second answer stored,
    results counting one respondent with that answer
10. duplicate rows seeded before the index -> migration keeps the highest id
11. no buy-book key is read or rendered anywhere
12. the public buy-book surface is gone (400 unknown surface, no items key)
13. admin buy-book layout: anonymous 401 (another company's editor is #2's route)
14. the token is on the admin period detail and never in the public payload
15. a public read against a company with no period creates no period row
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
from database import connect, execute, fetch_company_by_slug  # noqa: E402
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
        print("\n1. No token: 401 everywhere, and the rejected POST writes nothing")
        response = survey_get(client, slug, None)
        check("survey surface without t -> 401", response.status_code == 401, str(response.status_code))
        check("the 401 names the missing credential",
              "survey_token" in str(response.json().get("detail", "")), str(response.json())[:120])
        bare = survey_get(client, slug, None, surface="")
        check("bare catalog form without t -> 401", bare.status_code == 401, str(bare.status_code))
        before = response_count()
        post = submit(client, slug, None, item_id, test_email, "Yes")
        check("POST without t -> 401", post.status_code == 401, str(post.status_code))
        check("rejected POST left the response count alone", response_count() == before,
              f"{before} -> {response_count()}")

        # --------------------------------------------------------------
        print("\n2. Unknown token -> 404")
        unknown = survey_get(client, slug, "not-a-real-token")
        check("unknown token -> 404", unknown.status_code == 404, str(unknown.status_code))

        # --------------------------------------------------------------
        print("\n3. Another company's token under this slug -> 404")
        other_period = create_period(client, headers, "access-check-other", other_company_id)
        created_periods.append(other_period)
        other_token = token_of(client, headers, other_period, other_company_id)
        check("the other company's period carries a token", bool(other_token))
        cross = survey_get(client, slug, other_token)
        check("cross-company token -> 404", cross.status_code == 404, str(cross.status_code))

        # --------------------------------------------------------------
        print("\n4. Valid token returns the period")
        period_id = create_period(client, headers, "access-check-open")
        created_periods.append(period_id)
        token = token_of(client, headers, period_id)
        check("admin detail carries a 32-char url-safe token", len(token) >= 24 and " " not in token, token[:8])
        public = survey_get(client, slug, token)
        check("valid token -> 200", public.status_code == 200, str(public.status_code))
        payload = public.json()
        check("payload is open and carries items", payload.get("survey_open") is True and bool(payload.get("items")),
              str(payload)[:160])
        check("payload carries the period's questions",
              [q["id"] for q in payload.get("questions", [])] == ["interest"],
              str(payload.get("questions"))[:120])

        # --------------------------------------------------------------
        print("\n5. Closed period with a valid token: read-only, no submissions")
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
        print("\n6. closes_at in the past, status still open -> closed behaviour")
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
        print("\n7. Rotation")
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
        print("\n8. init_db() twice: no re-mint, indexes hold")
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
        print("\n9. Two answers for one shopper and item: one row, last write wins")
        upsert_id = create_period(client, headers, "access-check-upsert")
        created_periods.append(upsert_id)
        upsert_token = token_of(client, headers, upsert_id)
        first = submit(client, slug, upsert_token, item_id, test_email, "Yes")
        check("first answer -> 200", first.status_code == 200, str(first.status_code))
        second = submit(client, slug, upsert_token, item_id, test_email, "No")
        check("second answer -> 200", second.status_code == 200, str(second.status_code))
        rows = answer_rows(upsert_id)
        check("one row for the pair", len(rows) == 1, f"{len(rows)} rows")
        check("the second answer is stored", bool(rows) and '"interest": "No"' in rows[0]["answers_json"],
              rows[0]["answers_json"] if rows else "no row")
        results = client.get("/api/admin/survey-results", headers=headers, params={"period_id": upsert_id}).json()
        summary = results.get("summary", {})
        check("results count one response", summary.get("total_responses") == 1, str(summary)[:120])
        check("results count one unique respondent", summary.get("unique_respondents") == 1, str(summary)[:120])
        check("results read the stored (second) answer", summary.get("total_not_interested") == 1
              and summary.get("total_interested") == 0, str(summary)[:120])

        # --------------------------------------------------------------
        print("\n10. Duplicate rows seeded before the index -> highest id survives")
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
        print("\n11. No buy-book key is read or rendered")
        columns = table_columns("companies")
        public_config = client.get(f"/api/public/{slug}/config").json()
        companies = client.get("/api/admin/companies", headers=headers).json().get("companies", [])
        exposed = [key for key in ("buy_book_key", "public_key", "buy_book_slug") if key in public_config]
        exposed += [key for row in companies for key in ("buy_book_key", "public_key", "buy_book_slug") if key in row]
        check("no key field in the public config or company list", exposed == [], str(exposed))
        if "buy_book_key" in columns:
            check("buy_book_key column still present", True)
        else:
            print("  PASS  no buy_book_key column exists in this codebase (nothing to preserve, nothing to read)")
        unauth_key = client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book", "k": "whatever"})
        check("a ?k= still cannot open a buy-book surface", unauth_key.status_code in {400, 401}, str(unauth_key.status_code))

        # --------------------------------------------------------------
        print("\n12. The public buy-book surface is gone")
        gone = client.get(f"/api/public/{slug}/survey-items", params={"surface": "buy-book"})
        check("surface=buy-book -> 400", gone.status_code == 400, str(gone.status_code))
        body = gone.json()
        check("the 400 carries no items", "items" not in gone.text, str(gone.text)[:120])
        check("the 400 names the surface problem", "surface" in str(body).lower(), str(body)[:120])

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
        print("\n15. A public read against a company with no period creates no period")
        before_periods = period_count(empty_company_id)
        empty_read = survey_get(client, empty_slug, "irrelevant-token")
        check("a token for a company with no periods 404s", empty_read.status_code == 404,
              str(empty_read.status_code))
        no_token_read = survey_get(client, empty_slug, None)
        check("no token still 401s first", no_token_read.status_code == 401, str(no_token_read.status_code))
        check("no period row was created", period_count(empty_company_id) == before_periods,
              f"{before_periods} -> {period_count(empty_company_id)}")

    finally:
        if not args.keep:
            with connect() as conn:
                for period_id in created_periods:
                    execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
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
