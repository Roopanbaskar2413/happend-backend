"""Pure Pydantic data models for the catalog and planning engine.

No FastAPI or database imports belong in this module or anywhere else under
`app/engine/` — the engine must stay usable as a standalone library.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

TIME_WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")

Category = Literal[
    "beach", "heritage", "spiritual", "museum", "park", "market",
    "shopping", "nature", "activity", "culture", "nightlife",
    "turf", "pool",
]
GroupType = Literal["solo", "couple", "family", "friends"]
Slot = Literal["sunrise", "morning", "afternoon", "evening", "night"]
PriceBand = Literal["low", "mid", "high"]
Meal = Literal["breakfast", "lunch", "dinner", "snack"]
BudgetLevel = Literal["low", "mid", "high"]
Pace = Literal["relaxed", "balanced", "packed"]
Diet = Literal["any", "veg", "vegan"]
TransportMode = Literal["bus", "train", "car", "flight"]


def _validate_windows(windows: list[str]) -> list[str]:
    for w in windows:
        if not TIME_WINDOW_RE.match(w):
            raise ValueError(f"invalid time window {w!r}, expected 'HH:MM-HH:MM'")
    return windows


def _validate_closed_days(days: list[int]) -> list[int]:
    for d in days:
        if not 0 <= d <= 6:
            raise ValueError(f"closed_days entries must be 0-6, got {d}")
    return days


class Place(BaseModel):
    id: str
    name: str
    category: Category
    area: str
    city: str
    lat: float
    lng: float
    duration_min: int = Field(gt=0)
    windows: list[str]
    closed_days: list[int] = Field(default_factory=list)
    cost_pp: int = Field(ge=0)
    interests: list[str]
    group_fit: list[GroupType]
    rating: float = Field(ge=0, le=5)
    best_slots: list[Slot]
    weather_dependent: bool
    indoor: bool
    heat_exposed: bool
    notes: Optional[str] = None
    booking_url: Optional[str] = None
    verified: bool
    source: Optional[str] = None

    _check_windows = field_validator("windows")(_validate_windows)
    _check_closed_days = field_validator("closed_days")(_validate_closed_days)


class Food(BaseModel):
    id: str
    name: str
    meals: list[Meal]
    area: str
    city: str
    lat: float
    lng: float
    duration_min: int = Field(gt=0)
    windows: list[str]
    closed_days: list[int] = Field(default_factory=list)
    cost_pp: int = Field(ge=0)
    price_band: PriceBand
    veg_friendly: bool
    rating: float = Field(ge=0, le=5)
    notes: Optional[str] = None
    booking_url: Optional[str] = None
    verified: bool
    source: Optional[str] = None

    _check_windows = field_validator("windows")(_validate_windows)
    _check_closed_days = field_validator("closed_days")(_validate_closed_days)


class Stay(BaseModel):
    id: str
    name: str
    area: str
    city: str
    lat: float
    lng: float
    price_band: PriceBand
    price_per_night: int = Field(ge=0)
    group_fit: list[GroupType]
    rating: float = Field(ge=0, le=5)
    notes: Optional[str] = None
    booking_url: Optional[str] = None
    verified: bool
    source: Optional[str] = None


class Amenity(BaseModel):
    """A general-purpose POI (gas station, school, shop, etc.) bulk-imported
    for a plain nearby/name search feature only -- never surfaced by the
    itinerary planning engine, which only reasons about Place/Food/Stay."""

    id: str
    name: str
    raw_category: Optional[str] = None
    area: str
    city: str
    lat: float
    lng: float
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    phone: Optional[str] = None
    source: Optional[str] = None
    verified: bool


class City(BaseModel):
    id: str
    name: str
    coming_soon: bool
    blurb: Optional[str] = None


class Catalog(BaseModel):
    """Everything loaded for one plannable city."""
    city: City
    places: list[Place]
    food: list[Food]
    stays: list[Stay]
    travel_origins: list["TravelOrigin"]
    local_transport: list["LocalTransportOption"]


class TravelOption(BaseModel):
    mode: TransportMode
    label: str
    duration_min: int = Field(ge=0)
    cost_pp: int = Field(ge=0)
    frequency: str
    booking_url: Optional[str] = None
    notes: Optional[str] = None


class TravelOrigin(BaseModel):
    city: str
    distance_km: float = Field(ge=0)
    options: list[TravelOption]


class LocalTransportOption(BaseModel):
    mode: str
    label: str
    cost_per_day: int = Field(ge=0)
    notes: Optional[str] = None
    booking_url: Optional[str] = None


class PlanRequest(BaseModel):
    city: str  # destination city id, e.g. "pondicherry" — required now that the catalog is multi-city
    origin_city: str
    transport_mode: TransportMode
    arrival_date: str
    arrival_time: str
    departure_date: str
    departure_time: str
    group_type: GroupType
    party_size: int = Field(gt=0)
    budget_level: BudgetLevel
    pace: Pace
    diet: Diet
    interests: list[str] = Field(default_factory=list)
    has_own_vehicle: bool
    has_own_stay: bool  # true if arranging their own accommodation, independent of stay_id
    stay_id: Optional[str] = None  # chosen from the catalog's stays; None if undecided/own arrangement


ItemKind = Literal["travel", "place", "meal", "transfer", "checkin", "checkout", "free"]
ItemStatus = Literal["planned", "done", "skipped", "dropped"]


class ItineraryItem(BaseModel):
    id: str
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    kind: ItemKind
    ref_id: Optional[str] = None
    title: str
    area: Optional[str] = None
    cost_pp: int = 0
    notes: Optional[str] = None
    booking_url: Optional[str] = None
    map_url: Optional[str] = None
    locked: bool = False
    status: ItemStatus = "planned"
    warning: Optional[str] = None


class DayPlan(BaseModel):
    date: str  # ISO "2026-09-26"
    weekday: int  # 0=Mon .. 6=Sun
    items: list[ItineraryItem]


class Itinerary(BaseModel):
    days: list[DayPlan]
