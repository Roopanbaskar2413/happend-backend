import httpx

import app.email as email_module
from app.orm import EmailToken, User
from tests.conftest import signup


def _latest_token(db_factory, email, purpose):
    db = db_factory()
    try:
        user = db.query(User).filter(User.email == email).first()
        return (
            db.query(EmailToken)
            .filter(EmailToken.user_id == user.id, EmailToken.purpose == purpose)
            .order_by(EmailToken.created_at.desc())
            .first()
        )
    finally:
        db.close()


def test_signup_creates_unverified_user(client):
    body = signup(client, email="new@example.com")
    assert body["email_verified"] is False


def test_signup_succeeds_even_if_email_provider_rejects_the_send(client, monkeypatch):
    # Regression test: Resend's sandbox sender only delivers to the account
    # owner's own address, so any *other* real user's signup would otherwise
    # hit this exact failure — it must not take the whole request down with it.
    monkeypatch.setattr(email_module, "RESEND_API_KEY", "fake-key-for-test")

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("simulated provider failure")

    monkeypatch.setattr(email_module.httpx, "post", _boom)

    body = signup(client, email="providerdown@example.com")
    assert body["email_verified"] is False


def test_verify_email_marks_user_verified(client, db_session_factory):
    signup(client, email="verifyme@example.com")
    token = _latest_token(db_session_factory, "verifyme@example.com", "verify")
    assert token is not None

    res = client.post("/api/auth/verify-email", json={"token": token.id})
    assert res.status_code == 200
    assert res.json()["email_verified"] is True

    me = client.get("/api/auth/me")
    assert me.json()["email_verified"] is True


def test_verify_email_rejects_bad_token(client):
    res = client.post("/api/auth/verify-email", json={"token": "not-a-real-token"})
    assert res.status_code == 400


def test_verify_email_token_is_single_use(client, db_session_factory):
    signup(client, email="onceonly@example.com")
    token = _latest_token(db_session_factory, "onceonly@example.com", "verify")

    first = client.post("/api/auth/verify-email", json={"token": token.id})
    assert first.status_code == 200
    second = client.post("/api/auth/verify-email", json={"token": token.id})
    assert second.status_code == 400


def test_resend_verification_requires_auth(client):
    res = client.post("/api/auth/resend-verification")
    assert res.status_code == 401


def test_resend_verification_noop_once_verified(client, db_session_factory):
    signup(client, email="resend@example.com")
    token = _latest_token(db_session_factory, "resend@example.com", "verify")
    client.post("/api/auth/verify-email", json={"token": token.id})

    res = client.post("/api/auth/resend-verification")
    assert res.status_code == 200
    assert res.json()["already_verified"] is True


def test_forgot_password_generic_response_regardless_of_email(client):
    known = client.post("/api/auth/forgot-password", json={"email": "unknown@example.com"})
    assert known.status_code == 200
    assert known.json() == {"ok": True}


def test_reset_password_changes_password_and_kills_sessions(client, db_session_factory):
    signup(client, email="reset@example.com", password="oldpassword123")

    forgot = client.post("/api/auth/forgot-password", json={"email": "reset@example.com"})
    assert forgot.status_code == 200
    token = _latest_token(db_session_factory, "reset@example.com", "reset")
    assert token is not None

    reset = client.post(
        "/api/auth/reset-password", json={"token": token.id, "new_password": "newpassword456"}
    )
    assert reset.status_code == 200

    # The session created at signup must now be dead.
    assert client.get("/api/auth/me").status_code == 401

    # Old password no longer works, new one does.
    bad = client.post("/api/auth/login", json={"email": "reset@example.com", "password": "oldpassword123"})
    assert bad.status_code == 401
    good = client.post("/api/auth/login", json={"email": "reset@example.com", "password": "newpassword456"})
    assert good.status_code == 200


def test_reset_password_token_is_single_use(client, db_session_factory):
    signup(client, email="resetonce@example.com")
    client.post("/api/auth/forgot-password", json={"email": "resetonce@example.com"})
    token = _latest_token(db_session_factory, "resetonce@example.com", "reset")

    first = client.post("/api/auth/reset-password", json={"token": token.id, "new_password": "abcdefgh1"})
    assert first.status_code == 200
    second = client.post("/api/auth/reset-password", json={"token": token.id, "new_password": "zzzzzzzz9"})
    assert second.status_code == 400


def test_reset_password_rejects_short_password(client, db_session_factory):
    signup(client, email="shortpw@example.com")
    client.post("/api/auth/forgot-password", json={"email": "shortpw@example.com"})
    token = _latest_token(db_session_factory, "shortpw@example.com", "reset")

    res = client.post("/api/auth/reset-password", json={"token": token.id, "new_password": "short"})
    assert res.status_code == 400
