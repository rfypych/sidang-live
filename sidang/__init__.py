"""SIDANG — Sidang Monte Carlo untuk keputusan trading berbasis bukti.

W1 prototype: market data nyata (Binance public stream, tanpa KYC) ->
dua generator independen (block bootstrap + FHS-GARCH) -> pricer
double-barrier TP/SL -> verdict. Paper-only. Tidak ada order live.
"""

__version__ = "0.1.0-w1"
