"""Delete external proofs before removing their transaction or metadata."""
import logging

from fastapi import HTTPException

from adapters.external.gdrive_adapter import delete_file_from_gdrive, is_configured

logger = logging.getLogger(__name__)


async def delete_transaction_proofs(proofs):
    if not proofs:
        return
    if any(not proof.get("file_id") for proof in proofs):
        raise HTTPException(409, "Identitas file bukti tidak lengkap; hubungi admin sebelum menghapus transaksi")
    if not is_configured():
        raise HTTPException(503, "Google Drive belum dikonfigurasi; bukti dan transaksi belum dihapus")
    for proof in proofs:
        try:
            # The adapter treats missing files as success: retries remain safe
            # if an earlier file was deleted before a later deletion failed.
            await delete_file_from_gdrive(proof["file_id"])
        except Exception as exc:
            logger.exception("Google Drive proof deletion failed; transaction retained")
            raise HTTPException(502, "Gagal menghapus bukti dari Google Drive. Transaksi tetap tersimpan; coba lagi.") from exc
