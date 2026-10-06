"""Staff authentication: scrypt password hashes and a signed session cookie (ported from
ClinicVoice ADR 0006; see ADR 0006 here).

The web app calls /api on its own origin (Vite proxy locally, a Vercel rewrite in production),
so the cookie is httpOnly + SameSite=Lax with no token in JavaScript-readable storage. Every
state-changing request must also send the X-Intake-CSRF header, which a cross-site form cannot.
"""

import hashlib
import hmac
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status

from intake.settings import get_settings

Role = Literal["intake", "radiologist", "admin"]
COOKIE_NAME = "intake_session"
CSRF_HEADER = "x-intake-csrf"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


# Spent on unknown usernames so they take as long as wrong passwords.
DUMMY_HASH = hash_password("not-a-real-password")


@dataclass(frozen=True)
class User:
    id: str
    display_name: str
    role: Role


def issue_session(user: User) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {
        "sub": user.id,
        "name": user.display_name,
        "role": user.role,
        "iat": now,
        "exp": now + settings.session_hours * 3600,
    }
    return jwt.encode(claims, settings.session_secret, algorithm="HS256")


def read_session(token: str) -> User | None:
    try:
        claims = jwt.decode(token, get_settings().session_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return User(id=claims["sub"], display_name=claims["name"], role=claims["role"])


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    user = read_session(token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in required")
    if request.method not in ("GET", "HEAD", "OPTIONS") and CSRF_HEADER not in request.headers:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def _role_dependency(*roles: Role) -> Callable[[User], User]:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role: {', '.join(roles)}")
        return user

    return dependency


StaffUser = Annotated[User, Depends(_role_dependency("intake", "radiologist", "admin"))]
IntakeUser = Annotated[User, Depends(_role_dependency("intake", "admin"))]
RadiologistUser = Annotated[User, Depends(_role_dependency("radiologist"))]
ReviewerOrAdmin = Annotated[User, Depends(_role_dependency("radiologist", "admin"))]
AdminUser = Annotated[User, Depends(_role_dependency("admin"))]
