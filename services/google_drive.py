"""
services/google_drive.py  –  All Google Drive operations for MemVault
"""
import io
import json
import logging
from typing import Optional, Tuple

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

MEMVAULT_FOLDER_NAME = "MemVault"
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",   # Only files created by MemVault
    "openid",
    "email",
    "profile",
]


def _build_drive(token_json: str):
    """Build an authenticated Drive client from stored token JSON."""
    token = json.loads(token_json)
    creds = Credentials(
        token=token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=token.get("client_id"),
        client_secret=token.get("client_secret"),
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


async def ensure_memvault_folder(token_json: str) -> str:
    """
    Get or create the top-level MemVault folder in the user's Drive.
    Returns the folder ID.
    """
    service = _build_drive(token_json)
    query = (
        f"name='{MEMVAULT_FOLDER_NAME}' "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create folder
    meta = {
        "name": MEMVAULT_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=meta, fields="id").execute()
    logger.info(f"Created MemVault folder: {folder['id']}")
    return folder["id"]


async def upload_file_to_drive(
    token_json: str,
    folder_id: str,
    filename: str,
    content: bytes,
    mime_type: str,
) -> Tuple[str, str]:
    """
    Upload a file to the MemVault folder.
    Returns (file_id, web_view_link).
    """
    service = _build_drive(token_json)
    meta = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
    file = (
        service.files()
        .create(body=meta, media_body=media, fields="id, webViewLink, thumbnailLink")
        .execute()
    )
    return file["id"], file.get("webViewLink", "")


async def get_download_url(token_json: str, file_id: str) -> str:
    """Return a short-lived direct download URL for a Drive file."""
    service = _build_drive(token_json)
    file = service.files().get(file_id=file_id, fields="webContentLink").execute()
    return file.get("webContentLink", "")


async def download_file_from_drive(token_json: str, file_id: str) -> bytes:
    """Download the raw bytes of a Drive file."""
    service = _build_drive(token_json)
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


async def delete_file_from_drive(token_json: str, file_id: str) -> bool:
    """Permanently delete a file from Drive."""
    try:
        service = _build_drive(token_json)
        service.files().delete(fileId=file_id).execute()
        return True
    except HttpError as e:
        logger.error(f"Drive delete error: {e}")
        return False


async def list_drive_files(token_json: str, folder_id: str) -> list:
    """List all files in the MemVault folder (for sync/import)."""
    service = _build_drive(token_json)
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, size, createdTime, thumbnailLink)",
        pageSize=100,
    ).execute()
    return results.get("files", [])


async def get_drive_storage_quota(token_json: str) -> dict:
    """Return Drive storage quota info."""
    service = _build_drive(token_json)
    about = service.about().get(fields="storageQuota").execute()
    quota = about.get("storageQuota", {})
    return {
        "limit": int(quota.get("limit", 0)),
        "usage": int(quota.get("usage", 0)),
        "usage_in_drive": int(quota.get("usageInDrive", 0)),
    }
