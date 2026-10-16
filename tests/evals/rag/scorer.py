"""Semantic-only vs. hybrid (semantic + interests-tag boost) scoring over
the cached catalog embeddings. See build_embeddings.py for how the cache
is produced.

Hybrid rationale: the load test showed pure cosine similarity picks up
thematic/devotional "quiet" (temples, a basilica) over the specific
"a place to sit and work" sense, missing a place explicitly tagged
"quiet" with a real description (Romain Rolland Library). The interests
tags are precise, human-curated signal that raw embedding similarity
throws away -- boosting on an exact tag-word match recovers it without
needing a bigger/different embedding model.
"""
from __future__ import annotations

import os

import numpy as np

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
EMBEDDING_MODEL = "gemini-embedding-001"

# Tuned empirically against the eval set in cases.py, not guessed -- see
# run_rag_eval.py's output for the semantic-only vs hybrid comparison that
# justified this value.
TAG_BOOST_PER_MATCH = 0.12
# Same boost magnitude, applied to word-overlap against name/category/notes
# instead of the curated interests list -- the hypothesis being tested is
# that a distinguishing word ("library", "work") lives in the free-text
# notes, not the coarse interests vocabulary a temple/library/ashram all
# share equally.
TEXT_BOOST_PER_MATCH = 0.12


def load_cache():
    data = np.load(os.path.join(CACHE_DIR, "catalog_embeddings.npz"))
    import json

    with open(os.path.join(CACHE_DIR, "catalog_items.json")) as f:
        items = json.load(f)
    return data["vectors"], items


def embed_query(client, query: str) -> np.ndarray:
    resp = client.models.embed_content(model=EMBEDDING_MODEL, contents=query)
    return np.array(resp.embeddings[0].values)


def cosine_similarities(vecs: np.ndarray, qvec: np.ndarray) -> np.ndarray:
    return vecs @ qvec / (np.linalg.norm(vecs, axis=1) * np.linalg.norm(qvec))


def tag_overlap_counts(items: list[dict], query: str) -> np.ndarray:
    """Exact-match count against the curated `interests` tag list."""
    words = {w for w in query.lower().split() if w}
    counts = []
    for item in items:
        tags = {t.lower() for t in (item.get("interests") or [])}
        counts.append(len(words & tags))
    return np.array(counts, dtype=float)


def text_overlap_counts(items: list[dict], query: str) -> np.ndarray:
    """Substring-match count against name/category/notes free text -- same
    approach as guide.py's _find_place, extended to the notes field, which
    _find_place itself doesn't search."""
    words = [w for w in query.lower().split() if w]
    counts = []
    for item in items:
        haystack = f"{item.get('name','')} {item.get('category','')} {item.get('notes','')}".lower()
        counts.append(sum(1 for w in words if w in haystack))
    return np.array(counts, dtype=float)


MODES = ("semantic", "tag_hybrid", "text_hybrid")


def rank(vecs, items, qvec, query: str, k: int, mode: str):
    assert mode in MODES
    sims = cosine_similarities(vecs, qvec)
    scores = sims.copy()
    if mode == "tag_hybrid":
        scores = scores + TAG_BOOST_PER_MATCH * tag_overlap_counts(items, query)
    elif mode == "text_hybrid":
        scores = scores + TEXT_BOOST_PER_MATCH * text_overlap_counts(items, query)
    top = np.argsort(-scores)[:k]
    return [(items[i]["id"], items[i]["name"], float(sims[i]), float(scores[i])) for i in top]
