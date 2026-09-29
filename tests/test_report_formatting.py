"""format_money() is what fixes 'nominal tidak terformat' in PDF/Word exports
(Excel already formats real numeric cells via its own number_format)."""
from __future__ import annotations

from decimal import Decimal

from modules.siabumdes.adapters.external.report_formatting import format_money


def test_format_money_formats_float_as_indonesian_rupiah():
    assert format_money(1000000.0) == "Rp 1.000.000"


def test_format_money_rounds_and_uses_thousands_separator():
    assert format_money(1234567.89) == "Rp 1.234.568"


def test_format_money_handles_decimal_and_negative():
    assert format_money(Decimal("50000.00")) == "Rp 50.000"
    assert format_money(-2500) == "-Rp 2.500"


def test_format_money_passes_through_non_numeric_and_empty():
    assert format_money(None) == ""
    assert format_money("") == ""
    assert format_money("Total Pendapatan") == "Total Pendapatan"


def test_branding_from_org_profile_converts_db_row_to_report_branding():
    from types import SimpleNamespace

    from modules.siabumdes.report_branding import branding_from_org_profile

    # A plain stand-in for the OrgProfile ORM row: branding_from_org_profile
    # only reads attributes, and importing the real model would pull in
    # shared.database, which requires DATABASE_URL -- unset in this CI job
    # on purpose (see .github/workflows/ci.yml).
    profile = SimpleNamespace(
        id="default",
        org_name="BUMDes Karya Raharja",
        org_legal_name="Badan Usaha Milik Desa Karya Raharja",
        address="Jl. Merdeka No 45",
        village="Desa Sukamaju",
        district="Kec. Cikupa",
        regency="Kab. Tangerang",
        province="Banten",
        phone="0812-3456-7890",
        email="bumdes@sukamaju.desa.id",
        tagline="Laporan Keuangan",
        logo_url="https://drive.google.com/uc?export=view&id=abc123",
        signatory_left_title="Direktur",
        signatory_left_name="",
        signatory_mid_title="Bendahara",
        signatory_mid_name="",
        signatory_right_title="Mengetahui",
        signatory_right_name="",
        primary_color="#1F4E79",
    )

    branding = branding_from_org_profile(profile)

    assert branding.org_name == "BUMDes Karya Raharja"
    assert branding.logo_url == "https://drive.google.com/uc?export=view&id=abc123"
    assert branding.primary_color == "1F4E79"  # leading '#' stripped
    assert "Desa Sukamaju, Kec. Cikupa, Kab. Tangerang, Banten" in branding.address_block


def test_row_is_total_ignores_long_free_text_mentioning_total_keywords():
    """Regression: a CaLK policy paragraph mentioning 'Laba Bersih Operasional'
    in passing must not be shaded/bolded like a real total row."""
    from modules.siabumdes.adapters.external.report_formatting import row_is_total

    real_total_row = ["Laba Bersih", 40315951.0]
    long_note_row = [
        "",
        "a. Kantor Pusat BUM Desa (BUMDES): Setiap akhir bulan berjalan, "
        "Laba Bersih Operasional dialokasikan dengan memindahkan porsi 52%.",
    ]
    assert row_is_total(real_total_row) is True
    assert row_is_total(long_note_row) is False


def test_looks_numeric_distinguishes_amounts_from_notes_in_the_same_column():
    """Regression: a CaLK 'Nilai' column mixes real amounts with long notes;
    only genuine numbers should be treated as numeric (right-aligned, no wrap)."""
    from modules.siabumdes.adapters.external.report_formatting import looks_numeric

    assert looks_numeric(40315951.0) is True
    assert looks_numeric("2026-01-01") is False
    assert looks_numeric("BUMDes") is False
    assert looks_numeric(
        "Berdasarkan kepatuhan terhadap Kepmendesa No. 136 Tahun 2022, ..."
    ) is False
