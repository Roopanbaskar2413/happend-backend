"""Semantic retrieval over the catalog, for fuzzy requests that don't map
to a filter ("somewhere quiet to work", "something romantic for an
anniversary") -- see guide.py's search_pois_semantic tool.

Scoring is "text-hybrid": embedding cosine similarity plus a boost for
keyword overlap against name/category/notes free text (the same
word-matching approach guide.py's _find_place already uses, extended to
the notes field, which _find_place itself doesn't search). This is the
one of three scoring variants that actually earned its place -- see
tests/evals/rag/ for the eval set and the semantic-only vs. tag-boost vs.
text-boost comparison that justified it (5/6 vs. 3/6 vs. 3/6). The eval
harness imports the scoring functions from here rather than keeping its
own copy, specifically so what's validated and what ships can't drift
apart.

Only verified catalog entries are embedded -- unverified, auto-merged
scrape output (no real descriptive text, confirmed to surface garbage
into results during the load test) is excluded at embedding-build time,
not filtered at query time.
"""
from __future__ import annotations

import json
import os

import numpy as np

EMBEDDING_MODEL = "gemini-embedding-001"

# Tuned against tests/evals/rag/cases.py -- see that eval's README/output
# for what justified this value, not a guess.
TEXT_BOOST_PER_MATCH = 0.12


def _cache_paths(city_dir: str) -> tuple[str, str]:
    return (
        os.path.join(city_dir, "embeddings.npz"),
        os.path.join(city_dir, "embeddings_meta.json"),
    )


def cache_exists(city_dir: str) -> bool:
    vecs_path, meta_path = _cache_paths(city_dir)
    return os.path.exists(vecs_path) and os.path.exists(meta_path)


def load_cache(city_dir: str):
    """Returns (vectors, items) where items[i] = {"id", "name", "category",
    "notes"} aligned with vectors[i]. Raises FileNotFoundError if the cache
    hasn't been built yet for this city -- callers should check
    cache_exists() first and degrade gracefully, same pattern as a missing
    API key elsewhere in this app."""
    vecs_path, meta_path = _cache_paths(city_dir)
    data = np.load(vecs_path)
    with open(meta_path) as f:
        items = json.load(f)
    return data["vectors"], items


def embed_query(client, query: str) -> np.ndarray:
    resp = client.models.embed_content(model=EMBEDDING_MODEL, contents=query)
    return np.array(resp.embeddings[0].values)


def cosine_similarities(vecs: np.ndarray, qvec: np.ndarray) -> np.ndarray:
    return vecs @ qvec / (np.linalg.norm(vecs, axis=1) * np.linalg.norm(qvec))


def text_overlap_counts(items: list[dict], query: str) -> np.ndarray:
    words = [w for w in query.lower().split() if w]
    counts = []
    for item in items:
        haystack = f"{item.get('name', '')} {item.get('category', '')} {item.get('notes', '')}".lower()
        counts.append(sum(1 for w in words if w in haystack))
    return np.array(counts, dtype=float)


def rank(vecs: np.ndarray, items: list[dict], qvec: np.ndarray, query: str, k: int):
    sims = cosine_similarities(vecs, qvec)
    scores = sims + TEXT_BOOST_PER_MATCH * text_overlap_counts(items, query)
    top = np.argsort(-scores)[:k]
    return [items[i]["id"] for i in top]


def similarity_to_all(city_dir: str, reference_id: str) -> dict[str, float] | None:
    """Cosine similarity from one cached item's own vector against every
    other item in the cache -- for "find something like the POI that just
    fell through" (reflow.py), not a query-driven search. No embedding API
    call: both sides are already in the cache. Returns None if the cache
    doesn't exist for this city, or reference_id isn't in it (e.g. an
    unverified catalog entry, which was never embedded in the first
    place) -- callers should treat that as "no signal", not an error."""
    if not cache_exists(city_dir):
        return None
    vecs, items = load_cache(city_dir)
    ids = [it["id"] for it in items]
    if reference_id not in ids:
        return None
    ref_idx = ids.index(reference_id)
    sims = cosine_similarities(vecs, vecs[ref_idx])
    return dict(zip(ids, (float(s) for s in sims)))
