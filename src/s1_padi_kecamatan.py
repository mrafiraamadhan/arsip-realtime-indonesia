"""
Fase tanam padi per kecamatan DIY dari radar Sentinel-1 (versi 2, bukti-konsep "SIMOTANDI realtime").

Sumber: Sentinel-1 GRD di Microsoft Planetary Computer (tanpa akun), lintasan relatif 127
ascending (geometri sama, tiap 6-12 hari, terbit beberapa jam setelah perekaman).

Langkah:
  1. Tiap adegan dikalibrasi ke sigma0 dan dibersihkan dari noise termal memakai LUT anotasi
     produk (penting: lantai noise S1A ~4x S1C), digeoreferensi dari GCP lokal, lalu diproyeksikan
     ke kisi tetap ~44 m (BB DIY); dibaca dari overview 1/4 supaya cepat.
  2. Piksel sawah = rentang VH sepanjang waktu (p95-p5) >= 6 dB dan berada di poligon kecamatan.
  3. Per piksel per tanggal: "basah" = VH <= p15 pikselnya + 1 dB (tergenang: olah tanah/tanam).
     Umur = hari sejak tanggal basah terakhir -> kelas umur yang sejajar dengan fase SIMOTANDI.
  4. Agregasi per kecamatan per tanggal -> CSV; peta kelas umur tanggal terakhir -> GeoTIFF.

Dua mode:
    python src/s1_padi_kecamatan.py --mode rujukan   # seluruh riwayat sejak Jan 2024 (sesekali)
        -> statistik per piksel dibekukan di rujukan_piksel.tif (p5,p15,p50,p95 dB x100) + deret lengkap
    python src/s1_padi_kecamatan.py                  # harian: hanya ~220 hari terakhir
        -> memakai rujukan beku, menambah tanggal baru ke deret tanpa mengubah angka lama
Keluaran (arsip/sentinel1_padi/):
    kecamatan_deret.csv   deret per kecamatan per tanggal (stabil, tambah-saja)
    sawah_kecamatan.csv   luas piksel sawah per kecamatan (mode rujukan)
    rujukan_piksel.tif    statistik beku per piksel sawah (mode rujukan)
    fase_terbaru.tif      kelas umur tiap piksel sawah pada tanggal terakhir
    offset_tanggal.csv    diagnostik radiometrik per tanggal
Batas kecamatan: geoBoundaries (CC BY 4.0). Citra: Copernicus Sentinel-1 (ESA), via Planetary Computer.
"""
import argparse, csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

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


def _vektor(root, daftar, nilai):
    """Ambil vektor LUT: list (line, pixel[], nilai[])."""
    out = []
    for v in root.find(daftar):
        out.append((int(v.findtext("line")), np.array(v.findtext("pixel").split(), float),
                    np.array(v.findtext(nilai).split(), float)))
    return out


def _grid_lut(vek, baris, kolom):
    """Interpolasi bilinear LUT (vektor per-baris dengan sampel piksel) ke kisi baris x kolom."""
    vek = sorted(vek, key=lambda x: x[0])
    lines = np.array([v[0] for v in vek], float)
    V = np.stack([np.interp(kolom, v[1], v[2]) for v in vek])          # nvek x ncol
    j = np.clip(np.searchsorted(lines, baris, side="right") - 1, 0, len(lines) - 2) if len(lines) > 1 else np.zeros(len(baris), int)
    if len(lines) == 1:
        return np.repeat(V, len(baris), 0)
    w = np.clip((baris - lines[j]) / np.maximum(lines[j + 1] - lines[j], 1), 0, 1)[:, None]
    return V[j] * (1 - w) + V[j + 1] * w


def kalibrasi(it, pol, baris, kolom):
    """sigmaNought^2 dan noise (DN^2) pada titik (baris, kolom) resolusi penuh, dari anotasi produk."""
    import requests, xml.etree.ElementTree as ET
    cal = ET.fromstring(requests.get(it.assets[f"schema-calibration-{pol}"].href, timeout=120).content)
    A = _grid_lut(_vektor(cal, "calibrationVectorList", "sigmaNought"), baris, kolom)
    noi = ET.fromstring(requests.get(it.assets[f"schema-noise-{pol}"].href, timeout=120).content)
    daftar = "noiseRangeVectorList" if noi.find("noiseRangeVectorList") is not None else "noiseVectorList"
    nilai = "noiseRangeLut" if daftar == "noiseRangeVectorList" else "noiseLut"
    N = _grid_lut(_vektor(noi, daftar, nilai), baris, kolom)
    az = np.ones_like(N)
    if noi.find("noiseAzimuthVectorList") is not None:
        for b in noi.find("noiseAzimuthVectorList"):
            a0, a1 = int(b.findtext("firstAzimuthLine")), int(b.findtext("lastAzimuthLine"))
            r0, r1 = int(b.findtext("firstRangeSample")), int(b.findtext("lastRangeSample"))
            lin = np.array(b.findtext("line").split(), float); lut = np.array(b.findtext("noiseAzimuthLut").split(), float)
            bi = (baris >= a0) & (baris <= a1); ki = (kolom >= r0) & (kolom <= r1)
            if bi.any() and ki.any():
                az[np.ix_(bi, ki)] = np.interp(baris[bi], lin, lut)[:, None] if len(lin) > 1 else lut[0]
    return A * A, N * az


def baca(it, pol="vh"):
    """Sigma0 (dB) terkalibrasi & bebas noise termal, diproyeksikan ke kisi tetap.
    GRD hanya punya GCP: bentuk afin lokal dari GCP sekitar DIY, baca jendela pada overview 1/4."""
    with rasterio.open(it.assets[pol].href) as src:
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
    h, w = data.shape
    baris = r0 + (np.arange(h) + 0.5) * win.height / h
    kolom = c0 + (np.arange(w) + 0.5) * win.width / w
    A2, N = kalibrasi(it, pol, baris, kolom)
    valid = data > 0
    s0 = np.where(valid, np.maximum(data * data - N, 0) / A2, 0).astype("float32")
    s0[valid] = np.maximum(s0[valid], 10 ** (-3.5))       # lantai -35 dB untuk piksel di bawah noise
    dst = np.zeros((NY, NX), "float32")
    reproject(s0, dst, src_transform=wt, src_crs="EPSG:4326", dst_transform=GRID, dst_crs="EPSG:4326",
              src_nodata=0, dst_nodata=0, resampling=Resampling.average)
    dst[dst <= 0] = np.nan
    return (10 * np.log10(dst)).astype("float16")


def proses(it):
    tgl = it.datetime.date().isoformat()
    try:
        return tgl, baca(it, "vh"), it.properties.get("platform", "?").upper()
    except Exception as e:
        return tgl, None, f"{it.id}: {type(e).__name__} {str(e)[:150]}"


def normalisasi(VH, tgl, platform, terapkan=False):
    """DN GRD belum terkalibrasi; tiap satelit (S1A/S1C/S1D) dan tanggal bisa bergeser beberapa dB.
    Koreksi per tanggal memakai target stabil (piksel dengan simpangan waktu terkecil, umumnya atap/beton):
    offset_t = median(VH_t - median_waktu) di piksel stabil, lalu VH_t -= offset_t."""
    sub = VH[:, ::4, ::4].astype("float32").reshape(VH.shape[0], -1)
    ok = np.isfinite(sub).sum(0) >= 0.8 * VH.shape[0]
    sub = sub[:, ok]
    with np.errstate(all="ignore"):
        med = np.nanmedian(sub, 0); sd = np.nanstd(sub, 0)
    ref = sd <= np.nanpercentile(sd, 10)
    off = np.array([np.nanmedian(sub[t, ref] - med[ref]) for t in range(VH.shape[0])])
    off = np.nan_to_num(off)
    if terapkan:
        for t in range(VH.shape[0]):
            VH[t] = (VH[t].astype("float32") - off[t]).astype("float16")
    with open(f"{KELUAR}/offset_tanggal.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["tanggal", "platform", "offset_db", "n_ref"])
        for t in range(len(tgl)):
            w.writerow([tgl[t], platform.get(tgl[t], "?"), round(float(off[t]), 3), int(ref.sum())])
    per = {}
    for t in range(len(tgl)):
        per.setdefault(platform.get(tgl[t], "?"), []).append(off[t])
    print("offset per satelit (median dB):", {k: round(float(np.median(v)), 2) for k, v in per.items()},
          "| rentang", round(float(off.min()), 2), "s.d.", round(float(off.max()), 2), flush=True)


LOOKBACK = 150          # hari riwayat minimum agar kelas umur (<=140 hari) lengkap
DERET = f"{KELUAR}/kecamatan_deret.csv"
RUJUKAN = f"{KELUAR}/rujukan_piksel.tif"
KOLOM = (["kode_wilayah", "kecamatan", "tanggal", "platform", "n_sawah", "n_valid", "f_basah_baru", "f_naik", "f_turun",
          "z_rata", "vh_db"] + ["f_" + k for k in KELAS])


def cari_item(mulai):
    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["sentinel-1-grd"], bbox=list(BB), datetime=f"{mulai}/..",
                            query={"sat:relative_orbit": {"eq": 127}, "sat:orbit_state": {"eq": "ascending"}},
                            max_items=800).items())
    return sorted(items, key=lambda i: i.datetime)


def baca_stack(items):
    per_tgl, platform = {}, {}
    with ThreadPoolExecutor(8) as ex:
        for tgl, vh, info in ex.map(proses, items):
            if vh is None:
                print("  lewati", tgl, info, flush=True); continue
            platform[tgl] = info
            per_tgl[tgl] = vh if tgl not in per_tgl else np.where(np.isnan(per_tgl[tgl]), vh, per_tgl[tgl])
    tgl = sorted(per_tgl)
    return np.stack([per_tgl[t] for t in tgl]), tgl, platform


def hitung_rujukan(VH, zona):
    T = VH.shape[0]
    st = np.full((4, NY, NX), np.nan, "float32")
    for r0 in range(0, NY, 80):
        r1 = min(NY, r0 + 80)
        v = VH[:, r0:r1].astype("float32")
        nval = np.isfinite(v).sum(0)
        with np.errstate(all="ignore"):
            q = np.nanpercentile(v, [5, 15, 50, 95], axis=0)
        m = (nval >= 0.6 * T) & ((q[3] - q[0]) >= 6) & (zona[r0:r1] > 0)
        st[:, r0:r1] = np.where(m, q, np.nan)
    with rasterio.open(RUJUKAN, "w", driver="GTiff", width=NX, height=NY, count=4, dtype="int16", crs="EPSG:4326",
                       transform=GRID, compress="deflate", predictor=2, nodata=-32768) as dst:
        dst.write(np.where(np.isfinite(st), np.round(st * 100), -32768).astype("int16"))
        dst.update_tags(band="1=p5,2=p15,3=p50,4=p95 (dB x100)", jumlah_tanggal=str(T),
                        kriteria="tervalidasi >=60% tanggal, rentang p95-p5 >= 6 dB, di dalam poligon kecamatan")
    return st


def baca_rujukan():
    with rasterio.open(RUJUKAN) as src:
        a = src.read().astype("float32")
    a[a == -32768] = np.nan
    return a / 100


def fitur_per_tanggal(VH, tgl, zona, st, nz):
    T = len(tgl)
    hari = np.array([(datetime.fromisoformat(t) - datetime(2024, 1, 1)).days for t in tgl])
    F = ["n_valid", "basah_baru", "naik", "turun", "z_sum", "db_sum"] + KELAS
    hit = np.zeros((T, nz, len(F)), "float64")
    n_sawah = np.zeros(nz)
    kelas_akhir = np.zeros((NY, NX), "uint8")
    for r0 in range(0, NY, 80):
        r1 = min(NY, r0 + 80)
        sel = np.isfinite(st[1, r0:r1]).ravel()
        if not sel.any():
            continue
        z = zona[r0:r1].ravel()[sel]
        p15 = st[1, r0:r1].ravel()[sel]; med = st[2, r0:r1].ravel()[sel]
        v = VH[:, r0:r1].reshape(T, -1)[:, sel].astype("float32")
        n_sawah += np.bincount(z, minlength=nz)
        basah = v <= (p15 + 1.0)
        akhir_basah = np.full(v.shape[1], -10_000)
        sebelum = np.full(v.shape[1], np.nan, "float32"); basah_sebelum = np.zeros(v.shape[1], bool)
        for t in range(T):
            ok = np.isfinite(v[t]); b = basah[t] & ok
            umur = hari[t] - akhir_basah
            kls = np.full(v.shape[1], 7)
            for j, (lo, hi) in enumerate(BATAS_UMUR):
                kls[(umur >= lo) & (umur <= hi)] = j + 1
            kls[b] = 0
            d = v[t] - sebelum
            fit = {"n_valid": ok, "basah_baru": b & ~basah_sebelum, "naik": ok & (d >= 3), "turun": ok & (d <= -3),
                   "z_sum": np.where(ok, v[t] - med, 0), "db_sum": np.where(ok, v[t], 0)}
            for j, k in enumerate(KELAS):
                fit[k] = ok & (kls == j)
            for j, k in enumerate(F):
                hit[t, :, j] += np.bincount(z, weights=fit[k].astype("float64"), minlength=nz)
            akhir_basah = np.where(b, hari[t], akhir_basah)
            sebelum = np.where(ok, v[t], sebelum); basah_sebelum = np.where(ok, b, basah_sebelum)
            if t == T - 1:
                blok = np.zeros((r1 - r0) * NX, "uint8")
                blok[np.flatnonzero(sel)] = np.where(ok, kls + 1, 0).astype("uint8")
                kelas_akhir[r0:r1] = blok.reshape(r1 - r0, NX)
    return hit, n_sawah, kelas_akhir


def baris_deret(hit, n_sawah, tgl, platform, fitur, pilih):
    out = []
    for i, f in enumerate(fitur, start=1):
        if n_sawah[i] == 0:
            continue
        for t in pilih:
            h = hit[t, i]; nv = h[0]
            if nv == 0:
                continue
            out.append([f["properties"]["kode_wilayah"], f["properties"]["kecamatan"], tgl[t], platform.get(tgl[t], "?"),
                        int(n_sawah[i]), int(nv), round(h[1] / nv, 4), round(h[2] / nv, 4), round(h[3] / nv, 4),
                        round(h[4] / nv, 3), round(h[5] / nv, 2)] + [round(x / nv, 4) for x in h[6:]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["harian", "rujukan"], default="harian")
    ap.add_argument("--hari-jendela", type=int, default=220)
    ap.add_argument("--normalisasi", action="store_true"); a = ap.parse_args()
    os.makedirs(KELUAR, exist_ok=True)
    fitur = json.load(open("config/batas_kecamatan_diy.geojson"))["features"]
    zona = rasterize([(f["geometry"], i + 1) for i, f in enumerate(fitur)], out_shape=(NY, NX),
                     transform=GRID, fill=0, dtype="int16")
    nz = len(fitur) + 1
    mode = a.mode if (a.mode == "rujukan" or os.path.exists(RUJUKAN)) else "rujukan"
    if mode != a.mode:
        print("Rujukan belum ada -> jalankan mode rujukan.", flush=True)
    mulai = "2024-01-01" if mode == "rujukan" else (datetime.now(timezone.utc).date() - timedelta(days=a.hari_jendela)).isoformat()
    t0 = time.time()
    items = cari_item(mulai)
    if not items:
        sys.exit("Tidak ada item Sentinel-1.")
    lama = []
    if mode == "harian" and os.path.exists(DERET):
        lama = list(csv.reader(open(DERET)))[1:]
        sudah = {r[2] for r in lama}
        # hanya tanggal setelah tanggal terakhir di deret (irisan yang tak mencakup DIY tidak dihitung ulang tiap hari)
        baru = sorted(t for t in {i.datetime.date().isoformat() for i in items} if t > max(sudah))
        if not baru:
            print(f"Tidak ada tanggal baru (terakhir {max(sudah)}).", flush=True); return
        print(f"tanggal baru: {baru}", flush=True)
    print(f"mode {mode}: {len(items)} item {items[0].datetime.date()} s.d. {items[-1].datetime.date()}", flush=True)
    VH, tgl, platform = baca_stack(items)
    print(f"baca {time.time()-t0:.0f} dtk; {len(tgl)} tanggal; {VH.nbytes/1e9:.2f} GB", flush=True)
    if mode == "rujukan" or a.normalisasi:
        normalisasi(VH, tgl, platform, terapkan=a.normalisasi)
    st = hitung_rujukan(VH, zona) if mode == "rujukan" else baca_rujukan()
    hit, n_sawah, kelas_akhir = fitur_per_tanggal(VH, tgl, zona, st, nz)
    print(f"olah {time.time()-t0:.0f} dtk; piksel sawah {int(n_sawah.sum())} (~{n_sawah.sum()*HA_PIKSEL:,.0f} ha)", flush=True)

    if mode == "rujukan":
        pilih = range(len(tgl)); simpan = []
    else:
        batas = (datetime.fromisoformat(tgl[0]) + timedelta(days=LOOKBACK)).date().isoformat()
        pilih = [i for i, t in enumerate(tgl) if t >= batas and t not in {r[2] for r in lama}]
        simpan = lama
    rows = simpan + baris_deret(hit, n_sawah, tgl, platform, fitur, pilih)
    rows.sort(key=lambda r: (r[2], int(r[0])))
    with open(DERET, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(KOLOM); w.writerows(rows)
    print(f"deret: {len(rows)} baris; +{len(rows) - len(simpan)} baru; tanggal terakhir {rows[-1][2]}", flush=True)
    if mode == "rujukan":
        with open(f"{KELUAR}/sawah_kecamatan.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["kode_wilayah", "kabupaten", "kecamatan", "piksel_sawah", "ha_sawah_s1"])
            for i, f in enumerate(fitur, start=1):
                p = f["properties"]
                w.writerow([p["kode_wilayah"], p["kabupaten"], p["kecamatan"], int(n_sawah[i]), round(n_sawah[i] * HA_PIKSEL, 1)])
    with rasterio.open(f"{KELUAR}/fase_terbaru.tif", "w", driver="GTiff", width=NX, height=NY, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=GRID, compress="deflate", nodata=0) as dst:
        dst.write(kelas_akhir, 1)
        dst.update_tags(tanggal=tgl[-1], kelas="1=basah,2=u01_12,3=u13_36,4=u37_60,5=u61_84,6=u85_110,7=u111_140,8=u_lain",
                        sumber="Copernicus Sentinel-1 GRD via Microsoft Planetary Computer; lintasan 127 asc")


if __name__ == "__main__":
    main()
