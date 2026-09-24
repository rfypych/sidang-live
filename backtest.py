"""SIDANG backtest — replay historis walk-forward, tanpa fitting parameter.

Parameter (TP/SL/horizon/profil) TIDAK dioptimasi terhadap historis ini;
mereka ditetapkan sebelum replay. Backtest ini = validasi plumbing +
baseline buku, BUKAN bukti edge (struktur dipilih dengan pengetahuan
perilaku pasar baru-baru ini — anggap weak-form evidence).

Deterministik: seed per candle (crc32) + data bursa publik.

  python backtest.py --months 6 --profile conservative
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from replay import DEFAULTS, INTERVAL_MS, ReplayCore, iso
from sidang.feed import fetch_klines_range
from sidang.ledger import PaperLedger

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="SIDANG backtest walk-forward (paper-only)")
    ap.add_argument("--symbol", default=DEFAULTS["symbol"])
    ap.add_argument("--interval", default=DEFAULTS["interval"])
    ap.add_argument("--months", type=float, default=6.0)
    ap.add_argument("--profile", default="conservative", choices=["conservative", "balanced", "aggressive"])
    ap.add_argument("--tp", type=float, default=DEFAULTS["tp"])
    ap.add_argument("--sl", type=float, default=DEFAULTS["sl"])
    ap.add_argument("--horizon", type=int, default=DEFAULTS["horizon"])
    ap.add_argument("--paths", type=int, default=DEFAULTS["paths"])
    ap.add_argument("--fee", type=float, default=DEFAULTS["fee"])
    ap.add_argument("--lookback", type=int, default=DEFAULTS["lookback"])
    ap.add_argument("--block", type=int, default=DEFAULTS["block"])
    ap.add_argument("--cash", type=float, default=DEFAULTS["cash"])
    ap.add_argument("--alloc", type=float, default=DEFAULTS["alloc"])
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--tag", default=None, help="suffix nama file laporan")
    return ap.parse_args()


def main() -> None:
    a = parse_args()
    t_wall = time.time()
    iv_ms = INTERVAL_MS[a.interval]
    now_ms = time.time() * 1000.0
    start_ms = now_ms - a.months * 30.44 * 86_400_000.0
    fetch_from = start_ms - (a.lookback + 5) * iv_ms  # lookback konteks sebelum periode

    print(f"SIDANG backtest | {a.symbol} {a.interval} | {a.months} bulan | profil {a.profile}")
    print(f"periode keputusan: {iso(start_ms)} .. {iso(now_ms)} | fetch dari {iso(fetch_from)}")
    data = fetch_klines_range(a.symbol, a.interval, fetch_from, now_ms)
    n = data["close"].size
    print(f"candle tertutup termuat: {n:,}")

    params = {
        "symbol": a.symbol, "interval": a.interval, "lookback": a.lookback, "tp": a.tp,
        "sl": a.sl, "horizon": a.horizon, "paths": a.paths, "fee": a.fee,
        "profile": a.profile, "block": a.block, "cash": a.cash, "alloc": a.alloc,
    }
    core = ReplayCore(params, seed_base=a.seed_base)
    ledger = PaperLedger(cash=a.cash, path=REPORTS / ".backtest-ledger-tmp.jsonl", fee_rt_pct=a.fee)
    core.attach_ledger(ledger)
    core.state["started_iso"] = iso(start_ms)

    first_decision = a.lookback - 1  # window penuh berakhir di candle ini
    if first_decision < 63:
        raise ValueError("lookback terlalu pendek (min 64)")

    opens = data["open_time"]
    highs, lows, closes = data["high"], data["low"], data["close"]

    equity_curve: list[tuple[float, float]] = []  # (open_ms, equity mark-to-close)
    trades: list[dict] = []
    agg = {
        "trials": 0, "enters": 0, "stand_downs": 0,
        "p_tp_used_sum": 0.0, "ev_used_sum": 0.0, "div_sum": 0.0,
        "div_max": 0.0, "be_sum": 0.0, "ms_sum": 0.0,
    }
    bars_in_position = 0
    day_equity: dict[str, float] = {}
    progress_path = REPORTS / ".backtest-progress.json"
    REPORTS.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    for i in range(first_decision, n):
        open_ms = float(opens[i])
        # hanya keputusan di dalam periode
        if open_ms < start_ms:
            continue
        ev = core.process_candle(
            open_ms=open_ms, high=float(highs[i]), low=float(lows[i]), close=float(closes[i]),
            window_closes=closes[i - a.lookback + 1 : i + 1],
        )
        if ev["type"] == "trial":
            agg["trials"] += 1
            agg["stand_downs"] += 1
            tr = ev["trial"]
            agg["p_tp_used_sum"] += tr["p_tp_used"]
            agg["ev_used_sum"] += tr["ev_used"]
            agg["div_sum"] += tr["div_pp"]
            agg["div_max"] = max(agg["div_max"], tr["div_pp"])
            agg["be_sum"] += tr["be_pct"]
            agg["ms_sum"] += tr["ms"]
        elif ev["type"] == "enter":
            agg["enters"] += 1
            agg["stand_downs"] -= 1
        elif ev["type"] == "exit":
            tr = dict(ev["trade"])
            tr["exit_iso"] = iso(open_ms)
            tr["entry_iso"] = iso(float(tr.get("meta", {}).get("entry_open_ms", open_ms)))
            trades.append(tr)

        eq = core.equity(float(closes[i]))
        equity_curve.append((open_ms, eq))
        day_equity[iso(open_ms)[:10]] = eq
        if core.ledger.position is not None:
            bars_in_position += 1

        done = i - first_decision + 1
        if done % 1000 == 0 or i == n - 1:
            el = (time.time() - t0) / 60.0
            pct = done / max(1, (n - first_decision))
            prog = {
                "done": done, "total": n - first_decision, "pct": round(pct * 100, 2),
                "elapsed_min": round(el, 1), "eta_min": round(el / max(pct, 1e-9) * (1 - pct), 1),
                "trials": agg["trials"], "enters": agg["enters"], "exits": core.state["n_exits"],
                "equity": round(equity_curve[-1][1], 2), "ts": iso(time.time() * 1000),
            }
            progress_path.write_text(json.dumps(prog))
            print(
                f"  [{done:>6}/{n - first_decision}] {iso(open_ms)} eq=${eq:,.2f} "
                f"trials={agg['trials']:,} enters={agg['enters']} exits={core.state['n_exits']} "
                f"eta {prog['eta_min']}m"
            )

    # ---- statistik ----
    eq_arr = np.array([e for _, e in equity_curve], dtype=float)
    run_max = np.maximum.accumulate(eq_arr)
    max_dd = float(np.max(1.0 - eq_arr / run_max)) if eq_arr.size else 0.0
    daily = np.array(list(day_equity.values()), dtype=float)
    ret_d = np.diff(daily) / daily[:-1] if daily.size > 1 else np.array([])
    sharpe = float(np.mean(ret_d) / np.std(ret_d) * np.sqrt(365.0)) if ret_d.size and np.std(ret_d) > 0 else 0.0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = round(gross_w / gross_l, 3) if gross_l > 0 else (None if not wins else float("inf"))
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    report = {
        "kind": "backtest",
        "generated_utc": iso(time.time() * 1000),
        "params": params,
        "period": {
            "from": iso(start_ms), "to": iso(float(opens[-1])), "bars": len(equity_curve),
            "months": a.months, "symbol": a.symbol, "interval": a.interval,
        },
        "stats": {
            "starting_cash": a.cash,
            "final_equity": round(eq_arr[-1], 2) if eq_arr.size else a.cash,
            "total_return_pct": round((eq_arr[-1] / a.cash - 1) * 100, 2) if eq_arr.size else 0.0,
            "realized_pnl": round(sum(t["pnl"] for t in trades), 2),
            "fees_paid": round(sum(t["fee"] for t in trades), 2),
            "n_trades": len(trades),
            "n_wins": len(wins),
            "win_rate_pct": round(100.0 * len(wins) / len(trades), 2) if trades else None,
            "profit_factor": pf,
            "sharpe_daily_ann": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "exposure_pct": round(100.0 * bars_in_position / max(1, len(equity_curve)), 2),
            "exit_reasons": reasons,
            "avg_win": round(gross_w / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_l / len(losses), 2) if losses else None,
        },
        "sidang": {
            "n_trials": agg["trials"],
            "n_enters": agg["enters"],
            "enter_rate_pct": round(100.0 * agg["enters"] / max(1, agg["trials"]), 2),
            "avg_p_tp_used_pct": round(agg["p_tp_used_sum"] / max(1, agg["trials"]), 2),
            "avg_ev_used_pct": round(agg["ev_used_sum"] / max(1, agg["trials"]), 3),
            "avg_breakeven_pct": round(agg["be_sum"] / max(1, agg["trials"]), 2),
            "avg_divergence_pp": round(agg["div_sum"] / max(1, agg["trials"]), 2),
            "max_divergence_pp": round(agg["div_max"], 2),
            "avg_trial_ms": round(agg["ms_sum"] / max(1, agg["trials"]), 1),
        },
        "equity_curve": [  # downsample <= 1200 titik
            [int(ms), round(eq, 2)]
            for ms, eq in _downsample(equity_curve, 1200)
        ],
        "trades": [
            {
                "entry_iso": t["entry_iso"], "exit_iso": t["exit_iso"], "reason": t["reason"],
                "entry_price": round(t["entry_price"], 2), "exit_price": round(t["exit_price"], 2),
                "qty": round(t["qty"], 6), "pnl": round(t["pnl"], 2),
                "pnl_gross": round(t["pnl_gross"], 2), "fee": round(t["fee"], 2),
                "pnl_pct": round(t["pnl_pct"], 3),
                "p_tp_at_entry": t.get("meta", {}).get("p_tp"),
                "ev_at_entry": t.get("meta", {}).get("ev_pct"),
            }
            for t in trades
        ],
        "honesty": {
            "paper_only": True,
            "fills": "harga close candle sinyal; exit di level TP/SL; tie intrabar = SL; timeout di close",
            "fees": f"{a.fee}% roundtrip dimodelkan; tanpa slippage tambahan",
            "no_parameter_fitting": "TP/SL/horizon/profil ditetapkan pra-replay, tidak dioptimasi ke historis ini",
            "disclaimer": "Struktur strategi dipilih dengan pengetahuan perilaku pasar baru-baru ini; anggap validasi plumbing + baseline, bukan bukti edge. Bukti sejati = live paper OOS mulai sekarang.",
            "reproducible": f"seed per candle (crc32) + seed_base={a.seed_base}",
        },
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    tag = a.tag or datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    out = REPORTS / f"backtest-{tag}.json"
    out.write_text(json.dumps(report, indent=1))
    (REPORTS / "backtest-latest.json").write_text(json.dumps(report, indent=1))
    progress_path.unlink(missing_ok=True)
    (REPORTS / ".backtest-ledger-tmp.jsonl").unlink(missing_ok=True)

    print("=" * 64)
    s = report["stats"]
    print(f"PERIODE    : {report['period']['from']} .. {report['period']['to']} ({len(equity_curve):,} bar)")
    print(f"SIDANG     : {agg['trials']:,} trial | {agg['enters']} ENTER ({report['sidang']['enter_rate_pct']}%)")
    print(f"TRADE      : {s['n_trades']} | WR {s['win_rate_pct']}% | PF {s['profit_factor']}")
    print(f"HASIL      : ${a.cash:,.0f} -> ${s['final_equity']:,.2f} ({s['total_return_pct']:+.2f}%)")
    print(f"RISIKO     : maxDD {s['max_drawdown_pct']}% | Sharpe(d) {s['sharpe_daily_ann']} | exposure {s['exposure_pct']}%")
    print(f"FEE        : ${s['fees_paid']} terbayar (jujur dihitung)")
    print(f"lokal      : {out.name} + backtest-latest.json | wall {((time.time()-t_wall)/60):.1f} menit")


def _downsample(curve: list[tuple[float, float]], max_pts: int) -> list[tuple[float, float]]:
    if len(curve) <= max_pts:
        return curve
    step = len(curve) / max_pts
    idx = sorted({int(k * step) for k in range(max_pts)} | {len(curve) - 1})
    return [curve[i] for i in idx]


if __name__ == "__main__":
    main()
