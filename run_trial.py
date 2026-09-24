"""SIDANG run_trial — satu sidang penuh di data pasar NYATA, sekali jalan.

Contoh:
  python run_trial.py --symbol BTCUSDT --interval 5m --tp 1.5 --sl 0.75
  python run_trial.py --symbol ETHUSDT --interval 15m --tp 2.0 --sl 1.0 --profile conservative

Paper-only. Ini alat riset, bukan saran investasi.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from sidang.feed import fetch_klines
from sidang.judge import RulesJudge
from sidang.mc_engine import SidangEngine


def main() -> None:
    ap = argparse.ArgumentParser(description="SIDANG trial sekali jalan (paper-only)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="5m", help="1m/5m/15m/1h (disarankan 5m atau 15m)")
    ap.add_argument("--lookback", type=int, default=1000, help="candle tertutup untuk lookback")
    ap.add_argument("--tp", type=float, default=1.5, help="take profit, % dari entry")
    ap.add_argument("--sl", type=float, default=0.75, help="stop loss, % dari entry")
    ap.add_argument("--horizon", type=int, default=96, help="bar simulasi ke depan")
    ap.add_argument("--paths", type=int, default=10_000, help="jumlah path Monte Carlo")
    ap.add_argument("--fee", type=float, default=0.25, help="fee+slippage roundtrip, %")
    ap.add_argument("--profile", default="balanced", choices=["conservative", "balanced", "aggressive"])
    ap.add_argument("--block", type=int, default=24, help="panjang blok bootstrap")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--save", action="store_true", help="simpan JSON ke sidang/reports/")
    args = ap.parse_args()

    print(f"SIDANG W1 | {args.symbol} {args.interval} | data venue nyata Binance, publik, Rp0")
    data = fetch_klines(args.symbol, args.interval, args.lookback)
    closes = data["close"]
    print(
        f"lookback {closes.size} candle tertutup | harga terakhir "
        f"${closes[-1]:,.2f} | vol per-bar {np.std(np.diff(np.log(closes)))*100:.4f}%"
    )

    engine = SidangEngine(closes, block_len=args.block)
    trial = engine.run_trial(
        symbol=args.symbol,
        interval=args.interval,
        tp_pct=args.tp,
        sl_pct=args.sl,
        horizon=args.horizon,
        n_paths=args.paths,
        fee_rt_pct=args.fee,
        seed=args.seed,
    )
    verdict = RulesJudge(args.profile).evaluate(trial)

    # ---------- laporan ----------
    print()
    print("=" * 62)
    print(f"SIDANG: {args.paths:,} path x {args.horizon} bar, {trial.elapsed_ms} ms")
    print(f"entry (harga sekarang)      : ${trial.entry_price:,.2f}")
    print(f"struktur                    : TP +{args.tp}% / SL -{args.sl}% / fee {args.fee:.2f}%")
    print(f"breakeven P(TP duluan)      : {trial.breakeven_p_tp*100:.1f}%")
    print("-" * 62)
    for name, s in trial.sims.items():
        print(
            f"[{s.generator:>9}] P(TP)={s.p_tp_first*100:5.1f}%  P(SL)={s.p_sl_first*100:5.1f}%  "
            f"timeout={s.p_timeout*100:5.1f}%  EV={s.ev_pct:+.2f}%  p5={s.p5_pct:+.2f}%"
        )
    if "fhs_garch" in trial.sims:
        print(f"divergence generator        : {trial.divergence_pp:.1f} pp")
    g = trial.extra.get("garch_fit")
    if g:
        print(
            f"GARCH(1,1)                  : alpha={g['alpha']} beta={g['beta']} "
            f"persistence={g['persistence']}"
        )
    print("-" * 62)
    print(f"VERDICT ({args.profile}): {verdict.action.upper()}")
    for r in verdict.reasons:
        print(f"  - {r}")
    print("=" * 62)

    if args.save:
        out = Path(__file__).resolve().parent / "reports" / f"trial_{args.symbol}_{args.interval}_{int(time.time())}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"trial": trial.as_dict(), "verdict": verdict.as_dict()}, indent=2))
        print(f"tersimpan: {out}")


if __name__ == "__main__":
    main()
