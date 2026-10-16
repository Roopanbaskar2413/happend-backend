"""One-time (re-run only when the catalog changes) job: embed every
*verified* catalog entry via Gemini's embedding API and cache the result.

Unverified entries (131 of 268 places -- auto-merged scrape output with
generic "Auto-added from scraped data" notes, never reviewed) are excluded
entirely: they have no real descriptive text to embed, and including them
surfaces garbage into semantic search results (confirmed empirically --
see the load-test notes in the parent conversation).

Usage:
    cd backend && .venv/bin/python -m tests.evals.rag.build_embeddings

Writes catalog_embeddings.npz (vectors) and catalog_items.json (id/name/
interests, aligned by row) into this directory. Both are gitignored --
this is an experimental cache, not something to commit, and it's cheap
to regenerate (~3 minutes against the real API, see below).
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
from google.genai import Client, errors

CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
EMBEDDING_MODEL = "gemini-embedding-001"
BATCH_SIZE = 50  # the API's hard cap is 100; smaller batches + spacing
                 # avoid the free-tier rate limit hit when batches of 100
                 # were fired back-to-back with no delay (confirmed empirically).
BATCH_DELAY_SECONDS = 6


def _text_repr(item: dict, name_field: str, category_field: str, tags_field: str) -> str:
    tags = item.get(tags_field) or []
    return f"{item[name_field]}. {item.get(category_field, '')}. {', '.join(tags)}. {item.get('notes', '')}"


def load_verified_catalog_texts(city_data_path: str):
    with open(city_data_path) as f:
        data = json.load(f)

    entries = []
    for p in data["places"]:
        if not p.get("verified", True):
            continue
        entries.append(
            {
                "id": p["id"],
                "name": p["name"],
                "kind": "place",
                "interests": p.get("interests") or [],
                "text": _text_repr(p, "name", "category", "interests"),
            }
        )
    for f in data["food"]:
        if not f.get("verified", True):
            continue
        entries.append(
            {
                "id": f["id"],
                "name": f["name"],
                "kind": "food",
                "interests": f.get("meals") or [],  # closest analog to a tag list for food
                "text": _text_repr(f, "name", "price_band", "meals"),
            }
        )
    return entries


def embed_texts(client: Client, texts: list[str]) -> list[list[float]]:
    batches = [texts[i : i + BATCH_SIZE] for i in range(0, len(texts), BATCH_SIZE)]
    vectors: list[list[float]] = []
    for i, batch in enumerate(batches):
        for attempt in range(4):
            try:
                resp = client.models.embed_content(model=EMBEDDING_MODEL, contents=batch)
                vectors.extend([e.values for e in resp.embeddings])
                break
            except errors.ClientError as exc:
                if attempt == 3:
                    raise
                print(f"  batch {i} attempt {attempt} failed ({exc}), retrying...")
                time.sleep(20)
        if i < len(batches) - 1:
            time.sleep(BATCH_DELAY_SECONDS)
    return vectors


def main():
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(CACHE_DIR)))
    catalog_path = os.path.join(backend_dir, "app", "data", "pondicherry", "places.json")

    entries = load_verified_catalog_texts(catalog_path)
    print(f"{len(entries)} verified entries to embed")

    client = Client(api_key=os.environ["GEMINI_API_KEY"])
    t0 = time.time()
    vectors = embed_texts(client, [e["text"] for e in entries])
    print(f"embedded {len(vectors)} items in {time.time() - t0:.1f}s")

    vecs = np.array(vectors)
    np.savez(os.path.join(CACHE_DIR, "catalog_embeddings.npz"), vectors=vecs)
    with open(os.path.join(CACHE_DIR, "catalog_items.json"), "w") as f:
        json.dump(
            [{"id": e["id"], "name": e["name"], "kind": e["kind"], "interests": e["interests"]} for e in entries],
            f,
        )
    print(f"saved {vecs.shape} ({vecs.nbytes / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
