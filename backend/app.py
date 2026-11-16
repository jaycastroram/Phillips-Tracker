from __future__ import annotations

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, FRONTEND_DIST
from database import company_id_for_user, connect, execute, get_sheet_config, resolve_scoped_company_id
from init_db import init_db
from routes.auth import get_current_user
from routes import auth, items, admin, public, uploads, zoho_analytics

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

init_db()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="MRL SmartBuy Tracker API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(items.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(uploads.router)
app.include_router(zoho_analytics.router)


# ---------------------------------------------------------------------------
# Shared endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/sheets")
def get_sheets(company_id: int | None = None, user=Depends(get_current_user)) -> dict:
    return {"sheets": get_sheet_config(resolve_scoped_company_id(user, company_id))}


# ---------------------------------------------------------------------------
# Frontend static files (production)
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        requested_file = FRONTEND_DIST / full_path
        if full_path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(FRONTEND_DIST / "index.html")


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
