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
