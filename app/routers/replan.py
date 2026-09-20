from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.catalog import CityNotAvailable, load_catalog
from app.engine.models import Itinerary, PlanRequest
from app.engine.reflow import Change, Disruption, DisruptionError, replan

router = APIRouter()


class ReplanRequest(BaseModel):
    city: str
    itinerary: Itinerary
    plan_request: PlanRequest
    disruption: Disruption


class ReplanResponse(BaseModel):
    itinerary: Itinerary
    changes: list[Change]


@router.post("/replan", response_model=ReplanResponse)
def create_replan(body: ReplanRequest):
    try:
        catalog = load_catalog(body.city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {body.city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        result = replan(body.itinerary, body.disruption, catalog, body.plan_request)
    except DisruptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ReplanResponse(itinerary=result.itinerary, changes=result.changes)
