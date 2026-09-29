"""Logo upload for the BUMDES Profil (kop surat) settings.

Reuses the existing generic Google Drive adapter (adapters/external/
gdrive_adapter.py, already used for transaction proof uploads) instead of
local disk: this app deploys to a target with an ephemeral filesystem, so
anything saved to local disk is lost on the next deploy/restart.

Uses its own optional folder (GDRIVE_FOLDER_ID_LOGO), falling back to the
shared GDRIVE_FOLDER_ID if that's not set separately.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, status

from adapters.external.gdrive_adapter import is_configured, set_gdrive_file_public, upload_file_to_gdrive

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/svg+xml"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class LogoUploadNotConfigured(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload logo belum dikonfigurasi (GDRIVE_FOLDER_ID / GDRIVE_SERVICE_ACCOUNT_JSON belum diisi).",
        )


async def upload_org_logo(file_bytes: bytes, file_name: str, content_type: str) -> str:
    """Uploads to Drive, makes it publicly viewable, and returns a direct
    embeddable image URL (not the Drive web viewer link, which can't be
    used in <img>/WeasyPrint/openpyxl/python-docx)."""
    if not is_configured():
        raise LogoUploadNotConfigured()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format logo harus JPEG, PNG, WebP, atau SVG",
        )
    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ukuran logo maksimal 2 MB")

    folder_id = os.getenv("GDRIVE_FOLDER_ID_LOGO")
    result = await upload_file_to_gdrive(file_bytes, file_name, content_type, folder_id=folder_id)
    file_id = result["id"]
    await set_gdrive_file_public(file_id)

    # NOT drive.google.com/uc?export=view&id=... -- Google now frequently
    # serves that URL as an HTML "can't scan this file for viruses" /
    # download-confirmation interstitial instead of the actual image bytes,
    # both for <img> hotlinking in the browser and for our own server-side
    # fetch_logo_bytes() (used to embed the logo in Word/Excel). The
    # thumbnail endpoint reliably returns real image bytes for a publicly
    # shared file.
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w512"
