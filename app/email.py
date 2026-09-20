"""Transactional email via Resend. Mirrors app/reminders.py's fallback
pattern: with no RESEND_API_KEY configured (local dev, CI, tests), it logs
instead of sending, so nothing here requires real credentials to run.
"""
from __future__ import annotations

import logging

import httpx

from app.config import EMAIL_FROM, FRONTEND_URL, RESEND_API_KEY

logger = logging.getLogger("email")


def send_email(to: str, subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        logger.info("[SIMULATED EMAIL] To: %s | Subject: %s | (RESEND_API_KEY not configured)", to, subject)
        return
    # Best-effort: a bounced/rejected/rate-limited send (e.g. Resend's sandbox
    # sender only delivering to the account owner) must never break the
    # actual account action (signup, password reset) that triggered it.
    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to send email to %s (subject: %s)", to, subject)


def send_verification_email(to: str, token: str) -> None:
    link = f"{FRONTEND_URL}/verify-email?token={token}"
    send_email(
        to,
        "Verify your Happend account",
        f'<p>Confirm your email to finish setting up your account.</p>'
        f'<p><a href="{link}">Verify email</a></p>'
        f'<p>This link expires in 24 hours.</p>',
    )


def send_password_reset_email(to: str, token: str) -> None:
    link = f"{FRONTEND_URL}/reset-password?token={token}"
    send_email(
        to,
        "Reset your Happend password",
        f'<p>Someone requested a password reset for this account. If this was you, click below:</p>'
        f'<p><a href="{link}">Reset password</a></p>'
        f"<p>This link expires in 1 hour. If you didn't request this, you can ignore this email.</p>",
    )
