"""
routers/memories.py  –  CRUD + upload for memories
"""
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from models.database import get_db, Memory, User
from models.schemas import MemoryOut, MemoryListResponse, MemoryUpdateRequest, StorageStats, AITagResponse
from utils.auth_helpers import get_current_user
from services.compression import (
    compress_image, compress_video, generate_thumbnail,
    extract_video_thumbnail, compute_perceptual_hash,
    compute_sha256, SUPPORTED_IMAGE_TYPES, SUPPORTED_VIDEO_TYPES,
)
from services.google_drive import upload_file_to_drive, delete_file_from_drive, get_drive_storage_quota
from services.ai_tagging import generate_ai_tags
from models.database import settings

router = APIRouter(prefix="/memories", tags=["memories"])
bearer = HTTPBearer()

logger = logging.getLogger(__name__)


async def _require_user(creds: HTTPAuthorizationCredentials, db: AsyncSession) -> User:
    user = await get_current_user(creds.credentials, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


@router.post("/upload", response_model=MemoryOut)
async def upload_memory(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    album_id: Optional[str] = Form(None),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)

    raw = await file.read()
    original_size = len(raw)
    mime = file.content_type or "application/octet-stream"
    filename = file.filename or f"memory_{uuid.uuid4()}"

    if original_size > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")

    # ── Compress ──────────────────────────────────────────────────────────────
    if mime in SUPPORTED_IMAGE_TYPES:
        compressed, new_mime = compress_image(raw, quality=settings.compression_quality)
        thumb_bytes = generate_thumbnail(raw)
    elif mime in SUPPORTED_VIDEO_TYPES:
        compressed, new_mime = compress_video(raw, original_mime=mime)
        thumb_bytes = extract_video_thumbnail(raw, mime) or b""
    else:
        compressed, new_mime = raw, mime
        thumb_bytes = b""

    stored_size = len(compressed)

    # ── Dedup check ───────────────────────────────────────────────────────────
    phash = compute_perceptual_hash(raw) if mime in SUPPORTED_IMAGE_TYPES else None
    is_dup = False
    if phash:
        existing = await db.execute(select(Memory).where(Memory.owner_id == user.id).limit(200))
        for m in existing.scalars():
            # We'd compare phash here; simplified check
            pass  # Full implementation would store phash column

    # ── Upload to Drive ───────────────────────────────────────────────────────
    if not user.drive_folder_id or not user.google_token:
        raise HTTPException(400, "Google Drive not connected. Please re-authenticate.")

    safe_name = f"{uuid.uuid4()}{_ext(new_mime)}"
    try:
        drive_file_id, _ = await upload_file_to_drive(
            user.google_token, user.drive_folder_id, safe_name, compressed, new_mime
        )
        thumb_id = ""
        thumb_url = ""
        if thumb_bytes:
            thumb_id, _ = await upload_file_to_drive(
                user.google_token, user.drive_folder_id,
                f"thumb_{safe_name}.jpg", thumb_bytes, "image/jpeg"
            )
            thumb_url = f"https://drive.google.com/thumbnail?id={thumb_id}&sz=w400"
    except Exception as e:
        logger.error(f"Drive upload error: {e}")
        raise HTTPException(502, "Failed to upload to Google Drive")

    # ── AI Tagging ────────────────────────────────────────────────────────────
    tag_input = thumb_bytes if thumb_bytes else (compressed if mime in SUPPORTED_IMAGE_TYPES else b"")
    ai_tags, ai_desc = [], None
    if tag_input and settings.openai_api_key:
        try:
            ai_tags, ai_desc = await generate_ai_tags(
                tag_input, "image/jpeg", settings.openai_api_key, filename
            )
        except Exception as e:
            logger.warning(f"AI tagging skipped: {e}")

    # ── Persist ───────────────────────────────────────────────────────────────
    memory = Memory(
        id=str(uuid.uuid4()),
        owner_id=user.id,
        title=title or filename.rsplit(".", 1)[0],
        description=ai_desc,
        mime_type=new_mime,
        original_size=original_size,
        stored_size=stored_size,
        drive_file_id=drive_file_id,
        drive_thumb_id=thumb_id,
        thumbnail_url=thumb_url,
        uploaded_at=datetime.utcnow(),
        tags=[],
        ai_tags=ai_tags,
        album_id=album_id,
        shared_with=[],
        is_duplicate=is_dup,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return MemoryOut.from_orm_with_savings(memory)


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None),
    album_id: Optional[str] = Query(None),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    offset = (page - 1) * page_size

    query = select(Memory).where(Memory.owner_id == user.id, Memory.is_duplicate == False)
    if album_id:
        query = query.where(Memory.album_id == album_id)
    if q:
        query = query.where(
            or_(
                Memory.title.ilike(f"%{q}%"),
                Memory.description.ilike(f"%{q}%"),
            )
        )

    total_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_q)).scalar()

    result = await db.execute(query.order_by(Memory.uploaded_at.desc()).offset(offset).limit(page_size))
    items = [MemoryOut.from_orm_with_savings(m) for m in result.scalars()]

    return MemoryListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.owner_id == user.id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")
    return MemoryOut.from_orm_with_savings(memory)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.owner_id == user.id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")

    if body.title is not None: memory.title = body.title
    if body.description is not None: memory.description = body.description
    if body.tags is not None: memory.tags = body.tags
    if body.album_id is not None: memory.album_id = body.album_id

    await db.commit()
    await db.refresh(memory)
    return MemoryOut.from_orm_with_savings(memory)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.owner_id == user.id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")

    # Delete from Drive
    if memory.drive_file_id and user.google_token:
        await delete_file_from_drive(user.google_token, memory.drive_file_id)
    if memory.drive_thumb_id and user.google_token:
        await delete_file_from_drive(user.google_token, memory.drive_thumb_id)

    await db.delete(memory)
    await db.commit()
    return {"message": "Deleted"}


@router.get("/stats/storage", response_model=StorageStats)
async def storage_stats(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(
        select(
            func.count(Memory.id),
            func.sum(Memory.original_size),
            func.sum(Memory.stored_size),
        ).where(Memory.owner_id == user.id)
    )
    count, orig_total, stored_total = result.one()
    orig_total = orig_total or 0
    stored_total = stored_total or 0
    savings = round((1 - stored_total / orig_total) * 100, 1) if orig_total else 0.0

    return StorageStats(
        total_memories=count or 0,
        total_original_bytes=orig_total,
        total_stored_bytes=stored_total,
        savings_pct=savings,
        drive_folder_id=user.drive_folder_id,
    )


@router.post("/{memory_id}/retag", response_model=AITagResponse)
async def retag_memory(
    memory_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    """Force re-run AI tagging on a memory."""
    user = await _require_user(creds, db)
    result = await db.execute(select(Memory).where(Memory.id == memory_id, Memory.owner_id == user.id))
    memory = result.scalar_one_or_none()
    if not memory:
        raise HTTPException(404, "Memory not found")

    if not settings.openai_api_key:
        raise HTTPException(503, "AI tagging not configured")

    from services.google_drive import download_file_from_drive
    img_bytes = await download_file_from_drive(user.google_token, memory.drive_thumb_id or memory.drive_file_id)
    tags, desc = await generate_ai_tags(img_bytes, "image/jpeg", settings.openai_api_key, memory.title)

    memory.ai_tags = tags
    if desc: memory.description = desc
    await db.commit()

    return AITagResponse(memory_id=memory_id, tags=tags, description=desc)


def _ext(mime: str) -> str:
    return {
        "image/avif": ".avif", "image/webp": ".webp",
        "image/jpeg": ".jpg", "image/png": ".png",
        "video/mp4": ".mp4",
    }.get(mime, ".bin")
