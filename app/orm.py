"""SQLAlchemy ORM models. Kept separate from app/engine/models.py (the pure
Pydantic catalog/engine models) — the engine never imports from here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
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
    status: Mapped[str] = mapped_column(String, default="upcoming")  # "upcoming" | "completed"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StaySelection(Base):
    """One "Select" click on a stay in the planner flow -- logged pre-signup,
    pre-save, so it captures real referral volume even for the many people
    who browse a plan without ever creating an account. Deliberately its own
    table rather than a new column on SavedPlan: this codebase has no
    migration tool (`init_db` only runs `create_all`, which never alters an
    existing table), so a new table is safe to add but a new column on an
    already-deployed table is not. Cross-reference with SavedPlan.plan_request_json
    (which already carries stay_id) to see which selections became real trips --
    see scripts/stay_performance.py.
    """

    __tablename__ = "stay_selections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    city: Mapped[str] = mapped_column(String, index=True)
    stay_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


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


class Memory(Base):
    """The container for one completed trip's memories: an auto-computed
    summary snapshot, plus any number of user-written stories and photos."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    saved_plan_id: Mapped[str] = mapped_column(ForeignKey("saved_plans.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    summary_json: Mapped[dict] = mapped_column(JSON)
    # Optional background track for the trip-clip slideshow player — the
    # traveler's own uploaded song, not licensed music (nothing here sources
    # copyrighted audio).
    music_key: Mapped[str | None] = mapped_column(String, nullable=True)
    music_backend: Mapped[str | None] = mapped_column(String, nullable=True)  # "r2" | "local"
    music_content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MemoryStory(Base):
    """One journal entry within a memory — a moment the traveler wants to
    write down ("the sunset at Promenade Beach was incredible"). A memory can
    have any number of these, added over time, not just a single note."""

    __tablename__ = "memory_stories"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    text: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MemoryPhoto(Base):
    __tablename__ = "memory_photos"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String)
    storage_backend: Mapped[str] = mapped_column(String)  # "r2" | "local" — set at upload time
    original_filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
