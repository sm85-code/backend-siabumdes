"""Auth, RBAC, system lock, and period-close use cases."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.siabumdes.identity.infrastructure.models import AuditLog, ClosedPeriod, OrgProfile, SystemControl, User
from shared.config import PUBLIC_ROLES, public_role
from modules.siabumdes.report_branding import get_report_branding
from shared.security import hash_password, verify_password

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def user_to_out(user: User, *, recording_locked: bool = False) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "name": user.name,
        "role": public_role(user.role),
        "unit_usaha_id": user.unit_usaha_id,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "blocked_periods": list(user.blocked_periods or []),
        "photo_url": user.photo_url,
        "edits_locked": recording_locked,
    }


async def get_system_control(session: AsyncSession) -> SystemControl:
    row = await session.get(SystemControl, "default")
    if row:
        return row
    row = SystemControl(id="default", recording_locked=False)
    session.add(row)
    await session.flush()
    return row


async def get_org_profile(session: AsyncSession) -> OrgProfile:
    """Kop surat/letterhead settings, editable via the Profil BUMDES menu.
    Seeded from the legacy env-var branding on first read so an existing
    deployment doesn't suddenly show a blank letterhead."""
    row = await session.get(OrgProfile, "default")
    if row:
        return row
    seed = get_report_branding()
    row = OrgProfile(
        id="default",
        org_name=seed.org_name,
        org_legal_name=seed.org_legal_name,
        address=seed.address,
        village=seed.village,
        district=seed.district,
        regency=seed.regency,
        province=seed.province,
        phone=seed.phone,
        email=seed.email,
        tagline=seed.tagline,
        signatory_left_title=seed.signatory_left_title,
        signatory_left_name=seed.signatory_left_name,
        signatory_mid_title=seed.signatory_mid_title,
        signatory_mid_name=seed.signatory_mid_name,
        signatory_right_title=seed.signatory_right_title,
        signatory_right_name=seed.signatory_right_name,
        primary_color=seed.primary_color,
    )
    session.add(row)
    await session.flush()
    return row


async def update_org_profile(session: AsyncSession, **fields: Any) -> OrgProfile:
    row = await get_org_profile(session)
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    await session.flush()
    return row


async def find_user_by_login(session: AsyncSession, username: str) -> Optional[User]:
    stmt: Select[tuple[User]] = select(User).where(
        or_(User.username == username, User.email == username)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def authenticate(session: AsyncSession, username: str, password: str) -> User:
    user = await find_user_by_login(session, username.strip())
    if not user or not verify_password(password, user.password_hash):
        raise ValueError("Username atau password salah")
    if not user.active:
        raise PermissionError("Akun dinonaktifkan")
    return user


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    name: str,
    password: str,
    role: str,
    unit_usaha_id: Optional[str],
) -> User:
    mapped = public_role(role)
    if mapped not in PUBLIC_ROLES:
        raise ValueError("Role tidak valid")
    if mapped == "pengelola" and not unit_usaha_id:
        raise ValueError("Pengelola harus memiliki unit_usaha_id")
    existing = await session.execute(
        select(User).where(or_(User.username == username, User.email == email))
    )
    if existing.scalar_one_or_none():
        raise ValueError("Email atau username sudah terdaftar")
    user = User(
        username=username.strip(),
        email=email.strip(),
        name=name.strip(),
        password_hash=hash_password(password),
        role=mapped,
        unit_usaha_id=unit_usaha_id,
        must_change_password=True,
    )
    session.add(user)
    await session.flush()
    return user


async def list_users(session: AsyncSession) -> list[User]:
    rows = await session.execute(select(User).order_by(User.created_at.desc()))
    return list(rows.scalars())


def normalize_periods(raw: list[Any]) -> list[str]:
    cleaned: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if _PERIOD_RE.match(value):
            cleaned.append(value)
    return sorted(set(cleaned))


async def set_blocked_periods(session: AsyncSession, user_id: str, periods: list[Any]) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise LookupError("User tidak ditemukan")
    user.blocked_periods = normalize_periods(periods)
    await session.flush()
    return user


async def set_recording_lock(
    session: AsyncSession,
    *,
    locked: bool,
    actor_id: str,
    note: str = "",
) -> SystemControl:
    control = await get_system_control(session)
    control.recording_locked = locked
    control.locked_by = actor_id if locked else None
    control.locked_at = datetime.now(timezone.utc) if locked else None
    control.note = note or ""
    control.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return control



async def list_closed_periods(session: AsyncSession) -> list[ClosedPeriod]:
    rows = await session.execute(
        select(ClosedPeriod).order_by(ClosedPeriod.period.desc(), ClosedPeriod.group_code.asc())
    )
    return list(rows.scalars())


async def record_audit(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity: str = "",
    entity_id: str | None = None,
    detail: str = "",
    ip: str = "",
) -> None:
    row = AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else "-",
        actor_role=public_role(actor.role) if actor else "-",
        action=action,
        entity=entity,
        entity_id=entity_id,
        detail=detail,
        ip=ip,
    )
    session.add(row)
    await session.flush()


async def list_audit_log(session: AsyncSession, limit: int = 200) -> list[AuditLog]:
    rows = await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))
    return list(rows.scalars())


def audit_out(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "actor_id": row.actor_id,
        "actor_name": row.actor_name,
        "actor_role": row.actor_role,
        "action": row.action,
        "entity": row.entity,
        "entity_id": row.entity_id,
        "detail": row.detail,
        "ip": row.ip,
    }
