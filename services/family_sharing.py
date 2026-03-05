"""
services/family_sharing.py  –  Family vault management
"""
import uuid
import secrets
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from models.database import FamilyVault, FamilyMember, User, Memory

logger = logging.getLogger(__name__)


async def create_family_vault(db: AsyncSession, owner_id: str, name: str) -> FamilyVault:
    vault = FamilyVault(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        name=name,
        invite_code=secrets.token_urlsafe(8).upper(),
    )
    db.add(vault)
    # Add owner as first member
    member = FamilyMember(
        id=str(uuid.uuid4()),
        vault_id=vault.id,
        user_id=owner_id,
        role="owner",
    )
    db.add(member)
    await db.commit()
    await db.refresh(vault)
    return vault


async def join_vault_by_code(db: AsyncSession, user_id: str, invite_code: str) -> Optional[FamilyVault]:
    result = await db.execute(
        select(FamilyVault).where(FamilyVault.invite_code == invite_code.upper())
    )
    vault = result.scalar_one_or_none()
    if not vault:
        return None

    # Check if already a member
    existing = await db.execute(
        select(FamilyMember).where(
            FamilyMember.vault_id == vault.id,
            FamilyMember.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        return vault  # Already a member

    member = FamilyMember(
        id=str(uuid.uuid4()),
        vault_id=vault.id,
        user_id=user_id,
        role="viewer",
    )
    db.add(member)
    await db.commit()
    return vault


async def get_user_vaults(db: AsyncSession, user_id: str) -> List[FamilyVault]:
    result = await db.execute(
        select(FamilyVault)
        .join(FamilyMember)
        .where(FamilyMember.user_id == user_id)
        .options(selectinload(FamilyVault.members).selectinload(FamilyMember.user))
        .options(selectinload(FamilyVault.owner))
    )
    return result.scalars().all()


async def share_memory_with_vault(
    db: AsyncSession, memory_id: str, vault_id: str, requester_id: str
) -> bool:
    """Add vault_id to memory.shared_with list."""
    from models.database import Memory
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id, Memory.owner_id == requester_id)
    )
    memory = result.scalar_one_or_none()
    if not memory:
        return False
    shared = list(memory.shared_with or [])
    if vault_id not in shared:
        shared.append(vault_id)
        memory.shared_with = shared
        await db.commit()
    return True


async def get_vault_memories(
    db: AsyncSession, vault_id: str, user_id: str, page: int = 1, page_size: int = 20
) -> tuple:
    """Get memories shared with a vault (user must be a member)."""
    # Verify membership
    member_check = await db.execute(
        select(FamilyMember).where(
            FamilyMember.vault_id == vault_id,
            FamilyMember.user_id == user_id,
        )
    )
    if not member_check.scalar_one_or_none():
        return [], 0

    offset = (page - 1) * page_size
    # Memories where vault_id is in shared_with (SQLite JSON contains)
    result = await db.execute(
        select(Memory)
        .where(Memory.shared_with.contains([vault_id]))
        .offset(offset)
        .limit(page_size)
    )
    items = result.scalars().all()

    count_result = await db.execute(
        select(func.count()).select_from(Memory)
        .where(Memory.shared_with.contains([vault_id]))
    )
    total = count_result.scalar()
    return items, total
