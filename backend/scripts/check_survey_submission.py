"""Acceptance check for the standalone survey link, end to end.

A survey is a name plus an ordered question list. This check proves the whole
public flow on the database the app is configured with (SQLite locally;
Postgres 16 when DATABASE_URL points at it):

1. the static link opens the open survey with no token at all
2. the page payload is a question list — no catalog items
3. one visitor submits one response and exactly one row is stored
4. the admin report reads it back: count, respondent, and per-question tallies
5. the Excel export carries that answer under its question's column
6. a closed survey refuses the submission (410) and a survey with no open
   period (403)
7. init_db() is idempotent: the submissions table and its index survive a
   second run

    python backend/scripts/check_survey_submission.py
    DATABASE_URL=postgresql://... python backend/scripts/check_survey_submission.py
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from app import app  # noqa: E402
from config import ADMIN_EMAIL, ADMIN_PASSWORD, DEFAULT_COMPANY_SLUG, IS_POSTGRES  # noqa: E402
from database import connect, execute  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


QUESTIONS = [
    {"id": "q_rating", "type": "scale", "label": "How interested are you?", "required": True, "options": []},
    {"id": "q_size", "type": "single_choice", "label": "Which size do you stock?",
     "required": False, "options": ["Small", "Medium", "Large"]},
    {"id": "q_notes", "type": "text", "label": "Anything else?", "required": False, "options": []},
]


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


def submission_rows(period_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT id, email, answers_json FROM survey_submissions WHERE survey_period_id = ? ORDER BY id",
            (period_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", default=DEFAULT_COMPANY_SLUG)
    parser.add_argument("--keep", action="store_true", help="keep the survey and submission this script creates")
    args = parser.parse_args()

    print(f"Backend: {'Postgres' if IS_POSTGRES else 'SQLite'}")
    init_db()
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if login.status_code != 200:
        raise SystemExit(f"Login failed ({login.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    created: list[int] = []
    try:
        # ------------------------------------------------------------------
        print("\n1. The static link opens the survey with no token")
        create = client.post(
            "/api/admin/survey-periods",
            headers=headers,
            json={"name": "check-submission-flow", "open_immediately": True, "questions": QUESTIONS},
        )
        check("survey created and opened", create.status_code == 201, f"HTTP {create.status_code} {create.text[:160]}")
        period_id = int(create.json()["period"]["id"])
        created.append(period_id)

        page = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "survey"})
        check("the static link answers 200", page.status_code == 200, f"HTTP {page.status_code}")
        body = page.json()
        check("the survey reads open", body.get("survey_open") is True, str(body)[:160])
        check("the page carries the question list in order",
              [question["id"] for question in body.get("questions", [])] == ["q_rating", "q_size", "q_notes"],
              str([q.get("id") for q in body.get("questions", [])]))
        check("the page carries no catalog items", body.get("items") == [], str(body.get("items"))[:80])
        check("the page carries a period block", isinstance(body.get("period"), dict), str(body.get("period"))[:120])

        # ------------------------------------------------------------------
        print("\n2. One visitor submits one response")
        submit = client.post(
            f"/api/public/{args.company}/survey-submissions",
            json={
                "email": "buyer@example.com",
                "answers": {"q_rating": 5, "q_size": "Medium", "q_notes": "Order three cases"},
            },
        )
        check("submission accepted", submit.status_code == 201, f"HTTP {submit.status_code} {submit.text[:200]}")
        stored = submit.json().get("submission", {})
        check("the response echoes the answers", stored.get("answers", {}).get("q_size") == "Medium",
              str(stored.get("answers")))
        check("the response carries no item id", "survey_item_id" not in stored, str(list(stored.keys())))
        rows = submission_rows(period_id)
        check("exactly one row stored", len(rows) == 1, f"{len(rows)} rows")
        check("the row keeps the answer set", bool(rows) and '"q_rating": 5' in rows[0]["answers_json"],
              rows[0]["answers_json"] if rows else "no row")

        # A required answer is still required through the link.
        missing = client.post(
            f"/api/public/{args.company}/survey-submissions",
            json={"answers": {"q_size": "Medium"}},
        )
        check("a missing required answer -> 400", missing.status_code == 400, f"HTTP {missing.status_code}")
        check("the 400 names the question", "How interested are you?" in str(missing.json().get("detail", "")),
              str(missing.text[:160]))
        check("the refused post stored nothing", len(submission_rows(period_id)) == 1,
              f"{len(submission_rows(period_id))} rows")

        # ------------------------------------------------------------------
        print("\n3. The report reads the submission back")
        report = client.get("/api/admin/survey-results", headers=headers, params={"period_id": period_id})
        check("report answers 200", report.status_code == 200, f"HTTP {report.status_code}")
        report_body = report.json()
        summary = report_body.get("summary", {})
        check("report counts one submission", summary.get("total_submissions") == 1, str(summary)[:140])
        check("report counts one respondent", summary.get("unique_respondents") == 1, str(summary)[:140])
        check("report lists the submission",
              [row.get("email") for row in report_body.get("submissions", [])] == ["buyer@example.com"],
              str(report_body.get("submissions"))[:160])
        stats = {row["id"]: row for row in report_body.get("question_stats", [])}
        check("the scale question reports its average", stats.get("q_rating", {}).get("average") == 5.0,
              str(stats.get("q_rating")))
        check("the choice question counts the pick",
              stats.get("q_size", {}).get("counts", {}).get("Medium") == 1,
              str(stats.get("q_size", {}).get("counts")))
        check("the text question lists the answer",
              [entry["answer"] for entry in stats.get("q_notes", {}).get("texts", [])] == ["Order three cases"],
              str(stats.get("q_notes", {}).get("texts")))

        # ------------------------------------------------------------------
        print("\n4. The Excel export carries the answer")
        export = client.get("/api/admin/survey-responses/export", headers=headers, params={"period_id": period_id})
        check("export returns a workbook", export.status_code == 200 and export.content[:2] == b"PK")
        sheet = load_workbook(io.BytesIO(export.content)).active
        header_row = [cell.value for cell in sheet[1]]
        notes_column = header_row.index("Anything else?") + 1
        check("one data row", sheet.max_row - 1 == 1, str(sheet.max_row))
        check("the text answer is in its column",
              sheet.cell(row=2, column=notes_column).value == "Order three cases",
              str(sheet.cell(row=2, column=notes_column).value))

        # ------------------------------------------------------------------
        print("\n5. A closed survey collects nothing")
        close = client.patch(f"/api/admin/survey-periods/{period_id}", headers=headers, json={"status": "closed"})
        check("survey closed", close.status_code == 200, f"HTTP {close.status_code}")
        closed_post = client.post(
            f"/api/public/{args.company}/survey-submissions",
            json={"answers": {"q_rating": 3}},
        )
        check("closed -> 403 on the anonymous link", closed_post.status_code == 403,
              f"HTTP {closed_post.status_code}")
        check("the refusal stored nothing", len(submission_rows(period_id)) == 1,
              f"{len(submission_rows(period_id))} rows")

        # ------------------------------------------------------------------
        print("\n6. init_db() twice: the submissions table is idempotent")
        columns = table_columns("survey_submissions")
        check("table exists with the expected columns",
              {"company_id", "survey_period_id", "email", "answers_json", "submitted_at"} <= set(columns),
              str(columns))
        init_db()
        init_db()
        check("columns unchanged after two more runs", table_columns("survey_submissions") == columns,
              str(table_columns("survey_submissions")))
        check("the rows survive a re-init", len(submission_rows(period_id)) == 1,
              f"{len(submission_rows(period_id))} rows")
    finally:
        if not args.keep:
            with connect() as conn:
                for period_id in created:
                    execute(conn, "DELETE FROM survey_submissions WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
            print(f"\nCleaned up {len(created)} check surveys.")

    if FAILURES:
        print(f"\nFAILURES ({len(FAILURES)}):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
