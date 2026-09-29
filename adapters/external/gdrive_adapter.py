"""Google Drive adapter.

Two auth modes, tried in this order:

1. OAuth 2.0 as a real Google account (GOOGLE_OAUTH_CLIENT_ID/SECRET +
   GOOGLE_OAUTH_REFRESH_TOKEN) -- uploads count against THAT account's own
   Drive quota, so this works for plain "My Drive" folders. Needed because
   a service account (mode 2) has 0 bytes of its own quota and can only
   write into a Shared Drive -- which requires Google Workspace, not
   available on a personal Gmail account.
2. Service account (GDRIVE_SERVICE_ACCOUNT_JSON / GOOGLE_APPLICATION_CREDENTIALS)
   -- kept for setups that DO have a Workspace Shared Drive configured
   (e.g. a different tenant than the one that hit the quota issue).
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any, Optional

import httplib2
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"

# Without an explicit socket timeout, a stalled request to the Drive API
# (network blip, slow upload) hangs indefinitely -- the request then sits
# until the platform's own gateway kills it with a bare 504, instead of
# failing fast with a clear error we can log/return to the client.
_REQUEST_TIMEOUT_SECONDS = 25


def _oauth_env() -> tuple[Optional[str], Optional[str], Optional[str]]:
    return (
        os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN"),
    )


def is_oauth_configured() -> bool:
    client_id, client_secret, refresh_token = _oauth_env()
    return bool(client_id and client_secret and refresh_token)


def is_configured() -> bool:
    folder = os.getenv("GDRIVE_FOLDER_ID")
    if not folder:
        return False
    if is_oauth_configured():
        return True
    raw = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    return bool(raw or (path and os.path.isfile(path)))


def oauth_flow(redirect_uri: str) -> Flow:
    """Builds the Flow used by /admin/gdrive/connect + /oauth-callback."""
    client_id, client_secret, _ = _oauth_env()
    if not client_id or not client_secret:
        raise RuntimeError("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first")
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": _TOKEN_URI,
        }
    }
    flow = Flow.from_client_config(client_config, scopes=_SCOPES, redirect_uri=redirect_uri)
    # /connect and /oauth-callback are two separate HTTP requests (no shared
    # in-memory state between them, and possibly different worker processes)
    # -- PKCE's code_verifier generated in /connect would never make it to
    # /oauth-callback's fresh Flow instance, causing "invalid_grant: Missing
    # code verifier". Our client is a confidential "Web" app (has a client
    # secret), so PKCE isn't required the way it is for public/native apps.
    flow.autogenerate_code_verifier = False
    return flow


def _drive_service() -> Any:
    client_id, client_secret, refresh_token = _oauth_env()
    if client_id and client_secret and refresh_token:
        creds = OAuthCredentials(
            None,
            refresh_token=refresh_token,
            token_uri=_TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )
    else:
        raw = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
        path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if raw:
            info = json.loads(raw)
            creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        elif path and os.path.isfile(path):
            creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
        else:
            raise RuntimeError(
                "Set GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN, or "
                "GDRIVE_SERVICE_ACCOUNT_JSON/GOOGLE_APPLICATION_CREDENTIALS"
            )
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=_REQUEST_TIMEOUT_SECONDS))
    return build("drive", "v3", http=http, cache_discovery=False)


def _upload_sync(
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
    folder_id: Optional[str],
) -> dict[str, str]:
    target = folder_id or os.getenv("GDRIVE_FOLDER_ID")
    if not target:
        raise RuntimeError("folder_id or GDRIVE_FOLDER_ID is required")

    service = _drive_service()
    metadata = {"name": file_name, "parents": [target]}
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype=mime_type or "application/octet-stream",
        resumable=False,
    )
    created = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    file_id = created.get("id")
    if not file_id:
        raise RuntimeError("Drive upload returned no file id")
    return {
        "id": str(file_id),
        "name": str(created.get("name") or file_name),
        "webViewLink": str(created.get("webViewLink") or ""),
    }


def _delete_sync(file_id: str) -> None:
    service = _drive_service()
    try:
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    except HttpError as exc:
        if getattr(exc, "status_code", None) == 404 or "404" in str(exc):
            return
        raise


def _exists_sync(file_id: str) -> bool:
    service = _drive_service()
    try:
        service.files().get(fileId=file_id, fields="id", supportsAllDrives=True).execute()
        return True
    except HttpError:
        return False


def _set_public_sync(file_id: str) -> None:
    service = _drive_service()
    service.permissions().create(
        fileId=file_id, body={"type": "anyone", "role": "reader"}, supportsAllDrives=True
    ).execute()


async def upload_file_to_gdrive(
    file_bytes: bytes,
    file_name: str,
    mime_type: str,
    folder_id: Optional[str] = None,
) -> dict[str, str]:
    """Upload bytes to the shared folder. Returns {id, name, webViewLink}."""
    return await asyncio.to_thread(_upload_sync, file_bytes, file_name, mime_type, folder_id)


async def delete_file_from_gdrive(file_id: str) -> None:
    await asyncio.to_thread(_delete_sync, file_id)


async def gdrive_file_exists(file_id: str) -> bool:
    return await asyncio.to_thread(_exists_sync, file_id)


async def set_gdrive_file_public(file_id: str) -> None:
    """Grants anyone-with-the-link read access. Needed for files that must
    be embeddable (e.g. <img src>), since the default sharing for a service
    account upload is private."""
    await asyncio.to_thread(_set_public_sync, file_id)
