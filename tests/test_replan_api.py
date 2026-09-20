def make_plan_request(client):
    body = dict(
        city="pondicherry",
        origin_city="Chennai",
        transport_mode="bus",
        arrival_date="2026-09-21",
        arrival_time="07:00",
        departure_date="2026-09-23",
        departure_time="20:00",
        group_type="couple",
        party_size=2,
        budget_level="mid",
        pace="packed",
        diet="any",
        interests=["beach", "heritage", "culture", "nature", "walk", "photography", "food"],
        has_own_vehicle=True,
        has_own_stay=True,
        stay_id=None,
    )
    itinerary = client.post("/api/plan", json=body).json()
    return body, itinerary


def real_items(day):
    return [i for i in day["items"] if i["kind"] not in ("travel", "transfer")]


def test_replan_running_late_returns_shorter_plan_and_changes(client):
    plan_request, itinerary = make_plan_request(client)
    items = real_items(itinerary["days"][0])
    now_time = items[len(items) // 2]["start"]

    res = client.post(
        "/api/replan",
        json={
            "city": "pondicherry",
            "itinerary": itinerary,
            "plan_request": plan_request,
            "disruption": {"type": "running_late", "day_index": 0, "now_time": now_time},
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert "itinerary" in body and "changes" in body
    assert len(body["changes"]) >= 1
    assert all(c["reason"] for c in body["changes"])


def test_replan_unknown_city_returns_404(client):
    _plan_request, itinerary = make_plan_request(client)
    res = client.post(
        "/api/replan",
        json={
            "city": "narnia",
            "itinerary": itinerary,
            "plan_request": {
                "city": "narnia",
                "origin_city": "Chennai",
                "transport_mode": "bus",
                "arrival_date": "2026-09-21",
                "arrival_time": "07:00",
                "departure_date": "2026-09-23",
                "departure_time": "20:00",
                "group_type": "couple",
                "party_size": 2,
                "budget_level": "mid",
                "pace": "packed",
                "diet": "any",
                "interests": [],
                "has_own_vehicle": True,
                "has_own_stay": True,
                "stay_id": None,
            },
            "disruption": {"type": "energy", "day_index": 0},
        },
    )
    assert res.status_code == 404


def test_replan_bad_disruption_returns_400(client):
    plan_request, itinerary = make_plan_request(client)
    res = client.post(
        "/api/replan",
        json={
            "city": "pondicherry",
            "itinerary": itinerary,
            "plan_request": plan_request,
            "disruption": {"type": "closed", "day_index": 0, "item_id": "does_not_exist"},
        },
    )
    assert res.status_code == 400
