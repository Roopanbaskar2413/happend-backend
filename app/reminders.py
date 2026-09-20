"""Trip-reminder logic: finds saved plans starting soon and notifies the owner.

No real email provider is configured yet. `send_reminder_email` is the single
integration point — swap its body for a real provider (Resend, SendGrid, SMTP,
etc.) and nothing else in this file needs to change. Until then it only logs
and records that a reminder "went out," so the rest of the pipeline (finding
due plans, marking them sent, not re-sending) is real and testable.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.orm import SavedPlan

logger = logging.getLogger("reminders")

REMINDER_WINDOW_DAYS = 2  # send when the trip starts within this many days


def send_reminder_email(plan: SavedPlan) -> None:
    logger.info(
        "[SIMULATED EMAIL] To: %s | Subject: Your %s trip starts %s | (not actually delivered — "
        "no email provider configured)",
        plan.email,
        plan.city,
        plan.arrival_date,
    )


def due_plans(db: Session, today: date | None = None) -> list[SavedPlan]:
    today = today or datetime.now(timezone.utc).date()
    cutoff = (today + timedelta(days=REMINDER_WINDOW_DAYS)).isoformat()
    today_iso = today.isoformat()
    stmt = select(SavedPlan).where(
        SavedPlan.reminder_sent_at.is_(None),
        SavedPlan.arrival_date >= today_iso,
        SavedPlan.arrival_date <= cutoff,
    )
    return list(db.scalars(stmt))


def send_due_reminders(db: Session) -> int:
    plans = due_plans(db)
    for plan in plans:
        send_reminder_email(plan)
        plan.reminder_sent_at = datetime.now(timezone.utc)
    if plans:
        db.commit()
    return len(plans)
