import io
import uuid
from datetime import date

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

MAX_MUSIC_BYTES = 15 * 1024 * 1024
ALLOWED_MUSIC_CONTENT_TYPES = {"audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav"}
MUSIC_EXT_BY_CONTENT_TYPE = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav", "audio/x-wav": "wav"}


def _memory_out(memory: Memory, stories: list[MemoryStory], photos: list[MemoryPhoto]) -> MemoryOut:
    return MemoryOut(
        id=memory.id,
        saved_plan_id=memory.saved_plan_id,
        summary=memory.summary_json,
        stories=[MemoryStoryOut.model_validate(s) for s in stories],
        photos=[MemoryPhotoOut.model_validate(p) for p in photos],
        has_music=memory.music_key is not None,
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


def delete_memory_cascade(db: DbSession, memory: Memory) -> None:
    """Deletes a memory and everything under it (photos + their storage
    objects, stories, music). Also used when a SavedPlan is deleted, since a
    memory can't outlive the trip it belongs to.

    Children are committed in their own transaction, separate from the
    parent delete: without a declared ORM relationship(), SQLAlchemy's
    unit-of-work doesn't know these rows depend on `memory` and may flush the
    parent DELETE first, which Postgres's real foreign-key enforcement then
    rejects (SQLite doesn't enforce FKs by default, so this passed in tests
    until FKs were turned on there too).
    """
    for photo in _photos_for(db, memory.id):
        storage.delete_photo(photo.storage_key)
        db.delete(photo)
    for story in _stories_for(db, memory.id):
        db.delete(story)
    if memory.music_key:
        storage.delete_photo(memory.music_key)
    db.commit()

    db.delete(memory)
    db.commit()


@router.post("/memories", response_model=MemoryOut)
def create_memory(
    request: CreateMemoryRequest, db: DbSession = Depends(get_db), user: User = Depends(require_user)
):
    plan = db.get(SavedPlan, request.saved_plan_id)
    if plan is None or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail="saved plan not found")
    trip_started = plan.arrival_date and date.fromisoformat(plan.arrival_date) <= date.today()
    if plan.status != "completed" and not trip_started:
        raise HTTPException(
            status_code=400, detail="wait until your trip starts before adding a memory"
        )

    # A trip has at most one memory -- re-requesting one (e.g. clicking "Add
    # memory" again after navigating away) continues the existing draft
    # instead of creating a duplicate.
    existing = db.scalars(
        select(Memory).where(Memory.saved_plan_id == plan.id, Memory.user_id == user.id)
    ).first()
    if existing is not None:
        return _memory_out(existing, _stories_for(db, existing.id), _photos_for(db, existing.id))

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
    delete_memory_cascade(db, memory)
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


# --- Background music for the trip-clip player -----------------------------


@router.post("/memories/{memory_id}/music", response_model=MemoryOut)
@limiter.limit("10/hour")
def upload_music(
    request: Request,
    memory_id: str,
    file: UploadFile,
    db: DbSession = Depends(get_db),
    user: User = Depends(require_user),
):
    memory = _get_owned_memory(db, memory_id, user)

    if file.content_type not in ALLOWED_MUSIC_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="only mp3, m4a, or wav audio is allowed")

    data = file.file.read(MAX_MUSIC_BYTES + 1)
    if len(data) > MAX_MUSIC_BYTES:
        raise HTTPException(status_code=400, detail="audio must be 15MB or smaller")

    if memory.music_key:
        storage.delete_photo(memory.music_key)

    ext = MUSIC_EXT_BY_CONTENT_TYPE[file.content_type]
    key = f"{user.id}/{memory.id}/music-{uuid.uuid4().hex}.{ext}"
    storage.save_photo(key, data, file.content_type)

    memory.music_key = key
    memory.music_backend = storage.backend_name()
    memory.music_content_type = file.content_type
    db.commit()
    db.refresh(memory)
    return _memory_out(memory, _stories_for(db, memory.id), _photos_for(db, memory.id))


@router.get("/memories/{memory_id}/music")
def get_music(memory_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    memory = _get_owned_memory(db, memory_id, user)
    if not memory.music_key:
        raise HTTPException(status_code=404, detail="no music attached to this memory")

    if memory.music_backend == "r2":
        url = storage.photo_url(memory.music_key)
        return RedirectResponse(url, status_code=307)
    return FileResponse(storage.local_photo_path(memory.music_key), media_type=memory.music_content_type)


@router.delete("/memories/{memory_id}/music", response_model=MemoryOut)
def delete_music(memory_id: str, db: DbSession = Depends(get_db), user: User = Depends(require_user)):
    memory = _get_owned_memory(db, memory_id, user)
    if memory.music_key:
        storage.delete_photo(memory.music_key)
    memory.music_key = None
    memory.music_backend = None
    memory.music_content_type = None
    db.commit()
    db.refresh(memory)
    return _memory_out(memory, _stories_for(db, memory.id), _photos_for(db, memory.id))
