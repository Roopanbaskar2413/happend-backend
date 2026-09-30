import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # Whatever the connection string says -- "postgres://", "postgresql://",
    # "postgres+psycopg://", "postgresql+psycopg://" (a connection string
    # re-copied from Neon's dashboard can default to the newer psycopg v3
    # format), etc. -- force it to the dialect/driver this project actually
    # installs (psycopg2-binary, not psycopg). A naive string-prefix
    # replace here previously missed the "postgres+psycopg://" case (note:
    # no "ql") and shipped 5 days of silent production outages: every
    # deploy crashed at startup with `ModuleNotFoundError: No module named
    # 'psycopg'` before serving a single request, while "deploy: completed
    # success" in CI kept reporting green because that only confirms the
    # Render deploy hook was called, not that the app actually started --
    # see PROJECT_OVERVIEW.md. Parsing the URL structurally instead of
    # guessing at prefixes is what makes this actually robust.
    url = make_url(DATABASE_URL)
    if url.get_backend_name() in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
        DATABASE_URL = str(url)
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
