# Riska — kontrak bantuan internal

Riska memakai parser Bahasa Indonesia deterministik dan service SIABUMDES yang ada. Tidak ada panggilan LLM atau SQL dari teks pengguna. Hak akses diambil dari pengguna login di database, bukan `user_role` dalam payload.

## Penugasan pengguna

Admin selalu dapat memakai Riska. Pengguna lain memerlukan penugasan aktif **per pengguna** di `riska_assignments`, termasuk direktur, bendahara, pengelola, pengawas, dan penasihat. Hak pengguna penerima tetap berlaku: penugasan tidak meminjamkan hak Admin. Pengelola dibatasi ke unit aktifnya; pengawas/penasihat tetap baca saja.

Admin dapat memakai halaman `/riska-permissions` atau chat:

- `aktifkan dirimu di pengelola 4`
- `aktifkan Riska untuk [username atau nama lengkap]`
- `nonaktifkan Riska untuk [username atau nama lengkap]`

Chat menampilkan pengguna, role, cakupan, dan konfirmasi sebelum perubahan. Jika lebih dari satu pengguna cocok, Riska meminta nama/username yang pasti. “Pengelola 4” diselesaikan dari kode unit aktif UU04 di database, bukan nama seed. Pengguna baru atau perubahan role/unit membutuhkan penugasan eksplisit.

API Admin: GET/PATCH `/api/v1/admin/riska-assignments`; PATCH `{user_id,is_allowed}`. Endpoint lama `/api/v1/admin/riska-permissions` dipertahankan untuk pemberian/pencabutan bantuan secara eksplisit kepada pengelola dalam satu unit. Ini bukan sumber otorisasi runtime.

Widget berada di Layout halaman terautentikasi. Visibilitas diperiksa setiap 30 detik; backend memeriksa izin pada setiap permintaan. Tombol X hanya meminimalkan. “Akhiri sesi bantuan” menawarkan “Masih ada” dan “Tidak, selesai”; pilihan selesai memanggil POST `/api/riska/session/finish` dengan `session_id`, membatalkan draf yang belum disimpan, lalu menyembunyikan widget. Teks `tidak, selesai`, `sudah selesai`, `cukup`, atau `tidak ada lagi` juga mengakhiri sesi sementara. Admin dapat menugaskan kembali.

## Bahasa sederhana dan konfirmasi

POST `/api/riska/chat`: `{message,unit_usaha_id?,conversation_id?,session_id?}`. `session_id` wajib cocok untuk pengguna yang ditugaskan. Data yang belum lengkap diminta satu per satu; respons membawa `conversation_id` milik pengguna, berlaku 10 menit. Teks `batal` membatalkan pengisian, bukan sesi bantuan.

Contoh:

- `cek saldo kas`
- `berapa pendapatan bulan ini?`
- `laba rugi September 2026`
- `laba rugi unit toko dan unit air September 2026` (nama diselesaikan dari database; pengelola tidak boleh lintas unit)
- `input barang masuk produk Daster Arab Jumbo sebanyak 5 pcs harga 20 ribu dari pemasok Toko Makmur kemarin`
- `input barang keluar produk Daster Arab Jumbo sebanyak 2 pcs harga 30 ribu untuk pelanggan Bu Ani hari ini`
- `catat barang rusak produk Daster Arab Jumbo sebanyak 1 pcs tanggal 10 Oktober 2026`
- `catat setoran imbal hasil mitra Bu Ani nominal 300 ribu bulan oktober tahun 2026 hari ini`

Nama/ID/SKU diselesaikan dalam unit terpilih. Nama pendek hanya diterima jika cocok tunggal; nama ganda meminta klarifikasi. Tanda kutip boleh dipakai untuk nama panjang. Nominal mendukung `20000`, `20.000`, `20 ribu`, `20ribu`, `1,5 juta`. Harga stok selalu **per barang**, bukan total. Tanggal mendukung ISO, `10-10-2026`, `10/10/2026`, `10 Oktober 2026`, `hari ini`, dan `kemarin`, dengan zona waktu Jakarta. Tanggal yang belum lengkap tidak ditebak. Format `key=value` lama tetap didukung.

Harga, qty, periode dan tanggal ganda/bertentangan tidak ditebak. Tidak ada pencatatan ketika Riska masih meminta data. Draf final menampilkan rincian dan jurnal sebelum konfirmasi. POST `/api/riska/drafts/{id}/execute` mengeksekusi; DELETE membatalkan. Posting, audit, dan konsumsi draf berada dalam satu transaksi. Draf terikat pada pemilik, role, dan identitas sesi; pencabutan, akhir sesi, atau penugasan baru menolak konfirmasi lama. Stok/COA/nominal diverifikasi ulang. Jika respons jaringan terputus, periksa transaksi; jangan membuat draf pengganti tanpa mengecek hasil.

## Aksi yang terhubung pada tahap ini

| Kemampuan | Cakupan |
| --- | --- |
| Saldo kas/bank, pendapatan, laba rugi, perbandingan unit | REPORT_READ_LEVEL sesuai akses entitas; periode eksplisit didukung |
| Imbal hasil baru | WRITE_LEVEL, UU04, service `save_linked_payment()` |
| Barang masuk/keluar tunai dan opname barang rusak | WRITE_LEVEL, unit perdagangan/manufaktur, service InventoryService |
| Tutup buku, kunci periode, transfer bagi hasil | Admin, satu entitas/periode per konfirmasi, workflow resmi period_close_router |
| Penugasan/pencabutan Riska per pengguna | Admin, konfirmasi dalam chat atau saklar halaman kontrol |

Gabungan laba merupakan ringkasan analitis **tanpa eliminasi antarentitas**, bukan konsolidasi akuntansi resmi. Pembukuan tidak digabung. Workflow Admin tetap mematuhi tutup buku, aturan transfer, dan konfigurasi proporsi pembagian yang sudah ada. Draf strategis disimulasikan dalam savepoint dan di-rollback; eksekusi nyata membandingkan hasil dengan draf sebelum commit.

`mitra` menampilkan register modal dan pembayaran Jan–Des tahun berjalan UU04; modal register bukan saldo piutang. `stok` menampilkan qty dan valuasi Qty × cost_price register; bukan jaminan saldo buku besar persediaan. `stok tipis` memfilter Qty ≤ 5.

**Batas tahap ini:** seluruh CRUD Admin belum otomatis tersedia lewat chat. Input/ubah/hapus transaksi umum, COA, pengguna, unit, produk, dan ubah pembayaran yang sudah ada masih melalui halaman resmi. Pintasan navigasi diberi label sebagai navigasi, bukan eksekusi chat. Adapter berikutnya harus memanggil workflow resmi dan menambahkan kontrak draf serta pemeriksaan hak akses; jangan mengeksekusi endpoint arbitrer dari teks.

## Migrasi dan verifikasi

Alembic 16 mengizinkan draf Pusat tanpa unit FK. Alembic 17 menambahkan `riska_assignments`. Startup `ensure_schema()` memakai jalur create_all yang sudah ada dan migrasi grant lama dalam transaksi. Grant lama hanya disalin bagi pengelola aktif/unit aktif, kemudian flag lama dikonsumsi. Record sesi yang sudah selesai tidak diaktifkan lagi saat restart. `users.riska_enabled` tidak menjadi sumber izin.

Tidak ada perubahan data keuangan Neon selama pengujian. Verifikasi memakai skema/database PostgreSQL lokal terisolasi, pengujian hak akses/replay, migrasi 15→17, dan browser dengan API tiruan untuk tampilan serta konfirmasi.
