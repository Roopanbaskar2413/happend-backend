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


def test_guide_chat_falls_through_to_next_model_on_non_rate_limit_error(client, monkeypatch):
    """If the first model fails for any reason (not just 429 -- e.g. a model
    name not available on this key's tier), the guide must still try the
    next model in the chain instead of giving up immediately."""
    from google.genai import errors, types

    from app.routers import guide as guide_module

    call_log = []

    class _FakeModels:
        def generate_content(self, *, model, **kwargs):
            call_log.append(model)
            if model == guide_module._model_chain()[0]:
                raise errors.ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}})
            return types.GenerateContentResponse(
                candidates=[
                    types.Candidate(
                        content=types.Content(role="model", parts=[types.Part(text="Sure, here you go!")])
                    )
                ]
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
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["reply"] == "Sure, here you go!"
    assert len(call_log) == 2  # first model failed, second one answered


def test_find_open_after_matches_manually_verified_ground_truth():
    """Cross-checked against the real Pondicherry catalog: at 22:00 on a day
    with no closures, exactly these 9 attractions are genuinely still open
    (Promenade Beach 24h, one nightlife spot, 6 turfs closing 23:00/23:59,
    and FFC Arena) -- pinned here so a future catalog edit or logic change
    that silently drops/adds one gets caught."""
    from app.engine.catalog import load_catalog
    from app.routers.guide import _find_open_after

    catalog = load_catalog("pondicherry")
    result = _find_open_after(catalog, weekday=0, time_str="22:00")

    attraction_names = {a["name"] for a in result["attractions"]}
    assert attraction_names == {
        "Promenade (Rock) Beach",
        "Rooftop bar / live music night",
        "FFC Arena - Cricket & Football Turf",
        "K7 Sports Hub",
        "Eagle Turf",
        "HTZee - Hitterz Turf Zone",
        "SRC Turf",
        "AR Sports-Verse",
        "Unity Park Turf",
    }


def test_find_open_after_excludes_places_closing_exactly_at_the_boundary():
    from app.engine.catalog import load_catalog
    from app.routers.guide import _find_open_after

    catalog = load_catalog("pondicherry")
    result = _find_open_after(catalog, weekday=0, time_str="22:00")
    all_names = {a["name"] for a in result["attractions"]} | {r["name"] for r in result["restaurants"]}
    # These close at exactly 22:00 in the catalog -- not open "at or after" 10pm.
    assert "Baker Street" not in all_names
    assert "Global Sports Badminton Court" not in all_names


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
