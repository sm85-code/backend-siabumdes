from datetime import date

from modules.siabumdes.inventory.application.weekly_number import doc_number, partner_code, week_label


def test_nomor_dan_periode_mingguan():
    assert week_label(date(2026, 10, 10)) == "Minggu 1 Oktober 2026 (05/10/2026 s.d. 10/10/2026)"
    assert week_label(date(2026, 10, 11)).startswith("Minggu 1 Oktober")  # Minggu ikut minggu sebelumnya
    assert week_label(date(2026, 10, 2)).startswith("Minggu 4 September")
    assert doc_number("INV", "Toko Maju Jaya", date(2026, 10, 7), 1) == "INV-TKM-M1-001/X/2026"
    assert partner_code("Mandalawangi") == "MDL" and partner_code("BUMI LESTARI") == "BML" and partner_code("Ani") == "ANI"
    assert doc_number("PO", "Mandala", date(2026, 10, 7), 2, "mdl") == "PO-MDL-M1-002/X/2026"


def test_isi_minggu_berdasarkan_tanggal_transaksi():
    from modules.siabumdes.inventory.application.weekly_number import in_week

    wk = date(2026, 10, 5)
    assert in_week(date(2026, 10, 10), wk) and in_week(date(2026, 10, 11), wk)  # Sabtu & Minggu
    assert not in_week(date(2026, 10, 12), wk) and not in_week(date(2026, 10, 4), wk)

