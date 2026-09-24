"""SIDANG live catch-up — loop paper otomatis untuk GitHub Actions (cron).

Dirancang untuk scheduler yang TIDAK presisi (cron GH bisa telat/macet):
  - state.json menyimpan candle terakhir yang sudah diproses;
  - tiap run menarik SEMUA candle tertutup sejak terakhir (catch-up),
    memproses berurutan dengan logika replay yang sama persis dengan
    backtest -> hasil DETERMINISTIK terhadap data, tidak terhadap jam cron;
  - auto-beli uang virtual ($10.000 default) saat verdict ENTER;
  - semua kegagalan DIBESAR-SUARAKAN (exit 1) -> run Actions merah.
    Bot trading yang mati diam-diam = bencana.

  python live_catchup.py                       # pakai data/ + default
  python live_catchup.py --bootstrap-hours 24  # run pertama: isi 24 jam
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import numpy as np

from replay import DEFAULTS, INTERVAL_MS, ReplayCore, iso
from sidang.feed import fetch_klines_range
from sidang.ledger import PaperLedger

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="SIDANG live catch-up (paper-only, uang virtual)")
    ap.add_argument("--symbol", default=DEFAULTS["symbol"])
    ap.add_argument("--interval", default=DEFAULTS["interval"])
    ap.add_argument("--profile", default="conservative", choices=["conservative", "balanced", "aggressive"])
    ap.add_argument("--data-dir", default=str(DATA))
    ap.add_argument("--bootstrap-hours", type=float, default=24.0, help="isi awal buku pada run pertama")
    ap.add_argument("--max-catchup-bars", type=int, default=3000, help="guard: proses maksimal N candle per run")
    ap.add_argument("--seed-base", type=int, default=0)
    return ap.parse_args()


def main() -> int:
    a = parse_args()
    data_dir = Path(a.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "state.json"
    trials_path = data_dir / "trials.jsonl"
    equity_path = data_dir / "equity.jsonl"
    runs_path = data_dir / "runs.jsonl"
    iv_ms = INTERVAL_MS[a.interval]

    params = {
        "symbol": a.symbol, "interval": a.interval, "profile": a.profile,
        "lookback": DEFAULTS["lookback"], "tp": DEFAULTS["tp"], "sl": DEFAULTS["sl"],
        "horizon": DEFAULTS["horizon"], "paths": DEFAULTS["paths"], "fee": DEFAULTS["fee"],
        "block": DEFAULTS["block"], "cash": DEFAULTS["cash"], "alloc": DEFAULTS["alloc"],
    }
    core = ReplayCore(params, seed_base=a.seed_base)
    ledger = PaperLedger(cash=params["cash"], path=data_dir / "ledger.jsonl", fee_rt_pct=params["fee"])
    core.attach_ledger(ledger)

    t0 = time.time()
    now_ms = time.time() * 1000.0
    had_state = core.load_state(state_path)

    if not had_state:
        # run pertama: buku baru, mundur bootstrap-hours (dibulatkan ke batas candle)
        boot_ms = (now_ms - a.bootstrap_hours * 3_600_000.0)
        boot_ms = boot_ms - (boot_ms % iv_ms)
        core.state["last_open_ms"] = boot_ms
        core.state["started_iso"] = iso(boot_ms)
        print(f"[boot ] buku baru: replay {a.bootstrap_hours:.0f} jam terakhir dari {iso(boot_ms)}")

    last_open = float(core.state["last_open_ms"])
    fetch_from = last_open - (params["lookback"] + 5) * iv_ms  # window lookback utk sidang
    data = fetch_klines_range(a.symbol, a.interval, fetch_from, now_ms)

    new_idx = [i for i, t in enumerate(data["open_time"]) if float(t) > last_open]
    n_new = len(new_idx)
    if n_new > a.max_catchup_bars:
        raise RuntimeError(
            f"catch-up terlalu dalam: {n_new} candle baru > guard {a.max_catchup_bars}. "
            "Jalankan manual atau naikkan --max-catchup-bars."
        )
    if n_new == 0:
        staleness_min = (now_ms - last_open) / 60000.0
        if staleness_min > 3.0 * iv_ms / 60000.0 + 15.0:
            raise RuntimeError(
                f"tidak ada candle baru padahal candle terakhir {staleness_min:.0f} menit lalu — "
                "kemungkinan data bursa/API bermasalah. Tidak diam-diam."
            )
        core.state["last_run_iso"] = iso(now_ms)
        core.save_state(state_path)
        ReplayCore.append_jsonl(runs_path, {
            "run": iso(now_ms), "candles": 0, "trials": 0, "enters": 0, "exits": 0,
            "equity": core.state["last_equity"], "elapsed_s": round(time.time() - t0, 1), "status": "idle",
        })
        print(f"[idle ] tidak ada candle baru (terakhir {iso(last_open)}); heartbeat dicatat")
        return 0

    n_trials = n_enters = n_exits = 0
    first_open = float(data["open_time"][new_idx[0]])
    print(
        f"[catch] {n_new} candle baru: {iso(first_open)} .. {iso(float(data['open_time'][new_idx[-1]]))} "
        f"| posisi: {'ADA' if ledger.position else 'flat'} | equity ${core.state['last_equity']:,.2f}"
    )

    for i in new_idx:
        ev = core.process_candle(
            open_ms=float(data["open_time"][i]),
            high=float(data["high"][i]),
            low=float(data["low"][i]),
            close=float(data["close"][i]),
            window_closes=data["close"][max(0, i - params["lookback"] + 1): i + 1],
        )
        if ev["type"] == "trial":
            n_trials += 1
            ReplayCore.append_jsonl(trials_path, ev["trial"])
            print(
                f"  [{ev['iso']}] close=${ev['trial']['close']:>10,.2f} "
                f"P(TP)={ev['trial']['p_tp_used']:5.1f}% EV={ev['trial']['ev_used']:+.2f}% "
                f"div={ev['trial']['div_pp']:.1f}pp -> {ev['trial']['verdict'].upper()}"
            )
        elif ev["type"] == "enter":
            n_enters += 1
            p = ev["position"]
            print(
                f"  [ENTER] qty={p['qty']:.6f} @ ${p['entry_price']:,.2f} "
                f"TP=${p['tp_price']:,.2f} SL=${p['sl_price']:,.2f} (uang virtual)"
            )
        elif ev["type"] == "exit":
            n_exits += 1
            tr = ev["trade"]
            print(
                f"  [EXIT ] {ev['reason']:>13} @ ${tr['exit_price']:,.2f} "
                f"pnl={tr['pnl']:+.2f} (gross {tr['pnl_gross']:+.2f}, fee {tr['fee']:.2f})"
            )

    # titik ekuitas terakhir run ini (mark-to-close)
    ReplayCore.append_jsonl(equity_path, {
        "ts": int(float(data["open_time"][new_idx[-1]])), "iso": iso(float(data["open_time"][new_idx[-1]])),
        "equity": core.state["last_equity"],
    })
    core.state["last_run_iso"] = iso(now_ms)
    core.save_state(state_path)
    ReplayCore.append_jsonl(runs_path, {
        "run": iso(now_ms), "candles": n_new, "trials": n_trials, "enters": n_enters,
        "exits": n_exits, "equity": core.state["last_equity"],
        "elapsed_s": round(time.time() - t0, 1), "status": "ok",
    })

    snap = {
        "cash": round(ledger.cash, 2), "equity": core.state["last_equity"],
        "position": ledger.position, "n_trials": core.state["n_trials"],
        "n_enters": core.state["n_enters"], "n_exits": core.state["n_exits"],
        "n_wins": core.state["n_wins"], "realized_pnl": core.state["realized_pnl"],
    }
    print(f"[done ] {json.dumps(snap, default=float)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()  # GAK BOLEH senyap — Actions harus merah kalau apa pun pecah
        raise SystemExit(1)
