"""Fungsi bersama: sesi HTTP sopan, pembatas laju, penulisan berkas arsip, dan log run."""
import csv
import gzip
import io
import os
import time
from datetime import datetime, timezone

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ARSIP = os.path.join(ROOT, "arsip")
LOG = os.path.join(ROOT, "log")

USER_AGENT = "arsip-realtime-indonesia/1.0 (arsip data publik untuk riset ekonomi; github.com/mrafiraamadhan)"


def kini() -> datetime:
    return datetime.now(timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def sesi() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


class Pembatas:
    """Jaga laju di bawah batas BMKG (60 permintaan/menit/IP) dengan jeda tetap."""

    def __init__(self, per_menit=50):
        self.jeda = 60.0 / per_menit
        self.terakhir = 0.0

    def tunggu(self):
        sisa = self.jeda - (time.monotonic() - self.terakhir)
        if sisa > 0:
            time.sleep(sisa)
        self.terakhir = time.monotonic()


def ambil(s: requests.Session, url: str, pembatas: Pembatas = None, coba=3, timeout=30, **kw):
    """GET dengan ulang (jeda bertambah). Mengembalikan (response|None, pesan_galat)."""
    galat = ""
    for i in range(coba):
        if pembatas:
            pembatas.tunggu()
        try:
            r = s.get(url, timeout=timeout, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                galat = f"HTTP {r.status_code}"
                time.sleep(5 * (i + 1))
                continue
            return r, ""
        except requests.RequestException as e:
            galat = f"{type(e).__name__}: {str(e)[:150]}"
            time.sleep(3 * (i + 1))
    return None, galat


def path_run(sumber: str, t: datetime, akhiran: str) -> str:
    """arsip/<sumber>/YYYY/MM/YYYY-MM-DD_HHMMZ<akhiran> -- satu berkas per run, tidak pernah ditimpa."""
    p = os.path.join(ARSIP, sumber, t.strftime("%Y"), t.strftime("%m"), t.strftime("%Y-%m-%d_%H%MZ") + akhiran)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def tulis_csv_gz(path: str, baris: list, kolom: list):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=kolom, extrasaction="ignore")
    w.writeheader()
    w.writerows(baris)
    # mtime=0 -> berkas identik untuk isi identik (git tidak melihat perubahan palsu)
    with gzip.GzipFile(path, "wb", mtime=0) as f:
        f.write(buf.getvalue().encode("utf-8"))


def tambah_csv(path: str, baris: list, kolom: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    baru = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=kolom, extrasaction="ignore")
        if baru:
            w.writeheader()
        w.writerows(baris)


def catat_log(t: datetime, sumber: str, **isi):
    """Satu baris ringkasan per sumber per run -> log/<sumber>_YYYY-MM.csv.
    Berkas log dipisah per sumber supaya dua alur kerja yang berjalan bersamaan
    tidak menambah baris ke berkas yang sama (penyebab bentrok saat git rebase)."""
    kolom = ["run_utc", "sumber", "status", "diminta", "berhasil", "gagal", "baris", "berkas", "catatan"]
    tambah_csv(os.path.join(LOG, f"{sumber}_{t:%Y-%m}.csv"),
               [{"run_utc": iso(t), "sumber": sumber, **isi}], kolom)
    print(f"[{sumber}] " + ", ".join(f"{k}={v}" for k, v in isi.items()))
