"""Draw the Buy Book PDF: one cover page, then six items to a page.

The geometry is fixed on purpose - Letter landscape, 25.2 pt margins, a 78 pt
heading band, two 348.8 pt columns with a 44 pt gutter, three 126 pt rows at a
158.5 pt pitch - so the page count is ``1 + sum(ceil(section_items / 6))`` and
can be asserted. Text is truncated to its line budget and never allowed to push
a cell, so a long item name cannot move an item onto another page.

The render consumes what the layered page-break pass derived - the order, the
derived ``page_break_before`` and each item's ``group_path`` - and never sorts
or groups again. The cover lives in one function so artwork can replace that
block later without touching the route.
"""

from __future__ import annotations

import io
import math
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from buy_book_images import ITEM_IMAGE_PX, load_images

# The page, in points. Letter landscape.
PAGE_WIDTH = 792.0
PAGE_HEIGHT = 612.0
MARGIN = 25.2
HEADER_BAND = 78.0
COLUMN_WIDTH = 348.8
COLUMN_GUTTER = 44.0
ROW_HEIGHT = 126.0
ROW_PITCH = 158.5
TILE_MAX_WIDTH = 116.0
TILE_MAX_HEIGHT = 104.0
FOOTER_Y = 14.0

# Six to a page: two columns, three rows.
ITEMS_PER_ROW = 2
ROWS_PER_PAGE = 3
ITEMS_PER_PAGE = ITEMS_PER_ROW * ROWS_PER_PAGE

CONTENT_TOP = PAGE_HEIGHT - MARGIN
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
HEADER_BOTTOM = CONTENT_TOP - HEADER_BAND

# The image size control maps onto the fixed cell; "full" cannot exceed it.
TILE_SIZES = {"small": 64.0, "medium": 88.0, "large": 116.0, "full": 116.0}
IMAGE_CIRCLE_DIAMETER = 70.0

# Line budgets: a name is 48 characters over two lines, a description 110 over
# three. A clipped line ends in an ellipsis rather than shrinking the type.
NAME_CHARS = 48
NAME_LINES = 2
DESCRIPTION_CHARS = 110
DESCRIPTION_LINES = 3

MAX_TITLE_SIZE = 36.0
MIN_TITLE_SIZE = 24.0
HEADING_SIZE = 16.0

INK = HexColor("#111827")
MUTED = HexColor("#6b7280")
FAINT = HexColor("#9ca3af")
RULE = HexColor("#d1d5db")
CARD_RULE = HexColor("#e5e7eb")
WHITE = white

FACET_LABELS = {
    "brand": "Brand",
    "channel": "Channel",
    "key_program": "Program",
    "product_type": "Type",
}

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONT_REGULAR = ASSETS_DIR / "fonts" / "DejaVuSans.ttf"
FONT_BOLD = ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf"
BUY_BOOK_MARK = ASSETS_DIR / "buy-book-mark.png"

_INITIALS_BG = ("#1f2937", "#334155", "#4c1d95", "#065f46", "#7c2d12", "#1e3a8a")
_FONTS: dict[str, str] | None = None
_MARK: Any = None


def _fonts() -> dict[str, str]:
    """DejaVu, so an item name with an accent or a registered mark prints.

    A missing asset file falls back to Helvetica rather than failing the book.
    """
    global _FONTS
    if _FONTS is not None:
        return _FONTS
    regular, bold = "Helvetica", "Helvetica-Bold"
    try:
        if FONT_REGULAR.exists() and FONT_BOLD.exists():
            pdfmetrics.registerFont(TTFont("BuyBookSans", str(FONT_REGULAR)))
            pdfmetrics.registerFont(TTFont("BuyBookSans-Bold", str(FONT_BOLD)))
            regular, bold = "BuyBookSans", "BuyBookSans-Bold"
    except Exception:
        regular, bold = "Helvetica", "Helvetica-Bold"
    _FONTS = {"regular": regular, "bold": bold}
    return _FONTS


def _regular() -> str:
    return _fonts()["regular"]


def _bold() -> str:
    return _fonts()["bold"]


def _trim_mark(image: Any) -> Any:
    """Keep only the SmartBuy buy-book lockup at the right of the bundled art.

    The asset is the book's whole header strip - Tito's, LALO, then "BUY BOOK |
    SMARTBUY" - so the left-hand brand artwork is dropped and the right-hand
    lockup is what prints. The widest transparent gap is the separator.
    """
    rgba = image.convert("RGBA")
    width, height = rgba.size
    alpha = rgba.getchannel("A").load()
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x in range(width):
        inked = any(alpha[x, y] > 16 for y in range(height))
        if inked and start is None:
            start = x
        elif not inked and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, width))
    if not runs:
        return rgba
    gap = max(
        (right[0] - left[1] for left, right in zip(runs, runs[1:])),
        default=0,
    )
    if gap < width * 0.1:
        return rgba
    for left, right in zip(runs, runs[1:]):
        if right[0] - left[1] == gap:
            tail = rgba.crop((right[0], 0, width, height))
            box = tail.getbbox()
            return tail.crop(box) if box else tail
    return rgba


def _load_mark() -> Any:
    global _MARK
    if _MARK is None:
        try:
            from PIL import Image

            image = Image.open(BUY_BOOK_MARK)
            image.load()
            _MARK = _trim_mark(image)
        except Exception:
            _MARK = False
    return _MARK or None


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _width(text: str, font: str, size: float) -> float:
    try:
        return pdfmetrics.stringWidth(text, font, size)
    except Exception:
        return len(text) * size * 0.55


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _truncate(text: Any, limit: int) -> str:
    clean = _one_line(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _wrap(text: Any, font: str, size: float, width: float, max_lines: int) -> list[str]:
    """Break one line of text into at most ``max_lines`` drawn lines."""
    words = _one_line(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    index = 0
    while index < len(words) and len(lines) < max_lines:
        candidate = f"{current} {words[index]}".strip()
        if current and _width(candidate, font, size) > width:
            lines.append(current)
            current = ""
            continue
        current = candidate
        index += 1
    overflowing = index < len(words) or (bool(current) and len(lines) >= max_lines)
    if current and len(lines) < max_lines:
        lines.append(current)
    if overflowing and lines:
        last = lines[-1]
        while last and _width(last + "…", font, size) > width:
            last = last[:-1].rstrip()
        lines[-1] = f"{last}…" if last else "…"
    return lines[:max_lines]


def _fit_size(text: str, font: str, width: float, start: float, minimum: float) -> float:
    size = start
    while size > minimum and _width(text, font, size) > width:
        size -= 0.5
    return size


def _right_text(pdf: canvas.Canvas, x: float, y: float, text: str, font: str, size: float, color) -> None:
    pdf.setFont(font, size)
    pdf.setFillColor(color)
    pdf.drawRightString(x, y, text)


def _label_value(pdf: canvas.Canvas, x: float, y: float, label: str, value: str) -> None:
    pdf.setFont(_bold(), 8.0)
    pdf.setFillColor(FAINT)
    pdf.drawString(x, y, label.upper())
    pdf.setFont(_regular(), 12.0)
    pdf.setFillColor(INK)
    pdf.drawString(x, y - 16, _truncate(value, 56))


# ---------------------------------------------------------------------------
# Pagination and headings
# ---------------------------------------------------------------------------

def paginate_items(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split the derived order into pages.

    A section opens a page and a full page closes one; nothing else breaks, so
    the derived flag is the only page break the book honours.
    """
    pages: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for item in items:
        if current and (bool(item.get("page_break_before")) or len(current) >= ITEMS_PER_PAGE):
            pages.append(current)
            current = []
        current.append(item)
    if current:
        pages.append(current)
    return pages


def page_heading(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The longest common prefix of the page's group paths, as (facet, label).

    A continuation page repeats the heading exactly because its items share
    the prefix; a page that mixes two programs only names the brand.
    """
    paths: list[list[tuple[str, str]]] = []
    for item in items:
        path = item.get("group_path") or []
        paths.append([(str(entry.get("field", "")), str(entry.get("label", ""))) for entry in path])
    if not paths or not paths[0]:
        return []
    prefix = list(paths[0])
    for other in paths[1:]:
        kept: list[tuple[str, str]] = []
        for position, entry in enumerate(prefix):
            if position >= len(other) or other[position] != entry:
                break
            kept.append(entry)
        prefix = kept
        if not prefix:
            break
    return prefix


def heading_text(heading: list[tuple[str, str]]) -> str:
    return " › ".join(f"{FACET_LABELS.get(field, str(field).title())}: {label}" for field, label in heading)


# ---------------------------------------------------------------------------
# The cover
# ---------------------------------------------------------------------------

def _draw_cover(pdf: canvas.Canvas, cover: dict[str, str], images: dict[str, Any], page_label: str) -> None:
    top = CONTENT_TOP
    banner = images.get(str(cover.get("banner_url") or "").strip())
    if banner is not None:
        band = 64.0
        try:
            ratio = banner.width / float(banner.height or 1)
            draw_width = max(PAGE_WIDTH, band * ratio)
            draw_height = draw_width / ratio
            pdf.saveState()
            clip = pdf.beginPath()
            clip.rect(0, PAGE_HEIGHT - band, PAGE_WIDTH, band)
            pdf.clipPath(clip, stroke=0, fill=0)
            pdf.drawImage(
                ImageReader(banner),
                (PAGE_WIDTH - draw_width) / 2,
                PAGE_HEIGHT - band - (draw_height - band) / 2,
                width=draw_width,
                height=draw_height,
                mask="auto",
            )
            pdf.restoreState()
            top = PAGE_HEIGHT - band - MARGIN
        except Exception:
            pass

    # The logo slot: a real logo, or the company name set as a wordmark.
    logo = images.get(str(cover.get("logo_url") or "").strip())
    logo_w, logo_h = 120.0, 60.0
    logo_y = top - 12 - logo_h
    drawn = False
    if logo is not None:
        try:
            pdf.drawImage(
                ImageReader(logo),
                MARGIN,
                logo_y,
                width=logo_w,
                height=logo_h,
                preserveAspectRatio=True,
                anchor="sw",
                mask="auto",
            )
            drawn = True
        except Exception:
            drawn = False
    if not drawn:
        wordmark = _truncate(cover.get("company_name") or "SmartBuy", 32)
        size = _fit_size(wordmark, _bold(), 220.0, 20.0, 11.0)
        pdf.setFont(_bold(), size)
        pdf.setFillColor(INK)
        pdf.drawString(MARGIN, logo_y + logo_h / 2 - size * 0.34, wordmark)

    mark = _load_mark()
    if mark is not None:
        try:
            mark_h_target = 26.0
            mark_h = mark_h_target
            mark_w = mark_h * mark.width / float(mark.height or 1)
            pdf.drawImage(
                ImageReader(mark),
                PAGE_WIDTH - MARGIN - mark_w,
                logo_y + logo_h / 2 - mark_h / 2,
                width=mark_w,
                height=mark_h,
                mask="auto",
            )
        except Exception:
            pass

    y = logo_y - 34
    # With the wordmark in the logo slot the name is already on the page, so
    # the eyebrow only prints when a real logo took that slot.
    if drawn:
        eyebrow = _one_line(cover.get("company_name") or "SmartBuy").upper()
        pdf.setFont(_bold(), 9.0)
        pdf.setFillColor(FAINT)
        pdf.drawString(MARGIN, y, _truncate(eyebrow, 54))

    title = _one_line(cover.get("title") or "Buy Book")
    title_size = _fit_size(title, _bold(), CONTENT_WIDTH, MAX_TITLE_SIZE, MIN_TITLE_SIZE)
    y -= title_size + 12
    pdf.setFont(_bold(), title_size)
    pdf.setFillColor(INK)
    pdf.drawString(MARGIN, y, title)

    subtitle = _one_line(cover.get("subtitle"))
    if subtitle:
        y -= 20
        pdf.setFont(_regular(), 12.0)
        pdf.setFillColor(MUTED)
        pdf.drawString(MARGIN, y, _truncate(subtitle, 120))

    y -= 22
    pdf.setStrokeColor(RULE)
    pdf.setLineWidth(0.8)
    pdf.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)

    # Market / Period on the left, Date / Prepared for on the right, then the
    # author underneath. An empty buyer disappears with its whole line.
    row_y = y - 28
    for label, value in (("Market", cover.get("market")), ("Period", cover.get("period_name"))):
        if _one_line(value):
            _label_value(pdf, MARGIN, row_y, label, str(value))
            row_y -= 52
    right_y = y - 28
    for label, value in (("Date", cover.get("date_label")), ("Prepared for", cover.get("buyer"))):
        if _one_line(value):
            _label_value(pdf, MARGIN + COLUMN_WIDTH + COLUMN_GUTTER, right_y, label, str(value))
            right_y -= 52
    if _one_line(cover.get("author")):
        _label_value(pdf, MARGIN, y - 28 - 104, "Prepared by", str(cover.get("author")))

    _draw_footer(pdf, cover, page_label)


def _draw_footer(pdf: canvas.Canvas, cover: dict[str, str], page_label: str) -> None:
    note = _one_line(cover.get("footer_note"))
    if note:
        pdf.setFont(_regular(), 8.0)
        pdf.setFillColor(FAINT)
        pdf.drawString(MARGIN, FOOTER_Y, _truncate(note, 90))
    _right_text(pdf, PAGE_WIDTH - MARGIN, FOOTER_Y, page_label, _regular(), 8.0, FAINT)


# ---------------------------------------------------------------------------
# Item pages
# ---------------------------------------------------------------------------

def _initials(item: dict[str, Any]) -> str:
    brand = _one_line(item.get("brand"))
    letters = "".join(ch for ch in brand if ch.isalnum())[:2].upper()
    return letters or "SB"


def _circle_color(initials: str) -> str:
    return _INITIALS_BG[sum(ord(ch) for ch in initials) % len(_INITIALS_BG)]


def _draw_tile(pdf: canvas.Canvas, item: dict[str, Any], x: float, y_top: float, images: dict[str, Any]) -> float:
    """Draw the item's picture, or its brand initials, and return the text x."""
    size_name = str(item.get("image_display_size") or "medium").lower()
    tile_size = TILE_SIZES.get(size_name, TILE_SIZES["medium"])
    tile_w = min(tile_size, TILE_MAX_WIDTH)
    tile_h = min(tile_size, TILE_MAX_HEIGHT)
    image = images.get(str(item.get("image_url") or "").strip())

    if image is not None:
        try:
            ratio = image.width / float(image.height or 1)
            if str(item.get("image_fit") or "contain").lower() == "cover":
                pdf.saveState()
                clip = pdf.beginPath()
                clip.rect(x, y_top - tile_h, tile_w, tile_h)
                pdf.clipPath(clip, stroke=0, fill=0)
                if tile_w / ratio >= tile_h:
                    draw_w, draw_h = tile_w, tile_w / ratio
                else:
                    draw_w, draw_h = tile_h * ratio, tile_h
                pdf.drawImage(
                    ImageReader(image),
                    x + (tile_w - draw_w) / 2,
                    y_top - tile_h + (tile_h - draw_h) / 2,
                    width=draw_w,
                    height=draw_h,
                    mask="auto",
                )
                pdf.restoreState()
            else:
                draw_w = min(tile_w, tile_h * ratio)
                draw_h = draw_w / ratio
                pdf.drawImage(
                    ImageReader(image),
                    x + (tile_w - draw_w) / 2,
                    y_top - tile_h + (tile_h - draw_h) / 2,
                    width=draw_w,
                    height=draw_h,
                    mask="auto",
                )
            return x + tile_w + 10
        except Exception:
            pass

    # No usable picture: a brand-initial circle, never a gap.
    radius = IMAGE_CIRCLE_DIAMETER / 2
    centre_x = x + tile_w / 2
    centre_y = y_top - tile_h / 2
    initials = _initials(item)
    pdf.setFillColor(HexColor(_circle_color(initials)))
    pdf.circle(centre_x, centre_y, radius, stroke=0, fill=1)
    pdf.setFillColor(WHITE)
    pdf.setFont(_bold(), 20.0)
    pdf.drawCentredString(centre_x, centre_y - 7, initials)
    return x + tile_w + 10


def _draw_item(pdf: canvas.Canvas, item: dict[str, Any], x: float, y_top: float, images: dict[str, Any]) -> None:
    pdf.setStrokeColor(CARD_RULE)
    pdf.setLineWidth(0.5)
    pdf.roundRect(x, y_top - ROW_HEIGHT, COLUMN_WIDTH, ROW_HEIGHT, 3, stroke=1, fill=0)

    text_x = _draw_tile(pdf, item, x + 10, y_top - 8, images)
    text_width = x + COLUMN_WIDTH - 10 - text_x
    y = y_top - 22

    name = _truncate(item.get("item_name") or "Item", NAME_CHARS)
    pdf.setFont(_bold(), 10.5)
    pdf.setFillColor(INK)
    for line in _wrap(name, _bold(), 10.5, text_width, NAME_LINES):
        pdf.drawString(text_x, y, line)
        y -= 12.5

    description = _truncate(item.get("item_description"), DESCRIPTION_CHARS)
    if description:
        pdf.setFont(_regular(), 8.0)
        pdf.setFillColor(MUTED)
        for line in _wrap(description, _regular(), 8.0, text_width, DESCRIPTION_LINES):
            pdf.drawString(text_x, y, line)
            y -= 10.0

    parts = []
    if _one_line(item.get("uom")):
        parts.append(f"UOM: {_one_line(item.get('uom'))}")
    if _one_line(item.get("price")):
        parts.append(f"Price: {_one_line(item.get('price'))}")
    if parts:
        pdf.setFont(_regular(), 8.0)
        pdf.setFillColor(FAINT)
        pdf.drawString(text_x, y - 2, _truncate("  ·  ".join(parts), 44))


def _draw_heading(pdf: canvas.Canvas, heading: list[tuple[str, str]], cover: dict[str, str]) -> None:
    period = _one_line(cover.get("period_name"))
    window = _one_line(cover.get("window_line"))
    right_x = PAGE_WIDTH - MARGIN
    if period:
        _right_text(pdf, right_x, CONTENT_TOP - 14, _truncate(period, 46), _bold(), 9.0, MUTED)
    if window:
        _right_text(pdf, right_x, CONTENT_TOP - 26, _truncate(window, 54), _regular(), 8.0, FAINT)

    y = CONTENT_TOP - 20
    if heading:
        field, label = heading[0]
        text = f"{FACET_LABELS.get(field, str(field).title())}: {label}"
        size = _fit_size(text, _bold(), CONTENT_WIDTH - 240, HEADING_SIZE, 11.0)
        pdf.setFont(_bold(), size)
        pdf.setFillColor(INK)
        pdf.drawString(MARGIN, y, text)
        y -= size + 8
        for field, label in heading[1:]:
            text = f"{FACET_LABELS.get(field, str(field).title())}: {label}"
            pdf.setFont(_regular(), 9.0)
            pdf.setFillColor(MUTED)
            pdf.drawString(MARGIN, y, _truncate(text, 96))
            y -= 11

    pdf.setStrokeColor(RULE)
    pdf.setLineWidth(0.6)
    pdf.line(MARGIN, HEADER_BOTTOM, PAGE_WIDTH - MARGIN, HEADER_BOTTOM)


def _draw_item_page(
    pdf: canvas.Canvas,
    page_items: list[dict[str, Any]],
    cover: dict[str, str],
    images: dict[str, Any],
    page_label: str,
) -> None:
    _draw_heading(pdf, page_heading(page_items), cover)
    for index, item in enumerate(page_items):
        row = index // ITEMS_PER_ROW
        column = index % ITEMS_PER_ROW
        x = MARGIN + column * (COLUMN_WIDTH + COLUMN_GUTTER)
        y_top = HEADER_BOTTOM - 10 - row * ROW_PITCH
        _draw_item(pdf, item, x, y_top, images)
    _draw_footer(pdf, cover, page_label)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def render_buy_book_pdf(
    *,
    items: list[dict[str, Any]],
    cover: dict[str, str],
    generated_at: datetime,
) -> bytes:
    """Render the whole book from the derived items and the resolved cover."""
    del generated_at  # the cover already carries its label and date
    _fonts()
    pages = paginate_items(items)
    total_pages = 1 + len(pages)

    images = load_images([str(item.get("image_url") or "").strip() for item in items], max_px=ITEM_IMAGE_PX)
    cover_urls = [
        url
        for url in (str(cover.get("logo_url") or "").strip(), str(cover.get("banner_url") or "").strip())
        if url
    ]
    images.update(load_images(cover_urls, max_px=1600))

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    period = cover.get("period_name") or ""
    pdf.setTitle(f"Buy Book — {period}".strip(" —"))
    pdf.setAuthor(cover.get("author") or "MRL Creative")
    pdf.setSubject("Buy Book")

    _draw_cover(pdf, cover, images, f"Page 1 of {total_pages}")
    pdf.showPage()

    for index, page_items in enumerate(pages, start=2):
        _draw_item_page(pdf, page_items, cover, images, f"Page {index} of {total_pages}")
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    text = "-".join(part for part in text.split("-") if part)
    return text or "book"


def buy_book_filename(company_slug: str, period_slug: str | None, generated_at: datetime) -> str:
    company = slugify(company_slug)
    period = slugify(period_slug) if period_slug else "all"
    return f"buy-book-{company}-{period}-{generated_at:%Y-%m-%d}.pdf"


def expected_page_count(items: list[dict[str, Any]]) -> int:
    """1 + sum(ceil(section_items / 6)) - the invariant the geometry guarantees."""
    if not items:
        return 1
    total = 1
    section_size = 0
    for index, item in enumerate(items):
        if index > 0 and bool(item.get("page_break_before")):
            total += math.ceil(section_size / ITEMS_PER_PAGE)
            section_size = 0
        section_size += 1
    total += math.ceil(section_size / ITEMS_PER_PAGE)
    return total
