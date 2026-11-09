"""
Arsip harga tiket pesawat (Google Flights lewat SerpApi) untuk rute utama.

Tarif angkutan udara rutin menjadi penyumbang inflasi menjelang Nataru & Lebaran,
tetapi harga tiket untuk tanggal keberangkatan ke depan tidak diarsipkan publik.
Sekali sehari, untuk tiap rute di config/rute_pesawat.csv dan tiap jarak
keberangkatan (H+7 dan H+30), dicatat semua opsi penerbangan + ringkasan Google
(harga terendah, rentang harga wajar, level harga).

NONAKTIF sampai secret SERPAPI_KEY dipasang (Settings -> Secrets and variables ->
Actions). Paket gratis SerpApi = 250 pencarian/bulan; skrip berhenti sendiri di 240.
Data tidak resmi -> dipakai sebagai indikator dini; IHK BPS tetap pembanding.

Keluaran: arsip/tiket_pesawat/YYYY/MM/YYYY-MM-DD_HHMMZ.csv.gz  (semua opsi)
          arsip/tiket_pesawat/ringkasan_YYYY-MM.csv           (satu baris per rute x jarak)
          log/tiket_pesawat_YYYY-MM.csv
"""
import csv
import os
import statistics
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umum as U  # noqa: E402

URL = "https://serpapi.com/search.json"
JARAK_HARI = [7, 30]
KUOTA_BULAN = 240
WIB = timezone(timedelta(hours=7))
KOLOM_OPSI = ["diambil_utc", "asal", "tujuan", "tgl_berangkat", "jarak_hari", "kelompok", "harga", "maskapai",
              "no_penerbangan", "berangkat", "tiba", "transit", "durasi_menit"]
KOLOM_RINGKAS = ["diambil_utc", "asal", "tujuan", "tgl_berangkat", "jarak_hari", "n_opsi", "harga_min",
                 "harga_median", "google_terendah", "google_level", "google_wajar_bawah", "google_wajar_atas",
                 "status"]


def terpakai_bulan_ini(t) -> int:
    p = os.path.join(U.LOG, f"tiket_pesawat_{t:%Y-%m}.csv")
    if not os.path.isfile(p):
        return 0
    with open(p, encoding="utf-8") as f:
        return sum(int(r.get("diminta") or 0) for r in csv.DictReader(f))


def main():
    t0 = U.kini()
    kunci = os.environ.get("SERPAPI_KEY", "").strip()
    if not kunci:
        U.catat_log(t0, "tiket_pesawat", status="nonaktif", diminta=0, berhasil=0, gagal=0, baris=0, berkas="",
                    catatan="secret SERPAPI_KEY belum dipasang")
        return 0
    def bersih(x) -> str:
        # Pesan galat requests bisa memuat URL lengkap termasuk api_key -> jangan sampai
        # ter-commit ke repo publik.
        return str(x).replace(kunci, "***")

    rute = [r for r in csv.DictReader(open(os.path.join(U.ROOT, "config", "rute_pesawat.csv"), encoding="utf-8"))
            if r.get("aktif", "1").strip() == "1"]
    sisa = KUOTA_BULAN - terpakai_bulan_ini(t0)
    s = U.sesi()
    opsi, ringkas, gagal, diminta = [], [], [], 0
    hari_ini = t0.astimezone(WIB).date()
    for r in rute:
        for jarak in JARAK_HARI:
            if diminta >= sisa:
                gagal.append("kuota bulanan habis")
                break
            tgl = (hari_ini + timedelta(days=jarak)).isoformat()
            diminta += 1
            resp, galat = U.ambil(s, URL, coba=2, timeout=60, params={
                "engine": "google_flights", "departure_id": r["asal"], "arrival_id": r["tujuan"],
                "outbound_date": tgl, "type": "2", "currency": "IDR", "hl": "id", "gl": "id", "api_key": kunci})
            dasar = {"diambil_utc": U.iso(U.kini()), "asal": r["asal"], "tujuan": r["tujuan"],
                     "tgl_berangkat": tgl, "jarak_hari": jarak}
            try:
                js = resp.json() if resp is not None else {}
            except ValueError:
                js = {}
            if resp is None or resp.status_code != 200 or js.get("error"):
                pesan = bersih(js.get("error") or galat or f"HTTP {getattr(resp, 'status_code', '')}")
                gagal.append(f"{r['asal']}-{r['tujuan']}+{jarak}({str(pesan)[:80]})")
                ringkas.append({**dasar, "status": f"gagal: {str(pesan)[:80]}"})
                continue
            harga = []
            for kel in ["best_flights", "other_flights"]:
                for o in js.get(kel) or []:
                    seg = o.get("flights") or [{}]
                    opsi.append({**dasar, "kelompok": kel, "harga": o.get("price", ""),
                                 "maskapai": " / ".join(dict.fromkeys(x.get("airline", "") for x in seg)),
                                 "no_penerbangan": " / ".join(x.get("flight_number", "") for x in seg),
                                 "berangkat": (seg[0].get("departure_airport") or {}).get("time", ""),
                                 "tiba": (seg[-1].get("arrival_airport") or {}).get("time", ""),
                                 "transit": max(len(seg) - 1, 0), "durasi_menit": o.get("total_duration", "")})
                    if isinstance(o.get("price"), (int, float)):
                        harga.append(o["price"])
            pi = js.get("price_insights") or {}
            wajar = pi.get("typical_price_range") or ["", ""]
            ringkas.append({**dasar, "n_opsi": len(harga), "harga_min": min(harga) if harga else "",
                            "harga_median": statistics.median(harga) if harga else "",
                            "google_terendah": pi.get("lowest_price", ""), "google_level": pi.get("price_level", ""),
                            "google_wajar_bawah": wajar[0] if len(wajar) > 0 else "",
                            "google_wajar_atas": wajar[1] if len(wajar) > 1 else "", "status": "ok"})
    berkas = ""
    if opsi:
        berkas = U.path_run("tiket_pesawat", t0, ".csv.gz")
        U.tulis_csv_gz(berkas, opsi, KOLOM_OPSI)
        berkas = os.path.relpath(berkas, U.ROOT)
    if ringkas:
        U.tambah_csv(os.path.join(U.ARSIP, "tiket_pesawat", f"ringkasan_{t0:%Y-%m}.csv"), ringkas, KOLOM_RINGKAS)
    U.catat_log(t0, "tiket_pesawat", status="ok" if not gagal else ("sebagian" if opsi else "gagal"),
                diminta=diminta, berhasil=diminta - len([g for g in gagal if "kuota" not in g]), gagal=len(gagal),
                baris=len(opsi), berkas=berkas, catatan=bersih("; ".join(gagal))[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
