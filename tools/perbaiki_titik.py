"""
Ganti titik kab/kota yang ditolak API BMKG dengan kode wilayah Kepmendagri 2025.

Uji penuh pertama (9 Nov 2026): 488 dari 514 titik berhasil memakai kode 2022.
Yang gagal adalah kab/kota di provinsi hasil pemekaran Papua (kode 91.xx/92.xx
pindah ke 93-96) dan Kota Sabang (kode desanya berubah). BMKG sudah memakai kode
baru untuk wilayah itu. Skrip ini membaca kab/kota yang gagal dari log terakhir,
membangun titik penggantinya dari db/wilayah.sql (2025) dengan aturan yang sama,
lalu menimpa barisnya di config/titik_kabkota.csv. Titik yang sudah berhasil tidak diubah.

    python tools/perbaiki_titik.py <wilayah.sql 2025> <wilayah_level_1_2.sql> [log.csv]
"""
import csv, glob, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bangun_titik as B  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main(sql2025, sql12, log=None):
    log = log or sorted(glob.glob(os.path.join(ROOT, "log", "bmkg_prakiraan_*.csv")))[-1]
    akhir = list(csv.DictReader(open(log, encoding="utf-8")))[-1]
    gagal = set(re.findall(r"(\d\d\.\d\d)\(", akhir["catatan"]))
    p = os.path.join(ROOT, "config", "titik_kabkota.csv")
    titik = list(csv.DictReader(open(p, encoding="utf-8")))
    baru = {B.norm(r["kabkota"]): r for r in B.bangun(B.baca_wilayah_sql(sql2025), B.baca_ibukota(sql12))}
    diganti, tak_ketemu = [], []
    for i, r in enumerate(titik):
        if r["kode_kabkota"] not in gagal:
            continue
        g = baru.get(B.norm(r["kabkota"]))
        if not g:
            tak_ketemu.append(r["kabkota"])
            continue
        g = dict(g, metode="kode 2025: " + g["metode"])
        titik[i] = g
        diganti.append(f"{r['kode_kabkota']}->{g['kode_kabkota']} {g['kabkota']}: {g['desa']} ({g['adm4']})")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(titik[0].keys()))
        w.writeheader()
        w.writerows(titik)
    print(f"{len(diganti)} titik diganti, tidak ketemu: {tak_ketemu}")
    print("\n".join(diganti))


if __name__ == "__main__":
    main(*sys.argv[1:])
