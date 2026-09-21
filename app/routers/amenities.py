from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_amenities

router = APIRouter()


@router.get("/amenities")
def list_amenities(city: str = "pondicherry"):
    try:
        amenities = load_amenities(city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return [a.model_dump() for a in amenities]
