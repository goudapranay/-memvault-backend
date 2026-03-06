"""
utils/auth_helpers.py  –  JWT creation, verification, Google OAuth flow
"""
import os
import json
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests
from google_auth_oauthlib.flow import Flow
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models.database import User, settings
from services.google_drive import ensure_memvault_folder

logger = logging.getLogger(__name__)


def create_access_token(user_id: str, expires_minutes: int = None) -> str:
    exp = datetime.utcnow() + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": user_id, "exp": exp},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload.get("sub")
    except JWTError:
        return None


def get_google_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uris": [settings.google_redirect_uri],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/drive.file",
        ],
        redirect_uri=settings.google_redirect_uri,
    )
    return flow


async def get_or_create_user_from_google(
    db: AsyncSession,
    code: str,
) -> Optional[User]:
    """
    Exchange authorization code for tokens, get user info, upsert User row.
    """
    try:
        flow = get_google_flow()
        flow.fetch_token(code=code)
	os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        creds = flow.credentials

        # Get user info from Google
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {creds.token}"},
            )
            info = resp.json()

        google_id = info["sub"]
        email = info.get("email", "")
        name = info.get("name", "")
        avatar = info.get("picture", "")

        # Store token as JSON for Drive API
        token_data = json.dumps({
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        })

        result = await db.execute(select(User).where(User.id == google_id))
        user = result.scalar_one_or_none()

        if user:
            user.google_token = token_data
            user.display_name = name
            user.avatar_url = avatar
        else:
            user = User(
                id=google_id,
                email=email,
                display_name=name,
                avatar_url=avatar,
                google_token=token_data,
            )
            db.add(user)

        await db.flush()

        # Ensure Drive folder exists
        if not user.drive_folder_id:
            try:
                folder_id = await ensure_memvault_folder(token_data)
                user.drive_folder_id = folder_id
            except Exception as e:
                logger.warning(f"Could not create Drive folder: {e}")

        await db.commit()
        await db.refresh(user)
        return user

    except Exception as e:
        logger.error(f"Google auth error: {e}")
        await db.rollback()
        return None


async def get_current_user(token: str, db: AsyncSession) -> Optional[User]:
    user_id = decode_token(token)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
