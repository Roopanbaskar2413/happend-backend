import io

from PIL import Image

from app import storage
from tests.conftest import signup

SAMPLE_PLAN = {
    "city": "pondicherry",
    "arrival_date": "2026-10-01",
    "departure_date": "2026-10-03",
    "itinerary": {
        "days": [
            {
                "date": "2026-10-01",
                "weekday": 3,
                "items": [
                    {"id": "i1", "kind": "place", "status": "done", "cost_pp": 100, "title": "Museum"},
                    {"id": "i2", "kind": "place", "status": "skipped", "cost_pp": 50, "title": "Skipped"},
                ],
            }
        ]
    },
    "plan_request": {"origin_city": "Chennai"},
}


def _png_bytes(size=(10, 10)):
    buf = io.BytesIO()
    Image.new("RGB", size, color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _save_and_complete_plan(client):
    plan = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    res = client.patch(f"/api/saved-plans/{plan['id']}/status", json={"status": "completed"})
    assert res.status_code == 200, res.text
    return plan


def test_saved_plan_defaults_to_upcoming(client):
    signup(client)
    plan = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    assert plan["status"] == "upcoming"


def test_update_status_rejects_unknown_value(client):
    signup(client)
    plan = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    res = client.patch(f"/api/saved-plans/{plan['id']}/status", json={"status": "vacationing"})
    assert res.status_code == 400


def test_create_memory_requires_completed_trip(client):
    signup(client)
    plan = client.post("/api/saved-plans", json=SAMPLE_PLAN).json()
    res = client.post("/api/memories", json={"saved_plan_id": plan["id"]})
    assert res.status_code == 400


def test_create_memory_computes_summary_from_itinerary(client):
    signup(client)
    plan = _save_and_complete_plan(client)

    res = client.post("/api/memories", json={"saved_plan_id": plan["id"]})
    assert res.status_code == 200, res.text
    memory = res.json()
    assert memory["summary"]["done_count"] == 1
    assert memory["summary"]["skipped_count"] == 1
    assert memory["summary"]["total_spend"] == 100
    assert memory["stories"] == []
    assert memory["photos"] == []


def test_create_memory_is_idempotent_per_plan(client):
    # Regression test: re-requesting a memory for the same plan (e.g.
    # clicking "Add memory" again after navigating away) must continue the
    # existing one, not create a duplicate.
    signup(client)
    plan = _save_and_complete_plan(client)

    first = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()
    second = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()
    assert first["id"] == second["id"]

    all_memories = client.get("/api/memories").json()
    assert len(all_memories) == 1


def test_memory_not_accessible_by_non_owner(client):
    signup(client, email="owner@example.com")
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as stranger:
        signup(stranger, email="stranger@example.com")
        assert stranger.get(f"/api/memories/{memory['id']}").status_code == 404


def test_list_memories_returns_only_current_users(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    client.post("/api/memories", json={"saved_plan_id": plan["id"]})

    res = client.get("/api/memories")
    assert res.status_code == 200
    assert len(res.json()) == 1


# --- Stories ---------------------------------------------------------------


def test_add_update_delete_story(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    added = client.post(f"/api/memories/{memory['id']}/stories", json={"text": "Watched the sunrise."})
    assert added.status_code == 200
    story = added.json()
    assert story["text"] == "Watched the sunrise."

    updated = client.put(
        f"/api/memories/{memory['id']}/stories/{story['id']}", json={"text": "Watched an amazing sunrise."}
    )
    assert updated.status_code == 200
    assert updated.json()["text"] == "Watched an amazing sunrise."

    got = client.get(f"/api/memories/{memory['id']}")
    assert len(got.json()["stories"]) == 1

    deleted = client.delete(f"/api/memories/{memory['id']}/stories/{story['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/memories/{memory['id']}").json()["stories"] == []


def test_add_story_rejects_empty_text(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    res = client.post(f"/api/memories/{memory['id']}/stories", json={"text": "   "})
    assert res.status_code == 400


# --- Photos ------------------------------------------------------------------


def test_upload_valid_photo_and_retrieve_it(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    upload = client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("beach.png", _png_bytes(), "image/png")},
    )
    assert upload.status_code == 200, upload.text
    photo = upload.json()
    assert photo["content_type"] == "image/png"

    fetched = client.get(f"/api/memories/{memory['id']}/photos/{photo['id']}")
    assert fetched.status_code == 200
    assert fetched.content == _png_bytes()


def test_upload_rejects_non_image_content_type(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    res = client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert res.status_code == 400


def test_upload_rejects_bytes_that_arent_really_an_image(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    res = client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("fake.png", b"not actually a png", "image/png")},
    )
    assert res.status_code == 400


def test_upload_rejects_oversized_photo(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    oversized = b"\x00" * (8 * 1024 * 1024 + 1)
    res = client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("huge.png", oversized, "image/png")},
    )
    assert res.status_code == 400


def test_delete_photo_removes_it(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()
    photo = client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("beach.png", _png_bytes(), "image/png")},
    ).json()

    res = client.delete(f"/api/memories/{memory['id']}/photos/{photo['id']}")
    assert res.status_code == 200
    assert client.get(f"/api/memories/{memory['id']}/photos/{photo['id']}").status_code == 404


def _mp3_bytes():
    # A minimal, valid-enough MP3 frame header isn't necessary here — the
    # endpoint only checks declared content-type + size, not real audio
    # decoding (unlike photos, which are pixel-verified).
    return b"ID3" + b"\x00" * 100


def test_memory_has_music_false_until_uploaded(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()
    assert memory["has_music"] is False


def test_upload_and_delete_music(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    upload = client.post(
        f"/api/memories/{memory['id']}/music",
        files={"file": ("trip.mp3", _mp3_bytes(), "audio/mpeg")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["has_music"] is True

    fetched = client.get(f"/api/memories/{memory['id']}/music")
    assert fetched.status_code == 200
    assert fetched.content == _mp3_bytes()

    deleted = client.delete(f"/api/memories/{memory['id']}/music")
    assert deleted.status_code == 200
    assert deleted.json()["has_music"] is False
    assert client.get(f"/api/memories/{memory['id']}/music").status_code == 404


def test_upload_music_rejects_bad_content_type(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    res = client.post(
        f"/api/memories/{memory['id']}/music",
        files={"file": ("trip.pdf", b"not audio", "application/pdf")},
    )
    assert res.status_code == 400


def test_uploading_new_music_replaces_old(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()

    client.post(
        f"/api/memories/{memory['id']}/music", files={"file": ("a.mp3", _mp3_bytes(), "audio/mpeg")}
    )
    music_dir = list(storage.LOCAL_UPLOADS_DIR.glob(f"*/{memory['id']}"))[0]
    assert len(list(music_dir.iterdir())) == 1

    client.post(
        f"/api/memories/{memory['id']}/music", files={"file": ("b.mp3", _mp3_bytes(), "audio/mpeg")}
    )
    # The old file should be gone, not left behind alongside the new one.
    assert len(list(music_dir.iterdir())) == 1


def test_delete_memory_cascades_stories_and_photos(client):
    signup(client)
    plan = _save_and_complete_plan(client)
    memory = client.post("/api/memories", json={"saved_plan_id": plan["id"]}).json()
    client.post(f"/api/memories/{memory['id']}/stories", json={"text": "hi"})
    client.post(
        f"/api/memories/{memory['id']}/photos",
        files={"file": ("beach.png", _png_bytes(), "image/png")},
    )

    memory_dirs = list(storage.LOCAL_UPLOADS_DIR.glob(f"*/{memory['id']}"))
    assert len(memory_dirs) == 1
    assert list(memory_dirs[0].iterdir()), "photo file should exist on disk before delete"

    res = client.delete(f"/api/memories/{memory['id']}")
    assert res.status_code == 200
    assert client.get(f"/api/memories/{memory['id']}").status_code == 404
    assert not list(memory_dirs[0].iterdir()), "photo file should be gone from disk after delete"
