"""
UJI COBA API gelombang BMKG (model InaWaves, Pusat Meteorologi Maritim).

API resmi ini (maritim.bmkg.go.id/pusmar/api23) mengirim satu grid se-Indonesia per
waktu, dalam bentuk "string JSON". Bentuk pasti grid dan ukurannya belum diketahui,
jadi skrip ini BELUM mengarsip gelombang penuh. Tugasnya: sekali sehari mencatat
jawaban /modelrun dan mencoba satu permintaan tinggi gelombang signifikan (hs),
lalu menyimpan ukuran + cuplikan awal. Satu jawaban lengkap disimpan SEKALI saja
(bila < 3 MB) sebagai bahan merancang pengambil titik perairan yang sebenarnya.

Dokumentasi: https://maritim.bmkg.go.id/pusmar/api23/arr_req  (wajib sebut BMKG)
Keluaran   : arsip/bmkg_gelombang_uji/YYYY/MM/YYYY-MM-DD_HHMMZ.json.gz  + log/<sumber>_YYYY-MM.csv
"""
import glob
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umum as U  # noqa: E402

DASAR = "https://maritim.bmkg.go.id/pusmar/api23"
BATAS_PENUH = 3_000_000


def main():
    t0 = U.kini()
    s = U.sesi()
    hasil = {"diambil_utc": U.iso(t0)}
    r, g = U.ambil(s, f"{DASAR}/modelrun", timeout=60)
    hasil["modelrun_status"] = r.status_code if r is not None else g
    teks = r.text if r is not None else ""
    hasil["modelrun_cuplikan"] = teks[:20000]
    m = re.search(r"(20\d{10})", teks)
    baserun = m.group(1) if m else ""
    hasil["baserun"] = baserun
    sudah_penuh = bool(glob.glob(os.path.join(U.ARSIP, "bmkg_gelombang_uji", "contoh_penuh_*.txt.gz")))
    if baserun:
        url = f"{DASAR}/arr_req/inawaves/hs/{baserun}/{baserun}"
        rh, gh = U.ambil(s, url, timeout=120)
        hasil.update({"hs_url": url, "hs_status": rh.status_code if rh is not None else gh,
                      "hs_bytes": len(rh.content) if rh is not None else 0,
                      "hs_cuplikan": rh.text[:3000] if rh is not None else ""})
        if rh is not None and rh.status_code == 200 and 0 < len(rh.content) < BATAS_PENUH and not sudah_penuh:
            p = os.path.join(U.ARSIP, "bmkg_gelombang_uji", f"contoh_penuh_{baserun}.txt.gz")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with gzip.GzipFile(p, "wb", mtime=0) as f:
                f.write(rh.content)
            hasil["contoh_penuh"] = os.path.relpath(p, U.ROOT)
    berkas = U.path_run("bmkg_gelombang_uji", t0, ".json.gz")
    with gzip.GzipFile(berkas, "wb", mtime=0) as f:
        f.write(json.dumps(hasil, ensure_ascii=False).encode("utf-8"))
    ok = hasil.get("hs_status") == 200
    U.catat_log(t0, "bmkg_gelombang_uji", status="ok" if ok else "gagal", diminta=2, berhasil=int(ok) + (r is not None),
                gagal=0 if ok else 1, baris=0, berkas=os.path.relpath(berkas, U.ROOT),
                catatan=f"modelrun={hasil['modelrun_status']} baserun={baserun or '-'} "
                        f"hs={hasil.get('hs_status', '-')} {hasil.get('hs_bytes', 0)} byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
