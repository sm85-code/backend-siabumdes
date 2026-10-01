"""Session + profile + user-admin routes matching frontend-siabumdes."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.adapters.api.deps import get_current_user, require_roles
from modules.siabumdes.adapters.external.user_photo_upload import upload_user_photo
from modules.siabumdes.identity.application.services import (
    authenticate,
    create_user,
    get_system_control,
    list_users,
    record_audit,
    set_blocked_periods,
    user_to_out,
)
from modules.siabumdes.identity.infrastructure.models import User
from shared.config import PUBLIC_ROLES, public_role
from shared.database import get_db
from shared.security import (
    clear_auth_cookie,
    create_access_token,
    hash_password,
    set_auth_cookie,
    verify_password,
)

router = APIRouter(prefix="/api", tags=["auth"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class LoginRequest(BaseModel):
    username: str
    password: str = Field(..., max_length=72)


class RegisterRequest(BaseModel):
    username: str
    email: str
    name: str
    password: str = Field(..., max_length=72)
    role: str
    unit_usaha_id: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=72)
    new_password: str = Field(..., max_length=72)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(..., max_length=72)


class ProfileUpdateRequest(BaseModel):
    name: str
    email: str = ""
    username: str = ""


class AdminUserUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    username: Optional[str] = None
    role: Optional[str] = None
    unit_usaha_id: Optional[str] = None


class BlockedPeriodsRequest(BaseModel):
    blocked_periods: list[str] = Field(default_factory=list)


def _token_for(user: User) -> str:
    return create_access_token(
        user.id,
        user.role,
        user.session_version,
        {"name": user.name, "unit": user.unit_usaha_id},
        tenant="bumdes",
    )


def _looks_like_email(value: str) -> bool:
    return "@" in value and "." in value.split("@")[-1]


async def _username_taken(session: AsyncSession, username: str, exclude_id: str) -> bool:
    row = (
        await session.execute(select(User).where(User.username == username, User.id != exclude_id))
    ).scalar_one_or_none()
    return row is not None


async def _email_taken(session: AsyncSession, email: str, exclude_id: str) -> bool:
    row = (
        await session.execute(select(User).where(User.email == email, User.id != exclude_id))
    ).scalar_one_or_none()
    return row is not None


@router.post("/auth/login")
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    try:
        user = await authenticate(session, payload.username, payload.password)
    except (ValueError, PermissionError) as exc:
        # Commit eksplisit di sini -- get_db() rollback seluruh sesi saat
        # handler melempar exception, jadi tanpa commit ini catatan
        # login_failed ikut hilang persis saat paling dibutuhkan (login
        # gagal/brute force).
        await record_audit(
            session, actor=None, action="login_failed", entity="users",
            detail=f"username={payload.username.strip()}", ip=_client_ip(request),
        )
        await session.commit()
        status_code = status.HTTP_401_UNAUTHORIZED if isinstance(exc, ValueError) else status.HTTP_403_FORBIDDEN
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    set_auth_cookie(response, _token_for(user))
    control = await get_system_control(session)
    await record_audit(session, actor=user, action="login_success", entity="users", entity_id=user.id, ip=_client_ip(request))
    return {"user": user_to_out(user, recording_locked=control.recording_locked)}


@router.post("/auth/logout")
async def logout(response: Response):
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/auth/me")
async def me(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    control = await get_system_control(session)
    return user_to_out(user, recording_locked=control.recording_locked)


@router.post("/auth/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password baru minimal 8 karakter")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Password baru harus berbeda")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password sementara salah")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.session_version += 1
    # Cookie lama membawa session_version lama; terbitkan yang baru agar
    # sesi pengguna sendiri tetap valid setelah ganti password.
    set_auth_cookie(response, _token_for(user))
    return {"ok": True}


@router.put("/auth/profile")
async def update_profile(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    name = payload.name.strip()
    email = (payload.email or user.email or "").strip()
    username = (payload.username or user.username or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama wajib diisi")
    if email and not _looks_like_email(email):
        raise HTTPException(status_code=400, detail="Format email tidak valid")
    if email and await _email_taken(session, email, user.id):
        raise HTTPException(status_code=400, detail="Email sudah dipakai")

    user.name = name
    if email:
        user.email = email

    if username and username != user.username:
        if public_role(user.role) != "admin":
            raise HTTPException(
                status_code=403,
                detail="Hanya admin yang dapat mengubah username",
            )
        if len(username) < 3:
            raise HTTPException(status_code=400, detail="Username minimal 3 karakter")
        if await _username_taken(session, username, user.id):
            raise HTTPException(status_code=400, detail="Username sudah dipakai")
        user.username = username

    control = await get_system_control(session)
    await session.flush()
    return user_to_out(user, recording_locked=control.recording_locked)


@router.put("/users/{user_id}")
async def admin_update_user(
    user_id: str,
    payload: AdminUserUpdateRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Nama wajib diisi")
        target.name = name
    if payload.email is not None:
        email = payload.email.strip()
        if email and not _looks_like_email(email):
            raise HTTPException(status_code=400, detail="Format email tidak valid")
        if email and await _email_taken(session, email, target.id):
            raise HTTPException(status_code=400, detail="Email sudah dipakai")
        target.email = email
    if payload.username is not None:
        username = payload.username.strip()
        if len(username) < 3:
            raise HTTPException(status_code=400, detail="Username minimal 3 karakter")
        if await _username_taken(session, username, target.id):
            raise HTTPException(status_code=400, detail="Username sudah dipakai")
        target.username = username
    if payload.role is not None:
        new_role = public_role(payload.role)
        if new_role not in PUBLIC_ROLES:
            raise HTTPException(status_code=400, detail="Role tidak valid")
        if public_role(target.role) == "admin" and new_role != "admin":
            all_users = (await session.execute(select(User))).scalars().all()
            admin_count = sum(1 for u in all_users if public_role(u.role) == "admin")
            if admin_count <= 1:
                raise HTTPException(status_code=400, detail="Tidak bisa mengubah role admin terakhir")
        unit_usaha_id = payload.unit_usaha_id if payload.unit_usaha_id is not None else target.unit_usaha_id
        if new_role == "pengelola" and not unit_usaha_id:
            raise HTTPException(status_code=400, detail="Pengelola harus memiliki unit_usaha_id")
        target.role = new_role
        target.unit_usaha_id = unit_usaha_id if new_role == "pengelola" else None
    elif payload.unit_usaha_id is not None and public_role(target.role) == "pengelola":
        target.unit_usaha_id = payload.unit_usaha_id
    control = await get_system_control(session)
    await session.flush()
    await record_audit(
        session, actor=admin, action="update_user", entity="users", entity_id=target.id,
        detail=f"Ubah akun {target.username}", ip=_client_ip(request),
    )
    return user_to_out(target, recording_locked=control.recording_locked)


@router.post("/auth/profile/photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    data = await file.read()
    try:
        photo_url = await upload_user_photo(data, file.filename or "photo.jpg", file.content_type or "image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Gagal upload foto: {exc}") from exc
    user.photo_url = photo_url
    control = await get_system_control(session)
    await session.flush()
    return user_to_out(user, recording_locked=control.recording_locked)


@router.post("/auth/register")
async def register(
    payload: RegisterRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        user = await create_user(
            session,
            username=payload.username,
            email=str(payload.email),
            name=payload.name,
            password=payload.password,
            role=payload.role,
            unit_usaha_id=payload.unit_usaha_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await record_audit(
        session, actor=admin, action="create_user", entity="users", entity_id=user.id,
        detail=f"Buat akun {user.username} ({user.role})", ip=_client_ip(request),
    )
    return user_to_out(user)


@router.get("/users")
async def users(
    _: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    control = await get_system_control(session)
    rows = await list_users(session)
    return [user_to_out(row, recording_locked=control.recording_locked) for row in rows]


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    if public_role(target.role) == "admin":
        admin_count = (await session.execute(select(User))).scalars().all()
        admin_count = sum(1 for u in admin_count if public_role(u.role) == "admin")
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Tidak bisa menghapus admin terakhir")
    await record_audit(
        session, actor=admin, action="delete_user", entity="users", entity_id=target.id,
        detail=f"Hapus akun {target.username}", ip=_client_ip(request),
    )
    await session.delete(target)
    return {"deleted": 1}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password sementara minimal 8 karakter")
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    target.password_hash = hash_password(payload.new_password)
    target.must_change_password = True
    target.session_version += 1
    await record_audit(
        session, actor=admin, action="reset_password", entity="users", entity_id=target.id,
        detail=f"Reset password {target.username}", ip=_client_ip(request),
    )
    return {"ok": True}


@router.put("/users/{user_id}/blocked-periods")
async def update_blocked_periods(
    user_id: str,
    payload: BlockedPeriodsRequest,
    request: Request,
    admin: User = Depends(require_roles("admin")),
    session: AsyncSession = Depends(get_db),
):
    try:
        user = await set_blocked_periods(session, user_id, payload.blocked_periods)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    control = await get_system_control(session)
    await record_audit(
        session, actor=admin, action="update_blocked_periods", entity="users", entity_id=user.id,
        detail=f"Blokir periode {user.username}: {', '.join(user.blocked_periods) or '-'}", ip=_client_ip(request),
    )
    return user_to_out(user, recording_locked=control.recording_locked)
