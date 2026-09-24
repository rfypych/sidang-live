"""Generator path harga untuk simulasi Monte Carlo SIDANG.

Dua generator independen, sengaja beda asumsi supaya perbedaan hasilnya
jadi alarm regime (divergence check):

1. MovingBlockBootstrap (MBB)
   Resample blok return historis dengan wrap sirkular. Mempertahankan
   autokorelasi + volatility clustering jangka pendek. Asumsi implisit:
   masa depan ditarik dari distribusi masa lalu (lookback window).

2. Filtered Historical Simulation GARCH(1,1) (FHS)
   Fit GARCH(1,1) dengan variance targeting + QMLE, lalu simulan maju
   memakai residual empiris yang diresample (iid). Menjaga ekor gemuk
   historis sekaligus memodelkan clustering volatilitas dinamis.

Keduanya menghasilkan matriks log-return (n_paths, horizon).
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

EPS = 1e-12


class MovingBlockBootstrap:
    """Circular moving block bootstrap atas log-return historis."""

    name = "mbb"

    def __init__(self, returns: np.ndarray, block_len: int = 24) -> None:
        r = np.asarray(returns, dtype=np.float64)
        r = r[np.isfinite(r)]
        if r.size < 32:
            raise ValueError("lookback terlalu pendek (min 32 bar)")
        self.returns = r
        self.n = r.size
        self.block_len = max(4, int(block_len))

    def sample(self, n_paths: int, horizon: int, rng: np.random.Generator) -> np.ndarray:
        L = self.block_len
        n_blocks = int(np.ceil(horizon / L))
        starts = rng.integers(0, self.n, size=(n_paths, n_blocks))
        step_idx = np.arange(horizon)
        block_of_step = step_idx // L
        offset_of_step = step_idx % L
        idx = starts[:, block_of_step] + offset_of_step[None, :]
        idx = idx % self.n  # wrap sirkular
        return self.returns[idx]


class FhsGarch:
    """Filtered Historical Simulation dengan GARCH(1,1).

    omega ditetapkan via variance targeting (omega = var*(1-alpha-beta))
    supaya fit stabil pada sampel pendek. Residual terstandarisasi
    empiris diresample iid -> ekor gemuk historis ikut terbawa.
    """

    name = "fhs_garch"

    def __init__(self, returns: np.ndarray) -> None:
        r = np.asarray(returns, dtype=np.float64)
        r = r[np.isfinite(r)]
        if r.size < 64:
            raise ValueError("lookback terlalu pendek untuk GARCH (min 64 bar)")
        self.returns = r
        self.n = r.size
        self.longrun_var = float(np.var(r))
        if self.longrun_var <= EPS:
            raise ValueError("variance hampir nol; pasar mati?")
        self._fit()

    # ---------- fitting ----------

    def _sigma_path(self, alpha: float, beta: float) -> np.ndarray:
        """Varian kondisional DENGAN lag yang benar: sigma2[t] memakai r[t-1]."""
        omega = self.longrun_var * (1.0 - alpha - beta)
        sig2 = np.empty(self.n)
        prev = self.longrun_var  # sigma2_0 = varian tak-kondisional
        for t in range(self.n):
            sig2[t] = prev
            prev = omega + alpha * self.returns[t] ** 2 + beta * prev
        return sig2

    def _neg_qml(self, params: np.ndarray) -> float:
        alpha, beta = params
        if alpha <= 0 or beta <= 0 or alpha + beta >= 0.999:
            return 1e12
        sig2 = self._sigma_path(alpha, beta)
        if np.any(sig2 <= EPS):
            return 1e12
        return float(np.sum(np.log(sig2) + self.returns ** 2 / sig2))

    def _fit(self) -> None:
        best, best_val = None, np.inf
        for a0, b0 in ((0.08, 0.88), (0.05, 0.93), (0.15, 0.80), (0.02, 0.96)):
            try:
                res = minimize(
                    self._neg_qml,
                    x0=np.array([a0, b0]),
                    method="Nelder-Mead",
                    options={"xatol": 1e-7, "fatol": 1e-7, "maxiter": 2000},
                )
            except Exception:
                continue
            if res.fun < best_val:
                best_val, best = res.fun, res.x
        if best is None:
            raise RuntimeError("GARCH fit gagal total")
        self.alpha, self.beta = float(best[0]), float(best[1])
        self.alpha = min(max(self.alpha, 1e-4), 0.98)
        self.beta = min(max(self.beta, 1e-4), 0.98 - self.alpha if self.alpha > 0.98 - self.beta else 0.98)
        if self.alpha + self.beta >= 0.999:  # jepit persistence
            scale = 0.999 / (self.alpha + self.beta)
            self.alpha *= scale
            self.beta *= scale
        self.omega = self.longrun_var * (1.0 - self.alpha - self.beta)
        sig2 = self._sigma_path(self.alpha, self.beta)
        self.sig2_last = float(sig2[-1])  # kondisi terkini untuk simulasi maju
        z = self.returns / np.sqrt(np.maximum(sig2, EPS))
        self.z_pool = z[np.isfinite(z)]

    # ---------- sampling ----------

    def sample(self, n_paths: int, horizon: int, rng: np.random.Generator) -> np.ndarray:
        z_draws = rng.choice(self.z_pool, size=(n_paths, horizon), replace=True)
        out = np.empty((n_paths, horizon))
        # mulai dari varian kondisional TERAKHIR hasil fit (bukan varian tak-kondisional):
        # itulah arti "filtered" pada FHS — ramalan bersyarat pada regime saat ini.
        sig2 = np.full(n_paths, self.sig2_last)
        r_prev = np.full(n_paths, self.returns[-1] ** 2)
        for t in range(horizon):
            sig2 = self.omega + self.alpha * r_prev + self.beta * sig2
            r = np.sqrt(np.maximum(sig2, EPS)) * z_draws[:, t]
            out[:, t] = r
            r_prev = r ** 2
        return out

    def describe(self) -> dict:
        return {
            "generator": self.name,
            "alpha": round(self.alpha, 5),
            "beta": round(self.beta, 5),
            "persistence": round(self.alpha + self.beta, 5),
            "longrun_vol_pct_per_bar": round(np.sqrt(self.longrun_var) * 100, 5),
        }
