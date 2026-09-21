from datetime import date, timedelta

import pytest

from app.engine.catalog import load_catalog
from app.engine.engine import compute_day_bounds, earliest_start, generate, time_to_minutes
from app.engine.models import PlanRequest
from app.engine.scoring import DAILY_BUDGET


@pytest.fixture(scope="module")
def catalog():
    return load_catalog("pondicherry")


def make_request(**overrides):
    base = dict(
        city="pondicherry",
        origin_city="Chennai",
        transport_mode="bus",
        arrival_date="2026-09-21",
        arrival_time="09:00",
        departure_date="2026-09-23",
        departure_time="18:00",
        group_type="couple",
        party_size=2,
        budget_level="mid",
        pace="balanced",
        diet="any",
        interests=["beach", "heritage", "culture", "nature", "walk", "photography"],
        has_own_vehicle=True,
        has_own_stay=True,
        stay_id=None,
    )
    base.update(overrides)
    return PlanRequest(**base)


def num_days(itinerary):
    return len(itinerary.days)


def test_no_closed_venues(catalog):
    place_by_id = {p.id: p for p in catalog.places}
    food_by_id = {f.id: f for f in catalog.food}
    start = date(2026, 9, 21)
    for offset in range(7):
        d = start + timedelta(days=offset)
        req = make_request(
            arrival_date=d.isoformat(),
            arrival_time="06:30",
            departure_date=d.isoformat(),
            departure_time="23:30",
            pace="packed",
        )
        itinerary = generate(req, catalog)
        for day in itinerary.days:
            for item in day.items:
                if item.kind == "place":
                    place = place_by_id[item.ref_id]
                    assert day.weekday not in place.closed_days
                elif item.kind == "meal":
                    food = food_by_id[item.ref_id]
                    assert day.weekday not in food.closed_days


def test_no_overlaps(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    for day in itinerary.days:
        prev_end = None
        for item in day.items:
            start = time_to_minutes(item.start)
            end = time_to_minutes(item.end)
            assert end >= start
            if prev_end is not None:
                assert start >= prev_end
            prev_end = end


def test_day_end_respected(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    arrival_min = time_to_minutes(req.arrival_time)
    departure_min = time_to_minutes(req.departure_time)
    n = num_days(itinerary)
    for i, day in enumerate(itinerary.days):
        _, day_end = compute_day_bounds(req.pace, i, n, arrival_min, departure_min)
        for item in day.items:
            assert time_to_minutes(item.end) <= day_end


def test_departure_day_no_late_dinner(catalog):
    # Regression test for the documented failure mode: a tight departure day
    # (flight/bus soon after usual dinner time) must never get a dinner
    # scheduled past the day's real cutoff.
    req = make_request(
        arrival_date="2026-09-21",
        arrival_time="09:00",
        departure_date="2026-09-22",
        departure_time="18:00",
        pace="balanced",
    )
    itinerary = generate(req, catalog)
    n = num_days(itinerary)
    arrival_min = time_to_minutes(req.arrival_time)
    departure_min = time_to_minutes(req.departure_time)
    last_day = itinerary.days[-1]
    _, day_end = compute_day_bounds(req.pace, n - 1, n, arrival_min, departure_min)
    assert day_end < time_to_minutes("19:00")  # dinner window shouldn't even be reachable
    for item in last_day.items:
        assert time_to_minutes(item.end) <= day_end
        assert item.title != "dinner"  # no item should be a stray late dinner


def test_paradise_beach_ferry_cutoff_constraint(catalog):
    paradise = next(p for p in catalog.places if p.id == "paradise_beach")
    last_ferry_start = time_to_minutes("16:00") - paradise.duration_min
    for arrive in range(0, 1440, 15):
        start = earliest_start(paradise, arrive, weekday=2, day_end=1380)
        if start is not None:
            assert start <= last_ferry_start
            assert start + paradise.duration_min <= time_to_minutes("16:00")


def test_paradise_beach_never_missed_in_full_plan(catalog):
    req = make_request(
        arrival_date="2026-09-21",
        arrival_time="07:00",
        departure_date="2026-09-25",
        departure_time="20:00",
        pace="packed",
        interests=["beach", "boat", "swim", "family", "photography"],
    )
    itinerary = generate(req, catalog)
    for day in itinerary.days:
        for item in day.items:
            if item.ref_id == "paradise_beach":
                start = time_to_minutes(item.start)
                assert start + 210 <= time_to_minutes("16:00")


def test_no_zigzag(catalog):
    req = make_request(
        pace="packed",
        arrival_date="2026-09-21",
        arrival_time="06:30",
        departure_date="2026-09-24",
        departure_time="23:00",
    )
    itinerary = generate(req, catalog)
    n = num_days(itinerary)
    arrival_min = time_to_minutes(req.arrival_time)
    departure_min = time_to_minutes(req.departure_time)
    for i, day in enumerate(itinerary.days):
        day_start, day_end = compute_day_bounds(req.pace, i, n, arrival_min, departure_min)
        active_minutes = day_end - day_start
        if active_minutes <= 0:
            continue
        travel_total = sum(
            time_to_minutes(item.end) - time_to_minutes(item.start)
            for item in day.items
            if item.kind == "travel"
        )
        assert travel_total <= 0.25 * active_minutes


def test_budget_respected(catalog):
    for level in ("low", "mid", "high"):
        req = make_request(budget_level=level)
        itinerary = generate(req, catalog)
        for day in itinerary.days:
            total_cost = sum(item.cost_pp for item in day.items if item.kind in ("place", "meal"))
            assert total_cost <= DAILY_BUDGET[level]


def test_no_duplicate_places(catalog):
    req = make_request(
        arrival_date="2026-09-21",
        arrival_time="06:30",
        departure_date="2026-09-27",
        departure_time="22:00",
        pace="packed",
    )
    itinerary = generate(req, catalog)
    seen = set()
    for day in itinerary.days:
        for item in day.items:
            if item.kind == "place":
                assert item.ref_id not in seen
                seen.add(item.ref_id)


def test_no_back_to_back_exertion_without_a_refresh(catalog):
    """After a sports/turf stop, the next stop should be a meal/snack or a
    non-strenuous place — never straight into another sports/turf stop."""
    from app.engine.engine import EXERTION_CATEGORIES

    places_by_id = {p.id: p for p in catalog.places}

    req = make_request(
        arrival_date="2026-09-21",
        arrival_time="06:30",
        departure_date="2026-09-24",
        departure_time="22:00",
        pace="packed",
        interests=["activity", "beach", "nature"],
    )
    itinerary = generate(req, catalog)
    for day in itinerary.days:
        substantive = [item for item in day.items if item.kind in ("place", "meal")]
        for prev, nxt in zip(substantive, substantive[1:]):
            if prev.kind != "place" or nxt.kind != "place":
                continue
            prev_category = places_by_id[prev.ref_id].category
            next_category = places_by_id[nxt.ref_id].category
            assert not (prev_category in EXERTION_CATEGORIES and next_category in EXERTION_CATEGORIES), (
                f"{prev.title} ({prev_category}) is immediately followed by "
                f"{nxt.title} ({next_category}) with no refresh stop in between"
            )
