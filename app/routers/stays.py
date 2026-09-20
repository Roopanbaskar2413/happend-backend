from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_catalog

router = APIRouter()


@router.get("/stays")
def list_stays(city: str = "pondicherry"):
    try:
        catalog = load_catalog(city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return [s.model_dump() for s in catalog.stays]
