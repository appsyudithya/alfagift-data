# Alfagift Data

Memantau harga, diskon, dan stok produk Alfagift di toko Alfamart yang melayani **alamat aktif akun Alfagift** (saat ini Pontianak). Riwayat harga dicatat setiap kali harga berubah.

## Cara kerja

- **Login:** crawler login ke Alfagift dengan akun pemilik (`ALFA_PHONE` / `ALFA_PASSWORD`). Toko ditentukan dari alamat aktif akun, dan token disimpan ±30 hari.
- **Sumber data:** API kategori yang dipakai web alfagift.id. Satu request mengambil 200 produk, dan seluruh katalog (±8.800 produk) butuh ±45 request.
- **Harga & stok sesuai toko:** harga dan stok mengikuti toko tersebut. Harga 0 berarti produk tidak dijual di toko itu.
- **Kategori yang error di server Alfagift:** crawler mencoba memindai per subkategori. Produk dari kategori yang tetap gagal tidak ikut ditandai "tidak tersedia".
- **Sopan ke server:** ada jeda antar request dan batas request per putaran. Kalau server menolak (HTTP 403/429), crawler berhenti.

## Fitur

- **Highlight:** skor Worth It berdasarkan diskon, nominal hemat, dan riwayat harga.
- **Produk:** tabel yang bisa difilter (kategori, stok, diskon, harga berubah), diurutkan, dan di-export ke CSV.
- **Detail produk:** grafik riwayat harga.
- **Pengaturan:** kategori yang dipindai dan jadwal crawl harian (bisa beberapa jam).

## Menjalankan lokal

```bash
ALFA_PHONE=08xxx ALFA_PASSWORD='...' python3 app.py    # http://localhost:9094
```

Data disimpan di `data/alfagift.db`, dan sesi login di `data/session.json`. Keduanya tidak ikut git.

## Deploy ke Raspberry Pi

1. Buat file `.deploy.env`. File ini tidak ikut git.

   ```
   PI_HOST=192.168.110.90
   PI_USER=pi
   PI_PASS=...
   APP_PASSWORD=
   ALFA_PHONE='08...'
   ALFA_PASSWORD='...'
   PUBLIC_HOST=alfagift.csmartkubu.uk
   ```

2. Jalankan `./deploy.sh`. Script ini memasang service `alfagift-data` (port 9094) dan, kalau `PUBLIC_HOST` diisi, mendaftarkan hostname-nya ke Cloudflare Tunnel `csmart`.
