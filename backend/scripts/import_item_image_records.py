"""Import NetSuite item image records: internal id -> media file / image URL.

The app never formats an image URL from an item id (see ``item_images.py``), so
a URL has to arrive from a NetSuite item record. This script is that entrance,
and it is the same entrance for either route:

* **export route** — Jon exports ``internal id + image url`` (saved search,
  report, or the ``MultiOrder.Service.ss`` payload) and we import the file;
* **live route** — a RESTlet returning ``itemimages_detail`` writes this same
  table, or its JSON is piped through this script. Nothing else in the app
  changes.

It writes only ``netsuite_item_images``. ``scripts/backfill_netsuite_ids.py``
fills the tracker and catalog rows from it, and the app's write hook does the
same for a row added later.

Input: JSON or CSV, re-runnable (rows upsert by company + internal id).

Each record needs:

* an internal id — read from ``internalid`` / ``internal_id`` / ``internalId``.
  Numeric, because that is the key the lookup uses; a SKU such as
  ``60047-RAIL`` is rejected rather than stored under the wrong key.
* a media reference — any value in the record holding ``/images/<file>`` or
  ``/core/media/media.nl?id=<file>`` (so a raw ``itemimages_detail`` blob
  works), or an explicit ``image_url`` / ``url`` / ``media_file``.
* optionally ``item_name``, kept so the backfill can join rows that carry no id
  yet, and ``source`` (free text, defaults to the file name).

``/core/media/media.nl?id=…&resizeid=11&resizew=225&resizeh=255`` is rewritten
to ``<host>/images/<file>``: the media endpoint answers 500 without a shopper
session and its path carries no extension (so the frontend's render gate
rejects it), while the file itself is public. Image size stays a CSS decision.

``--host`` is what turns a record's relative path absolute — it is per company
dataset (``companies.netsuite_image_base`` overrides the env default). Import
without it and the file name is stored, the URL is not; re-run with it later
and the URLs fill in, no second export needed.

Usage (from the repo root, same env as the app):
    python backend/scripts/import_item_image_records.py records.json
    python backend/scripts/import_item_image_records.py records.json --apply
    python backend/scripts/import_item_image_records.py records.json --apply \
        --host https://demo.mrlsmartbuy.com
    python backend/scripts/import_item_image_records.py records.csv --apply \
        --company phillips --verify

Default is a dry run. Prints what it would insert, update, skip and verify.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.append(str(Path(__file__).resolve().parents[1]))

from config import DEFAULT_COMPANY_SLUG, NETSUITE_IMAGE_BASE  # noqa: E402
from database import connect, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402
from item_images import (  # noqa: E402
    media_reference,
    normalize_item_id,
    store_record,
    verify_image_url,
)

INTERNAL_ID_KEYS = ("internalid", "internal_id", "internalId", "internal id")
MEDIA_KEYS = ("image_url", "url", "media_file", "image", "itemimage")
NAME_KEYS = ("item_name", "itemname", "storedisplayname", "displayname")

PAYLOAD_LIST_KEYS = ("items", "buyInItems", "records", "results", "rows")


def load_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSON or CSV file into a list of record dicts."""
    if path.suffix.lower() in (".csv", ".tsv"):
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle, delimiter=delimiter)]
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in PAYLOAD_LIST_KEYS:
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [data]
    raise SystemExit(f"{path} holds no records (expected a list or an object with 'items').")


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_strings(item)


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        for existing in record:
            if str(existing).strip().lower() == key.lower():
                text = str(record[existing] or "").strip()
                if text:
                    return text
    return ""


def extract(record: dict[str, Any], host: str = "") -> dict[str, str]:
    """Pull (internal id, media file, image URL, item name) out of one record."""
    internal_id = normalize_item_id(_first(record, INTERNAL_ID_KEYS))
    if not internal_id:
        return {"internal_id": "", "reason": "no numeric internal id"}

    item_name = _first(record, NAME_KEYS)

    # An explicit media field first, then any string in the record that parses
    # as a NetSuite media reference — a raw itemimages_detail blob has no fixed
    # shape we should depend on.
    explicit = _first(record, MEDIA_KEYS)
    candidates = [explicit] if explicit else []
    candidates.extend(str(text) for text in _walk_strings(record))

    for candidate in candidates:
        reference = media_reference(candidate, host)
        if reference is not None:
            return {
                "internal_id": internal_id,
                "media_file": reference["media_file"],
                "image_url": reference["image_url"],
                "item_name": item_name,
                "reason": "",
            }
    return {"internal_id": internal_id, "media_file": "", "image_url": "",
            "item_name": item_name, "reason": "no media reference in the record"}


def ingest_records(
    conn: Any,
    records: list[dict[str, Any]],
    company_id: int,
    host: str = "",
    *,
    apply_changes: bool = False,
    verify: bool = False,
    source: str = "",
) -> dict[str, int]:
    """Store one record set; returns counts. Re-running the same set is a no-op."""
    counts = {
        "records": len(records), "inserted": 0, "updated": 0, "unchanged": 0,
        "skipped": 0, "no_media": 0, "no_url": 0, "verify_failed": 0,
    }
    for record in records:
        parsed = extract(record, host)
        if not parsed["internal_id"]:
            counts["skipped"] += 1
            continue
        if not parsed["media_file"]:
            counts["no_media"] += 1
            continue
        if not parsed["image_url"]:
            counts["no_url"] += 1
        if verify and parsed["image_url"] and not verify_image_url(parsed["image_url"]):
            counts["verify_failed"] += 1
            continue
        if not apply_changes:
            counts["inserted"] += 1
            continue
        result = store_record(
            conn,
            company_id,
            parsed["internal_id"],
            media_file=parsed["media_file"],
            image_url=parsed["image_url"],
            item_name=parsed["item_name"],
            source=source,
        )
        counts[result] += 1
    if apply_changes:
        conn.commit()
    return counts


def _report(counts: dict[str, int], applied: bool, host: str) -> None:
    print(f"  records read       {counts['records']}")
    print(f"  {'would insert' if not applied else 'inserted'}       {counts['inserted']}")
    print(f"  {'would update' if not applied else 'updated'}       {counts['updated']}")
    print(f"  unchanged          {counts['unchanged']}")
    print(f"  no internal id     {counts['skipped']}")
    print(f"  no media reference {counts['no_media']}")
    print(f"  file but no URL    {counts['no_url']}")
    if counts["verify_failed"]:
        print(f"  verify failed      {counts['verify_failed']}  (URL did not answer 200 with an image)")
    if counts["no_url"] and not host:
        print("  no host given: file names stored, URLs left empty. Re-run with --host <suitecommerce site>.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("records", help="records file (.json, .csv or .tsv)")
    parser.add_argument("--company", default=DEFAULT_COMPANY_SLUG, help=f"company slug (default {DEFAULT_COMPANY_SLUG})")
    parser.add_argument("--host", default="", help="SuiteCommerce site host, e.g. https://demo.mrlsmartbuy.com")
    parser.add_argument("--apply", action="store_true", help="write the records (default is a dry run)")
    parser.add_argument("--verify", action="store_true", help="HEAD each URL and skip records that do not answer 200")
    args = parser.parse_args()

    path = Path(args.records)
    if not path.exists():
        raise SystemExit(f"No such file: {path}")
    records = load_records(path)
    if not records:
        raise SystemExit(f"{path} holds no records.")

    init_db()
    with connect() as conn:
        company = fetch_company_by_slug(conn, args.company)
        if company is None:
            raise SystemExit(f"No company with slug '{args.company}'.")
        host = (args.host or str(company["netsuite_image_base"] or "") or NETSUITE_IMAGE_BASE).strip()
        if not host:
            print("No --host and no companies.netsuite_image_base: relative record paths cannot become URLs.")

        counts = ingest_records(
            conn, records, int(company["id"]), host,
            apply_changes=args.apply, verify=args.verify, source=path.name,
        )

    print(f"Company: {args.company}   host: {host or '<none>'}   source: {path.name}")
    _report(counts, args.apply, host)
    print("\nDone." + ("" if args.apply else " No changes written (use --apply)."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
