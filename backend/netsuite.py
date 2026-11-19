"""The one NetSuite client this app uses: read-only SuiteQL, plus facet labels.

Every NetSuite reader goes through this module — the per-customer Buy In pull
(#6) and the catalog reconciliation both call ``SuiteQLClient.query_all``, and
both resolve facet list ids through ``resolve_facets``. There is no second HTTP
client and no second credential.

Read-only by construction: the only request this module makes is
``POST <base>/services/rest/query/v1/suiteql``. SuiteQL is a POST that *runs a
query*; no record is created, changed or deleted. There is no code path here
that can reach a record endpoint, and ``scripts/check_netsuite_readonly.py``
fails the tree if another POST appears beside a NetSuite host.

Credentials are NetSuite's token-based authentication (TBA, OAuth 1.0a
HMAC-SHA256) — the set the account issues and the one verified live on
2026-11-18. The two URLs in a pasted set arrive quoted and prefixed
``htttps://``; ``normalize_url`` repairs them on read so a typo in a secret
store cannot take the sync down.

    client = client_from_env()            # NETSUITE_* variables
    rows = client.query_all("SELECT id FROM item ORDER BY id", limit=1000)

Paging rule that cost a day of data: **SuiteQL ignores OFFSET**. A statement
that asks for page 2 gets page 1 again, forever. ``query_all`` therefore refuses
to page on its own and tells the caller to use a keyset
(``WHERE id > :last ORDER BY id``). That is how both readers page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

DEFAULT_PAGE_SIZE = 1000
DEFAULT_TIMEOUT = 60.0

# The four facet columns on the item record, and the list table each value's
# label lives in. Verified against the account on 2026-11-19: the record API
# href on an item names the exact list table, and only two of the four carry
# the ``facet_options_`` word in their script id.
FACET_COLUMNS: dict[str, str] = {
    "brand": "custitem_facet_brand",
    "channel": "custitem_facet_channel",
    "key_program": "custitem_facet_keyprogram",
    "product_type": "custitem_facet_producttype",
}

FACET_VALUE_TABLES: dict[str, str] = {
    "brand": "customlist_facetoptions_brand",
    "channel": "customlist1495",
    "key_program": "customlist_facet_options_keyprogram",
    "product_type": "customlist_facet_options_producttype",
}

# The order Multi-Order Sort uses, and the category bucket the catalog keeps.
SORT_COLUMN = "custitem_acs_multi_order_sort"
CATEGORY_COLUMN = "custitem_acs_multi_order_cat"

CREDENTIAL_ENV_VARS = (
    "NETSUITE_CONSUMER_KEY",
    "NETSUITE_CONSUMER_SECRET",
    "NETSUITE_TOKEN_ID",
    "NETSUITE_TOKEN_SECRET",
    "NETSUITE_REALM",
    "NETSUITE_BASE_URL",
)


class SourceError(Exception):
    """The source could not be read, or answered something unusable."""


def normalize_url(value: Any) -> str:
    """A pasted NetSuite URL, repaired: quotes off, ``htttps`` back to ``https``.

    Credential files arrive with every URL wrapped in quotes and prefixed with
    an extra ``t`` — that is what Jon's card contained and it is not worth a
    failed night's sync to be strict about it.
    """
    text = str(value or "").strip().strip('"').strip("'").strip()
    if not text:
        return ""
    text = re.sub(r"^h+t+ps?://", "https://", text, flags=re.IGNORECASE)
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = "https://" + text.lstrip("/")
    return text.rstrip("/")


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'").strip()


@dataclass
class NetSuiteCredentials:
    consumer_key: str
    consumer_secret: str
    token_id: str
    token_secret: str
    realm: str
    base_url: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "NetSuiteCredentials":
        source = os.environ if env is None else env
        values = {name: _clean(source.get(name, "")) for name in CREDENTIAL_ENV_VARS}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise SourceError(
                "NetSuite is not configured: "
                + ", ".join(missing)
                + " must be set (read-only TBA token: consumer key/secret, token id/secret,"
                " realm and base URL)"
            )
        return cls(
            consumer_key=values["NETSUITE_CONSUMER_KEY"],
            consumer_secret=values["NETSUITE_CONSUMER_SECRET"],
            token_id=values["NETSUITE_TOKEN_ID"],
            token_secret=values["NETSUITE_TOKEN_SECRET"],
            realm=values["NETSUITE_REALM"].upper(),
            base_url=normalize_url(values["NETSUITE_BASE_URL"]),
        )


class SuiteQLClient:
    """Read-only SuiteQL over a TBA token. One POST call site, nothing else."""

    name = "suiteql"

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        token_id: str,
        token_secret: str,
        realm: str,
        base_url: str,
        timeout: float | None = None,
    ):
        self.credentials = NetSuiteCredentials(
            consumer_key=_clean(consumer_key),
            consumer_secret=_clean(consumer_secret),
            token_id=_clean(token_id),
            token_secret=_clean(token_secret),
            realm=_clean(realm).upper(),
            base_url=normalize_url(base_url),
        )
        if not self.credentials.base_url:
            raise SourceError("NetSuite base URL is empty")
        self.timeout = float(timeout or os.getenv("NETSUITE_TIMEOUT", DEFAULT_TIMEOUT))
        self.url = self._query_url()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SuiteQLClient":
        credentials = NetSuiteCredentials.from_env(env)
        return cls(**credentials.__dict__)

    # -- the one request ----------------------------------------------------

    def _query_url(self) -> str:
        base = self.credentials.base_url
        if base.endswith("/services/rest"):
            root = base[: -len("/services/rest")]
        elif base.endswith("/services"):
            root = base[: -len("/services")]
        else:
            root = base
        return f"{root}/services/rest/query/v1/suiteql"

    def _authorization(self, method: str, url: str) -> str:
        credentials = self.credentials
        oauth = {
            "oauth_consumer_key": credentials.consumer_key,
            "oauth_token": credentials.token_id,
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(int(time.time())),
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_version": "1.0",
        }
        parts = urllib.parse.urlparse(url)
        params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        params.update(oauth)
        normalized = "&".join(
            f"{urllib.parse.quote(str(key), safe='')}={urllib.parse.quote(str(value), safe='')}"
            for key, value in sorted(params.items())
        )
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        signing = (
            f"{method.upper()}&{urllib.parse.quote(base, safe='')}"
            f"&{urllib.parse.quote(normalized, safe='')}"
        )
        key = (
            f"{urllib.parse.quote(credentials.consumer_secret, safe='')}"
            f"&{urllib.parse.quote(credentials.token_secret, safe='')}"
        )
        signature = base64.b64encode(
            hmac.new(key.encode("utf-8"), signing.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        oauth["oauth_signature"] = signature
        header = [f'realm="{credentials.realm}"'] + [
            f'{name}="{urllib.parse.quote(str(value), safe="")}"'
            for name, value in oauth.items()
        ]
        return "OAuth " + ",".join(header)

    def _post(self, statement: str) -> dict[str, Any]:
        try:
            import requests
        except ImportError as error:  # pragma: no cover - dependency is pinned
            raise SourceError(f"the NetSuite client needs requests: {error}") from error

        body = json.dumps({"q": statement})
        response = requests.post(
            self.url,
            headers={
                "Authorization": self._authorization("POST", self.url),
                "Content-Type": "application/json",
                "Prefer": "transient",
            },
            data=body,
            timeout=self.timeout,
        )
        if response.status_code == 401:
            raise SourceError(
                "NetSuite rejected the credentials (401): the TBA token is revoked or the"
                " realm/base URL is wrong"
            )
        if response.status_code == 403:
            raise SourceError("NetSuite refused the query (403): the token's role lacks SuiteQL access")
        if response.status_code != 200:
            raise SourceError(
                f"NetSuite answered {response.status_code} for a SuiteQL query: "
                f"{response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise SourceError(f"NetSuite returned no JSON: {error}") from error

    # -- reading ------------------------------------------------------------

    def query(self, statement: str, limit: int | None = None) -> tuple[list[dict[str, Any]], bool]:
        """One page: (rows, has_more). Adds ``FETCH FIRST`` unless the statement has it."""
        text = statement.strip().rstrip(";")
        if not re.search(r"\bfetch\s+first\b", text, flags=re.IGNORECASE) and not re.search(
            r"\blimit\b", text, flags=re.IGNORECASE
        ):
            size = int(limit or DEFAULT_PAGE_SIZE)
            text = f"{text} FETCH FIRST {size} ROWS ONLY"
        payload = self._post(text)
        rows = payload.get("items") or []
        has_more = bool(payload.get("hasMore"))
        return rows, has_more

    def query_all(self, statement: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Every row of one statement, or a refusal that names the fix.

        A statement with more rows than the page is a paging bug waiting to
        happen (SuiteQL ignores OFFSET, so "page 2" is page 1 forever). Callers
        that can exceed a page use a keyset cursor instead.
        """
        rows, has_more = self.query(statement, limit=limit)
        if has_more:
            size = int(limit or DEFAULT_PAGE_SIZE)
            raise SourceError(
                f"statement returned more than {size} rows; SuiteQL ignores OFFSET, so page"
                " it with a keyset (`WHERE id > :last ORDER BY id`) instead of asking for"
                " a second page"
            )
        return rows


def client_from_env(env: dict[str, str] | None = None) -> SuiteQLClient:
    """A client from ``NETSUITE_*``, or a SourceError naming what is missing."""
    return SuiteQLClient.from_env(env)


# ---------------------------------------------------------------------------
# Facet labels: list id -> name, cached per company
# ---------------------------------------------------------------------------

def ensure_facet_cache(conn: Any) -> None:
    """Create the label cache if a script reached here before ``init_db``."""
    from config import IS_POSTGRES

    if IS_POSTGRES:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS netsuite_facet_values (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                value_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, field, value_id)
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS netsuite_facet_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                value_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, field, value_id)
            )
            """
        )


@dataclass
class FacetResolution:
    """Labels found for facet list ids, and the ids that resolved to nothing."""

    labels: dict[str, dict[str, str]] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)
    cached: int = 0
    fetched: int = 0

    def label(self, facet: str, value_id: Any) -> str:
        key = _clean(value_id)
        if not key:
            return ""
        return self.labels.get(facet, {}).get(key, "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved": sum(len(values) for values in self.labels.values()),
            "cached": self.cached,
            "fetched": self.fetched,
            "missing": {facet: len(ids) for facet, ids in self.missing.items() if ids},
        }


def facet_value_ids(rows: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """{facet: {list id}} for the values these item rows carry."""
    values: dict[str, set[str]] = {facet: set() for facet in FACET_COLUMNS}
    for row in rows:
        for facet, column in FACET_COLUMNS.items():
            value = _clean(row.get(column) or row.get(facet))
            if value:
                values[facet].add(value)
    return values


def _lookup(client: Any, facet: str, ids: list[str], batch: int = 200) -> dict[str, str]:
    """{list id: label} from the facet's own list table. Unknown ids are absent."""
    table = FACET_VALUE_TABLES[facet]
    found: dict[str, str] = {}
    for start in range(0, len(ids), batch):
        chunk = [value for value in ids[start : start + batch] if value.isdigit()]
        if not chunk:
            continue
        rows = client.query_all(
            f"SELECT id, name FROM {table} WHERE id IN ({', '.join(chunk)})",
            limit=len(chunk) + 5,
        )
        for row in rows:
            value_id = _clean(row.get("id"))
            label = _clean(row.get("name"))
            if value_id and label:
                found[value_id] = label
    return found


def fetch_labels(
    client: Any, values: dict[str, Iterable[Any]], batch: int = 200
) -> FacetResolution:
    """Labels straight from the account, with no cache and no database.

    For a read-only report: a sample of twenty rows must not create a table or
    write a row anywhere.
    """
    resolution = FacetResolution()
    for facet in FACET_COLUMNS:
        wanted = {_clean(value) for value in values.get(facet, []) if _clean(value)}
        if not wanted:
            continue
        found = _lookup(client, facet, sorted(wanted), batch=batch)
        resolution.labels[facet] = {value: found[value] for value in wanted if value in found}
        resolution.fetched += len(resolution.labels[facet])
        unresolved = [value for value in sorted(wanted) if value not in found]
        if unresolved:
            resolution.missing[facet] = unresolved
    return resolution


def resolve_facets(
    conn: Any,
    client: Any,
    company_id: int,
    values: dict[str, Iterable[Any]],
    batch: int = 200,
) -> FacetResolution:
    """Labels with the per-company cache in front of the account.

    The cache is keyed by company because the four lists belong to one NetSuite
    account, and a second company dataset may point at another. Only found
    labels are cached: an id that resolves to nothing is reported every run, so
    a list that later gains a value starts working without a cache flush.
    Unresolvable ids stay blank — they are never printed as a number.
    """
    from database import execute

    ensure_facet_cache(conn)
    resolution = FacetResolution()
    rows = execute(
        conn, "SELECT field, value_id, label FROM netsuite_facet_values WHERE company_id = ?", (company_id,)
    ).fetchall()
    cache: dict[str, dict[str, str]] = {facet: {} for facet in FACET_COLUMNS}
    for row in rows:
        facet = str(row["field"])
        if facet in cache:
            cache[facet][str(row["value_id"])] = str(row["label"] or "")

    for facet in FACET_COLUMNS:
        wanted = {_clean(value) for value in values.get(facet, []) if _clean(value)}
        if not wanted:
            continue
        known = {value: label for value, label in cache[facet].items() if label}
        resolution.labels[facet] = {value: known[value] for value in wanted if value in known}
        resolution.cached += len(resolution.labels[facet])

        missing = sorted(value for value in wanted if value not in known)
        if not missing:
            continue
        found = _lookup(client, facet, missing, batch=batch)
        for value, label in found.items():
            resolution.labels[facet][value] = label
            resolution.fetched += 1
            _store_label(conn, company_id, facet, value, label)
        unresolved = [value for value in missing if value not in found]
        if unresolved:
            resolution.missing[facet] = unresolved
    return resolution


def _store_label(conn: Any, company_id: int, facet: str, value_id: str, label: str) -> None:
    """Cache one label. Update first, so a value missed before is corrected in place."""
    from database import execute

    cursor = execute(
        conn,
        "UPDATE netsuite_facet_values SET label = ?, updated_at = CURRENT_TIMESTAMP"
        " WHERE company_id = ? AND field = ? AND value_id = ?",
        (label, company_id, facet, value_id),
    )
    if cursor.rowcount:
        return
    execute(
        conn,
        "INSERT INTO netsuite_facet_values (company_id, field, value_id, label) VALUES (?, ?, ?, ?)",
        (company_id, facet, value_id, label),
    )
