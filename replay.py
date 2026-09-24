"""Replay core SIDANG — SATU logika untuk backtest historis dan live catch-up.

Semantik replay (deterministik, identik di backtest dan live):
  Untuk setiap candle tertutup t, urutan:
    1. kalau ada posisi terbuka  -> cek barrier di HIGH/LOW candle NYATA:
       - low <= SL             -> exit SL   (tie intrabar = SL, konservatif)
       - high >= TP (tanpa SL) -> exit TP
       - bars_held >= horizon  -> exit TIMEOUT di harga close candle t
    2. kalau flat -> adakan sidang Monte Carlo (2 generator) pada window
       lookback yang BERAKHIR di candle t -> verdict RulesJudge.
       verdict ENTER -> entry paper di harga CLOSE candle t.
       (fill = harga close candle sinyal; cron/scheduler jitter tidak
        mengubah angka — hanya kapan angkanya tercatat.)

  Paper-only. Uang virtual. Tidak ada kode order live di file ini.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from sidang.judge import RulesJudge
from sidang.ledger import PaperLedger
from sidang.mc_engine import SidangEngine

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}

DEFAULTS: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "interval": "5m",
    "lookback": 1000,
    "tp": 1.5,
    "sl": 0.75,
    "horizon": 96,
    "paths": 10_000,
    "fee": 0.25,
    "profile": "conservative",
    "block": 24,
    "cash": 10_000.0,
    "alloc": 25.0,  # % ekuitas per posisi
}


def iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trial_seed(symbol: str, open_ms: float, base: int = 0) -> int:
    """Seed deterministik per candle: backtest & live reproducible."""
    h = zlib.crc32(f"{symbol}:{int(open_ms)}".encode()) & 0x7FFFFFFF
    return (h ^ (base & 0x7FFFFFFF)) & 0x7FFFFFFF


class ReplayCore:
    """State machine paper-trading SIDANG atas deret candle tertutup."""

    def __init__(self, params: dict[str, Any], seed_base: int = 0) -> None:
        self.p = {**DEFAULTS, **params}
        self.judge = RulesJudge(self.p["profile"])
        # ledger dipasang pemanggil (path file bisa beda utk backtest/live)
        self.ledger: PaperLedger | None = None
        self.state: dict[str, Any] = self._fresh_state()
        self.seed_base = seed_base

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "params": self.p,
            "last_open_ms": 0.0,
            "cash": self.p["cash"],
            "position": None,
            "n_trials": 0,
            "n_enters": 0,
            "n_exits": 0,
            "n_wins": 0,
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "started_iso": None,
            "last_run_iso": None,
            "last_verdict": None,
            "last_verdict_iso": None,
            "last_equity": self.p["cash"],
        }

    # ---------- persistence (live catch-up) ----------

    def attach_ledger(self, ledger: PaperLedger) -> None:
        self.ledger = ledger

    def load_state(self, path: Path) -> bool:
        if not path.exists():
            return False
        s = json.loads(path.read_text())
        if s.get("params", {}).get("symbol") != self.p["symbol"] or s.get("params", {}).get("interval") != self.p["interval"]:
            raise RuntimeError(
                f"state.json milik {s.get('params', {}).get('symbol')} "
                f"{s.get('params', {}).get('interval')}; jalankan dengan parameter yang sama "
                "atau hapus data/state.json untuk mulai buku baru."
            )
        self.state = s
        # pulihkan posisi + kas ke ledger in-memory
        self.ledger.cash = float(s["cash"])
        self.ledger.position = s["position"]
        return True

    def save_state(self, path: Path) -> None:
        self.state["cash"] = self.ledger.cash
        self.state["position"] = self.ledger.position
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(path)  # atomic: state korup setengah-tulis tidak akan terbaca

    # ---------- equity ----------

    def equity(self, mark: float) -> float:
        if self.ledger is None:
            raise RuntimeError("ledger belum dipasang")
        if self.ledger.position is None:
            return self.ledger.cash
        return self.ledger.cash + self.ledger.position["qty"] * mark

    # ---------- inti: proses satu candle tertutup ----------

    def process_candle(
        self,
        open_ms: float,
        high: float,
        low: float,
        close: float,
        window_closes: np.ndarray,
    ) -> dict[str, Any]:
        """window_closes = lookback candle berakhir di candle INI (termasuk).

        Return event ringkas utk log pemanggil (enter/exit/trial/none).
        """
        ev: dict[str, Any] = {"open_ms": open_ms, "iso": iso(open_ms), "type": "none"}

        # 1) kelola posisi terbuka di candle NYATA
        pos = self.ledger.position
        if pos is not None:
            pos.setdefault("bars_held", 0)
            tie = (low <= pos["sl_price"]) and (high >= pos["tp_price"])
            if low <= pos["sl_price"]:
                reason = "stop_loss_tie" if tie else "stop_loss"
                trade = self.ledger.close(reason, pos["sl_price"])
                self._book_exit(trade)
                ev.update({"type": "exit", "reason": reason, "trade": trade})
            elif high >= pos["tp_price"]:
                trade = self.ledger.close("take_profit", pos["tp_price"])
                self._book_exit(trade)
                ev.update({"type": "exit", "reason": "take_profit", "trade": trade})
            elif pos["bars_held"] >= self.p["horizon"]:
                trade = self.ledger.close("timeout", close)
                self._book_exit(trade)
                ev.update({"type": "exit", "reason": "timeout", "trade": trade})
            else:
                pos["bars_held"] += 1

        # 2) flat -> adakan sidang pada window lookback
        if self.ledger.position is None and window_closes.size >= max(64, self.p["lookback"] // 2):
            engine = SidangEngine(window_closes, block_len=self.p["block"])
            trial = engine.run_trial(
                symbol=self.p["symbol"],
                interval=self.p["interval"],
                tp_pct=self.p["tp"],
                sl_pct=self.p["sl"],
                horizon=self.p["horizon"],
                n_paths=self.p["paths"],
                fee_rt_pct=self.p["fee"],
                seed=trial_seed(self.p["symbol"], open_ms, self.seed_base),
            )
            verdict = self.judge.evaluate(trial)
            self.state["n_trials"] += 1
            self.state["last_verdict"] = verdict.action
            self.state["last_verdict_iso"] = iso(open_ms)

            compact = {
                "ts": int(open_ms),
                "iso": iso(open_ms),
                "close": round(close, 2),
                "p_tp_mbb": round(trial.sims["mbb"].p_tp_first * 100, 2),
                "p_tp_fhs": (
                    round(trial.sims["fhs_garch"].p_tp_first * 100, 2)
                    if "fhs_garch" in trial.sims
                    else None
                ),
                "ev_mbb": round(trial.sims["mbb"].ev_pct, 3),
                "ev_fhs": (
                    round(trial.sims["fhs_garch"].ev_pct, 3) if "fhs_garch" in trial.sims else None
                ),
                "div_pp": round(trial.divergence_pp, 2),
                "be_pct": round(trial.breakeven_p_tp * 100, 2),
                "verdict": verdict.action,
                "p_tp_used": round(verdict.p_tp_used * 100, 2),
                "ev_used": round(verdict.ev_used_pct, 3),
                "ms": trial.elapsed_ms,
                "garch": (
                    {
                        "alpha": trial.extra["garch_fit"]["alpha"],
                        "beta": trial.extra["garch_fit"]["beta"],
                        "persistence": trial.extra["garch_fit"]["persistence"],
                    }
                    if trial.extra.get("garch_fit")
                    else None
                ),
            }
            ev.update({"type": "trial", "trial": compact})

            if verdict.action == "enter":
                meta = {
                    "p_tp": verdict.p_tp_used,
                    "ev_pct": verdict.ev_used_pct,
                    "reasons": verdict.reasons,
                    "entry_open_ms": int(open_ms),
                }
                self.ledger.enter(
                    symbol=self.p["symbol"],
                    price=close,
                    allocation_pct=self.p["alloc"],
                    tp_pct=self.p["tp"],
                    sl_pct=self.p["sl"],
                    meta=meta,
                )
                self.ledger.position["bars_held"] = 0
                self.state["n_enters"] += 1
                ev.update(
                    {
                        "type": "enter",
                        "position": {
                            k: self.ledger.position[k]
                            for k in ("qty", "entry_price", "tp_price", "sl_price")
                        },
                    }
                )

        self.state["last_open_ms"] = float(open_ms)
        self.state["last_equity"] = round(self.equity(close), 2)
        return ev

    def _book_exit(self, trade: dict[str, Any]) -> None:
        self.state["n_exits"] += 1
        self.state["realized_pnl"] = round(self.state["realized_pnl"] + trade["pnl"], 2)
        self.state["fees_paid"] = round(self.state["fees_paid"] + trade["fee"], 2)
        if trade["pnl"] > 0:
            self.state["n_wins"] += 1

    # ---------- log ringkas (dipakai live catch-up) ----------

    @staticmethod
    def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":"), default=float) + "\n")
