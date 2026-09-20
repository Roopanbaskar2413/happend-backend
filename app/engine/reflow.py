"""Live re-flow: given an existing itinerary and something that just went
wrong (or a spontaneous change of plan), rebuild the rest of the affected day.

Pure module: no FastAPI, no database. Algorithm, for every disruption type:
freeze everything already in the past, keep every locked item exactly where
it is, build an exclusion set (places/food already used today, plus anything
the disruption itself rules out), greedily re-fill the open time around those
fixed points using the same scoring engine as whole-day generation, then diff
the old day against the new one to produce a human-readable change log.
"""
from __future__ import annotations

import itertools
from typing import Literal, Optional

from pydantic import BaseModel

from app.engine import engine as eng
from app.engine.models import Catalog, DayPlan, Itinerary, ItineraryItem, PlanRequest
from app.engine.scoring import DAILY_BUDGET

DisruptionType = Literal["running_late", "weather", "closed", "skip", "extend", "add", "energy"]

ENERGY_SHORTEN_MINUTES = 90


class Disruption(BaseModel):
    type: DisruptionType
    day_index: int
    item_id: Optional[str] = None  # target item for closed / skip / extend
    now_time: Optional[str] = None  # "HH:MM" — required for running_late, optional override otherwise
    weather: Optional[Literal["rain", "heat"]] = None  # required for weather
    extend_minutes: Optional[int] = None  # required for extend
    place_id: Optional[str] = None  # required for add


class Change(BaseModel):
    item_id: Optional[str] = None
    title: str
    kind: Literal["removed", "added", "rescheduled", "modified"]
    reason: str


class ReplanResult(BaseModel):
    itinerary: Itinerary
    changes: list[Change]


class DisruptionError(ValueError):
    """A disruption that can't be applied as described (bad reference, no
    feasible slot, etc.) — the caller should surface `str(exc)` to the user."""


def _real_items(day: DayPlan) -> list[ItineraryItem]:
    return [i for i in day.items if i.kind not in ("travel", "transfer")]


def _find_item(day: DayPlan, item_id: str) -> ItineraryItem:
    for item in day.items:
        if item.id == item_id:
            return item
    raise DisruptionError(f"no item {item_id!r} on this day")


def _cutoff_minutes(day: DayPlan, disruption: Disruption, real_items: list[ItineraryItem]) -> int:
    if disruption.type == "running_late":
        if not disruption.now_time:
            raise DisruptionError("running_late requires now_time")
        return eng.time_to_minutes(disruption.now_time)

    if disruption.type in ("closed", "skip", "extend"):
        if not disruption.item_id:
            raise DisruptionError(f"{disruption.type} requires item_id")
        return eng.time_to_minutes(_find_item(day, disruption.item_id).start)

    # weather / add / energy: an explicit now_time wins, otherwise cut over at
    # the first item that hasn't happened yet.
    if disruption.now_time:
        return eng.time_to_minutes(disruption.now_time)
    for item in real_items:
        if item.status != "done":
            return eng.time_to_minutes(item.start)
    return eng.time_to_minutes(real_items[-1].end) if real_items else 0


def _item_location(catalog: Catalog, item: ItineraryItem):
    if item.kind == "place":
        return next(p for p in catalog.places if p.id == item.ref_id)
    if item.kind == "meal":
        return next(f for f in catalog.food if f.id == item.ref_id)
    raise DisruptionError(f"cannot locate anchor item of kind {item.kind!r}")


def _state_after(catalog: Catalog, request: PlanRequest, head_items: list[ItineraryItem]):
    """Where the traveler physically is, what category they last did, and how
    much of today's budget is already spent, once `head_items` have happened."""
    last_location = eng._resolve_base_location(request, catalog)
    last_category: str | None = None
    spent = 0
    for item in head_items:
        if item.status == "skipped":
            continue
        if item.kind == "place":
            place = next(p for p in catalog.places if p.id == item.ref_id)
            last_location = place
            last_category = place.category
            spent += place.cost_pp
        elif item.kind == "meal":
            food = next(f for f in catalog.food if f.id == item.ref_id)
            last_location = food
            spent += food.cost_pp
    return last_location, last_category, spent


def _meal_state(day_start0: int, day_end0: int, head_items: list[ItineraryItem]):
    meals_done = {
        meal: day_end0 <= w_start or day_start0 > w_end for meal, (w_start, w_end) in eng.MEAL_WINDOWS.items()
    }
    used_food_today: set[str] = set()
    for item in head_items:
        if item.kind != "meal" or item.status == "skipped":
            continue
        used_food_today.add(item.ref_id)
        start_minutes = eng.time_to_minutes(item.start)
        for meal, (w_start, w_end) in eng.MEAL_WINDOWS.items():
            if w_start <= start_minutes < w_end:
                meals_done[meal] = True
    return meals_done, used_food_today


def _next_id_counter(itinerary: Itinerary) -> itertools.count:
    max_n = 0
    for d in itinerary.days:
        for item in d.items:
            suffix = item.id.rsplit("_", 1)[-1]
            if suffix.isdigit():
                max_n = max(max_n, int(suffix))
    return itertools.count(max_n + 1)


def _segments(cutoff: int, day_end: int, anchors: list[ItineraryItem]) -> list[tuple[int, int]]:
    """Open stretches of time to greedily fill: before the first anchor,
    between consecutive anchors, and after the last one."""
    bounds = [cutoff]
    for a in anchors:
        bounds.append(eng.time_to_minutes(a.start))
        bounds.append(eng.time_to_minutes(a.end))
    bounds.append(day_end)
    return [(bounds[i], bounds[i + 1]) for i in range(0, len(bounds) - 1, 2)]


def _regenerate_tail(
    catalog: Catalog,
    request: PlanRequest,
    weekday: int,
    cutoff: int,
    day_end: int,
    anchors: list[ItineraryItem],
    exclude_ids: set[str],
    used_food_today: set[str],
    meals_done: dict[str, bool],
    remaining_budget: int,
    daily_budget_total: int,
    id_counter: itertools.count,
    last_location,
    last_category: str | None,
) -> list[ItineraryItem]:
    result: list[ItineraryItem] = []
    current = cutoff
    segments = _segments(cutoff, day_end, anchors)

    def next_id() -> str:
        return f"itm_{next(id_counter)}"

    for seg_index, (seg_start, seg_end) in enumerate(segments):
        if seg_end > seg_start:
            filled, last_location, last_category, remaining_budget, current = eng.fill_window(
                catalog,
                request,
                weekday,
                seg_start,
                seg_end,
                exclude_ids,
                used_food_today,
                meals_done,
                remaining_budget,
                daily_budget_total,
                id_counter,
                last_location,
                last_category,
            )
            result.extend(filled)
        else:
            current = seg_start

        if seg_index < len(anchors):
            anchor = anchors[seg_index]
            anchor_start = eng.time_to_minutes(anchor.start)
            loc = _item_location(catalog, anchor)
            travel = eng.travel_minutes(last_location, loc, request.has_own_vehicle)
            if travel >= 1 and current + travel <= anchor_start:
                result.append(eng._make_travel_item(next_id(), loc, travel, anchor_start - travel))
            result.append(anchor)
            last_location = loc
            last_category = getattr(loc, "category", last_category)
            remaining_budget -= anchor.cost_pp
            current = eng.time_to_minutes(anchor.end)

    return result


def _removed_reason(item: ItineraryItem, disruption: Disruption) -> str:
    if disruption.type == "closed" and item.id == disruption.item_id:
        return f"{item.title} is closed, so it was dropped from today."
    if disruption.type == "skip" and item.id == disruption.item_id:
        return f"Skipped {item.title} at your request."
    if disruption.type == "weather":
        word = "rain" if disruption.weather == "rain" else "heat"
        return f"{item.title} isn't a good fit in this {word}, so it was dropped."
    if disruption.type == "running_late":
        return f"No longer fits after running late — {item.title} was dropped."
    if disruption.type == "energy":
        return f"Trimmed to keep the rest of today lighter — {item.title} was dropped."
    if disruption.type == "extend":
        return f"No longer fits after extending an earlier stop — {item.title} was dropped."
    if disruption.type == "add":
        return f"No longer fits after adding a new stop — {item.title} was dropped."
    return f"{item.title} no longer fits the schedule."


def _added_reason(item: ItineraryItem, disruption: Disruption) -> str:
    if disruption.type == "add" and item.ref_id == disruption.place_id:
        return f"Added {item.title} as requested."
    return f"Added {item.title} to fill the freed-up time."


def _changed_reason(old: ItineraryItem, new: ItineraryItem, disruption: Disruption) -> tuple[str, str]:
    if disruption.type == "extend" and new.id == disruption.item_id:
        return "modified", f"Extended {new.title} by {disruption.extend_minutes} minutes."
    return "rescheduled", f"{new.title} moved from {old.start} to {new.start}."


def _diff_changes(
    old_real: list[ItineraryItem], new_real: list[ItineraryItem], disruption: Disruption
) -> list[Change]:
    old_by_id = {i.id: i for i in old_real}
    new_by_id = {i.id: i for i in new_real}
    changes: list[Change] = []

    for old in old_real:
        if old.id not in new_by_id:
            changes.append(
                Change(item_id=old.id, title=old.title, kind="removed", reason=_removed_reason(old, disruption))
            )

    for new in new_real:
        old = old_by_id.get(new.id)
        if old is None:
            changes.append(
                Change(item_id=new.id, title=new.title, kind="added", reason=_added_reason(new, disruption))
            )
        elif old.start != new.start or old.end != new.end:
            kind, reason = _changed_reason(old, new, disruption)
            changes.append(Change(item_id=new.id, title=new.title, kind=kind, reason=reason))

    return changes


def replan(itinerary: Itinerary, disruption: Disruption, catalog: Catalog, request: PlanRequest) -> ReplanResult:
    if not (0 <= disruption.day_index < len(itinerary.days)):
        raise DisruptionError("day_index out of range")

    day = itinerary.days[disruption.day_index]
    weekday = day.weekday
    real_items = _real_items(day)

    cutoff = _cutoff_minutes(day, disruption, real_items)

    head_items: list[ItineraryItem] = []
    tail_items: list[ItineraryItem] = []
    for item in real_items:
        if eng.time_to_minutes(item.start) < cutoff:
            if disruption.type == "running_late" and item.status == "planned":
                item = item.model_copy(update={"status": "done"})
            head_items.append(item)
        else:
            tail_items.append(item)

    arrival_minutes = eng.time_to_minutes(request.arrival_time)
    departure_minutes = eng.time_to_minutes(request.departure_time)
    day_start0, day_end0 = eng.compute_day_bounds(
        request.pace, disruption.day_index, len(itinerary.days), arrival_minutes, departure_minutes
    )
    meals_done, used_food_today = _meal_state(day_start0, day_end0, head_items)

    # Only places/food that actually survive into the new day (the frozen
    # head — locked/extended anchors are reserved below once known) block
    # themselves from re-selection. An old tail item that's about to be
    # discarded and regenerated must NOT keep excluding itself — otherwise a
    # dropped stop still poisons the pool it's supposed to free up.
    exclude_ids: set[str] = {i.ref_id for i in head_items if i.ref_id}

    def _reserve(item: ItineraryItem) -> None:
        """Stop the greedy re-fill from re-picking `item`'s place/food.
        Meals and places are tracked in separate sets by `fill_window`, so
        which one an anchor/removed item reserves depends on its kind."""
        if not item.ref_id:
            return
        if item.kind == "meal":
            used_food_today.add(item.ref_id)
        else:
            exclude_ids.add(item.ref_id)

    if disruption.type in ("closed", "skip"):
        target = next((i for i in tail_items if i.id == disruption.item_id), None)
        if target is None:
            raise DisruptionError(f"no upcoming item {disruption.item_id!r} to {disruption.type}")
        _reserve(target)
        tail_items = [i for i in tail_items if i.id != disruption.item_id]

    if disruption.type == "weather":
        if not disruption.weather:
            raise DisruptionError("weather disruption requires a weather value")
        for place in catalog.places:
            is_bad = (disruption.weather == "rain" and place.weather_dependent) or (
                disruption.weather == "heat" and place.heat_exposed
            )
            if is_bad:
                exclude_ids.add(place.id)
        # A lock is a firm commitment the traveler made on purpose — it survives
        # even a weather call, unlike an unlocked stop that just gets swapped out.
        tail_items = [i for i in tail_items if i.locked or not (i.kind == "place" and i.ref_id in exclude_ids)]

    anchors = [i for i in tail_items if i.locked and i.id != disruption.item_id]
    for anchor in anchors:
        _reserve(anchor)

    id_counter = _next_id_counter(itinerary)

    if disruption.type == "extend":
        target = _find_item(day, disruption.item_id) if disruption.item_id else None
        if target is None:
            raise DisruptionError("extend requires item_id")
        if not disruption.extend_minutes or disruption.extend_minutes <= 0:
            raise DisruptionError("extend requires a positive extend_minutes")
        orig_duration = eng.time_to_minutes(target.end) - eng.time_to_minutes(target.start)
        new_end = eng.time_to_minutes(target.start) + orig_duration + disruption.extend_minutes
        extended = target.model_copy(update={"end": eng.minutes_to_time(new_end)})
        anchors.append(extended)
        _reserve(extended)

    last_location, last_category, spent = _state_after(catalog, request, head_items)
    remaining_budget = DAILY_BUDGET[request.budget_level] - spent
    daily_budget_total = DAILY_BUDGET[request.budget_level]

    day_end = day_end0
    if disruption.type == "energy":
        day_end = max(cutoff, day_end0 - ENERGY_SHORTEN_MINUTES)

    if disruption.type == "add":
        if not disruption.place_id:
            raise DisruptionError("add requires place_id")
        add_place = next((p for p in catalog.places if p.id == disruption.place_id), None)
        if add_place is None:
            raise DisruptionError(f"unknown place {disruption.place_id!r}")
        if add_place.id in exclude_ids:
            raise DisruptionError(f"{add_place.name} is already part of today's plan")
        arrive = cutoff + eng.travel_minutes(last_location, add_place, request.has_own_vehicle)
        start = eng.earliest_start(add_place, arrive, weekday, day_end)
        if start is None:
            raise DisruptionError(f"{add_place.name} doesn't fit into today's remaining schedule")
        end = start + add_place.duration_min
        forced = ItineraryItem(
            id=f"itm_{next(id_counter)}",
            start=eng.minutes_to_time(start),
            end=eng.minutes_to_time(end),
            kind="place",
            ref_id=add_place.id,
            title=add_place.name,
            area=add_place.area,
            cost_pp=add_place.cost_pp,
            notes=add_place.notes,
            booking_url=add_place.booking_url,
            map_url=eng.map_url_for(add_place),
        )
        anchors.append(forced)
        _reserve(forced)

    anchors.sort(key=lambda i: eng.time_to_minutes(i.start))
    for a, b in zip(anchors, anchors[1:]):
        if eng.time_to_minutes(a.end) > eng.time_to_minutes(b.start):
            raise DisruptionError("that doesn't fit alongside the rest of today's plan")

    new_tail = _regenerate_tail(
        catalog,
        request,
        weekday,
        cutoff,
        day_end,
        anchors,
        exclude_ids,
        used_food_today,
        meals_done,
        remaining_budget,
        daily_budget_total,
        id_counter,
        last_location,
        last_category,
    )

    new_day_items = head_items + new_tail
    new_day = DayPlan(date=day.date, weekday=weekday, items=new_day_items)
    new_days = list(itinerary.days)
    new_days[disruption.day_index] = new_day
    new_itinerary = Itinerary(days=new_days)

    # Diff on "real" stops only — travel connectors are an implementation
    # detail of the schedule, not something a traveler reads as a change.
    new_real_items = [i for i in new_day_items if i.kind not in ("travel", "transfer")]
    changes = _diff_changes(real_items, new_real_items, disruption)
    return ReplanResult(itinerary=new_itinerary, changes=changes)
