import pytest

from app.engine.catalog import load_catalog
from app.engine.engine import generate, time_to_minutes
from app.engine.models import PlanRequest
from app.engine.reflow import Disruption, DisruptionError, replan

DISRUPTION_KINDS = ["running_late", "weather", "closed", "skip", "extend", "add", "energy"]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog("pondicherry")


def make_request(**overrides):
    base = dict(
        city="pondicherry",
        origin_city="Chennai",
        transport_mode="bus",
        arrival_date="2026-09-21",
        arrival_time="07:00",
        departure_date="2026-09-23",
        departure_time="20:00",
        group_type="couple",
        party_size=2,
        budget_level="mid",
        pace="packed",
        diet="any",
        interests=["beach", "heritage", "culture", "nature", "walk", "photography", "food"],
        has_own_vehicle=True,
        has_own_stay=True,
        stay_id=None,
    )
    base.update(overrides)
    return PlanRequest(**base)


def real_items(day):
    return [i for i in day.items if i.kind not in ("travel", "transfer")]


def assert_no_overlaps(day):
    prev_end = None
    for item in day.items:
        start = time_to_minutes(item.start)
        end = time_to_minutes(item.end)
        assert end >= start
        if prev_end is not None:
            assert start >= prev_end
        prev_end = end


def baseline(catalog):
    """A generated itinerary with day 0's last stop locked — a safe anchor
    position for every disruption type, since none of them target the tail
    end of the day, so extend/add/closed/skip all have room to reshuffle
    around it without colliding."""
    req = make_request()
    itinerary = generate(req, catalog)
    day0 = itinerary.days[0]
    items = real_items(day0)
    assert len(items) >= 3, "test fixture assumes a busy day 0"
    items[-1].locked = True
    return req, itinerary


def make_disruption(kind, itinerary, catalog):
    day0 = itinerary.days[0]
    items = real_items(day0)
    target = items[0].id
    if kind == "running_late":
        return Disruption(type="running_late", day_index=0, now_time=items[0].end)
    if kind == "weather":
        return Disruption(type="weather", day_index=0, weather="rain")
    if kind == "closed":
        return Disruption(type="closed", day_index=0, item_id=target)
    if kind == "skip":
        return Disruption(type="skip", day_index=0, item_id=target)
    if kind == "extend":
        return Disruption(type="extend", day_index=0, item_id=target, extend_minutes=20)
    if kind == "add":
        used = {i.ref_id for d in itinerary.days for i in d.items if i.kind == "place"}
        free_place_id = next(p.id for p in catalog.places if p.id not in used)
        return Disruption(type="add", day_index=0, place_id=free_place_id)
    if kind == "energy":
        return Disruption(type="energy", day_index=0)
    raise ValueError(kind)


# --- Test 8: locked items survive every disruption type -----------------


@pytest.mark.parametrize("kind", DISRUPTION_KINDS)
def test_locked_item_survives_every_disruption(catalog, kind):
    req, itinerary = baseline(catalog)
    locked_item = next(i for i in real_items(itinerary.days[0]) if i.locked)

    disruption = make_disruption(kind, itinerary, catalog)
    result = replan(itinerary, disruption, catalog, req)

    match = next((i for i in result.itinerary.days[0].items if i.id == locked_item.id), None)
    assert match is not None, f"{kind} dropped a locked item"
    assert match.start == locked_item.start
    assert match.end == locked_item.end
    assert match.title == locked_item.title


# --- Regression: closed/skipped places must not be immediately re-picked -


@pytest.mark.parametrize("kind", ["closed", "skip"])
def test_closed_or_skipped_place_is_not_reselected(catalog, kind):
    req, itinerary = baseline(catalog)
    target = real_items(itinerary.days[0])[0]

    disruption = make_disruption(kind, itinerary, catalog)
    result = replan(itinerary, disruption, catalog, req)

    ref_ids = {i.ref_id for i in result.itinerary.days[0].items if i.ref_id}
    assert target.ref_id not in ref_ids


# --- Test 9: running-late produces a valid, shorter plan -----------------


def test_running_late_produces_valid_shorter_plan(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    items = real_items(itinerary.days[0])
    assert len(items) >= 3
    now_time = items[len(items) // 2].start

    disruption = Disruption(type="running_late", day_index=0, now_time=now_time)
    result = replan(itinerary, disruption, catalog, req)
    new_day0 = result.itinerary.days[0]

    assert_no_overlaps(new_day0)

    cutoff = time_to_minutes(now_time)
    old_head_ids = [i.id for i in items if time_to_minutes(i.start) < cutoff]
    new_head_ids = [i.id for i in real_items(new_day0) if time_to_minutes(i.start) < cutoff]
    assert old_head_ids == new_head_ids

    for item in new_day0.items:
        assert time_to_minutes(item.end) <= time_to_minutes(req.departure_time) + 1440


# --- Test 10: rain plan has zero weather-dependent items -----------------


def test_rain_plan_has_no_weather_dependent_items(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    place_by_id = {p.id: p for p in catalog.places}

    disruption = Disruption(type="weather", day_index=0, weather="rain")
    result = replan(itinerary, disruption, catalog, req)

    for item in result.itinerary.days[0].items:
        if item.kind == "place":
            assert not place_by_id[item.ref_id].weather_dependent


# --- Test 11: every re-flow emits at least one change with a reason ------


@pytest.mark.parametrize("kind", DISRUPTION_KINDS)
def test_every_reflow_emits_at_least_one_change_with_reason(catalog, kind):
    req, itinerary = baseline(catalog)
    disruption = make_disruption(kind, itinerary, catalog)
    result = replan(itinerary, disruption, catalog, req)

    assert len(result.changes) >= 1
    for change in result.changes:
        assert change.reason.strip() != ""


# --- Test 12: double re-flow is stable ------------------------------------


def _content(day):
    # Ids are an internal detail that legitimately keeps climbing across
    # successive replans — idempotency is about the actual schedule content.
    return [(i.kind, i.ref_id, i.title, i.start, i.end, i.status, i.locked, i.cost_pp) for i in day.items]


def test_double_reflow_is_stable(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    items = real_items(itinerary.days[0])
    now_time = items[len(items) // 2].start
    disruption = Disruption(type="running_late", day_index=0, now_time=now_time)

    result1 = replan(itinerary, disruption, catalog, req)
    result2 = replan(result1.itinerary, disruption, catalog, req)

    assert _content(result2.itinerary.days[0]) == _content(result1.itinerary.days[0])


# --- A few error-path sanity checks ---------------------------------------


def test_replan_rejects_out_of_range_day(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    disruption = Disruption(type="energy", day_index=99)
    with pytest.raises(DisruptionError):
        replan(itinerary, disruption, catalog, req)


def test_closed_requires_a_real_upcoming_item(catalog):
    req = make_request()
    itinerary = generate(req, catalog)
    disruption = Disruption(type="closed", day_index=0, item_id="does_not_exist")
    with pytest.raises(DisruptionError):
        replan(itinerary, disruption, catalog, req)
