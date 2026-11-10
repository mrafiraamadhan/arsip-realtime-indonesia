"""
Siapkan batas kecamatan DIY dari geoBoundaries (CC BY 4.0, https://www.geoboundaries.org)
dan cocokkan dengan daftar kecamatan SIMOTANDI (config/kecamatan_simotandi_diy.csv).

Dijalankan di GitHub Actions (geoboundaries.org tidak terjangkau dari lingkungan lain).
Keluaran: config/batas_kecamatan_diy.geojson  (properti: kode_wilayah, kabupaten, kecamatan, nama_gb)
"""
import csv, json, re, sys
import requests
from shapely.geometry import shape, mapping

UA = {"User-Agent": "arsip-realtime-indonesia (riset statistik; github.com/mrafiraamadhan)"}
ALIAS = {"keraton": "kraton", "gedongtengen": "gedongtengen", "pakualaman": "pakualaman"}


def norm(s):
    s = re.sub(r"[^a-z]", "", s.lower())
    return ALIAS.get(s, s)


def unduh(level, rilis="gbOpen"):
    r = requests.get(f"https://www.geoboundaries.org/api/current/{rilis}/IDN/{level}/", headers=UA, timeout=60)
    if r.status_code != 200 or not r.text.strip().startswith("{"):
        print(f"{rilis} {level}: HTTP {r.status_code} {r.text[:120]!r}", flush=True)
        return None
    meta = r.json()
    url = meta.get("simplifiedGeometryGeoJSON") or meta["gjDownloadURL"]
    print(rilis, level, meta.get("boundarySource"), meta.get("boundaryYearRepresented"), url, flush=True)
    return requests.get(url, headers=UA, timeout=300).json()


def unduh_hdx_adm3():
    """Cadangan: COD-AB Indonesia (BPS) di HDX, shapefile level 3 (kecamatan)."""
    import io, zipfile, shapefile
    meta = requests.get("https://data.humdata.org/api/3/action/package_show?id=cod-ab-idn", headers=UA, timeout=60).json()
    res = meta["result"]["resources"]
    for r in res:
        print("  HDX:", r.get("name"), r.get("format"), r.get("url"), flush=True)
    kandidat = [r for r in res if "shp" in (r.get("format") or "").lower() or r["url"].lower().endswith(".zip")]
    for r in kandidat:
        z = zipfile.ZipFile(io.BytesIO(requests.get(r["url"], headers=UA, timeout=600).content))
        nama = [n for n in z.namelist() if n.lower().endswith(".shp") and "adm3" in n.lower()]
        if not nama:
            continue
        b = nama[0][:-4]
        sf = shapefile.Reader(shp=io.BytesIO(z.read(b + ".shp")), dbf=io.BytesIO(z.read(b + ".dbf")))
        kol = [f[0] for f in sf.fields[1:]]
        print("  HDX adm3:", nama[0], kol, flush=True)
        fitur = []
        for sr in sf.iterShapeRecords():
            p = dict(zip(kol, sr.record))
            nama3 = p.get("ADM3_EN") or p.get("ADM3_ID") or p.get("ADM3_NAME")
            fitur.append({"geometry": sr.shape.__geo_interface__, "properties": {"shapeName": nama3, **{k: str(v) for k, v in p.items()}}})
        return {"features": fitur}
    return None


def main():
    adm1 = unduh("ADM1")
    diy = [f for f in adm1["features"] if "yogya" in f["properties"]["shapeName"].lower()]
    if len(diy) != 1:
        sys.exit(f"ADM1 DIY tidak unik: {[f['properties']['shapeName'] for f in diy]}")
    poli_diy = shape(diy[0]["geometry"]).buffer(0)
    adm3 = unduh("ADM3") or unduh("ADM3", "gbHumanitarian") or unduh_hdx_adm3()
    if not adm3:
        sys.exit("Batas ADM3 tidak tersedia dari geoBoundaries maupun HDX.")
    calon = []
    for f in adm3["features"]:
        g = shape(f["geometry"]).buffer(0)
        if g.is_empty or not poli_diy.intersects(g):
            continue
        if poli_diy.contains(g.representative_point()):
            calon.append((f["properties"]["shapeName"], g))
    print(f"{len(calon)} poligon ADM3 di dalam DIY", flush=True)
    ref = list(csv.DictReader(open("config/kecamatan_simotandi_diy.csv")))
    peta = {}
    for r in ref:
        peta.setdefault(norm(r["kecamatan"]), []).append(r)
    fitur, tak_cocok = [], []
    for nama, g in calon:
        rr = peta.get(norm(nama))
        if not rr or len(rr) > 1:
            tak_cocok.append(nama); continue
        r = rr[0]
        fitur.append({"type": "Feature", "geometry": mapping(g.simplify(0.0002)),
                      "properties": {"kode_wilayah": int(r["kode_wilayah"]), "kabupaten": r["kabupaten_kota"],
                                     "kecamatan": r["kecamatan"], "nama_gb": nama}})
    kode = {f["properties"]["kode_wilayah"] for f in fitur}
    hilang = [r["kecamatan"] for r in ref if int(r["kode_wilayah"]) not in kode]
    print(f"cocok {len(fitur)}; poligon tak cocok {tak_cocok}; kecamatan SIMOTANDI tanpa poligon {hilang}", flush=True)
    json.dump({"type": "FeatureCollection",
               "atribusi": "Batas: geoBoundaries (CC BY 4.0), www.geoboundaries.org",
               "features": fitur}, open("config/batas_kecamatan_diy.geojson", "w"))


if __name__ == "__main__":
    main()
