"""
Arsip peringatan dini cuaca BMKG (nowcast) untuk SELURUH Indonesia.

Peringatan dini hanya tampil selama berlaku (umumnya 1-3 jam) lalu hilang dari
feed; BMKG tidak menyediakan arsipnya untuk publik. Skrip ini dijalankan tiap
30 menit, mengambil feed RSS nasional, lalu untuk setiap peringatan BARU mengunduh
dokumen CAP-nya (waktu berlaku, tingkat bahaya, kecamatan terdampak, poligon).

Sumber : https://www.bmkg.go.id/alerts/nowcast/id            (RSS, semua provinsi)
         https://www.bmkg.go.id/alerts/nowcast/id/<kode>_alert.xml  (detail CAP)
         Wajib mencantumkan BMKG sebagai sumber.
Keluaran:
  arsip/bmkg_nowcast/ringkasan_YYYY-MM.csv   satu baris per peringatan (tambah-saja)
  arsip/bmkg_nowcast/YYYY/MM/YYYY-MM-DD_HHMMZ.jsonl.gz  XML CAP asli peringatan baru
                                                (utuh kecuali isi poligon kecamatan)
  log/<sumber>_YYYY-MM.csv                            ringkasan run

    python src/bmkg_nowcast.py
"""
import glob
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umum as U  # noqa: E402

RSS = "https://www.bmkg.go.id/alerts/nowcast/id"
DIR = os.path.join(U.ARSIP, "bmkg_nowcast")
KOLOM = ["kode", "pertama_terlihat_utc", "rss_pubdate", "rss_judul", "identifier", "sent", "msg_type",
         "event", "effective", "expires", "severity", "urgency", "certainty", "sender_name",
         "headline", "n_area", "area", "deskripsi", "web"]


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def anak(el, nama):
    return [c for c in list(el) if _lokal(c.tag) == nama]


def teks(el, nama):
    c = anak(el, nama)
    return (c[0].text or "").strip() if c else ""


def kode_terlihat() -> set:
    """Kode yang sudah diarsip (bulan ini + bulan lalu cukup; peringatan tidak berlaku lintas bulan)."""
    sudah = set()
    for p in sorted(glob.glob(os.path.join(DIR, "ringkasan_*.csv")))[-2:]:
        with open(p, encoding="utf-8") as f:
            next(f, None)
            for baris in f:
                sudah.add(baris.split(",", 1)[0])
    return sudah


def tanpa_poligon(xml: str) -> str:
    """Poligon = batas kecamatan terdampak (sudah tersedia di data batas wilayah) dan bisa
    puluhan KB per peringatan. Isinya diganti jumlah titiknya agar arsip tetap ringan;
    nama kecamatan (areaDesc) dan seluruh isi lain CAP tetap utuh."""
    return re.sub(r"(<(?:\w+:)?polygon>)(.*?)(</(?:\w+:)?polygon>)",
                  lambda m: f"{m.group(1)}[dihapus: {len(m.group(2).split())} titik]{m.group(3)}",
                  xml, flags=re.S)


def urai_cap(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    info = (anak(root, "info") or [root])[0]
    area = anak(info, "area")
    return {
        "identifier": teks(root, "identifier"), "sent": teks(root, "sent"), "msg_type": teks(root, "msgType"),
        "event": teks(info, "event"), "effective": teks(info, "effective"), "expires": teks(info, "expires"),
        "severity": teks(info, "severity"), "urgency": teks(info, "urgency"), "certainty": teks(info, "certainty"),
        "sender_name": teks(info, "senderName"), "headline": teks(info, "headline"),
        "n_area": len(area), "area": "; ".join(teks(a, "areaDesc") for a in area)[:2000],
        "deskripsi": " ".join(teks(info, "description").split())[:1500], "web": teks(info, "web"),
    }


def main():
    t0 = U.kini()
    s = U.sesi()
    r, galat = U.ambil(s, RSS)
    if r is None or r.status_code != 200:
        U.catat_log(t0, "bmkg_nowcast", status="gagal", diminta=1, berhasil=0, gagal=1, baris=0, berkas="",
                    catatan=galat or f"RSS HTTP {r.status_code}")
        return 1
    try:
        items = [el for el in ET.fromstring(r.content).iter() if _lokal(el.tag) == "item"]
    except ET.ParseError as e:
        U.catat_log(t0, "bmkg_nowcast", status="gagal", diminta=1, berhasil=0, gagal=1, baris=0, berkas="",
                    catatan=f"RSS bukan XML: {str(e)[:100]} | awal: {r.text[:120]!r}")
        return 1

    sudah = kode_terlihat()
    baru_ringkas, baru_mentah, gagal = [], [], []
    for it in items:
        link = teks(it, "link")
        kode = link.rsplit("/", 1)[-1].replace("_alert.xml", "") if link else ""
        if not kode or kode in sudah:
            continue
        rc, g = U.ambil(s, link)
        dasar = {"kode": kode, "pertama_terlihat_utc": U.iso(U.kini()), "rss_pubdate": teks(it, "pubDate"),
                 "rss_judul": teks(it, "title")}
        if rc is None or rc.status_code != 200:
            gagal.append(f"{kode}({g or rc.status_code})")
            continue  # tidak dicatat sbg "sudah", jadi dicoba lagi di run berikutnya
        try:
            isi = urai_cap(rc.content)
        except ET.ParseError as e:
            isi = {"deskripsi": f"(CAP tidak terbaca: {str(e)[:80]})"}
        baru_ringkas.append({**dasar, **isi})
        baru_mentah.append({"kode": kode, "url": link, "diambil_utc": dasar["pertama_terlihat_utc"],
                            "xml": tanpa_poligon(rc.content.decode("utf-8", "replace"))})
        sudah.add(kode)

    berkas = ""
    if baru_ringkas:
        U.tambah_csv(os.path.join(DIR, f"ringkasan_{t0:%Y-%m}.csv"), baru_ringkas, KOLOM)
        berkas = U.path_run("bmkg_nowcast", t0, ".jsonl.gz")
        with gzip.GzipFile(berkas, "wb", mtime=0) as f:
            for m in baru_mentah:
                f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))
        berkas = os.path.relpath(berkas, U.ROOT)
    U.catat_log(t0, "bmkg_nowcast", status="ok" if not gagal else "sebagian", diminta=len(items),
                berhasil=len(baru_ringkas), gagal=len(gagal), baris=len(baru_ringkas), berkas=berkas,
                catatan=(f"{len(items)} aktif di feed, {len(baru_ringkas)} baru; " + "; ".join(gagal))[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
