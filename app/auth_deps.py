from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from app.db import get_db
from app.orm import Session as SessionModel
from app.orm import User
from app.security import SESSION_COOKIE_NAME, SESSION_TTL_DAYS


def get_current_user(request: Request, db: DbSession = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    session = db.get(SessionModel, token)
    if session is None:
        return None

    now = datetime.now(timezone.utc)
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        db.delete(session)
        db.commit()
        return None

    # Sliding expiry: being active extends the session.
    session.last_seen_at = now
    session.expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    db.commit()

    return db.get(User, session.user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user
