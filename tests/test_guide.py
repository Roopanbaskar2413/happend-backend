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


def test_guide_chat_never_surfaces_a_raw_rate_limit_error(client, monkeypatch):
    """Every model in the fallback chain is rate-limited — the guide must
    still answer in character (200 + canned reply), never a raw error."""
    from google.genai import errors

    from app.routers import guide as guide_module

    class _FakeModels:
        def generate_content(self, **kwargs):
            raise errors.ClientError(
                429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}}
            )

    class _FakeClient:
        def __init__(self, api_key=None):
            self.models = _FakeModels()

    monkeypatch.setattr(guide_module, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(guide_module, "Client", _FakeClient)
    monkeypatch.setattr(guide_module, "_model_cooldowns", {})

    res = client.post(
        "/api/guide/chat",
        json={
            "city": "pondicherry",
            "day": {"weekday": 0, "items": []},
            "contents": [{"role": "user", "parts": [{"text": "add the museum"}]}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == guide_module.FALLBACK_REPLY
    assert "429" not in body["reply"]
    assert "quota" not in body["reply"].lower()
    assert "rate" not in body["reply"].lower()


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
