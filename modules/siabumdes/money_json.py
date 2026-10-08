"""Serialize SIABUMDES money fields as Decimal-safe strings on JSON out.

Inventory already emits many money fields as str. Transactions/reports historically
used float(...). After frontend-siabumdes-ts accepts number|string, prefer strings
for exact IDR (no binary float). Internal ReportingService math stays float/Decimal;
call stringify_money_fields only at JSON HTTP boundaries (not Excel/PDF/Word export).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Keys whose numeric values are rupiah amounts (not percentages / counts / flags).
MONEY_KEYS: frozenset[str] = frozenset(
    {
        "amount",
        "pendapatan",
        "beban",
        "laba",
        "laba_bersih",
        "laba_kotor",
        "total_pendapatan",
        "total_beban",
        "total_hpp",
        "total_aset",
        "total_kewajiban",
        "total_ekuitas",
        "total_pasiva",
        "total_masuk",
        "total_keluar",
        "arus_kas_bersih",
        "debit",
        "credit",
        "balance",
        "saldo_awal",
        "saldo_akhir",
        "total_debit",
        "total_credit",
        "kas_bank",
        "modal_desa",
        "pades_estimasi",
        "laba_bersih_asli",
        "laba_periode",
        "laba_dicadangkan_periode",
        "share_pengelola_30",
        "share_bumdes_70",
        "modal_awal",
        "modal_akhir",
        "pades_desa",
        "laba_dicadangkan_tahun_lalu",
        # Nested bagi-hasil allocation amounts (Rp), not share_* percentages
        "pengurus",
        "penasihat",
        "pengawas",
        "dana_sosial",
        "pades",
        "modal_bumdes",
    }
)


def money_str(value: Any) -> str:
    """Format a money-ish value as a plain decimal string (quantize 0.01)."""
    if value is None:
        return "0.00"
    if isinstance(value, bool):
        # bool is int subclass — never treat as money
        return "0.00"
    try:
        if isinstance(value, Decimal):
            d = value
        elif isinstance(value, int):
            d = Decimal(value)
        elif isinstance(value, float):
            # Round-trip via str to avoid binary float artifacts where possible
            d = Decimal(str(value))
        else:
            s = str(value).strip()
            if not s:
                return "0.00"
            d = Decimal(s)
        return format(d.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError, TypeError):
        return "0.00"


def stringify_money_fields(payload: Any) -> Any:
    """Recursively convert allowlisted money keys from int/float/Decimal → str.

    Leaves percentages (share_*), counts, booleans, and non-money keys unchanged.
    Already-string money values are left as-is.
    """
    if isinstance(payload, list):
        return [stringify_money_fields(item) for item in payload]
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in MONEY_KEYS and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                out[key] = money_str(value)
            else:
                out[key] = stringify_money_fields(value)
        return out
    return payload
