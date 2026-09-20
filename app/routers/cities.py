from fastapi import APIRouter

from app.engine.catalog import load_cities

router = APIRouter()


@router.get("/cities")
def list_cities():
    return [city.model_dump() for city in load_cities()]
