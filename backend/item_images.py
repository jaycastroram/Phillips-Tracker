"""NetSuite item images: the URL always comes from an item record.

Every page in this app renders an image from a stored URL field — tracker rows
read ``tracker_items.visual_reference``, catalog and published period rows read
``image_url`` — and that string is the whole contract with the frontend
(``isImageReference``, frontend/src/utils.ts:51-63). This module is the only
code that decides what goes into those fields.

It does not build URLs, and the reason is in the data. A SuiteCommerce media
file is named ``<site id>_<file>_1.<ext>``, and the middle segment is whatever
the upload was called, not the item id:

* ``4006_10001_1.png`` — numeric, the shape a naive template assumes;
* ``4006_60047-RAIL_3_1.png`` — a SKU-like file name with a variant suffix;
* ``/core/media/media.nl?id=3184_69859_1.png`` — the live page's own form.

Same catalog, three different middle segments. So an item id cannot be turned
into a URL: ``netsuite_image_url`` is a stub that always returns ``''``.

The URL is read instead, from ``netsuite_item_images`` — one row per (company,
NetSuite internal id), written only from a record set NetSuite itself produced:
``scripts/import_item_image_records.py`` imports an export today, and a RESTlet
sync can fill the same table later without touching anything here.

Two shapes of the same file appear in records, and only one is usable here:

* ``/core/media/media.nl?id=<file>&resizeid=11&resizew=225&resizeh=255`` — the
  resize parameters are theme config and the endpoint answers 500 without a
  shopper session (verified 2026-11-17), and the path has no extension, so the
  frontend's render gate rejects it;
* ``<host>/images/<file>`` — the file itself, public and anonymous, extension in
  the path (verified 200 for every seeded row).

``media_reference`` maps the first onto the second. Image size stays a
frontend/CSS decision; a tighter thumbnail is a different Image Size ID, not
part of the stored URL.

Item ids are NetSuite **internal ids**, which are numeric. ``60047-RAIL`` is a
file name or a SKU, and it is rejected here: the lookup keys off the internal
id, so a SKU in that field resolves to nothing at all.
"""

from __future__ import annotations

import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from config import NETSUITE_IMAGE_VERIFY_TIMEOUT
from database import execute, utc_now

# NetSuite internal ids are numeric. A guard with a wider shape would let the
# file-name segment of a URL (``60047-RAIL``) look like a key.
INTERNAL_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")

# Mirrors isImageReference (frontend/src/utils.ts:51-63): Cloudinary host, or a
# supported extension on the URL path. Anything else renders as brand initials,
# so a URL that fails this is invisible even though the data is right.
SUPPORTED_IMAGE_EXTENSIONS = (".avif", ".gif", ".jpg", ".jpeg", ".png", ".webp")
CLOUDINARY_HOST_FRAGMENT = "res.cloudinary.com"

MEDIA_ENDPOINT_FRAGMENT = "/core/media/media.nl"
IMAGES_PATH_FRAGMENT = "/images/"


class _NoRedirect(HTTPRedirectHandler):
    """NetSuite answers 302 to its login page for a missing file — never follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def normalize_item_id(value: Any) -> str:
    """The internal id as a string, or '' when the value is not one."""
    text = str(value or "").strip()
    if not INTERNAL_ID_PATTERN.match(text):
        return ""
    return text


# ---------------------------------------------------------------------------
# Record URLs
# ---------------------------------------------------------------------------


def media_reference(raw_url: Any, host: str = "") -> dict[str, str] | None:
    """Parse one NetSuite media reference into its file name and image URL.

    Accepts both forms a record can carry — ``/images/<file>`` and
    ``/core/media/media.nl?id=<file>`` — absolute or relative, and returns
    ``{'media_file', 'image_url', 'host'}``. ``image_url`` is '' when the
    reference was relative and no host was given: the file name is still worth
    storing so a later host answer can point it somewhere without another
    export.

    Returns None for anything that is not a NetSuite media reference (a
    Cloudinary upload, an external URL, a non-image file).
    """
    text = str(raw_url or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None

    path = unquote(parsed.path or "")
    if MEDIA_ENDPOINT_FRAGMENT in path:
        filename = unquote(parse_qs(parsed.query).get("id", [""])[0]).strip()
        filename = filename.rsplit("/", 1)[-1]
    elif IMAGES_PATH_FRAGMENT in path:
        filename = path.rsplit("/", 1)[-1]
    else:
        return None

    if not filename or not filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
        return None
    if "/" in filename:
        return None

    base = str(host or "").strip().rstrip("/")
    if not base and parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "media_file": filename,
        "image_url": f"{base}/images/{filename}" if base else "",
        "host": base,
    }


def media_file_from_url(url: Any) -> str:
    """The media file name a stored URL points at, or ''."""
    reference = media_reference(url)
    return reference["media_file"] if reference else ""


def is_netsuite_media_url(url: Any) -> bool:
    """True when the stored URL is a NetSuite media reference (either form)."""
    return media_reference(url) is not None


# ---------------------------------------------------------------------------
# The record store
# ---------------------------------------------------------------------------


def record_image_url(conn: Any, company_id: Any, internal_id: Any) -> str:
    """The image URL imported for this (company, internal id), or ''.

    This is the only way the app gets a NetSuite image URL: no network call and
    no formatting. A missing row means the id has no record yet, and the caller
    leaves the field blank rather than guessing.
    """
    item_id = normalize_item_id(internal_id)
    if not item_id or company_id in (None, ""):
        return ""
    row = execute(
        conn,
        "SELECT image_url FROM netsuite_item_images WHERE company_id = ? AND internal_id = ?",
        (int(company_id), item_id),
    ).fetchone()
    if row is None:
        return ""
    return str(row["image_url"] or "").strip()


def store_record(
    conn: Any,
    company_id: int,
    internal_id: Any,
    *,
    media_file: str = "",
    image_url: str = "",
    item_name: str = "",
    source: str = "",
) -> str:
    """Upsert one imported record; returns 'inserted', 'updated' or 'unchanged'.

    Records are the truth about what NetSuite has, so an existing row is
    rewritten — except that an empty ``image_url`` never erases a stored one.
    That is what makes a host-less import safe: the file name lands, the URL
    follows on the next run once the host is known.
    """
    item_id = normalize_item_id(internal_id)
    if not item_id:
        raise ValueError(f"not a NetSuite internal id: {internal_id!r}")
    media_file = str(media_file or "").strip()
    image_url = str(image_url or "").strip()
    item_name = str(item_name or "").strip()
    source = str(source or "").strip()

    existing = execute(
        conn,
        "SELECT id, media_file, image_url, item_name FROM netsuite_item_images"
        " WHERE company_id = ? AND internal_id = ?",
        (int(company_id), item_id),
    ).fetchone()
    now = utc_now()

    if existing is None:
        execute(
            conn,
            """
            INSERT INTO netsuite_item_images (
                company_id, internal_id, media_file, image_url, item_name, source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(company_id), item_id, media_file, image_url, item_name, source, now, now),
        )
        return "inserted"

    new_url = image_url or str(existing["image_url"] or "")
    new_file = media_file or str(existing["media_file"] or "")
    new_name = item_name or str(existing["item_name"] or "")
    if (
        new_url == str(existing["image_url"] or "")
        and new_file == str(existing["media_file"] or "")
        and new_name == str(existing["item_name"] or "")
    ):
        return "unchanged"

    execute(
        conn,
        """
        UPDATE netsuite_item_images
        SET media_file = ?, image_url = ?, item_name = ?, source = ?, updated_at = ?
        WHERE id = ?
        """,
        (new_file, new_url, new_name, source, now, existing["id"]),
    )
    return "updated"


# ---------------------------------------------------------------------------
# The stub, kept by name so nothing calls a URL builder
# ---------------------------------------------------------------------------


def netsuite_image_url(company: Any, item_id: Any, ext: str = "") -> str:
    """STUB — never formats a URL from an item id, and always returns ''.

    The media file name cannot be derived from an internal id (see the module
    docstring), so there is nothing to build. Callers read the imported URL with
    ``record_image_url``; the parameters are kept only so a caller written
    against the old signature fails loudly here instead of quietly making up a
    URL somewhere else.
    """
    return ""


def fill_blank(current_url: Any, record_url: Any) -> str:
    """Blank-only fill: a stored value always wins over the record's."""
    existing = str(current_url or "").strip()
    if existing:
        return existing
    return str(record_url or "").strip()


# ---------------------------------------------------------------------------
# Verification helpers (checks and the import's --verify)
# ---------------------------------------------------------------------------


def verify_image_url(url: Any, timeout: float | None = None) -> bool:
    """True only when the URL itself answers 200 with an image body.

    A 302 (NetSuite's login page for a file that does not exist) is a miss, not
    a redirect to follow.
    """
    text = str(url or "").strip()
    if not text:
        return False
    opener = build_opener(_NoRedirect())
    request = Request(text, method="HEAD", headers={"User-Agent": "smartbuy-image-check/1.0"})
    try:
        with opener.open(request, timeout=timeout or NETSUITE_IMAGE_VERIFY_TIMEOUT) as response:
            if response.status != 200:
                return False
            return str(response.headers.get("Content-Type", "")).lower().startswith("image/")
    except (HTTPError, URLError, socket.timeout, OSError, ValueError):
        return False


def passes_render_gate(url: Any) -> bool:
    """Mirror of isImageReference so a check can assert the gate's verdict."""
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    if CLOUDINARY_HOST_FRAGMENT in (parsed.hostname or ""):
        return True
    path = parsed.path.lower()
    return path.endswith(SUPPORTED_IMAGE_EXTENSIONS)
