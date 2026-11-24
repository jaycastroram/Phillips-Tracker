"""The Buy Book PDF download: admin-only, one render per request.

The route does three things in order - pick the period, take the derived item
order from the layer pass, render - and it never writes anything but one audit
row. All geometry lives in ``buy_book_pdf``; all image fetching in
``buy_book_images``, so a broken picture can never fail a download.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from buy_book_pdf import buy_book_filename, expected_page_count, render_buy_book_pdf
from buy_book_periods import period_by_id
from database import (
    apply_buy_book_layers,
    buy_book_page_break_layers,
    buy_book_pdf_rows,
    connect,
    execute,
    fetch_company_by_id,
    resolve_buy_book_cover,
    resolve_buy_book_period,
    buy_book_pdf_period_rows,
    resolve_scoped_company_id,
    row_value,
    survey_item_to_public,
    write_audit_log,
)
from models import BuyBookPdfRequest
from routes.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["buy-book"])


def _export_period_cover(conn: Any, company_id: int, export_period: dict) -> dict:
    """A cover-shaped period for a buy-in period that lives only in the exports.

    The stored cover of a survey period with the same name is reused when one
    exists, so a book keeps the artwork someone set up for it; otherwise the
    default cover is built from the export's name and dates.
    """
    stored = execute(
        conn,
        """
        SELECT cover_json FROM survey_periods
        WHERE company_id = ? AND lower(name) = lower(?)
        ORDER BY id DESC LIMIT 1
        """,
        (company_id, str(export_period["name"])),
    ).fetchone()
    return {
        "name": str(export_period["name"]),
        "opens_at": export_period.get("starts_on", "") or "",
        "closes_at": export_period.get("ends_on", "") or "",
        "cover_json": row_value(stored, "cover_json", "") or "",
    }


@router.post("/buy-book/pdf")
def create_buy_book_pdf(
    payload: BuyBookPdfRequest | None = Body(default=None),
    user: sqlite3.Row = Depends(require_admin),
) -> Response:
    """Render the Buy Book as a PDF for the current admin.

    No selection means the whole Buy Book (``in_buy_book``), because the admin
    layout GET returns every active item and the filter belongs here.
    """
    request = payload or BuyBookPdfRequest()
    company_id = resolve_scoped_company_id(user, request.company_id)
    generated_at = datetime.now(UTC)
    # The cover's date is the admin's day, not UTC's: 8 PM in New York is still
    # today there, and the buyer only sees the paper. The filename keeps the
    # generation instant, so it stays unique to the render.
    admin_clock = generated_at + timedelta(minutes=request.tz_offset_minutes)

    with connect() as conn:
        company = fetch_company_by_id(conn, company_id)
        if company is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

        export_period = None
        if str(request.buy_in_period or "").strip():
            export_period = period_by_id(
                str(request.buy_in_period),
                str(row_value(company, "name", "") or ""),
                str(row_value(company, "slug", "") or ""),
            )
            if export_period is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")

        if request.period_id is not None:
            # A stored period still supplies the cover when one is named.
            period = resolve_buy_book_period(conn, company_id, request.period_id)
        elif export_period is not None:
            # No stored period: label the book from the buy-in period itself,
            # reusing a stored cover when a period of the same name has one.
            period = _export_period_cover(conn, company_id, export_period)
        else:
            period = resolve_buy_book_period(conn, company_id, None)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")

        if request.item_ids is not None:
            # An explicit list is the selection: an empty one is a refusal, not
            # a request for everything.
            rows = buy_book_pdf_rows(conn, company_id, request.item_ids)
        elif export_period is not None:
            # A period book is that period's items, the same membership the page
            # reads - not the whole catalog with a period name on the cover.
            rows = buy_book_pdf_period_rows(conn, company_id, str(export_period["id"]))
        elif request.period_id is not None:
            # A stored period has no export membership of its own; try its
            # name, and keep the whole book when the exports know no such period.
            named = period_by_id(
                str(row_value(period, "name", "") or ""),
                str(row_value(company, "name", "") or ""),
                str(row_value(company, "slug", "") or ""),
            )
            rows = (
                buy_book_pdf_period_rows(conn, company_id, str(named["id"]))
                if named is not None and named.get("item_count")
                else buy_book_pdf_rows(conn, company_id, None)
            )
        else:
            rows = buy_book_pdf_rows(conn, company_id, None)
        if not rows:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing selected")

        # Filter first, then run the derived pass on what is left: the book's
        # order and breaks are the selection's, never the layout's unfiltered ones.
        layers = buy_book_page_break_layers(conn, company_id)
        items = [survey_item_to_public(row, surface="buy-book") for row in rows]
        book_items = apply_buy_book_layers(items, layers)
        cover = resolve_buy_book_cover(company, period, user, admin_clock)
        period_name = str(cover.get("period_name") or "")

    pdf_bytes = render_buy_book_pdf(items=book_items, cover=cover, generated_at=generated_at)
    filename = buy_book_filename(str(company["slug"]), period_name, generated_at)

    with connect() as conn:
        write_audit_log(
            conn,
            user,
            "buy_book_pdf",
            sheet="buy-book",
            after={
                "company": str(company["slug"]),
                "period": period_name,
                "items": len(book_items),
                "pages": expected_page_count(book_items),
                "filename": filename,
            },
            company_id=company_id,
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
