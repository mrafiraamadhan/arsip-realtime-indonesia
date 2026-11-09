"""
Arsip prakiraan cuaca BMKG untuk SELURUH kabupaten/kota Indonesia (514 titik).

BMKG hanya menampilkan prakiraan TERKINI (3 hari ke depan, per 3 jam); prakiraan
lama tidak diarsipkan untuk publik. Dengan merekamnya setiap hari, kita bisa
menguji "apa yang sudah diperingatkan BMKG beberapa hari sebelum harga bergerak".

Sumber : https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=<kode>  (data terbuka BMKG,
         wajib mencantumkan BMKG sebagai sumber; batas 60 permintaan/menit/IP)
Titik  : config/titik_kabkota.csv (satu desa/kelurahan di ibukota tiap kab/kota;
         lihat tools/bangun_titik.py). Bila kode utama ditolak, kode cadangan dicoba.
Keluaran: arsip/bmkg_prakiraan/YYYY/MM/YYYY-MM-DD_HHMMZ.csv.gz (satu berkas per run)
          + satu baris ringkasan di log/<sumber>_YYYY-MM.csv

    python src/bmkg_prakiraan.py [--batas N]   # --batas untuk uji cepat N titik pertama
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umum as U  # noqa: E402

URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca"
KOLOM = ["diambil_utc", "kode_kabkota", "kabkota", "adm4", "desa", "kecamatan", "lat", "lon",
         "analysis_date", "utc_datetime", "local_datetime", "t", "hu", "tp", "weather",
         "weather_desc", "ws", "wd", "tcc", "vs_text"]


def urai(payload: dict, dasar: dict, diambil: str) -> list:
    """Ubah JSON BMKG menjadi baris per slot 3-jam. Struktur: data[0].cuaca = [[slot,...] per hari]."""
    data = payload.get("data") or []
    if not data:
        return []
    lok = payload.get("lokasi") or data[0].get("lokasi") or {}
    out = []
    for blok in data[0].get("cuaca") or []:
        for slot in (blok if isinstance(blok, list) else [blok]):
            if not isinstance(slot, dict):
                continue
            out.append({
                **dasar, "diambil_utc": diambil,
                "desa": lok.get("desa", dasar.get("desa", "")),
                "kecamatan": lok.get("kecamatan", dasar.get("kecamatan", "")),
                "lat": lok.get("lat", ""), "lon": lok.get("lon", ""),
                **{k: slot.get(k, "") for k in ["analysis_date", "utc_datetime", "local_datetime", "t", "hu",
                                                 "tp", "weather", "weather_desc", "ws", "wd", "tcc", "vs_text"]},
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batas", type=int, default=0)
    a = ap.parse_args()

    titik = list(csv.DictReader(open(os.path.join(U.ROOT, "config", "titik_kabkota.csv"), encoding="utf-8")))
    if a.batas:
        titik = titik[:a.batas]
    t0 = U.kini()
    s, lim = U.sesi(), U.Pembatas(per_menit=50)
    semua, gagal, pakai_cadangan = [], [], 0
    for w in titik:
        kode_list = [w["adm4"]] + [k for k in w.get("adm4_cadangan", "").split(";") if k]
        baris, galat = [], ""
        for i, kode in enumerate(kode_list):
            r, galat = U.ambil(s, URL, lim, params={"adm4": kode})
            if r is None:
                continue
            if r.status_code != 200:
                galat = f"HTTP {r.status_code}"
                continue
            try:
                baris = urai(r.json(), {"kode_kabkota": w["kode_kabkota"], "kabkota": w["kabkota"], "adm4": kode,
                                        "desa": w["desa"], "kecamatan": w["kecamatan"]}, U.iso(U.kini()))
            except ValueError as e:
                galat = f"JSON: {str(e)[:80]}"
            if baris:
                pakai_cadangan += i > 0
                break
            galat = galat or "data kosong"
        if baris:
            semua.extend(baris)
        else:
            gagal.append(f"{w['kode_kabkota']}({galat})")

    berkas = ""
    if semua:
        berkas = U.path_run("bmkg_prakiraan", t0, ".csv.gz")
        U.tulis_csv_gz(berkas, semua, KOLOM)
        berkas = os.path.relpath(berkas, U.ROOT)
    berhasil = len(titik) - len(gagal)
    U.catat_log(t0, "bmkg_prakiraan",
                status="ok" if not gagal else ("sebagian" if semua else "gagal"),
                diminta=len(titik), berhasil=berhasil, gagal=len(gagal), baris=len(semua), berkas=berkas,
                catatan=(f"cadangan dipakai {pakai_cadangan}; " if pakai_cadangan else "") + "; ".join(gagal)[:600])
    print("Sumber data: BMKG (Badan Meteorologi, Klimatologi, dan Geofisika).")
    return 0 if semua else 1


if __name__ == "__main__":
    raise SystemExit(main())
