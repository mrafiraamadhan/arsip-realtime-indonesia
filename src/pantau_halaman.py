"""
Arsip halaman harga resmi yang hanya menampilkan angka TERKINI (tanpa riwayat).

Daftar halaman ada di config/halaman.csv. Untuk tiap halaman, sekali sehari:
  1. cek robots.txt situsnya -- bila melarang, halaman DILEWATI (tercatat di log);
  2. ambil halaman, kumpulkan semua angka berformat harga Indonesia
     ("12.500", "1.234.000", "Rp 21.369,60") sebagai "sidik harga";
  3. bila sidik harga BERUBAH dari snapshot terakhir (atau sudah 7 hari tanpa
     snapshot), simpan HTML aslinya + daftar angkanya. Bila tidak berubah, hanya
     log yang bertambah. Jadi arsip kecil, tetapi setiap perubahan harga tertangkap.

Halaman disimpan MENTAH; pengurai per situs bisa dibuat belakangan dari arsip ini.
Bila sidik harga kosong (n_angka=0), kemungkinan angkanya dimuat lewat JavaScript
-- itu tanda perlu mencari API di balik halaman tersebut.

Keluaran: arsip/halaman/<id>/YYYY/MM/YYYY-MM-DD_HHMMZ.html.gz (+ .json berisi angka)
          arsip/halaman/<id>/terakhir.json   (sidik & waktu snapshot terakhir)
          log/halaman_YYYY-MM.csv            (satu baris per halaman per run)
"""
import csv
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from urllib import robotparser
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umum as U  # noqa: E402

POLA_HARGA = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?(?![\d.,]*\d)")
DETAK_HARI = 7


def boleh(s, url: str):
    """Hormati robots.txt. Gagal mengambil robots.txt (404 dsb.) = boleh."""
    p = urlsplit(url)
    r, _ = U.ambil(s, f"{p.scheme}://{p.netloc}/robots.txt", coba=1, timeout=15)
    if r is None or r.status_code != 200:
        return True, "robots.txt tidak ada/terbaca"
    rp = robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    ok = rp.can_fetch(U.USER_AGENT, url)
    return ok, "diizinkan robots.txt" if ok else "DILARANG robots.txt"


def main():
    t0 = U.kini()
    s = U.sesi()
    s.headers.update({"Accept-Language": "id-ID,id;q=0.9,en;q=0.6"})
    halaman = list(csv.DictReader(open(os.path.join(U.ROOT, "config", "halaman.csv"), encoding="utf-8")))
    log = []
    for h in halaman:
        if h.get("aktif", "1").strip() != "1":
            continue
        hid, url = h["id"], h["url"]
        dasar = os.path.join(U.ARSIP, "halaman", hid)
        baris = {"run_utc": U.iso(t0), "id": hid, "url": url}
        ok, alasan = boleh(s, url)
        if not ok:
            log.append({**baris, "status": "dilewati", "catatan": alasan})
            continue
        r, galat = U.ambil(s, url, timeout=45)
        if r is None or r.status_code != 200:
            log.append({**baris, "status": "gagal", "http": getattr(r, "status_code", ""), "catatan": galat})
            continue
        html = r.text
        angka = sorted(set(POLA_HARGA.findall(html)))
        sidik = hashlib.sha1("|".join(angka).encode()).hexdigest()[:16]
        p_akhir = os.path.join(dasar, "terakhir.json")
        akhir = json.load(open(p_akhir)) if os.path.isfile(p_akhir) else {}
        lama = (t0 - datetime.fromisoformat(akhir["waktu"])).days if akhir.get("waktu") else 999
        berubah = sidik != akhir.get("sidik")
        simpan = berubah or lama >= DETAK_HARI
        berkas = ""
        if simpan:
            p = os.path.join(dasar, t0.strftime("%Y"), t0.strftime("%m"), t0.strftime("%Y-%m-%d_%H%MZ"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with gzip.GzipFile(p + ".html.gz", "wb", mtime=0) as f:
                f.write(r.content)
            json.dump({"url": url, "diambil_utc": U.iso(t0), "http": r.status_code, "n_angka": len(angka),
                       "sidik": sidik, "angka": angka}, open(p + ".json", "w"), ensure_ascii=False, indent=0)
            json.dump({"sidik": sidik, "waktu": U.iso(t0)}, open(p_akhir, "w"))
            berkas = os.path.relpath(p + ".html.gz", U.ROOT)
        log.append({**baris, "status": "ok", "http": r.status_code, "bytes": len(r.content), "n_angka": len(angka),
                    "sidik": sidik, "berubah": berubah, "disimpan": simpan, "berkas": berkas,
                    "catatan": ("angka kosong: mungkin dimuat JavaScript; " if not angka else "") + alasan})
    U.tambah_csv(os.path.join(U.LOG, f"halaman_{t0:%Y-%m}.csv"), log,
                 ["run_utc", "id", "url", "status", "http", "bytes", "n_angka", "sidik", "berubah", "disimpan",
                  "berkas", "catatan"])
    for b in log:
        print(f"[halaman] {b['id']}: {b['status']} n_angka={b.get('n_angka', '-')} "
              f"berubah={b.get('berubah', '-')} {b.get('catatan', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
