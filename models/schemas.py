"""
models/schemas.py  –  Pydantic request/response models
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr


# ─── Auth ─────────────────────────────────────────────────────────────────────
class GoogleAuthRequest(BaseModel):
    code: str                  # OAuth authorization code from Flutter app

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"

class UserOut(BaseModel):
    id: str
    email: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    plan: str
    drive_folder_id: Optional[str]

    class Config:
        from_attributes = True


# ─── Memory ───────────────────────────────────────────────────────────────────
class MemoryOut(BaseModel):
    id: str
    title: str
    description: Optional[str]
    mime_type: Optional[str]
    original_size: Optional[int]
    stored_size: Optional[int]
    thumbnail_url: Optional[str]
    drive_file_id: Optional[str]
    taken_at: Optional[datetime]
    uploaded_at: datetime
    tags: List[str] = []
    ai_tags: List[str] = []
    location: Optional[str]
    album_id: Optional[str]
    savings_pct: Optional[float] = None

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_with_savings(cls, obj):
        data = cls.from_orm(obj)
        if obj.original_size and obj.stored_size and obj.original_size > 0:
            data.savings_pct = round((1 - obj.stored_size / obj.original_size) * 100, 1)
        return data


class MemoryListResponse(BaseModel):
    items: List[MemoryOut]
    total: int
    page: int
    page_size: int


class MemoryUpdateRequest(BaseModel):
    title: Optional[str]
    description: Optional[str]
    tags: Optional[List[str]]
    album_id: Optional[str]


# ─── Album ────────────────────────────────────────────────────────────────────
class AlbumCreate(BaseModel):
    name: str

class AlbumOut(BaseModel):
    id: str
    name: str
    cover_url: Optional[str]
    created_at: datetime
    memory_count: int = 0

    class Config:
        from_attributes = True


# ─── Family Sharing ───────────────────────────────────────────────────────────
class VaultCreate(BaseModel):
    name: str

class VaultOut(BaseModel):
    id: str
    name: str
    invite_code: str
    owner: UserOut
    member_count: int = 0

    class Config:
        from_attributes = True

class JoinVaultRequest(BaseModel):
    invite_code: str

class MemberOut(BaseModel):
    id: str
    user: UserOut
    role: str
    joined_at: datetime

    class Config:
        from_attributes = True


# ─── Stats ────────────────────────────────────────────────────────────────────
class StorageStats(BaseModel):
    total_memories: int
    total_original_bytes: int
    total_stored_bytes: int
    savings_pct: float
    drive_folder_id: Optional[str]


# ─── AI Tagging ───────────────────────────────────────────────────────────────
class AITagResponse(BaseModel):
    memory_id: str
    tags: List[str]
    description: Optional[str]


TokenResponse.model_rebuild()
