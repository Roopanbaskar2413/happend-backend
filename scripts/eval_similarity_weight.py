"""Validates SIMILARITY_WEIGHT before trusting it -- same discipline as
TEXT_BOOST_PER_MATCH in semantic_search.py. Runs the REAL replan() (not a
standalone similarity calculation) across a few closed/skip disruption
scenarios on real generated itineraries, at three weight values (1.5, 3.0,
5.0), and prints each scenario's FULL resulting tail -- not just the top
candidate -- so the weight's effect on the whole itinerary ranking (once
combined with interest/slot/budget/travel terms) can actually be judged.

Pure local computation: no API calls, no database, no deploy-related
anything. Uses the already-committed embeddings cache
(app/data/pondicherry/embeddings.npz).

Usage:
    cd backend && .venv/bin/python scripts/eval_similarity_weight.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import scoring
from app.engine.catalog import load_catalog
from app.engine.engine import generate, time_to_minutes
from app.engine.models import PlanRequest
from app.engine.reflow import Disruption, replan

WEIGHTS_TO_TRY = [1.5, 3.0, 5.0]


def make_request(**overrides):
    base = dict(
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
    base.update(overrides)
    return PlanRequest(**base)


def real_items(day):
    return [i for i in day.items if i.kind not in ("travel", "transfer")]


def describe_tail(day, from_minutes: int) -> list[str]:
    # The closed/skipped item's own id no longer exists in the regenerated
    # day -- select by time instead of trying to find its id.
    return [
        f"{it.start}-{it.end}  {it.title}"
        for it in real_items(day)
        if time_to_minutes(it.start) >= from_minutes
    ]


def run_scenario(name: str, catalog, interests: list[str], kind: str):
    req = make_request(interests=interests)
    itinerary = generate(req, catalog)
    day0 = itinerary.days[0]
    items = real_items(day0)
    # Specifically a *place* -- food goes through _score_food, not
    # score_place, so closing a food item would never exercise this signal
    # at all (the exact mistake the first run of this eval made by accident).
    place_items = [i for i in items if i.kind == "place"]
    if not place_items:
        print(f"\n[{name}] SKIPPED -- day 0 has no place stops to close")
        return
    target = place_items[0]

    print(f"\n{'=' * 70}")
    print(f"[{name}] closing: {target.title} ({target.ref_id})")
    print(f"  interests: {interests}")

    disruption = Disruption(type=kind, day_index=0, item_id=target.id)
    cutoff_minutes = time_to_minutes(target.start)

    for weight in [0.0, *WEIGHTS_TO_TRY]:
        original = scoring.SIMILARITY_WEIGHT
        scoring.SIMILARITY_WEIGHT = weight
        try:
            result = replan(itinerary, disruption, catalog, req)
        finally:
            scoring.SIMILARITY_WEIGHT = original

        label = "weight=0.0 (no similarity signal -- current behavior)" if weight == 0.0 else f"weight={weight}"
        print(f"\n  --- {label} ---")
        for line in describe_tail(result.itinerary.days[0], cutoff_minutes):
            print(f"    {line}")


def main():
    catalog = load_catalog("pondicherry")
    run_scenario("default_interests", catalog, ["beach", "heritage", "culture", "nature", "walk", "photography", "food"], "closed")
    run_scenario("nightlife_leaning", catalog, ["nightlife", "food", "chill"], "skip")
    run_scenario("nature_leaning", catalog, ["nature", "adventure", "water"], "closed")


if __name__ == "__main__":
    main()
