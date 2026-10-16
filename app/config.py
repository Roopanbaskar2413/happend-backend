"""Environment-driven settings. Defaults match local dev over http://localhost;
override via env vars when the frontend/backend aren't on the same origin
(e.g. sharing the app over a tunnel), where cookies need SameSite=None +
Secure and CORS needs the tunnel's actual origin allow-listed.
"""
import os

CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "lax")

# Email (verification / password reset). Without RESEND_API_KEY, app/email.py
# logs instead of sending — fine for local dev, CI, and tests.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")
# Where emailed verify/reset links point — the deployed frontend origin.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

# Trip-memory photo storage (Cloudflare R2). Without these, app/storage.py
# falls back to local disk — fine for local dev/tests, not for Render
# (ephemeral filesystem, wiped on every redeploy).
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")

# AI trip guide (Gemini). Without GEMINI_API_KEY, /api/guide/chat returns 503
# instead of crashing — fine for local dev/tests where no key is set.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# Cross-provider fallback for the guide: when every Gemini model in the chain
# is rate-limited (same free-tier quota pool for all of them), fall through
# to a different provider entirely instead of giving up. Both optional --
# without an API key, that provider is just skipped, same pattern as above.
#
# GROQ_MODEL: Groq's own /docs/models page lists "llama-3.3-70b-versatile"
# as Production, but a live call against this project's actual API key 404s
# on it ("does not exist or you do not have access to it") -- the real,
# authoritative source is what GET /openai/v1/models returns for your own
# key, not the docs page, and that disagreed here. "openai/gpt-oss-120b" is
# verified working (incl. tool-calling) against a real key as of 2026-10-16.
# Re-verify against your own key before trusting either the docs or this
# comment -- this model lineup has already changed more than once in the
# last few months.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
