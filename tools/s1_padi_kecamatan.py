"""
Fase tanam padi per kecamatan DIY dari radar Sentinel-1 (versi 2, bukti-konsep "SIMOTANDI realtime").

Sumber: Sentinel-1 GRD di Microsoft Planetary Computer (tanpa akun), lintasan relatif 127
ascending (geometri sama, tiap 6-12 hari, terbit beberapa jam setelah perekaman).

Langkah:
  1. Tiap adegan digeoreferensi dari GCP lokal lalu diproyeksikan ke kisi tetap ~44 m
     (BB DIY), dibaca dari overview 1/4 supaya cepat.
  2. Piksel sawah = rentang VH sepanjang waktu (p95-p5) >= 6 dB dan berada di poligon kecamatan.
  3. Per piksel per tanggal: "basah" = VH <= p15 pikselnya + 1 dB (tergenang: olah tanah/tanam).
     Umur = hari sejak tanggal basah terakhir -> kelas umur yang sejajar dengan fase SIMOTANDI.
  4. Agregasi per kecamatan per tanggal -> CSV; peta kelas umur tanggal terakhir -> GeoTIFF.

    python tools/s1_padi_kecamatan.py [--batas N]
Keluaran:
    arsip/sentinel1_padi/kecamatan_<run>.csv   (deret per kecamatan per tanggal)
    arsip/sentinel1_padi/sawah_kecamatan.csv  (luas piksel sawah per kecamatan vs luas baku SIMOTANDI)
    arsip/sentinel1_padi/fase_terbaru.tif      (kelas umur tiap piksel sawah, tanggal terakhir)
Batas kecamatan: geoBoundaries (CC BY 4.0). Citra: Copernicus Sentinel-1 (ESA), via Planetary Computer.
"""
import argparse, csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import planetary_computer as pc
import rasterio
from affine import Affine
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_gcps, from_origin
from rasterio.warp import reproject
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

os.environ.update({"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
                   "GDAL_HTTP_MULTIPLEX": "YES", "VSI_CACHE": "TRUE", "GDAL_HTTP_MAX_RETRY": "3"})

BB = (110.00, -8.22, 110.86, -7.53)
RES = 0.0004                                   # ~44 m
NX, NY = int(round((BB[2] - BB[0]) / RES)), int(round((BB[3] - BB[1]) / RES))
GRID = from_origin(BB[0], BB[3], RES, RES)
FAK = 4                                        # piksel GRD 10 m -> baca 1/4
HA_PIKSEL = (RES * 111320 * np.cos(np.radians(7.9))) * (RES * 110574) / 1e4
KELAS = ["basah", "u01_12", "u13_36", "u37_60", "u61_84", "u85_110", "u111_140", "u_lain"]
BATAS_UMUR = [(1, 12), (13, 36), (37, 60), (61, 84), (85, 110), (111, 140)]
KELUAR = "arsip/sentinel1_padi"


def baca(href):
    with rasterio.open(href) as src:
        g, _ = src.gcps
        dekat = [q for q in g if BB[0] - 0.4 <= q.x <= BB[2] + 0.4 and BB[1] - 0.4 <= q.y <= BB[3] + 0.4]
        if len(dekat) < 4:
            return None
        T = from_gcps(dekat)
        inv = ~T
        pts = [inv * (x, y) for x, y in [(BB[0], BB[1]), (BB[0], BB[3]), (BB[2], BB[1]), (BB[2], BB[3])]]
        c0 = max(0, int(min(p[0] for p in pts)) - 40); c1 = min(src.width, int(max(p[0] for p in pts)) + 40)
        r0 = max(0, int(min(p[1] for p in pts)) - 40); r1 = min(src.height, int(max(p[1] for p in pts)) + 40)
        if c1 - c0 < 50 or r1 - r0 < 50:
            return None
        win = Window(c0, r0, c1 - c0, r1 - r0)
        data = src.read(1, window=win, out_shape=(max(1, win.height // FAK), max(1, win.width // FAK))).astype("float32")
        wt = window_transform(win, T) * Affine.scale(win.width / data.shape[1], win.height / data.shape[0])
    dst = np.zeros((NY, NX), "float32")
    reproject(data, dst, src_transform=wt, src_crs="EPSG:4326", dst_transform=GRID, dst_crs="EPSG:4326",
              src_nodata=0, dst_nodata=0, resampling=Resampling.average)
    dst[dst <= 0] = np.nan
    return (20 * np.log10(dst)).astype("float16")


def proses(it):
    tgl = it.datetime.date().isoformat()
    try:
        return tgl, baca(it.assets["vh"].href), it.id
    except Exception as e:
        return tgl, None, f"{it.id}: {type(e).__name__} {str(e)[:150]}"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--batas", type=int, default=0); a = ap.parse_args()
    os.makedirs(KELUAR, exist_ok=True)
    gj = json.load(open("config/batas_kecamatan_diy.geojson"))
    fitur = gj["features"]
    zona = rasterize([(f["geometry"], i + 1) for i, f in enumerate(fitur)], out_shape=(NY, NX),
                     transform=GRID, fill=0, dtype="int16")
    nz = len(fitur) + 1
    print(f"{len(fitur)} poligon kecamatan; kisi {NX}x{NY}; {HA_PIKSEL:.3f} ha/piksel", flush=True)

    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["sentinel-1-grd"], bbox=list(BB), datetime="2024-01-01/..",
                            query={"sat:relative_orbit": {"eq": 127}, "sat:orbit_state": {"eq": "ascending"}},
                            max_items=600).items())
    items.sort(key=lambda i: i.datetime)
    if a.batas:
        items = items[-a.batas:]
    print(f"{len(items)} item; {items[0].datetime.date()} s.d. {items[-1].datetime.date()}", flush=True)
    t0 = time.time()
    per_tgl = {}
    with ThreadPoolExecutor(8) as ex:
        for tgl, vh, info in ex.map(proses, items):
            if vh is None:
                print("  lewati", tgl, info, flush=True); continue
            per_tgl[tgl] = vh if tgl not in per_tgl else np.where(np.isnan(per_tgl[tgl]), vh, per_tgl[tgl])
    tgl = sorted(per_tgl)
    VH = np.stack([per_tgl[t] for t in tgl]); del per_tgl
    T = len(tgl)
    hari = np.array([(datetime.fromisoformat(t) - datetime(2024, 1, 1)).days for t in tgl])
    print(f"baca {time.time()-t0:.0f} dtk; {T} tanggal; stack {VH.shape} {VH.nbytes/1e9:.2f} GB", flush=True)

    # akumulator: hit[t, zona, fitur]
    F = ["n_valid", "basah_baru", "naik", "turun", "z_sum"] + KELAS
    hit = np.zeros((T, nz, len(F)), "float64")
    n_sawah = np.zeros(nz); n_zona = np.bincount(zona.ravel(), minlength=nz).astype(float)
    kelas_akhir = np.zeros((NY, NX), "uint8")       # 0 = bukan sawah, 1..8 = KELAS
    langkah = 80
    for r0 in range(0, NY, langkah):
        r1 = min(NY, r0 + langkah)
        z = zona[r0:r1].ravel()
        v = VH[:, r0:r1].reshape(T, -1).astype("float32")
        sel = z > 0
        if not sel.any():
            continue
        v = v[:, sel]; z = z[sel]
        nval = np.isfinite(v).sum(0)
        with np.errstate(all="ignore"):
            p5, p15, med, p95 = np.nanpercentile(v, [5, 15, 50, 95], axis=0)
        m = (nval >= 0.6 * T) & ((p95 - p5) >= 6)
        if not m.any():
            continue
        v = v[:, m]; z = z[m]; p15 = p15[m]; med = med[m]
        n_sawah += np.bincount(z, minlength=nz)
        basah = v <= (p15 + 1.0)
        akhir_basah = np.full(v.shape[1], -10_000)
        sebelum = np.full(v.shape[1], np.nan, "float32"); basah_sebelum = np.zeros(v.shape[1], bool)
        for t in range(T):
            ok = np.isfinite(v[t])
            b = basah[t] & ok
            umur = hari[t] - akhir_basah
            kls = np.full(v.shape[1], 7)                   # u_lain
            for j, (lo, hi) in enumerate(BATAS_UMUR):
                kls[(umur >= lo) & (umur <= hi)] = j + 1
            kls[b] = 0
            d = v[t] - sebelum
            fit = {"n_valid": ok, "basah_baru": b & ~basah_sebelum, "naik": ok & (d >= 3), "turun": ok & (d <= -3),
                   "z_sum": np.where(ok, v[t] - med, 0)}
            for j, k in enumerate(KELAS):
                fit[k] = ok & (kls == j)
            for j, k in enumerate(F):
                hit[t, :, j] += np.bincount(z, weights=fit[k].astype("float64") if k != "z_sum" else fit[k], minlength=nz)
            akhir_basah = np.where(b, hari[t], akhir_basah)
            sebelum = np.where(ok, v[t], sebelum); basah_sebelum = np.where(ok, b, basah_sebelum)
            if t == T - 1:
                blok = np.zeros((r1 - r0) * NX, "uint8")
                idx = np.flatnonzero(sel)[m]
                blok[idx] = np.where(ok, kls + 1, 0).astype("uint8")
                kelas_akhir[r0:r1] = blok.reshape(r1 - r0, NX)
    print(f"olah selesai {time.time()-t0:.0f} dtk; piksel sawah total {int(n_sawah.sum())} (~{n_sawah.sum()*HA_PIKSEL:,.0f} ha)", flush=True)

    run = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%MZ")
    out = f"{KELUAR}/kecamatan_{run}.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kode_wilayah", "kecamatan", "tanggal", "n_sawah", "n_valid", "f_basah_baru", "f_naik", "f_turun", "z_rata"]
                   + ["f_" + k for k in KELAS])
        for i, f in enumerate(fitur, start=1):
            if n_sawah[i] == 0:
                continue
            for t in range(T):
                h = hit[t, i]; nv = h[0]
                if nv == 0:
                    continue
                w.writerow([f["properties"]["kode_wilayah"], f["properties"]["kecamatan"], tgl[t], int(n_sawah[i]), int(nv),
                            round(h[1] / nv, 4), round(h[2] / nv, 4), round(h[3] / nv, 4), round(h[4] / nv, 3)]
                           + [round(x / nv, 4) for x in h[5:]])
    with open(f"{KELUAR}/sawah_kecamatan.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["kode_wilayah", "kabupaten", "kecamatan", "piksel_zona", "piksel_sawah", "ha_sawah_s1"])
        for i, f in enumerate(fitur, start=1):
            p = f["properties"]
            w.writerow([p["kode_wilayah"], p["kabupaten"], p["kecamatan"], int(n_zona[i]), int(n_sawah[i]), round(n_sawah[i] * HA_PIKSEL, 1)])
    with rasterio.open(f"{KELUAR}/fase_terbaru.tif", "w", driver="GTiff", width=NX, height=NY, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=GRID, compress="deflate", nodata=0) as dst:
        dst.write(kelas_akhir, 1)
        dst.update_tags(tanggal=tgl[-1], kelas="1=basah,2=u01_12,3=u13_36,4=u37_60,5=u61_84,6=u85_110,7=u111_140,8=u_lain",
                        sumber="Copernicus Sentinel-1 GRD via Microsoft Planetary Computer; lintasan 127 asc")
    print(f"tersimpan {out}; tanggal terakhir {tgl[-1]}", flush=True)


if __name__ == "__main__":
    main()
