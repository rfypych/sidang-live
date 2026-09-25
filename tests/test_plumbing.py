"""Uji plumbing SIDANG dengan data sintetis — deterministik, tanpa jaringan.

Skenario: pasar trending naik (drift +0.06%/bar, vol 0.18%/bar).
Hipotesis: verdict ENTER, entry kertas tercatat, TP tersentuh di bar
berikutnya, ledger menutup dengan profit. Kalau salah satu gagal ->
PASS=False, keluar kode 1.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import replay as replay_mod
from sidang.judge import RulesJudge
from sidang.ledger import PaperLedger
from sidang.mc_engine import SidangEngine, SimResult, TrialResult


def test_events_same_candle() -> bool:
    """Regresi: candle yang sama bisa berisi exit lalu enter — KEDUANYA harus tercatat.

    Bug lama: ev.update() menimpa -> event 'exit' hilang saat candle yang sama
    memicu enter baru. Akibatnya backtest melaporkan n_trades=0 padahal ada trade
    (laporan balanced 6 bulan 2026-09-24: realized 0 vs ekuitas turun).
    """
    class FakeEngine:
        def __init__(self, closes, block_len=None):
            pass

        def run_trial(self, **kw):
            be = (kw["sl_pct"] + kw["fee_rt_pct"]) / (
                (kw["tp_pct"] - kw["fee_rt_pct"]) + (kw["sl_pct"] + kw["fee_rt_pct"])
            )
            mbb = SimResult("mbb", 0.60, 0.30, 0.10, 0.50, -1.0, 12.0, kw["n_paths"], kw["horizon"])
            fhs = SimResult("fhs_garch", 0.58, 0.32, 0.10, 0.45, -1.0, 13.0, kw["n_paths"], kw["horizon"])
            return TrialResult(
                symbol=kw["symbol"], interval=kw["interval"], entry_price=50_000.0,
                tp_pct=kw["tp_pct"], sl_pct=kw["sl_pct"], fee_rt_pct=kw["fee_rt_pct"],
                breakeven_p_tp=be, sims={"mbb": mbb, "fhs_garch": fhs},
                divergence_pp=2.0, elapsed_ms=1, extra={},
            )

    params = {"lookback": 1000, "profile": "balanced"}
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    ledger = PaperLedger(cash=10_000.0, path=tmp.name, fee_rt_pct=0.25)
    core = replay_mod.ReplayCore(params, seed_base=0)
    core.attach_ledger(ledger)
    window = np.full(600, 50_000.0)

    orig = replay_mod.SidangEngine
    replay_mod.SidangEngine = FakeEngine
    try:
        evA = core.process_candle(open_ms=1_000, high=50_050.0, low=49_950.0, close=50_000.0,
                                  window_closes=window)
        # candle B: harga jatuh tembus SL (low 49.000 <= SL 49.625) lalu engine
        # (fake) bilang ENTER lagi di candle yang sama -> events harus [exit, trial, enter]
        evB = core.process_candle(open_ms=2_000, high=49_700.0, low=49_000.0, close=49_100.0,
                                  window_closes=window)
    finally:
        replay_mod.SidangEngine = orig

    types_b = [e["type"] for e in evB.get("events", [])]
    ok = (
        [e["type"] for e in evA.get("events", [])] == ["trial", "enter"]
        and types_b == ["exit", "trial", "enter"]
        and evB["type"] == "enter"  # kompat: top-level = kejadian terakhir
        and core.state["n_enters"] == 2
        and core.state["n_exits"] == 1
        and len(ledger.trades) == 1
        and ledger.trades[0]["reason"] == "stop_loss"
    )
    print(f"events-same-candle: A={[e['type'] for e in evA.get('events', [])]} B={types_b} "
          f"enters={core.state['n_enters']} exits={core.state['n_exits']} "
          f"trades={len(ledger.trades)} -> {'OK' if ok else 'GAGAL'}")
    Path(tmp.name).unlink(missing_ok=True)
    return ok


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

    ok_events = test_events_same_candle()

    passed = ok_verdict and ok_entry and ok_exit and ok_tie and ok_events
    print("PLUMBING:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
