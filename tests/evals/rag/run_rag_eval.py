"""Compares semantic-only vs. hybrid (semantic + interests-tag boost)
retrieval against the hand-labeled cases in cases.py, and reports the
delta -- not just whether hybrid "looks better", but whether it actually
fixes the known miss without regressing the known hit.

Requires catalog_embeddings.npz / catalog_items.json to already exist --
run build_embeddings.py first. Requires GEMINI_API_KEY (for query-time
embedding only; the catalog side is cached).

Usage:
    cd backend && .venv/bin/python -m tests.evals.rag.run_rag_eval
"""
from __future__ import annotations

import os
import time

from google.genai import Client

from tests.evals.rag.cases import CASES, TOP_K
from tests.evals.rag.scorer import MODES, embed_query, load_cache, rank


def run():
    client = Client(api_key=os.environ["GEMINI_API_KEY"])
    vecs, items = load_cache()

    results = {mode: [] for mode in MODES}
    for case in CASES:
        qvec = embed_query(client, case["query"])
        time.sleep(2)  # stay well clear of the free-tier rate limit
        for mode in MODES:
            top = rank(vecs, items, qvec, case["query"], TOP_K, mode=mode)
            hit = any(item_id == case["expect_id"] for item_id, *_ in top)
            results[mode].append(hit)
            print(f"\n[{mode}] {case['id']}: {'HIT' if hit else 'MISS'} (expected {case['expect_id']})")
            for item_id, name, sim, score in top:
                marker = " <-- expected" if item_id == case["expect_id"] else ""
                print(f"    {score:.3f} (sim={sim:.3f})  {name}{marker}")

    print("\n=== summary ===")
    for mode in MODES:
        print(f"{mode}: {sum(results[mode])}/{len(CASES)}")
    for case, sem, tagh, texth in zip(CASES, results["semantic"], results["tag_hybrid"], results["text_hybrid"]):
        if not (sem == tagh == texth):
            print(f"  {case['id']}: semantic={sem} tag_hybrid={tagh} text_hybrid={texth}")


if __name__ == "__main__":
    run()
