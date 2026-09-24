"""Feed data pasar Binance — REST backfill + WebSocket live, PUBLIK.

Tanpa API key, tanpa KYC, market data saja. Ini perbedaan fundamental
dari Jev-Trades (yang memakai Yahoo = harga agregat, bukan venue
eksekusi): di sini harga yang dinilai = harga venue tempat paper
order (kalau suatu hari) akan disimulasikan.

W2: multi-host + fetch berhalaman.
- Host REST dicoba bergantian (data-api.binance.vision lalu api.binance.com):
  runner cloud kadang diblok salah satunya; fallback menutup keduanya.
- fetch_klines_range() menarik rentang historis panjang (backtest 6 bulan)
  dengan pagination 1500 candle/request.
- Candle TERAKHIR dari REST adalah candle yang masih berjalan — selalu
  dibuang. Hanya candle tertutup yang dipakai.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any, Awaitable, Callable

import numpy as np

REST_HOSTS = [
    "https://data-api.binance.vision",  # endpoint data publik resmi (cloud-friendly)
    "https://api.binance.com",           # endpoint utama (ISP-friendly)
]
WS_HOSTS = [
    "wss://data-stream.binance.vision/ws",
    "wss://stream.binance.com:9443/ws",
]
_UA = "sidang-w2/0.1 (+paper-trading-research)"
_page = {"i": 0}  # rotasi host: ingat host yang terakhir sukses


def _get_json(path: str, timeout: int = 20) -> Any:
    last_exc: Exception | None = None
    for _ in range(len(REST_HOSTS)):
        host = REST_HOSTS[_page["i"] % len(REST_HOSTS)]
        _page["i"] += 1
        url = f"{host}{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # JANGAN senyap: cetak lalu coba host lain
            print(f"[feed ] {host} gagal ({type(exc).__name__}: {str(exc)[:90]}) -> host lain", flush=True)
            last_exc = exc
    raise RuntimeError(f"semua host REST gagal: {last_exc!r}")


def _rows_to_arrays(rows: list[list]) -> dict[str, np.ndarray]:
    return {
        "open_time": np.array([float(k[0]) for k in rows]),
        "open": np.array([float(k[1]) for k in rows]),
        "high": np.array([float(k[2]) for k in rows]),
        "low": np.array([float(k[3]) for k in rows]),
        "close": np.array([float(k[4]) for k in rows]),
        "volume": np.array([float(k[5]) for k in rows]),
        "close_time": np.array([float(k[6]) for k in rows]),
    }


def fetch_klines_page(
    symbol: str,
    interval: str,
    limit: int = 1500,
    start_ms: float | None = None,
    end_ms: float | None = None,
) -> list[list]:
    """Satu halaman kline mentah (bisa termasuk candle berjalan)."""
    q = f"symbol={symbol.upper()}&interval={interval}&limit={limit}"
    if start_ms is not None:
        q += f"&startTime={int(start_ms)}"
    if end_ms is not None:
        q += f"&endTime={int(end_ms)}"
    raw = _get_json(f"/api/v3/klines?{q}")
    if not isinstance(raw, list):
        raise RuntimeError(f"respons klines tak terduga: {str(raw)[:120]}")
    return raw


def fetch_klines_range(
    symbol: str,
    interval: str,
    start_ms: float,
    end_ms: float,
    page_limit: int = 1000,  # empiris: /api/v3/klines membatasi 1000 baris/request
    pause_s: float = 0.25,
) -> dict[str, np.ndarray]:
    """Tarik SEMUA candle tertutup di [start_ms, end_ms), berhalaman.

    Dipakai backtest (bulan-an data) dan catch-up (lookback + candle baru).
    Candle berjalan dibuang; celah maintenance bursa dibiarkan apa adanya
    (tidak direkayasa).
    """
    rows: list[list] = []
    cursor = float(start_ms)
    end = float(end_ms)
    while cursor < end:
        page = fetch_klines_page(symbol, interval, page_limit, start_ms=cursor, end_ms=end)
        if not page:
            break
        for k in page:
            if float(k[6]) < end:  # hanya candle yang sudah tertutup sebelum end
                rows.append(k)
        last_open = float(page[-1][0])
        nxt = last_open + 1.0
        if nxt <= cursor:
            raise RuntimeError("pagination tidak maju — kemungkinan data bursa bermasalah")
        cursor = nxt
        if len(page) < page_limit:
            break
        time.sleep(pause_s)  # santai terhadap rate limit
    # dedup + sort by open_time
    seen: dict[float, list] = {}
    for k in rows:
        seen[float(k[0])] = k
    rows = [seen[t] for t in sorted(seen)]
    if len(rows) < 2:
        raise RuntimeError("candle tertutup terlalu sedikit untuk rentang ini")
    return _rows_to_arrays(rows)


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> dict[str, np.ndarray]:
    """Ambil candle tertutup terbaru via REST publik. Return dict array numpy."""
    end_ms = time.time() * 1000.0
    start_ms = end_ms - (limit + 3) * 60_000.0 * ({"1m": 1, "5m": 5, "15m": 15, "1h": 60}.get(interval, 5))
    data = fetch_klines_range(symbol, interval, start_ms, end_ms)
    if data["close"].size > limit:
        data = {k: v[-limit:] for k, v in data.items()}
    return data


class LiveKlineStream:
    """Stream kline live; panggil callback hanya saat candle TERTUTUP.

    Reconnect otomatis dengan backoff + rotasi host WS.
    Buffer closed-candle dibuka dengan backfill REST supaya engine
    langsung punya lookback.
    """

    def __init__(
        self,
        symbol: str,
        interval: str,
        on_closed: Callable[[dict[str, float]], Awaitable[None]],
        backfill: int = 1000,
        max_runtime_s: float | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.on_closed = on_closed
        self.backfill = backfill
        self.max_runtime_s = max_runtime_s
        self.closes: list[float] = []
        self.highs: list[float] = []
        self.lows: list[float] = []
        self._host_i = 0

    async def _prime(self) -> None:
        data = fetch_klines(self.symbol, self.interval, self.backfill)
        self.closes = data["close"].tolist()
        self.highs = data["high"].tolist()
        self.lows = data["low"].tolist()

    async def run(self) -> None:
        import websockets

        await self._prime()
        t0 = time.time()
        backoff = 1.0
        while True:
            if self.max_runtime_s and time.time() - t0 > self.max_runtime_s:
                return
            host = WS_HOSTS[self._host_i % len(WS_HOSTS)]
            uri = f"{host}/{self.symbol.lower()}@kline_{self.interval}"
            try:
                async with websockets.connect(uri, open_timeout=15, ping_interval=20) as ws:
                    backoff = 1.0
                    async for msg in ws:
                        if self.max_runtime_s and time.time() - t0 > self.max_runtime_s:
                            return
                        k = json.loads(msg).get("k", {})
                        if not k or not k.get("x"):
                            continue  # hanya candle tertutup
                        bar = {
                            "open": float(k["o"]),
                            "high": float(k["h"]),
                            "low": float(k["l"]),
                            "close": float(k["c"]),
                            "volume": float(k["v"]),
                            "close_time": float(k["T"]),
                        }
                        self.closes.append(bar["close"])
                        self.highs.append(bar["high"])
                        self.lows.append(bar["low"])
                        await self.on_closed(bar)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # JANGAN senyap: bot trading mati diam-diam = bencana
                print(
                    f"[feed ] {host} reconnect setelah {type(exc).__name__}: {str(exc)[:120]}",
                    flush=True,
                )
                self._host_i += 1  # rotasi host WS
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)
