# Rencana: Rapikan Struktur Backend — Satukan Semua Milik Siabumdes

> **Catatan arsip (backend-siabumdes):** dokumen ini disalin apa adanya dari repo
> `sm85-code/sm85-arch`, sebelum SIABUMDES dipisah menjadi repo sendiri. Referensi ke
> `tenants/`, madrasah, dan toko di bawah hanya berlaku untuk repo `sm85-arch`; nomor PR
> (#75–#79) juga merujuk ke repo tersebut.

> **STATUS: SELESAI ✅** — semua 7 tahap (Tahap 0–6) sudah dieksekusi dan di-merge ke `main`,
> masing-masing sebagai PR terpisah: #75 (Tahap 1), #76 (Tahap 2), #77 (Tahap 3),
> #78 (Tahap 4), #79 (Tahap 5) — Tahap 0 & 6 tidak menghasilkan kode pindahan sendiri,
> jadi digabung ke commit terdekat. Struktur akhir di bawah ini sudah menjadi kenyataan,
> bukan lagi rencana. Lihat "Hasil eksekusi & verifikasi" di bagian paling bawah untuk detail
> pembuktian tiap tahap.

> Disusun dari sesi analisis: siabumdes TETAP jadi inti aplikasi (bukan dipindah ke `tenants/`).
> Tujuan: `modules/identity`, `modules/uu05_inventory`, dan file-file di `shared/`/`adapters/`
> yang ternyata cuma dipakai siabumdes, disatukan ke dalam `modules/siabumdes/`.

## Latar belakang (kesimpulan dari analisis)

- `modules/` berisi 3 paket: `identity`, `siabumdes`, `uu05_inventory` — bukan cuma siabumdes.
- `modules/identity` dan `modules/uu05_inventory` **konseptual bagian dari siabumdes**
  (identity = auth/user/tutup-buku siabumdes; uu05_inventory = menu Inventory siabumdes
  untuk unit usaha UU05), tapi diletakkan terpisah.
- `tenants/madrasah` dan `tenants/toko` **fully independen** — auth sendiri, database sendiri,
  zero import ke `modules.identity`/`modules.siabumdes`.
- Ditemukan bug: `modules/identity/application/services.py` sempat mengimpor balik
  `from modules.siabumdes.infrastructure.models import Transaction, UnitUsaha` — arah
  ketergantungan terbalik (core→tenant-like). **Sudah diperbaiki** di PR #72 (sekaligus
  menghapus 2 fungsi mati `close_period`/`reopen_period` versi lama yang sudah digantikan
  oleh `run_monthly_close`/`undo_monthly_close` di `modules/siabumdes/application/closing.py`).
- Rekomendasi: **JANGAN** pindahkan siabumdes ke `tenants/` — dia bukan tenant tambahan,
  dia inti/produk utama (routing tanpa prefix, dipakai di hampir semua adapters/shared).

## Hasil audit: file mana yang genuinely "shared" vs cuma dipakai siabumdes

Dicek dengan grep terhadap `tenants/madrasah` dan `tenants/toko`:

### `shared/` — HANYA 3 file yang genuinely dipakai semua tenant
- `shared/config.py` ✅ dipakai madrasah + toko
- `shared/database.py` ✅ dipakai madrasah + toko
- `shared/security.py` ✅ dipakai madrasah + toko

### `shared/` — 5 file yang TERNYATA cuma dipakai siabumdes (harus pindah)
- `shared/coa_taxonomy.py`
- `shared/period.py`
- `shared/report_branding.py`
- `shared/schema.py`
- `shared/seed.py`

### `adapters/api/` — SEMUA cuma dipakai siabumdes (harus pindah)
- `adapters/api/deps.py`, `adapters/api/scope.py`
- 11 router di `adapters/api/v1/*.py` (admin_control, auth, io, master_data, org_profile,
  period_close, public, reports, siabumdes, transaction, uu05_inventory)
- Catatan: madrasah & toko sudah punya `adapters/api/` sendiri masing-masing di foldernya —
  pola ini tinggal diikuti siabumdes juga.

### `adapters/external/` — CAMPURAN, cek satu per satu!
- ❌ TETAP di root (genuinely dipakai bersama): **`gdrive_adapter.py`** — dipakai toko juga
  (ada komentar eksplisit di `tenants/toko/modules/toko/infrastructure/image_upload.py`
  yang bilang "already used by the BUMDes reporting side")
- ✅ Pindah ke siabumdes (cuma dipakai siabumdes): `excel_adapter.py`, `html_pdf.py`,
  `org_logo_upload.py`, `pdf_generator.py`, `report_formatting.py`, `word_generator.py`

## Struktur akhir yang dituju

```
sm85-arch/
├── shared/
│   ├── config.py
│   ├── database.py
│   └── security.py
├── adapters/
│   └── external/
│       └── gdrive_adapter.py
├── modules/
│   └── siabumdes/
│       ├── identity/              (dari modules/identity/)
│       ├── inventory/             (dari modules/uu05_inventory/)
│       ├── application/           (sudah ada)
│       ├── infrastructure/        (sudah ada)
│       ├── adapters/
│       │   ├── api/               (dari adapters/api/deps.py, scope.py, v1/*.py)
│       │   └── external/          (dari adapters/external/*, kecuali gdrive_adapter.py)
│       ├── coa_taxonomy.py
│       ├── period.py
│       ├── report_branding.py
│       ├── schema.py
│       └── seed.py
├── tenants/
│   ├── madrasah/{modules, adapters/api}
│   └── toko/{modules, adapters/api}
├── alembic/
└── main.py
```

## Tahapan eksekusi (1 PR per tahap)

- [x] **Tahap 0** — Cek Procfile/pyproject.toml/CI YAML/Dockerfile untuk path hardcode.
- [x] **Tahap 1** — Pindahkan `modules/identity` → `modules/siabumdes/identity/`.
  28 baris referensi, 8 file pemakai. Verifikasi: login, Tutup Buku, profil organisasi. (PR #75)
- [x] **Tahap 2** — Pindahkan `modules/uu05_inventory` → `modules/siabumdes/inventory/`.
  14 baris referensi, 3 file pemakai. Verifikasi: fitur Inventory. (PR #76)
- [x] **Tahap 3** — Pindahkan 5 file `shared/` yang bukan shared ke `modules/siabumdes/`.
  Verifikasi: laporan keuangan, tutup buku, export PDF/Excel/Word. (PR #77)
- [x] **Tahap 4** — Pindahkan `adapters/api/` (deps, scope, 11 router) ke
  `modules/siabumdes/adapters/api/`. Update `main.py`. **Paling luas dampaknya** —
  jalankan full test suite + cek manual semua grup fitur. (PR #78)
- [x] **Tahap 5** — Pindahkan 6 file `adapters/external/` (KECUALI `gdrive_adapter.py`) ke
  `modules/siabumdes/adapters/external/`. **Hati-hati jangan sampai gdrive_adapter.py
  ikut kepindah** — akan merusak fitur upload gambar toko. (PR #79)
- [x] **Tahap 6** — Hapus folder kosong, jalankan `alembic revision --autogenerate` untuk
  pastikan tidak ada perubahan skema tak sengaja, full test suite + smoke test tiap tenant.

## Yang TIDAK berubah
- URL API (routing independen dari lokasi file, sudah diverifikasi).
- Nama tabel database / skema (cuma pindah lokasi kode Python).
- Frontend tidak perlu disentuh sama sekali.
- Status siabumdes sebagai inti aplikasi (bukan dipindah ke `tenants/`).

## Estimasi
- ~24 file dipindah, ~75+ baris import diupdate.
- Risiko rendah-menengah, murni pemindahan lokasi kode (tidak ada perubahan logika bisnis).
- Titik paling perlu hati-hati: Tahap 4 (blast radius luas) dan Tahap 5 (jangan salah pindah gdrive_adapter.py).

## Hasil eksekusi & verifikasi

Semua tahap dieksekusi sebagai pure move (rename file via `git mv` + rewrite import),
tanpa mengubah satu baris logika bisnis, nama tabel, atau path API pun. Tiap tahap
diverifikasi dengan checklist yang sama sebelum di-PR-kan:

1. `python -c "import main"` — jumlah route terdaftar tetap 251 di setiap tahap.
2. **Route-identity diff** (Tahap 4, tahap paling luas): daftar lengkap (method, path)
   di-capture sebelum & sesudah pemindahan, di-diff — hasilnya identik byte-per-byte,
   bukan cuma sama jumlahnya.
3. Full test suite dijalankan terhadap Postgres asli (bukan cuma subset yang di-skip
   CI) di tiap tahap: konsisten 136 passed, 1 gagal (pre-existing, tidak terkait —
   `test_postgresql_integration.py` mengimpor modul `database` yang sudah lama tidak
   dipakai, direproduksi identik di `main` sebelum restrukturisasi dimulai).
4. `ruff check .` bersih di tiap tahap.
5. **Live smoke test** lewat `TestClient` + lifespan aplikasi asli (`ensure_schema` +
   `seed_if_needed`) terhadap Postgres nyata di tiap tahap — bukan cuma import check:
   - Tahap 1: login berhasil sampai ke database sungguhan lewat path baru.
   - Tahap 2: seluruh endpoint Inventory (meta, products, buat vendor, laporan valuasi).
   - Tahap 3: laba-rugi, dashboard, tutup-buku + undo, dan ketiga format export
     (PDF/Excel/Word) — semua menghasilkan file valid.
   - Tahap 4 (16 endpoint lintas modul: auth, unit-usaha, accounts, transaction-types,
     mitra, transactions create, org-profile, admin, users, reports, inventory,
     public, gdrive).
   - Tahap 5: ketiga export + template Excel + status gdrive, plus test khusus
     `test_toko_image_upload.py`/`test_logo_fetch.py` untuk membuktikan
     `gdrive_adapter.py` yang tetap di lokasi lama tidak ikut rusak.
6. **Tahap 6 (final)**: `alembic revision --autogenerate` (lewat API `compare_metadata`
   langsung karena `alembic/script.py.mako` memang tidak ada di repo ini sejak awal)
   menunjukkan 44 perbedaan — tapi ini semua *drift lama* antara riwayat migrasi dan
   model ORM (tabel legacy `produk`/`stok_masuk`/`stok_keluar` vs tabel baru
   `products`/`stock_cards`, dll — aplikasi memang mengandalkan `ensure_schema()`,
   bukan `alembic upgrade`, untuk sinkronisasi skema produksi). Dibuktikan dengan
   `diff` isi ketiga file `models.py` (siabumdes, identity, inventory) terhadap versi
   sebelum Tahap 1 — **identik byte-per-byte** — dan `git diff` folder
   `alembic/versions/` sejak sebelum Tahap 1 — **nol perubahan**. Jadi 44 perbedaan
   itu 100% pre-existing, bukan drift baru dari pemindahan file.
   Ditutup dengan smoke test lintas 3 tenant: SIABUMDES (login + dashboard), Madrasah
   (request sampai ke guard auth-nya sendiri, bukan 500), dan Toko (endpoint publik
   `/api/toko/produk` setelah skema tenant itu di-seed) — semua tersambung normal.

Folder `adapters/api/` (lama) dan `modules/identity/`, `modules/uu05_inventory/`
(lama) sudah tidak ada lagi. Struktur saat ini persis sama dengan "Struktur akhir
yang dituju" di atas.
oke
