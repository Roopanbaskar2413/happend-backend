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
    created_at: datetime
