from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "tracker.db"
SEED_ITEMS_PATH = BASE_DIR / "data" / "seed_items.json"
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"


def load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip().lstrip("\ufeff"), value.strip().strip('"').strip("'"))


load_local_env()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

SESSION_DAYS = 7

# Company config — override via env vars for each deployment
DEFAULT_COMPANY_SLUG = os.getenv("COMPANY_SLUG", "phillips").strip()
DEFAULT_COMPANY_NAME = os.getenv("COMPANY_NAME", "Phillips").strip()
COMPANY_LOGO_URL = os.getenv("COMPANY_LOGO_URL", "").strip()
COMPANY_BANNER_URL = os.getenv("COMPANY_BANNER_URL", "").strip()

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin123")
ADMIN_NAME = os.getenv("ADMIN_NAME", "Dev Admin")

DEFAULT_RESPONSE_WIDTH = 36
DEFAULT_SURVEY_FILTER_FIELDS = ["brand", "channel", "product_type"]
SURVEY_FILTER_FIELDS = {"brand", "channel", "product_type", "key_program"}

COMPANY_SCOPED_TABLES = (
    "tracker_items",
    "survey_items",
    "survey_responses",
    "survey_periods",
    "kanban_columns",
    "sheet_statuses",
    "audit_logs",
)

SURVEY_PERIOD_STATUSES = ("draft", "open", "closed")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "").strip()

# Zoho Analytics — fill these in backend/.env (Self Client OAuth)
ZOHO_ANALYTICS_CLIENT_ID = os.getenv("ZOHO_ANALYTICS_CLIENT_ID", "").strip()
ZOHO_ANALYTICS_CLIENT_SECRET = os.getenv("ZOHO_ANALYTICS_CLIENT_SECRET", "").strip()
ZOHO_ANALYTICS_REFRESH_TOKEN = os.getenv("ZOHO_ANALYTICS_REFRESH_TOKEN", "").strip()
ZOHO_ANALYTICS_ORG_ID = os.getenv("ZOHO_ANALYTICS_ORG_ID", "").strip()
ZOHO_ANALYTICS_WORKSPACE_ID = os.getenv("ZOHO_ANALYTICS_WORKSPACE_ID", "").strip()
ZOHO_ACCOUNTS_SERVER_URL = os.getenv("ZOHO_ACCOUNTS_SERVER_URL", "https://accounts.zoho.com").strip().rstrip("/")
ZOHO_ANALYTICS_SERVER_URL = os.getenv("ZOHO_ANALYTICS_SERVER_URL", "https://analyticsapi.zoho.com").strip().rstrip("/")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

SHEETS = {
    "ad-hoc": {
        "label": "Ad Hoc",
        "dateLabel": "Request Date",
        "statuses": [
            "Details Needed",
            "Quoting",
            "Pending Feedback",
            "Creative",
            "Processing OA",
            "OA Pending Approval",
            "PPS Underway",
            "PPS In Transit",
            "PPS Pending Approval",
            "Order In Production",
            "Order In Transit",
            "Delivered",
            "ON HOLD",
        ],
    },
    "buys": {
        "label": "Buys",
        "dateLabel": "Buy",
        "statuses": [
            "Details Needed",
            "Quoting",
            "Pending Feedback",
            "Creative",
            "FINAL Quoting",
            "Canceled",
            "Processing OA",
            "OA Pending Approval",
            "PPS Underway",
            "PPS In Transit",
            "PPS Pending Approval",
            "Order In Production",
            "Order In Transit",
            "Delivered",
            "ON HOLD",
        ],
    },
    "completed": {
        "label": "Completed",
        "dateLabel": "Date/Buy",
        "statuses": [
            "Details Needed",
            "Quoting",
            "Pending Feedback",
            "Creative",
            "Processing OA",
            "OA Pending Approval",
            "PPS Underway",
            "PPS In Transit",
            "PPS Pending Approval",
            "Order In Production",
            "Order In Transit",
            "Delivered",
            "ON HOLD",
        ],
    },
}

EDITABLE_FIELDS = {
    "date_or_buy",
    "current_status",
    "visual_reference",
    "brand",
    "program_name",
    "item_name",
    "qty",
    "important_notes",
    "mrl_order_number",
    "estimated_ship_date",
    "estimated_ihd",
    "tracking",
}

ALLOWED_UPLOAD_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

DEFAULT_KANBAN_COLUMNS = [
    {"title": "Needs Details", "statuses": ["Details Needed"], "sort_order": 1},
    {"title": "Quoting / Feedback", "statuses": ["Quoting", "Pending Feedback", "FINAL Quoting"], "sort_order": 2},
    {"title": "Creative / OA", "statuses": ["Creative", "Processing OA", "OA Pending Approval"], "sort_order": 3},
    {"title": "PPS", "statuses": ["PPS Underway", "PPS In Transit", "PPS Pending Approval"], "sort_order": 4},
    {"title": "Production / Transit", "statuses": ["Order In Production", "Order In Transit"], "sort_order": 5},
    {"title": "On Hold / Canceled", "statuses": ["ON HOLD", "Canceled"], "sort_order": 6},
]
