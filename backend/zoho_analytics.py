from __future__ import annotations

import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import (
    ZOHO_ACCOUNTS_SERVER_URL,
    ZOHO_ANALYTICS_CLIENT_ID,
    ZOHO_ANALYTICS_CLIENT_SECRET,
    ZOHO_ANALYTICS_ORG_ID,
    ZOHO_ANALYTICS_REFRESH_TOKEN,
    ZOHO_ANALYTICS_SERVER_URL,
    ZOHO_ANALYTICS_WORKSPACE_ID,
)

# Reuse one access token across calls. Zoho limits refresh to ~10 tokens / 10 minutes.
_token_cache: dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
}


class ZohoAnalyticsError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(
        ZOHO_ANALYTICS_CLIENT_ID
        and ZOHO_ANALYTICS_CLIENT_SECRET
        and ZOHO_ANALYTICS_REFRESH_TOKEN
    )


def config_status() -> dict[str, Any]:
    return {
        "configured": is_configured(),
        "accounts_server_url": ZOHO_ACCOUNTS_SERVER_URL,
        "analytics_server_url": ZOHO_ANALYTICS_SERVER_URL,
        "org_id_set": bool(ZOHO_ANALYTICS_ORG_ID),
        "workspace_id_set": bool(ZOHO_ANALYTICS_WORKSPACE_ID),
        "client_id_set": bool(ZOHO_ANALYTICS_CLIENT_ID),
        "client_secret_set": bool(ZOHO_ANALYTICS_CLIENT_SECRET),
        "refresh_token_set": bool(ZOHO_ANALYTICS_REFRESH_TOKEN),
    }


def _read_json(response) -> dict[str, Any]:
    import json

    raw = response.read().decode("utf-8")
    if not raw:
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        return data
    raise ZohoAnalyticsError("Unexpected Zoho response payload")


def refresh_access_token(*, force: bool = False) -> str:
    """Return a valid OAuth access token, refreshing when needed."""
    now = time.time()
    cached = _token_cache.get("access_token")
    expires_at = float(_token_cache.get("expires_at") or 0)
    if not force and cached and now < expires_at - 60:
        return str(cached)

    if not is_configured():
        raise ZohoAnalyticsError(
            "Zoho Analytics is not configured. Set ZOHO_ANALYTICS_CLIENT_ID, "
            "ZOHO_ANALYTICS_CLIENT_SECRET, and ZOHO_ANALYTICS_REFRESH_TOKEN in backend/.env"
        )

    payload = urlencode(
        {
            "refresh_token": ZOHO_ANALYTICS_REFRESH_TOKEN,
            "client_id": ZOHO_ANALYTICS_CLIENT_ID,
            "client_secret": ZOHO_ANALYTICS_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = Request(
        f"{ZOHO_ACCOUNTS_SERVER_URL}/oauth/v2/token",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(request, timeout=30) as response:
            data = _read_json(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ZohoAnalyticsError(f"Zoho token refresh failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ZohoAnalyticsError(f"Zoho token refresh network error: {exc.reason}") from exc

    access_token = data.get("access_token")
    if not access_token:
        raise ZohoAnalyticsError(f"Zoho token refresh returned no access_token: {data}")

    expires_in = int(data.get("expires_in") or 3600)
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + expires_in
    return str(access_token)


def analytics_request(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
    org_id: str | None = None,
) -> dict[str, Any]:
    """
    Call Zoho Analytics REST API.

    path should be like '/restapi/v2/workspaces' (leading slash optional).
    """
    import json

    token = refresh_access_token()
    normalized = path if path.startswith("/") else f"/{path}"
    url = f"{ZOHO_ANALYTICS_SERVER_URL}{normalized}"
    if query:
        url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"

    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
    }
    effective_org = (org_id or ZOHO_ANALYTICS_ORG_ID).strip()
    if effective_org:
        headers["ZANALYTICS-ORGID"] = effective_org
    if body is not None:
        headers["Content-Type"] = content_type

    request = Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return _read_json(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # One retry on auth failure in case the cached token expired early.
        if exc.code in {401, 403}:
            token = refresh_access_token(force=True)
            headers["Authorization"] = f"Zoho-oauthtoken {token}"
            retry = Request(url, data=body, method=method.upper(), headers=headers)
            try:
                with urlopen(retry, timeout=60) as response:
                    return _read_json(response)
            except HTTPError as retry_exc:
                retry_detail = retry_exc.read().decode("utf-8", errors="replace")
                raise ZohoAnalyticsError(
                    f"Zoho Analytics API failed ({retry_exc.code}): {retry_detail}"
                ) from retry_exc
        raise ZohoAnalyticsError(f"Zoho Analytics API failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ZohoAnalyticsError(f"Zoho Analytics network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ZohoAnalyticsError("Zoho Analytics returned invalid JSON") from exc


def list_workspaces() -> dict[str, Any]:
    return analytics_request("GET", "/restapi/v2/workspaces")


def get_default_workspace_id() -> str:
    if ZOHO_ANALYTICS_WORKSPACE_ID:
        return ZOHO_ANALYTICS_WORKSPACE_ID
    raise ZohoAnalyticsError(
        "ZOHO_ANALYTICS_WORKSPACE_ID is not set. Add it to backend/.env after creating/selecting a workspace."
    )
