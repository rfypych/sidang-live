# Aktifasi otomatisasi (sekali, ±60 detik)

File `setup/live.yml` + `setup/backtest.yml` adalah workflow GitHub Actions.
Karena alasan keamanan, **file workflow hanya boleh dibuat lewat web GitHub
atau token dengan scope `workflow`** — bot/sandbox dengan token `repo` saja
akan ditolak. Setelah repo + dashboard ini ter-push (itu sudah otomatis),
lakukan SALAH SATU dari dua ini:

## Jalur A — lewat web GitHub (paling cepat, tanpa token baru)

1. Buka `https://github.com/rfypych/sidang-live`
2. Klik **Add file → Create new file**
3. Ketik nama file: `.github/workflows/live.yml`
   (GitHub otomatis bikin folder `.github/workflows/` saat kamu mengetik `/`)
4. Buka file `setup/live.yml` di repo (raw), copy seluruh isinya, paste ke editor web
5. **Commit changes** (tombak hijau)
6. Ulangi langkah 2–5 untuk `.github/workflows/backtest.yml` dari `setup/backtest.yml`
7. Buka tab **Actions** → kalau ada tombol *"I understand my workflows, go
   ahead and enable them"* → klik sekali
8. Selesai. Dalam ≤15 menit run pertama jalan sendiri; dashboard ikut hidup.

## Jalur B — token baru dengan scope `workflow`

1. GitHub → Settings → Developer settings → Personal access tokens (classic)
   → **Generate new token (classic)** → centang `repo` + `workflow`
2. Kirim token ke agen yang mengelola repo ini; dia push `.github/workflows/`
   dan trigger run pertama.

## Verifikasi

- Tab **Actions** di repo: `sidang-live` harus hijau tiap ±15 menit.
- Dashboard (`https://rfypych.github.io/sidang-live/`): pill **BOT HIDUP**
  hijau, tabel "Kesehatan Run" dapat baris baru tiap 15 menit.
- Kalau pill merah >40 menit: cek tab Actions dulu (run merah = bot bersuara,
  bukan mati diam-diam — by design).

## Notes

- Cron GitHub bisa telat beberapa menit. Aman: bot *catch-up*, tidak skip data.
- Repo **harus publik** supaya menit Actions gratis tak terbatas. Repo privat
  = 2.000 menit/bulan (kurang untuk 96 run/hari).
- Menit per run ≈ 1–2 menit (install numpy/scipy ter-cache).
