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
    # pool_pre_ping: Neon (and most managed Postgres) will silently close
    # idle connections; without this, the first query on a stale connection
    # fails outright instead of transparently reconnecting.
    #
    # create_engine() takes the URL *object* here, not str(url) -- SQLAlchemy
    # 2.x's URL.__str__ masks the password as a literal "***" (a security
    # hardening change, so credentials don't leak into logs/prints), and that
    # masked string was then round-tripped back into create_engine() as the
    # actual DSN. Every connection attempt authenticated with the password
    # "***" instead of the real one, everywhere, always -- silent outage,
    # because it looked exactly like a normal auth failure.
    engine = create_engine(url, pool_pre_ping=True)
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
