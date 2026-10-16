# happend-backend

Backend for Happend, a trip planner for day-by-day itineraries. Two things make up the actual planning logic:

- **The itinerary engine** (`app/engine/`): a deterministic, rule-based planner. It builds and re-plans a day's itinerary against a fixed catalog of places, food, and stays, checking real opening hours, closed days, and travel time — no LLM involved. This also handles live disruptions (running late, bad weather, a place turning out to be closed, etc.) by re-running the same deterministic logic against the current state.
- **The AI Guide** (`app/routers/guide.py`): a conversational layer on top, backed by an LLM with function-calling. It can look up places in the catalog and suggest changes, but it never edits the itinerary directly — mutating actions (add/remove/reorder a stop) are handed back to the frontend as a proposed action, which applies the same feasibility checks the deterministic engine already uses before anything actually changes.

## Stack

- **FastAPI** (Python), **SQLAlchemy** ORM
- **PostgreSQL** in production (via `DATABASE_URL`), SQLite locally if that's unset
- **Gemini** for the AI Guide's primary model, with **Groq** and **OpenRouter** as fallback providers (see below)

## Running locally

```
pip install -r requirements.txt
pytest                                    # run the test suite
uvicorn app.main:app --reload             # run the API
```

No database setup step is needed — `init_db()` runs on startup and creates tables via SQLAlchemy's `create_all()`. There's no migration framework; schema changes need new tables, not altered columns on existing ones.

### Environment variables

Everything below is optional in the sense that the app starts without it — the feature it gates just degrades gracefully (falls back to SQLite, skips sending email, returns 503 for the Guide, etc.) rather than crashing.

| Variable | Used for |
|---|---|
| `DATABASE_URL` | Postgres connection string. Without it, falls back to a local SQLite file. |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | AI Guide's primary model. Without the key, `/api/guide/chat` returns 503. |
| `GROQ_API_KEY` / `GROQ_MODEL` | AI Guide fallback provider. Skipped if unset. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | AI Guide fallback provider. Skipped if unset. |
| `RESEND_API_KEY` / `EMAIL_FROM` | Sending verification/reset emails. Without the key, emails are logged instead of sent. |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_NAME` | Trip-memory photo storage (Cloudflare R2). Without these, falls back to local disk — fine for dev, not for most hosts with an ephemeral filesystem. |
| `FRONTEND_URL` | Where emailed verify/reset links point. |
| `CORS_ORIGINS` | Comma-separated allowed origins. |
| `COOKIE_SECURE` / `COOKIE_SAMESITE` | Cookie flags — need adjusting if frontend and backend aren't on the same origin. |

## AI Guide reliability work

The Guide depends on a third-party LLM with its own rate limits, which has caused real outages before (a model's free-tier daily quota running out with no fallback). Three pieces address that:

- **Eval harness** (`tests/evals/`) — a small, checklist-based eval set run against the real API (not mocked), checking the Guide never invents a place outside the catalog, respects explicit constraints, and never hands back a tool call with an id that doesn't actually exist. See `tests/evals/README.md` for how to run it and what it checks.
- **Observability** — every call to the model and every tool dispatch (`find_place`, `find_open_after`, and the client-executed add/remove/reorder actions) logs its latency and token/result cost. Enough to answer "where does the Guide spend time or money," not a full tracing system.
- **Cross-provider fallback chain** — Gemini has 3 model variants in its own fallback chain, but they all share one quota pool, so they can and do run out together. Groq and OpenRouter are genuinely separate providers with their own quota, tried in that order once Gemini is exhausted. Both speak the OpenAI chat-completions format, not Gemini's, so `guide.py` translates at the edges only; the rest of the Guide's logic doesn't know or care which provider actually answered.
