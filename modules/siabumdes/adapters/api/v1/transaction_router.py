"""Transactions + proof uploads. Isolates pengelola to their own unit."""
from __future__ import annotations

import logging
import secrets
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from modules.siabumdes.adapters.api.deps import get_current_user, require_roles
from modules.siabumdes.money_json import money_str
from shared.config import JWT_ALGORITHM, JWT_SECRET
from modules.siabumdes.adapters.api.scope import (
    TX_DELETE_ROLES,
    WRITE_ROLES,
    assert_can_mutate_period,
    assert_not_readonly,
    assert_unit_active,
    can_access_unit,
    is_pengelola,
    parse_date,
    scoped_unit_id,
    unit_code_for,
)
from adapters.external.gdrive_adapter import (
    gdrive_file_exists,
    is_configured,
    is_oauth_configured,
    oauth_flow,
    upload_file_to_gdrive,
)
from modules.siabumdes.identity.infrastructure.models import User
from modules.siabumdes.application.transaction_proofs import delete_transaction_proofs
from modules.siabumdes.application.bagi_hasil_transfer import is_bh_reference
from modules.siabumdes.application.transaction_journal import sync_transaction_journal as _sync_journal
from modules.siabumdes.infrastructure.models import JournalEntry, Transaction
from shared.database import get_db

router = APIRouter(prefix="/api", tags=["transactions"])
ALLOWED_PROOF_EXT = {"pdf", "jpg", "jpeg", "png"}
MAX_PROOF_BYTES = 1 * 1024 * 1024
MAX_PROOFS = 3

logger = logging.getLogger("sm85.gdrive_oauth")
_GDRIVE_OAUTH_STATE_PURPOSE = "gdrive_oauth"
_GDRIVE_OAUTH_STATE_TTL_SECONDS = 600


def _make_gdrive_oauth_state() -> str:
    """Signed, expiring OAuth `state` (multi-worker safe; no in-memory store)."""
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT_SECRET harus di-set untuk OAuth GDrive")
    now = int(time.time())
    payload = {
        "purpose": _GDRIVE_OAUTH_STATE_PURPOSE,
        "iat": now,
        "exp": now + _GDRIVE_OAUTH_STATE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_gdrive_oauth_state(state: str) -> None:
    if not state or not JWT_SECRET:
        raise HTTPException(status_code=403, detail="State OAuth tidak valid atau kedaluwarsa")
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=403, detail="State OAuth tidak valid atau kedaluwarsa") from exc
    if payload.get("purpose") != _GDRIVE_OAUTH_STATE_PURPOSE:
        raise HTTPException(status_code=403, detail="State OAuth tidak valid atau kedaluwarsa")


# Pagination for GET /transactions (B3). Default body stays a bare JSON array so
# live FE (`setTxs(t.data)`) keeps working; pass meta=true for the envelope.
# Response headers always carry total/limit/offset/has_more for honest clients.
DEFAULT_TX_LIMIT = 500
MAX_TX_LIMIT = 2000


def _tx_page_meta(*, total: int, limit: int, offset: int, item_count: int) -> dict:
    return {
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "has_more": int(offset) + int(item_count) < int(total),
    }


def _apply_tx_list_headers(response: Response, page: dict) -> None:
    response.headers["X-Total-Count"] = str(page["total"])
    response.headers["X-Limit"] = str(page["limit"])
    response.headers["X-Offset"] = str(page["offset"])
    response.headers["X-Has-More"] = "true" if page["has_more"] else "false"


class TransactionIn(BaseModel):
    date: date
    unit_usaha_id: Optional[str] = None
    transaction_type: str
    description: str = ""
    amount: Decimal = Field(gt=0)
    debit_account_code: str
    credit_account_code: str
    reference: str = ""
    mitra_id: Optional[str] = None


def _tx_out(row: Transaction) -> dict:
    # Money JSON: Decimal → string (TS FE parseMoney accepts number|string; JS FE not serving).
    proofs = list(row.proofs or [])
    return {
        "id": row.id,
        "date": row.date.isoformat(),
        "unit_usaha_id": row.unit_usaha_id,
        "transaction_type": row.transaction_type,
        "description": row.description,
        "amount": money_str(row.amount),
        "debit_account_code": row.debit_account_code,
        "credit_account_code": row.credit_account_code,
        "reference": row.reference,
        "mitra_id": row.mitra_id,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "is_closing": row.is_closing,
        "proofs": proofs,
    }



def _assert_not_bagi_hasil(reference: Optional[str]) -> None:
    if reference and reference.startswith("YIELD:"):
        raise HTTPException(400, "Transaksi Imbal Hasil otomatis hanya dapat diubah/dihapus melalui menu Imbal Hasil")
    if is_bh_reference(reference):
        raise HTTPException(
            status_code=400,
            detail="Transaksi bagi hasil otomatis tidak bisa dibuat, diubah, atau dihapus di sini. "
            "Gunakan menu Laporan > Transfer Bagi Hasil.",
        )




@router.get("/transactions")
async def list_transactions(
    response: Response,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    unit_usaha_id: Optional[str] = None,
    reference: Optional[str] = None,
    limit: int = Query(DEFAULT_TX_LIMIT, ge=1, le=MAX_TX_LIMIT),
    offset: int = Query(0, ge=0),
    meta: bool = Query(
        False,
        description=(
            "If true, return {items, total, limit, offset, has_more}. "
            "Default false keeps a bare JSON array for live FE compatibility."
        ),
    ),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """List transactions with limit/offset pagination.

    Body (default): JSON array of transaction objects (backward compatible).
    Body (meta=true): {"items": [...], "total": N, "limit": L, "offset": O, "has_more": bool}.

    Always sets response headers X-Total-Count, X-Limit, X-Offset, X-Has-More so
    clients can detect silent truncation even when using the legacy array body.

    Filter by start_date/end_date/unit_usaha_id/reference server-side — preferred
    over loading a large page and filtering in the browser.

    unit_usaha_id filter (non-pengelola; omit keeps all units — live FE safe):
      - omitted / None → no unit filter (all units)
      - "" or "__null__" → BUMDES pusat only (unit_usaha_id IS NULL), same as export
      - real id → that unit only
    """
    filters = []
    start = parse_date(start_date)
    end = parse_date(end_date)
    if start:
        filters.append(Transaction.date >= start)
    if end:
        filters.append(Transaction.date <= end)
    if is_pengelola(user):
        filters.append(Transaction.unit_usaha_id == user.unit_usaha_id)
    elif unit_usaha_id == "" or unit_usaha_id == "__null__":
        # BUMDES pusat sentinel — matches GET /transactions/export empty-string filter.
        filters.append(Transaction.unit_usaha_id.is_(None))
    elif unit_usaha_id:
        filters.append(Transaction.unit_usaha_id == unit_usaha_id)
    if reference:
        # Substring match, not exact: a single inventory movement can post more
        # than one transaction (e.g. stock-out posts both "stock-out:<id>" for
        # COGS and "stock-out-rev:<id>" for revenue). Filtering by the shared
        # <id> fragment surfaces every transaction tied to that movement/
        # adjustment in one request, from Inventory's "Lihat transaksi" link.
        filters.append(Transaction.reference.contains(reference))

    count_stmt = select(func.count()).select_from(Transaction)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int(await session.scalar(count_stmt) or 0)

    stmt = (
        select(Transaction)
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if filters:
        stmt = stmt.where(*filters)
    rows = (await session.execute(stmt)).scalars().all()
    items = [_tx_out(row) for row in rows]
    page = _tx_page_meta(total=total, limit=limit, offset=offset, item_count=len(items))
    _apply_tx_list_headers(response, page)
    if meta:
        return {"items": items, **page}
    return items


@router.post("/transactions")
async def create_transaction(
    payload: TransactionIn,
    user: User = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    assert_not_readonly(user)
    _assert_not_bagi_hasil(payload.reference)
    unit_id = await scoped_unit_id(session, user, payload.unit_usaha_id)
    await assert_unit_active(session, unit_id)
    await assert_can_mutate_period(session, user, payload.date, unit_id)
    group = await unit_code_for(session, unit_id)
    tx = Transaction(
        date=payload.date,
        unit_usaha_id=unit_id,
        transaction_type=payload.transaction_type,
        description=payload.description,
        amount=payload.amount,
        debit_account_code=payload.debit_account_code,
        credit_account_code=payload.credit_account_code,
        reference=payload.reference or "",
        mitra_id=payload.mitra_id,
        created_by=user.id,
        proofs=[],
    )
    session.add(tx)
    await session.flush()
    await _sync_journal(session, tx, group)
    return _tx_out(tx)


@router.put("/transactions/{tx_id}")
async def update_transaction(
    tx_id: str,
    payload: TransactionIn,
    user: User = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    assert_not_readonly(user)
    tx = (
        await session.execute(
            select(Transaction).options(selectinload(Transaction.journal_entry)).where(Transaction.id == tx_id)
        )
    ).scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    _assert_not_bagi_hasil(tx.reference)
    _assert_not_bagi_hasil(payload.reference)
    if not can_access_unit(user, tx.unit_usaha_id):
        raise HTTPException(status_code=403, detail="Hanya bisa mengedit transaksi unit Anda")
    unit_id = await scoped_unit_id(session, user, payload.unit_usaha_id if not is_pengelola(user) else tx.unit_usaha_id)
    if is_pengelola(user):
        # Pengelola yang unitnya sudah dinonaktifkan bersifat read-only --
        # tidak boleh mengedit apa pun lagi, termasuk transaksi lama di
        # unitnya sendiri.
        await assert_unit_active(session, unit_id)
    elif unit_id != tx.unit_usaha_id:
        # Untuk admin/direktur/dst: hanya cek unit aktif kalau transaksi
        # dipindah ke unit lain -- mengedit transaksi lama yang sudah ada
        # di unit tetap boleh, biar masih bisa membetulkan salah ketik di
        # transaksi historis.
        await assert_unit_active(session, unit_id)
    await assert_can_mutate_period(session, user, tx.date, tx.unit_usaha_id)
    await assert_can_mutate_period(session, user, payload.date, unit_id)
    tx.date = payload.date
    tx.unit_usaha_id = unit_id
    tx.transaction_type = payload.transaction_type
    tx.description = payload.description
    tx.amount = payload.amount
    tx.debit_account_code = payload.debit_account_code
    tx.credit_account_code = payload.credit_account_code
    tx.reference = payload.reference or ""
    tx.mitra_id = payload.mitra_id
    await session.flush()
    group = await unit_code_for(session, unit_id)
    await _sync_journal(session, tx, group)
    return _tx_out(tx)


@router.delete("/transactions/{tx_id}")
async def delete_transaction(
    tx_id: str,
    user: User = Depends(require_roles(*TX_DELETE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    tx = await session.get(Transaction, tx_id, with_for_update=True)
    if not tx:
        return {"deleted": 0}
    _assert_not_bagi_hasil(tx.reference)
    await assert_can_mutate_period(session, user, tx.date, tx.unit_usaha_id)
    await delete_transaction_proofs(list(tx.proofs or []))
    # Delete the journal entry (+ items via ORM cascade) explicitly first.
    # ORM delete of Transaction alone would try to SET NULL
    # journal_entries.transaction_id, which violates NOT NULL and 500s —
    # same class of bug already worked around in cancel_inventory_journal.
    existing_entry = await session.scalar(
        select(JournalEntry).where(JournalEntry.transaction_id == tx.id)
    )
    if existing_entry:
        await session.delete(existing_entry)
        await session.flush()
    await session.delete(tx)
    return {"deleted": 1}


@router.post("/transactions/{tx_id}/proof")
async def upload_proof(
    tx_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    tx = await session.get(Transaction, tx_id, with_for_update=True)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    if not can_access_unit(user, tx.unit_usaha_id):
        raise HTTPException(status_code=403, detail="Bukan transaksi unit Anda")
    if is_pengelola(user):
        await assert_unit_active(session, user.unit_usaha_id)
    await assert_can_mutate_period(session, user, tx.date, tx.unit_usaha_id)
    proofs = list(tx.proofs or [])
    if len(proofs) >= MAX_PROOFS:
        raise HTTPException(status_code=400, detail="Maksimal 3 file bukti per transaksi.")
    fname = (file.filename or "bukti").lower()
    ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
    if ext not in ALLOWED_PROOF_EXT:
        raise HTTPException(status_code=400, detail="Format harus PDF/JPG/JPEG/PNG")
    data = await file.read()
    if len(data) > MAX_PROOF_BYTES:
        raise HTTPException(status_code=400, detail="Ukuran file maksimal 1 MB")
    if not is_configured():
        raise HTTPException(status_code=503, detail="Google Drive service account belum dikonfigurasi")

    group_code = await unit_code_for(session, tx.unit_usaha_id)
    ddmmyyyy = tx.date.strftime("%d%m%Y")
    same_q = select(Transaction).where(Transaction.date == tx.date)
    if tx.unit_usaha_id:
        same_q = same_q.where(Transaction.unit_usaha_id == tx.unit_usaha_id)
    else:
        same_q = same_q.where(Transaction.unit_usaha_id.is_(None))
    max_urut = 0
    for other in (await session.execute(same_q)).scalars():
        for item in other.proofs or []:
            max_urut = max(max_urut, int(item.get("urut") or 0))
    urut = max_urut + 1
    new_name = f"{group_code}_{ddmmyyyy}_{urut}.{ext}"
    meta = await upload_file_to_gdrive(data, new_name, file.content_type or "application/octet-stream")
    proof = {
        "file_id": meta["id"],
        "file_name": meta["name"],
        "url": meta.get("webViewLink"),
        "urut": urut,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": user.id,
        "size": len(data),
    }
    proofs.append(proof)
    tx.proofs = proofs
    return {"ok": True, "proofs": proofs}


@router.delete("/transactions/{tx_id}/proofs/{file_id}")
async def delete_proof(
    tx_id: str,
    file_id: str,
    user: User = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    tx = await session.get(Transaction, tx_id, with_for_update=True)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")
    if not can_access_unit(user, tx.unit_usaha_id):
        raise HTTPException(status_code=403, detail="Bukan transaksi unit Anda")
    if is_pengelola(user):
        await assert_unit_active(session, user.unit_usaha_id)
    await assert_can_mutate_period(session, user, tx.date, tx.unit_usaha_id)
    proofs = list(tx.proofs or [])
    if not any(p.get("file_id") == file_id for p in proofs):
        raise HTTPException(status_code=404, detail="File bukti tidak ditemukan")
    await delete_transaction_proofs([p for p in proofs if p.get("file_id") == file_id])
    tx.proofs = [p for p in proofs if p.get("file_id") != file_id]
    return {"ok": True, "proofs": tx.proofs}


@router.post("/transactions/verify-proofs")
async def verify_proofs(
    user: User = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_db),
):
    if not is_configured():
        return {"ok": True, "checked": 0, "removed": 0, "note": "Drive belum dikonfigurasi"}
    stmt = select(Transaction).with_for_update()
    if is_pengelola(user):
        stmt = stmt.where(Transaction.unit_usaha_id == user.unit_usaha_id)
    checked = removed = 0
    for tx in (await session.execute(stmt)).scalars():
        arr = list(tx.proofs or [])
        if not arr:
            continue
        kept = []
        changed = False
        for item in arr:
            fid = item.get("file_id")
            if not fid:
                continue
            checked += 1
            try:
                exists = await gdrive_file_exists(fid)
            except Exception as exc:
                raise HTTPException(502, "Gagal memeriksa bukti Google Drive; daftar bukti tetap tersimpan") from exc
            if exists:
                kept.append(item)
            else:
                removed += 1
                changed = True
        if changed:
            tx.proofs = kept
    return {"ok": True, "checked": checked, "removed": removed}


@router.get("/admin/gdrive/status")
async def gdrive_status(_: User = Depends(get_current_user)):
    oauth = is_oauth_configured()
    return {
        "connected": is_configured(),
        "mode": "oauth" if oauth else "service_account",
        "configured": is_configured(),
    }


@router.get("/admin/gdrive/connect")
async def gdrive_connect(request: Request, _: User = Depends(require_roles("admin"))):
    """Mulai alur OAuth (admin-only): kembalikan link consent Google.

    Google redirect ke /admin/gdrive/oauth-callback. Callback tidak pernah
    mengembalikan refresh_token ke browser — preferensi produksi adalah
    env-only (set GOOGLE_OAUTH_REFRESH_TOKEN dari saluran ops), dengan
    signed one-time `state` + sesi admin pada callback sebagai pengaman.
    """
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/api/admin/gdrive/oauth-callback"
    flow = oauth_flow(redirect_uri)
    state = _make_gdrive_oauth_state()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    return {"auth_url": auth_url}


@router.get("/admin/gdrive/oauth-callback")
async def gdrive_oauth_callback(
    request: Request,
    code: str,
    state: str = "",
    _: User = Depends(require_roles("admin")),
):
    """OAuth redirect target — admin session + signed state required.

    Never returns refresh_token in JSON. On success the token is written once
    to the application log so ops can set GOOGLE_OAUTH_REFRESH_TOKEN via the
    platform env UI (App Platform cannot write its own env at runtime).
    Preferred production path: obtain the refresh token offline and set env only.
    """
    _verify_gdrive_oauth_state(state)
    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/api/admin/gdrive/oauth-callback"
    flow = oauth_flow(redirect_uri)
    flow.fetch_token(code=code)
    refresh_token = flow.credentials.refresh_token
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google tidak mengembalikan refresh token (biasanya karena akun ini sudah "
                "pernah kasih izin sebelumnya). Buka https://myaccount.google.com/permissions, "
                "cabut akses aplikasi ini, lalu ulangi proses connect dari awal."
            ),
        )
    # Intentional one-shot ops bootstrap: token must not appear in any HTTP body.
    logger.warning(
        "GDrive OAuth OK — set platform env GOOGLE_OAUTH_REFRESH_TOKEN then redeploy "
        "(value intentionally omitted from HTTP response): %s",
        refresh_token,
    )
    return {
        "ok": True,
        "detail": (
            "OAuth berhasil. refresh_token TIDAK dikirim ke browser. "
            "Salin nilai dari application log (baris 'GDrive OAuth OK'), set sebagai "
            "GOOGLE_OAUTH_REFRESH_TOKEN di App Platform bersama GOOGLE_OAUTH_CLIENT_ID/"
            "GOOGLE_OAUTH_CLIENT_SECRET, lalu redeploy. "
            "Preferensi produksi: dapatkan refresh token offline (env-only) tanpa memakai callback ini."
        ),
    }
