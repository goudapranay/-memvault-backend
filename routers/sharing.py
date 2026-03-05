"""
routers/sharing.py  –  Family vault + album endpoints
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from models.database import get_db, User, Album, Memory, FamilyVault, FamilyMember
from models.schemas import (
    VaultCreate, VaultOut, JoinVaultRequest, MemberOut,
    AlbumCreate, AlbumOut, MemoryListResponse, MemoryOut, UserOut,
)
from utils.auth_helpers import get_current_user
from services.family_sharing import (
    create_family_vault, join_vault_by_code,
    get_user_vaults, share_memory_with_vault, get_vault_memories,
)

router = APIRouter(tags=["sharing & albums"])
bearer = HTTPBearer()


async def _require_user(creds, db):
    user = await get_current_user(creds.credentials, db)
    if not user:
        raise HTTPException(401, "Unauthorized")
    return user


# ─── Albums ───────────────────────────────────────────────────────────────────

@router.post("/albums", response_model=AlbumOut)
async def create_album(
    body: AlbumCreate,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    album = Album(id=str(uuid.uuid4()), owner_id=user.id, name=body.name)
    db.add(album)
    await db.commit()
    await db.refresh(album)
    return AlbumOut(id=album.id, name=album.name, cover_url=album.cover_url,
                    created_at=album.created_at, memory_count=0)


@router.get("/albums", response_model=List[AlbumOut])
async def list_albums(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(select(Album).where(Album.owner_id == user.id))
    albums = result.scalars().all()

    out = []
    for a in albums:
        count_res = await db.execute(
            select(func.count(Memory.id)).where(Memory.album_id == a.id)
        )
        count = count_res.scalar() or 0
        # Use first memory thumbnail as cover
        first = await db.execute(
            select(Memory.thumbnail_url).where(Memory.album_id == a.id).limit(1)
        )
        cover = first.scalar()
        out.append(AlbumOut(id=a.id, name=a.name, cover_url=cover,
                            created_at=a.created_at, memory_count=count))
    return out


@router.delete("/albums/{album_id}")
async def delete_album(
    album_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(select(Album).where(Album.id == album_id, Album.owner_id == user.id))
    album = result.scalar_one_or_none()
    if not album:
        raise HTTPException(404, "Album not found")
    await db.delete(album)
    await db.commit()
    return {"message": "Album deleted"}


# ─── Family Vaults ────────────────────────────────────────────────────────────

@router.post("/vaults", response_model=VaultOut)
async def create_vault(
    body: VaultCreate,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    vault = await create_family_vault(db, user.id, body.name)
    return VaultOut(
        id=vault.id, name=vault.name, invite_code=vault.invite_code,
        owner=UserOut.from_orm(user), member_count=1,
    )


@router.get("/vaults", response_model=List[VaultOut])
async def list_vaults(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    vaults = await get_user_vaults(db, user.id)
    out = []
    for v in vaults:
        count_res = await db.execute(
            select(func.count(FamilyMember.id)).where(FamilyMember.vault_id == v.id)
        )
        out.append(VaultOut(
            id=v.id, name=v.name, invite_code=v.invite_code,
            owner=UserOut.from_orm(v.owner), member_count=count_res.scalar() or 0,
        ))
    return out


@router.post("/vaults/join", response_model=VaultOut)
async def join_vault(
    body: JoinVaultRequest,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    vault = await join_vault_by_code(db, user.id, body.invite_code)
    if not vault:
        raise HTTPException(404, "Invalid invite code")
    owner_res = await db.execute(select(User).where(User.id == vault.owner_id))
    owner = owner_res.scalar_one()
    return VaultOut(id=vault.id, name=vault.name, invite_code=vault.invite_code,
                    owner=UserOut.from_orm(owner), member_count=0)


@router.post("/vaults/{vault_id}/share/{memory_id}")
async def share_memory(
    vault_id: str,
    memory_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    ok = await share_memory_with_vault(db, memory_id, vault_id, user.id)
    if not ok:
        raise HTTPException(404, "Memory not found or not owned by you")
    return {"message": "Shared"}


@router.get("/vaults/{vault_id}/memories", response_model=MemoryListResponse)
async def vault_memories(
    vault_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    items, total = await get_vault_memories(db, vault_id, user.id, page, page_size)
    return MemoryListResponse(
        items=[MemoryOut.from_orm_with_savings(m) for m in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/vaults/{vault_id}/members", response_model=List[MemberOut])
async def vault_members(
    vault_id: str,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await _require_user(creds, db)
    result = await db.execute(
        select(FamilyMember).where(FamilyMember.vault_id == vault_id)
    )
    members = result.scalars().all()
    out = []
    for m in members:
        user_res = await db.execute(select(User).where(User.id == m.user_id))
        u = user_res.scalar_one()
        out.append(MemberOut(id=m.id, user=UserOut.from_orm(u), role=m.role, joined_at=m.joined_at))
    return out
