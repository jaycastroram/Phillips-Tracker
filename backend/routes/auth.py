from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from database import (
    connect,
    execute,
    hash_password,
    serialize_user,
    utc_now,
    verify_password,
)
from config import SESSION_DAYS
from models import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(authorization: str | None = Header(default=None)) -> sqlite3.Row:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing login token")

    token = authorization.split(" ", 1)[1].strip()
    with connect() as conn:
        row = execute(
            conn,
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ?
            """,
            (token, utc_now().isoformat()),
        ).fetchone()

    if row is None or not row["is_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired login token")
    return row


def require_admin(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    with connect() as conn:
        user = execute(
            conn,
            "SELECT * FROM users WHERE lower(email) = lower(?)",
            (payload.email,),
        ).fetchone()
        if user is None or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        if not user["is_active"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is inactive")

        token = secrets.token_urlsafe(32)
        expires_at = (utc_now() + timedelta(days=SESSION_DAYS)).isoformat()
        execute(
            conn,
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires_at),
        )

        public_user = serialize_user(conn, user)

    return {"token": token, "user": public_user}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    authorization: str | None = Header(default=None),
    user: sqlite3.Row = Depends(get_current_user),
) -> Response:
    del user
    if authorization:
        token = authorization.split(" ", 1)[1].strip()
        with connect() as conn:
            execute(conn, "DELETE FROM sessions WHERE token = ?", (token,))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(user: sqlite3.Row = Depends(get_current_user)) -> dict:
    with connect() as conn:
        return {"user": serialize_user(conn, user)}
