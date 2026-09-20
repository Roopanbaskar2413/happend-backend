"""Pure data-loading for the city registry and per-city catalogs.

No FastAPI or database imports here — this module only reads JSON off disk
and returns validated Pydantic models, so it can be used standalone (see
`scripts/audit_data.py`) or from the API layer.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.engine.models import (
    Catalog,
    City,
    Food,
    LocalTransportOption,
    Place,
    Stay,
    TravelOrigin,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class CityNotAvailable(Exception):
    """Raised when a city is in the registry but has no data folder yet."""


def _city_dir(city_id: str) -> Path:
    return DATA_DIR / city_id


@lru_cache(maxsize=1)
def load_cities() -> list[City]:
    registry = json.loads((DATA_DIR / "cities.json").read_text())
    cities = [City.model_validate(row) for row in registry["cities"]]
    for city in cities:
        has_data = (_city_dir(city.id) / "places.json").exists()
        if city.coming_soon and has_data:
            raise ValueError(f"city {city.id!r} has data but is marked coming_soon")
        if not city.coming_soon and not has_data:
            raise ValueError(f"city {city.id!r} is not coming_soon but has no data folder")
    return cities


def get_city(city_id: str) -> City:
    for city in load_cities():
        if city.id == city_id:
            return city
    raise KeyError(f"unknown city {city_id!r}")


@lru_cache(maxsize=8)
def load_catalog(city_id: str) -> Catalog:
    city = get_city(city_id)
    if city.coming_soon:
        raise CityNotAvailable(f"{city_id!r} is coming soon and has no data yet")

    city_dir = _city_dir(city_id)
    places_raw = json.loads((city_dir / "places.json").read_text())
    travel_raw = json.loads((city_dir / "travel.json").read_text())

    return Catalog(
        city=city,
        places=[Place.model_validate(row) for row in places_raw["places"]],
        food=[Food.model_validate(row) for row in places_raw["food"]],
        stays=[Stay.model_validate(row) for row in places_raw["stays"]],
        travel_origins=[TravelOrigin.model_validate(row) for row in travel_raw["origins"]],
        local_transport=[
            LocalTransportOption.model_validate(row) for row in travel_raw["local_transport"]
        ],
    )
