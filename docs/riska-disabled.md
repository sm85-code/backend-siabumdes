# Menonaktifkan Riska tanpa menghapus data

`RISKA_ENABLED` default nonaktif; hanya nilai `true` (abaikan kapital/spasi) mengaktifkan Riska.
Guard dependency pada ketiga router menolak seluruh endpoint dengan HTTP 404 sebelum autentikasi,
akses database atau provider. Akun Admin, grant lama, dan RISKA_AI_ENABLED tidak dapat melewati guard.

Endpoint tercakup:
- GET `/api/riska/availability`, `/api/riska/units`
- POST `/api/riska/session/finish`, `/api/riska/chat`, `/api/riska/comparison`
- POST `/api/riska/drafts`, `/api/riska/drafts/{draft_id}/execute`
- DELETE `/api/riska/drafts/{draft_id}`
- GET/PATCH `/api/v1/admin/riska-permissions`, `/api/v1/admin/riska-assignments`

Tidak ditemukan scheduler/background job Riska terdaftar pada aplikasi. Router adalah entry point runtime.
Kode, tabel, penugasan, sesi dan draf tetap disimpan. Endpoint lama tidak dapat mengeksekusi draf saat nonaktif.
Permintaan yang sudah berjalan sebelum proses diganti tidak dibatalkan oleh flag ini.

## Deployment setelah persetujuan

Jangan merge sebelum persetujuan: push main memicu CI lalu deploy Oracle otomatis.
Set `RISKA_ENABLED=false` pada environment container SIABUMDes (bukan hanya host shell),
lalu deploy backend beserta frontend dengan `VITE_RISKA_ENABLED=false`.
Backend lebih dahulu memblokir klien lama. Klien lama masih dapat mengirim polling yang mendapat 404
sampai tab memuat ulang frontend baru; backend tidak dapat menghentikan timer JavaScript di tab lama.
Pastikan menu/widget hilang, direct URL `/riska-permissions` menuju dashboard, dan Network tidak mengirim
request Riska setelah 15 detik/focus. Periksa transaksi/laporan melalui alur normal.

Gateway OCI hanya menyediakan status/log, bukan inspect image commit/env/file/proxy.
Commit image produksi dan konfigurasi frontend aktual belum dapat dicocokkan lewat gateway.
Status running tanpa healthcheck bukan bukti fungsi aplikasi sepenuhnya sehat.

## Aktivasi kembali

Memerlukan keputusan terpisah: set `RISKA_ENABLED=true` di backend dan build frontend dengan
`VITE_RISKA_ENABLED=true`, lalu jalankan tes Riska aktif. Grant dan sesi lama tetap ada dan bisa
berlaku lagi sesuai aturan lama; tinjau penugasan/draf sebelum aktivasi ulang. Tidak diperlukan rollback database.
