"""
Uji bukti-konsep: apakah sinyal padi terbaca dari radar Sentinel-1 di DIY?

Untuk 9 kecamatan lumbung padi DIY, ambil deret waktu Sentinel-1 GRD (lintasan
relatif 127, ascending -- geometri sama tiap 6/12 hari) sejak Jan 2024 dari
Microsoft Planetary Computer (tanpa akun), lalu hitung per tanggal:
  - median backscatter VH/VV (dB relatif, dari amplitudo GRD) di piksel "padi"
  - frac_genang: porsi piksel padi yang sedang tergenang (VH dekat titik terendahnya)
  - frac_tinggi: porsi piksel padi dengan VH tinggi (kanopi rapat)
Piksel "padi" = piksel dengan rentang VH sepanjang waktu >= 6 dB (sawah berulang
digenangi lalu tumbuh; atap & pohon nyaris datar). Hasil dibandingkan dengan fase
SIMOTANDI di luar skrip ini.

    python tools/uji_sentinel1_padi.py [--batas N]
Keluaran: arsip/sentinel1_uji/padi_<waktu>.csv
"""
import argparse, csv, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import numpy as np
import planetary_computer as pc
import rasterio
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.transform import from_origin

os.environ.update({"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
                   "GDAL_HTTP_MULTIPLEX": "YES", "VSI_CACHE": "TRUE", "GDAL_HTTP_MAX_RETRY": "3"})
KEC = {"Galur": (-7.945, 110.205), "Panjatan": (-7.905, 110.150), "Sentolo": (-7.840, 110.215),
       "Sanden": (-7.980, 110.265), "Pandak": (-7.920, 110.300), "Moyudan": (-7.775, 110.245),
       "Minggir": (-7.735, 110.250), "Seyegan": (-7.725, 110.305), "Godean": (-7.770, 110.290)}
H = 0.018                     # setengah sisi kotak (~2 km)
RES = 0.0002                  # ~22 m
BB = (110.10, -8.02, 110.35, -7.69)
NX, NY = int(round((BB[2] - BB[0]) / RES)), int(round((BB[3] - BB[1]) / RES))


GRID = from_origin(BB[0], BB[3], RES, RES)


def baca(href):
    # Proyeksikan citra (geometri radar + GCP) langsung ke kisi tetap DIY, supaya
    # piksel antartanggal sejajar dan bisa ditumpuk menjadi deret waktu.
    with rasterio.open(href) as src, WarpedVRT(src, crs="EPSG:4326", transform=GRID, width=NX, height=NY,
                                               src_nodata=0, nodata=0, resampling=Resampling.average) as vrt:
        a = vrt.read(1).astype("float32")
    a[a <= 0] = np.nan
    return 20 * np.log10(a)


def proses(it):
    try:
        return it.datetime.date().isoformat(), baca(it.assets["vh"].href), baca(it.assets["vv"].href), ""
    except Exception as e:
        return it.datetime.date().isoformat(), None, None, f"{type(e).__name__}: {str(e)[:120]}"


def diagnosa(it):
    href = it.assets["vh"].href
    with rasterio.open(href) as src:
        g, gcrs = src.gcps
        print(f"DIAG {it.id}: {src.width}x{src.height} dtype={src.dtypes[0]} crs={src.crs} "
              f"transform={tuple(round(v, 6) for v in src.transform)[:6]} n_gcp={len(g)} gcp_crs={gcrs} "
              f"overviews={src.overviews(1)}", flush=True)
        if g:
            xs = [p.x for p in g]; ys = [p.y for p in g]
            print(f"DIAG gcp lon {min(xs):.3f}..{max(xs):.3f} lat {min(ys):.3f}..{max(ys):.3f}", flush=True)
        a = src.read(1, out_shape=(src.height // 32, src.width // 32))
        print(f"DIAG baca kasar: min={a.min()} max={a.max()} nol={np.mean(a == 0):.0%}", flush=True)
    print("DIAG aset:", {k: v.href.split('?')[0][-60:] for k, v in it.assets.items()}, flush=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--batas", type=int, default=0); a = ap.parse_args()
    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["sentinel-1-grd"], bbox=list(BB), datetime="2024-01-01/..",
                            query={"sat:relative_orbit": {"eq": 127}, "sat:orbit_state": {"eq": "ascending"}},
                            max_items=400).items())
    items.sort(key=lambda i: i.datetime)
    if a.batas:
        items = items[-a.batas:]
    try:
        diagnosa(items[-1])
    except Exception as e:
        print("DIAG gagal:", type(e).__name__, str(e)[:200], flush=True)
    print(f"{len(items)} item lintasan 127 ascending; {items[0].datetime.date()} s.d. {items[-1].datetime.date()}", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        hasil = list(ex.map(proses, items))
    print(f"baca selesai {time.time()-t0:.0f} dtk", flush=True)
    # gabung irisan pada tanggal yang sama (ambil nilai non-NaN)
    per_tgl = {}
    for tgl, vh, vv, g in hasil:
        if vh is None:
            print("  gagal", tgl, g); continue
        if tgl in per_tgl:
            pvh, pvv = per_tgl[tgl]; per_tgl[tgl] = (np.where(np.isnan(pvh), vh, pvh), np.where(np.isnan(pvv), vv, pvv))
        else:
            per_tgl[tgl] = (vh, vv)
    tgl = sorted(per_tgl)
    if not tgl:
        sys.exit("Tidak ada tanggal yang berhasil dibaca.")
    VH = np.stack([per_tgl[t][0] for t in tgl]); VV = np.stack([per_tgl[t][1] for t in tgl])
    print(f"{len(tgl)} tanggal unik; ukuran stack {VH.shape}", flush=True)
    baris = []
    for nama, (lat, lon) in KEC.items():
        x0 = int((lon - H - BB[0]) / RES); x1 = int((lon + H - BB[0]) / RES)
        y0 = int((BB[3] - (lat + H)) / RES); y1 = int((BB[3] - (lat - H)) / RES)
        vh = VH[:, y0:y1, x0:x1].reshape(len(tgl), -1); vv = VV[:, y0:y1, x0:x1].reshape(len(tgl), -1)
        p5, p95 = np.nanpercentile(vh, 5, axis=0), np.nanpercentile(vh, 95, axis=0)
        mask = (p95 - p5) >= 6
        lo = np.nanpercentile(vh[:, mask], 10, axis=0) if mask.any() else None
        hi = np.nanpercentile(vh[:, mask], 75, axis=0) if mask.any() else None
        print(f"  {nama}: {mask.sum()} dari {mask.size} piksel ditandai padi ({mask.mean():.0%})", flush=True)
        for k, t in enumerate(tgl):
            v = vh[k, mask]
            ok = ~np.isnan(v)
            baris.append({"kecamatan": nama, "tanggal": t, "n_padi": int(mask.sum()), "n_valid": int(ok.sum()),
                          "median_vh": round(float(np.nanmedian(v)), 3) if ok.any() else "",
                          "median_vv": round(float(np.nanmedian(vv[k, mask])), 3) if ok.any() else "",
                          "frac_genang": round(float(np.mean(v[ok] <= lo[ok] + 1.0)), 4) if ok.any() else "",
                          "frac_tinggi": round(float(np.mean(v[ok] >= hi[ok])), 4) if ok.any() else ""})
    kini = datetime.now(timezone.utc)
    p = os.path.join("arsip", "sentinel1_uji", f"padi_{kini:%Y-%m-%d_%H%MZ}.csv")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(baris[0].keys())); w.writeheader(); w.writerows(baris)
    print(f"tersimpan {p}: {len(baris)} baris", flush=True)


if __name__ == "__main__":
    main()
