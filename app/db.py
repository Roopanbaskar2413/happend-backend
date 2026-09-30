import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Neon (and Heroku-style providers) hand out "postgres://" — SQLAlchemy's
    # psycopg2 dialect wants "postgresql://".
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    # Some connection strings (e.g. one re-copied from Neon's dashboard,
    # which can default to the newer psycopg v3 format) specify the driver
    # explicitly as "+psycopg" -- but this project installs psycopg2-binary,
    # not psycopg. Left as-is, this crashes the app at startup with
    # `ModuleNotFoundError: No module named 'psycopg'` before it ever serves
    # a request (this happened in production for 5 days straight, silently,
    # because "deploy: completed success" in CI only means the Render deploy
    # hook was called, not that the app actually started -- see
    # PROJECT_OVERVIEW.md). Force the dialect that's actually installed,
    # regardless of what the URL says.
    if DATABASE_URL.startswith("postgresql+psycopg://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    # pool_pre_ping: Neon (and most managed Postgres) will silently close
    # idle connections; without this, the first query on a stale connection
    # fails outright instead of transparently reconnecting.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    DB_PATH = Path(__file__).resolve().parent.parent / "app.db"
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db():
    from app import orm  # noqa: F401  (registers models on Base before create_all)

    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
