"""
Uji kelayakan: seberapa cepat citra radar Sentinel-1 di atas DIY tersedia untuk publik?
Membandingkan dua katalog terbuka (tanpa akun):
  - Copernicus Data Space Ecosystem (CDSE) OData  -> sumber resmi ESA
  - Microsoft Planetary Computer STAC (sentinel-1-grd dan sentinel-1-rtc)
Hasil: arsip/sentinel1_uji/<waktu>.json (daftar akuisisi + jeda tersedia), dicetak ke log.
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
import requests

BBOX = [110.0, -8.2, 110.9, -7.5]          # DIY
POLY = "POLYGON((110.0 -8.2,110.9 -8.2,110.9 -7.5,110.0 -7.5,110.0 -8.2))"
kini = datetime.now(timezone.utc)
awal = (kini - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
hasil = {"diambil_utc": kini.isoformat(timespec="seconds"), "bbox_diy": BBOX}

def cdse():
    url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
    f = (f"Collection/Name eq 'SENTINEL-1' and contains(Name,'GRD') and "
         f"OData.CSC.Intersects(area=geography'SRID=4326;{POLY}') and ContentDate/Start gt {awal}")
    r = requests.get(url, params={"$filter": f, "$orderby": "ContentDate/Start desc", "$top": 60}, timeout=60)
    out = []
    for p in r.json().get("value", []):
        out.append({"nama": p["Name"], "rekam": p["ContentDate"]["Start"], "terbit": p.get("PublicationDate")})
    return r.status_code, out

def pc(koleksi):
    r = requests.post("https://planetarycomputer.microsoft.com/api/stac/v1/search", timeout=60,
                      json={"collections": [koleksi], "bbox": BBOX, "datetime": f"{awal}/..", "limit": 60,
                            "sortby": [{"field": "datetime", "direction": "desc"}]})
    out = []
    for it in r.json().get("features", []):
        pr = it["properties"]
        out.append({"id": it["id"], "rekam": pr.get("datetime"), "platform": pr.get("platform"),
                    "orbit": pr.get("sat:orbit_state"), "relorbit": pr.get("sat:relative_orbit")})
    return r.status_code, out

def jeda(rekam, terbit):
    try:
        a = datetime.fromisoformat(rekam.replace("Z", "+00:00")); b = datetime.fromisoformat(terbit.replace("Z", "+00:00"))
        return round((b - a).total_seconds() / 3600, 1)
    except Exception:
        return None

for nama, fn in [("cdse", cdse), ("pc_grd", lambda: pc("sentinel-1-grd")), ("pc_rtc", lambda: pc("sentinel-1-rtc"))]:
    try:
        st, d = fn()
        hasil[nama] = {"http": st, "n": len(d), "item": d}
        print(f"== {nama}: HTTP {st}, {len(d)} item dalam 45 hari")
        for x in d[:15]:
            j = jeda(x.get("rekam", ""), x.get("terbit", "")) if x.get("terbit") else None
            print("  ", x.get("rekam"), x.get("platform", ""), x.get("orbit", ""), x.get("relorbit", ""),
                  (x.get("nama") or x.get("id"))[:70], f"| terbit +{j} jam" if j is not None else "")
        if d:
            terbaru = max(x["rekam"] for x in d)
            umur = (kini - datetime.fromisoformat(terbaru.replace("Z", "+00:00"))).total_seconds() / 86400
            print(f"   terbaru direkam {terbaru} ({umur:.1f} hari lalu)")
    except Exception as e:
        hasil[nama] = {"galat": f"{type(e).__name__}: {str(e)[:200]}"}
        print(f"== {nama}: GAGAL {e}")

p = os.path.join("arsip", "sentinel1_uji", kini.strftime("%Y-%m-%d_%H%MZ") + ".json")
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(hasil, open(p, "w"), ensure_ascii=False, indent=1)
