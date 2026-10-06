from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from intake import audit
from intake.api.deps import Conn
from intake.api.ratelimit import failed_logins
from intake.auth import (
    COOKIE_NAME,
    DUMMY_HASH,
    CurrentUser,
    User,
    issue_session,
    verify_password,
)
from intake.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_json(user: User) -> dict[str, Any]:
    return {"id": user.id, "display_name": user.display_name, "role": user.role}


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, conn: Conn) -> dict[str, Any]:
    client = request.client.host if request.client else "unknown"
    if failed_logins.blocked(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts; wait a minute")
    row = conn.execute("SELECT * FROM users WHERE id = %s", (body.username.strip(),)).fetchone()
    # Same work and same answer for unknown users and wrong passwords.
    valid = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
    if row is None or not valid:
        failed_logins.record(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    user = User(id=row["id"], display_name=row["display_name"], role=row["role"])
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        issue_session(user),
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        max_age=settings.session_hours * 3600,
        path="/api",
    )
    audit.record(conn, user.id, "auth.login", "user", user.id)
    return _user_json(user)


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE_NAME, path="/api")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return _user_json(user)
