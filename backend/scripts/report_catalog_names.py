"""Which column holds an item's real name? Answer it from data, once.

    python backend/scripts/report_catalog_names.py
    python backend/scripts/report_catalog_names.py --rows 40 --json

Read-only: no database, no writes, no migration. It prints sample rows of
facet-bearing items — internal id, itemid, displayname, description,
storedisplayname (if the account has it), the four facet ids with their labels,
Multi-Order Sort, the category and ``isinactive`` — and counts how often each
candidate column is usable.

This is commit 1 of the catalog sync, and it is a report on purpose: the name
column has to be chosen from what the account actually holds. On this account
``displayname`` is the numeric code for most facet-bearing items, which is why
a sync that trusted it would fill the catalog with numbers.

Exit code 0 when a usable name was found on every sampled row, 1 when some row
would fall back to the ``Item <id>`` placeholder.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from catalog_mapping import NAME_FIELD, is_numeric_code, resolve_name  # noqa: E402
from netsuite import (  # noqa: E402
    CATEGORY_COLUMN,
    FACET_COLUMNS,
    SORT_COLUMN,
    SourceError,
    client_from_env,
    facet_value_ids,
    fetch_labels,
)

SAMPLE_FIELDS = ("id", "itemid", "displayname", "storedisplayname", "description", "isinactive")


def _columns(with_store_name: bool) -> str:
    columns = [
        "i.id AS id",
        "i.itemid AS itemid",
        "i.displayname AS displayname",
        "i.description AS description",
        "i.isinactive AS isinactive",
    ]
    if with_store_name:
        columns.insert(3, "i.storedisplayname AS storedisplayname")
    columns.extend(f"i.{column} AS {column}" for column in FACET_COLUMNS.values())
    columns.append(f"i.{SORT_COLUMN} AS {SORT_COLUMN}")
    columns.append(f"i.{CATEGORY_COLUMN} AS {CATEGORY_COLUMN}")
    return ", ".join(columns)


def fetch_rows(client: Any, rows: int) -> tuple[list[dict[str, Any]], bool]:
    """A sample of active facet-bearing items; the second value says the column exists."""
    with_store_name = True
    while True:
        statement = (
            f"SELECT {_columns(with_store_name)} FROM item i"
            " WHERE i.isinactive = 'F' AND ("
            + " OR ".join(f"i.{column} IS NOT NULL" for column in FACET_COLUMNS.values())
            + f") ORDER BY i.id FETCH FIRST {int(rows)} ROWS ONLY"
        )
        try:
            page, _ = client.query(statement, limit=rows)
            return page, with_store_name
        except SourceError as error:
            if with_store_name and "storedisplayname" in str(error).lower():
                with_store_name = False
                continue
            raise


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "rows": len(rows),
        "displayname_blank": 0,
        "displayname_numeric": 0,
        "itemid_numeric": 0,
        "storedisplayname_blank": 0,
        "storedisplayname_present": 0,
        "usable_name": 0,
    }
    for row in rows:
        displayname = str(row.get("displayname") or "").strip()
        itemid = str(row.get("itemid") or "").strip()
        stored = str(row.get("storedisplayname") or "").strip()
        if not displayname:
            totals["displayname_blank"] += 1
        elif is_numeric_code(displayname):
            totals["displayname_numeric"] += 1
        if itemid and is_numeric_code(itemid):
            totals["itemid_numeric"] += 1
        if "storedisplayname" in row:
            if stored:
                totals["storedisplayname_present"] += 1
            else:
                totals["storedisplayname_blank"] += 1
        name, _ = resolve_name(row)
        if name:
            totals["usable_name"] += 1
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=20, help="sample size (default 20)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    client = client_from_env()
    rows, has_store_name = fetch_rows(client, max(1, args.rows))
    labels = fetch_labels(client, facet_value_ids(rows))

    def facet_text(row: dict[str, Any]) -> str:
        parts = []
        for facet, column in FACET_COLUMNS.items():
            value_id = str(row.get(column) or "").strip()
            if not value_id:
                continue
            label = labels.label(facet, value_id)
            parts.append(f"{facet}={label or f'(unresolved id {value_id})'}")
        return "; ".join(parts) or "(no facet)"

    report = {
        "name_field": NAME_FIELD,
        "storedisplayname_present": has_store_name,
        "summary": summarise(rows),
        "rows": [
            {
                **{field: str(row.get(field) or "") for field in SAMPLE_FIELDS if field in row},
                "chosen_name": resolve_name(row)[0],
                "name_source": resolve_name(row)[1] or "none",
                "facets": facet_text(row),
                "multi_order_sort": str(row.get(SORT_COLUMN) or ""),
                "category": str(row.get(CATEGORY_COLUMN) or ""),
            }
            for row in rows
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"name field: {NAME_FIELD}")
        print(f"storedisplayname in this account: {'yes' if has_store_name else 'no'}")
        header = (
            f"{'internalid':>10}  {'itemid':<28} {'displayname':<20} "
            f"{'storedisplayname':<44} {'chosen name':<40} facets"
        )
        print(header)
        print("-" * len(header))
        for entry in report["rows"]:
            print(
                f"{entry.get('id', ''):>10}  {entry.get('itemid', '')[:28]:<28} "
                f"{entry.get('displayname', '')[:20]:<20} "
                f"{entry.get('storedisplayname', '')[:44]:<44} "
                f"{entry['chosen_name'][:40]:<40} {entry['facets']}"
            )
            print(
                f"{'':>10}  sort={entry['multi_order_sort'] or '-'} "
                f"category={entry['category'] or '-'} inactive={entry.get('isinactive', '')} "
                f"name from {entry['name_source']}"
            )
        print()
        summary = report["summary"]
        print(
            f"{summary['rows']} rows: storedisplayname present {summary['storedisplayname_present']},"
            f" blank {summary['storedisplayname_blank']}; displayname numeric"
            f" {summary['displayname_numeric']} (blank {summary['displayname_blank']});"
            f" itemid numeric {summary['itemid_numeric']}; usable name"
            f" {summary['usable_name']}/{summary['rows']}"
        )

    return 0 if report["summary"]["usable_name"] == report["summary"]["rows"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SourceError as error:
        print(f"NetSuite read failed: {error}", file=sys.stderr)
        raise SystemExit(2)
