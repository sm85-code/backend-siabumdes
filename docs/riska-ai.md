# Riska: agen percakapan dengan alat resmi

Mode `auto`, `sol`, dan `sonnet` memakai pola agen marketplace ERP: pesan pengguna → model → alat terstruktur → hasil resmi → model. Model tidak menghasilkan kalimat perintah untuk parser. Klarifikasi dan koreksi ditangani dalam satu percakapan, dengan riwayat enam giliran, hasil laporan terakhir, dan status tugas yang terikat pengguna, role, versi login, entitas pilihan, serta sesi penugasan. Memori berakhir setelah satu jam; tidak menjadi izin eksekusi.

`auto` memilih Sol bila dikonfigurasi, kemudian Sonnet. Ini pemilihan provider, bukan delegasi multiagen. Provider yang gagal tidak dialihkan diam-diam. `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-6.1-sol`), `ANTHROPIC_API_KEY`, `RISKA_SONNET_MODEL` (default `claude-sonnet-5-5`) hanya berada pada backend. `RISKA_AI_ENABLED=false` mematikan agen AI; mode Internal tetap tersedia.

## Alat

- `daftar_unit`: daftar unit aktif sesuai hak pengguna.
- `baca_laporan`: laba rugi/perbandingan atau kas; menerima scope dan tanggal, tahun, atau jumlah bulan terakhir.
- `referensi_transaksi`: akun dan jenis transaksi yang benar-benar terdaftar.
- `data_resmi`: baca/cari atau draf CRUD transaksi, akun, pengguna, unit, produk, mitra, pembayaran.
- `draf_operasional`: stok masuk/keluar/rusak dan setoran imbal hasil.
- `draf_strategis`: Admin saja, tutup buku/kunci periode/transfer bagi hasil.
- `draf_penugasan`: Admin saja, aktifkan/akhiri bantuan untuk penerima tanpa meminjam hak Admin.

Semua alat memvalidasi parameter dan hak akses di backend. Pengelola hanya melihat unitnya sendiri. Perhitungan uang menggunakan layanan resmi dan Decimal. Akun biaya menunjukkan komponen, bukan bukti penyebab operasional. Gabungan antarentitas tanpa eliminasi bukan konsolidasi akuntansi resmi.

`last_months=6` berarti awal lima bulan sebelum bulan berjalan sampai hari ini. `year=2026` berarti 1 Januari 2026 sampai hari ini bila tahun tersebut sedang berjalan; tahun lampau sampai 31 Desember. Bentuk periode saling eksklusif: koreksi mengganti periode lama.

## Perubahan dan konfirmasi

Model hanya menyiapkan draf. Tidak ada alat eksekusi, shell, SQL, GitHub atau API bebas. Eksekusi/cancel memakai endpoint aplikasi yang sudah ada, dengan validasi pemilik, role/sesi, masa berlaku, snapshot target dan dependensi, serta penguncian transaksi. Draf yang mendukung revisi tetap pada resource/action/entitas awal; draf lama dinonaktifkan hanya setelah draf pengganti berhasil dibuat. Bila revisi perlu klarifikasi, draf lama tetap tersedia.

Percakapan lama dapat dibaca sebagai konteks migrasi, tetapi tidak mengaktifkan loop parser kedua. Mode Internal tetap memakai perintah langsung yang sudah dikenal; tombol pintasan melewati model untuk menghemat token. Teks bebas pada mode Agen memakai model, bukan fallback parser. ID operasi mencegah request agen yang sama memakai token/membuat draf lagi selama berada dalam enam giliran memori. Pemanggilan pengguna yang sama diserialisasi dengan lock PostgreSQL.

## Batas dan kegagalan

Default `RISKA_AI_DAILY_CALLS=20`, `RISKA_AI_DAILY_USD=1.20`. Setiap panggilan provider mencadangkan USD 0.06 secara atomik, termasuk kegagalan. Angka ini cadangan pengendalian, bukan tagihan aktual. Kuota bersama SIABUMDES, terpisah dari ERP, reset pukul 00.00 WIB. Satu giliran agen dapat memakai beberapa panggilan; maksimal empat panggilan dan delapan alat. Provider timeout 40 detik, tanpa retry otomatis, output maksimal 2400 token dan konteks maksimal 60 KB.

Widget menampilkan cadangan, status panggilan, sisa kuota, alasan kuota habis dan reset. Jika penjelasan AI gagal setelah laporan berhasil dibaca, hasil resmi tetap dikembalikan. Jika kuota habis sebelum alat berjalan, pengguna dapat memilih pintasan/Perintah langsung. Riwayat dan bukti alat tersimpan pada `ai_history`; jangan kirim kata sandi atau API key ke chat.

## Verifikasi

CI menjalankan kontrak provider Sol/Sonnet dengan respons simulasi dan integrasi PostgreSQL untuk loop laporan, koreksi periode, klarifikasi singkat, draf/revisi/konfirmasi, otorisasi, pencabutan akses, deduplikasi dan kegagalan setelah pembacaan. Tes simulasi tidak membuktikan kesehatan API key atau deployment produksi; verifikasi langsung membutuhkan akses deployment tersebut.
