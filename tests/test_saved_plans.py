from tests.conftest import signup

SAMPLE_PLAN = {
    "city": "pondicherry",
    "arrival_date": "2026-10-01",
    "departure_date": "2026-10-03",
    "itinerary": {"days": [{"date": "2026-10-01", "weekday": 3, "items": []}]},
    "plan_request": {"origin_city": "Chennai"},
}


def test_save_plan_requires_auth(client):
    res = client.post("/api/saved-plans", json=SAMPLE_PLAN)
    assert res.status_code == 401


def test_save_and_list_plan(client):
    signup(client)
    saved = client.post("/api/saved-plans", json=SAMPLE_PLAN)
    assert saved.status_code == 200
    assert saved.json()["city"] == "pondicherry"

    listed = client.get("/api/saved-plans")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_get_plan_not_owner_returns_404(client):
    signup(client, email="owner@example.com")
    saved = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()

    # A second, independent identity (fresh cookie jar, same test DB) shouldn't see it.
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as stranger:
        signup(stranger, email="stranger@example.com")
        res = stranger.get(f"/api/saved-plans/{saved['id']}")
        assert res.status_code == 404


def test_update_plan_requires_edit_permission(client):
    signup(client, email="owner2@example.com")
    saved = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()

    new_itinerary = {"days": [{"date": "2026-10-01", "weekday": 3, "items": [{"id": "x"}]}]}
    res = client.put(f"/api/saved-plans/{saved['id']}", json={"itinerary": new_itinerary})
    assert res.status_code == 200
    assert res.json()["id"] == saved["id"]

    detail = client.get(f"/api/saved-plans/{saved['id']}").json()
    assert detail["itinerary"] == new_itinerary
    assert detail["is_owner"] is True
    assert detail["can_edit"] is True


def test_delete_plan(client):
    signup(client)
    saved = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    res = client.delete(f"/api/saved-plans/{saved['id']}")
    assert res.status_code == 200
    assert client.get(f"/api/saved-plans/{saved['id']}").status_code == 404
