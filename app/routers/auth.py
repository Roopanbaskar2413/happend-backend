from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.auth_deps import require_user
from app.config import COOKIE_SAMESITE, COOKIE_SECURE
from app.db import get_db
from app.limiter import limiter
from app.orm import Session as SessionModel
from app.orm import User
from app.schemas import LoginRequest, SignupRequest, UserOut
from app.security import SESSION_COOKIE_NAME, SESSION_TTL_DAYS, hash_password, new_session_token, verify_password

router = APIRouter()

COOKIE_MAX_AGE = SESSION_TTL_DAYS * 24 * 60 * 60


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
