# SIDANG — Live Paper Trading + Backtest (serverless, Rp0)

Bot paper-trading **BTCUSDT 5m** berbasis *sidang* Monte Carlo: setiap entry harus
menang 10.000 path simulasi dari **dua generator independen** (Moving Block
Bootstrap + FHS-GARCH) yang menghargai posisi ber-TP/SL sebagai **opsi barrier
ganda** — bukan prediksi, tapi pricing. Verdict dievaluasi RulesJudge profil
`conservative` (margin 12pp di atas breakeven, EV pesimis minimal +0,50%,
divergensi generator maks 8pp). Uang virtual $10.000. **Tidak ada — dan tidak
akan ada — kode order live di repo ini.**

## Arsitektur (semua server gratisan)

```
GitHub Actions (cron tiap 15 menit, repo publik = menit gratis tak terbatas)
   │  python live_catchup.py
   │    1. tarik semua candle TERTUTUP sejak run terakhir (catch-up, REST publik)
   │    2. replay tiap candle: cek TP/SL posisi (tie=SL) → kalau flat → SIDANG
   │    3. verdict ENTER → beli uang virtual, tulis ledger
   │    4. commit data/ kembali ke repo ini (record = bagian dari git history)
   ▼
GitHub Pages (domain gratis: https://rfypych.github.io/sidang-live/)
   └─ index.html + web/ membaca data/*.jsonl + reports/backtest-*.json
      → candlestick chart harga (telemetry data/klines.jsonl), ekuitas, P(TP)
        per sidang, tabel trade, perbandingan profil backtest, heartbeat. HP-friendly.
```

**Kenapa catch-up, bukan loop 24/7?** Cron GitHub tidak presisi (bisa telat
beberapa menit). Karena fill ditetapkan di **harga close candle sinyal** dan
exit di level barrier, hasil paper **deterministik terhadap data** — jitter
cron hanya menggeser *kapan* angka tercatat, bukan *berapa* angkanya. Desain
ini menukar presisi jam dengan ketahanan macet, dan tidak kehilangan apa pun.

## File penting

| File | Peran |
|---|---|
| `replay.py` | Satu logika replay untuk backtest & live (konsistensi penuh) |
| `backtest.py` | Walk-forward historis (`--months 6`), laporan per-profil di `reports/` |
| `live_catchup.py` | Loop paper otomatis untuk cron / dijalankan manual |
| `klines_log.py` | Telemetry OHLCV untuk chart dashboard — BUKAN buku keputusan (aman OOS) |
| `backfill_klines.py` | Isi historis chart harga sekali (`--days 5`) |
| `sidang/` | Engine MC, generator, judge, ledger, feed multi-host Binance |
| `data/` | Record live: `state.json`, `ledger.jsonl`, `trials.jsonl`, `runs.jsonl`, `klines.jsonl` — **di-commit** (ini bukunya) |
| `web/`, `index.html` | Dashboard Pages (chart candlestick + kalibrasi profil), nol dependensi eksternal, bahasa visual BoardUI |
| `setup/` | Kit aktifasi otomatisasi — **sudah aktif** (workflow live + backtest terpasang) |

## Menjalankan manual

```bash
pip install -r requirements.txt
python backtest.py --months 6 --profile conservative   # replay historis
python live_catchup.py                                 # satu kali catch-up
python run_live.py --symbol BTCUSDT --interval 5m      # mode WS di laptop (opsional)
python tests/test_plumbing.py                          # tes sanitasi
```

Trigger backtest di cloud: tab **Actions → backtest → Run workflow** (input
`months`), hasilnya di-commit ke `reports/`.

## Kejujuran (baca dulu sebelum memantau)

- **Paper-only.** Semua angka = uang kertas. Bukan saran keuangan.
- **Fee dihitung jujur** 0,25% roundtrip + tie intrabar = SL + timeout di
  harga close. Tidak ada fill ajaib.
- **Backtest bukan bukti edge**: parameter tidak di-fit ke historis, tapi
  struktur dipilih dengan pengetahuan pasar baru-baru ini (weak-form
  evidence). Bukti sejati = **live paper OOS mulai hari bot dinyalakan**.
- **Gate uang nyata**: umur 18 (KYC venue legal) DAN track record OOS
  3–6 bulan (Sharpe harian > 1,5; PF > 1,3; tanpa bulan negatif parah).
  Sampai keduanya hijau, duit nyata = Rp0.
- Bot yang diam (semua STAND_DOWN) = bot yang sehat. Market choppy +
  fee = tidak ada kasus; menolak trade adalah fitur termahal sistem ini.

## Lisensi & etika

Kode milik pemilik repo. Data dari endpoint publik Binance (market data
saja, tanpa API key, tanpa KYC). Jangan pakai repo ini untuk mengklaim
profit live — semua yang tercatat di sini adalah simulasi.
