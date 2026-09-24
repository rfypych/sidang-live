"""SIDANG run_live — loop paper bot: candle tertutup -> sidang -> verdict -> ledger.

Contoh:
  python run_live.py --symbol BTCUSDT --interval 1m --max-runtime 180
  python run_live.py --symbol BTCUSDT --interval 5m --once   # satu verdict lalu keluar

Alur tiap candle tertutup:
  1. kalau ada posisi  -> cek TP/SL di HIGH/LOW candle NYATA (tie = SL)
  2. kalau tidak       -> sidang Monte Carlo penuh (dua generator)
     verdict enter     -> entry paper + pasang TP/SL
     verdict stand down-> diam, catat alasannya ke ledger
Semua kejadian append-only ke ledger JSONL. Paper-only, Rp0, tanpa KYC.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import numpy as np

from sidang.feed import LiveKlineStream
from sidang.judge import RulesJudge
from sidang.ledger import PaperLedger
from sidang.mc_engine import SidangEngine

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


class SidangBot:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.judge = RulesJudge(args.profile)
        self.ledger = PaperLedger(cash=args.cash, path=REPORTS_DIR / "ledger.jsonl")
        self.n_trials = 0
        self.n_entries = 0
        self._engine: SidangEngine | None = None

    def engine(self, closes) -> SidangEngine:
        eng = SidangEngine(closes, block_len=self.args.block)
        self._engine = eng
        return eng

    async def on_closed(self, bar: dict[str, float]) -> None:
        stream: LiveKlineStream = self._stream
        closes = stream.closes

        # 1. kelola posisi terbuka di candle NYATA
        if self.ledger.position is not None:
            trade = self.ledger.check_barriers(bar["high"], bar["low"])
            if trade:
                print(
                    f"[exit ] {trade['reason']:>12} @ ${trade['exit_price']:,.2f} "
                    f"| pnl {trade['pnl_pct']:+.2f}% | equity ${self.ledger.snapshot()['equity']:,.2f}"
                )
                return

        # 2. tanpa posisi -> adakan sidang
        if len(closes) < max(64, self.args.lookback // 2):
            return
        trial = self.engine(np.array(closes[-self.args.lookback :], dtype=float)).run_trial(
            symbol=self.args.symbol,
            interval=self.args.interval,
            tp_pct=self.args.tp,
            sl_pct=self.args.sl,
            horizon=self.args.horizon,
            n_paths=self.args.paths,
            fee_rt_pct=self.args.fee,
            seed=None,
        )
        verdict = self.judge.evaluate(trial)
        self.n_trials += 1
        self.ledger.record_trial(trial, verdict)
        print(
            f"[sidang#{self.n_trials:03d}] close=${bar['close']:,.2f} "
            f"P(TP) pesimis={verdict.p_tp_used*100:5.1f}% (be {trial.breakeven_p_tp*100:.1f}%) "
            f"EV={verdict.ev_used_pct:+.2f}% div={trial.divergence_pp:.1f}pp [{trial.elapsed_ms}ms] "
            f"-> {verdict.action.upper()}"
        )

        if verdict.action == "enter":
            meta = {"p_tp": verdict.p_tp_used, "ev_pct": verdict.ev_used_pct, "reasons": verdict.reasons}
            self.ledger.enter(
                symbol=self.args.symbol,
                price=bar["close"],
                allocation_pct=self.args.alloc,
                tp_pct=self.args.tp,
                sl_pct=self.args.sl,
                meta=meta,
            )
            self.n_entries += 1
            pos = self.ledger.position
            print(
                f"[enter] qty={pos['qty']:.6f} @ ${pos['entry_price']:,.2f} "
                f"TP=${pos['tp_price']:,.2f} SL=${pos['sl_price']:,.2f}"
            )

        if self.args.once:
            raise SystemExit(0)

    async def run(self) -> None:
        self._stream = LiveKlineStream(
            symbol=self.args.symbol,
            interval=self.args.interval,
            on_closed=self.on_closed,
            backfill=self.args.lookback,
            max_runtime_s=self.args.max_runtime,
        )
        print(
            f"SIDANG live (paper-only) | {self.args.symbol} {self.args.interval} | "
            f"modal kertas ${self.args.cash:,.0f} | TP {self.args.tp}% SL {self.args.sl}% "
            f"| profil {self.args.profile}"
        )
        print(f"menunggu candle tertutup pertama... (interval {self.args.interval})")
        await self._stream.run()
        print(
            f"selesai: {self.n_trials} sidang, {self.n_entries} entry, "
            f"{self.ledger.snapshot()}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="SIDANG live paper loop")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--lookback", type=int, default=1000)
    ap.add_argument("--tp", type=float, default=1.5)
    ap.add_argument("--sl", type=float, default=0.75)
    ap.add_argument("--horizon", type=int, default=96)
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--fee", type=float, default=0.25)
    ap.add_argument("--profile", default="balanced")
    ap.add_argument("--block", type=int, default=24)
    ap.add_argument("--cash", type=float, default=10_000.0)
    ap.add_argument("--alloc", type=float, default=25.0, help="% ekuitas per posisi")
    ap.add_argument("--max-runtime", type=float, default=None, help="detik; None = selamanya")
    ap.add_argument("--once", action="store_true", help="berhenti setelah sidang pertama")
    args = ap.parse_args()

    bot = SidangBot(args)
    try:
        asyncio.run(bot.run())
    except (KeyboardInterrupt, SystemExit):
        print(f"berhenti. {bot.ledger.snapshot()}")


if __name__ == "__main__":
    main()
