# Riska internal, Sol, dan Sonnet

Mode Otomatis, Sol, dan Sonnet memahami setiap pesan melalui AI terlebih dahulu, termasuk jawaban pendek dan lanjutan pengisian. Jika maksud atau field wajib belum jelas, AI menanyakan bagian yang kurang dengan konteks sebelumnya. Permintaan yang sudah jelas baru diteruskan ke kode internal untuk validasi, laporan resmi, dan draf. Tidak ada fallback diam-diam ke parser bila provider tidak tersedia atau kuota habis.

Widget mengirim mode Otomatis secara eksplisit. Pemanggilan API tanpa ai_mode tetap memakai Internal untuk klien internal; mode Internal merupakan pilihan eksplisit tanpa token dan memakai parser yang ada. AI tidak punya SQL, shell, endpoint arbitrer, atau izin eksekusi langsung. Seluruh perubahan tetap menunggu tombol konfirmasi draf. Jawaban teks bukan otorisasi eksekusi.

Konfigurasi backend (key hanya di environment terenkripsi):
- `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-6.1-sol`; juga mendukung `gpt-6-luna`).
- `ANTHROPIC_API_KEY`, `RISKA_SONNET_MODEL` (default `claude-sonnet-5-5`; juga mendukung `claude-sonnet-4-6`).
- `RISKA_AI_ENABLED` default true; false mematikan bantuan model, tidak mematikan Riska internal.
- `RISKA_AI_DAILY_USD` default 1.20, `RISKA_AI_DAILY_CALLS` default 20. Batas berlaku bersama untuk SIABUMDES, terpisah dari ERP.

Setiap panggilan mencadangkan USD 0.06 secara atomik sebelum request; cadangan tidak dikembalikan, termasuk kegagalan. Dengan default USD 1.20, paling banyak 20 panggilan sehari. Ini batas konservatif, bukan tagihan aktual. Untuk deployment yang memiliki override lama, tetapkan `RISKA_AI_DAILY_CALLS=20` dan `RISKA_AI_DAILY_USD=1.20`. Input dibatasi 12 KB dan output 1200 token; timeout 35 detik, tidak ada retry model otomatis. `riska_ai_budget` dan `riska_ai_calls` menyimpan cadangan, model, status dan jumlah token, tanpa prompt maupun API key. Startup initializer/Alembic 18 membuat tabel tambahan tanpa mengubah jurnal.

Hak akses/role/sesi penugasan diperiksa lagi setelah respons model. Pengelola tetap terikat unit aktifnya dan tidak meminjam akses Admin. Setelah sesi selesai, penugasan berhenti seperti sebelumnya. Sapaan penerima penugasan menjelaskan bahwa Riska didesain Pa Saepul untuk membantu pekerjaan sesuai role. Admin tetap disapa Anda, tanpa Kak/nama.

Perbaikan CRUD: perintah CRUD baru menggantikan pengisian sebelumnya tanpa menyimpan data; entitas dalam kalimat `tambah mitra ke UU04` dikenali; nama/modal dapat diisi bertahap. Penghapusan yang tidak diizinkan workflow asal menjelaskan alasannya tanpa mengulang pertanyaan. Tidak menghapus jurnal generator langsung.

Verifikasi memakai PostgreSQL lokal terisolasi dan respons provider tiruan. Koneksi API dengan key DigitalOcean serta keberhasilan deployment belum dibuktikan oleh pengujian lokal. Uji produksi dengan pertanyaan/penjelasan dahulu; setiap pencatatan tetap menunggu pemeriksaan draf.

## Konteks percakapan dan laporan tahunan

Mode Otomatis/Sol/Sonnet memahami instruksi lengkap sebelum dispatch ke workflow resmi; hasil pembacaan dikembalikan untuk penjelasan. Mode Internal tetap tanpa model. Rincian transaksi yang sudah diberikan dapat diterjemahkan menjadi field typed dan divalidasi terhadap jenis transaksi/COA terdaftar, tanpa meminta ulang seluruh instruksi. Field yang benar-benar belum tersedia tetap ditanyakan.

Memori kerja memakai record `ai_history` per pengguna dan entitas, terikat role, versi sesi login dan sesi penugasan. Menyimpan paling banyak tiga pertukaran terakhir, hingga delapan pesan untuk permintaan yang sedang diklarifikasi, serta snapshot laporan resmi, berlaku satu jam sejak aktivitas. Snapshot bukan pembacaan live baru; pertanyaan data terbaru harus membaca ulang workflow. Berakhirnya penugasan membatalkan memori beserta draf. Pergantian entitas menghapus konteks widget; percakapan milik pengguna lain ditolak. Konteks model menerima tanggal Jakarta, daftar unit aktif, serta jenis transaksi/COA scoped bila dibutuhkan. Riwayat lama dikurangi jika melewati batas input; angka laporan tetap berasal dari service.

`tahun ini` berarti 1 Januari sampai hari ini; `tahun lalu` adalah tahun kalender sebelumnya; `tahun 2026` memakai tahun tersebut, sampai hari ini bila masih tahun berjalan. Klarifikasi periode memiliki conversation_id sehingga jawaban singkat melanjutkan laporan sebelumnya. `unit 1` dikenali dari kode aktif UU01, tanpa hardcode nama seed.

Perbandingan laba rugi menyertakan margin, rasio HPP+beban, tiga akun HPP/beban terbesar dan porsi omzet terhadap total unit yang ditampilkan. Semua indikator dihitung kode dengan Decimal. Pusat tidak masuk denominator kontribusi omzet unit. Komponen biaya bukan bukti penyebab operasional; analisis tidak boleh mengarang penyebab atau menyebut ringkasan tanpa eliminasi sebagai konsolidasi resmi.

## Percobaan memori tugas terstruktur

Selain riwayat singkat, Riska menyimpan resource, rincian yang sudah disebut, field yang kurang, dan status tugas tambah mitra/transaksi pada memori milik pengguna/entitas yang sama. Model hanya mengusulkan rincian; backend membatasi field dan memvalidasi melalui CRUD resmi. Belum memasang Hermes atau menjalankan shell/SQL dari model.

Draf AI yang mendukung revisi mengembalikan can_revise. Pengguna boleh menjawab “modalnya jadi 6 juta” dengan revision_draft_id miliknya. Backend memeriksa pemilik, sesi, entitas, jenis dan masa berlaku; draf lama dinonaktifkan dalam transaksi yang sama dan draf baru tetap membutuhkan konfirmasi. Setelah eksekusi/batal/kedaluwarsa, tugas ditandai selesai pada pembacaan berikutnya, bukan dianggap sebagai izin pencatatan lanjutan. Memori tetap berlaku satu jam dan berakhir bersama penugasan.

Widget memakai tema indigo yang berbeda dari dashboard, sapaan singkat dengan efek mengetik, dan panel pengaturan/bantuan yang tertutup pada awal. Animasi menghormati prefers-reduced-motion; mobile tetap layar penuh.

Widget menampilkan cadangan biaya bersama, jumlah panggilan/status, sisa panggilan, alasan kuota habis dan reset pukul 00.00 WIB. Satu laporan dengan penjelasan bisa memakai dua panggilan. Mode Internal dapat dipilih tanpa memakai AI.
