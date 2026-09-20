from app.memory_summary import compute_summary


def _item(kind, status="planned", cost_pp=0, title="Place"):
    return {"kind": kind, "status": status, "cost_pp": cost_pp, "title": title}


def test_compute_summary_counts_done_and_skipped():
    itinerary = {
        "days": [
            {
                "items": [
                    _item("place", "planned", 100, "A"),
                    _item("place", "skipped", 50, "B"),
                    _item("meal", "planned", 300, "Lunch"),
                    _item("travel", "planned", 0),  # connectors never count
                ]
            },
            {"items": [_item("place", "done", 0, "C")]},
        ]
    }
    summary = compute_summary(itinerary)
    assert summary["days"] == 2
    assert summary["done_count"] == 3  # A, Lunch, C (skipped B excluded)
    assert summary["skipped_count"] == 1
    assert summary["total_spend"] == 400  # 100 + 300 + 0, not the skipped 50
    assert summary["places_visited"] == ["A", "C"]  # meals aren't "places"


def test_compute_summary_handles_empty_itinerary():
    summary = compute_summary({"days": []})
    assert summary == {
        "days": 0,
        "done_count": 0,
        "skipped_count": 0,
        "total_spend": 0,
        "places_visited": [],
    }
