# backend-siabumdes

Backend **SIABUMDES** (Sistem Informasi Akuntansi BUMDes) — FastAPI + SQLAlchemy async + PostgreSQL.

Repo ini hasil ekstraksi modul SIABUMDES dari backend bersama `sm85-code/sm85-arch`
(modular monolith). Setelah pemisahan:

| Repo | Isi |
|------|-----|
| `backend-siabumdes` (repo ini) | Hanya SIABUMDES |
| `sm85-arch` | madrasah, toko, marketplace_erp |

Path API, klaim JWT (`aud=bumdes`, `iss=sm85:bumdes`), nama cookie (`bumdes_token`), dan
pengaturan cookie **identik** dengan SIABUMDES di `sm85-arch`, jadi frontend
`siabumdes.ampelkuning.com` cukup mengganti `VITE_BACKEND_URL`, dan sesi yang sudah login
tetap valid selama secret JWT-nya sama.

## Struktur

```
main.py                     # app FastAPI: router SIABUMDES, CORS, CSRF-origin + rate limit login, /health, /
modules/siabumdes/          # domain, application, infrastructure, adapters/api/v1 (semua router)
shared/                     # config (env), database (engine async), security (hash + JWT cookie)
adapters/external/          # gdrive_adapter (Google Drive: service account / OAuth)
alembic/ + alembic.ini      # riwayat migrasi (lihat alembic/README.md)
data/                       # coa_code.xlsx, coa_taxonomy.xlsx (seed COA)
tests/                      # pytest (unit + integrasi Postgres)
```

Saat startup (`lifespan`) aplikasi menjalankan `ensure_schema()` (create_all + penambahan kolom
idempoten) lalu `seed_if_needed()` (taksonomi, COA, unit usaha, user default — idempoten, tidak
menimpa data yang sudah ada).

## Menjalankan lokal

Butuh Python 3.11+ (lihat `.python-version`) dan PostgreSQL.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # isi DATABASE_URL, JWT_SECRET, dst.
# Postgres lokal tanpa TLS: set POSTGRES_SSL=false dan COOKIE_SECURE=false, COOKIE_SAMESITE=lax
uvicorn main:app --reload --port 8080
curl localhost:8080/health   # {"status":"ok","database":"connected"}
```

Dokumentasi API (`/docs`, `/redoc`, `/openapi.json`) **mati secara default** (404). Untuk
lokal/dev jalankan dengan `ENABLE_API_DOCS=true uvicorn main:app --reload --port 8080`.

Pada database kosong, seed membuat user default (mis. `admin` / `admin123@`). **Segera ganti
password** bila dipakai di luar lokal.

## Environment variables

Semua nama di bawah diambil dari `os.getenv` di kode (lihat juga `.env.example`).

| Variabel | Wajib | Default | Keterangan |
|----------|-------|---------|------------|
| `DATABASE_URL` (atau `POSTGRES_URL`) | ya | — | URL PostgreSQL; `sslmode`/`channel_binding` dibuang, driver di-rewrite ke asyncpg |
| `POSTGRES_SSL` | | `true` | `ssl=True` pada koneksi asyncpg |
| `JWT_SECRET` | ya* | — | Secret JWT bersama (fallback) |
| `JWT_SECRET_BUMDES` | | — | Secret khusus tenant `bumdes`; bila di-set, dipakai menggantikan `JWT_SECRET` |
| `JWT_ALGORITHM` | | `HS256` | |
| `JWT_EXPIRE_HOURS` | | `168` | |
| `COOKIE_SECURE` | | `true` | |
| `COOKIE_SAMESITE` | | `none` | |
| `COOKIE_PATH` | | `/` | |
| `CORS_ORIGINS` | | `http://localhost:3000` | Daftar origin dipisah koma (juga dipakai cek CSRF-origin) |
| `CORS_ORIGIN_REGEX` | | — | Regex origin tambahan |
| `APP_TITLE` | | `SIABUMDES API` | |
| `ENABLE_API_DOCS` | | `false` | `true` = aktifkan `/docs`, `/redoc`, `/openapi.json`. Default mati (404) agar skema API tidak terekspos publik; aktifkan hanya untuk lokal/dev |
| `GDRIVE_FOLDER_ID` | | — | Folder Drive utama (bukti transaksi/laporan) |
| `GDRIVE_FOLDER_ID_LOGO` | | — | Folder logo organisasi |
| `GDRIVE_FOLDER_ID_PHOTO` | | — | Folder foto profil user |
| `GDRIVE_SERVICE_ACCOUNT_JSON` | | — | JSON service account (disarankan) |
| `GOOGLE_APPLICATION_CREDENTIALS` | | — | Path file service account (alternatif) |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` / `GOOGLE_OAUTH_REFRESH_TOKEN` | | — | Alternatif OAuth untuk Drive |
| `REPORT_ORG_NAME`, `REPORT_ORG_LEGAL_NAME`, `REPORT_ORG_ADDRESS`, `REPORT_ORG_VILLAGE`, `REPORT_ORG_DISTRICT`, `REPORT_ORG_REGENCY`, `REPORT_ORG_PROVINCE`, `REPORT_ORG_PHONE`, `REPORT_ORG_EMAIL`, `REPORT_ORG_TAGLINE`, `REPORT_ORG_LOGO_URL`, `REPORT_PRIMARY_COLOR` | | lihat kode | Kop surat export PDF/Excel |
| `REPORT_SIGN_LEFT_TITLE`, `REPORT_SIGN_LEFT_NAME`, `REPORT_SIGN_MID_TITLE`, `REPORT_SIGN_MID_NAME`, `REPORT_SIGN_RIGHT_TITLE`, `REPORT_SIGN_RIGHT_NAME` | | lihat kode | Blok tanda tangan export |
| `ORG_NAME`, `ORG_LEGAL_NAME`, `ORG_ADDRESS`, `ORG_VILLAGE`, `ORG_DISTRICT`, `ORG_REGENCY`, `ORG_PROVINCE`, `ORG_PHONE`, `ORG_EMAIL` | | — | Fallback lama untuk `REPORT_ORG_*` |

\* `JWT_SECRET` boleh kosong hanya jika `JWT_SECRET_BUMDES` di-set (state OAuth GDrive tetap
memakai `JWT_SECRET`, jadi sebaiknya selalu di-set).

Tidak ada env khusus seed: seed selalu jalan saat startup dan idempoten.

## Tes

```bash
ruff check .
python -m pytest tests/ -n 0          # tanpa DATABASE_URL: tes Postgres otomatis skip
```

Integrasi Postgres (sama dengan job CI `integration-pg`):

```bash
docker compose -f docker-compose.pg-ci.yml up -d
DATABASE_URL=postgresql://siabumdes:siabumdes@127.0.0.1:55432/siabumdes_test POSTGRES_SSL=false \
  python -m pytest tests/test_postgresql_integration.py tests/test_b1_stock_atomicity.py -n 0
docker compose -f docker-compose.pg-ci.yml down -v
```

CI (`.github/workflows/ci.yml`): job `lint-and-test` (ruff + pytest) dan `integration-pg`
(service Postgres 16).

## Deploy / cutover dari sm85-arch

Tujuan: memindahkan trafik frontend SIABUMDES dari backend `sm85-arch` ke app baru ini tanpa
downtime dan dengan rollback instan. Database **tidak** dipindah — app baru memakai DB yang sama.

1. **Buat App baru di DigitalOcean App Platform** dari repo `sm85-code/backend-siabumdes`
   (branch `main`), komponen *Web Service* Python. Run command sama dengan `Procfile`
   sm85-arch:
   ```
   uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
   ```
   Health check HTTP: `/health`.
2. **Set env** — salin nilai dari env SIABUMDES di app `sm85-arch` yang sekarang:
   - `DATABASE_URL` → **DB yang sama** dengan sm85-arch (DB SIABUMDES), `POSTGRES_SSL=true`
   - `JWT_SECRET` dan (jika dipakai) `JWT_SECRET_BUMDES` → **nilai yang sama persis**, agar
     cookie `bumdes_token` yang sudah beredar tetap valid (tidak ada logout massal)
   - `JWT_EXPIRE_HOURS`, `JWT_ALGORITHM` bila di-override di sm85-arch (cookie name `bumdes_token`
     is fixed in code, nothing to copy)
   - `CORS_ORIGINS` → minimal `https://siabumdes.ampelkuning.com` (+ origin lain yang dipakai
     FE SIABUMDES); `CORS_ORIGIN_REGEX` bila ada
   - `COOKIE_SECURE=true`, `COOKIE_SAMESITE=none` (FE & BE beda domain), `COOKIE_PATH`
   - GDrive: `GDRIVE_FOLDER_ID`, `GDRIVE_FOLDER_ID_LOGO`, `GDRIVE_FOLDER_ID_PHOTO`,
     `GDRIVE_SERVICE_ACCOUNT_JSON` / `GOOGLE_APPLICATION_CREDENTIALS`,
     `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` / `GOOGLE_OAUTH_REFRESH_TOKEN`
   - Kop laporan: `REPORT_*` (dan fallback `ORG_*`) bila di-set di sm85-arch
   - Seed: tidak ada env seed; seed idempoten dan tidak menimpa data di DB yang sudah terisi.
   - Env tenant lain (`DATABASE_URL_MADRASAH`, `DATABASE_URL_TOKO`,
     `DATABASE_URL_MARKETPLACE_ERP`, `JWT_SECRET_MADRASAH/_TOKO/_MARKETPLACE_ERP`, `SHOPEE_*`,
     `IPAYMU_*`, `BITESHIP_*`, `GOOGLE_CLIENT_ID`, `GDRIVE_FOLDER_ID_TOKO`, `*_SEED_SECRET`)
     **tidak** diperlukan.
3. **Uji lewat URL app baru** (`https://<app-baru>.ondigitalocean.app`):
   - `GET /health` → `{"status":"ok","database":"connected"}`
   - `GET /api/public/summary` → data sama dengan backend lama
   - Login (`POST /api/auth/login`) dan `GET /api/auth/me`; bisa juga via FE lokal/preview
     dengan `VITE_BACKEND_URL` diarahkan ke app baru (origin FE itu harus ada di
     `CORS_ORIGINS`).
   - Startup log harus menampilkan `seed completed` tanpa error schema.
4. **Cutover**: ubah `VITE_BACKEND_URL` di static site FE SIABUMDES
   (`siabumdes.ampelkuning.com`) ke URL app baru, lalu rebuild/redeploy FE.
5. **Rollback**: kembalikan `VITE_BACKEND_URL` ke URL backend sm85-arch lalu redeploy FE.
   Karena DB dan secret JWT sama, rollback tidak kehilangan data maupun sesi.

Catatan:
- Selama transisi kedua backend melayani DB yang sama; `ensure_schema()`/seed identik dan
  idempoten, jadi aman. Rate limit login bersifat in-memory per instance.
- Jika memakai alur GDrive OAuth (`/api/admin/gdrive/connect`), tambahkan redirect URI
  `https://<app-baru>/api/admin/gdrive/oauth-callback` di Google Cloud Console sebelum
  menjalankan ulang alur connect. Refresh token yang sudah ada tetap berfungsi tanpa perubahan.
- Setelah cutover stabil, rute SIABUMDES di sm85-arch dapat dihapus di PR terpisah (di luar
  cakupan repo ini).
