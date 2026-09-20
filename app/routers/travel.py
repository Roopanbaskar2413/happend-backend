from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_catalog

router = APIRouter()


@router.get("/travel-info")
def get_travel_info(city: str = "pondicherry"):
    try:
        catalog = load_catalog(city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return {
        "origins": [o.model_dump() for o in catalog.travel_origins],
        "local_transport": [t.model_dump() for t in catalog.local_transport],
    }
