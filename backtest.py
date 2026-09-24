"""SIDANG backtest — replay historis walk-forward, tanpa fitting parameter.

Parameter (TP/SL/horizon/profil) TIDAK dioptimasi terhadap historis ini;
mereka ditetapkan sebelum replay. Backtest ini = validasi plumbing +
baseline buku, BUKAN bukti edge (struktur dipilih dengan pengetahuan
perilaku pasar baru-baru ini — anggap weak-form evidence).

Deterministik: seed per candle (crc32) + data bursa publik.

Mendukung CHUNKED RUN (sandbox/laptop terbatas):
  python backtest.py --months 1 --max-minutes 7    # chunk pertama
  python backtest.py --months 1 --resume           # ulangi sampai selesai
Checkpoint ditulis ke reports/.backtest-checkpoint.json; laporan final
hanya ditulis saat replay menyusul data terbaru.
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
CKPT = REPORTS / ".backtest-checkpoint.json"


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
    ap.add_argument("--resume", action="store_true", help="lanjutkan dari checkpoint")
    ap.add_argument("--max-minutes", type=float, default=7.0, help="batas wall per chunk (lalu checkpoint)")
    return ap.parse_args()


def main() -> None:
    a = parse_args()
    t_wall = time.time()
    iv_ms = INTERVAL_MS[a.interval]
    now_ms = time.time() * 1000.0

    params = {
        "symbol": a.symbol, "interval": a.interval, "lookback": a.lookback, "tp": a.tp,
        "sl": a.sl, "horizon": a.horizon, "paths": a.paths, "fee": a.fee,
        "profile": a.profile, "block": a.block, "cash": a.cash, "alloc": a.alloc,
    }

    core = ReplayCore(params, seed_base=a.seed_base)
    ledger = PaperLedger(cash=a.cash, path=REPORTS / ".backtest-ledger-tmp.jsonl", fee_rt_pct=a.fee)
    core.attach_ledger(ledger)

    # ---------- muat checkpoint (resume) atau mulai segar ----------
    ck: dict | None = None
    if a.resume and CKPT.exists():
        ck = json.loads(CKPT.read_text())
        if ck["params"] != params:
            raise RuntimeError("checkpoint milik parameter berbeda — hapus checkpoint atau samakan parameter")
        core.state = ck["state"]
        ledger.cash = float(ck["cash"])
        ledger.position = ck["position"]
        print(f"[resume] dari {iso(ck['last_open_ms'])} | equity terakhir ${ck['equity_curve'][-1][1]:,.2f}")
    if ck is None:
        core.state["started_iso"] = None  # diisi setelah tahu start_ms

    start_ms = float(ck["start_ms"]) if ck else now_ms - a.months * 30.44 * 86_400_000.0
    last_open = float(ck["last_open_ms"]) if ck else 0.0
    agg = ck["agg"] if ck else {
        "trials": 0, "enters": 0,
        "p_tp_used_sum": 0.0, "ev_used_sum": 0.0, "div_sum": 0.0,
        "div_max": 0.0, "be_sum": 0.0, "ms_sum": 0.0,
    }
    trades: list[dict] = ck["trades"] if ck else []
    equity_curve: list[list] = ck["equity_curve"] if ck else []
    day_equity: dict[str, float] = {d: e for d, e in ck["day_equity"]} if ck else {}
    bars_in_position = int(ck["bars_in_position"]) if ck else 0

    if ck is None:
        core.state["started_iso"] = iso(start_ms)

    # ---------- fetch konteks + candle baru ----------
    fetch_from = min(start_ms, (last_open if ck else start_ms)) - (a.lookback + 5) * iv_ms
    if ck:
        fetch_from = last_open - (a.lookback + 5) * iv_ms  # hanya konteks sejak checkpoint
    print(
        f"SIDANG backtest | {a.symbol} {a.interval} | periode {iso(start_ms)} .. sekarang "
        f"| profil {a.profile} | resume={'ya' if ck else 'tidak'}"
    )
    data = fetch_klines_range(a.symbol, a.interval, fetch_from, now_ms)
    n = data["close"].size
    opens, highs, lows, closes = data["open_time"], data["high"], data["low"], data["close"]
    print(f"candle tertutup termuat: {n:,} (konteks lookback {a.lookback})")

    decision_from = start_ms if not ck else last_open + 0.5
    progress_path = REPORTS / ".backtest-progress.json"
    REPORTS.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    deadline = t0 + a.max_minutes * 60.0
    n_done_this_chunk = 0
    for i in range(a.lookback - 1, n):
        open_ms = float(opens[i])
        if open_ms < decision_from:
            continue
        if time.time() > deadline and n_done_this_chunk > 0:
            print(f"[chunk] batas {a.max_minutes} menit tercapai -> checkpoint di {iso(open_ms)}")
            break
        ev = core.process_candle(
            open_ms=open_ms, high=float(highs[i]), low=float(lows[i]), close=float(closes[i]),
            window_closes=closes[i - a.lookback + 1: i + 1],
        )
        n_done_this_chunk += 1
        if ev["type"] == "trial":
            agg["trials"] += 1
            tr = ev["trial"]
            agg["p_tp_used_sum"] += tr["p_tp_used"]
            agg["ev_used_sum"] += tr["ev_used"]
            agg["div_sum"] += tr["div_pp"]
            agg["div_max"] = max(agg["div_max"], tr["div_pp"])
            agg["be_sum"] += tr["be_pct"]
            agg["ms_sum"] += tr["ms"]
            if tr["verdict"] == "enter":
                agg["enters"] += 1
        elif ev["type"] == "exit":
            tr = dict(ev["trade"])
            tr["exit_iso"] = iso(open_ms)
            tr["entry_iso"] = iso(float(tr.get("meta", {}).get("entry_open_ms", open_ms)))
            trades.append(tr)

        eq = core.equity(float(closes[i]))
        equity_curve.append([int(open_ms), round(eq, 2)])
        day_equity[iso(open_ms)[:10]] = round(eq, 2)
        if ledger.position is not None:
            bars_in_position += 1

        done = n_done_this_chunk
        if done % 1000 == 0:
            el = (time.time() - t0) / 60.0
            prog = {
                "done": done, "trials": agg["trials"], "enters": agg["enters"],
                "exits": core.state["n_exits"], "equity": equity_curve[-1][1],
                "ts": iso(time.time() * 1000),
            }
            progress_path.write_text(json.dumps(prog))
            print(
                f"  [{done:>6} chunk-bar] {iso(open_ms)} eq=${eq:,.2f} "
                f"trials={agg['trials']:,} enters={agg['enters']} exits={core.state['n_exits']}"
            )

    if not equity_curve:
        raise RuntimeError("tidak ada bar keputusan yang diproses — periksa rentang waktu")

    last_processed_open = float(equity_curve[-1][0])
    caught_up = last_processed_open >= now_ms - 2.0 * iv_ms

    if not caught_up:
        # ---------- simpan checkpoint, chunk berikutnya via --resume ----------
        CKPT.write_text(json.dumps({
            "params": params, "start_ms": start_ms, "last_open_ms": last_processed_open,
            "cash": ledger.cash, "position": ledger.position, "state": core.state,
            "agg": agg, "trades": trades, "equity_curve": equity_curve,
            "day_equity": [[d, e] for d, e in day_equity.items()],
            "bars_in_position": bars_in_position,
        }))
        progress_path.write_text(json.dumps({
            "done": n_done_this_chunk, "trials": agg["trials"], "enters": agg["enters"],
            "exits": core.state["n_exits"], "equity": equity_curve[-1][1],
            "status": "checkpoint", "last": iso(last_processed_open),
            "ts": iso(time.time() * 1000),
        }))
        print(
            f"[checkpoint] {iso(last_processed_open)} | total {len(equity_curve):,} bar | "
            f"LANJUTKAN: python backtest.py --months {a.months} --profile {a.profile} --resume"
        )
        return

    # ---------- selesai: statistik + laporan ----------
    final_to = iso(last_processed_open)
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
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    report = {
        "kind": "backtest",
        "generated_utc": iso(time.time() * 1000),
        "params": params,
        "period": {
            "from": iso(start_ms), "to": final_to, "bars": len(equity_curve),
            "months": a.months, "symbol": a.symbol, "interval": a.interval,
        },
        "stats": {
            "starting_cash": a.cash,
            "final_equity": round(float(eq_arr[-1]), 2) if eq_arr.size else a.cash,
            "total_return_pct": round((float(eq_arr[-1]) / a.cash - 1) * 100, 2) if eq_arr.size else 0.0,
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
        "equity_curve": _downsample(equity_curve, 1200),
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
    # per-profil: dashboard membandingkan konservatif/balanced/aggressive tanpa glob HTTP
    (REPORTS / f"backtest-{a.profile}-latest.json").write_text(json.dumps(report, indent=1))
    CKPT.unlink(missing_ok=True)
    progress_path.unlink(missing_ok=True)
    (REPORTS / ".backtest-ledger-tmp.jsonl").unlink(missing_ok=True)

    print("=" * 64)
    s = report["stats"]
    print(f"PERIODE    : {report['period']['from']} .. {final_to} ({len(equity_curve):,} bar)")
    print(f"SIDANG     : {agg['trials']:,} trial | {agg['enters']} ENTER ({report['sidang']['enter_rate_pct']}%)")
    print(f"TRADE      : {s['n_trades']} | WR {s['win_rate_pct']}% | PF {s['profit_factor']}")
    print(f"HASIL      : ${a.cash:,.0f} -> ${s['final_equity']:,.2f} ({s['total_return_pct']:+.2f}%)")
    print(f"RISIKO     : maxDD {s['max_drawdown_pct']}% | Sharpe(d) {s['sharpe_daily_ann']} | exposure {s['exposure_pct']}%")
    print(f"FEE        : ${s['fees_paid']} terbayar (jujur dihitung)")
    print(f"lokal      : {out.name} + backtest-latest.json | wall {((time.time()-t_wall)/60):.1f} menit")


def _downsample(curve: list[list], max_pts: int) -> list[list]:
    if len(curve) <= max_pts:
        return curve
    step = len(curve) / max_pts
    idx = sorted({int(k * step) for k in range(max_pts)} | {len(curve) - 1})
    return [curve[i] for i in idx]


if __name__ == "__main__":
    main()
