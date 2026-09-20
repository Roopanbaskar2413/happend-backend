"""Conversational trip guide, backed by Gemini function-calling.

The model never edits the itinerary itself. `find_place` is the only tool
executed here (a read-only catalog search); `add_place`/`remove_place`/
`reorder_before` are returned to the frontend as a pending `tool_call` and
executed there against the live itinerary state, using the same
opening-hours/feasibility checks that already power drag-reorder and
"+ Add a place". That's what keeps the guide from ever producing an
itinerary with a place that doesn't exist or a stop scheduled while closed.
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from google.genai import Client, errors, types

from app.config import GEMINI_API_KEY, GEMINI_MODEL
from app.engine.catalog import CityNotAvailable, load_catalog
from app.engine.engine import time_to_minutes
from app.limiter import limiter
from app.schemas import GuideRequest, GuideResponse, GuideSuggestedPlace, GuideToolCall

router = APIRouter()
logger = logging.getLogger("guide")

MAX_INTERNAL_STEPS = 8
MAX_FIND_PLACE_RESULTS = 5

CLIENT_TOOL_NAMES = {"add_place", "remove_place", "reorder_before"}

# The user should never see a raw provider error ("rate limited", "quota
# exhausted") — if one model is out of quota, silently try the next free-tier
# model before giving up, and if every model is unavailable, the guide still
# answers in character instead of surfacing anything technical.
#
# "gemini-flash-latest" is Google's self-updating alias for whatever the
# current-generation flash model is — listed first so this chain doesn't go
# stale again the way a hardcoded "gemini-2.5-flash" did (retired for new API
# keys within the same year it shipped). The pinned names after it are exact
# fallbacks in case the alias itself has an outage.
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
MODEL_COOLDOWN_SECONDS = 300
FALLBACK_REPLY = "I'm a bit swamped right now — give me a moment and try that again?"

_model_cooldowns: dict[str, float] = {}


def _model_chain() -> list[str]:
    ordered = [GEMINI_MODEL, *FALLBACK_MODELS]
    seen: set[str] = set()
    chain = []
    for model in ordered:
        if model not in seen:
            seen.add(model)
            chain.append(model)
    return chain


def _available_models() -> list[str]:
    now = time.monotonic()
    chain = _model_chain()
    fresh = [m for m in chain if _model_cooldowns.get(m, 0.0) <= now]
    return fresh or chain  # everything's cooling down — try anyway rather than give up


def _mark_rate_limited(model: str) -> None:
    _model_cooldowns[model] = time.monotonic() + MODEL_COOLDOWN_SECONDS

_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="find_place",
                description=(
                    "Search the destination's catalog of places and restaurants by name, "
                    "category, or interest keyword. Always call this before add_place to "
                    "resolve what the user is asking for into a real catalog id — never "
                    "invent a place or id. Each result includes real 'windows' (opening-hour "
                    "ranges like '19:00-23:00') and 'closed_days' — use those, not the category "
                    "name, to judge whether something is actually open at a given time. A wide "
                    "range like '00:00-23:59' means effectively always open (e.g. most beaches, "
                    "promenades). When asked what's open late, don't only search nightlife-ish "
                    "terms — also check beaches/outdoor spots, which are often open around the "
                    "clock even though 'beach' doesn't sound like nightlife."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "search text, e.g. 'museum' or 'seafood'",
                        }
                    },
                    "required": ["query"],
                },
            ),
            types.FunctionDeclaration(
                name="find_open_after",
                description=(
                    "Returns EVERY real catalog place/restaurant where a full visit genuinely "
                    "fits starting at the given time today — already checked against real "
                    "opening-hour data AND each place's required duration (a place that's "
                    "technically still open but doesn't have enough time left for a full visit "
                    "before closing is correctly left out, not just filtered by 'open right "
                    "now'). Grouped into two fixed categories: 'attractions' (kind=place: "
                    "beaches, nightlife spots, turfs, activities, etc.) and 'restaurants' "
                    "(kind=meal: cafes, bars, restaurants). ALWAYS use this instead of "
                    "find_place whenever the user asks what's open/available at or after a "
                    "specific time — never guess from category names or call find_place with "
                    "terms like 'nightlife' for this kind of question. Every result returned "
                    "here can genuinely be added and fully completed — don't second-guess it."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "time": {
                            "type": "string",
                            "description": "24-hour HH:MM, e.g. '22:00' for 10pm",
                        }
                    },
                    "required": ["time"],
                },
            ),
            types.FunctionDeclaration(
                name="add_place",
                description="Add a catalog place to today's itinerary. Requires a place_id from find_place.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"place_id": {"type": "string"}},
                    "required": ["place_id"],
                },
            ),
            types.FunctionDeclaration(
                name="remove_place",
                description=(
                    "Remove a stop that is currently in today's itinerary. item_id must be "
                    "one of the ids given in the itinerary context, never guessed."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {"item_id": {"type": "string"}},
                    "required": ["item_id"],
                },
            ),
            types.FunctionDeclaration(
                name="reorder_before",
                description=(
                    "Move an existing itinerary item to happen earlier, right before another "
                    "existing item. Both ids must already be in today's itinerary context."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "description": "the item to move"},
                        "before_item_id": {"type": "string", "description": "move it to before this item"},
                    },
                    "required": ["item_id", "before_item_id"],
                },
            ),
        ]
    )
]

_SYSTEM_PROMPT = """You are a friendly, concise local trip guide inside the Happend itinerary \
app for {city}. You help the traveler adjust today's plan by chatting naturally.

CRITICAL RULE — read this twice: you may ONLY say the name of a place that appears, verbatim, \
in a find_place result you actually received in this conversation, or in today's itinerary \
below. This app has a real, fixed catalog of real businesses — it is NOT okay to make up a \
plausible-sounding name (a fake bar, restaurant, or attraction) even if it fits the vibe of \
what the user asked for. If find_place returns zero or few results for what the user wants, \
that means the catalog genuinely doesn't have it — say so honestly ("I don't have anything \
like that in the catalog") rather than inventing options. Every business you mention must be \
traceable to one specific find_place result.

SECOND CRITICAL RULE — never fabricate a success message: you may ONLY say a place was added, \
removed, or moved in the SAME turn where you actually called add_place/remove_place/reorder_before \
and it succeeded. Saying "I've added X" or "Done!" without having just made that exact tool call \
in this response is a lie the user cannot detect until they look at their itinerary — never do \
this. Concretely: when the user confirms an action ("yes", "sure", "go ahead", naming a place), \
your very next step must be to CALL the tool, not to describe the result in words. You also \
cannot control WHERE in the day a new place lands (add_place always appends to the end of \
today's schedule) — never claim it was placed "before lunch," "after X," or at any specific \
position; just say it was added to today's plan.

Other rules:
- If a request is ambiguous — "move dinner earlier" without saying earlier than what, or \
"add something fun" without specifics — ask one short clarifying question instead of guessing.
- Keep replies short (1-3 sentences), like a real guide texting back, not a formal assistant.
- If a tool call fails or a place turns out closed at the only slot available, say so plainly \
and suggest an alternative if one is obvious from context — again, only from find_place results.
- Call find_place at most 2-3 times per user message. If a broad search (e.g. "nightlife") comes \
back empty, try one or two more specific/related terms, then answer with only the real results \
you found — never pad the list with invented names to seem more helpful.

When asked what's open/available at or after a specific time (e.g. "anywhere open after 10pm"), \
follow this exact flow instead of guessing a partial answer yourself:
1. Call find_open_after with that time. Do not call find_place for this.
2. Look at which of the two categories (attractions, restaurants) actually have results. Tell \
the user which categories have options WITHOUT listing individual places yet, and ask which \
one they want to see (skip this step and go straight to step 3 if only one category has results).
3. Once they pick a category, the app shows the user every option as clickable cards below your \
message — you do NOT need to list them all out in your text. Just say something short like \
"Here's what's open — tap one to add it!" (the results you already have from find_open_after \
are all that matters; don't invent additional commentary about places not in that list).
4. If they confirm one by name, call add_place with that place's id from the find_open_after \
result you already have — no need to call find_place again.

Today is weekday index {weekday} (0=Monday). Today's itinerary items:
{items_json}
"""


def _find_place(catalog, query: str) -> list[dict]:
    # A multi-word query like "nightlife pub bar" should match a place
    # tagged only "nightlife" -- matching the whole phrase as one substring
    # (the previous approach) never matched anything but an exact name.
    words = [w for w in query.strip().lower().split() if w]
    if not words:
        return []

    def score(name, category, interests):
        haystack = [name or "", category or "", *interests]
        hits = sum(1 for w in words for field in haystack if w in field.lower())
        return hits

    scored = []
    for p in catalog.places:
        hits = score(p.name, p.category, p.interests)
        if hits:
            scored.append(
                (
                    hits,
                    {
                        "id": p.id,
                        "name": p.name,
                        "kind": "place",
                        "category": p.category,
                        "duration_min": p.duration_min,
                        "cost_pp": p.cost_pp,
                        "rating": p.rating,
                        "windows": p.windows,
                        "closed_days": p.closed_days,
                    },
                )
            )
    for f in catalog.food:
        hits = score(f.name, f.price_band, [])
        if hits:
            scored.append(
                (
                    hits,
                    {
                        "id": f.id,
                        "name": f.name,
                        "kind": "meal",
                        "category": f.price_band,
                        "duration_min": f.duration_min,
                        "cost_pp": f.cost_pp,
                        "rating": f.rating,
                        "windows": f.windows,
                        "closed_days": f.closed_days,
                    },
                )
            )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:MAX_FIND_PLACE_RESULTS]]


def _still_open_at(
    windows: list[str], closed_days: list[int], weekday: int, at_minutes: int, duration_min: int
) -> str | None:
    """Returns the closing time (HH:MM) of the window covering `at_minutes`
    today, or None if closed at that time OR if a full `duration_min` visit
    starting then wouldn't fit before closing (e.g. a 60-min activity
    starting 20 minutes before close doesn't actually fit, even though the
    place is technically "open" at that instant). A window that wraps past
    midnight (end <= start, e.g. "22:00-02:00") counts `at_minutes` as
    inside it if it's on either side of midnight."""
    if weekday in closed_days:
        return None
    for w in windows:
        start_str, end_str = w.split("-")
        start, end = time_to_minutes(start_str), time_to_minutes(end_str)
        if end <= start:  # wraps past midnight
            if at_minutes >= start:
                remaining = (end + 1440) - at_minutes
            elif at_minutes < end:
                remaining = end - at_minutes
            else:
                continue
        else:
            if not (start <= at_minutes < end):
                continue
            remaining = end - at_minutes
        if remaining >= duration_min:
            return end_str
    return None


def _find_open_after(catalog, weekday: int, time_str: str) -> dict[str, list[dict]]:
    try:
        at_minutes = time_to_minutes(time_str)
    except (ValueError, IndexError):
        at_minutes = 22 * 60  # a malformed time from the model still returns something usable

    attractions = []
    for p in catalog.places:
        closes_at = _still_open_at(p.windows, p.closed_days, weekday, at_minutes, p.duration_min)
        if closes_at:
            attractions.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "category": p.category,
                    "closes_at": closes_at,
                    "duration_min": p.duration_min,
                    "cost_pp": p.cost_pp,
                    "rating": p.rating,
                }
            )

    restaurants = []
    for f in catalog.food:
        closes_at = _still_open_at(f.windows, f.closed_days, weekday, at_minutes, f.duration_min)
        if closes_at:
            restaurants.append(
                {
                    "id": f.id,
                    "name": f.name,
                    "category": f.price_band,
                    "closes_at": closes_at,
                    "duration_min": f.duration_min,
                    "cost_pp": f.cost_pp,
                    "rating": f.rating,
                }
            )

    return {"attractions": attractions, "restaurants": restaurants}


@router.post("/guide/chat", response_model=GuideResponse)
@limiter.limit("30/hour")
def guide_chat(request: Request, body: GuideRequest):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="the AI guide isn't configured yet")

    try:
        catalog = load_catalog(body.city)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown city {body.city!r}")
    except CityNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    system_text = _SYSTEM_PROMPT.format(
        city=body.city,
        weekday=body.day.weekday,
        items_json=json.dumps([item.model_dump() for item in body.day.items]),
    )

    contents = [types.Content.model_validate(c) for c in body.contents]
    client = Client(api_key=GEMINI_API_KEY)
    last_suggestions: list[dict] = []

    def friendly_fallback(reason: str) -> GuideResponse:
        logger.warning("guide falling back to canned reply: %s", reason)
        contents.append(types.Content(role="model", parts=[types.Part(text=FALLBACK_REPLY)]))
        return GuideResponse(
            contents=[c.model_dump(mode="json", exclude_none=True) for c in contents],
            reply=FALLBACK_REPLY,
        )

    def call_gemini():
        """Tries every model in the fallback chain — on ANY failure (rate
        limit, a model name unavailable for this key's tier, a transient
        provider error) it moves on to the next one, only giving up once
        every model has failed (returns None)."""
        for model in _available_models():
            try:
                return client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=system_text, tools=_TOOLS),
                )
            except errors.ClientError as exc:
                if getattr(exc, "code", None) == 429:
                    _mark_rate_limited(model)
                logger.warning("guide: model %s failed (%s), trying next", model, exc)
                continue
            except errors.APIError as exc:
                logger.warning("guide: model %s failed (%s), trying next", model, exc)
                continue
        return None

    try:
        for _ in range(MAX_INTERNAL_STEPS):
            response = call_gemini()
            if response is None:
                return friendly_fallback("no model in the fallback chain could answer")

            candidate_content = response.candidates[0].content
            contents.append(candidate_content)

            function_call = next(
                (part.function_call for part in candidate_content.parts or [] if part.function_call),
                None,
            )

            if function_call is None:
                # Built from actual text parts, not the SDK's `.text` helper --
                # that helper can stringify a stray non-text part (a
                # function_call/function_response with no text) into
                # debug-looking garbage like "response:default_api:find_place{...}"
                # when a candidate has no real text, which must never reach chat.
                text_parts = [part.text for part in candidate_content.parts or [] if part.text]
                reply_text = "".join(text_parts).strip()
                if not reply_text:
                    return friendly_fallback("model returned no usable text")
                return GuideResponse(
                    contents=[c.model_dump(mode="json", exclude_none=True) for c in contents],
                    reply=reply_text,
                    suggested_places=[GuideSuggestedPlace(**s) for s in last_suggestions] or None,
                )

            args = dict(function_call.args or {})

            if function_call.name in CLIENT_TOOL_NAMES:
                return GuideResponse(
                    contents=[c.model_dump(mode="json", exclude_none=True) for c in contents],
                    tool_call=GuideToolCall(name=function_call.name, args=args, call_id=function_call.id),
                )

            if function_call.name == "find_place":
                matches = _find_place(catalog, args.get("query", ""))
                result = {"result": matches}
                last_suggestions = [
                    {
                        "id": m["id"],
                        "name": m["name"],
                        "kind": m["kind"],
                        "category": m.get("category"),
                        "closes_at": None,
                        "duration_min": m["duration_min"],
                        "cost_pp": m["cost_pp"],
                        "rating": m["rating"],
                    }
                    for m in matches
                ]
            elif function_call.name == "find_open_after":
                grouped = _find_open_after(catalog, body.day.weekday, args.get("time", ""))
                result = grouped
                last_suggestions = [
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "kind": kind,
                        "category": item.get("category"),
                        "closes_at": item.get("closes_at"),
                        "duration_min": item["duration_min"],
                        "cost_pp": item["cost_pp"],
                        "rating": item["rating"],
                    }
                    for kind, key in (("place", "attractions"), ("meal", "restaurants"))
                    for item in grouped[key]
                ]
            else:
                result = {"error": f"unknown tool {function_call.name!r}"}

            response_part = types.Part.from_function_response(name=function_call.name, response=result)
            contents.append(types.Content(role="user", parts=[response_part]))
    except Exception:  # noqa: BLE001 - last-resort net: never surface a raw error to the user
        logger.exception("guide: unexpected error")
        return friendly_fallback("unexpected exception")

    return friendly_fallback("exceeded max internal steps")
