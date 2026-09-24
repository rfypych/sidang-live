"""Jury SIDANG: mengubah hasil simulasi menjadi keputusan enter/stand-down.

System 0 (sekarang)   : RulesJudge — ambang keras, pesimis, bisa dijelaskan.
System 1 (W2, opsional): LayaJudge — cascade binary NL (zona tervalidasi
                         6/6), hanya mengadili ANGKA hasil simulasi,
                         bukan 30 indikator mentah (zona degenerate).

Prinsip agregasi lintas generator: pesimis (min) untuk P_tp dan EV.
Kalau dua generator tidak setuju >10pp -> regime tidak stabil ->
STAND DOWN, bukan rata-rata lalu berharap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mc_engine import TrialResult

PROFILES: dict[str, dict[str, float]] = {
    # margin di atas breakeven (pp), EV minimal (%), divergensi maks (pp)
    "conservative": {"be_margin_pp": 12.0, "min_ev_pct": 0.50, "max_div_pp": 8.0},
    "balanced": {"be_margin_pp": 8.0, "min_ev_pct": 0.30, "max_div_pp": 10.0},
    "aggressive": {"be_margin_pp": 5.0, "min_ev_pct": 0.15, "max_div_pp": 15.0},
}


@dataclass
class Verdict:
    action: str  # "enter" | "stand_down"
    reasons: list[str] = field(default_factory=list)
    p_tp_used: float = 0.0
    ev_used_pct: float = 0.0
    profile: str = "balanced"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reasons": self.reasons,
            "p_tp_used": round(self.p_tp_used, 4),
            "ev_used_pct": round(self.ev_used_pct, 4),
            "profile": self.profile,
        }


class RulesJudge:
    """System 0: aturan keras atas angka simulasi."""

    def __init__(self, profile: str = "balanced") -> None:
        if profile not in PROFILES:
            raise ValueError(f"profil tidak dikenal: {profile}")
        self.profile = profile
        self.cfg = PROFILES[profile]

    def evaluate(self, trial: TrialResult) -> Verdict:
        reasons: list[str] = []
        if not trial.sims:
            return Verdict("stand_down", ["tidak ada hasil simulasi"], profile=self.profile)

        p_tp = min(s.p_tp_first for s in trial.sims.values())
        ev = min(s.ev_pct for s in trial.sims.values())

        be = trial.breakeven_p_tp
        if be == float("inf"):
            return Verdict("stand_down", ["TP <= fee: struktur tidak bisa profit"], profile=self.profile)

        need = be * 100.0 + self.cfg["be_margin_pp"]
        if p_tp * 100.0 < need:
            reasons.append(
                f"P(TP duluan) {p_tp*100:.1f}% < breakeven+margin {need:.1f}% "
                f"(breakeven murni {be*100:.1f}%)"
            )
        if ev < self.cfg["min_ev_pct"]:
            reasons.append(f"EV pesimis {ev:+.2f}% < minimum {self.cfg['min_ev_pct']:.2f}%")
        if trial.divergence_pp > self.cfg["max_div_pp"]:
            reasons.append(
                f"generator berselisih {trial.divergence_pp:.1f}pp > "
                f"{self.cfg['max_div_pp']:.0f}pp: regime tidak stabil"
            )

        if reasons:
            return Verdict("stand_down", reasons, p_tp, ev, self.profile)
        return Verdict(
            "enter",
            [
                f"P(TP duluan) {p_tp*100:.1f}% >= {need:.1f}% (margin aman)",
                f"EV pesimis {ev:+.2f}% >= {self.cfg['min_ev_pct']:.2f}%",
                f"divergence {trial.divergence_pp:.1f}pp dalam toleransi",
            ],
            p_tp,
            ev,
            self.profile,
        )


# ---------------------------------------------------------------------------
# W2: pertanyaan binary untuk LayaJudge (cascade NL English — zona 6/6).
# Sengaja didefinisikan di sini supaya kontraknya dikunci sejak W1.
# ---------------------------------------------------------------------------

Q_ENTRY = (
    "Monte Carlo over {paths} simulated paths says: probability of hitting "
    "take-profit before stop-loss is {p_tp:.1f}%, breakeven probability is "
    "{be:.1f}%, and pessimistic expected value is {ev:+.2f}% per trade after "
    "{fee:.2f}% roundtrip fees. Risk profile: {profile}. Is this trade worth "
    "entering?"
)

Q_REGIME = (
    "Two independent market generators (block bootstrap and GARCH filtered "
    "historical simulation) disagree by {div:.1f} percentage points on the "
    "win probability of the same trade. Is the current regime stable enough "
    "to trust either simulation?"
)

Q_TAIL = (
    "The 5th percentile simulated outcome of this trade is {p5:.2f}% and the "
    "stop-loss caps the loss at {sl:.2f}% plus fees. Is the worst-case "
    "acceptable for a {profile} account?"
)


class LayaJudge:
    """W2 stub: binary NL cascade ke model Laya lokal (ONNX/int8).

    Rencana integrasi:
    1. Muat laya.onnx (int8, teacher-student agreement >= 95% vs fp32).
    2. Tanya Q_ENTRY; confidence >= 0.6 -> lanjut, else abstain.
    3. Q_REGIME; abstain/yes-tidak-stabil -> stand down.
    4. Q_TAIL sebagai konfirmasi terakhir.
    Abstention adalah fitur: Laya bilang 'tidak yakin' = tidak trade.
    """

    def __init__(self, model_path: str, profile: str = "balanced") -> None:
        raise NotImplementedError(
            "LayaJudge = W2. Model Laya belum tersedia di sandbox ini; "
            "gunakan RulesJudge. Kontrak pertanyaan sudah dikunci di "
            "Q_ENTRY/Q_REGIME/Q_TAIL."
        )
