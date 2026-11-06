from tests.conftest import signup


def test_signup_creates_user_and_logs_in(client):
    body = signup(client)
    assert body["email"] == "tester@example.com"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "tester@example.com"


def test_signup_duplicate_email_rejected(client):
    signup(client, email="dup@example.com")
    res = client.post(
        "/api/auth/signup", json={"email": "dup@example.com", "password": "anotherpassword"}
    )
    assert res.status_code == 409


def test_signup_short_password_rejected(client):
    res = client.post("/api/auth/signup", json={"email": "short@example.com", "password": "abc"})
    assert res.status_code == 400


def test_me_without_session_returns_null_not_401(client):
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json() is None


def test_login_wrong_password_generic_error(client):
    signup(client, email="realuser@example.com")
    res = client.post(
        "/api/auth/login", json={"email": "realuser@example.com", "password": "wrongpassword"}
    )
    assert res.status_code == 401

    # An unknown email should fail the same way, so login can't be used to
    # discover which emails have accounts.
    res_unknown = client.post(
        "/api/auth/login", json={"email": "nosuchuser@example.com", "password": "wrongpassword"}
    )
    assert res_unknown.status_code == 401
    assert res_unknown.json()["detail"] == res.json()["detail"]


def test_login_success(client):
    signup(client, email="loginok@example.com", password="correcthorse123")
    client.post("/api/auth/logout")
    res = client.post(
        "/api/auth/login", json={"email": "loginok@example.com", "password": "correcthorse123"}
    )
    assert res.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_logout_invalidates_session(client):
    signup(client)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    # A logged-out /me is 200 + null, not a 401 (see app/routers/auth.py's
    # `me`) -- it's the expected, common case on every page load, not an
    # error.
    after_logout = client.get("/api/auth/me")
    assert after_logout.status_code == 200
    assert after_logout.json() is None


def test_login_rate_limited_after_repeated_attempts(client):
    signup(client, email="ratelimited@example.com", password="correcthorse123")
    client.post("/api/auth/logout")

    statuses = []
    for _ in range(12):
        res = client.post(
            "/api/auth/login",
            json={"email": "ratelimited@example.com", "password": "wrongpassword"},
        )
        statuses.append(res.status_code)

    assert statuses.count(401) == 10
    assert statuses[10:] == [429, 429]
