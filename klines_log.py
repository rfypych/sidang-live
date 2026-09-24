"""Telemetry OHLCV untuk chart dashboard — BUKAN buku keputusan.

data/klines.jsonl murni observasi harga (candlestick dashboard). File ini
TIDAK PERNAH dibaca engine keputusan; menambah/menghapusnya tidak mengubah
verdict apa pun — oleh karena itu aman diubah kapan pun tanpa menyentuh
jam OOS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_klines(path: Path, data: dict[str, Any], idx: list[int], keep: int = 2500) -> int:
    """Append candle {ts,o,h,l,c,v} untuk index idx; dedup via ts terakhir; trim ramping.

    Return jumlah baris baru yang benar-benar ditulis.
    """
    last_ts = 0
    if path.exists():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            try:
                last_ts = int(json.loads(lines[-1])["ts"])
            except (ValueError, KeyError, json.JSONDecodeError):
                last_ts = 0

    rows: list[str] = []
    for i in idx:
        ts = int(float(data["open_time"][i]))
        if ts <= last_ts:
            continue  # dedup: catch-up ganda / tumpang tindih halaman fetch
        rows.append(
            json.dumps(
                {
                    "ts": ts,
                    "o": round(float(data["open"][i]), 2),
                    "h": round(float(data["high"][i]), 2),
                    "l": round(float(data["low"][i]), 2),
                    "c": round(float(data["close"][i]), 2),
                    "v": round(float(data["volume"][i]), 3),
                },
                separators=(",", ":"),
            )
        )
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    # trim sesekali: chart hanya butuh beberapa hari; file tetap ramping di git
    if path.exists():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > keep + 100:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
            tmp.replace(path)
    return len(rows)
