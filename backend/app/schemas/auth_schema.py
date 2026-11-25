from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(Credentials):
    display_name: str | None = Field(default=None, max_length=80)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    """A user as the API shows it (never the password hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    display_name: str | None = None
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # ISO 8601
    expires_at: str
    user: UserResponse


class ChangePasswordResponse(BaseModel):
    changed: bool
    revoked_sessions: int


class AuthConfigResponse(BaseModel):
    allow_registration: bool
    min_password_length: int


@dataclass
class AuthContext:
    """The authenticated user of a request and the login session of its access token."""

    user: UserResponse
    session_id: uuid.UUID


@dataclass
class LoginResult:
    """Access token response, and the refresh token to put in the cookie (None: keep the cookie)."""

    tokens: TokenResponse
    refresh_token: str | None
