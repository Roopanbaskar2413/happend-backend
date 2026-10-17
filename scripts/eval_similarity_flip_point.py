"""Finds a real close-contest pair (two candidates whose score_place
scores, WITHOUT the similarity term, are nearly tied) for one specific
replacement slot, then sweeps SIMILARITY_WEIGHT to see at what value, if
any, the new term flips the choice between them.

Usage:
    cd backend && .venv/bin/python scripts/eval_similarity_flip_point.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import engine as eng
from app.engine import scoring
from app.engine.catalog import load_catalog
from app.engine.engine import generate
from app.engine.models import PlanRequest
from app.engine.reflow import Disruption, replan


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


def main():
    catalog = load_catalog("pondicherry")
    req = make_request()
    itinerary = generate(req, catalog)
    day0 = itinerary.days[0]
    items = real_items(day0)
    place_items = [i for i in items if i.kind == "place"]
    target = place_items[0]
    print(f"Closing: {target.title} ({target.ref_id})")

    disruption = Disruption(type="closed", day_index=0, item_id=target.id)

    # Capture every (place, score_without_similarity, similarity) considered
    # for the very first candidate-scoring pass after the cutoff, by
    # wrapping score_place to log before it's called for real.
    captured = []
    original_score_place = scoring.score_place

    def spying_score_place(place, request, **kwargs):
        sim = kwargs.get("similarity_to_removed")
        kwargs_no_sim = dict(kwargs)
        kwargs_no_sim["similarity_to_removed"] = None
        base_score = original_score_place(place, request, **kwargs_no_sim)
        if sim is not None:  # only true for the one slot that matters
            captured.append((place.name, place.id, base_score, sim))
        return original_score_place(place, request, **kwargs)

    scoring.score_place = spying_score_place
    eng.score_place = spying_score_place
    try:
        replan(itinerary, disruption, catalog, req)
    finally:
        scoring.score_place = original_score_place
        eng.score_place = original_score_place

    if not captured:
        print("No candidates captured -- the similarity signal never reached score_place for this scenario.")
        return

    captured.sort(key=lambda c: -c[2])
    print(f"\n{len(captured)} real candidates considered for this slot (base score = no similarity term):")
    for name, pid, base_score, sim in captured[:8]:
        print(f"  base={base_score:+.3f}  sim={sim:.3f}  {name}")

    # Find the closest real pair among the top candidates by base score.
    top = captured[:6]
    best_pair = None
    best_gap = None
    for i in range(len(top)):
        for j in range(len(top)):
            if i == j:
                continue
            gap = top[i][2] - top[j][2]
            if gap > 0 and (best_gap is None or gap < best_gap):
                best_gap = gap
                best_pair = (top[i], top[j])

    if best_pair is None:
        print("\nNo close pair found among top candidates.")
        return

    leader, challenger = best_pair
    print(f"\nClosest real pair: {leader[0]!r} (base={leader[2]:+.3f}) vs {challenger[0]!r} (base={challenger[2]:+.3f})")
    print(f"Base score gap: {best_gap:.4f}")
    print(f"Similarities: {leader[0]}={leader[3]:.3f}, {challenger[0]}={challenger[3]:.3f}")

    if challenger[3] <= leader[3]:
        print("\nThe challenger isn't even more similar than the leader -- the similarity term would push the WRONG direction here. Picking a different pair isn't needed per instructions; reporting as-is.")

    sim_diff = challenger[3] - leader[3]
    if sim_diff <= 0:
        print("No weight can flip this pair in the challenger's favor (challenger isn't more similar).")
        return

    weight_needed = best_gap / sim_diff
    print(f"\nWeight needed to flip this exact pair: {weight_needed:.3f}")
    for w in [1.5, 3.0, 5.0, weight_needed]:
        flips = w * sim_diff > best_gap
        print(f"  weight={w:.3f}: {'FLIPS to ' + challenger[0] if flips else 'no flip, stays ' + leader[0]}")


if __name__ == "__main__":
    main()
