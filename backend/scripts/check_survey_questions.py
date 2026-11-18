"""Acceptance check for configurable survey questions.

Runs against a temporary client of this app and the database it is configured
with (SQLite locally). It creates survey periods, answers them, reads the
results and the Excel export, then removes everything it created.

    python backend/scripts/check_survey_questions.py
    python backend/scripts/check_survey_questions.py --company phillips --keep

What it proves:

1. a period created with all six question types returns them on the public
   surface, and the public form data carries required flags
2. required answers are enforced (400 naming the question), option membership is
   enforced (400), and a valid submission returns 200
3. a checkbox answer stores every picked option, exports as "A; B", and does
   not change the interest counts
4. interest counts follow the interest question after a rename and after
   relabelling its options, and a response with no answers_json falls back to
   the stored legacy column
5. a period with no scale question reports no average attention/engagement
6. init_db() twice leaves an existing period's questions untouched
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
from database import connect, execute, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402

FAILURES: list[str] = []

# The newest created period is the open one (creating a period closes the rest),
# so every public call below uses that period's token.
ACTIVE: dict[str, Any] = {"period_id": 0, "token": ""}


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


def questions_for(types: list[str], *, interest_label: str = "Are you interested in this item?",
                  interest_options: list[str] | None = None) -> list[dict[str, Any]]:
    builders = {
        "yes_no": {
            "id": "interest",
            "type": "yes_no",
            "label": interest_label,
            "required": True,
            "options": interest_options or ["Yes", "No"],
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
        raise SystemExit(f"Could not create period {name}: {response.status_code} {response.text[:300]}")
    period = response.json()["period"]
    ACTIVE["period_id"] = int(period["id"])
    ACTIVE["token"] = str(period.get("survey_token") or "")
    return int(period["id"])


def submit(client: TestClient, slug: str, item_id: int, email: str, answers: Any, legacy: dict | None = None) -> Any:
    payload: dict[str, Any] = {"survey_item_id": item_id, "email": email}
    if legacy is not None:
        payload.update(legacy)
    if answers is not None:
        payload["answers"] = answers
    return client.post(
        f"/api/public/{slug}/survey-responses",
        params={"t": ACTIVE["token"]},
        json=payload,
    )


def results(client: TestClient, headers: dict[str, str], period_id: int) -> dict[str, Any]:
    response = client.get("/api/admin/survey-results", headers=headers, params={"period_id": period_id})
    response.raise_for_status()
    return response.json()


def count_answers(item_id: int, period_id: int) -> dict[str, Any]:
    with connect() as conn:
        return dict(execute(
            conn,
            "SELECT answers_json, recommend_rollout FROM survey_responses"
            " WHERE survey_item_id = ? AND survey_period_id = ? ORDER BY id DESC LIMIT 1",
            (item_id, period_id),
        ).fetchone())


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
    parser.add_argument("--keep", action="store_true", help="keep the periods and responses this script creates")
    args = parser.parse_args()

    init_db()
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if login.status_code != 200:
        raise SystemExit(f"Login failed ({login.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    catalog = client.get("/api/admin/survey-layout", headers=headers).json()["items"]
    if not catalog:
        raise SystemExit("No catalog items — seed the database first.")
    item_id = int(catalog[0]["id"])

    created: list[int] = []
    try:
        # ------------------------------------------------------------------
        print("\n1. All six types round-trip to the public surface")
        period_all = create_period(client, headers, "check-all-types", questions_for(
            ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"]
        ))
        created.append(period_all)
        public = client.get(
            f"/api/public/{args.company}/survey-items",
            params={"surface": "survey", "t": ACTIVE["token"]},
        ).json()
        types = [question["type"] for question in public.get("questions", [])]
        check("public surface returns every type", types == ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"], str(types))
        required = [question["id"] for question in public["questions"] if question["required"]]
        check("required flags survive", sorted(required) == ["interest", "size_fit"], str(required))
        checkbox_question = next(q for q in public["questions"] if q["type"] == "checkbox")
        check("checkbox keeps all three options",
              checkbox_question["options"] == ["Red", "Black", "Blue"], str(checkbox_question["options"]))

        # ------------------------------------------------------------------
        print("\n2. Required and option validation")
        item_in_period = next((row for row in public["items"] if int(item_id) == int(row["id"])), None)
        check("the item is in the open survey snapshot", item_in_period is not None)

        response = submit(client, args.company, item_id, "a@example.com", {"interest": "Yes"})
        check("missing required answer is rejected", response.status_code == 400, f"HTTP {response.status_code}")
        check("the 400 names the question", "How is the size?" in str(response.json().get("detail", "")), str(response.text[:160]))

        response = submit(client, args.company, item_id, "a@example.com", {
            "interest": "Yes", "size_fit": "Slim fit"})
        check("an option outside the list is rejected", response.status_code == 400, str(response.text[:160]))

        valid = {
            "interest": "Yes",
            "size_fit": "Just right",
            "channel_pick": "On Premise",
            "colors": ["Red", "Blue"],
            "feedback": "Looks good",
            "attention_1": 4,
        }
        response = submit(client, args.company, item_id, "a@example.com", valid)
        check("a valid answer returns 200", response.status_code == 200, f"HTTP {response.status_code} {response.text[:200]}")
        stored = count_answers(item_id, period_all)
        import json as _json
        check("the checkbox answer stores both picks",
              _json.loads(stored["answers_json"]).get("colors") == ["Red", "Blue"], stored["answers_json"])

        # ------------------------------------------------------------------
        print("\n3. Checkbox export and unchanged counts")
        before = results(client, headers, period_all)
        before_counts = {row["id"]: (row["interested"], row["not_interested"]) for row in before["items"]}
        response = submit(client, args.company, item_id, "b@example.com", {
            "interest": "No", "size_fit": "Too big", "colors": ["Red", "Black"], "attention_1": 2})
        check("second response accepted", response.status_code == 200, str(response.text[:160]))
        after = results(client, headers, period_all)
        item_after = next(row for row in after["items"] if row["id"] == item_id)
        check("counts follow the answers, not the checkbox",
              (item_after["interested"], item_after["not_interested"]) == (1, 1),
              f"{item_after['interested']}/{item_after['not_interested']}")

        with connect() as conn:
            period_row = execute(conn, "SELECT questions_json FROM survey_periods WHERE id = ?", (period_all,)).fetchone()
        question_labels = _json.loads(period_row["questions_json"])
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

        # ------------------------------------------------------------------
        print("\n4. Interest question resolution")
        period_renamed = create_period(client, headers, "check-renamed", questions_for(
            ["yes_no"], interest_label="Want it?"
        ))
        created.append(period_renamed)
        for email, answer in [("y1@example.com", "Yes"), ("y2@example.com", "Yes"), ("n1@example.com", "No")]:
            response = submit(client, args.company, item_id, email, {"interest": answer})
            check(f"submit {answer} for {email}", response.status_code == 200, str(response.text[:120]))
        renamed = results(client, headers, period_renamed)
        renamed_item = next(row for row in renamed["items"] if row["id"] == item_id)
        check("renamed yes/no gives 2 interested, 1 not",
              (renamed_item["interested"], renamed_item["not_interested"]) == (2, 1),
              f"{renamed_item['interested']}/{renamed_item['not_interested']}")

        period_relabelled = create_period(client, headers, "check-relabelled", questions_for(
            ["yes_no"], interest_options=["Interested", "Not interested"]
        ))
        created.append(period_relabelled)
        for email, answer in [("y1@example.com", "Interested"), ("y2@example.com", "Interested"), ("n1@example.com", "Not interested")]:
            submit(client, args.company, item_id, email, {"interest": answer})
        relabelled = results(client, headers, period_relabelled)
        relabelled_item = next(row for row in relabelled["items"] if row["id"] == item_id)
        check("relabelled options give the same counts",
              (relabelled_item["interested"], relabelled_item["not_interested"]) == (2, 1),
              f"{relabelled_item['interested']}/{relabelled_item['not_interested']}")

        # ------------------------------------------------------------------
        print("\n4b. Copy from a past survey keeps that survey's questions")
        period_source = create_period(client, headers, "check-source", questions_for(["yes_no"], interest_label="SOURCE interest") + [
            {"id": "source_only", "type": "text", "label": "SOURCE only question", "required": False, "options": []},
        ])
        created.append(period_source)
        response = client.post("/api/admin/survey-periods", headers=headers, json={
            "name": "check-copied", "open_immediately": False, "copy_from_period_id": period_source,
        })
        check("copy-from create succeeds", response.status_code == 201, f"HTTP {response.status_code} {response.text[:160]}")
        copied_id = int(response.json()["period"]["id"])
        created.append(copied_id)
        copied = client.get(f"/api/admin/survey-periods/{copied_id}", headers=headers).json()
        copied_labels = [question["label"] for question in copied.get("questions", [])]
        check("the copied survey keeps the source's questions",
              copied_labels == ["SOURCE interest", "SOURCE only question"], str(copied_labels))

        # ------------------------------------------------------------------
        print("\n5. Legacy response, and no synthesised averages")
        # A fresh open period with a required interest question and no scale
        # question: the legacy payload must satisfy it and must not produce
        # average figures out of thin air.
        period_legacy = create_period(client, headers, "check-legacy", questions_for(["yes_no"]))
        created.append(period_legacy)
        legacy = submit(client, args.company, item_id, "legacy@example.com", None, legacy={
            "attention_effectiveness": 5, "recommend_rollout": "Yes", "retail_engagement": 5,
            "stands_out": "Yes", "price_reasonable": "Yes", "feedback": "legacy note",
        })
        check("a legacy POST without answers is accepted", legacy.status_code == 200, f"HTTP {legacy.status_code} {legacy.text[:160]}")
        legacy_results = results(client, headers, period_legacy)
        legacy_item = next(row for row in legacy_results["items"] if row["id"] == item_id)
        check("the legacy response is counted", legacy_item["interested"] == 1, str(legacy_item["interested"]))
        check("no scale question means no average figure",
              legacy_item["avg_attention"] is None and legacy_item["avg_engagement"] is None,
              f"{legacy_item['avg_attention']}/{legacy_item['avg_engagement']}")
        scaled = next(row for row in after["items"] if row["id"] == item_id)
        check("one scale question sets attention only",
              scaled["avg_attention"] == 3.0 and scaled["avg_engagement"] is None,
              f"{scaled['avg_attention']}/{scaled['avg_engagement']}")

        period_two_scales = create_period(client, headers, "check-two-scales", questions_for(["yes_no"]) + [
            {"id": "attention_1", "type": "scale", "label": "Attention score", "required": False, "options": []},
            {"id": "engagement_1", "type": "scale", "label": "Engagement score", "required": False, "options": []},
        ])
        created.append(period_two_scales)
        response = submit(client, args.company, item_id, "two@example.com", {
            "interest": "Yes", "attention_1": 5, "engagement_1": 2})
        check("two-scale response accepted", response.status_code == 200, str(response.text[:160]))
        two_scale = results(client, headers, period_two_scales)
        two_scale_item = next(row for row in two_scale["items"] if row["id"] == item_id)
        check("attention and engagement are different numbers",
              (two_scale_item["avg_attention"], two_scale_item["avg_engagement"]) == (5.0, 2.0),
              f"{two_scale_item['avg_attention']}/{two_scale_item['avg_engagement']}")

        # ------------------------------------------------------------------
        print("\n6. Migrations do not rewrite an existing period")
        before_json = questions_json_of(period_all)
        init_db()
        init_db()
        after_json = questions_json_of(period_all)
        columns = column_names("survey_periods")
        response_columns = column_names("survey_responses")
        check("questions survive two migrations", before_json == after_json, after_json[:120])
        check("questions_json added once", columns.count("questions_json") == 1, str(columns))
        check("answers_json added once", response_columns.count("answers_json") == 1, str(response_columns))
    finally:
        if not args.keep:
            with connect() as conn:
                for period_id in created:
                    execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
            print("\n(created periods and their responses removed)")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
