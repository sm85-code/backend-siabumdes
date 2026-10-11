# Riska: pengelolaan data melalui chat

Pilih entitas di atas chat. Untuk Pusat, Riska akan meminta konfirmasi entitas bila belum disebut. Nama panjang tidak memerlukan ID: ketik nama lengkap, dengan atau tanpa tanda kutip. Jika nama cocok dengan beberapa data, Riska meminta pilihan; tidak memilih sendiri.

Admin dapat menggunakan `lihat`, `tambah`/`buat`/`input`, `ubah`/`edit`/`koreksi`, dan `hapus` dengan:

| Data | Contoh | Rincian yang ditanyakan |
| --- | --- | --- |
| Transaksi umum | `tambah transaksi`, `ubah transaksi Pembelian Beras` | Tanggal, jenis transaksi terdaftar, keterangan, nominal, akun debit/kredit |
| COA | `tambah akun`, `ubah akun Kas Desa` | Kode, nama, kategori, subkategori, saldo normal; entitas menentukan kelompok akun |
| Pengguna | `tambah pengguna`, `ubah pengguna pengelola4` | Username, nama, email, role; unit wajib untuk pengelola |
| Unit | `tambah unit`, `ubah unit Toko Desa` | Kode, nama, jenis usaha; pilihan menyalin template COA sejenis |
| Produk | `tambah produk`, `ubah produk Daster Arab Jumbo` | SKU baru, nama, kategori terdaftar, satuan, harga modal/jual |
| Mitra imbal hasil | `tambah mitra`, `ubah mitra Bu Hajah Ani` | Nama, modal; register UU04 |
| Pembayaran imbal hasil | `ubah pembayaran`, `hapus pembayaran` | Nama mitra, tahun, bulan; bagian dan nilai yang diubah |

Contoh percakapan: `ubah produk Daster Arab Jumbo` → pilih **harga jual** → `30 ribu` → periksa draf → **Ya, Eksekusi**. Bisa juga langsung `ubah produk Daster Arab Jumbo harga jual jadi 30 ribu`. Tanggal dapat dijawab `hari ini`, `kemarin`, atau `10 Oktober 2026`. Data kurang selalu ditanyakan; nominal/tanggal tidak ditebak. Ketik `batal` untuk membatalkan pengisian.

Batas yang mengikuti sistem:

- Semua perubahan membutuhkan draf, konfirmasi, dan pemeriksaan ulang data terbaru. Draf berlaku 10 menit dan hanya dapat dipakai satu kali oleh pemilik sesi.
- COA, pengguna, dan unit hanya dapat diubah Admin. Pengguna yang ditugaskan tetap memakai akses rolenya sendiri; pengelola hanya pada unit aktif miliknya. Pengelola tidak boleh menghapus transaksi umum.
- `hapus unit` berarti **nonaktifkan**, bukan menghapus riwayat. `aktifkan unit Nama Unit` mengaktifkannya kembali.
- Akun yang telah digunakan jurnal/jenis transaksi tidak dapat dihapus. Kode/kelompok akun lama tidak dipindahkan melalui chat; perubahan yang tersedia memakai kontrak form resmi sistem.
- Produk baru dimulai dengan stok nol. Gunakan **barang masuk** untuk mencatat stok beserta jurnal. Produk dengan stok/riwayat mutasi tidak dihapus melalui CRUD produk.
- Transaksi otomatis stok, pembayaran, tutup buku, dan bagi hasil harus dikoreksi melalui modul asalnya agar register/jurnal tetap sinkron. Perubahan pembayaran yang didukung di sini adalah **pembayaran imbal hasil UU04**, bukan perubahan langsung jurnal pembayaran dagang.
- Koreksi nominal pembayaran imbal hasil memperbarui transaksi dan jurnal pasangannya. Koreksi tanggal saja mempertahankan pilihan otomatis/manual pembayaran sebelumnya. Periode tutup buku tetap berlaku untuk Admin.
- Kata sandi pengguna baru dibuat acak **saat eksekusi**, ditampilkan hanya dalam respons saat itu, tidak ditulis dalam draf/audit/localStorage, dan wajib diganti saat login. Sampaikan secara pribadi; hapus dari tampilan setelah disampaikan. Jika respons hilang, gunakan reset kata sandi resmi, jangan mengulang eksekusi draf.
- Daftar dibatasi 100 baris dengan pemberitahuan; gunakan `cari produk Nama`/`cari transaksi Referensi` untuk mempersempit pencarian.

Efek mengetik hanya pada tampilan pesan Riska: tidak memperlambat API atau mengeksekusi transaksi. Tombol **Tampilkan langsung** melewati animasi; pengaturan perangkat `prefers-reduced-motion` menampilkan respons langsung. Tabel dan tombol konfirmasi muncul setelah teks selesai.
