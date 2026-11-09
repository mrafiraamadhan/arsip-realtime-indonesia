# Arsip Realtime Indonesia

Repo ini merekam data resmi yang **hanya tersedia "hari ini"**, yaitu data yang hilang dari situs sumbernya begitu diperbarui. Sumbernya tidak menyimpan riwayatnya untuk publik, jadi satu-satunya cara punya riwayat adalah merekamnya sendiri setiap hari. Cakupannya seluruh Indonesia, untuk bahan lomba dan riset kebijakan di provinsi mana pun.

Semua berkas arsip **hanya bertambah**. Satu run menghasilkan satu berkas baru, dan berkas lama tidak pernah ditimpa.

## Yang direkam

| Sumber | Isi | Cakupan | Frekuensi | Lokasi |
|---|---|---|---|---|
| BMKG, prakiraan cuaca | Prakiraan 3 hari per 3 jam: suhu, kelembapan, hujan (mm), cuaca, angin, tutupan awan | 514 kab/kota, satu desa/kelurahan di ibukota masing-masing | 2× sehari (06.17 & 18.17 WIB) | `arsip/bmkg_prakiraan/` |
| BMKG, peringatan dini cuaca (nowcast) | Setiap peringatan: waktu berlaku, tingkat bahaya, kecamatan terdampak, teks lengkap | Seluruh provinsi | Tiap 30 menit | `arsip/bmkg_nowcast/` |
| BMKG, gelombang (InaWaves) | **Masih uji coba.** Mencatat format dan ukuran jawaban API untuk merancang pengambil per perairan | Seluruh perairan | 1× sehari | `arsip/bmkg_gelombang_uji/` |
| Halaman harga resmi (`config/halaman.csv`) | Harga BBM Pertamina per provinsi, BBM Shell, emas Antam & buyback. HTML asli disimpan setiap kali angka harganya berubah | Nasional / per provinsi | 1× sehari | `arsip/halaman/<id>/` |
| Tiket pesawat (Google Flights lewat SerpApi) | Semua opsi penerbangan, plus harga terendah dan rentang wajar versi Google, untuk keberangkatan H+7 dan H+30 | 4 rute: Jakarta–Medan, Yogyakarta, Balikpapan, Makassar (`config/rute_pesawat.csv`) | 1× sehari, **aktif setelah secret `SERPAPI_KEY` dipasang** | `arsip/tiket_pesawat/` |

Perekam halaman **mematuhi robots.txt**: halaman yang dilarang situsnya dilewati dan tercatat di log.

**Sengaja tidak direkam.** Sumber yang riwayatnya bisa diunduh ulang kapan saja tidak direkam di sini:
- PIHPS BI
- SP2KP Kemendag
- Panel Harga Bapanas
- kurs JISDOR
- curah hujan historis (ERA5/Open-Meteo)
- Google Trends (rentang lampau bisa diminta ulang)

Setiap run juga menulis satu baris ringkasan di `log/<sumber>_YYYY-MM.csv`, berisi jumlah berhasil dan gagal beserta alasannya. Jadi "tidak ada data baru" bisa dibedakan dari "pengambilan gagal".

**Sumber data: BMKG (Badan Meteorologi, Klimatologi, dan Geofisika).** Mencantumkan BMKG sebagai sumber adalah syarat penggunaan data terbukanya.

## Cara memasang (sekali saja)

1. Buat repo **publik** baru di GitHub, misalnya `arsip-realtime-indonesia`. Gunakan repo publik karena Actions di repo publik gratis tanpa batas menit. Di repo privat, jatah gratis 2.000 menit/bulan tidak cukup untuk nowcast tiap 30 menit.
2. Ekstrak zip, lalu unggah **seluruh isinya**, termasuk folder `.github`, lewat *Add file → Upload files → Commit changes*.
   - Di Mac, folder yang diawali titik tersembunyi di Finder. Tekan `Cmd+Shift+.` untuk menampilkannya.
   - Bisa juga buat dua berkas workflow lewat *Add file → Create new file* dengan nama `.github/workflows/arsip-nowcast.yml` dan `.github/workflows/arsip-harian.yml`, lalu tempel isinya.
3. Buka *Settings → Actions → General → Workflow permissions*, lalu pilih **Read and write permissions** dan *Save*.
4. Uji sekali: tab *Actions* → **Arsip prakiraan cuaca BMKG…** → *Run workflow*, isi `5`. Setelah 1–2 menit, harus muncul folder `arsip/` dan `log/`.
5. Selesai. Mulai saat itu jadwal berjalan otomatis.

GitHub menjalankan jadwal "sebisanya". Pada jam sibuk run bisa terlambat 5–30 menit atau sesekali terlewat, dan setiap run tercatat di `log/`.

## Cara membaca data

```bash
# prakiraan Samarinda & Kota Yogyakarta selama November
python tools/gabung.py --dari 2026-11-01 --sampai 2026-11-30 --kab 64.72,34.71 > prakiraan.csv
```

- `diambil_utc` adalah waktu prakiraan itu terlihat. `utc_datetime` adalah waktu yang diprakirakan. Selisih keduanya adalah jarak prakiraan, yang dipakai untuk menguji "berapa hari sebelumnya BMKG sudah memperingatkan".
- **Nowcast.** `ringkasan_YYYY-MM.csv` berisi satu baris per peringatan. Berkas `.jsonl.gz` menyimpan dokumen CAP aslinya, utuh kecuali isi poligon kecamatan yang diganti jumlah titiknya. Poligon itu sama dengan batas kecamatan yang tersedia di data batas wilayah, dan ukurannya bisa puluhan KB per peringatan.
- **Daftar titik.** Daftar titik ada di `config/titik_kabkota.csv`, dan kolom `metode` mencatat cara tiap titik dipilih. Kode wilayah mengikuti Kepmendagri 2022 karena edisi itu yang dipakai API BMKG. Bila kode utama ditolak, kode cadangan dipakai otomatis dan tercatat di log. Untuk membangun ulang, jalankan `tools/bangun_titik.py`.

## Perkiraan ukuran

- Prakiraan: ±12 ribu baris per run, sekitar 150–180 KB terkompresi, atau kurang dari 140 MB per tahun.
- Nowcast: kecil, tergantung jumlah peringatan.
- Ukuran ini aman untuk repo GitHub selama bertahun-tahun.

## Rencana berikutnya

- **Mengaktifkan tiket pesawat.** Daftar gratis di serpapi.com, lalu simpan kuncinya sebagai secret `SERPAPI_KEY` di *Settings → Secrets and variables → Actions*. Kuotanya 250 pencarian/bulan, dan skrip berhenti sendiri di 240.
  - Kunci tidak pernah ditulis ke berkas.
  - Pesan galat yang memuat kunci disensor sebelum disimpan.
- **Gelombang per perairan.** Dibangun setelah uji coba InaWaves menunjukkan format grid-nya.
- **Pengurai halaman harga.** Dibuat dari arsip HTML yang sudah terkumpul. Halaman dengan `n_angka=0` di log kemungkinan memuat angkanya lewat JavaScript, jadi perlu dicari API di baliknya.
- **Calon sumber berikutnya.** Harga avtur per bandara (Pertamina, berubah tiap awal bulan) dan stok/harga di pasar induk.
