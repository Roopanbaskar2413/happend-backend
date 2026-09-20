"""SQLAlchemy ORM models. Kept separate from app/engine/models.py (the pure
Pydantic catalog/engine models) — the engine never imports from here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.security import SESSION_TTL_DAYS, new_session_token


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_expiry() -> datetime:
    return _now() + timedelta(days=SESSION_TTL_DAYS)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_session_token)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_session_expiry)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship()


class EmailToken(Base):
    """A single-use, expiring token emailed to a user — for verifying their
    address or authorizing a password reset. The token value itself is the
    primary key (same pattern as `Session`), so verifying is a direct lookup."""

    __tablename__ = "email_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_session_token)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String)  # "verify" | "reset"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship()


class SavedPlan(Base):
    __tablename__ = "saved_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    city: Mapped[str] = mapped_column(String)
    arrival_date: Mapped[str] = mapped_column(String)  # ISO date, kept as string like the rest of the app
    departure_date: Mapped[str] = mapped_column(String)
    itinerary_json: Mapped[dict] = mapped_column(JSON)
    plan_request_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanShare(Base):
    """A saved plan shared with another person by email.

    Viewer is the default and only role a share can grant on its own — editing
    requires the owner to explicitly approve a request (see `edit_requested`).
    Matching is by email, not a stored user_id, so an owner can share with
    someone who hasn't signed up yet; it just resolves the next time that
    email logs in.
    """

    __tablename__ = "plan_shares"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    saved_plan_id: Mapped[str] = mapped_column(ForeignKey("saved_plans.id"), index=True)
    shared_with_email: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String, default="viewer")  # "viewer" | "editor"
    edit_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
