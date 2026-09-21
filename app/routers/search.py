from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_amenities, load_catalog

router = APIRouter()


def _matches(query: str, *fields: str | None) -> bool:
    return any(query in (field or "").lower() for field in fields)


@router.get("/search")
def search(q: str, city: str = "pondicherry"):
    query = q.strip().lower()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="query must be at least 2 characters")

    try:
        catalog = load_catalog(city)
        amenities = load_amenities(city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    results = []

    for p in catalog.places:
        if _matches(query, p.name, p.category, *p.interests):
            results.append({"type": "place", **p.model_dump()})

    for f in catalog.food:
        if _matches(query, f.name, *f.meals):
            results.append({"type": "food", **f.model_dump()})

    for s in catalog.stays:
        if _matches(query, s.name):
            results.append({"type": "stay", **s.model_dump()})

    for a in amenities:
        if _matches(query, a.name, a.raw_category):
            results.append({"type": "amenity", **a.model_dump()})

    return results
