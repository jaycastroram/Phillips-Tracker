"""The Buy Book PDF download: admin-only, one render per request.

The route does three things in order - pick the period, take the derived item
order from the layer pass, render - and it never writes anything but one audit
row. All geometry lives in ``buy_book_pdf``; all image fetching in
``buy_book_images``, so a broken picture can never fail a download.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from buy_book_pdf import buy_book_filename, expected_page_count, render_buy_book_pdf
from database import (
    apply_buy_book_layers,
    buy_book_page_break_layers,
    buy_book_pdf_rows,
    connect,
    fetch_company_by_id,
    resolve_buy_book_cover,
    resolve_buy_book_period,
    resolve_scoped_company_id,
    survey_item_to_public,
    write_audit_log,
)
from models import BuyBookPdfRequest
from routes.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["buy-book"])


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

    with connect() as conn:
        company = fetch_company_by_id(conn, company_id)
        if company is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")

        period = resolve_buy_book_period(conn, company_id, request.period_id)
        if period is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Period not found")

        rows = buy_book_pdf_rows(conn, company_id, request.item_ids)
        if not rows:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing selected")

        # Filter first, then run the derived pass on what is left: the book's
        # order and breaks are the selection's, never the layout's unfiltered ones.
        layers = buy_book_page_break_layers(conn, company_id)
        items = [survey_item_to_public(row, surface="buy-book") for row in rows]
        book_items = apply_buy_book_layers(items, layers)
        cover = resolve_buy_book_cover(company, period, user, generated_at)
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
