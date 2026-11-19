"""Reconcile one company's catalog against the NetSuite item master.

    python backend/scripts/sync_catalog.py                      # dry run
    python backend/scripts/sync_catalog.py --apply              # write
    python backend/scripts/sync_catalog.py --company phillips --limit 200 --report /tmp/run.json

Dry run unless ``--apply``. The whole run is one transaction, so a failure
mid-scan leaves the catalog untouched; a dry run rolls back on purpose and is
therefore an honest preview of the write.

One company per run: ``--company`` (a slug), else ``NETSUITE_SYNC_COMPANY_SLUG``,
else the app's default company. The item master read is per NetSuite account;
the rows it writes belong to that one dataset.

Read-only towards NetSuite: SuiteQL queries only, no record is ever written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from catalog_sync import sync_catalog  # noqa: E402
from config import DEFAULT_COMPANY_SLUG, NETSUITE_SYNC_COMPANY_SLUG  # noqa: E402
from database import connect, fetch_company_by_slug  # noqa: E402
from init_db import init_db  # noqa: E402
from netsuite import SourceError  # noqa: E402


def resolve_company(conn, slug: str):
    """The company this run owns: explicit slug, the scheduler's, or the default."""
    wanted = (slug or NETSUITE_SYNC_COMPANY_SLUG or DEFAULT_COMPANY_SLUG).strip()
    company = fetch_company_by_slug(conn, wanted)
    if company is None:
        raise SystemExit(f"No company with slug '{wanted}'.")
    return dict(company)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--company", default="", help="company slug (default: the configured one)")
    parser.add_argument("--report", default="", help="write the report as JSON to this path")
    parser.add_argument("--limit", type=int, default=None, help="read at most this many item rows")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    init_db()
    with connect() as conn:
        company = resolve_company(conn, args.company)

    try:
        report = sync_catalog(company, apply=args.apply, limit=args.limit, log=print)
    except SourceError as error:
        print(f"sync failed: {error}", file=sys.stderr)
        print("the catalog is unchanged", file=sys.stderr)
        return 2

    payload = report.as_dict()
    if args.report:
        Path(args.report).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        mode = "applied" if args.apply else "dry run, nothing written"
        print(f"{company['slug']}: {mode}")
        print(f"  {report.summary()}")
        for note in report.notes:
            print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
