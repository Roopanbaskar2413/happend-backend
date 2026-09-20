from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import signup

SAMPLE_PLAN = {
    "city": "pondicherry",
    "arrival_date": "2026-10-01",
    "departure_date": "2026-10-03",
    "itinerary": {"days": [{"date": "2026-10-01", "weekday": 3, "items": []}]},
    "plan_request": {"origin_city": "Chennai"},
}


def _owner_and_friend(client):
    """`client` (from the fixture) is the owner; returns a second, independent
    TestClient (separate cookie jar, same in-memory test DB) as the friend."""
    signup(client, email="owner@example.com")
    plan = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    friend = TestClient(app)
    friend.__enter__()
    signup(friend, email="friend@example.com")
    return plan, friend


def test_share_grants_viewer_by_default(client):
    plan, friend = _owner_and_friend(client)
    try:
        share = client.post(f"/api/saved-plans/{plan['id']}/shares", json={"email": "friend@example.com"})
        assert share.status_code == 200
        assert share.json()["role"] == "viewer"

        seen = friend.get(f"/api/saved-plans/{plan['id']}")
        assert seen.status_code == 200
        assert seen.json()["can_edit"] is False
        assert seen.json()["is_owner"] is False
    finally:
        friend.__exit__(None, None, None)


def test_viewer_cannot_edit_until_approved(client):
    plan, friend = _owner_and_friend(client)
    try:
        client.post(f"/api/saved-plans/{plan['id']}/shares", json={"email": "friend@example.com"})

        blocked = friend.put(f"/api/saved-plans/{plan['id']}", json={"itinerary": {"days": []}})
        assert blocked.status_code == 403

        request_res = friend.post(f"/api/saved-plans/{plan['id']}/request-edit")
        assert request_res.status_code == 200
        assert request_res.json()["already_editor"] is False

        shares = client.get(f"/api/saved-plans/{plan['id']}/shares").json()
        assert len(shares) == 1
        assert shares[0]["edit_requested"] is True

        approve = client.post(
            f"/api/saved-plans/{plan['id']}/shares/{shares[0]['id']}/approve-edit"
        )
        assert approve.status_code == 200
        assert approve.json()["role"] == "editor"

        allowed = friend.put(f"/api/saved-plans/{plan['id']}", json={"itinerary": {"days": [{"date": "edited"}]}})
        assert allowed.status_code == 200

        owner_view = client.get(f"/api/saved-plans/{plan['id']}").json()
        assert owner_view["itinerary"] == {"days": [{"date": "edited"}]}
    finally:
        friend.__exit__(None, None, None)


def test_deny_edit_keeps_viewer(client):
    plan, friend = _owner_and_friend(client)
    try:
        client.post(f"/api/saved-plans/{plan['id']}/shares", json={"email": "friend@example.com"})
        friend.post(f"/api/saved-plans/{plan['id']}/request-edit")
        shares = client.get(f"/api/saved-plans/{plan['id']}/shares").json()

        deny = client.post(f"/api/saved-plans/{plan['id']}/shares/{shares[0]['id']}/deny-edit")
        assert deny.status_code == 200
        assert deny.json()["role"] == "viewer"
        assert deny.json()["edit_requested"] is False

        still_blocked = friend.put(f"/api/saved-plans/{plan['id']}", json={"itinerary": {"days": []}})
        assert still_blocked.status_code == 403
    finally:
        friend.__exit__(None, None, None)


def test_revoke_share_removes_access(client):
    plan, friend = _owner_and_friend(client)
    try:
        share = client.post(
            f"/api/saved-plans/{plan['id']}/shares", json={"email": "friend@example.com"}
        ).json()
        assert friend.get(f"/api/saved-plans/{plan['id']}").status_code == 200

        revoke = client.delete(f"/api/saved-plans/{plan['id']}/shares/{share['id']}")
        assert revoke.status_code == 200

        assert friend.get(f"/api/saved-plans/{plan['id']}").status_code == 404
    finally:
        friend.__exit__(None, None, None)


def test_shared_with_me_lists_the_plan(client):
    plan, friend = _owner_and_friend(client)
    try:
        client.post(f"/api/saved-plans/{plan['id']}/shares", json={"email": "friend@example.com"})
        mine = friend.get("/api/shared-with-me")
        assert mine.status_code == 200
        assert len(mine.json()) == 1
        assert mine.json()[0]["owner_email"] == "owner@example.com"
        assert mine.json()[0]["role"] == "viewer"
    finally:
        friend.__exit__(None, None, None)


def test_only_owner_can_share(client):
    plan, friend = _owner_and_friend(client)
    try:
        res = friend.post(
            f"/api/saved-plans/{plan['id']}/shares", json={"email": "someoneelse@example.com"}
        )
        assert res.status_code == 404  # friend doesn't own the plan
    finally:
        friend.__exit__(None, None, None)
