from fastapi import APIRouter

from app.engine.catalog import load_cities

router = APIRouter()


@router.get("/health")
def health():
    cities = load_cities()
    return {
        "status": "ok",
        "cities_total": len(cities),
        "cities_available": sum(1 for c in cities if not c.coming_soon),
    }
