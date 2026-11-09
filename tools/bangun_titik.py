"""
Bangun config/titik_kabkota.csv: SATU titik desa/kelurahan (kode adm4) per
kabupaten/kota se-Indonesia untuk API prakiraan cuaca BMKG.

Sumber (keduanya MIT, data dasarnya kode wilayah Kemendagri):
  * base.csv dari github.com/revomkg/mcp-bmkg  -> daftar kode adm4 Kepmendagri 2022,
    yaitu edisi yang dipakai API BMKG (dicek: kode SIGAP DIY ada di daftar ini).
  * db/wilayah_level_1_2.sql dari github.com/cahyadsn/wilayah -> nama ibukota
    kabupaten/kota (Kepmendagri 2025), dicocokkan lewat NAMA kab/kota.

Aturan memilih titik (dicatat di kolom `metode`):
  1. kecamatan yang namanya = ibukota; di dalamnya desa/kelurahan bernama sama,
     kalau tidak ada kelurahan pertama (kode 1xxx), kalau tidak ada desa pertama;
  2. kalau tak ada kecamatan cocok: desa/kelurahan bernama = ibukota;
  3. kalau masih tak ada: kelurahan pertama di kecamatan dengan kelurahan terbanyak
     (biasanya pusat kota); terakhir desa pertama.
  Dua kode cadangan (desa lain di kab/kota yang sama) dipakai otomatis bila kode
  utama ditolak API. Titik DIY memakai kode yang sudah diverifikasi di SIGAP.

    python tools/bangun_titik.py <base.csv> <wilayah_level_1_2.sql>
"""
import csv, re, sys, collections, difflib

VERIFIKASI_SIGAP = {  # kab/kota DIY: kode yang sudah diuji di repo sigap-pangan-diy
    "34.71": "34.71.10.1001", "34.04": "34.04.13.2002", "34.02": "34.02.08.2003",
    "34.01": "34.01.02.1008", "34.03": "34.03.01.2001",
}


def norm(s):
    s = " " + s.upper().replace(".", ". ") + " "
    for a, b in [(" KABUPATEN ", " "), (" KAB. ", " "), (" KAB ", " "), (" KOTA ", " "),
                 (" ADMINISTRASI ", " "), (" ADM. ", " "), (" KEP. ", " KEPULAUAN "), (" DAN ", " ")]:
        s = s.replace(a, b)
    return re.sub(r"[^A-Z0-9]", "", s)


def main(base_csv, sql):
    rows = [r for r in csv.reader(open(base_csv, encoding="utf-8")) if r and r[0][:1].isdigit()]
    nama = {r[0]: r[1].strip() for r in rows}
    prov = {k: v for k, v in nama.items() if k.count(".") == 0}
    kab = {k: v for k, v in nama.items() if k.count(".") == 1}
    kec = collections.defaultdict(list)
    desa = collections.defaultdict(list)
    for k in nama:
        if k.count(".") == 2:
            kec[k[:5]].append(k)
        elif k.count(".") == 3:
            desa[k[:8]].append(k)

    txt = open(sql, encoding="utf-8").read()
    ibukota = {}
    for kode, nm, ibk in re.findall(r"\('(\d\d\.\d\d)','([^']*)','([^']*)'", txt):
        ibukota[norm(nm)] = ibk.strip()

    out = []
    for k, nm in sorted(kab.items()):
        ibk = ibukota.get(norm(nm), "")
        if not ibk:  # nama sedikit berbeda antar-edisi (mis. Padang Sidempuan/Padangsidimpuan)
            mirip = difflib.get_close_matches(norm(nm), list(ibukota), n=1, cutoff=0.8)
            ibk = ibukota[mirip[0]] if mirip else ""
        n_ibk = norm(ibk)
        semua_desa = [d for c in sorted(kec[k]) for d in sorted(desa[c])]
        pilih, metode = None, ""
        if k in VERIFIKASI_SIGAP:
            pilih, metode = VERIFIKASI_SIGAP[k], "verifikasi SIGAP"
        if not pilih and n_ibk:
            cocok_kec = [c for c in sorted(kec[k]) if norm(nama[c]) == n_ibk]
            if cocok_kec:
                ds = sorted(desa[cocok_kec[0]])
                sama = [d for d in ds if norm(nama[d]) == n_ibk]
                kel = [d for d in ds if d.split(".")[3].startswith("1")]
                if sama:
                    pilih, metode = sama[0], "kecamatan & desa = ibukota"
                elif kel:
                    pilih, metode = kel[0], "kecamatan = ibukota, kelurahan pertama"
                elif ds:
                    pilih, metode = ds[0], "kecamatan = ibukota, desa pertama"
            if not pilih:
                sama = [d for d in semua_desa if norm(nama[d]) == n_ibk]
                if sama:
                    pilih, metode = sama[0], "desa = ibukota"
        if not pilih:
            per_kec = sorted(kec[k], key=lambda c: -sum(d.split(".")[3].startswith("1") for d in desa[c]))
            for c in per_kec:
                kel = [d for d in sorted(desa[c]) if d.split(".")[3].startswith("1")]
                if kel:
                    pilih, metode = kel[0], "kelurahan pertama di kecamatan paling urban"
                    break
        if not pilih and semua_desa:
            pilih, metode = semua_desa[0], "desa pertama (cadangan terakhir)"
        if not pilih:
            continue
        cad = [d for d in sorted(desa[pilih[:8]]) if d != pilih][:1] + \
              [d for d in semua_desa if d[:8] != pilih[:8]][:1]
        out.append({
            "kode_kabkota": k, "kabkota": nm, "provinsi": prov.get(k[:2], ""),
            "ibukota": ibk, "adm4": pilih, "desa": nama[pilih],
            "kecamatan": nama.get(pilih[:8], ""), "metode": metode,
            "adm4_cadangan": ";".join(cad),
        })
    with open("config/titik_kabkota.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(len(out), "kab/kota;", collections.Counter(r["metode"] for r in out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
