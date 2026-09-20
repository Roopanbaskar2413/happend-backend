import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import CORS_ORIGINS
from app.db import SessionLocal, init_db
from app.limiter import limiter
from app.reminders import send_due_reminders
from app.routers import (
    auth,
    cities,
    food,
    health,
    memories,
    options,
    places,
    plan,
    replan,
    saved_plans,
    stays,
    travel,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("reminders")
REMINDER_CHECK_INTERVAL_SECONDS = 3600  # once an hour is plenty for a "days before" reminder


async def _reminder_loop():
    while True:
        db = SessionLocal()
        try:
            sent = send_due_reminders(db)
            if sent:
                logger.info("reminder loop: sent %d reminder(s)", sent)
        finally:
            db.close()
        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_reminder_loop())
    yield
    task.cancel()


app = FastAPI(title="Happend API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(cities.router, prefix="/api")
app.include_router(options.router, prefix="/api")
app.include_router(places.router, prefix="/api")
app.include_router(stays.router, prefix="/api")
app.include_router(food.router, prefix="/api")
app.include_router(travel.router, prefix="/api")
app.include_router(plan.router, prefix="/api")
app.include_router(replan.router, prefix="/api")
app.include_router(saved_plans.router, prefix="/api")
app.include_router(memories.router, prefix="/api")
