"""Paper ledger SIDANG — jejak audit lengkap, gaya agent_log.jsonl Jev-Trades.

Setiap event (trial, entry, exit, error) ditulis append-only ke JSONL.
Paper-only: tidak ada, dan tidak akan ada, kode order live di modul ini.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class PaperLedger:
    def __init__(
        self,
        cash: float = 10_000.0,
        path: str | Path = "sidang/reports/ledger.jsonl",
        fee_rt_pct: float = 0.25,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cash = float(cash)
        self.starting_cash = float(cash)
        self.fee_rt_pct = float(fee_rt_pct)  # dipotong dari realized pnl
        self.position: dict[str, Any] | None = None
        self.trades: list[dict[str, Any]] = []

    # ---------- internal ----------

    def _log(self, event: dict[str, Any]) -> None:
        event["ts"] = time.time()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, separators=(",", ":"), default=float) + "\n")

    def _equity(self, mark: float | None) -> float:
        if self.position is None:
            return self.cash
        mark = mark if mark is not None else self.position["entry_price"]
        return self.cash + self.position["qty"] * mark

    # ---------- API ----------

    def record_trial(self, trial: Any, verdict: Any) -> None:
        self._log(
            {
                "event": "trial",
                "trial": trial.as_dict(),
                "verdict": verdict.as_dict(),
                "equity": self._equity(trial.entry_price),
            }
        )

    def enter(
        self,
        symbol: str,
        price: float,
        allocation_pct: float,
        tp_pct: float,
        sl_pct: float,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.position is not None:
            raise ValueError("posisi masih terbuka; satu posisi saja (W1)")
        alloc = min(self.cash, self._equity(None) * allocation_pct / 100.0)
        if alloc <= 0:
            raise ValueError("kertas habis")
        qty = alloc / price
        self.cash -= alloc
        self.position = {
            "symbol": symbol,
            "qty": qty,
            "entry_price": price,
            "tp_price": price * (1 + tp_pct / 100.0),
            "sl_price": price * (1 - sl_pct / 100.0),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "entry_ts": time.time(),
            "meta": meta or {},
        }
        event = {"event": "enter", "position": dict(self.position), "cash": self.cash}
        self._log(event)
        return dict(self.position)

    def check_barriers(self, high: float, low: float) -> dict[str, Any] | None:
        """Cek TP/SL pada candle NYATA (high/low), bukan hasil simulasi.

        Tie intrabar (high >= TP dan low <= SL di candle sama):
        konservatif -> Stop Loss dieksekusi duluan.
        """
        p = self.position
        if p is None:
            return None
        if low <= p["sl_price"]:
            return self.close("stop_loss", p["sl_price"])
        if high >= p["tp_price"]:
            return self.close("take_profit", p["tp_price"])
        return None

    def close(self, reason: str, price: float) -> dict[str, Any]:
        p = self.position
        if p is None:
            raise ValueError("tidak ada posisi")
        proceeds = p["qty"] * price
        self.cash += proceeds
        # fee realistis: ~fee_rt_pct total roundtrip atas notional rata-rata dua sisi
        fee = p["qty"] * (p["entry_price"] + price) * (self.fee_rt_pct / 2.0 / 100.0)
        self.cash -= fee
        gross = proceeds - p["qty"] * p["entry_price"]
        pnl = gross - fee
        trade = {
            "symbol": p["symbol"],
            "qty": p["qty"],
            "entry_price": p["entry_price"],
            "exit_price": price,
            "reason": reason,
            "pnl": pnl,
            "pnl_gross": gross,
            "fee": fee,
            "pnl_pct": (price / p["entry_price"] - 1.0) * 100.0,
            "holding_s": time.time() - p["entry_ts"],
            "meta": p.get("meta", {}),
        }
        self.trades.append(trade)
        self.position = None
        event = {"event": "exit", "trade": trade, "cash": self.cash, "equity": self.cash}
        self._log(event)
        return trade

    def snapshot(self, mark: float | None = None) -> dict[str, Any]:
        pos = dict(self.position) if self.position else None
        return {
            "cash": round(self.cash, 2),
            "equity": round(self._equity(mark), 2),
            "starting_cash": self.starting_cash,
            "open_position": pos,
            "n_closed_trades": len(self.trades),
            "realized_pnl": round(sum(t["pnl"] for t in self.trades), 2),
        }
