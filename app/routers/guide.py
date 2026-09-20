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
from app.limiter import limiter
from app.schemas import GuideRequest, GuideResponse, GuideToolCall

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
                    "invent a place or id."
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

Rules:
- Only ever refer to places that come from find_place's results or from today's itinerary \
below. Never invent a place, id, or opening hours.
- If a request is ambiguous — "move dinner earlier" without saying earlier than what, or \
"add something fun" without specifics — ask one short clarifying question instead of guessing.
- Keep replies short (1-3 sentences), like a real guide texting back, not a formal assistant.
- If a tool call fails or a place turns out closed at the only slot available, say so plainly \
and suggest an alternative if one is obvious from context.
- Call find_place at most 2-3 times per user message. If a broad search (e.g. "nightlife") comes \
back empty, try one or two more specific/related terms, then answer with whatever you found \
instead of continuing to search — a partial answer beats no answer.

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
                    },
                )
            )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:MAX_FIND_PLACE_RESULTS]]


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
                return GuideResponse(
                    contents=[c.model_dump(mode="json", exclude_none=True) for c in contents],
                    reply=response.text or "",
                )

            args = dict(function_call.args or {})

            if function_call.name in CLIENT_TOOL_NAMES:
                return GuideResponse(
                    contents=[c.model_dump(mode="json", exclude_none=True) for c in contents],
                    tool_call=GuideToolCall(name=function_call.name, args=args, call_id=function_call.id),
                )

            if function_call.name == "find_place":
                result = {"result": _find_place(catalog, args.get("query", ""))}
            else:
                result = {"error": f"unknown tool {function_call.name!r}"}

            response_part = types.Part.from_function_response(name=function_call.name, response=result)
            contents.append(types.Content(role="user", parts=[response_part]))
    except Exception:  # noqa: BLE001 - last-resort net: never surface a raw error to the user
        logger.exception("guide: unexpected error")
        return friendly_fallback("unexpected exception")

    return friendly_fallback("exceeded max internal steps")
