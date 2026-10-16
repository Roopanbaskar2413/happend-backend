"""Builds the semantic-search embedding cache for a city's catalog.
One-time job, re-run only when the catalog's verified entries change --
not something that runs on every deploy or every request.

Excludes unverified entries (auto-merged scrape output with no real
descriptive text) entirely -- see app/engine/semantic_search.py's
docstring for why.

Usage:
    cd backend && .venv/bin/python scripts/build_catalog_embeddings.py pondicherry
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
from google.genai import Client, errors

BATCH_SIZE = 50  # API's hard cap is 100; smaller batches + spacing avoid
                  # the free-tier rate limit hit when 100-item batches were
                  # fired back-to-back with no delay (confirmed empirically).
BATCH_DELAY_SECONDS = 6
EMBEDDING_MODEL = "gemini-embedding-001"


def _text_repr(item: dict, name_field: str, category_field: str, tags_field: str) -> str:
    tags = item.get(tags_field) or []
    return f"{item[name_field]}. {item.get(category_field, '')}. {', '.join(tags)}. {item.get('notes', '')}"


def load_verified_entries(city_dir: str) -> list[dict]:
    with open(os.path.join(city_dir, "places.json")) as f:
        data = json.load(f)

    entries = []
    for p in data["places"]:
        if not p.get("verified", True):
            continue
        entries.append(
            {
                "id": p["id"],
                "name": p["name"],
                "category": p.get("category", ""),
                "notes": p.get("notes", ""),
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
                "category": f.get("price_band", ""),
                "notes": f.get("notes", ""),
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
    if len(sys.argv) != 2:
        print("usage: python scripts/build_catalog_embeddings.py <city_id>")
        sys.exit(1)
    city_id = sys.argv[1]

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    city_dir = os.path.join(backend_dir, "app", "data", city_id)
    if not os.path.isdir(city_dir):
        print(f"no catalog directory for {city_id!r} at {city_dir}")
        sys.exit(1)

    entries = load_verified_entries(city_dir)
    print(f"{len(entries)} verified entries to embed for {city_id}")

    client = Client(api_key=os.environ["GEMINI_API_KEY"])
    t0 = time.time()
    vectors = embed_texts(client, [e["text"] for e in entries])
    print(f"embedded {len(vectors)} items in {time.time() - t0:.1f}s")

    vecs = np.array(vectors)
    np.savez(os.path.join(city_dir, "embeddings.npz"), vectors=vecs)
    with open(os.path.join(city_dir, "embeddings_meta.json"), "w") as f:
        json.dump(
            [{"id": e["id"], "name": e["name"], "category": e["category"], "notes": e["notes"]} for e in entries],
            f,
        )
    print(f"saved {vecs.shape} ({vecs.nbytes / 1024 / 1024:.1f} MB) to {city_dir}")


if __name__ == "__main__":
    main()
