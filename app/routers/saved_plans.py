from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth_deps import require_user
from app.db import get_db
from app.orm import PlanShare, SavedPlan, User
from app.reminders import send_due_reminders
from app.schemas import (
    SavePlanRequest,
    SavedPlanOut,
    ShareOut,
    ShareRequest,
    SharedPlanOut,
    UpdatePlanRequest,
    UpdatePlanStatusRequest,
)

VALID_STATUSES = {"upcoming", "completed"}

router = APIRouter()


def _get_owned_plan(db: DbSession, plan_id: str, user: User) -> SavedPlan:
    plan = db.get(SavedPlan, plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="saved plan not found")
    return plan


def _find_share(db: DbSession, plan_id: str, email: str) -> PlanShare | None:
    stmt = select(PlanShare).where(
        PlanShare.saved_plan_id == plan_id, PlanShare.shared_with_email == email.lower()
    )
    return db.scalars(stmt).first()


@router.post("/saved-plans", response_model=SavedPlanOut)
def save_plan(
    request: SavePlanRequest, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = SavedPlan(
        user_id=user.id,
        email=user.email,
        city=request.city,
        arrival_date=request.arrival_date,
        departure_date=request.departure_date,
        itinerary_json=request.itinerary,
        plan_request_json=request.plan_request,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/saved-plans", response_model=list[SavedPlanOut])
def list_saved_plans(db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(SavedPlan).where(SavedPlan.user_id == user.id).order_by(SavedPlan.arrival_date)
    return list(db.scalars(stmt))


@router.get("/saved-plans/{plan_id}")
def get_saved_plan(plan_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    plan = db.get(SavedPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="saved plan not found")

    is_owner = plan.user_id == user.id
    share = None if is_owner else _find_share(db, plan_id, user.email)
    if not is_owner and share is None:
        raise HTTPException(status_code=404, detail="saved plan not found")

    return {
        "id": plan.id,
        "email": plan.email,
        "city": plan.city,
        "arrival_date": plan.arrival_date,
        "departure_date": plan.departure_date,
        "itinerary": plan.itinerary_json,
        "plan_request": plan.plan_request_json,
        "status": plan.status,
        "created_at": plan.created_at,
        "reminder_sent_at": plan.reminder_sent_at,
        "is_owner": is_owner,
        "can_edit": is_owner or (share is not None and share.role == "editor"),
        "edit_requested": share.edit_requested if share else False,
    }


@router.put("/saved-plans/{plan_id}", response_model=SavedPlanOut)
def update_saved_plan(
    plan_id: str,
    request: UpdatePlanRequest,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    plan = db.get(SavedPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="saved plan not found")

    is_owner = plan.user_id == user.id
    if not is_owner:
        share = _find_share(db, plan_id, user.email)
        if share is None or share.role != "editor":
            raise HTTPException(status_code=403, detail="you don't have edit access to this plan")

    plan.itinerary_json = request.itinerary
    db.commit()
    db.refresh(plan)
    return plan


@router.patch("/saved-plans/{plan_id}/status", response_model=SavedPlanOut)
def update_saved_plan_status(
    plan_id: str,
    request: UpdatePlanStatusRequest,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    plan = _get_owned_plan(db, plan_id, user)
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(VALID_STATUSES)}")
    plan.status = request.status
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/saved-plans/{plan_id}")
def delete_saved_plan(plan_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    plan = _get_owned_plan(db, plan_id, user)
    db.delete(plan)
    db.commit()
    return {"ok": True}


@router.post("/saved-plans/check-reminders")
def check_reminders(db: DbSession = Depends(get_db)):
    """Manual trigger for the same check the background loop runs periodically —
    useful for testing without waiting for the interval. Not user-scoped — it's
    an operational/admin action, not something an end user calls."""
    sent = send_due_reminders(db)
    return {"reminders_sent": sent}


# --- Sharing -----------------------------------------------------------------


@router.post("/saved-plans/{plan_id}/shares", response_model=ShareOut)
def share_plan(
    plan_id: str,
    request: ShareRequest,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    plan = _get_owned_plan(db, plan_id, user)
    email = request.email.lower()
    if email == user.email:
        raise HTTPException(status_code=400, detail="you already own this plan")

    existing = _find_share(db, plan.id, email)
    if existing:
        return existing

    share = PlanShare(saved_plan_id=plan.id, shared_with_email=email)
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


@router.get("/saved-plans/{plan_id}/shares", response_model=list[ShareOut])
def list_shares(plan_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    plan = _get_owned_plan(db, plan_id, user)
    stmt = select(PlanShare).where(PlanShare.saved_plan_id == plan.id).order_by(PlanShare.created_at)
    return list(db.scalars(stmt))


@router.delete("/saved-plans/{plan_id}/shares/{share_id}")
def revoke_share(
    plan_id: str, share_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = _get_owned_plan(db, plan_id, user)
    share = db.get(PlanShare, share_id)
    if share is None or share.saved_plan_id != plan.id:
        raise HTTPException(status_code=404, detail="share not found")
    db.delete(share)
    db.commit()
    return {"ok": True}


@router.post("/saved-plans/{plan_id}/shares/{share_id}/approve-edit", response_model=ShareOut)
def approve_edit(
    plan_id: str, share_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = _get_owned_plan(db, plan_id, user)
    share = db.get(PlanShare, share_id)
    if share is None or share.saved_plan_id != plan.id:
        raise HTTPException(status_code=404, detail="share not found")
    share.role = "editor"
    share.edit_requested = False
    db.commit()
    db.refresh(share)
    return share


@router.post("/saved-plans/{plan_id}/shares/{share_id}/deny-edit", response_model=ShareOut)
def deny_edit(
    plan_id: str, share_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = _get_owned_plan(db, plan_id, user)
    share = db.get(PlanShare, share_id)
    if share is None or share.saved_plan_id != plan.id:
        raise HTTPException(status_code=404, detail="share not found")
    share.edit_requested = False
    db.commit()
    db.refresh(share)
    return share


@router.post("/saved-plans/{plan_id}/request-edit")
def request_edit(plan_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    plan = db.get(SavedPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="saved plan not found")
    share = _find_share(db, plan_id, user.email)
    if share is None:
        raise HTTPException(status_code=404, detail="this plan isn't shared with you")
    if share.role == "editor":
        return {"ok": True, "already_editor": True}
    share.edit_requested = True
    db.commit()
    return {"ok": True, "already_editor": False}


@router.get("/shared-with-me", response_model=list[SharedPlanOut])
def shared_with_me(db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(PlanShare).where(PlanShare.shared_with_email == user.email)
    shares = list(db.scalars(stmt))
    out = []
    for share in shares:
        plan = db.get(SavedPlan, share.saved_plan_id)
        if plan is None:
            continue
        owner = db.get(User, plan.user_id)
        out.append(
            SharedPlanOut(
                id=plan.id,
                owner_email=owner.email if owner else "unknown",
                role=share.role,
                edit_requested=share.edit_requested,
                city=plan.city,
                arrival_date=plan.arrival_date,
                departure_date=plan.departure_date,
                created_at=plan.created_at,
            )
        )
    return out
