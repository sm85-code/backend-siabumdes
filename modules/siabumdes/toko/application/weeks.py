"""Minggu Manajemen Toko (Perduitan #1/#2/#3): Senin–Sabtu.

Minggu ke-1 periode YYYY-MM dimulai Senin pertama bulan itu; hari Minggu ikut minggu
sebelumnya. Hari sebelum Senin pertama (mis. 1–4 Okt 2026) ikut minggu terakhir
periode sebelumnya (Minggu ke-4 Sep 2026 = 28 Sep – 4 Okt).
"""
import calendar
from datetime import date, timedelta


def _ym(period: str) -> tuple[int, int]:
    y, m = (int(x) for x in period.split("-"))
    return y, m


def first_monday(period: str) -> date:
    y, m = _ym(period)
    d = date(y, m, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def week_count(period: str) -> int:
    """Jumlah minggu periode = jumlah Senin di bulan itu (4 atau 5)."""
    y, m = _ym(period)
    return (calendar.monthrange(y, m)[1] - first_monday(period).day) // 7 + 1


def week_range(period: str, week: int) -> tuple[date, date]:
    """(Senin, Minggu) minggu ke-N; Minggu bisa jatuh di bulan berikutnya."""
    start = first_monday(period) + timedelta(days=7 * (week - 1))
    return start, start + timedelta(days=6)


def week_saturday(period: str, week: int) -> date:
    return week_range(period, week)[0] + timedelta(days=5)


def period_range(period: str) -> tuple[date, date]:
    """Senin Minggu ke-1 s.d. Minggu (hari) minggu terakhir periode."""
    return week_range(period, 1)[0], week_range(period, week_count(period))[1]


def trade_week(d: date) -> tuple[str, int]:
    """(periode, minggu ke-N) untuk tanggal d."""
    period = f"{d.year}-{d.month:02d}"
    if d < first_monday(period):
        prev = d.replace(day=1) - timedelta(days=1)
        period = f"{prev.year}-{prev.month:02d}"
    return period, (d - first_monday(period)).days // 7 + 1
