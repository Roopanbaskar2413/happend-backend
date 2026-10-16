"""Deliberately-run eval harness for the AI Guide -- NOT part of pytest/CI.

This calls the real Gemini API (real quota, real cost) against a small set
of realistic trip-planning queries, and checks the response against a
checklist grounded in the actual Pondicherry catalog: never invents a
place, doesn't quietly recommend something that's closed, respects an
explicit "don't", and never hands back an itinerary-mutation tool_call
with an id that doesn't actually exist.

Usage:
    cd backend && .venv/bin/python -m tests.evals.run_guide_eval

Requires GEMINI_API_KEY to be set (same env var the app itself uses).
"""
from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.engine.catalog import load_catalog  # noqa: E402
from app.main import app  # noqa: E402
from tests.evals.cases import CASES, CITY, build_contents  # noqa: E402

MUSEUM_ID = "pondicherry_museum"
SEAFOOD_KEYWORDS = ("seafood", "fish", "prawn", "crab", "lobster")


def _load_known_ids():
    catalog = load_catalog(CITY)
    by_id = {}
    for p in catalog.places:
        by_id[p.id] = p
    for f in catalog.food:
        by_id[f.id] = f
    return by_id


def _function_calls_in(contents: list[dict]) -> list[dict]:
    """Every function_call part across the whole turn history, in order."""
    calls = []
    for turn in contents:
        for part in turn.get("parts", []):
            fc = part.get("functionCall") or part.get("function_call")
            if fc:
                calls.append(fc)
    return calls


def check_no_hallucinated_places(case, body, known_ids):
    bad = [sp["id"] for sp in (body.get("suggested_places") or []) if sp["id"] not in known_ids]
    if bad:
        return False, f"suggested_places includes unknown id(s): {bad}"
    return True, ""


def check_used_find_open_after_not_find_place(case, body, known_ids):
    names = {c.get("name") for c in _function_calls_in(body["contents"])}
    if "find_open_after" in names:
        return True, ""
    if "find_place" in names:
        return False, "called find_place for a 'what's open at a time' question instead of find_open_after"
    return False, "called neither tool at all"


def check_no_tool_call_for_nonexistent_category(case, body, known_ids):
    # A bowling alley genuinely isn't in the catalog. find_place returning
    # zero matches is fine; what's NOT fine is a suggested_place/tool_call
    # pointing at something that isn't actually a bowling alley.
    for sp in body.get("suggested_places") or []:
        item = known_ids.get(sp["id"])
        if item and "bowl" in json.dumps(item.model_dump()).lower():
            return False, f"claims {sp['id']} is a bowling alley -- check manually, likely wrong"
    return True, ""


def check_mentions_closure_if_suggesting_museum_on_monday(case, body, known_ids):
    suggested_ids = {sp["id"] for sp in (body.get("suggested_places") or [])}
    tool_call = body.get("tool_call")
    museum_added = tool_call and tool_call.get("args", {}).get("place_id") == MUSEUM_ID
    if MUSEUM_ID in suggested_ids or museum_added:
        reply = (body.get("reply") or "").lower()
        if "clos" in reply or "monday" in reply:
            return True, ""
        return False, "suggested/added the museum on Monday without mentioning it's closed that day"
    return True, "(museum wasn't suggested at all -- trivially fine)"


def check_suggested_food_not_seafood_named(case, body, known_ids):
    for sp in body.get("suggested_places") or []:
        item = known_ids.get(sp["id"])
        if not item:
            continue
        blob = json.dumps(item.model_dump()).lower()
        if any(kw in blob for kw in SEAFOOD_KEYWORDS):
            return False, f"suggested {sp['id']} despite a no-seafood request"
    return True, ""


def check_suggested_food_within_budget(case, body, known_ids):
    for sp in body.get("suggested_places") or []:
        if sp["kind"] == "food" and sp.get("cost_pp", 0) > 200:
            return False, f"suggested {sp['id']} at cost_pp={sp['cost_pp']} for a <=200 budget ask"
    return True, ""


def check_add_place_id_is_real_catalog_id(case, body, known_ids):
    tool_call = body.get("tool_call")
    if not tool_call or tool_call["name"] != "add_place":
        return False, "expected an add_place tool_call, got none"
    place_id = tool_call["args"].get("place_id")
    if place_id not in known_ids:
        return False, f"add_place used a non-existent place_id: {place_id!r}"
    return True, ""


def check_remove_place_id_matches_existing_item(case, body, known_ids):
    tool_call = body.get("tool_call")
    if not tool_call or tool_call["name"] != "remove_place":
        return False, "expected a remove_place tool_call, got none"
    existing_ids = {item["id"] for item in case["day"]["items"]}
    item_id = tool_call["args"].get("item_id")
    if item_id not in existing_ids:
        return False, f"remove_place used item_id {item_id!r}, not in the itinerary context {existing_ids}"
    return True, ""


CHECKS = {name: fn for name, fn in list(globals().items()) if name.startswith("check_")}


def run():
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set -- this eval calls the real API and needs it.")
        sys.exit(1)

    known_ids = _load_known_ids()
    client = TestClient(app)

    total = 0
    passed = 0
    for case in CASES:
        payload = {
            "city": CITY,
            "day": case["day"],
            "contents": build_contents(case["query"]),
        }
        res = client.post("/api/guide/chat", json=payload)
        print(f"\n=== {case['id']} ===")
        print(f"query: {case['query']!r}")
        if res.status_code != 200:
            print(f"  FAIL  http {res.status_code}: {res.text[:200]}")
            total += len(case["checks"])
            continue
        body = res.json()
        print(f"  reply: {(body.get('reply') or '')[:160]!r}")
        for check_name in case["checks"]:
            total += 1
            fn = CHECKS[f"check_{check_name}"]
            ok, detail = fn(case, body, known_ids)
            passed += int(ok)
            mark = "PASS" if ok else "FAIL"
            print(f"  {mark}  {check_name}{': ' + detail if detail else ''}")

    print(f"\n{passed}/{total} checks passed")


if __name__ == "__main__":
    run()
