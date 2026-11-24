"""Acceptance check for configurable survey questions.

A survey defines its questions once, in a pool; an item ticks the ones it asks
and the respondent's answers are stored per item x question. This check runs
against a temporary client of this app and the database it is configured with
(SQLite locally, Postgres 16 with DATABASE_URL set), creates its own surveys,
answers them, reads the results and the Excel export, then removes everything
it created.

    python backend/scripts/check_survey_questions.py
    python backend/scripts/check_survey_questions.py --company phillips --keep

What it proves:

1. a survey with all six question types returns them, in order and with their
   required flags and options, on the public surface of the item that asks them
2. required answers are enforced (400 naming the question), option membership is
   enforced (400), and a valid submission returns 200
3. a checkbox answer stores every picked option and exports as "A; B"; the
   same answer is tallied option by option for that item
4. a scale question's answers average per item, and a survey whose items ask
   no scale question reports no average at all
5. a question's own image reaches the public card
6. copying a past survey keeps its pool and its items' ticks
7. the builder payload carries only the survey's own items, and the catalog
   search stays paged and lean
8. init_db() twice leaves a survey's pool and ticks untouched
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

ACTIVE: dict[str, Any] = {"period_id": 0, "token": "", "item_ids": []}


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


def create_period(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    questions: list[dict[str, Any]],
    *,
    item_question_ids: list[str] | None = None,
    copy_from: int | None = None,
) -> int:
    body: dict[str, Any] = {"name": name, "open_immediately": True}
    if copy_from is None:
        body["questions"] = questions
        body["items"] = [{
            "item_name": "check item",
            "cost": "$10.00",
            "item_number": "90001",
            "item_description": "A mocked-up item",
            "question_ids": item_question_ids if item_question_ids is not None
            else [question["id"] for question in questions],
        }]
    else:
        body["copy_from_period_id"] = copy_from
    response = client.post("/api/admin/survey-periods", headers=headers, json=body)
    if response.status_code != 201:
        raise SystemExit(f"Could not create survey {name}: {response.status_code} {response.text[:300]}")
    period = response.json()["period"]
    ACTIVE["period_id"] = int(period["id"])
    ACTIVE["token"] = str(period.get("survey_token") or "")
    detail = client.get(f"/api/admin/survey-periods/{ACTIVE['period_id']}", headers=headers).json()
    ACTIVE["item_ids"] = [int(item["id"]) for item in detail.get("items", [])]
    return int(period["id"])


def public_payload(client: TestClient, slug: str) -> dict[str, Any]:
    response = client.get(
        f"/api/public/{slug}/survey-items",
        params={"surface": "survey", "t": ACTIVE["token"]},
    )
    response.raise_for_status()
    return response.json()


def submit(client: TestClient, slug: str, answers: Any, email: str | None = None,
           legacy: dict | None = None) -> Any:
    payload: dict[str, Any] = {"answers": answers}
    if email:
        payload["email"] = email
    if legacy is not None:
        payload.update(legacy)
    return client.post(f"/api/public/{slug}/survey-responses", params={"t": ACTIVE["token"]}, json=payload)


def results(client: TestClient, headers: dict[str, str], period_id: int) -> dict[str, Any]:
    response = client.get("/api/admin/survey-results", headers=headers, params={"period_id": period_id})
    response.raise_for_status()
    return response.json()


def item_results(client: TestClient, headers: dict[str, str], period_id: int) -> dict[str, Any]:
    for item in results(client, headers, period_id)["items"]:
        if item["item_name"] == "check item":
            return item
    raise SystemExit("no 'check item' in the results")


def tally(item: dict[str, Any], question_id: str) -> dict[str, Any]:
    match = next((entry for entry in item["questions"] if entry["question_id"] == question_id), None)
    if match is None:
        raise SystemExit(f"no tally for {question_id}")
    return match


def count_of(tally_row: dict[str, Any], option: str) -> int:
    match = next((choice for choice in tally_row["choices"] if choice["option"] == option), None)
    return int(match["count"]) if match else 0


def answer_value(period_id: int, item_id: int, question_id: str) -> Any:
    with connect() as conn:
        row = execute(
            conn,
            """
            SELECT a.answer_text, a.answer_options_json, a.answer_number
            FROM survey_answers a
            JOIN survey_responses r ON r.id = a.response_id
            WHERE r.survey_period_id = ? AND a.survey_period_item_id = ? AND a.question_id = ?
            ORDER BY a.id DESC LIMIT 1
            """,
            (period_id, item_id, question_id),
        ).fetchone()
    return dict(row) if row else None


def questions_json_of(period_id: int) -> str:
    with connect() as conn:
        row = execute(conn, "SELECT questions_json FROM survey_periods WHERE id = ?", (period_id,)).fetchone()
    return str(row["questions_json"] or "")


def column_names(table: str) -> list[str]:
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
    parser.add_argument("--keep", action="store_true", help="keep the surveys and responses this script creates")
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
        period_all = create_period(client, headers, "check-all-types", questions_for(
            ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"]
        ))
        created.append(period_all)
        public = public_payload(client, args.company)
        item = public["items"][0]
        types = [question["type"] for question in item["questions"]]
        check("the item asks every type, in pool order",
              types == ["yes_no", "single_choice", "dropdown", "checkbox", "text", "scale"], str(types))
        required = [question["id"] for question in item["questions"] if question["required"]]
        check("required flags survive", sorted(required) == ["interest", "size_fit"], str(required))
        checkbox_question = next(q for q in item["questions"] if q["type"] == "checkbox")
        check("checkbox keeps all three options",
              checkbox_question["options"] == ["Red", "Black", "Blue"], str(checkbox_question["options"]))
        check("the item carries its own cost and number",
              item["cost"] == "$10.00" and item["item_number"] == "90001",
              f"{item.get('cost')}/{item.get('item_number')}")
        item_id = int(item["id"])

        # ------------------------------------------------------------------
        print("\n2. Required and option validation")
        response = submit(client, args.company, {str(item_id): {"interest": "Yes"}}, "a@example.com")
        check("missing required answer is rejected", response.status_code == 400, f"HTTP {response.status_code}")
        check("the 400 names the question",
              "How is the size?" in str(response.json().get("detail", "")), str(response.text[:160]))

        response = submit(client, args.company, {str(item_id): {"interest": "Yes", "size_fit": "Slim fit"}},
                          "a@example.com")
        check("an option outside the list is rejected", response.status_code == 400, str(response.text[:160]))

        valid = {
            "interest": "Yes",
            "size_fit": "Just right",
            "channel_pick": "On Premise",
            "colors": ["Red", "Blue"],
            "feedback": "Looks good",
            "attention_1": 4,
        }
        response = submit(client, args.company, {str(item_id): valid}, "a@example.com")
        check("a valid answer returns 200", response.status_code == 200,
              f"HTTP {response.status_code} {response.text[:200]}")
        stored = answer_value(period_all, item_id, "colors")
        check("the checkbox answer stores both picks",
              stored is not None and str(stored["answer_options_json"]) == '["Red", "Blue"]',
              str(stored))

        # ------------------------------------------------------------------
        print("\n3. Checkbox tallies and export")
        response = submit(client, args.company, {str(item_id): {
            "interest": "No", "size_fit": "Too big", "colors": ["Red", "Black"], "attention_1": 2}},
            "b@example.com")
        check("second response accepted", response.status_code == 200, str(response.text[:160]))
        after = item_results(client, headers, period_all)
        colors = tally(after, "colors")
        check("each picked colour is counted on this item",
              (count_of(colors, "Red"), count_of(colors, "Black"), count_of(colors, "Blue")) == (2, 1, 1),
              str(colors["choices"]))
        check("the item counts two responses", after["response_count"] == 2, str(after["response_count"]))
        interest = tally(after, "interest")
        check("the yes/no tallies by option",
              (count_of(interest, "Yes"), count_of(interest, "No")) == (1, 1),
              str(interest["choices"]))

        export = client.get("/api/admin/survey-responses/export", headers=headers,
                            params={"period_id": period_all})
        check("export returns a workbook", export.status_code == 200 and export.content[:2] == b"PK")
        workbook = load_workbook(io.BytesIO(export.content))
        sheet = workbook.active
        headers_row = [cell.value for cell in sheet[1]]
        question_column = headers_row.index("Question") + 1
        answer_column = headers_row.index("Answer") + 1
        exported = [
            sheet.cell(row=row, column=answer_column).value
            for row in range(2, sheet.max_row + 1)
            if sheet.cell(row=row, column=question_column).value == "Which colors work?"
        ]
        check("checkbox exports as 'A; B'", "Red; Blue" in exported, str(exported))
        check("one row per answered question",
              sheet.max_row - 1 == 10, f"{sheet.max_row - 1} rows")
        check("the export names the item on each row",
              all(sheet.cell(row=row, column=headers_row.index("Item Name") + 1).value == "check item"
                  for row in range(2, sheet.max_row + 1)))

        # ------------------------------------------------------------------
        print("\n4. Scale averages are per item, and absent when not asked")
        check("a scale question averages on this item", tally(after, "attention_1")["average"] == 3.0,
              str(tally(after, "attention_1")["average"]))
        period_no_scale = create_period(
            client, headers, "check-no-scale", questions_for(["yes_no"]),
        )
        created.append(period_no_scale)
        no_scale_item = int(ACTIVE["item_ids"][0])
        response = submit(client, args.company, {str(no_scale_item): {"interest": "Yes"}}, "s@example.com")
        check("no-scale response accepted", response.status_code == 200, str(response.text[:160]))
        no_scale = item_results(client, headers, period_no_scale)
        check("no scale question means no tally for one",
              all(entry["question_id"] != "attention_1" for entry in no_scale["questions"]),
              str([entry["question_id"] for entry in no_scale["questions"]]))

        # ------------------------------------------------------------------
        print("\n5. A question's own image reaches the public card")
        period_image = create_period(client, headers, "check-image", [
            {"id": "interest", "type": "yes_no", "label": "Want it?", "required": True,
             "options": ["Yes", "No"], "image_url": "https://example.com/questions/q1.jpg"},
        ])
        created.append(period_image)
        image_payload = public_payload(client, args.company)
        image_question = image_payload["items"][0]["questions"][0]
        check("the question image is on the public question",
              image_question.get("image_url") == "https://example.com/questions/q1.jpg",
              str(image_question.get("image_url")))

        # ------------------------------------------------------------------
        print("\n6. Copying a survey keeps its pool and its items' ticks")
        period_source = create_period(client, headers, "check-source", questions_for(["yes_no"], interest_label="SOURCE interest") + [
            {"id": "source_only", "type": "text", "label": "SOURCE only question", "required": False, "options": []},
        ])
        created.append(period_source)
        copied_id = create_period(
            client, headers, "check-copied", questions_for(["yes_no"]), copy_from=period_source,
        )
        created.append(copied_id)
        copied = client.get(f"/api/admin/survey-periods/{copied_id}", headers=headers).json()
        copied_labels = [question["label"] for question in copied.get("questions", [])]
        check("the copied survey keeps the source's questions",
              copied_labels == ["SOURCE interest", "SOURCE only question"], str(copied_labels))
        check("the copied survey carries the source's item",
              [item["item_name"] for item in copied.get("items", [])] == ["check item"],
              str([item.get("item_name") for item in copied.get("items", [])]))
        check("and the item keeps ticking both questions",
              set(copied["items"][0]["question_ids"]) == {"interest", "source_only"},
              str(copied["items"][0]["question_ids"]))

        # ------------------------------------------------------------------
        print("\n7. The builder payload carries only the survey's own items")
        detail_response = client.get(f"/api/admin/survey-periods/{period_all}", headers=headers)
        detail = detail_response.json()
        detail_rows = detail.get("items", [])
        check("period detail returns only this survey's items", len(detail_rows) == 1,
              f"{len(detail_rows)} rows")
        check("the detail payload stays lean",
              len(detail_response.content) <= max(4_000, len(detail_rows) * 3_000),
              f"{len(detail_response.content)/1e3:.0f} KB for {len(detail_rows)} rows")

        empty = client.get("/api/admin/survey-catalog", headers=headers).json()
        check("catalog search requires a term", empty.get("requires_search") is True and not empty.get("items"),
              str(empty)[:80])
        found = client.get("/api/admin/survey-catalog", headers=headers, params={"q": "a", "limit": 5}).json()
        check("catalog search is paged", len(found.get("items", [])) <= 5, str(len(found.get("items", []))))
        check("catalog search returns lean rows",
              all("item_description" not in row and "image_url" not in row for row in found.get("items", [])),
              str(found.get("items", [{}])[0])[:80] if found.get("items") else "no rows")

        # ------------------------------------------------------------------
        print("\n8. Migrations do not rewrite an existing survey")
        before_json = questions_json_of(period_all)
        before_ticks = [
            item["question_ids"]
            for item in client.get(f"/api/admin/survey-periods/{period_all}", headers=headers).json()["items"]
        ]
        init_db()
        init_db()
        after_json = questions_json_of(period_all)
        after_ticks = [
            item["question_ids"]
            for item in client.get(f"/api/admin/survey-periods/{period_all}", headers=headers).json()["items"]
        ]
        columns = column_names("survey_periods")
        answer_columns = column_names("survey_answers")
        check("questions survive two migrations", before_json == after_json, after_json[:120])
        check("ticks survive two migrations", before_ticks == after_ticks, str(after_ticks))
        check("questions_json added once", columns.count("questions_json") == 1, str(columns))
        check("the answer table keeps one value per kind",
              {"answer_text", "answer_options_json", "answer_number"} <= set(answer_columns),
              str(answer_columns))
    finally:
        if not args.keep:
            with connect() as conn:
                for period_id in created:
                    execute(
                        conn,
                        "DELETE FROM survey_answers WHERE response_id IN"
                        " (SELECT id FROM survey_responses WHERE survey_period_id = ?)",
                        (period_id,),
                    )
                    execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (period_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
            print("\n(created surveys and their responses removed)")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
