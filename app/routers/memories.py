import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app import storage
from app.auth_deps import require_user
from app.db import get_db
from app.limiter import limiter
from app.memory_summary import compute_summary
from app.orm import Memory, MemoryPhoto, MemoryStory, SavedPlan, User
from app.schemas import (
    CreateMemoryRequest,
    CreateStoryRequest,
    MemoryOut,
    MemoryPhotoOut,
    MemoryStoryOut,
    UpdateStoryRequest,
)

router = APIRouter()

MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
EXT_BY_CONTENT_TYPE = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _memory_out(memory: Memory, stories: list[MemoryStory], photos: list[MemoryPhoto]) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        saved_plan_id=memory.saved_plan_id,
        summary=memory.summary_json,
        stories=[MemoryStoryOut.model_validate(s) for s in stories],
        photos=[MemoryPhotoOut.model_validate(p) for p in photos],
        created_at=memory.created_at,
    )


def _get_owned_memory(db: DbSession, memory_id: str, user: User) -> Memory:
    memory = db.get(Memory, memory_id)
    if memory is None or memory.user_id != user.id:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory


def _stories_for(db: DbSession, memory_id: str) -> list[MemoryStory]:
    stmt = select(MemoryStory).where(MemoryStory.memory_id == memory_id).order_by(MemoryStory.created_at)
    return list(db.scalars(stmt))


def _photos_for(db: DbSession, memory_id: str) -> list[MemoryPhoto]:
    stmt = select(MemoryPhoto).where(MemoryPhoto.memory_id == memory_id).order_by(MemoryPhoto.created_at)
    return list(db.scalars(stmt))


@router.post("/memories", response_model=MemoryOut)
def create_memory(
    request: CreateMemoryRequest, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = db.get(SavedPlan, request.saved_plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="saved plan not found")
    if plan.status != "completed":
        raise HTTPException(status_code=400, detail="mark this trip as completed before adding a memory")

    memory = Memory(
        saved_plan_id=plan.id,
        user_id=user.id,
        summary_json=compute_summary(plan.itinerary_json),
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return _memory_out(memory, [], [])


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    stmt = select(Memory).where(Memory.user_id == user.id).order_by(Memory.created_at.desc())
    memories = list(db.scalars(stmt))
    return [_memory_out(m, _stories_for(db, m.id), _photos_for(db, m.id)) for m in memories]


@router.get("/memories/{memory_id}", response_model=MemoryOut)
def get_memory(memory_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    memory = _get_owned_memory(db, memory_id, user)
    return _memory_out(memory, _stories_for(db, memory.id), _photos_for(db, memory.id))


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    memory = _get_owned_memory(db, memory_id, user)
    for photo in _photos_for(db, memory.id):
        storage.delete_photo(photo.storage_key)
        db.delete(photo)
    for story in _stories_for(db, memory.id):
        db.delete(story)
    db.delete(memory)
    db.commit()
    return {"ok": True}


# --- Stories -------------------------------------------------------------


@router.post("/memories/{memory_id}/stories", response_model=MemoryStoryOut)
def add_story(
    memory_id: str,
    request: CreateStoryRequest,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    memory = _get_owned_memory(db, memory_id, user)
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="story text can't be empty")
    story = MemoryStory(memory_id=memory.id, text=request.text)
    db.add(story)
    db.commit()
    db.refresh(story)
    return story


@router.put("/memories/{memory_id}/stories/{story_id}", response_model=MemoryStoryOut)
def update_story(
    memory_id: str,
    story_id: str,
    request: UpdateStoryRequest,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    memory = _get_owned_memory(db, memory_id, user)
    story = db.get(MemoryStory, story_id)
    if story is None or story.memory_id != memory.id:
        raise HTTPException(status_code=404, detail="story not found")
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="story text can't be empty")
    story.text = request.text
    db.commit()
    db.refresh(story)
    return story


@router.delete("/memories/{memory_id}/stories/{story_id}")
def delete_story(
    memory_id: str, story_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    memory = _get_owned_memory(db, memory_id, user)
    story = db.get(MemoryStory, story_id)
    if story is None or story.memory_id != memory.id:
        raise HTTPException(status_code=404, detail="story not found")
    db.delete(story)
    db.commit()
    return {"ok": True}


@router.post("/memories/{memory_id}/photos", response_model=MemoryPhotoOut)
@limiter.limit("20/hour")
def upload_photo(
    request: Request,
    memory_id: str,
    file: UploadFile,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    memory = _get_owned_memory(db, memory_id, user)

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="only jpeg, png, or webp images are allowed")

    data = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="photo must be 8MB or smaller")

    # Never trust the declared content-type alone — confirm the bytes really
    # are a decodable image of that shape before storing them anywhere.
    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception:
        raise HTTPException(status_code=400, detail="file isn't a valid image")

    ext = EXT_BY_CONTENT_TYPE[file.content_type]
    key = f"{user.id}/{memory.id}/{uuid.uuid4().hex}.{ext}"
    storage.save_photo(key, data, file.content_type)

    photo = MemoryPhoto(
        memory_id=memory.id,
        storage_key=key,
        storage_backend=storage.backend_name(),
        original_filename=file.filename or "photo",
        content_type=file.content_type,
        size_bytes=len(data),
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    return photo


@router.get("/memories/{memory_id}/photos/{photo_id}")
def get_photo(
    memory_id: str, photo_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    memory = _get_owned_memory(db, memory_id, user)
    photo = db.get(MemoryPhoto, photo_id)
    if photo is None or photo.memory_id != memory.id:
        raise HTTPException(status_code=404, detail="photo not found")

    if photo.storage_backend == "r2":
        url = storage.photo_url(photo.storage_key)
        return RedirectResponse(url, status_code=307)
    return FileResponse(storage.local_photo_path(photo.storage_key), media_type=photo.content_type)


@router.delete("/memories/{memory_id}/photos/{photo_id}")
def delete_photo(
    memory_id: str, photo_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    memory = _get_owned_memory(db, memory_id, user)
    photo = db.get(MemoryPhoto, photo_id)
    if photo is None or photo.memory_id != memory.id:
        raise HTTPException(status_code=404, detail="photo not found")

    storage.delete_photo(photo.storage_key)
    db.delete(photo)
    db.commit()
    return {"ok": True}
