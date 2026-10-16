# AI Guide evals

A small, checklist-based eval set for the AI Guide, grounded in the real
Pondicherry catalog -- not part of the normal pytest suite, because it
calls the real Gemini API (real quota, real cost) instead of a mocked
client.

## Run it

```
cd backend && .venv/bin/python -m tests.evals.run_guide_eval
```

Requires `GEMINI_API_KEY` set in your environment (same var the app uses).

## What it checks

Each case in `cases.py` is a realistic trip-planning message. `run_guide_eval.py`
sends it to the real `/api/guide/chat` endpoint and checks the response against
a checklist grounded in the catalog:

- **No hallucinated places** -- every `suggested_places` id must actually exist
  in the catalog.
- **Right tool for the job** -- a "what's open at this time" question must use
  `find_open_after`, not `find_place`.
- **Hallucination resistance** -- asking for something genuinely absent from
  the catalog (a bowling alley) must get an honest "not found", not an invented
  one.
- **Closed-day awareness** -- suggesting/adding a place on a day it's closed
  must say so, not silently approve it.
- **Explicit constraints respected** -- a "no seafood" or "under 200 rupees"
  ask must actually filter the suggestions.
- **Tool-call id validity** -- `add_place`/`remove_place` must reference real
  catalog ids / ids that actually exist in the itinerary context passed in,
  never an invented one.

## Known limitation

These are automated structural checks (does a returned id exist, does a tool
name match), not a judge of whether the *prose* itself is good or subtly wrong.
A model could pass every check here while still writing an awkward or
misleading sentence -- that still needs an occasional human read of the
`reply` text printed by the runner.

## Why this isn't in CI

It costs real API quota per run and isn't deterministic (the same case can get
a different response on different runs, and a transient 429/503 from Gemini's
free tier can fail a case for reasons that have nothing to do with the Guide's
actual correctness -- see the fallback-chain work in progress). Run it by hand
after a change to `guide.py`'s prompt, tools, or fallback logic, not on every
push.
