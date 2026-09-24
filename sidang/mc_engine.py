"""Pricer Monte Carlo double-barrier untuk posisi ber-TP/SL.

Satu posisi long dengan Take Profit (barrier atas) dan Stop Loss
(b barrier bawah) secara matematis = opsi barrier ganda. Kami
hargai via simulasi, bukan prediksi:

  P(TP duluan), P(SL duluan), P(timeout),
  EV setelah fee roundtrip, persentil ke-5, median bar ke exit.

Fill realistis: fee per sisi + slippage (default 0.25% roundtrip,
asumsi taker Binance spot 0.1%/sisi + slippage kecil).

Konservatisme yang disengaja:
- tie intrabar (TP dan SL tersentuh di bar yang sama) -> dihitung SL;
- agregasi lintas generator memakai nilai PESIMIS (min), bukan rata-rata;
- timeout ditutup di harga akhir horizon (bukan dijaga selamanya).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .generators import FhsGarch, MovingBlockBootstrap


@dataclass
class SimResult:
    generator: str
    p_tp_first: float
    p_sl_first: float
    p_timeout: float
    ev_pct: float
    p5_pct: float
    median_bars_to_tp: float
    n_paths: int
    horizon: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "generator": self.generator,
            "p_tp_first": round(self.p_tp_first, 4),
            "p_sl_first": round(self.p_sl_first, 4),
            "p_timeout": round(self.p_timeout, 4),
            "ev_pct": round(self.ev_pct, 4),
            "p5_pct": round(self.p5_pct, 4),
            "median_bars_to_tp": self.median_bars_to_tp,
            "n_paths": self.n_paths,
            "horizon": self.horizon,
        }


@dataclass
class TrialResult:
    symbol: str
    interval: str
    entry_price: float
    tp_pct: float
    sl_pct: float
    fee_rt_pct: float
    breakeven_p_tp: float
    sims: dict[str, SimResult] = field(default_factory=dict)
    divergence_pp: float = 0.0
    elapsed_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "entry_price": self.entry_price,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "fee_rt_pct": self.fee_rt_pct,
            "breakeven_p_tp": round(self.breakeven_p_tp, 4),
            "sims": {k: v.as_dict() for k, v in self.sims.items()},
            "divergence_pp": round(self.divergence_pp, 2),
            "elapsed_ms": self.elapsed_ms,
            **self.extra,
        }


class SidangEngine:
    def __init__(self, closes: np.ndarray, block_len: int = 24) -> None:
        c = np.asarray(closes, dtype=np.float64)
        c = c[np.isfinite(c) & (c > 0)]
        if c.size < 64:
            raise ValueError("butuh minimal 64 candle tertutup")
        self.closes = c
        self.returns = np.diff(np.log(c))
        self.mbb = MovingBlockBootstrap(self.returns, block_len=block_len)
        try:
            self.fhs = FhsGarch(self.returns)
            self.garch_ok = True
        except Exception:
            self.fhs = None
            self.garch_ok = False

    @staticmethod
    def breakeven(tp_pct: float, sl_pct: float, fee_rt_pct: float) -> float:
        """P(TP duluan) minimal agar EV = 0 setelah fee."""
        tp_eff = tp_pct - fee_rt_pct
        sl_eff = sl_pct + fee_rt_pct
        if tp_eff <= 0:
            return float("inf")
        return sl_eff / (tp_eff + sl_eff)

    def _simulate_one(
        self,
        gen: Any,
        entry_price: float,
        tp_pct: float,
        sl_pct: float,
        horizon: int,
        n_paths: int,
        fee_rt_pct: float,
        rng: np.random.Generator,
    ) -> SimResult:
        rets = gen.sample(n_paths, horizon, rng)
        logp = np.cumsum(rets, axis=1)
        price = entry_price * np.exp(logp)

        tp_level = entry_price * (1.0 + tp_pct / 100.0)
        sl_level = entry_price * (1.0 - sl_pct / 100.0)

        hit_tp = price >= tp_level
        hit_sl = price <= sl_level

        any_tp = hit_tp.any(axis=1)
        any_sl = hit_sl.any(axis=1)
        first_tp = np.where(any_tp, hit_tp.argmax(axis=1), np.iinfo(np.int64).max)
        first_sl = np.where(any_sl, hit_sl.argmax(axis=1), np.iinfo(np.int64).max)

        # tie / step yang melampaui dua barrier -> SL (konservatif)
        is_tp = any_tp & (first_tp < first_sl)
        is_sl = any_sl & (first_sl <= first_tp)
        is_to = ~is_tp & ~is_sl

        pnl = np.empty(n_paths)
        pnl[is_tp] = tp_pct - fee_rt_pct
        pnl[is_sl] = -sl_pct - fee_rt_pct
        end_rel = np.expm1(logp[is_to, -1]) if is_to.any() else np.empty(0)
        pnl[is_to] = end_rel * 100.0 - fee_rt_pct

        bars_to_tp = first_tp[is_tp].astype(np.float64)

        return SimResult(
            generator=gen.name,
            p_tp_first=float(is_tp.mean()),
            p_sl_first=float(is_sl.mean()),
            p_timeout=float(is_to.mean()),
            ev_pct=float(pnl.mean()),
            p5_pct=float(np.percentile(pnl, 5)),
            median_bars_to_tp=float(np.median(bars_to_tp)) if bars_to_tp.size else float("nan"),
            n_paths=n_paths,
            horizon=horizon,
        )

    def run_trial(
        self,
        symbol: str,
        interval: str,
        tp_pct: float,
        sl_pct: float,
        horizon: int = 96,
        n_paths: int = 10_000,
        fee_rt_pct: float = 0.25,
        seed: int | None = None,
    ) -> TrialResult:
        rng = np.random.default_rng(seed)
        entry_price = float(self.closes[-1])
        t0 = time.perf_counter()

        result = TrialResult(
            symbol=symbol,
            interval=interval,
            entry_price=entry_price,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            fee_rt_pct=fee_rt_pct,
            breakeven_p_tp=self.breakeven(tp_pct, sl_pct, fee_rt_pct),
        )

        result.sims["mbb"] = self._simulate_one(
            self.mbb, entry_price, tp_pct, sl_pct, horizon, n_paths, fee_rt_pct, rng
        )
        if self.garch_ok:
            result.sims["fhs_garch"] = self._simulate_one(
                self.fhs, entry_price, tp_pct, sl_pct, horizon, n_paths, fee_rt_pct, rng
            )
            result.divergence_pp = abs(
                result.sims["mbb"].p_tp_first - result.sims["fhs_garch"].p_tp_first
            ) * 100.0

        result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        result.extra = {
            "lookback_bars": int(self.closes.size),
            "garch_fit": self.fhs.describe() if self.garch_ok else None,
        }
        return result
