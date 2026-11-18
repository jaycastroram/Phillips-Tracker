"""Fetch Buy Book item images on the server, and never let one fail a book.

A Buy Book image URL is whatever the item record carries (usually a NetSuite
media file). It can answer with a picture, with the storefront's login page, or
not at all, so every failure here is expected and produces ``None`` - the
renderer draws a brand-initial circle instead.

Nothing in this module raises, and nothing is cached across renders: the cache
lives for one book, so a fixed id shows its picture the moment it is fixed.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PIL import Image

TIMEOUT_SECONDS = 3.0
MAX_BYTES = 6 * 1024 * 1024
MAX_REDIRECTS = 2
WORKERS = 8
ITEM_IMAGE_PX = 240


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects by hand, so a redirect loop cannot outlive the budget."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _fetch_bytes(url: str) -> bytes | None:
    """One image body, or None. Redirects are followed at most twice."""
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        return None
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(
            current,
            headers={"Accept": "image/*", "User-Agent": "SmartBuy-BuyBook/1.0"},
        )
        try:
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    return None
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    return None
                data = response.read(MAX_BYTES + 1)
                if not data or len(data) > MAX_BYTES:
                    return None
                # NetSuite answers an unknown media id with an HTML page and a
                # 200, so the body has to look like a picture, not a document.
                if data.lstrip()[:1] == b"<":
                    return None
                return data
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location") if error.headers else None
            try:
                error.close()
            except Exception:
                pass
            if error.code in {301, 302, 303, 307, 308} and location:
                current = urllib.parse.urljoin(current, location)
                continue
            return None
        except Exception:
            return None
    return None


def _prepare(data: bytes, max_px: int) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        if image.mode in {"P", "LA", "L", "CMYK", "1"}:
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        image.thumbnail((max_px, max_px), Image.LANCZOS)
        return image
    except Exception:
        return None


def fetch_image(url: str | None, *, max_px: int = ITEM_IMAGE_PX) -> Image.Image | None:
    if not url:
        return None
    data = _fetch_bytes(str(url).strip())
    if data is None:
        return None
    return _prepare(data, max_px)


def load_images(urls: list[str], *, max_px: int = ITEM_IMAGE_PX) -> dict[str, Any]:
    """Fetch every URL once, eight at a time, and hand back url -> image or None.

    The returned mapping is the per-render cache: an item that shares a URL
    with another item draws the same downloaded picture.
    """
    wanted: list[str] = []
    seen: set[str] = set()
    for url in urls:
        text = str(url or "").strip()
        if text and text not in seen:
            seen.add(text)
            wanted.append(text)
    if not wanted:
        return {}

    cache: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {url: pool.submit(fetch_image, url, max_px=max_px) for url in wanted}
        for url, future in futures.items():
            try:
                cache[url] = future.result()
            except Exception:
                cache[url] = None
    return cache
