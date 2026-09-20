from fastapi import APIRouter

from app.engine.catalog import load_catalog

router = APIRouter()


@router.get("/options")
def get_options():
    # Single plannable city for now; revisit once a second city goes live.
    catalog = load_catalog("pondicherry")
    interests = sorted({i for p in catalog.places for i in p.interests})
    origins = [o.city for o in catalog.travel_origins]

    return {
        "origins": origins,
        "transport_modes": ["bus", "train", "car", "flight"],
        "group_types": ["solo", "couple", "family", "friends"],
        "budget_levels": ["low", "mid", "high"],
        "paces": ["relaxed", "balanced", "packed"],
        "diets": ["any", "veg", "vegan"],
        "interests": interests,
    }
