"""Acceptance check for the NetSuite catalog sync. Offline, against a fake client.

    python backend/scripts/check_catalog_sync.py
    SMARTBUY_DB_PATH=/tmp/scratch.db python backend/scripts/check_catalog_sync.py
    DATABASE_URL=postgresql://... python backend/scripts/check_catalog_sync.py
    python backend/scripts/check_catalog_sync.py --live     # plus one real read, if configured

On SQLite it writes to its own throwaway database unless ``SMARTBUY_DB_PATH``
says otherwise, so running it never touches the configured one. What it creates
is deleted at the start and at the end.

What it proves:

0.  the name column is ``storedisplayname`` and the ladder around it holds
1.  a first run into an empty catalog creates rows with both book flags 0, no
    price, no image, and no period rows — including an item with no facet that
    a buy-in period points at
2.  a second identical run writes nothing — every ``updated_at`` unchanged,
    and a boot leaves the catalog alone
3.  a changed name or description updates; a changed price or image changes
    nothing; a blank NetSuite description leaves the stored one alone
4.  a human edit survives a differing sync value, is reported overridden, and
    "Reset to NetSuite" writes the snapshot back
5.  an inactive item is retired once with an audit reason, stays retired, a
    human restore is retired again; facets gone with no buy-in row retires,
    facets gone but still in a buy-in row stays, and losing both retires
6.  hand-created and from-tracker rows are untouched
7.  a dry run leaves the database byte-identical
8.  a client that dies mid-scan writes nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCRATCH_DB = str(Path(tempfile.gettempdir()) / "smartbuy-catalog-sync-check.db")
_OWNS_SCRATCH = not os.environ.get("DATABASE_URL") and not os.environ.get("SMARTBUY_DB_PATH")
if _OWNS_SCRATCH:
    os.environ["SMARTBUY_DB_PATH"] = _SCRATCH_DB

sys.path.append(str(Path(__file__).resolve().parents[1]))

import catalog_sync  # noqa: E402
from catalog_mapping import item_number, map_item, resolve_name  # noqa: E402
from config import IS_POSTGRES  # noqa: E402
from database import connect, execute, utc_now  # noqa: E402
from init_db import init_db  # noqa: E402
from netsuite import (  # noqa: E402
    CATEGORY_COLUMN,
    FACET_COLUMNS,
    SORT_COLUMN,
    FacetResolution,
    SourceError,
)

FAILURES: list[str] = []
SLUG = "check-catalog-sync"
PREFIX = "check-sync-"


def check(label: str, passed: bool, detail: Any = "") -> None:
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + ("" if passed else f"   [{detail}]"))
    if not passed:
        FAILURES.append(f"{label} :: {detail}")


# ---------------------------------------------------------------------------
# A fake account
# ---------------------------------------------------------------------------

def item(
    internal_id: int,
    *,
    name: str = "",
    itemid: str = "",
    displayname: str = "",
    description: str = "",
    brand: str = "",
    channel: str = "",
    program: str = "",
    product_type: str = "",
    order: str = "",
    category: str = "",
    inactive: bool = False,
    price: str = "0.00",
    image: str = "",
) -> dict[str, Any]:
    return {
        "id": str(internal_id),
        "itemid": itemid or f"3148_{internal_id}",
        "displayname": displayname,
        "storedisplayname": name,
        "description": description,
        "isinactive": "T" if inactive else "F",
        "custitem_facet_brand": brand or None,
        "custitem_facet_channel": channel or None,
        "custitem_facet_keyprogram": program or None,
        "custitem_facet_producttype": product_type or None,
        # Columns the map must never read, so a leaked write would fail a test.
        "baseprice": price,
        "image": image,
        CATEGORY_COLUMN: category or None,
        SORT_COLUMN: order or None,
    }


class FakeSuiteQL:
    """Answers the two shapes the sync asks for: item scans and facet labels."""

    def __init__(
        self,
        items: list[dict[str, Any]],
        labels: dict[str, dict[str, str]] | None = None,
        period_items: list[tuple[int, int]] | None = None,
    ):
        self.items = {row["id"]: dict(row) for row in items}
        self.labels = labels or {}
        # (period row id, item id) — the buy-in period item record.
        self.period_items = list(period_items or [])
        self.scans = 0
        self.fail_on_scan: int | None = None

    # -- fixture helpers ----------------------------------------------------
    def add(self, row: dict[str, Any]) -> None:
        self.items[row["id"]] = dict(row)

    def update(self, internal_id: int, **changes: Any) -> None:
        self.items[str(internal_id)].update(changes)

    def remove(self, internal_id: int) -> None:
        self.items.pop(str(internal_id), None)

    def copy(self) -> "FakeSuiteQL":
        return FakeSuiteQL(list(self.items.values()), self.labels, self.period_items)

    def add_period_item(self, item_id: int) -> None:
        next_id = max((row_id for row_id, _ in self.period_items), default=0) + 1
        self.period_items.append((next_id, item_id))

    def drop_period_items(self, item_id: int) -> None:
        self.period_items = [row for row in self.period_items if row[1] != item_id]

    def in_period(self, item_id: int) -> bool:
        return any(row[1] == item_id for row in self.period_items)

    # -- the client surface -------------------------------------------------
    def query(self, statement: str, limit: int | None = None) -> tuple[list[dict], bool]:
        label_query = re.search(r"FROM\s+(\w+)\s+WHERE\s+id\s+IN\s*\(([^)]*)\)", statement)
        if label_query:
            table = label_query.group(1)
            wanted = [part.strip() for part in label_query.group(2).split(",") if part.strip()]
            labels = self.labels.get(table, {})
            return ([{"id": value, "name": labels[value]} for value in wanted if value in labels], False)

        if "customrecord_ag_buy_in_period_item" in statement:
            after = int(re.search(r"id\s*>\s*(\d+)", statement).group(1))
            size = int(re.search(r"FETCH FIRST\s+(\d+)", statement).group(1))
            rows = [
                {"id": str(row_id), "item_id": str(item_id)}
                for row_id, item_id in sorted(self.period_items)
                if row_id > after
            ]
            return rows[:size], len(rows) > size

        in_list = re.search(r"FROM item i\s+WHERE\s+i\.id\s+IN\s*\(([^)]*)\)", statement)
        if in_list:
            wanted = {part.strip() for part in in_list.group(1).split(",") if part.strip()}
            size = int(re.search(r"FETCH FIRST\s+(\d+)", statement).group(1))
            rows = [row for key, row in self.items.items() if key in wanted]
            return sorted(rows, key=lambda row: int(row["id"]))[:size], False

        if "FROM item i" not in statement:
            raise SourceError(f"unexpected statement: {statement[:120]}")

        self.scans += 1
        if self.fail_on_scan is not None and self.scans >= self.fail_on_scan:
            raise SourceError("NetSuite went away mid-scan (fake)")

        after = int(re.search(r"i\.id\s*>\s*(\d+)", statement).group(1))
        size = int(re.search(r"FETCH FIRST\s+(\d+)", statement).group(1))
        rows = [
            row
            for row in sorted(self.items.values(), key=lambda value: int(value["id"]))
            if int(row["id"]) > after
            and (row["isinactive"] == "F" or any(row.get(column) for column in FACET_COLUMNS.values()))
        ]
        return rows[:size], len(rows) > size

    def query_all(self, statement: str, limit: int | None = None) -> list[dict]:
        rows, has_more = self.query(statement, limit=limit)
        if has_more:
            raise SourceError("statement returned more than one page (fake)")
        return rows


LABELS = {
    "customlist_facetoptions_brand": {"1": "Merch", "2": "LALO"},
    "customlist1495": {"10": "Unisex", "11": "BIGGER"},
    "customlist_facet_options_keyprogram": {"20": "Other", "21": "Spring"},
    "customlist_facet_options_producttype": {"30": "Apparel"},
}


def account() -> FakeSuiteQL:
    return FakeSuiteQL(
        [
            item(101, name="818 Tequila Pina Dad Hat", brand="1", channel="10", program="20",
                 product_type="30", order="3", category="Apparel", price="24.00", image="4006_101_1.jpg"),
            item(102, name="818 Tequila Tote Bag", brand="1", channel="10", program="20",
                 product_type="30", order="1", category="Bags"),
            item(103, name="LALO Tactical Hoodie", brand="2", channel="11", program="21",
                 product_type="30", order="2", description="Heavyweight hoodie"),
            # Numeric-only name: the ladder has nothing to work with.
            item(104, displayname="12345", itemid="123456", brand="2", channel="10"),
            # Facet-less: not part of the catalog.
            item(105, name="Plain Tee", displayname="Plain Tee"),
            # An unmapped facet id: resolves to nothing, must stay blank.
            item(106, name="Mystery Item", brand="999", channel="10"),
            # Inactive with a facet: a retirement candidate that was never created.
            item(107, name="Retired Cap", brand="1", channel="10", inactive=True),
            # In the catalog only because a buy-in period points at it: a kit
            # sold inside a real window, with no facet anywhere.
            item(108, name="Kitted Assembly", displayname="Kitted Assembly", itemid="3172_KIT-1"),
        ],
        LABELS,
        # A period points at the kit twice; 101 is also in a set, for the union.
        [(1, 108), (2, 108), (3, 101)],
    )


# ---------------------------------------------------------------------------
# Fixture database
# ---------------------------------------------------------------------------

def reset(conn: Any) -> int:
    execute(conn, "DELETE FROM users WHERE email LIKE ?", (f"{PREFIX}%",))
    company = execute(conn, "SELECT * FROM companies WHERE slug = ?", (SLUG,)).fetchone()
    if company is None:
        return 0
    company_id = int(company["id"])
    execute(conn, "DELETE FROM survey_responses WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM survey_period_items WHERE survey_period_id IN"
            " (SELECT id FROM survey_periods WHERE company_id = ?)", (company_id,))
    execute(conn, "DELETE FROM survey_periods WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM survey_items WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM audit_logs WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM netsuite_facet_values WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM survey_layouts WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM buy_book_layouts WHERE company_id = ?", (company_id,))
    execute(conn, "DELETE FROM companies WHERE id = ?", (company_id,))
    execute(conn, "DELETE FROM users WHERE email LIKE ?", (f"{PREFIX}%",))
    return company_id


def make_company(conn: Any) -> int:
    execute(
        conn,
        "INSERT INTO companies (slug, name, netsuite_image_base) VALUES (?, ?, '')",
        (SLUG, "Catalog Sync Check"),
    )
    row = execute(conn, "SELECT id FROM companies WHERE slug = ?", (SLUG,)).fetchone()
    return int(row["id"])


def sync(company_id: int, client: FakeSuiteQL, *, apply: bool = True, **kwargs: Any):
    with connect() as conn:
        company = dict(execute(conn, "SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone())
    return catalog_sync.sync_catalog(company, client=client, apply=apply, **kwargs)


def rows(conn: Any, company_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in execute(
            conn, "SELECT * FROM survey_items WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()
    ]


def catalog(conn: Any, company_id: int) -> dict[str, dict[str, Any]]:
    return {
        row["netsuite_item_id"]: row
        for row in rows(conn, company_id)
        if row["netsuite_item_id"]
    }


def digest(conn: Any, company_id: int) -> str:
    """A hash of everything a run could touch, for byte-identical assertions."""
    payload = {
        "items": rows(conn, company_id),
        "audit": [dict(row) for row in execute(
            conn, "SELECT * FROM audit_logs WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()],
        "period_items": [dict(row) for row in execute(
            conn,
            "SELECT * FROM survey_period_items WHERE survey_period_id IN"
            " (SELECT id FROM survey_periods WHERE company_id = ?) ORDER BY id",
            (company_id,),
        ).fetchall()],
        "periods": [dict(row) for row in execute(
            conn, "SELECT * FROM survey_periods WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()],
        "labels": [dict(row) for row in execute(
            conn, "SELECT * FROM netsuite_facet_values WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def add_period(conn: Any, company_id: int, catalog_item_id: int) -> int:
    now = utc_now().isoformat()
    execute(
        conn,
        "INSERT INTO survey_periods (company_id, name, status, survey_token, created_at, updated_at)"
        " VALUES (?, ?, 'open', ?, ?, ?)",
        (company_id, f"{PREFIX}Fall 2026", "token-check", now, now),
    )
    row = execute(
        conn, "SELECT id FROM survey_periods WHERE company_id = ? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()
    period_id = int(row["id"])
    execute(
        conn,
        "INSERT INTO survey_period_items (survey_period_id, catalog_item_id, item_name, sort_order,"
        " image_url) VALUES (?, ?, ?, 1, '')",
        (period_id, catalog_item_id, "Frozen row"),
    )
    return period_id


def period_item_rows(conn: Any, period_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in execute(
            conn, "SELECT * FROM survey_period_items WHERE survey_period_id = ? ORDER BY id", (period_id,)
        ).fetchall()
    ]


# ---------------------------------------------------------------------------
# The sections
# ---------------------------------------------------------------------------

def section_names() -> None:
    print("\n0. The name column and its ladder")
    chosen, source = resolve_name(
        {"storedisplayname": "818 Hat", "displayname": "21821", "itemid": "3148_21821"}
    )
    check("storedisplayname wins", (chosen, source) == ("818 Hat", "storedisplayname"), (chosen, source))
    chosen, source = resolve_name({"storedisplayname": "", "displayname": "Plain Tee", "itemid": "3148_1"})
    check("a non-numeric displayname is second", (chosen, source) == ("Plain Tee", "displayname"), (chosen, source))
    chosen, source = resolve_name({"storedisplayname": "", "displayname": "12345", "itemid": "3148_12345"})
    check("a numeric displayname is skipped", (chosen, source) == ("3148_12345", "itemid"), (chosen, source))
    chosen, source = resolve_name({"storedisplayname": "", "displayname": "12345", "itemid": "123456"})
    check("nothing usable means no name", (chosen, source) == ("", ""), (chosen, source))
    unnamed = map_item(
        item(200, displayname="12345", itemid="123", brand="1", description="Blue tee"),
        FacetResolution(labels={"brand": {"1": "Merch"}}),
        "now",
    )
    check("an unnamed row is Item <id> and flagged", unnamed.name == "Item 200" and unnamed.needs_name,
          unnamed.name)
    check("its description is taken (name came from elsewhere)",
          unnamed.fields.get("item_description") == "Blue tee", unnamed.fields)
    named = map_item(item(201, name="Hat", brand="1", description="Nice hat"), FacetResolution(), "now")
    check("a named row takes NetSuite's description too", named.fields.get("item_description") == "Nice hat",
          named.fields)
    blank = map_item(item(202, name="Hat two", brand="1"), FacetResolution(), "now")
    check("a blank description is left out entirely",
          "item_description" not in blank.fields and "item_description" not in blank.snapshot, blank.snapshot)
    check("the item's number is the row's itemid", item_number({"itemid": "3184_58212"}) == "3184_58212",
          item_number({"itemid": "3184_58212"}))
    check("a saved search's 'Item ID' column is the same field",
          item_number({"Item ID": "3184_58212"}) == "3184_58212", item_number({"Item ID": "3184_58212"}))
    check("no number field, no number — the internal id stands in for none",
          item_number({"id": "210"}) == "", item_number({"id": "210"}))
    numbered = map_item(item(203, name="Numbered Hat", itemid="3184_58212", brand="1"),
                        FacetResolution(), "now")
    check("the map carries the number into the row and the snapshot",
          numbered.fields.get("netsuite_item_number") == "3184_58212"
          and numbered.snapshot.get("netsuite_item_number") == "3184_58212", numbered.fields)
    unnumbered = map_item(
        {"id": "204", "storedisplayname": "Unnumbered Hat"}, FacetResolution(), "now"
    )
    check("a row with no number leaves the field out",
          "netsuite_item_number" not in unnumbered.fields
          and "netsuite_item_number" not in unnumbered.snapshot, unnumbered.snapshot)


def section_first_run(company_id: int, client: FakeSuiteQL) -> None:
    print("\n1. First run into an empty catalog")
    with connect() as conn:
        before = len(rows(conn, company_id))
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("catalog started empty", before == 0, before)
    check("6 items created (5 faceted + the kit a period points at)", report.created == 6, report.as_dict())
    check("scanned counts every item read (8)", report.scanned == 8, report.scanned)
    check("the facet-less item no period points at is excluded", report.excluded == 1, report.excluded)
    check("the buy-in-only item is counted as period_only", report.period_only == 1, report.period_only)
    check("both book flags are 0",
          all(int(row["in_survey"]) == 0 and int(row["in_buy_book"]) == 0 for row in after.values()),
          [(row["netsuite_item_id"], row["in_survey"], row["in_buy_book"]) for row in after.values()])
    check("price is left alone", all(row["price"] == "" for row in after.values()),
          [row["price"] for row in after.values()])
    check("image_url is left alone", all(row["image_url"] == "" for row in after.values()),
          [row["image_url"] for row in after.values()])
    check("the numeric-only item is Item 104", after["104"]["item_name"] == "Item 104",
          after.get("104", {}).get("item_name"))
    check("it is flagged needs_name", int(after["104"]["needs_name"]) == 1, after["104"]["needs_name"])
    check("it is out of every book",
          int(after["104"]["in_survey"]) == 0 and int(after["104"]["in_buy_book"]) == 0, after["104"])
    check("the unlabelled facet id stayed blank", after["106"]["brand"] == "", after["106"]["brand"])
    check("and was counted", report.label_misses == 1, report.label_misses)
    check("labels resolved", after["101"]["brand"] == "Merch" and after["101"]["channel"] == "Unisex",
          (after["101"]["brand"], after["101"]["channel"]))
    check("sort_order is numeric", int(after["101"]["sort_order"]) == 3, after["101"]["sort_order"])
    check("a blank sort is 0", int(after["104"]["sort_order"]) == 0, after["104"]["sort_order"])
    check("the facet-less item was not created", "105" not in after, sorted(after))
    check("the item only a buy-in period points at was created", "108" in after, sorted(after))
    check("it arrives with both book flags 0",
          int(after["108"]["in_survey"]) == 0 and int(after["108"]["in_buy_book"]) == 0, after["108"])
    check("the inactive item was not created", "107" not in after, sorted(after))
    snapshot = json.loads(after["101"]["netsuite_synced_json"])
    check("the snapshot holds itemid and category",
          snapshot["itemid"] == "3148_101" and snapshot["category"] == "Apparel", snapshot.get("itemid"))
    check("the row carries the number its itemid names",
          after["101"]["netsuite_item_number"] == "3148_101", after["101"]["netsuite_item_number"])
    check("every synced row has one",
          all(row["netsuite_item_number"] for row in after.values()),
          [(row["netsuite_item_id"], row["netsuite_item_number"]) for row in after.values()])
    check("the internal id is not the number",
          after["101"]["netsuite_item_number"] != after["101"]["netsuite_item_id"], after["101"])
    check("the snapshot holds the NetSuite name", snapshot["item_name"] == "818 Tequila Pina Dad Hat",
          snapshot.get("item_name"))


def section_number_backfill(company_id: int) -> None:
    """A table synced before the number column existed gets its numbers at boot."""
    print("\n1b. A pre-existing row is backfilled from its snapshot")
    now = utc_now().isoformat()
    snapshot = json.dumps({"netsuite_item_id": "900200", "itemid": "3184_90020", "item_name": "Old Row"})
    with connect() as conn:
        execute(
            conn,
            "INSERT INTO survey_items (company_id, item_name, brand, channel, product_type, key_program,"
            " item_description, sort_order, netsuite_item_id, netsuite_item_number, netsuite_synced_json,"
            " in_survey, in_buy_book, is_active, created_at, updated_at)"
            " VALUES (?, ?, '', '', '', '', '', 0, '900200', '', ?, 0, 0, ?, ?, ?)",
            (company_id, "Old Row", snapshot, True, now, now),
        )
        # A row that already has a number keeps it, whatever its snapshot says.
        execute(
            conn,
            "INSERT INTO survey_items (company_id, item_name, brand, channel, product_type, key_program,"
            " item_description, sort_order, netsuite_item_id, netsuite_item_number, netsuite_synced_json,"
            " in_survey, in_buy_book, is_active, created_at, updated_at)"
            " VALUES (?, ?, '', '', '', '', '', 0, '900201', '3184_KEEP', ?, 0, 0, ?, ?, ?)",
            (company_id, "Numbered Row",
             json.dumps({"netsuite_item_id": "900201", "itemid": "3184_OLD"}), True, now, now),
        )
    init_db()
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the blank number is filled from the snapshot",
          after["900200"]["netsuite_item_number"] == "3184_90020", after["900200"]["netsuite_item_number"])
    check("a row that already carries a number is never overwritten",
          after["900201"]["netsuite_item_number"] == "3184_KEEP", after["900201"]["netsuite_item_number"])
    init_db()
    with connect() as conn:
        again = catalog(conn, company_id)
    check("a second boot changes nothing",
          again["900200"]["netsuite_item_number"] == "3184_90020"
          and again["900201"]["netsuite_item_number"] == "3184_KEEP",
          (again["900200"]["netsuite_item_number"], again["900201"]["netsuite_item_number"]))
    with connect() as conn:
        for netsuite_id in ("900200", "900201"):
            execute(conn, "DELETE FROM survey_items WHERE company_id = ? AND netsuite_item_id = ?",
                    (company_id, netsuite_id))


def section_second_run(company_id: int, client: FakeSuiteQL) -> None:
    print("\n2. A second identical run writes nothing")
    with connect() as conn:
        before = {key: row["updated_at"] for key, row in catalog(conn, company_id).items()}
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("nothing created", report.created == 0, report.created)
    check("nothing updated", report.updated == 0, report.as_dict())
    check("everything unchanged", report.unchanged == 6, report.unchanged)
    moved = [key for key in before if after[key]["updated_at"] != before[key]]
    check("every updated_at is unchanged", not moved, moved)


def section_boot(company_id: int) -> None:
    """The demo seed must not retire or rewrite a catalog it did not create.

    Covered with the synced rows, plus the two kinds that carry no NetSuite id:
    a hand-created row, and the tracker-linked rows #5 will create.
    """
    print("\n2b. A boot (init_db) leaves the synced catalog alone")
    from seed import sync_default_survey_items

    now = utc_now().isoformat()
    with connect() as conn:
        for name in (f"{PREFIX}Hand row", f"{PREFIX}From tracker row"):
            execute(
                conn,
                "INSERT INTO survey_items (company_id, item_name, brand, channel, product_type,"
                " key_program, item_description, sort_order, netsuite_item_id, in_survey,"
                " in_buy_book, is_active, created_at, updated_at)"
                " VALUES (?, ?, '', '', '', '', '', 9, '', 1, 1, ?, ?, ?)",
                (company_id, name, True, now, now),
            )
        before_rows = rows(conn, company_id)
        before = catalog(conn, company_id)
    init_db()
    with connect() as conn:
        # init_db seeds the default company; run the same seed for this one, which
        # is the case a deployment hits when the app restarts.
        sync_default_survey_items(conn, company_id)
        after_rows = rows(conn, company_id)
        after = catalog(conn, company_id)
    check("every row is still there", set(before) == set(after), (sorted(before), sorted(after)))
    check("the seed added nothing to a catalog that is not empty",
          len(after_rows) == len(before_rows), (len(before_rows), len(after_rows)))
    check("every row is still active", all(int(row["is_active"]) == 1 for row in after_rows),
          [row["item_name"] for row in after_rows if int(row["is_active"]) != 1])
    check("the hand row and the tracker row are still active",
          all(int(row["is_active"]) == 1 for row in after_rows if not row["netsuite_item_id"]),
          [(row["item_name"], row["is_active"]) for row in after_rows if not row["netsuite_item_id"]])
    rewritten = [
        netsuite_id
        for netsuite_id, row in before.items()
        for field in ("item_name", "brand", "channel", "sort_order")
        if row[field] != after[netsuite_id][field]
    ]
    check("no name or facet was rewritten", not rewritten, rewritten)


def section_changes(company_id: int, client: FakeSuiteQL) -> None:
    print("\n3. A changed name updates; a price or an image does not")
    client.update(101, storedisplayname="818 Tequila Pina Hat (2026)")
    client.update(102, baseprice="99.00", image="4006_102_1.jpg")
    with connect() as conn:
        row = catalog(conn, company_id)["102"]
        before_price, before_image, before_updated = row["price"], row["image_url"], row["updated_at"]
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("one row updated", report.updated == 1, report.as_dict())
    check("the new name landed", after["101"]["item_name"] == "818 Tequila Pina Hat (2026)",
          after["101"]["item_name"])
    check("the price column is untouched", after["102"]["price"] == before_price == "", after["102"]["price"])
    check("the image column is untouched", after["102"]["image_url"] == before_image == "",
          after["102"]["image_url"])
    check("the price/image row was not rewritten", after["102"]["updated_at"] == before_updated,
          after["102"]["updated_at"])

    print("\n3b. A changed description of a fallback-named row updates")
    client.update(104, description="Blue tee, size L")
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the description landed", after["104"]["item_description"] == "Blue tee, size L",
          after["104"]["item_description"])
    check("reported as one update", report.updated == 1, report.as_dict())
    check("it stays flagged needs_name", int(after["104"]["needs_name"]) == 1, after["104"]["needs_name"])

    print("\n3c. A named row's description follows too; a blank one leaves it alone")
    client.update(103, description="Heavyweight hoodie, revised")
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the named row took the new description",
          after["103"]["item_description"] == "Heavyweight hoodie, revised",
          after["103"]["item_description"])
    check("reported as one update", report.updated == 1, report.as_dict())
    snapshot = json.loads(after["103"]["netsuite_synced_json"])
    check("the snapshot carries it", snapshot.get("item_description") == "Heavyweight hoodie, revised",
          snapshot.get("item_description"))

    client.update(103, description="")
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
        snapshot = json.loads(after["103"]["netsuite_synced_json"])
    check("a blank leaves the stored description", after["103"]["item_description"] == "Heavyweight hoodie, revised",
          after["103"]["item_description"])
    check("and is not counted as an override", report.overridden == 0, report.overridden)
    check("and is not written at all", report.updated == 0, report.as_dict())
    check("the snapshot drops the key", "item_description" not in snapshot, snapshot.get("item_description"))


def section_override(company_id: int, client: FakeSuiteQL) -> None:
    print("\n4. A person's edit wins, per field; Reset writes it back")
    with connect() as conn:
        row = catalog(conn, company_id)["103"]
        execute(
            conn,
            "UPDATE survey_items SET item_name = ?, updated_at = ? WHERE id = ?",
            ("LALO Hoodie (ours)", utc_now().isoformat(), row["id"]),
        )
    client.update(103, storedisplayname="LALO Tactical Hoodie v2", custitem_facet_brand="1")
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the human name survived", after["103"]["item_name"] == "LALO Hoodie (ours)",
          after["103"]["item_name"])
    check("but the brand followed NetSuite", after["103"]["brand"] == "Merch", after["103"]["brand"])
    check("one field counted as overridden", report.overridden == 1, report.overridden)
    check("the row still counted as updated (the brand changed)", report.updated == 1, report.as_dict())
    snapshot = json.loads(after["103"]["netsuite_synced_json"])
    check("the snapshot records what NetSuite said", snapshot["item_name"] == "LALO Tactical Hoodie v2",
          snapshot["item_name"])

    with connect() as conn:
        row = execute(conn, "SELECT * FROM survey_items WHERE id = ?", (after["103"]["id"],)).fetchone()
        restored = catalog_sync.reset_to_netsuite(conn, row)
        restored_row = execute(
            conn, "SELECT * FROM survey_items WHERE id = ?", (after["103"]["id"],)
        ).fetchone()
        audit = execute(
            conn,
            "SELECT * FROM audit_logs WHERE action = ? AND item_id = ? ORDER BY id DESC LIMIT 1",
            (catalog_sync.AUDIT_RESET, after["103"]["id"]),
        ).fetchone()
    check("Reset restored the NetSuite name", restored_row["item_name"] == "LALO Tactical Hoodie v2",
          restored_row["item_name"])
    check("Reset reported what it wrote", restored.get("item_name") == "LALO Tactical Hoodie v2", restored)
    check("Reset is audited", audit is not None, audit)
    report = sync(company_id, client)
    check("after Reset the row follows NetSuite again", report.updated == 0, report.as_dict())


def section_retire(company_id: int, client: FakeSuiteQL) -> None:
    print("\n5. Retirement is one-way")
    with connect() as conn:
        row = catalog(conn, company_id)["101"]
        period_id = add_period(conn, company_id, int(row["id"]))
        frozen = period_item_rows(conn, period_id)
    client.update(101, isinactive="T")
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
        frozen_after = period_item_rows(conn, period_id)
        audit = execute(
            conn,
            "SELECT * FROM audit_logs WHERE action = ? AND item_id = ? ORDER BY id DESC LIMIT 1",
            (catalog_sync.AUDIT_RETIRED, after["101"]["id"]),
        ).fetchone()
    check("the item was retired", int(after["101"]["is_active"]) == 0, after["101"]["is_active"])
    check("archived_at is set", bool(after["101"]["archived_at"]), after["101"]["archived_at"])
    check("one retirement counted", report.retired == 1, report.as_dict())
    reason = json.loads(audit["after_json"])["reason"] if audit else ""
    check("the audit names netsuite_inactive", reason == "netsuite_inactive", reason)
    check("survey_period_items are byte-identical", frozen == frozen_after, (frozen, frozen_after))

    report = sync(company_id, client)
    with connect() as conn:
        again = catalog(conn, company_id)
    check("a second run retires nothing", report.retired == 0, report.retired)
    check("and never reactivates", int(again["101"]["is_active"]) == 0, again["101"]["is_active"])

    print("\n5b. A human restore is retired again by the next run")
    with connect() as conn:
        execute(conn, "UPDATE survey_items SET is_active = ?, archived_at = NULL WHERE id = ?",
                (True, again["101"]["id"]))
    report = sync(company_id, client)
    with connect() as conn:
        restored = catalog(conn, company_id)
    check("NetSuite still calls it inactive, so it retires again",
          int(restored["101"]["is_active"]) == 0 and report.retired == 1, report.as_dict())

    print("\n5c. An item whose facets went away retires")
    client.update(102, custitem_facet_brand=None, custitem_facet_channel=None,
                  custitem_facet_keyprogram=None, custitem_facet_producttype=None)
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the facet-less row was retired", int(after["102"]["is_active"]) == 0, after["102"]["is_active"])
    check("one retirement counted", report.retired == 1, report.retired)

    print("\n5d. Facets gone but still in a buy-in period: it stays")
    client.update(104, custitem_facet_brand=None, custitem_facet_channel=None)
    client.add_period_item(104)
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the item is still active", int(after["104"]["is_active"]) == 1, after["104"]["is_active"])
    check("nothing was retired", report.retired == 0, report.as_dict())
    check("it is counted as in a buy-in but facet-less", report.period_only >= 1, report.period_only)

    print("\n5e. Losing the last period row and the facets retires it")
    check("the period row existed", client.in_period(104))
    client.drop_period_items(104)
    report = sync(company_id, client)
    with connect() as conn:
        after = catalog(conn, company_id)
    check("the item was retired", int(after["104"]["is_active"]) == 0, after["104"]["is_active"])
    check("one retirement counted", report.retired == 1, report.retired)


def section_untouched(company_id: int, client: FakeSuiteQL) -> None:
    print("\n6. Hand-created and from-tracker rows are untouched")
    now = utc_now().isoformat()
    with connect() as conn:
        execute(
            conn,
            "INSERT INTO survey_items (company_id, item_name, brand, channel, product_type, key_program,"
            " item_description, sort_order, netsuite_item_id, in_survey, in_buy_book, is_active,"
            " created_at, updated_at) VALUES (?, ?, '', '', '', '', '', 7, '', 1, 1, ?, ?, ?)",
            (company_id, f"{PREFIX}Hand made", True, now, now),
        )
        execute(
            conn,
            "INSERT INTO survey_items (company_id, item_name, brand, channel, product_type, key_program,"
            " item_description, sort_order, netsuite_item_id, in_survey, in_buy_book, is_active,"
            " created_at, updated_at) VALUES (?, ?, '', '', '', '', '', 8, '900001', 1, 1, ?, ?, ?)",
            (company_id, f"{PREFIX}From tracker", True, now, now),
        )
        before = [dict(row) for row in execute(
            conn, "SELECT * FROM survey_items WHERE item_name LIKE ? ORDER BY id", (f"{PREFIX}%",)
        ).fetchall()]
    report = sync(company_id, client)
    with connect() as conn:
        after = [dict(row) for row in execute(
            conn, "SELECT * FROM survey_items WHERE item_name LIKE ? ORDER BY id", (f"{PREFIX}%",)
        ).fetchall()]
    check("neither row changed at all", before == after,
          [(old["item_name"], new["updated_at"]) for old, new in zip(before, after) if old != new])
    tracker = next(row for row in after if row["netsuite_item_id"] == "900001")
    check("the tracker row keeps its foreign NetSuite id and stays active", int(tracker["is_active"]) == 1,
          tracker["is_active"])
    check("neither gained a snapshot", all(not row["netsuite_synced_json"] for row in after),
          [row["netsuite_synced_json"] for row in after])
    check("the sync ignored them", report.created == 0 and report.retired == 0, report.as_dict())


def section_dry_run(company_id: int, client: FakeSuiteQL) -> None:
    print("\n7. A dry run leaves the database byte-identical")
    client.add(item(109, name="Dry Run Item", brand="1", channel="10"))
    with connect() as conn:
        before = digest(conn, company_id)
    report = sync(company_id, client, apply=False)
    with connect() as conn:
        after = digest(conn, company_id)
    check("the report says it was a dry run", report.dry_run, report.dry_run)
    check("it still planned the work", report.created == 1, report.as_dict())
    check("the database is byte-identical", before == after, (before, after))
    client.remove(109)


def section_failure(company_id: int, client: FakeSuiteQL) -> None:
    print("\n8. A client that dies mid-scan writes nothing")
    client.add(item(110, name="Half Scanned", brand="1", channel="10"))
    with connect() as conn:
        before = digest(conn, company_id)
    failing = client.copy()
    failing.fail_on_scan = 2
    error = ""
    try:
        sync(company_id, failing, page_size=2)
    except SourceError as caught:
        error = str(caught)
    with connect() as conn:
        after = digest(conn, company_id)
    check("the run raised", bool(error), error)
    check("nothing was written", before == after, (before, after))

    print("\n8b. A failure in the middle of writing rolls every row back")
    calls = {"n": 0}
    real_map = catalog_sync.map_item

    def explode(row, labels, synced_at):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("the run died between two rows")
        return real_map(row, labels, synced_at)

    catalog_sync.map_item = explode
    try:
        try:
            sync(company_id, client)
            raised = False
        except RuntimeError:
            raised = True
    finally:
        catalog_sync.map_item = real_map
    with connect() as conn:
        after_write_failure = digest(conn, company_id)
    check("the run raised while writing", raised)
    check("the first rows were rolled back too", before == after_write_failure, (before, after_write_failure))
    client.remove(110)


def section_routes(company_id: int, client: FakeSuiteQL) -> None:
    """The admin route: who may call it, what it returns, and Reset.

    The route builds its client from the environment, so the fake is injected
    where the sync looks for one — the same function the cron calls.
    """
    print("\n9. The admin route")
    from fastapi.testclient import TestClient

    from app import app
    from config import ADMIN_EMAIL, ADMIN_PASSWORD
    from database import fetch_company_by_slug, hash_password

    with connect() as conn:
        execute(
            conn,
            "INSERT INTO users (email, name, password_hash, role, company_id)"
            " VALUES (?, ?, ?, 'editor', ?)",
            (f"{PREFIX}editor@example.com", "Check Editor", hash_password("Check123"), company_id),
        )
    api = TestClient(app)

    def login(email: str, password: str) -> dict[str, str]:
        response = api.post("/api/auth/login", json={"email": email, "password": password})
        return {"Authorization": f"Bearer {response.json()['token']}"}

    admin = login(ADMIN_EMAIL, ADMIN_PASSWORD)
    editor = login(f"{PREFIX}editor@example.com", "Check123")

    scoped = f"?company_id={company_id}"
    check("anonymous is refused", api.post(f"/api/admin/catalog/sync{scoped}").status_code == 401)
    check("an editor is refused", api.post(f"/api/admin/catalog/sync{scoped}", headers=editor).status_code == 403)

    real_client_from_env = catalog_sync.client_from_env
    catalog_sync.client_from_env = lambda env=None: client
    try:
        client.add(item(111, name="Route Item", brand="1", channel="10"))
        response = api.post(f"/api/admin/catalog/sync{scoped}", headers=admin)
        check("an admin gets 200", response.status_code == 200, response.status_code)
        payload = response.json() if response.status_code == 200 else {}
        report = payload.get("report") or {}
        check("the route returns the report", report.get("created") == 1, report)
        check("the run is not a dry run", report.get("dry_run") is False, report.get("dry_run"))

        response = api.get(f"/api/admin/catalog/sync{scoped}", headers=admin)
        last = (response.json() or {}).get("last_run") or {}
        check("the last-run line is readable", response.status_code == 200 and last.get("report") is not None,
              response.status_code)
        check("it carries the same counters", (last.get("report") or {}).get("scanned") == report.get("scanned"),
              last)

        with connect() as conn:
            created = execute(
                conn,
                "SELECT * FROM survey_items WHERE company_id = ? AND netsuite_item_id = '111'",
                (company_id,),
            ).fetchone()
        check("the row arrives with both book flags 0",
              created is not None and int(created["in_survey"]) == 0 and int(created["in_buy_book"]) == 0,
              dict(created) if created else None)

        with connect() as conn:
            catalog_row = execute(
                conn, "SELECT * FROM survey_items WHERE company_id = ? AND netsuite_item_id = '111'",
                (company_id,),
            ).fetchone()
            execute(conn, "UPDATE survey_items SET item_name = 'Typed by a person' WHERE id = ?",
                    (catalog_row["id"],))
        client.update(111, storedisplayname="Route Item v2")
        api.post(f"/api/admin/catalog/sync{scoped}", headers=admin)
        response = api.post(f"/api/admin/catalog/items/{catalog_row['id']}/reset{scoped}", headers=admin)
        check("Reset answers 200", response.status_code == 200, response.status_code)
        check("Reset restored the NetSuite name",
              (response.json() or {}).get("item", {}).get("item_name") == "Route Item v2",
              (response.json() or {}).get("item"))
        check("an unknown item is a 404",
              api.post(f"/api/admin/catalog/items/99999999/reset{scoped}", headers=admin).status_code == 404)
    finally:
        catalog_sync.client_from_env = real_client_from_env
        client.remove(111)


def section_live() -> None:
    print("\n9. Live read (only with credentials)")
    from catalog_sync import read_item_rows
    from netsuite import client_from_env

    try:
        client = client_from_env()
    except SourceError as error:
        print(f"  SKIP  no NetSuite credentials: {error}")
        return
    rows_read, complete = read_item_rows(client, page_size=50, limit=50)
    check("a live read returns items", len(rows_read) > 0, len(rows_read))
    check("the read stopped at the limit", not complete, complete)
    facet_bearing = [row for row in rows_read if any(row.get(column) for column in FACET_COLUMNS.values())]
    print(f"  INFO  {len(facet_bearing)} of {len(rows_read)} sampled items carry a facet")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also make one real NetSuite read")
    args = parser.parse_args()

    init_db()
    with connect() as conn:
        reset(conn)
        company_id = make_company(conn)

    try:
        section_names()
        client = account()
        section_first_run(company_id, client)
        section_number_backfill(company_id)
        section_second_run(company_id, client)
        section_boot(company_id)
        section_changes(company_id, client)
        section_override(company_id, client)
        section_retire(company_id, client)
        section_untouched(company_id, client)
        section_dry_run(company_id, client)
        section_failure(company_id, client)
        section_routes(company_id, client)
        if args.live:
            section_live()
    finally:
        with connect() as conn:
            reset(conn)

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} assertion(s):")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    if _OWNS_SCRATCH:
        print(f"(scratch database: {_SCRATCH_DB})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
