"""Isi data/klines.jsonl dengan historis N hari (sekali saja / maintenance).

  python backfill_klines.py --days 5

Telemetry chart dashboard — bukan buku keputusan (lihat klines_log.py).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from klines_log import append_klines
from replay import DEFAULTS, INTERVAL_MS
from sidang.feed import fetch_klines_range

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill telemetry klines (bukan buku keputusan)")
    ap.add_argument("--symbol", default=DEFAULTS["symbol"])
    ap.add_argument("--interval", default=DEFAULTS["interval"])
    ap.add_argument("--days", type=float, default=5.0)
    a = ap.parse_args()

    now_ms = time.time() * 1000.0
    start_ms = now_ms - a.days * 86_400_000.0
    print(f"backfill klines {a.symbol} {a.interval}: {a.days:.0f} hari terakhir")
    data = fetch_klines_range(a.symbol, a.interval, start_ms, now_ms)
    idx = list(range(data["close"].size))
    path = ROOT / "data" / "klines.jsonl"
    n = append_klines(path, data, idx)
    total = len(path.read_text(encoding="utf-8").strip().splitlines()) if path.exists() else 0
    print(f"ditulis: {n} candle baru | total baris klines.jsonl: {total}")


if __name__ == "__main__":
    main()
