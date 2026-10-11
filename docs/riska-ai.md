# Riska internal, Sol, dan Sonnet

Perintah yang dikenali parser SIABUMDES tetap menggunakan kode internal, tanpa token model. Mode Otomatis memanggil Sol (atau Sonnet jika hanya key Anthropic tersedia) ketika maksud belum dikenali. Permintaan analisis/ringkasan laporan yang dikenali juga dapat memakai model; angka dan tabel tetap berasal dari ReportingService.

Widget menyediakan Internal / Otomatis / Sol / Sonnet. Memilih Sol/Sonnet menambahkan penjelasan pada laporan resmi; perintah CRUD yang sudah dikenali tetap melalui alur internal. AI hanya menghasilkan interpretasi bahasa atau penjelasan, tidak punya SQL, shell, endpoint arbitrer, maupun akses langsung mengeksekusi transaksi. Interpretasi ditampilkan kepada pengguna; seluruh perubahan tetap menghasilkan draf yang harus dikonfirmasi dengan tombol. Jawaban teks bukan otorisasi eksekusi.

Konfigurasi backend (key hanya di environment terenkripsi):
- `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-6.1-sol`; juga mendukung `gpt-6-luna`).
- `ANTHROPIC_API_KEY`, `RISKA_SONNET_MODEL` (default `claude-sonnet-5-5`; juga mendukung `claude-sonnet-4-6`).
- `RISKA_AI_ENABLED` default true; false mematikan bantuan model, tidak mematikan Riska internal.
- `RISKA_AI_DAILY_USD` default 0.50, `RISKA_AI_DAILY_CALLS` default 20. Batas berlaku bersama untuk SIABUMDES, terpisah dari ERP.

Setiap panggilan mencadangkan USD 0.06 secara atomik sebelum request; cadangan tidak dikembalikan, termasuk kegagalan. Dengan default USD 0.50, paling banyak 8 panggilan sehari. Ini batas konservatif, bukan tagihan aktual. Input dibatasi 12 KB dan output 900 token; timeout 35 detik, tidak ada retry model otomatis. `riska_ai_budget` dan `riska_ai_calls` menyimpan cadangan, model, status dan jumlah token, tanpa prompt maupun API key. Startup initializer/Alembic 18 membuat tabel tambahan tanpa mengubah jurnal.

Hak akses/role/sesi penugasan diperiksa lagi setelah respons model. Pengelola tetap terikat unit aktifnya dan tidak meminjam akses Admin. Setelah sesi selesai, penugasan berhenti seperti sebelumnya. Sapaan penerima penugasan menjelaskan bahwa Riska didesain Pa Saepul untuk membantu pekerjaan sesuai role. Admin tetap disapa Anda, tanpa Kak/nama.

Perbaikan CRUD: perintah CRUD baru menggantikan pengisian sebelumnya tanpa menyimpan data; entitas dalam kalimat `tambah mitra ke UU04` dikenali; nama/modal dapat diisi bertahap. Penghapusan yang tidak diizinkan workflow asal menjelaskan alasannya tanpa mengulang pertanyaan. Tidak menghapus jurnal generator langsung.

Verifikasi memakai PostgreSQL lokal terisolasi dan respons provider tiruan. Koneksi API dengan key DigitalOcean serta keberhasilan deployment belum dibuktikan oleh pengujian lokal. Uji produksi dengan pertanyaan/penjelasan dahulu; setiap pencatatan tetap menunggu pemeriksaan draf.
