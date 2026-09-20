def test_guide_chat_without_api_key_returns_503(client):
    res = client.post(
        "/api/guide/chat",
        json={
            "city": "pondicherry",
            "day": {"weekday": 0, "items": []},
            "contents": [{"role": "user", "parts": [{"text": "add the museum"}]}],
        },
    )
    assert res.status_code == 503


def test_guide_chat_rejects_unknown_city(client, monkeypatch):
    monkeypatch.setattr("app.routers.guide.GEMINI_API_KEY", "fake-key-for-test")
    res = client.post(
        "/api/guide/chat",
        json={
            "city": "atlantis",
            "day": {"weekday": 0, "items": []},
            "contents": [{"role": "user", "parts": [{"text": "add the museum"}]}],
        },
    )
    assert res.status_code == 404
