"""Acceptance check for the standalone survey: a name and a question list.

Runs against a temporary client of this app and the database it is configured
with (SQLite locally, Postgres 16 with DATABASE_URL). It creates surveys,
submits answers through the public endpoint, reads the results and the Excel
export, then removes everything it created.

    python backend/scripts/check_survey_questions.py
    python backend/scripts/check_survey_questions.py --company phillips --keep

What it proves:

1. a survey created with all six question types returns them on the public
   surface, with the required flags and one image URL intact
2. a survey carries no item list — the public payload's ``items`` is empty and
   nothing in the flow reads the catalog
3. required answers are enforced (400 naming the question), option membership
   is enforced (400), and a valid submission is stored as one row
4. tallies follow the survey's own questions: choice counts, a scale average
   and every text answer, including after a rename and a relabelled option set
5. a survey with no scale question reports no average; a scale question does
6. the Excel export puts one column per question after the answers
7. init_db() twice leaves an existing survey's questions untouched
"""

from __future__ import annotations

import argparse
import io
import json
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

# Creating a survey closes the others, so the newest created one is the open
# survey every public submission below answers.
ACTIVE: dict[str, Any] = {"period_id": 0, "token": ""}


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


def questions_for(types: list[str], *, interest_label: str = "Are you interested?",
                  interest_options: list[str] | None = None,
                  image_url: str = "") -> list[dict[str, Any]]:
    builders = {
        "yes_no": {
            "id": "interest",
            "type": "yes_no",
            "label": interest_label,
            "required": True,
            "options": interest_options or ["Yes", "No"],
            "image_url": image_url,
        },
        "single_choice": {
            "id": "size_fit",
            "type": "single_choice",
            "label": "How is the size?",
            "required": True,
            "options": ["Too small", "Just right", "Too big"],
        },
        "dropdown": {
            "id": "channel_pick",
            "type": "dropdown",
            "label": "Which channel?",
            "required": False,
            "options": ["On Premise", "Off Premise"],
        },
        "checkbox": {
            "id": "colors",
            "type": "checkbox",
            "label": "Which colors work?",
            "required": False,
            "options": ["Red", "Black", "Blue"],
        },
        "text": {
            "id": "feedback",
            "type": "text",
            "label": "Any other feedback?",
            "required": False,
            "options": [],
        },
        "scale": {
            "id": "attention_1",
            "type": "scale",
            "label": "How much did this catch your eye?",
            "required": False,
            "options": [],
        },
    }
    return [dict(builders[question_type]) for question_type in types]


def create_period(client: TestClient, headers: dict[str, str], name: str, questions: list[dict[str, Any]]) -> int:
    response = client.post(
        "/api/admin/survey-periods",
        headers=headers,
        json={"name": name, "open_immediately": True, "questions": questions},
    )
    if response.status_code != 201:
        raise SystemExit(f"Could not create survey {name}: {response.status_code} {response.text[:300]}")
    period = response.json()["period"]
    ACTIVE["period_id"] = int(period["id"])
    ACTIVE["token"] = str(period.get("survey_token") or "")
    return int(period["id"])


def submit(client: TestClient, slug: str, email: str | None, answers: dict[str, Any]) -> Any:
    payload: dict[str, Any] = {"answers": answers}
    if email:
        payload["email"] = email
    return client.post(
        f"/api/public/{slug}/survey-submissions",
        params={"t": ACTIVE["token"]},
        json=payload,
    )


def results(client: TestClient, headers: dict[str, str], period_id: int) -> dict[str, Any]:
    response = client.get("/api/admin/survey-results", headers=headers, params={"period_id": period_id})
    response.raise_for_status()
    return response.json()


def stat_of(report: dict[str, Any], question_id: str) -> dict[str, Any]:
    return next((row for row in report.get("question_stats", []) if row["id"] == question_id), {})


def submission_count(period_id: int) -> int:
    with connect() as conn:
        return int(execute(
            conn,
            "SELECT COUNT(*) AS n FROM survey_submissions WHERE survey_period_id = ?",
            (period_id,),
        ).fetchone()["n"])


def questions_json_of(period_id: int) -> str:
    with connect() as conn:
        row = execute(conn, "SELECT questions_json FROM survey_periods WHERE id = ?", (period_id,)).fetchone()
    return str(row["questions_json"] or "")


def column_names(table: str) -> list[str]:
    """Works on both backends: dict_row on Postgres has no positional access."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--company", default=DEFAULT_COMPANY_SLUG)
    parser.add_argument("--keep", action="store_true", help="keep the surveys and submissions this script creates")
    args = parser.parse_args()

    init_db()
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if login.status_code != 200:
        raise SystemExit(f"Login failed ({login.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    created: list[int] = []
    try:
        # ------------------------------------------------------------------
        print("\n1. All six types round-trip to the public surface")
        image = "https://res.cloudinary.com/demo/image/upload/sample.jpg"
        period_all = create_period(client, headers, "check-all-types", questions_for(
            ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"], image_url=image
        ))
        created.append(period_all)
        public = client.get(
            f"/api/public/{args.company}/survey-items",
            params={"surface": "survey", "t": ACTIVE["token"]},
        ).json()
        types = [question["type"] for question in public.get("questions", [])]
        check("public surface returns every type",
              types == ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"], str(types))
        required = [question["id"] for question in public["questions"] if question["required"]]
        check("required flags survive", sorted(required) == ["interest", "size_fit"], str(required))
        checkbox_question = next(q for q in public["questions"] if q["type"] == "checkbox")
        check("checkbox keeps all three options",
              checkbox_question["options"] == ["Red", "Black", "Blue"], str(checkbox_question["options"]))
        check("the question image survives",
              public["questions"][0].get("image_url") == image, str(public["questions"][0].get("image_url")))

        # ------------------------------------------------------------------
        print("\n2. A survey carries no item list")
        check("public payload has an empty items list", public.get("items") == [], str(public.get("items"))[:80])
        with connect() as conn:
            period_items = int(execute(
                conn,
                "SELECT COUNT(*) AS n FROM survey_period_items WHERE survey_period_id = ?",
                (period_all,),
            ).fetchone()["n"])
        check("creating a survey copies no catalog items", period_items == 0, f"{period_items} rows")

        # ------------------------------------------------------------------
        print("\n3. Required and option validation, one submission per post")
        response = submit(client, args.company, "a@example.com", {"interest": "Yes"})
        check("missing required answer is rejected", response.status_code == 400, f"HTTP {response.status_code}")
        check("the 400 names the question",
              "How is the size?" in str(response.json().get("detail", "")), str(response.text[:160]))

        response = submit(client, args.company, "a@example.com", {"interest": "Yes", "size_fit": "Slim fit"})
        check("an option outside the list is rejected", response.status_code == 400, str(response.text[:160]))
        check("nothing was stored by the refused posts", submission_count(period_all) == 0,
              f"{submission_count(period_all)} rows")

        valid = {
            "interest": "Yes",
            "size_fit": "Just right",
            "channel_pick": "On Premise",
            "colors": ["Red", "Blue"],
            "feedback": "Looks good",
            "attention_1": 4,
        }
        response = submit(client, args.company, "a@example.com", valid)
        check("a valid submission is accepted", response.status_code == 201,
              f"HTTP {response.status_code} {response.text[:200]}")
        check("one row per submission", submission_count(period_all) == 1, f"{submission_count(period_all)} rows")
        stored = response.json().get("submission", {})
        check("the stored answers carry both checkbox picks",
              stored.get("answers", {}).get("colors") == ["Red", "Blue"], str(stored.get("answers")))

        # ------------------------------------------------------------------
        print("\n4. Tallies follow the survey's own questions")
        submit(client, args.company, "b@example.com", {
            "interest": "No", "size_fit": "Too big", "colors": ["Red", "Black"], "attention_1": 2})
        report = results(client, headers, period_all)
        interest_stat = stat_of(report, "interest")
        check("interest counts both answers",
              interest_stat.get("counts") == {"Yes": 1, "No": 1}, str(interest_stat.get("counts")))
        size_stat = stat_of(report, "size_fit")
        check("single choice counts each option",
              size_stat.get("counts") == {"Too small": 0, "Just right": 1, "Too big": 1},
              str(size_stat.get("counts")))
        color_stat = stat_of(report, "colors")
        check("checkbox tallies count each pick",
              color_stat.get("counts") == {"Red": 2, "Black": 1, "Blue": 1}, str(color_stat.get("counts")))
        scale_stat = stat_of(report, "attention_1")
        check("scale reports an average", scale_stat.get("average") == 3.0, str(scale_stat.get("average")))
        feedback_stat = stat_of(report, "feedback")
        check("text answers are listed",
              [entry["answer"] for entry in feedback_stat.get("texts", [])] == ["Looks good"],
              str(feedback_stat.get("texts")))
        check("report counts two submissions",
              report["summary"].get("total_submissions") == 2, str(report["summary"])[:140])
        check("report counts two respondents",
              report["summary"].get("unique_respondents") == 2, str(report["summary"])[:140])

        # ------------------------------------------------------------------
        print("\n5. Export puts one column per question after the answers")
        with connect() as conn:
            period_row = execute(conn, "SELECT questions_json FROM survey_periods WHERE id = ?", (period_all,)).fetchone()
        question_labels = json.loads(period_row["questions_json"])
        export = client.get("/api/admin/survey-responses/export", headers=headers, params={"period_id": period_all})
        check("export returns a workbook", export.status_code == 200 and export.content[:2] == b"PK")
        workbook = load_workbook(io.BytesIO(export.content))
        sheet = workbook.active
        headers_row = [cell.value for cell in sheet[1]]
        color_column = headers_row.index("Which colors work?") + 1
        exported = [sheet.cell(row=row, column=color_column).value for row in range(2, sheet.max_row + 1)]
        check("checkbox exports as 'A; B'", "Red; Blue" in exported, str(exported))
        check("one column per question",
              all(question["label"] in headers_row for question in question_labels),
              str(headers_row))
        check("one row per submission", sheet.max_row - 1 == 2, str(sheet.max_row))

        # ------------------------------------------------------------------
        print("\n6. A renamed survey still tallies, and a relabelled option set follows")
        period_renamed = create_period(client, headers, "check-renamed", questions_for(
            ["yes_no"], interest_label="Want it?"
        ))
        created.append(period_renamed)
        for email, answer in [("y1@example.com", "Yes"), ("y2@example.com", "Yes"), ("n1@example.com", "No")]:
            response = submit(client, args.company, email, {"interest": answer})
            check(f"submit {answer} for {email}", response.status_code == 201, str(response.text[:120]))
        renamed = results(client, headers, period_renamed)
        renamed_stat = stat_of(renamed, "interest")
        check("renamed yes/no tallies 2 yes, 1 no",
              renamed_stat.get("counts") == {"Yes": 2, "No": 1}, str(renamed_stat.get("counts")))
        check("summary follows the renamed question",
              renamed["summary"].get("total_interested") == 2
              and renamed["summary"].get("total_not_interested") == 1,
              str(renamed["summary"])[:160])

        period_relabelled = create_period(client, headers, "check-relabelled", questions_for(
            ["yes_no"], interest_options=["Interested", "Not interested"]
        ))
        created.append(period_relabelled)
        for email, answer in [("yy@example.com", "Interested"), ("nn@example.com", "Not interested")]:
            response = submit(client, args.company, email, {"interest": answer})
            check(f"submit {answer} for {email}", response.status_code == 201, str(response.text[:120]))
        relabelled = results(client, headers, period_relabelled)
        relabelled_stat = stat_of(relabelled, "interest")
        check("relabelled options tally under their own labels",
              relabelled_stat.get("counts") == {"Interested": 1, "Not interested": 1},
              str(relabelled_stat.get("counts")))

        # ------------------------------------------------------------------
        print("\n7. No scale question -> no average; a scale question -> one")
        no_scale = results(client, headers, period_renamed)
        check("no scale question means no scale stat",
              stat_of(no_scale, "attention_1") == {}, str([s["id"] for s in no_scale["question_stats"]]))
        with_scale = results(client, headers, period_all)
        check("a scale question reports an average only when answered",
              stat_of(with_scale, "attention_1").get("average") == 3.0,
              str(stat_of(with_scale, "attention_1")))

        # ------------------------------------------------------------------
        print("\n8. init_db() twice leaves an existing survey's questions untouched")
        before_questions = questions_json_of(period_all)
        init_db()
        init_db()
        check("the questions are unchanged", questions_json_of(period_all) == before_questions)
        check("the submissions table still exists", "answers_json" in column_names("survey_submissions"),
              str(column_names("survey_submissions"))[:140])
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
