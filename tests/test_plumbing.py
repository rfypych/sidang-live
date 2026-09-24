"""Uji plumbing SIDANG dengan data sintetis — deterministik, tanpa jaringan.

Skenario: pasar trending naik (drift +0.06%/bar, vol 0.18%/bar).
Hipotesis: verdict ENTER, entry kertas tercatat, TP tersentuh di bar
berikutnya, ledger menutup dengan profit. Kalau salah satu gagal ->
PASS=False, keluar kode 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidang.judge import RulesJudge
from sidang.ledger import PaperLedger
from sidang.mc_engine import SidangEngine


def main() -> int:
    rng = np.random.default_rng(42)
    n = 600
    drift, vol = 0.0006, 0.0018
    rets = drift + vol * rng.standard_normal(n)
    closes = 50_000.0 * np.exp(np.cumsum(rets))

    engine = SidangEngine(closes, block_len=24)
    trial = engine.run_trial(
        symbol="SYNTH-USD",
        interval="5m",
        tp_pct=1.2,
        sl_pct=0.6,
        horizon=96,
        n_paths=20_000,
        fee_rt_pct=0.25,
        seed=7,
    )
    verdict = RulesJudge("balanced").evaluate(trial)

    print(f"synthetic trending: P(TP) pesimis={verdict.p_tp_used*100:.1f}% "
          f"(be {trial.breakeven_p_tp*100:.1f}%) EV={verdict.ev_used_pct:+.2f}% "
          f"-> {verdict.action.upper()}")

    ok_verdict = verdict.action == "enter"
    if not ok_verdict:
        print("GAGAL: seharusnya ENTER pada pasar trending sintetis")
        return 1

    ledger = PaperLedger(cash=10_000.0, path="sidang/reports/ledger_test.jsonl")
    last = ledger.enter(
        symbol="SYNTH-USD",
        price=float(closes[-1]),
        allocation_pct=25.0,
        tp_pct=1.2,
        sl_pct=0.6,
        meta={"p_tp": verdict.p_tp_used, "reasons": verdict.reasons},
    )
    ok_entry = ledger.position is not None and last["qty"] > 0

    # bar berikutnya menembus TP (harga naik 1.5%)
    next_high = float(closes[-1]) * 1.015
    next_low = float(closes[-1]) * 0.999
    trade = ledger.check_barriers(next_high, next_low)

    ok_exit = (
        trade is not None
        and trade["reason"] == "take_profit"
        and trade["pnl"] > 0
    )
    # tie-break SL: bar yang menembus dua-duanya -> harus stop_loss
    ledger.enter(symbol="SYNTH-USD", price=float(closes[-1]), allocation_pct=25.0,
                 tp_pct=1.2, sl_pct=0.6)
    tie = ledger.check_barriers(float(closes[-1]) * 1.015, float(closes[-1]) * 0.98)
    ok_tie = tie is not None and tie["reason"] == "stop_loss"

    print(f"entry: {'OK' if ok_entry else 'GAGAL'} | exit TP: {'OK' if ok_exit else 'GAGAL'} "
          f"| tie->SL: {'OK' if ok_tie else 'GAGAL'} | pnl TP: {trade['pnl_pct']:+.2f}% "
          f"| equity: ${ledger.snapshot()['equity']:,.2f}")

    passed = ok_verdict and ok_entry and ok_exit and ok_tie
    print("PLUMBING:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
