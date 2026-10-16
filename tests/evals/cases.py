"""Eval cases for the AI Guide, grounded in the real Pondicherry catalog.

Each case is a realistic trip-planning message plus a checklist of
automated checks to run against the real response. These hit the actual
Gemini API (real quota/cost) -- see run_guide_eval.py for why this is a
deliberately-run script, not part of the normal pytest suite.
"""

CITY = "pondicherry"

# A single reusable "day" context: one real itinerary item already on the
# plan, so add/remove/reorder cases have something real to act on instead
# of an empty day.
EXISTING_ITEM = {
    "id": "item-1",
    "title": "Promenade (Rock) Beach",
    "kind": "place",
    "status": "planned",
    "start": "07:00",
    "end": "08:00",
}

EMPTY_DAY = {"weekday": 2, "items": []}  # Wednesday -- avoids the museum's Monday closure by default
DAY_WITH_ITEM = {"weekday": 2, "items": [EXISTING_ITEM]}
MONDAY_EMPTY_DAY = {"weekday": 0, "items": []}  # Monday -- pondicherry_museum is closed_days=[0]


def _user_turn(text: str) -> dict:
    return {"role": "user", "parts": [{"text": text}]}


CASES = [
    {
        "id": "simple_lookup",
        "query": "Where can I get a quiet coffee near the beach?",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places"],
    },
    {
        "id": "open_after_uses_right_tool",
        "query": "What's open after 9pm tonight?",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places", "used_find_open_after_not_find_place"],
    },
    {
        "id": "hallucination_resistance",
        "query": "Find me a bowling alley.",
        "day": EMPTY_DAY,
        # Pondicherry's catalog has no bowling alley (verified absent). The
        # guide must say so, not invent one. This is the sharpest check --
        # a hallucinated place would still pass "no_hallucinated_places" if
        # it hallucinated a name with no id, so this needs its own manual
        # read until the harness can judge prose, not just structured ids.
        "checks": ["no_hallucinated_places", "no_tool_call_for_nonexistent_category"],
    },
    {
        "id": "closed_day_awareness",
        "query": "Can I visit Pondicherry Museum today?",
        "day": MONDAY_EMPTY_DAY,
        # pondicherry_museum closed_days == [0] == Monday. The reply should
        # not casually recommend adding it today without flagging that.
        "checks": ["no_hallucinated_places", "mentions_closure_if_suggesting_museum_on_monday"],
    },
    {
        "id": "dont_constraint_no_seafood",
        "query": "Suggest a dinner place, but I don't eat seafood.",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places", "suggested_food_not_seafood_named"],
    },
    {
        "id": "budget_constraint",
        "query": "Find me somewhere cheap to eat, under 200 rupees a person.",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places", "suggested_food_within_budget"],
    },
    {
        "id": "add_place_valid_id",
        "query": "Add Promenade Beach to today's plan.",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places", "add_place_id_is_real_catalog_id"],
    },
    {
        "id": "remove_place_valid_id",
        "query": "Remove the beach from today's plan.",
        "day": DAY_WITH_ITEM,
        "checks": ["remove_place_id_matches_existing_item"],
    },
    {
        "id": "general_chat_no_tool_needed",
        "query": "What's Pondicherry known for?",
        "day": EMPTY_DAY,
        "checks": ["no_hallucinated_places"],
    },
]


def build_contents(query: str) -> list:
    return [_user_turn(query)]
