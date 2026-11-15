from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.security import APIKeyCookie
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from voice_archive_api.api.schemas.errors import HttpErrorResponse
from voice_archive_api.core.dependencies import get_app_state
from voice_archive_api.core.passwords import password_hasher
from voice_archive_api.db.models.user import User
from voice_archive_api.db.session import get_db
from voice_archive_api.logic.users import normalize_email_address
from voice_archive_api.services.access import AccessContext

COOKIE_NAME = "voice_archive_session"
DUMMY_PASSWORD_HASH = password_hasher.hash("unused authentication timing hash")
router = APIRouter(prefix="/auth", tags=["Authentication"], responses={503: {"model": HttpErrorResponse}})
Database = Annotated[Session, Depends(get_db, scope="function")]
SessionCookie = Annotated[
    str | None, Security(APIKeyCookie(name=COOKIE_NAME, auto_error=False, scheme_name="SessionCookie"))
]


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    display_name: str | None


class AuthConfigResponse(BaseModel):
    login_enabled: bool


def resolve_user(request: Request, db: Session, token: str | None) -> User | None:
    if not get_app_state(request).settings.login_enabled or token is None:
        return None
    secret = get_app_state(request).settings.auth_secret
    try:
        if secret is None:
            raise jwt.InvalidTokenError
        claims = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="voice-archive",
            audience="voice-archive",
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        subject = claims["sub"]
        if (
            not isinstance(subject, str)
            or len(subject) > 19
            or not subject.isascii()
            or not subject.isdigit()
            or not 0 < int(subject) <= 9_223_372_036_854_775_807
        ):
            raise jwt.InvalidTokenError
        user = db.get(User, int(subject))
        if user is None or not user.is_enabled or user.email is None or user.password_hash is None:
            raise jwt.InvalidTokenError
        return user
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Authentication required") from None


def authorize_api_request(request: Request, db: Database, token: SessionCookie) -> None:
    user = resolve_user(request, db, token)
    needs_user = request.method not in {"GET", "HEAD", "OPTIONS"}
    if needs_user and user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = None
    if user is not None:
        user_id = user.id
    access = AccessContext(
        user_id,
        is_write=needs_user and request.url.path != "/api/v1/library/ask",
    )
    request.state.access = access
    db.info["access"] = access


@router.post("/login", response_model=UserResponse, operation_id="login_api_v1_user")
def login(payload: LoginInput, request: Request, response: Response, db: Database) -> User:
    state = get_app_state(request)
    if not state.settings.login_enabled:
        raise HTTPException(status_code=503, detail="Sign-in is disabled")
    secret = state.settings.auth_secret
    if secret is None:
        raise HTTPException(status_code=503, detail="Sign-in is not configured")
    now = monotonic()
    client = request.client.host if request.client else "unknown"
    with state.login_lock:
        state.login_attempts = {
            key: [time for time in values if time > now - 60]
            for key, values in state.login_attempts.items()
            if values and values[-1] > now - 60
        }
        attempts = state.login_attempts.get(client, [])
        if len(attempts) >= 10 or len(state.login_attempts) >= 1024 and client not in state.login_attempts:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts", headers={"Retry-After": "60"})
        state.login_attempts[client] = [*attempts, now]
    try:
        email = normalize_email_address(payload.email)
    except ValueError:
        email = ""
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    can_sign_in = user is not None and user.is_enabled and user.password_hash is not None
    encoded = user.password_hash if can_sign_in and user is not None else DUMMY_PASSWORD_HASH
    valid = password_hasher.verify(payload.password.get_secret_value(), encoded or DUMMY_PASSWORD_HASH)
    if not valid or not can_sign_in or user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    issued = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": issued,
            "exp": issued + timedelta(seconds=state.settings.auth_token_ttl_seconds),
            "iss": "voice-archive",
            "aud": "voice-archive",
        },
        secret.get_secret_value(),
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=state.settings.auth_token_ttl_seconds,
        httponly=True,
        secure=state.settings.auth_cookie_secure,
        samesite="lax",
        path="/api",
    )
    return user


@router.get("/config", response_model=AuthConfigResponse, operation_id="get_api_v1_auth_config")
def get_auth_config(request: Request) -> AuthConfigResponse:
    settings = get_app_state(request).settings
    return AuthConfigResponse(login_enabled=settings.login_enabled and settings.auth_secret is not None)


@router.get("/me", response_model=UserResponse | None, operation_id="get_api_v1_current_user")
def get_current_user(request: Request, db: Database, token: SessionCookie) -> User | None:
    return resolve_user(request, db, token)


@router.post("/logout", status_code=204, response_class=Response, operation_id="logout_api_v1_user")
def logout(request: Request, response: Response) -> Response:
    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        secure=get_app_state(request).settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.status_code = 204
    return response
