"""
routers/auth.py  –  Google OAuth endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_db
from models.schemas import GoogleAuthRequest, TokenResponse, UserOut
from utils.auth_helpers import (
    get_google_flow,
    get_or_create_user_from_google,
    create_access_token,
    get_current_user,
    decode_token,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer()


@router.get("/google/login")
async def google_login():
    """Redirect user to Google OAuth consent screen."""
    flow = get_google_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return {"auth_url": auth_url}


@router.post("/google/exchange", response_model=TokenResponse)
async def google_exchange(body: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    """Exchange authorization code for MemVault JWT."""
    user = await get_or_create_user_from_google(db, body.code)
    if not user:
        raise HTTPException(status_code=400, detail="Google authentication failed")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserOut.from_orm(user),
    )


@router.get("/me", response_model=UserOut)
async def get_me(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    user = await get_current_user(creds.credentials, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return UserOut.from_orm(user)


@router.post("/logout")
async def logout():
    """Client should discard JWT. No server-side session to invalidate."""
    return {"message": "Logged out successfully"}
