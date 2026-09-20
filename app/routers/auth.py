from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.auth_deps import require_user
from app.config import COOKIE_SAMESITE, COOKIE_SECURE
from app.db import get_db
from app.email import send_password_reset_email, send_verification_email
from app.limiter import limiter
from app.orm import EmailToken
from app.orm import Session as SessionModel
from app.orm import User
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    UserOut,
    VerifyEmailRequest,
)
from app.security import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, hash_password, new_session_token, verify_password

router = APIRouter()

COOKIE_MAX_AGE = SESSION_TTL_DAYS * 24 * 60 * 60
VERIFY_TOKEN_TTL_HOURS = 24
RESET_TOKEN_TTL_HOURS = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_token(db: DbSession, user: User, purpose: str, ttl_hours: int) -> str:
    token = EmailToken(user_id=user.id, purpose=purpose, expires_at=_now() + timedelta(hours=ttl_hours))
    db.add(token)
    db.commit()
    return token.id


def _consume_token(db: DbSession, token_value: str, purpose: str) -> User:
    token = db.get(EmailToken, token_value)
    if token is None or token.purpose != purpose or token.used_at is not None:
        raise HTTPException(status_code=400, detail="invalid or already-used link")
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < _now():
        raise HTTPException(status_code=400, detail="this link has expired")
    token.used_at = _now()
    db.commit()
    return db.get(User, token.user_id)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        path="/",
    )


def _create_session(db: DbSession, user: User) -> str:
    session = SessionModel(id=new_session_token(), user_id=user.id)
    db.add(session)
    db.commit()
    return session.id


@router.post("/auth/signup", response_model=UserOut)
@limiter.limit("5/hour")
def signup(
    request: Request, body: SignupRequest, response: Response, db: DbSession = Depends(get_db)
):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="an account with that email already exists")
    db.refresh(user)

    verify_token = _make_token(db, user, "verify", VERIFY_TOKEN_TTL_HOURS)
    send_verification_email(user.email, verify_token)

    token = _create_session(db, user)
    _set_session_cookie(response, token)
    return user


@router.post("/auth/login", response_model=UserOut)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest, response: Response, db: DbSession = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    # Same generic error whether the email doesn't exist or the password is wrong,
    # so a login attempt can't be used to discover which emails have accounts.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="incorrect email or password")

    token = _create_session(db, user)
    _set_session_cookie(response, token)
    return user


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = db.get(SessionModel, token)
        if session is not None:
            db.delete(session)
            db.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(require_user)):
    return user


@router.post("/auth/verify-email", response_model=UserOut)
def verify_email(body: VerifyEmailRequest, db: DbSession = Depends(get_db)):
    user = _consume_token(db, body.token, "verify")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


@router.post("/auth/resend-verification")
@limiter.limit("5/hour")
def resend_verification(request: Request, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    if user.email_verified:
        return {"ok": True, "already_verified": True}
    verify_token = _make_token(db, user, "verify", VERIFY_TOKEN_TTL_HOURS)
    send_verification_email(user.email, verify_token)
    return {"ok": True, "already_verified": False}


@router.post("/auth/forgot-password")
@limiter.limit("5/hour")
def forgot_password(request: Request, body: ForgotPasswordRequest, db: DbSession = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    # Always the same response whether or not the email has an account, so
    # this can't be used to discover which emails are registered.
    if user is not None:
        reset_token = _make_token(db, user, "reset", RESET_TOKEN_TTL_HOURS)
        send_password_reset_email(user.email, reset_token)
    return {"ok": True}


@router.post("/auth/reset-password")
@limiter.limit("10/hour")
def reset_password(request: Request, body: ResetPasswordRequest, db: DbSession = Depends(get_db)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    user = _consume_token(db, body.token, "reset")
    user.password_hash = hash_password(body.new_password)
    # Resetting a password is a "something may have been compromised" moment —
    # kill every existing session so a stolen cookie stops working too.
    db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
    db.commit()
    return {"ok": True}
