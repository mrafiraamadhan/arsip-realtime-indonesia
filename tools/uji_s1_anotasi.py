"""Lihat aset & struktur XML kalibrasi/noise pada item Sentinel-1 GRD (S1A dan S1C) di Planetary Computer."""
import requests, planetary_computer as pc
from pystac_client import Client
import xml.etree.ElementTree as ET
cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
BB = [110.0, -8.22, 110.86, -7.53]
for plat, rentang in [("sentinel-1a", "2025-06-01/2025-07-01"), ("sentinel-1c", "2025-06-01/2025-07-01"), ("sentinel-1d", "2026-10-01/2026-11-06")]:
    its = list(cat.search(collections=["sentinel-1-grd"], bbox=BB, datetime=rentang,
                          query={"sat:relative_orbit": {"eq": 127}, "platform": {"eq": plat}}, max_items=2).items())
    if not its:
        its = [i for i in cat.search(collections=["sentinel-1-grd"], bbox=BB, datetime=rentang,
                                     query={"sat:relative_orbit": {"eq": 127}}, max_items=10).items() if i.properties.get("platform","").lower()==plat][:1]
    if not its:
        print(plat, "tidak ada item"); continue
    it = its[0]
    print("=" * 30, plat, it.id)
    print("properti:", {k: v for k, v in it.properties.items() if k.startswith(("s1:", "sar:", "sat:", "processing"))})
    print("aset:", sorted(it.assets))
    for kunci in ["schema-calibration-vh", "schema-noise-vh"]:
        if kunci not in it.assets:
            print(kunci, "TIDAK ADA"); continue
        x = requests.get(it.assets[kunci].href, timeout=120).text
        root = ET.fromstring(x)
        print(f"--- {kunci}: {len(x)} byte; anak root: {[c.tag for c in root]}")
        for c in root:
            if c.tag.endswith("List"):
                anak = list(c)
                print(f"  {c.tag} count={c.get('count')} contoh-anak:", [g.tag for g in anak[0]] if anak else None)
                if anak:
                    for g in anak[0]:
                        t = (g.text or "").strip()
                        print(f"     {g.tag}: {t[:120]}{'...' if len(t)>120 else ''} ({len(t.split())} nilai)")
                    if len(anak) > 1:
                        print("     baris vektor:", [a.findtext("line") for a in anak[:6]], "...")
