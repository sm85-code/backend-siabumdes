"""Foto profil pengguna (menu Profil Saya).

Sama persis dengan org_logo_upload.py: reuse adapter Google Drive generik
(sudah dipakai untuk bukti transaksi dan logo BUMDES) karena filesystem
deploy ini ephemeral. Pakai folder sendiri (GDRIVE_FOLDER_ID_PHOTO), fallback
ke GDRIVE_FOLDER_ID kalau tidak diisi terpisah.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, status

from adapters.external.gdrive_adapter import is_configured, set_gdrive_file_public, upload_file_to_gdrive

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


class PhotoUploadNotConfigured(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Upload foto profil belum dikonfigurasi (GDRIVE_FOLDER_ID / GDRIVE_SERVICE_ACCOUNT_JSON belum diisi).",
        )


async def upload_user_photo(file_bytes: bytes, file_name: str, content_type: str) -> str:
    """Uploads to Drive, makes it publicly viewable, and returns a direct
    embeddable image URL (thumbnail endpoint, not the Drive web viewer link)."""
    if not is_configured():
        raise PhotoUploadNotConfigured()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format foto harus JPEG, PNG, atau WebP",
        )
    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ukuran foto maksimal 2 MB")

    folder_id = os.getenv("GDRIVE_FOLDER_ID_PHOTO")
    result = await upload_file_to_gdrive(file_bytes, file_name, content_type, folder_id=folder_id)
    file_id = result["id"]
    await set_gdrive_file_public(file_id)
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w512"
