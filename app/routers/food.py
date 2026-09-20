from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_catalog

router = APIRouter()


@router.get("/food")
def list_food(city: str = "pondicherry"):
    try:
        catalog = load_catalog(city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return [f.model_dump() for f in catalog.food]
