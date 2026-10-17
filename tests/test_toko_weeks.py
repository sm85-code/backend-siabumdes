from datetime import date

from modules.siabumdes.toko.application.weeks import period_range, trade_week, week_count, week_range


def test_minggu_senin_sabtu_oktober_2026():
    assert week_range('2026-10', 1) == (date(2026, 10, 5), date(2026, 10, 11))
    assert week_count('2026-10') == 4
    assert period_range('2026-10') == (date(2026, 10, 5), date(2026, 11, 1))
    assert trade_week(date(2026, 10, 10)) == ('2026-10', 1)
    assert trade_week(date(2026, 10, 11)) == ('2026-10', 1)  # Minggu ikut minggu sebelumnya
    assert trade_week(date(2026, 10, 12)) == ('2026-10', 2)


def test_hari_sebelum_senin_pertama_ikut_bulan_lalu():
    for d in range(1, 5):
        assert trade_week(date(2026, 10, d)) == ('2026-09', 4)
    assert week_range('2026-09', 4) == (date(2026, 9, 28), date(2026, 10, 4))
    assert trade_week(date(2026, 9, 1)) == ('2026-08', 5)
