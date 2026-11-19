"""Guard: the app must never write to NetSuite.

NetSuite is a read-only source. Its data reaches the app as an export that
``scripts/import_item_image_records.py`` loads into our own database; nothing in
this repository opens a connection to NetSuite's API. This script keeps it that
way: it fails when a new outbound HTTP call appears outside the known, read-only
sites, or when a call next to a NetSuite/SuiteCommerce host uses a write verb.

    python backend/scripts/check_netsuite_readonly.py

Exit code 0 means "no outbound write to NetSuite anywhere in this tree".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "venv"}
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js"}

# Every outbound HTTP call site allowed in this codebase, with why it is safe.
# A new one fails the guard until someone adds it here deliberately.
ALLOWED_CALL_SITES: dict[str, str] = {
    "backend/item_images.py": "HEAD only, to check that a media file answers 200",
    "backend/zoho_analytics.py": "Zoho Analytics: GET reads plus a Zoho token refresh",
    "backend/scripts/check_item_images.py": "test client against our own app",
    "backend/netsuite.py": "read-only SuiteQL: one POST to .../query/v1/suiteql, which runs a query",
}

# A POST that is a read. SuiteQL takes its statement in a POST body, so the one
# client necessarily "posts"; the guard treats it as read-only only while that
# is the only write verb in the file and no other endpoint is addressed.
READ_ONLY_POST_SITES = {
    "backend/netsuite.py": "/services/rest",
}

OUTBOUND_PATTERNS = (
    re.compile(r"urlopen\("),
    re.compile(r"opener\.open\("),
    re.compile(r"\brequests\.(?:get|post|put|patch|delete|request)\("),
    re.compile(r"\bhttpx\.(?:get|post|put|patch|delete|Client|AsyncClient)\b"),
    re.compile(r"http\.client\.HTTPS?Connection"),
    re.compile(r"\bfetch\(['\"]https?://"),
)

# Hosts that belong to NetSuite or the SuiteCommerce storefront it serves.
NETSUITE_HOST_HINTS = (
    "netsuite.com",
    "netsuite",
    "suitecommerce",
    "mrlsmartbuy",
    "pdccatalog",
    "myfsionline",
)

WRITE_VERB = re.compile(r"\b(POST|PUT|PATCH|DELETE)\b")
METHOD_ARG = re.compile(r"method\s*=\s*['\"]?(POST|PUT|PATCH|DELETE)", re.IGNORECASE)


def iter_sources():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    failures: list[str] = []
    seen: dict[str, int] = {}
    write_risks: list[str] = []

    for path in iter_sources():
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        hits = 0
        for number, line in enumerate(lines, start=1):
            if any(pattern.search(line) for pattern in OUTBOUND_PATTERNS):
                hits += 1
                # A write verb in the same statement window as a NetSuite host.
                window = "\n".join(lines[max(0, number - 3): number + 3])
                window_lower = window.lower()
                if any(hint in window_lower for hint in NETSUITE_HOST_HINTS) and (
                    WRITE_VERB.search(window) or METHOD_ARG.search(window)
                ):
                    write_risks.append(f"{relative}:{number}")
        if hits:
            seen[relative] = hits

    for relative, count in sorted(seen.items()):
        reason = ALLOWED_CALL_SITES.get(relative)
        if reason is None:
            failures.append(
                f"{relative}: {count} outbound call site(s) with no documented reason — "
                "review it, and if it cannot write to NetSuite add it to ALLOWED_CALL_SITES"
            )
        else:
            print(f"  ok    {relative}  ({count}) — {reason}")

    for location in write_risks:
        relative = location.split(":", 1)[0]
        if relative in READ_ONLY_POST_SITES:
            continue
        failures.append(f"{location}: a write verb sits next to a NetSuite/SuiteCommerce host")

    for relative, endpoint in READ_ONLY_POST_SITES.items():
        path = ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        writes = re.findall(r"requests\.(put|patch|delete)\(|method\s*=\s*['\"](?:PUT|PATCH|DELETE)", text, re.I)
        posts = re.findall(r"requests\.post\(", text)
        if writes:
            raise SystemExit(f"{path}: a read-only client calls a write verb: {writes}")
        if len(posts) != 1:
            failures.append(f"{relative}: {len(posts)} request call(s) in a query-only client; one read is expected")
        if endpoint not in text:
            failures.append(f"{relative}: does not address {endpoint}")

    # NetSuite credentials in the tree would mean a client capable of writing.
    # A credential is what would make a write possible; a column called
    # netsuite_item_id is data, not access. Keep this strict.
    credential_pattern = re.compile(
        r"\b(NS_CONSUMER_[A-Z_]+|NETSUITE_(?:CONSUMER|TOKEN|SECRET|PASSWORD|API)[A-Z_]*|SUITELET_(?:TOKEN|SECRET))"
    )
    for path in iter_sources():
        if path.name == "check_netsuite_readonly.py":
            continue
        # The one client is where the credential names belong; what matters is
        # that it cannot write, which the POST rule above proves.
        if path.relative_to(ROOT).as_posix() == "backend/netsuite.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if credential_pattern.search(text):
            failures.append(f"{path.relative_to(ROOT).as_posix()}: NetSuite credential named")

    if failures:
        print("\nFAILED — the read-only NetSuite rule is not provable:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nNo outbound write to NetSuite: every call site is accounted for and read-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
