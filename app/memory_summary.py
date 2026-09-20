"""Computes the auto-generated snapshot stored on a Memory at creation time,
from the itinerary JSON blob already saved on the trip."""
from __future__ import annotations

from typing import Any


def compute_summary(itinerary: dict[str, Any]) -> dict[str, Any]:
    days = itinerary.get("days", [])
    done = 0
    skipped = 0
    total_spend = 0
    places_visited: list[str] = []

    for day in days:
        for item in day.get("items", []):
            if item.get("kind") not in ("place", "meal"):
                continue
            status = item.get("status", "planned")
            if status == "skipped":
                skipped += 1
                continue
            done += 1
            total_spend += item.get("cost_pp", 0) or 0
            if item.get("kind") == "place":
                places_visited.append(item.get("title", ""))

    return {
        "days": len(days),
        "done_count": done,
        "skipped_count": skipped,
        "total_spend": total_spend,
        "places_visited": [p for p in places_visited if p],
    }
