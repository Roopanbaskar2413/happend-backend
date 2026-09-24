from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from app.db import get_db
from app.engine.catalog import CityNotAvailable, load_catalog
from app.limiter import limiter
from app.orm import StaySelection
from app.schemas import LogStaySelectionRequest

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


@router.post("/stays/selections", status_code=204)
@limiter.limit("30/hour")
def log_stay_selection(
    request: Request, body: LogStaySelectionRequest, db: DbSession = Depends(get_db)
):
    """Fire-and-forget: logs a "Select" click in the planner flow, pre-signup
    and pre-save, so real referral volume is captured even for the many
    people who browse a plan without ever creating an account. See
    scripts/stay_performance.py for turning this into per-hotel numbers.
    """
    try:
        catalog = load_catalog(body.city)
    except (KeyError, CityNotAvailable):
        # Never let a logging call surface an error to the user over a
        # stale/bad city -- this must stay invisible in the UI either way.
        return

    if not any(s.id == body.stay_id for s in catalog.stays):
        return

    db.add(StaySelection(city=body.city, stay_id=body.stay_id))
    db.commit()
