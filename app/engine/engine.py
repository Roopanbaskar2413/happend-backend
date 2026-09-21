"""Deterministic greedy trip-planning engine.

Pure module: no FastAPI, no database, no network I/O. `generate()` takes a
validated `PlanRequest` and a `Catalog` and returns an `Itinerary`.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from datetime import date, timedelta

from app.engine.models import Catalog, DayPlan, Food, Itinerary, ItineraryItem, Place, PlanRequest
from app.engine.scoring import DAILY_BUDGET, score_place

PACE_WINDOWS = {
    "relaxed": (510, 1320),  # 08:30-22:00
    "balanced": (450, 1350),  # 07:30-22:30
    "packed": (390, 1380),  # 06:30-23:00
}

# (window_start, window_end) in minutes-since-midnight.
MEAL_WINDOWS = {
    "breakfast": (420, 630),  # 07:00-10:30
    "lunch": (750, 900),  # 12:30-15:00
    "dinner": (1140, 1320),  # 19:00-22:00
}

PRICE_BAND_ALLOWED = {
    "low": {"low"},
    "mid": {"low", "mid"},
    "high": {"low", "mid", "high"},
}

MAX_WAIT_MINUTES = 45
MAX_ITERATIONS_PER_DAY = 80

# Categories that leave a traveler physically spent — after one of these, try
# to slot in a snack/refreshment stop before considering another.
EXERTION_CATEGORIES = {"turf", "activity"}


@dataclass
class _Point:
    """Just enough shape to be used as a travel-distance endpoint."""

    name: str
    area: str
    lat: float
    lng: float


TOWN_CENTER = _Point(name="White Town", area="white_town", lat=11.9337, lng=79.8330)


def _resolve_base_location(request: PlanRequest, catalog: Catalog):
    """Where each day's travel starts/ends from. Prefer the traveler's actual
    chosen stay so travel times reflect reality; fall back to a generic town
    center when they're arranging their own place or haven't picked one."""
    if request.stay_id:
        for stay in catalog.stays:
            if stay.id == request.stay_id:
                return _Point(name=stay.name, area=stay.area, lat=stay.lat, lng=stay.lng)
    return TOWN_CENTER


def time_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(minutes: int) -> str:
    minutes = max(0, minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_window(window: str) -> tuple[int, int]:
    start, end = window.split("-")
    return time_to_minutes(start), time_to_minutes(end)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(from_pt, to_pt, has_own_vehicle: bool) -> int:
    dist_km = haversine_km(from_pt.lat, from_pt.lng, to_pt.lat, to_pt.lng) * 1.3
    if from_pt.area == to_pt.area and dist_km <= 1.2:
        minutes = dist_km / 4.5 * 60 + 3
    else:
        minutes = dist_km / 22 * 60 + 8
        if not has_own_vehicle and dist_km > 3:
            minutes += 6
    return round(minutes)


def earliest_start(entity, arrive_minutes: int, weekday: int, day_end: int) -> int | None:
    if weekday in entity.closed_days:
        return None
    best: int | None = None
    for window in entity.windows:
        w_start, w_end = parse_window(window)
        candidate_start = max(arrive_minutes, w_start)
        wait = candidate_start - arrive_minutes
        if wait > MAX_WAIT_MINUTES:
            continue
        candidate_end = candidate_start + entity.duration_min
        if candidate_end > w_end or candidate_end > day_end:
            continue
        if best is None or candidate_start < best:
            best = candidate_start
    return best


def price_band_ok(budget_level: str, price_band: str) -> bool:
    return price_band in PRICE_BAND_ALLOWED[budget_level]


def map_url_for(entity) -> str:
    return f"https://www.google.com/maps?q={entity.lat},{entity.lng}"


def compute_day_bounds(
    pace: str, day_index: int, num_days: int, arrival_minutes: int, departure_minutes: int
) -> tuple[int, int]:
    start, end = PACE_WINDOWS[pace]
    if day_index == 0:
        start = arrival_minutes
    if day_index == num_days - 1:
        end = min(end, departure_minutes - 45)
    return start, end


def _make_travel_item(id_: str, to_pt, minutes: int, start_time: int) -> ItineraryItem:
    return ItineraryItem(
        id=id_,
        start=minutes_to_time(start_time),
        end=minutes_to_time(start_time + minutes),
        kind="travel",
        ref_id=None,
        title=f"Travel to {to_pt.name}",
        area=None,
        cost_pp=0,
    )


def _score_food(food: Food, travel_min: int) -> float:
    return food.rating - travel_min / 12


def fill_window(
    catalog: Catalog,
    request: PlanRequest,
    weekday: int,
    current: int,
    day_end: int,
    used_place_ids: set[str],
    used_food_today: set[str],
    meals_done: dict[str, bool],
    remaining_budget: int,
    daily_budget_total: int,
    id_counter: itertools.count,
    last_location,
    last_category: str | None,
) -> tuple[list[ItineraryItem], object, str | None, int, int]:
    """Greedily fills [current, day_end) with places/meals, mutating
    `used_place_ids`/`used_food_today` in place as it goes.

    Extracted out of the whole-day generator so re-flow can reuse the exact
    same greedy scheduling logic on the partial-day segments left open after
    a disruption (freeze past / keep locks / re-fill the rest).
    """
    items: list[ItineraryItem] = []
    snack_pending = last_category in EXERTION_CATEGORIES

    def next_id() -> str:
        return f"itm_{next(id_counter)}"

    stall_guard = 0
    while current < day_end and stall_guard < MAX_ITERATIONS_PER_DAY:
        stall_guard += 1

        due_meal = None
        for meal in ("breakfast", "lunch", "dinner"):
            w_start, _ = MEAL_WINDOWS[meal]
            if not meals_done[meal] and current >= w_start:
                due_meal = meal
                break

        if due_meal is not None:
            _, w_end = MEAL_WINDOWS[due_meal]
            candidates = []
            for food in catalog.food:
                if food.id in used_food_today:
                    continue
                if due_meal not in food.meals:
                    continue
                if not price_band_ok(request.budget_level, food.price_band):
                    continue
                if request.diet in ("veg", "vegan") and not food.veg_friendly:
                    continue
                if food.cost_pp > remaining_budget:
                    continue
                travel = travel_minutes(last_location, food, request.has_own_vehicle)
                start = earliest_start(food, current + travel, weekday, day_end)
                if start is None:
                    continue
                candidates.append((_score_food(food, travel), food, start, travel))

            meals_done[due_meal] = True  # one attempt per meal per day, success or not

            if candidates:
                candidates.sort(key=lambda c: -c[0])
                _, food, start, travel = candidates[0]
                if travel >= 1:
                    items.append(_make_travel_item(next_id(), food, travel, current))
                warning = f"Fitted in after the usual {due_meal} window." if start > w_end else None
                end = start + food.duration_min
                items.append(
                    ItineraryItem(
                        id=next_id(),
                        start=minutes_to_time(start),
                        end=minutes_to_time(end),
                        kind="meal",
                        ref_id=food.id,
                        title=food.name,
                        area=food.area,
                        cost_pp=food.cost_pp,
                        notes=food.notes,
                        booking_url=food.booking_url,
                        map_url=map_url_for(food),
                        warning=warning,
                    )
                )
                used_food_today.add(food.id)
                remaining_budget -= food.cost_pp
                last_location = food
                current = end
                continue

        if snack_pending:
            snack_pending = False  # one attempt right after the exertion stop, success or not
            snack_candidates = []
            for food in catalog.food:
                if food.id in used_food_today:
                    continue
                if "snack" not in food.meals:
                    continue
                if not price_band_ok(request.budget_level, food.price_band):
                    continue
                if request.diet in ("veg", "vegan") and not food.veg_friendly:
                    continue
                if food.cost_pp > remaining_budget:
                    continue
                travel = travel_minutes(last_location, food, request.has_own_vehicle)
                start = earliest_start(food, current + travel, weekday, day_end)
                if start is None:
                    continue
                snack_candidates.append((_score_food(food, travel), food, start, travel))

            if snack_candidates:
                snack_candidates.sort(key=lambda c: -c[0])
                _, food, start, travel = snack_candidates[0]
                if travel >= 1:
                    items.append(_make_travel_item(next_id(), food, travel, current))
                end = start + food.duration_min
                items.append(
                    ItineraryItem(
                        id=next_id(),
                        start=minutes_to_time(start),
                        end=minutes_to_time(end),
                        kind="meal",
                        ref_id=food.id,
                        title=food.name,
                        area=food.area,
                        cost_pp=food.cost_pp,
                        notes=food.notes,
                        booking_url=food.booking_url,
                        map_url=map_url_for(food),
                    )
                )
                used_food_today.add(food.id)
                remaining_budget -= food.cost_pp
                last_location = food
                current = end
                continue

        best = None
        for place in catalog.places:
            if place.id in used_place_ids:
                continue
            if place.cost_pp > remaining_budget:
                continue
            travel = travel_minutes(last_location, place, request.has_own_vehicle)
            start = earliest_start(place, current + travel, weekday, day_end)
            if start is None:
                continue
            sc = score_place(
                place,
                request,
                travel_minutes=travel,
                start_minutes=start,
                previous_category=last_category,
                daily_budget_total=daily_budget_total,
            )
            if best is None or sc > best[0]:
                best = (sc, place, start, travel)

        if best is None:
            break

        _, place, start, travel = best
        if travel >= 1:
            items.append(_make_travel_item(next_id(), place, travel, current))
        end = start + place.duration_min
        items.append(
            ItineraryItem(
                id=next_id(),
                start=minutes_to_time(start),
                end=minutes_to_time(end),
                kind="place",
                ref_id=place.id,
                title=place.name,
                area=place.area,
                cost_pp=place.cost_pp,
                notes=place.notes,
                booking_url=place.booking_url,
                map_url=map_url_for(place),
            )
        )
        used_place_ids.add(place.id)
        remaining_budget -= place.cost_pp
        last_location = place
        last_category = place.category
        snack_pending = place.category in EXERTION_CATEGORIES
        current = end

    return items, last_location, last_category, remaining_budget, current


def _generate_day(
    catalog: Catalog,
    request: PlanRequest,
    current_date: date,
    weekday: int,
    day_start: int,
    day_end: int,
    used_place_ids: set[str],
    id_counter: itertools.count,
    last_location,
    last_category: str | None,
) -> tuple[DayPlan, object, str | None]:
    remaining_budget = DAILY_BUDGET[request.budget_level]
    daily_budget_total = remaining_budget
    used_food_today: set[str] = set()

    meals_done: dict[str, bool] = {}
    for meal, (w_start, w_end) in MEAL_WINDOWS.items():
        meals_done[meal] = day_end <= w_start or day_start > w_end

    items, last_location, last_category, _remaining_budget, _current = fill_window(
        catalog,
        request,
        weekday,
        day_start,
        day_end,
        used_place_ids,
        used_food_today,
        meals_done,
        remaining_budget,
        daily_budget_total,
        id_counter,
        last_location,
        last_category,
    )

    day_plan = DayPlan(date=current_date.isoformat(), weekday=weekday, items=items)
    return day_plan, last_location, last_category


def generate(request: PlanRequest, catalog: Catalog) -> Itinerary:
    arrival_date = date.fromisoformat(request.arrival_date)
    departure_date = date.fromisoformat(request.departure_date)
    num_days = (departure_date - arrival_date).days + 1
    if num_days < 1:
        raise ValueError("departure_date must not be before arrival_date")

    arrival_minutes = time_to_minutes(request.arrival_time)
    departure_minutes = time_to_minutes(request.departure_time)

    used_place_ids: set[str] = set()
    id_counter = itertools.count(1)
    last_location: object = _resolve_base_location(request, catalog)
    last_category: str | None = None
    days: list[DayPlan] = []

    for day_index in range(num_days):
        current_date = arrival_date + timedelta(days=day_index)
        weekday = current_date.weekday()
        day_start, day_end = compute_day_bounds(
            request.pace, day_index, num_days, arrival_minutes, departure_minutes
        )
        if day_end <= day_start:
            days.append(DayPlan(date=current_date.isoformat(), weekday=weekday, items=[]))
            continue

        day_plan, last_location, last_category = _generate_day(
            catalog,
            request,
            current_date,
            weekday,
            day_start,
            day_end,
            used_place_ids,
            id_counter,
            last_location,
            last_category,
        )
        days.append(day_plan)

    return Itinerary(days=days)
