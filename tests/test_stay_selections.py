from app.orm import StaySelection


def test_logging_a_real_stay_selection_persists_a_row(client, db_session_factory):
    res = client.post("/api/stays/selections", json={"city": "pondicherry", "stay_id": "zostel"})
    assert res.status_code == 204

    db = db_session_factory()
    try:
        rows = db.query(StaySelection).all()
        assert len(rows) == 1
        assert rows[0].city == "pondicherry"
        assert rows[0].stay_id == "zostel"
    finally:
        db.close()


def test_logging_an_unknown_stay_is_silently_ignored(client, db_session_factory):
    res = client.post(
        "/api/stays/selections", json={"city": "pondicherry", "stay_id": "not_a_real_stay"}
    )
    assert res.status_code == 204

    db = db_session_factory()
    try:
        assert db.query(StaySelection).count() == 0
    finally:
        db.close()


def test_logging_for_an_unknown_city_is_silently_ignored(client, db_session_factory):
    res = client.post("/api/stays/selections", json={"city": "atlantis", "stay_id": "zostel"})
    assert res.status_code == 204

    db = db_session_factory()
    try:
        assert db.query(StaySelection).count() == 0
    finally:
        db.close()
