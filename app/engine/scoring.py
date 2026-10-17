"""Place/food scoring. Pure functions only — no I/O, no FastAPI, no DB."""
from __future__ import annotations

from app.engine.models import Place, PlanRequest, Slot

SLOT_BOUNDS: list[tuple[int, int, Slot]] = [
    (300, 420, "sunrise"),  # 05:00-07:00
    (420, 720, "morning"),  # 07:00-12:00
    (720, 1020, "afternoon"),  # 12:00-17:00
    (1020, 1200, "evening"),  # 17:00-20:00
]


def slot_of(minutes: int) -> Slot:
    m = minutes % 1440
    for start, end, slot in SLOT_BOUNDS:
        if start <= m < end:
            return slot
    return "night"  # 20:00-24:00 and 00:00-05:00


DAILY_BUDGET = {"low": 900, "mid": 2200, "high": 6000}

# Categories that leave a traveler physically spent — stacking two of these
# back-to-back (even under different category labels, e.g. turf then
# activity) is discouraged just like repeating the same category.
EXERTION_CATEGORIES = {"turf", "activity"}

# "Similarity to the removed POI" signal, used only during closed/skip
# reflow (see reflow.py) -- None for a fresh generation or any other
# disruption type, which contributes exactly 0 and leaves this function's
# output identical to before this signal existed.
#
# Unrelated catalog pairs still cluster around cosine similarity 0.5-0.6
# (confirmed empirically, not assumed), not near 0 -- so the raw value is
# centered on that floor before weighting, otherwise every candidate would
# get a uniform positive bonus regardless of actual relevance.
#
# Weight is provisional -- being validated against a hand-labeled eval
# (scripts/eval_similarity_weight.py) before being trusted, same discipline
# used for TEXT_BOOST_PER_MATCH in semantic_search.py. Don't treat this
# value as settled without checking that eval's latest result.
SIMILARITY_BASELINE = 0.6
SIMILARITY_WEIGHT = 3.0


def score_place(
    place: Place,
    request: PlanRequest,
    *,
    travel_minutes: float,
    start_minutes: int,
    previous_category: str | None,
    daily_budget_total: int,
    similarity_to_removed: float | None = None,
) -> float:
    score = place.rating

    interest_overlap = len(set(place.interests) & set(request.interests))
    score += 2.0 * min(interest_overlap, 3)
    if interest_overlap == 0:
        score -= 2.5

    if request.group_type in place.group_fit:
        score += 1.5
    else:
        score -= 2.0

    if slot_of(start_minutes) in place.best_slots:
        score += 1.8
    else:
        score -= 0.8

    score -= travel_minutes / 12

    if daily_budget_total > 0:
        score -= (place.cost_pp / daily_budget_total) * 2.0

    if previous_category is not None and previous_category == place.category:
        score -= 1.5
    elif previous_category in EXERTION_CATEGORIES and place.category in EXERTION_CATEGORIES:
        score -= 1.5

    if place.duration_min > 180:
        score -= 1.0

    if similarity_to_removed is not None:
        score += SIMILARITY_WEIGHT * (similarity_to_removed - SIMILARITY_BASELINE)

    return score
