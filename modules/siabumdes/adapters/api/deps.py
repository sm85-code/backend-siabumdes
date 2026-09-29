"""FastAPI dependencies shared by HTTP adapters."""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.identity.application.services import get_system_control
from modules.siabumdes.identity.infrastructure.models import User
from shared.config import public_role
from shared.database import get_db
from shared.security import decode_access_token, token_from_request


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_access_token(token_from_request(request), expected_tenant="bumdes")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesi tidak valid")
    user = await session.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesi tidak valid")
    token_sv = int(payload.get("sv") or 0)
    if token_sv and token_sv != user.session_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesi sudah tidak berlaku")
    return user


def require_roles(*roles: str) -> Callable:
    allowed = {public_role(r) for r in roles}

    async def _inner(user: User = Depends(get_current_user)) -> User:
        if public_role(user.role) not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Akses ditolak")
        return user

    return _inner


async def recording_lock_state(session: AsyncSession = Depends(get_db)) -> bool:
    control = await get_system_control(session)
    return control.recording_locked
