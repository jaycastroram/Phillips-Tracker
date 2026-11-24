from __future__ import annotations

import io
import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from config import ALLOWED_UPLOAD_TYPES, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET, CLOUDINARY_CLOUD_NAME, MAX_UPLOAD_BYTES
from routes.auth import get_current_user

try:
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )
except ImportError:
    cloudinary = None

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


SURVEY_FOLDERS = {"survey-questions", "survey-items"}


@router.post("/survey-question")
async def upload_survey_question_image(
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """One image per survey question. Same Cloudinary account, its own folder."""
    del user
    return await _store(file, folder="smartbuy/survey-questions")


@router.post("/survey-item")
async def upload_survey_item_image(
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    """One image per survey item: the picture the public survey card shows."""
    del user
    return await _store(file, folder="smartbuy/survey-items")


@router.post("/visual-reference")
async def upload_visual_reference(
    file: UploadFile = File(...),
    user: sqlite3.Row = Depends(get_current_user),
) -> dict:
    del user
    return await _store(file, folder="phillips-tracker/visual-references")


async def _store(file: UploadFile, *, folder: str) -> dict:
    if cloudinary is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Cloudinary dependency is not installed")
    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Cloudinary is not configured")
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload a JPG, PNG, or WEBP image")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image must be 8 MB or smaller")

    try:
        result = cloudinary.uploader.upload(
            io.BytesIO(contents),
            folder=folder,
            resource_type="image",
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Cloudinary upload failed. Check the Cloudinary cloud name, API key, and API secret.",
        ) from exc
    return {"url": result["secure_url"], "public_id": result["public_id"]}
