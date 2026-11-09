"""
Gabungkan arsip prakiraan menjadi satu tabel untuk analisis.

    python tools/gabung.py --dari 2026-11-01 --sampai 2026-12-31 --kab 64.71,34.71 > prakiraan.csv
Tanpa --kab: semua kab/kota. Kolom `diambil_utc` = kapan prakiraan itu terlihat,
`utc_datetime` = waktu yang diprakirakan; selisih keduanya = jarak prakiraan (lead time).
"""
import argparse, glob, os, sys
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ap = argparse.ArgumentParser()
ap.add_argument("--dari", default="0000"); ap.add_argument("--sampai", default="9999")
ap.add_argument("--kab", default="")
a = ap.parse_args()
kab = set(filter(None, a.kab.split(",")))
bagian = []
for p in sorted(glob.glob(os.path.join(ROOT, "arsip", "bmkg_prakiraan", "*", "*", "*.csv.gz"))):
    tgl = os.path.basename(p)[:10]
    if a.dari <= tgl <= a.sampai:
        d = pd.read_csv(p, dtype={"kode_kabkota": str, "adm4": str})
        bagian.append(d[d["kode_kabkota"].isin(kab)] if kab else d)
if not bagian:
    sys.exit("Tidak ada berkas pada rentang itu.")
pd.concat(bagian, ignore_index=True).to_csv(sys.stdout, index=False)
