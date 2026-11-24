"""Acceptance check for item-based surveys.

A survey is a name, a pool of questions defined once, and a list of items the
survey owns. Each item ticks which of the pool's questions it asks, a
respondent answers item by item and submits once, and the results read back per
item. Runs against the app's own TestClient and whatever database it is
configured with (SQLite locally, Postgres 16 with DATABASE_URL set):

    python backend/scripts/check_survey_items.py
    python backend/scripts/check_survey_items.py --company phillips --keep

What it proves:

1.  the builder creates a survey with two items over one pool, and each item
    keeps the set of questions it was given (different sets, same pool)
2.  the public surface serves one card per item in survey order, carrying its
    image, name, cost, item number, description and only its own questions
3.  the static link (no token) opens the open survey, and one submission covers
    every item; required answers are enforced per item, and an answer for a
    question the item does not ask is refused
4.  results are per item: each item's response count and each of its questions'
    tallies (choice counts, scale average, text answers)
5.  the Excel export is one row per respondent x item x question, with the item
    image URL, number, description, name and cost printed on every row
6.  editing a survey keeps ids: a relabelled question, a reordered item and an
    unticked question leave every stored answer attached to its item
7.  one submission per respondent: a second submit updates, never duplicates
8.  the migration is idempotent on SQLite and Postgres
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

ITEM_IMAGE = "https://example.com/survey-items/blue-hoodie.jpg"


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not passed else ""))
    if not passed:
        FAILURES.append(label)
    return passed


POOL: list[dict[str, Any]] = [
    {
        "id": "interest",
        "type": "yes_no",
        "label": "Are you interested in this item?",
        "required": True,
        "options": ["Yes", "No"],
    },
    {
        "id": "size_fit",
        "type": "single_choice",
        "label": "How is the size?",
        "required": False,
        "options": ["Too small", "Just right", "Too big"],
    },
    {
        "id": "attention_1",
        "type": "scale",
        "label": "How much did this catch your eye?",
        "required": False,
        "options": [],
    },
    {
        "id": "notes",
        "type": "text",
        "label": "Any other feedback?",
        "required": False,
        "options": [],
    },
]


def items_payload(items: dict[str, int]) -> list[dict[str, Any]]:
    """The two items, in survey order, ticking different questions.

    The hoodie asks everything; the cap asks the interest question and its
    notes, and never the size or the scale - that is the whole point of a
    per-item question set.
    """
    return [
        {
            "id": items.get("hoodie"),
            "item_name": "Blue Hoodie",
            "cost": "$29.00",
            "image_url": ITEM_IMAGE,
            "item_number": "10042",
            "item_description": "Soft fleece, unisex fit.",
            "question_ids": ["interest", "size_fit", "attention_1", "notes"],
        },
        {
            "id": items.get("cap"),
            "item_name": "Red Cap",
            "cost": "$18.50",
            "image_url": "",
            "item_number": "20001",
            "item_description": "",
            "question_ids": ["interest", "notes"],
        },
    ]


def results(client: TestClient, headers: dict[str, str], period_id: int) -> dict[str, Any]:
    response = client.get("/api/admin/survey-results", headers=headers, params={"period_id": period_id})
    response.raise_for_status()
    return response.json()


def item_named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    match = next((item for item in payload["items"] if item["item_name"] == name), None)
    if match is None:
        raise SystemExit(f"no item named {name} in {payload}")
    return match


def tally_of(item: dict[str, Any], question_id: str) -> dict[str, Any]:
    match = next((tally for tally in item["questions"] if tally["question_id"] == question_id), None)
    if match is None:
        raise SystemExit(f"item {item['item_name']} has no tally for {question_id}")
    return match


def choice_count(tally: dict[str, Any], option: str) -> int:
    match = next((choice for choice in tally["choices"] if choice["option"] == option), None)
    return int(match["count"]) if match else 0


def rows_of(period_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = execute(
            conn,
            "SELECT * FROM survey_answers WHERE response_id IN"
            " (SELECT id FROM survey_responses WHERE survey_period_id = ?)",
            (period_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
    parser.add_argument("--keep", action="store_true", help="keep the survey and responses this script creates")
    args = parser.parse_args()

    init_db()
    client = TestClient(app)

    login = client.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if login.status_code != 200:
        raise SystemExit(f"Login failed ({login.status_code}). Set ADMIN_EMAIL / ADMIN_PASSWORD.")
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    period_id: int | None = None
    extra_periods: list[int] = []
    try:
        # ------------------------------------------------------------------
        print("\n1. The builder creates a survey with two items over one pool")
        created = client.post(
            "/api/admin/survey-periods",
            headers=headers,
            json={
                "name": "check-survey-items",
                "open_immediately": True,
                "questions": POOL,
                "items": items_payload({}),
            },
        )
        check("create -> 201", created.status_code == 201, f"{created.status_code} {created.text[:200]}")
        if created.status_code != 201:
            raise SystemExit("cannot continue without a created survey")
        period = created.json()["period"]
        period_id = int(period["id"])
        token = str(period.get("survey_token") or "")
        check("the survey is open", period["status"] == "open", str(period["status"]))
        check("it carries a private token", len(token) >= 24, token[:8])

        detail = client.get(f"/api/admin/survey-periods/{period_id}", headers=headers).json()
        hoodie = item_named(detail, "Blue Hoodie")
        cap = item_named(detail, "Red Cap")
        check("two items come back in survey order",
              [item["item_name"] for item in detail["items"]] == ["Blue Hoodie", "Red Cap"],
              str([item["item_name"] for item in detail["items"]]))
        check("the hoodie ticks all four pool questions",
              set(hoodie["question_ids"]) == {"interest", "size_fit", "attention_1", "notes"},
              str(hoodie["question_ids"]))
        check("the cap ticks only two of them",
              set(cap["question_ids"]) == {"interest", "notes"}, str(cap["question_ids"]))
        check("the two items were given different question sets",
              set(hoodie["question_ids"]) != set(cap["question_ids"]))
        check("item fields come back whole",
              hoodie["cost"] == "$29.00" and hoodie["item_number"] == "10042"
              and hoodie["item_description"] == "Soft fleece, unisex fit."
              and hoodie["image_url"] == ITEM_IMAGE,
              str({k: hoodie[k] for k in ("cost", "item_number", "image_url")}))
        check("no item depends on the catalog",
              hoodie.get("catalog_item_id") is None and cap.get("catalog_item_id") is None,
              f"{hoodie.get('catalog_item_id')}/{cap.get('catalog_item_id')}")
        hoodie_id = int(hoodie["id"])
        cap_id = int(cap["id"])
        check("each item has its own stable row id",
              hoodie_id > 0 and cap_id > 0 and hoodie_id != cap_id, f"{hoodie_id}/{cap_id}")

        # ------------------------------------------------------------------
        print("\n2. The public page gets one card per item, each with its own questions")
        public = client.get(
            f"/api/public/{args.company}/survey-items",
            params={"surface": "survey", "t": token},
        )
        check("token read -> 200", public.status_code == 200, str(public.status_code))
        payload = public.json()
        check("survey_open is true", payload.get("survey_open") is True, str(payload)[:120])
        check("the page carries both items in order",
              [item["item_name"] for item in payload["items"]] == ["Blue Hoodie", "Red Cap"],
              str([item["item_name"] for item in payload["items"]]))
        public_hoodie = item_named(payload, "Blue Hoodie")
        public_cap = item_named(payload, "Red Cap")
        check("the card carries image, cost, number and description",
              public_hoodie["cost"] == "$29.00" and public_hoodie["item_number"] == "10042"
              and public_hoodie["image_url"] == ITEM_IMAGE
              and public_hoodie["item_description"] == "Soft fleece, unisex fit.",
              str({k: public_hoodie.get(k) for k in ("cost", "item_number", "image_url")}))
        check("the hoodie asks its four questions",
              [q["id"] for q in public_hoodie["questions"]]
              == ["interest", "size_fit", "attention_1", "notes"],
              str([q["id"] for q in public_hoodie["questions"]]))
        check("the cap asks only its two",
              [q["id"] for q in public_cap["questions"]] == ["interest", "notes"],
              str([q["id"] for q in public_cap["questions"]]))
        check("the public item id is the survey item's own id",
              int(public_hoodie["id"]) == hoodie_id, f"{public_hoodie['id']} != {hoodie_id}")
        check("the pool is on the payload for the builder's reuse",
              [q["id"] for q in payload["questions"]] == ["interest", "size_fit", "attention_1", "notes"],
              str([q["id"] for q in payload.get("questions", [])]))
        check("the public payload carries no token",
              "survey_token" not in public_hoodie and "survey_token" not in (payload.get("period") or {}),
              str(list(payload.keys())))

        static = client.get(f"/api/public/{args.company}/survey-items", params={"surface": "survey"})
        check("the static link (no token) opens the open survey",
              static.status_code == 200 and len(static.json().get("items", [])) == 2,
              str(static.status_code))

        # ------------------------------------------------------------------
        print("\n3. One submission covers every item, validated per item")
        missing_required = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={"email": "missing@example.com", "answers": {str(hoodie_id): {"size_fit": "Just right"}}},
        )
        check("a missing required answer is refused", missing_required.status_code == 400,
              f"{missing_required.status_code} {missing_required.text[:140]}")
        check("the 400 names the question",
              "Are you interested in this item?" in str(missing_required.json().get("detail", "")),
              str(missing_required.json())[:160])

        not_asked = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={"email": "missing@example.com",
                  "answers": {str(cap_id): {"interest": "Yes", "size_fit": "Too big"}}},
        )
        check("an answer to a question the item does not ask is refused",
              not_asked.status_code == 400, f"{not_asked.status_code} {not_asked.text[:140]}")

        # Through the static link on purpose: the buyer's bookmark has no token.
        first = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={
                "email": "a@example.com",
                "answers": {
                    str(hoodie_id): {
                        "interest": "Yes",
                        "size_fit": "Just right",
                        "attention_1": 5,
                        "notes": "Love the fleece.",
                    },
                    str(cap_id): {"interest": "Yes", "notes": "Nice cap."},
                },
            },
        )
        check("one submit answers both items -> 200", first.status_code == 200,
              f"{first.status_code} {first.text[:200]}")
        if first.status_code == 200:
            body = first.json()["response"]
            check("the response reports both items answered", body.get("item_count") == 2, str(body.get("item_count")))
            check("and every answer written", body.get("answer_count") == 6, str(body.get("answer_count")))

        second = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={
                "email": "b@example.com",
                "answers": {
                    str(hoodie_id): {"interest": "No", "size_fit": "Too small", "attention_1": 2},
                    str(cap_id): {"interest": "No"},
                },
            },
        )
        check("a second respondent -> 200", second.status_code == 200, str(second.text[:140]))

        anonymous = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={"answers": {str(hoodie_id): {"interest": "Yes", "attention_1": 3}}},
        )
        check("an anonymous submit (no email) -> 200", anonymous.status_code == 200, str(anonymous.text[:140]))

        stored = rows_of(period_id)
        check("answers are one row per submission x item x question", len(stored) == 12, f"{len(stored)} rows")
        check("a row names its item and its question",
              all(row["survey_period_item_id"] and row["question_id"] for row in stored))
        check("the scale answer is stored as a number",
              all(row["answer_number"] == 5 for row in stored
                  if row["question_id"] == "attention_1" and row["answer_text"] == "5"),
              str([row["answer_number"] for row in stored if row["question_id"] == "attention_1"]))

        # ------------------------------------------------------------------
        print("\n4. Results read back per item")
        report = results(client, headers, period_id)
        check("the summary counts three submissions", report["summary"]["submissions"] == 3,
              str(report["summary"]))
        check("two distinct respondents are named", report["summary"]["unique_respondents"] == 2,
              str(report["summary"]))
        hoodie_result = item_named(report, "Blue Hoodie")
        cap_result = item_named(report, "Red Cap")
        check("the hoodie has three responses", hoodie_result["response_count"] == 3,
              str(hoodie_result["response_count"]))
        check("the cap has two responses", cap_result["response_count"] == 2,
              str(cap_result["response_count"]))
        check("interest is per item: hoodie 2 yes / 1 no",
              (choice_count(tally_of(hoodie_result, "interest"), "Yes"),
               choice_count(tally_of(hoodie_result, "interest"), "No")) == (2, 1),
              str(tally_of(hoodie_result, "interest")["choices"]))
        check("interest is per item: cap 1 yes / 1 no",
              (choice_count(tally_of(cap_result, "interest"), "Yes"),
               choice_count(tally_of(cap_result, "interest"), "No")) == (1, 1),
              str(tally_of(cap_result, "interest")["choices"]))
        check("the hoodie's size choices are tallied",
              (choice_count(tally_of(hoodie_result, "size_fit"), "Just right"),
               choice_count(tally_of(hoodie_result, "size_fit"), "Too small")) == (1, 1),
              str(tally_of(hoodie_result, "size_fit")["choices"]))
        check("the cap has no size tally at all (it never asked)",
              all(tally["question_id"] != "size_fit" for tally in cap_result["questions"]),
              str([tally["question_id"] for tally in cap_result["questions"]]))
        check("the hoodie's scale averages to 3.33",
              tally_of(hoodie_result, "attention_1")["average"] == 3.33,
              str(tally_of(hoodie_result, "attention_1")["average"]))
        check("the cap has no scale average",
              all(tally["question_id"] != "attention_1" for tally in cap_result["questions"]))
        hoodie_notes = tally_of(hoodie_result, "notes")
        check("text answers are listed with their respondent",
              [answer["answer"] for answer in hoodie_notes["answers"]] == ["Love the fleece."],
              str(hoodie_notes["answers"]))
        check("and they carry the email",
              hoodie_notes["answers"] and hoodie_notes["answers"][0]["email"] == "a@example.com",
              str(hoodie_notes["answers"])[:120])
        cap_notes = tally_of(cap_result, "notes")
        check("the cap's own notes question collects its own answers",
              [answer["answer"] for answer in cap_notes["answers"]] == ["Nice cap."],
              str(cap_notes["answers"]))

        # ------------------------------------------------------------------
        print("\n5. The export is one row per respondent x item x question")
        export = client.get(
            "/api/admin/survey-responses/export",
            headers=headers,
            params={"period_id": period_id},
        )
        check("export returns a workbook", export.status_code == 200 and export.content[:2] == b"PK",
              str(export.status_code))
        workbook = load_workbook(io.BytesIO(export.content))
        sheet = workbook.active
        headers_row = [cell.value for cell in sheet[1]]
        for column in ("Submitted At", "Email", "Item Name", "Item Number", "Cost",
                       "Item Description", "Item Image URL", "Question", "Answer"):
            check(f"export column: {column}", column in headers_row, str(headers_row))
        rows = list(sheet.iter_rows(min_row=2, values_only=True))
        check("one row per answer (12)", len(rows) == 12, f"{len(rows)} rows")
        column = {name: index for index, name in enumerate(headers_row)}

        def cell(row: tuple[Any, ...], name: str) -> Any:
            return row[column[name]]

        hoodie_interest_rows = [
            row for row in rows
            if cell(row, "Item Name") == "Blue Hoodie"
            and cell(row, "Question") == "Are you interested in this item?"
        ]
        check("every hoodie interest answer is its own row", len(hoodie_interest_rows) == 3,
              str(len(hoodie_interest_rows)))
        check("each row prints the item image URL",
              all(cell(row, "Item Image URL") == ITEM_IMAGE for row in hoodie_interest_rows))
        check("each row prints the item number",
              all(cell(row, "Item Number") == "10042" for row in hoodie_interest_rows))
        check("each row prints the item cost",
              all(cell(row, "Cost") == "$29.00" for row in hoodie_interest_rows))
        check("each row prints the description",
              all(cell(row, "Item Description") == "Soft fleece, unisex fit." for row in hoodie_interest_rows))
        check("the answer and the respondent are on the row",
              sorted(str(cell(row, "Answer")) for row in hoodie_interest_rows) == ["No", "Yes", "Yes"]
              and "b@example.com" in str([cell(row, "Email") for row in hoodie_interest_rows]),
              str([[cell(row, "Email"), cell(row, "Answer")] for row in hoodie_interest_rows]))
        scale_rows = [row for row in rows if cell(row, "Question") == "How much did this catch your eye?"]
        check("the scale answers export as numbers",
              sorted(str(cell(row, "Answer")) for row in scale_rows) == ["2", "3", "5"],
              str([cell(row, "Answer") for row in scale_rows]))
        cap_rows = [row for row in rows if cell(row, "Item Name") == "Red Cap"]
        check("the cap's rows name the cap", len(cap_rows) == 3, str(len(cap_rows)))
        check("no row prints a catalog or NetSuite id",
              not any("catalog" in name.lower() or "netsuite" in name.lower() for name in headers_row),
              str(headers_row))

        # ------------------------------------------------------------------
        print("\n6. Editing keeps ids: relabel, reorder, untick")
        resaved = client.put(
            f"/api/admin/survey-periods/{period_id}/layout",
            headers=headers,
            json={
                "name": "check-survey-items renamed",
                "questions": [
                    {**POOL[0], "label": "Do you want this item?"},
                    POOL[1],
                    POOL[2],
                    POOL[3],
                ],
                "items": [
                    # The cap first, the hoodie second, both keeping their ids,
                    # and the hoodie no longer asking for the size.
                    {**items_payload({"hoodie": hoodie_id, "cap": cap_id})[1]},
                    {
                        **items_payload({"hoodie": hoodie_id, "cap": cap_id})[0],
                        "question_ids": ["interest", "attention_1", "notes"],
                    },
                ],
            },
        )
        check("the edit saves -> 200", resaved.status_code == 200, f"{resaved.status_code} {resaved.text[:200]}")
        after = client.get(f"/api/admin/survey-periods/{period_id}", headers=headers).json()
        check("the name changed", after["period"]["name"] == "check-survey-items renamed", after["period"]["name"])
        check("the order follows the dialog",
              [item["item_name"] for item in after["items"]] == ["Red Cap", "Blue Hoodie"],
              str([item["item_name"] for item in after["items"]]))
        check("the item ids did not change",
              [int(item["id"]) for item in after["items"]] == [cap_id, hoodie_id],
              str([item["id"] for item in after["items"]]))
        relabelled = results(client, headers, period_id)
        hoodie_after = item_named(relabelled, "Blue Hoodie")
        cap_after = item_named(relabelled, "Red Cap")
        check("the hoodie keeps its three responses after the edit",
              hoodie_after["response_count"] == 3, str(hoodie_after["response_count"]))
        check("the cap keeps its two", cap_after["response_count"] == 2, str(cap_after["response_count"]))
        check("the relabelled question still holds its answers",
              (choice_count(tally_of(hoodie_after, "interest"), "Yes"),
               choice_count(tally_of(hoodie_after, "interest"), "No")) == (2, 1),
              str(tally_of(hoodie_after, "interest")))
        check("the tally shows the new label",
              tally_of(hoodie_after, "interest")["label"] == "Do you want this item?",
              tally_of(hoodie_after, "interest")["label"])
        check("the unticked question is no longer tallied for that item",
              all(tally["question_id"] != "size_fit" for tally in hoodie_after["questions"]),
              str([tally["question_id"] for tally in hoodie_after["questions"]]))
        check("no answer row was renumbered or orphaned", len(rows_of(period_id)) == 12,
              str(len(rows_of(period_id))))
        check("the answer rows still name a live item",
              all(row["survey_period_item_id"] in {hoodie_id, cap_id} for row in rows_of(period_id)))

        # ------------------------------------------------------------------
        print("\n7. One submission per respondent")
        resubmit = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={"email": "a@example.com", "answers": {str(cap_id): {"interest": "No"}}},
        )
        check("a second submit by the same email -> 200", resubmit.status_code == 200,
              str(resubmit.text[:160]))
        after_resubmit = results(client, headers, period_id)
        check("no fourth submission was created", after_resubmit["summary"]["submissions"] == 3,
              str(after_resubmit["summary"]))
        cap_latest = item_named(after_resubmit, "Red Cap")
        check("the cap's answer was replaced, not added to",
              (choice_count(tally_of(cap_latest, "interest"), "Yes"),
               choice_count(tally_of(cap_latest, "interest"), "No")) == (0, 2),
              str(tally_of(cap_latest, "interest")["choices"]))
        hoodie_latest = item_named(after_resubmit, "Blue Hoodie")
        check("the same shopper's hoodie answers are untouched",
              (choice_count(tally_of(hoodie_latest, "interest"), "Yes"),
               choice_count(tally_of(hoodie_latest, "interest"), "No")) == (2, 1),
              str(tally_of(hoodie_latest, "interest")["choices"]))

        # ------------------------------------------------------------------
        print("\n8. A question added to the pool reaches every item")

        def create_survey(name: str, item_specs: list[dict[str, Any]]) -> tuple[int, list[int]]:
            response = client.post(
                "/api/admin/survey-periods",
                headers=headers,
                json={
                    "name": name,
                    "open_immediately": True,
                    "questions": POOL,
                    "items": item_specs,
                },
            )
            if response.status_code != 201:
                raise SystemExit(f"could not create {name}: {response.status_code} {response.text[:240]}")
            new_id = int(response.json()["period"]["id"])
            extra_periods.append(new_id)
            detail = client.get(f"/api/admin/survey-periods/{new_id}", headers=headers).json()
            return new_id, [
                int(item["id"]) for item in detail["items"]
                if item["item_name"] in {spec["item_name"] for spec in item_specs}
            ]

        added_period, added_items = create_survey("check-added-question", [
            # The mat asks three of the four questions; the presenter asks one.
            {"item_name": "Ask Most", "cost": "$7.45", "item_number": "30001",
             "question_ids": ["interest", "attention_1", "notes"]},
            {"item_name": "Ask One", "cost": "$6.00", "item_number": "30002",
             "question_ids": ["interest"]},
        ])
        most_id, one_id = added_items
        added_detail = client.get(f"/api/admin/survey-periods/{added_period}", headers=headers).json()
        check("the survey starts with two items asking different sets",
              [len(item["question_ids"]) for item in added_detail["items"]] == [3, 1],
              str([len(item["question_ids"]) for item in added_detail["items"]]))

        # The stale-client save: a new pool question, and items that know
        # nothing about it (a dialog opened before it existed).
        new_question = {
            "id": "colour_fit",
            "type": "single_choice",
            "label": "How is the colour?",
            "required": False,
            "options": ["Too light", "Just right", "Too dark"],
        }
        stale = client.put(
            f"/api/admin/survey-periods/{added_period}/layout",
            headers=headers,
            json={
                "name": "check-added-question",
                "questions": POOL + [new_question],
                "items": [
                    {"id": most_id, "item_name": "Ask Most", "cost": "$7.45", "item_number": "30001",
                     "image_url": "", "item_description": "",
                     "question_ids": ["interest", "attention_1", "notes"]},
                    {"id": one_id, "item_name": "Ask One", "cost": "$6.00", "item_number": "30002",
                     "image_url": "", "item_description": "", "question_ids": ["interest"]},
                ],
            },
        )
        check("the save with a new question -> 200", stale.status_code == 200,
              f"{stale.status_code} {stale.text[:200]}")
        after_add = client.get(f"/api/admin/survey-periods/{added_period}", headers=headers).json()
        after_by_id = {int(item["id"]): item for item in after_add["items"]}
        check("every item now asks the new question",
              all("colour_fit" in after_by_id[item_id]["question_ids"] for item_id in (most_id, one_id)),
              str({item_id: after_by_id[item_id]["question_ids"] for item_id in (most_id, one_id)}))
        check("the item that asked three now asks four",
              len(after_by_id[most_id]["question_ids"]) == 4,
              str(after_by_id[most_id]["question_ids"]))
        check("the item that asked one now asks two",
              len(after_by_id[one_id]["question_ids"]) == 2,
              str(after_by_id[one_id]["question_ids"]))
        check("a question unticked before the add stays unticked",
              "size_fit" not in after_by_id[most_id]["question_ids"]
              and "size_fit" not in after_by_id[one_id]["question_ids"],
              str({item_id: after_by_id[item_id]["question_ids"] for item_id in (most_id, one_id)}))

        added_public = client.get(
            f"/api/public/{args.company}/survey-items", params={"surface": "survey"}
        ).json()
        public_most = item_named(added_public, "Ask Most")
        public_one = item_named(added_public, "Ask One")
        check("the public page asks the new question on every item",
              all("colour_fit" in [question["id"] for question in item["questions"]]
                  for item in (public_most, public_one)),
              str({item["item_name"]: [q["id"] for q in item["questions"]] for item in (public_most, public_one)}))
        check("the public page still skips the unticked question",
              "size_fit" not in [question["id"] for question in public_most["questions"]],
              str([question["id"] for question in public_most["questions"]]))

        answered = client.post(
            f"/api/public/{args.company}/survey-responses",
            json={
                "email": "added@example.com",
                "answers": {
                    str(most_id): {"interest": "Yes", "colour_fit": "Just right"},
                    str(one_id): {"interest": "No", "colour_fit": "Too dark"},
                },
            },
        )
        check("the new question can be answered on both items", answered.status_code == 200,
              f"{answered.status_code} {answered.text[:160]}")
        added_results = results(client, headers, added_period)
        most_result = item_named(added_results, "Ask Most")
        one_result = item_named(added_results, "Ask One")
        check("the new question is tallied on every item",
              choice_count(tally_of(most_result, "colour_fit"), "Just right") == 1
              and choice_count(tally_of(one_result, "colour_fit"), "Too dark") == 1,
              f"{tally_of(most_result, 'colour_fit')['choices']} / {tally_of(one_result, 'colour_fit')['choices']}")

        # The Items header action: every item ticked for the whole pool. It is
        # the same save the button sends.
        whole_pool = [question["id"] for question in after_add["questions"]]
        bulk = client.put(
            f"/api/admin/survey-periods/{added_period}/layout",
            headers=headers,
            json={
                "name": "check-added-question",
                "questions": POOL + [new_question],
                "items": [
                    {"id": most_id, "item_name": "Ask Most", "cost": "$7.45", "item_number": "30001",
                     "image_url": "", "item_description": "", "question_ids": whole_pool},
                    {"id": one_id, "item_name": "Ask One", "cost": "$6.00", "item_number": "30002",
                     "image_url": "", "item_description": "", "question_ids": whole_pool},
                ],
            },
        )
        check("the all-items save -> 200", bulk.status_code == 200, f"{bulk.status_code} {bulk.text[:160]}")
        bulked = client.get(f"/api/admin/survey-periods/{added_period}", headers=headers).json()
        check("every item asks the whole pool after the bulk save",
              all(sorted(item["question_ids"]) == sorted(whole_pool) for item in bulked["items"]),
              str([len(item["question_ids"]) for item in bulked["items"]]))
        bulked_public = client.get(
            f"/api/public/{args.company}/survey-items", params={"surface": "survey"}
        ).json()
        check("the public page asks the whole pool on every item",
              all(len(item["questions"]) == len(whole_pool) for item in bulked_public["items"]),
              str([len(item["questions"]) for item in bulked_public["items"]]))

        # And the reverse: a save that leaves a question out for one item keeps
        # it out. Only *new* questions are added on save.
        trim = client.put(
            f"/api/admin/survey-periods/{added_period}/layout",
            headers=headers,
            json={
                "name": "check-added-question",
                "questions": POOL + [new_question],
                "items": [
                    {"id": most_id, "item_name": "Ask Most", "cost": "$7.45", "item_number": "30001",
                     "image_url": "", "item_description": "", "question_ids": ["interest"]},
                    {"id": one_id, "item_name": "Ask One", "cost": "$6.00", "item_number": "30002",
                     "image_url": "", "item_description": "", "question_ids": whole_pool},
                ],
            },
        )
        check("a save that drops a tick -> 200", trim.status_code == 200, f"{trim.status_code} {trim.text[:160]}")
        trimmed = client.get(f"/api/admin/survey-periods/{added_period}", headers=headers).json()
        trimmed_by_id = {int(item["id"]): item for item in trimmed["items"]}
        check("an existing question is not re-ticked by a save",
              trimmed_by_id[most_id]["question_ids"] == ["interest"],
              str(trimmed_by_id[most_id]["question_ids"]))
        check("the other item keeps its whole pool",
              len(trimmed_by_id[one_id]["question_ids"]) == len(whole_pool),
              str(trimmed_by_id[one_id]["question_ids"]))

        # ------------------------------------------------------------------
        print("\n9. The migration is idempotent")
        item_columns = column_names("survey_period_items")
        answer_columns = column_names("survey_answers")
        tick_columns = column_names("survey_period_item_questions")
        ticks_before = len(rows_of(period_id))
        ticks_rows_before = client.get(f"/api/admin/survey-periods/{period_id}", headers=headers).json()["items"]
        init_db()
        init_db()
        ticks_rows_after = client.get(f"/api/admin/survey-periods/{period_id}", headers=headers).json()["items"]
        check("cost and item_number are single columns",
              item_columns.count("cost") == 1 and item_columns.count("item_number") == 1,
              str(item_columns))
        check("the catalog id may be empty",
              "catalog_item_id" in item_columns, str(item_columns[:6]))
        check("the tick table exists", "question_id" in tick_columns, str(tick_columns))
        check("the answer table has its value columns",
              {"answer_text", "answer_options_json", "answer_number"} <= set(answer_columns),
              str(answer_columns))
        check("a re-run keeps every item",
              [item["id"] for item in ticks_rows_after] == [item["id"] for item in ticks_rows_before],
              str([item["id"] for item in ticks_rows_after]))
        check("a re-run writes no answer rows", len(rows_of(period_id)) == ticks_before,
              f"{len(rows_of(period_id))} rows")
        check("a re-run keeps each item's ticks",
              [item["question_ids"] for item in ticks_rows_after]
              == [item["question_ids"] for item in ticks_rows_before],
              str([item["question_ids"] for item in ticks_rows_after]))
        with connect() as conn:
            duplicate_ticks = execute(
                conn,
                """
                SELECT COUNT(*) AS n FROM (
                    SELECT survey_period_item_id, question_id FROM survey_period_item_questions
                    GROUP BY survey_period_item_id, question_id HAVING COUNT(*) > 1
                ) doubled
                """,
            ).fetchone()
        check("no question is ticked twice on one item", int(duplicate_ticks["n"]) == 0,
              str(duplicate_ticks["n"]))
    finally:
        if period_id is not None and not args.keep:
            with connect() as conn:
                execute(
                    conn,
                    "DELETE FROM survey_answers WHERE response_id IN"
                    " (SELECT id FROM survey_responses WHERE survey_period_id = ?)",
                    (period_id,),
                )
                execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (period_id,))
                execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (period_id,))
                execute(conn, "DELETE FROM survey_periods WHERE id = ?", (period_id,))
                for extra_id in extra_periods:
                    execute(
                        conn,
                        "DELETE FROM survey_answers WHERE response_id IN"
                        " (SELECT id FROM survey_responses WHERE survey_period_id = ?)",
                        (extra_id,),
                    )
                    execute(conn, "DELETE FROM survey_responses WHERE survey_period_id = ?", (extra_id,))
                    execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id = ?", (extra_id,))
                    execute(conn, "DELETE FROM survey_periods WHERE id = ?", (extra_id,))
            print("\n(created surveys and their responses removed)")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}): " + "; ".join(FAILURES))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
