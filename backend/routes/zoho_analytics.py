from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from routes.auth import get_current_user
from zoho_analytics import (
    ZohoAnalyticsError,
    config_status,
    is_configured,
    list_workspaces,
    refresh_access_token,
)

router = APIRouter(prefix="/api/zoho-analytics", tags=["zoho-analytics"])


@router.get("/status")
def zoho_analytics_status(user: sqlite3.Row = Depends(get_current_user)) -> dict:
    del user
    payload = config_status()
    if not payload["configured"]:
        return {**payload, "ok": False, "message": "Missing Zoho Analytics env vars in backend/.env"}
    try:
        refresh_access_token()
        return {**payload, "ok": True, "message": "Zoho Analytics credentials look valid"}
    except ZohoAnalyticsError as exc:
        return {**payload, "ok": False, "message": str(exc)}


@router.get("/workspaces")
def zoho_analytics_workspaces(user: sqlite3.Row = Depends(get_current_user)) -> dict:
    del user
    if not is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Zoho Analytics is not configured",
        )
    try:
        return {"workspaces": list_workspaces()}
    except ZohoAnalyticsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
