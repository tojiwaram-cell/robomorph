# RoboMorph Auto-Reels

Membuat dan memposting Reels sains alam ke halaman Facebook RoboMorph secara otomatis, 3x sehari (07.00, 12.00, 19.00 WITA). Durasi 30 detik, vertikal 1080×1920, tanpa musik, dan sumbernya disaring dari klip yang menampilkan manusia.

## Cara kerja

Untuk setiap segmen video, sumber dicoba berurutan. Kalau satu sumber habis kuota atau tidak punya hasil, otomatis pindah ke sumber berikutnya:

1. **inbox/**: video yang Anda taruh sendiri, misalnya hasil Google Flow atau Dreamina. Selalu diposting paling dulu. Caption opsional bisa ditaruh di file `.txt` dengan nama yang sama.
2. **Pexels API**: video stok gratis, kuota 20.000 permintaan/bulan.
3. **Pixabay API**: video stok gratis.
4. **Wikimedia Commons + NASA**: foto dengan efek zoom pelan. Tidak butuh key, jadi selalu tersedia sebagai cadangan terakhir.

Selalu ada **4 video siap** di cadangan (`ready/`), diisi ulang tiap 3 jam. Jadi kalau semua sumber sedang gangguan, jadwal posting tetap jalan dari stok. Kalau posting gagal, video tetap di antrean dan dicoba lagi di jadwal berikutnya.

Topik ada di `topics.json`: 21 topik sains, masing-masing dengan 4 fakta. Topik bisa ditambah atau diedit kapan saja.

## Pemasangan di VPS (root)

```bash
cd robomorph && bash install.sh
nano /opt/robomorph/.env
```

## Mengisi .env

- **PEXELS_API_KEY**: daftar di https://www.pexels.com/api/ lalu salin key-nya.
- **PIXABAY_API_KEY**: login di https://pixabay.com/api/docs/, key-nya tampil di halaman itu.
- **FB_PAGE_ID** dan **FB_PAGE_TOKEN**:
  1. Buka https://developers.facebook.com, lalu buat aplikasi bertipe *Business*.
  2. Buka **Graph API Explorer**, pilih aplikasi tadi, dan tambahkan izin `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`. Klik *Generate Access Token*.
  3. Di Explorer, jalankan `me/accounts`. Salin `id` (ini FB_PAGE_ID) dan `access_token` (ini FB_PAGE_TOKEN) milik halaman RoboMorph.
  4. Supaya token tidak kedaluwarsa, tukar dulu dengan token jangka panjang lewat **Access Token Debugger** (tombol *Extend Access Token*), lalu jalankan `me/accounts` lagi dengan token baru itu.
- **TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID** (opsional): untuk notifikasi setiap posting atau kegagalan.

## Uji coba

```bash
cd /opt/robomorph
DRY_RUN=1 python3 robomorph.py post   # buat video, tidak diposting
python3 robomorph.py status
tail -n 30 logs/robomorph.log
```

Kalau hasilnya sudah sesuai, posting berjalan otomatis sesuai jadwal. Untuk melihat jadwal: `systemctl list-timers 'robomorph*'`.

## Tanpa VPS: GitHub Actions (sudah aktif)

Repo ini juga berjalan otomatis di GitHub Actions setiap jam 07.00, 12.00, dan 19.00 WITA, gratis untuk repo publik. Isi key dan token di **Settings → Secrets and variables → Actions → New repository secret**:

- `PIXABAY_API_KEY`
- `FB_PAGE_ID`, `FB_PAGE_TOKEN`
- opsional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

Selama `FB_PAGE_TOKEN` belum diisi, video tetap dibuat tetapi tidak diposting. Hasilnya bisa diunduh di tab **Actions → (run) → Artifacts**.

## Posting lewat Make.com (tanpa akun Meta Developer)

Kalau token Facebook tidak bisa didapat, isi secret `MAKE_WEBHOOK_URL`. Setiap jadwal, video diunggah sebagai file publik di Releases repo ini, lalu link-nya dikirim ke webhook Make bersama caption. Di Make: **Webhooks → Custom webhook** → **Facebook Pages → Publish a Reel**. Isi videonya dengan `video_url` dan deskripsinya dengan `caption`.
