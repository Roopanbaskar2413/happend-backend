"""Request/response DTOs for the API layer (not the pure engine models)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    email_verified: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class GuideItem(BaseModel):
    id: str
    title: str
    kind: str
    status: str
    start: str
    end: str


class GuideDay(BaseModel):
    weekday: int
    items: list[GuideItem]


class GuideRequest(BaseModel):
    city: str
    day: GuideDay
    # Opaque Gemini `Content` turns (role + parts), round-tripped as-is —
    # the frontend never needs to understand this shape, only pass it back.
    contents: list[dict[str, Any]]


class GuideToolCall(BaseModel):
    name: str
    args: dict[str, Any]
    call_id: str | None = None


class GuideSuggestedPlace(BaseModel):
    id: str
    name: str
    kind: str
    category: str | None = None
    closes_at: str | None = None
    duration_min: int
    cost_pp: int
    rating: float


class GuideResponse(BaseModel):
    contents: list[dict[str, Any]]
    reply: str | None = None
    tool_call: GuideToolCall | None = None
    # Whatever find_place/find_open_after actually returned for this turn --
    # lets the frontend render clickable place cards instead of parsing the
    # model's prose, and the model's text can go stale/wrong but this can't.
    suggested_places: list[GuideSuggestedPlace] | None = None


class LogStaySelectionRequest(BaseModel):
    city: str
    stay_id: str


class SavePlanRequest(BaseModel):
    city: str
    arrival_date: str
    departure_date: str
    itinerary: dict[str, Any]
    plan_request: dict[str, Any]


class SavedPlanOut(BaseModel):
    id: str
    email: str
    city: str
    arrival_date: str
    departure_date: str
    status: str
    created_at: datetime
    reminder_sent_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class UpdatePlanStatusRequest(BaseModel):
    status: str


class UpdatePlanRequest(BaseModel):
    itinerary: dict[str, Any]


class ShareRequest(BaseModel):
    email: EmailStr


class ShareOut(BaseModel):
    id: str
    shared_with_email: str
    role: str
    edit_requested: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SharedPlanOut(BaseModel):
    id: str
    owner_email: str
    role: str
    edit_requested: bool
    city: str
    arrival_date: str
    departure_date: str
    created_at: datetime


class CreateMemoryRequest(BaseModel):
    saved_plan_id: str


class CreateStoryRequest(BaseModel):
    text: str


class UpdateStoryRequest(BaseModel):
    text: str


class MemoryStoryOut(BaseModel):
    id: str
    text: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryPhotoOut(BaseModel):
    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryOut(BaseModel):
    id: str
    saved_plan_id: str
    summary: dict[str, Any]
    stories: list[MemoryStoryOut]
    photos: list[MemoryPhotoOut]
    has_music: bool
    created_at: datetime
