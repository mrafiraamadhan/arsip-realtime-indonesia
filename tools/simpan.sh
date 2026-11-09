#!/usr/bin/env bash
# Commit arsip & log hasil run, lalu push dengan ulang (rebase dulu bila ada run lain masuk).
set -u
git config user.name "arsip-bot"
git config user.email "actions@users.noreply.github.com"
git add arsip log 2>/dev/null || true
if git diff --cached --quiet; then echo "Tidak ada perubahan."; exit 0; fi
git commit -q -m "arsip $1: $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3 4 5; do
  if git pull -q --rebase origin main && git push -q; then echo "Tersimpan."; exit 0; fi
  git rebase --abort 2>/dev/null || true
  sleep $((i * 7))
done
echo "Gagal push setelah 5 kali coba."; exit 1
