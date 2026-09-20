from fastapi import APIRouter, HTTPException

from app.engine.catalog import CityNotAvailable, load_catalog
from app.engine.engine import generate
from app.engine.models import Itinerary, PlanRequest

router = APIRouter()


@router.post("/plan", response_model=Itinerary)
def create_plan(request: PlanRequest):
    try:
        catalog = load_catalog(request.city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {request.city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return generate(request, catalog)
