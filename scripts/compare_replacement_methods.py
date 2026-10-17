"""Before/after comparison: old (word/tag-overlap against the removed POI)
vs. new (embedding similarity to the removed POI) for finding a
replacement when something becomes unavailable mid-trip.

Honest framing up front: reflow.py's ACTUAL current replanning mechanism
(app/engine/scoring.py's score_place) is NOT word-overlap matching -- it's
a multi-factor score based on the user's stored trip interests, time slot,
budget, and travel time. It never compares a candidate against the
specific POI that fell through at all; it just asks "what's good for this
trip in general." So there's no existing "find something similar to X"
baseline in production to literally replace.

What this script actually evaluates is the standalone question: if you
wanted to add a "similar to what fell through" signal, is embedding
similarity better than the simplest word/tag-overlap approach (the same
style _find_place already uses elsewhere in the app)? This is a
prerequisite check before deciding whether/how to fold that signal into
score_place -- not a drop-in replacement for it.

Usage:
    cd backend && .venv/bin/python scripts/compare_replacement_methods.py
"""
from __future__ import annotations

import json
import os

import numpy as np

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "pondicherry")


def load_cache():
    data = np.load(os.path.join(CACHE_DIR, "embeddings.npz"))
    with open(os.path.join(CACHE_DIR, "embeddings_meta.json")) as f:
        items = json.load(f)
    return data["vectors"], items


def load_full_catalog_fields():
    """embeddings_meta.json doesn't carry `kind`/`interests` the way the
    eval's copy does -- load those fields fresh from the real catalog so
    the old (tag-overlap) method has the same signal _find_place uses."""
    with open(os.path.join(CACHE_DIR, "places.json")) as f:
        data = json.load(f)
    fields = {}
    for p in data["places"]:
        fields[p["id"]] = {"kind": "place", "interests": p.get("interests") or []}
    for f in data["food"]:
        fields[f["id"]] = {"kind": "food", "interests": f.get("meals") or []}
    return fields


def old_tag_overlap_rank(removed_id: str, candidates: list[int], items: list[dict], fields: dict, k: int):
    removed_tags = set(fields.get(removed_id, {}).get("interests", []))
    scored = []
    for i in candidates:
        cand_id = items[i]["id"]
        cand_tags = set(fields.get(cand_id, {}).get("interests", []))
        overlap = len(removed_tags & cand_tags)
        scored.append((overlap, i))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def new_embedding_rank(vecs: np.ndarray, removed_idx: int, candidates: list[int], k: int):
    removed_vec = vecs[removed_idx]
    sims = []
    for i in candidates:
        sim = np.dot(vecs[i], removed_vec) / (np.linalg.norm(vecs[i]) * np.linalg.norm(removed_vec))
        sims.append((sim, i))
    sims.sort(key=lambda x: -x[0])
    return sims[:k]


def run_scenario(removed_id: str, vecs, items, fields, k=5):
    removed_idx = next(i for i, it in enumerate(items) if it["id"] == removed_id)
    removed_kind = fields.get(removed_id, {}).get("kind")
    # Candidate pool: same kind (place vs food), excluding the removed item
    # itself -- "find something else in the same broad category" is the
    # realistic framing for a mid-trip swap.
    candidates = [i for i, it in enumerate(items) if it["id"] != removed_id and fields.get(it["id"], {}).get("kind") == removed_kind]

    print(f"\n{'=' * 70}")
    print(f"REMOVED: {items[removed_idx]['name']}  ({removed_id})")
    print(f"  notes: {items[removed_idx].get('notes', '')[:140]}")
    print(f"  interests/meals: {fields.get(removed_id, {}).get('interests')}")

    print(f"\n  OLD (tag/word overlap against removed item's own tags):")
    for overlap, i in old_tag_overlap_rank(removed_id, candidates, items, fields, k):
        print(f"    overlap={overlap}  {items[i]['name']}")

    print(f"\n  NEW (embedding similarity to removed item):")
    for sim, i in new_embedding_rank(vecs, removed_idx, candidates, k):
        print(f"    sim={sim:.3f}  {items[i]['name']}")


def main():
    vecs, items = load_cache()
    fields = load_full_catalog_fields()

    for removed_id in ["wtf_way_to_fly_restopub", "chunnambar_boat_house", "sunset_promenade"]:
        run_scenario(removed_id, vecs, items, fields)


if __name__ == "__main__":
    main()
