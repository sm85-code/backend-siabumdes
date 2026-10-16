from datetime import date

from modules.siabumdes.inventory.application.weekly_number import doc_number, partner_code, week_label


def test_nomor_dan_periode_mingguan():
    assert week_label(date(2026, 10, 10)) == "Minggu 1 Oktober 2026 (05/10/2026 s.d. 10/10/2026)"
    assert week_label(date(2026, 10, 11)).startswith("Minggu 1 Oktober")  # Minggu ikut minggu sebelumnya
    assert week_label(date(2026, 10, 2)).startswith("Minggu 4 September")
    assert doc_number("INV", "Toko Maju Jaya", date(2026, 10, 7), 1) == "INV-TMJ-M1-001/X/2026"
    assert partner_code("Mandala") == "MND"
    assert doc_number("PO", "Mandala", date(2026, 10, 7), 2, "mdl") == "PO-MDL-M1-002/X/2026"
