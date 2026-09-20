import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import orm  # noqa: F401  (registers ORM models on Base before create_all)
from app.db import Base, get_db
from app.limiter import limiter
from app.main import app


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    limiter.reset()  # tests share an IP (the test client), so don't let one test's
    # requests count against another's rate limit

    with TestClient(app) as test_client:
        test_client.db_session_factory = TestingSessionLocal
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db_session_factory(client):
    """The same session factory the API's `get_db` override uses, for tests
    that need to inspect rows the API never exposes directly (e.g. a raw
    email-verification/reset token value)."""
    return client.db_session_factory


def signup(client, email="tester@example.com", password="correcthorse123"):
    res = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()
