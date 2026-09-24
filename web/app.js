/* SIDANG live monitor — paper-only. Data: data/*.jsonl + reports/backtest-latest.json
   Nol dependensi eksternal; refresh otomatis tiap 60 detik. */

const $ = (id) => document.getElementById(id);
const WIB = { timeZone: "Asia/Jakarta" };
const fmtUSD = (v, d = 2) =>
  (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("id-ID", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const fmtWIB = (iso, withDate = true) => {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("T") ? iso : Number(iso));
  if (isNaN(d)) return iso;
  return d.toLocaleString("id-ID", {
    ...(withDate ? { day: "numeric", month: "short" } : {}),
    hour: "2-digit", minute: "2-digit", ...WIB,
  });
};
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "muted");
const REASON = { take_profit: "TP", stop_loss: "SL", stop_loss_tie: "SL (tie)", timeout: "timeout" };

async function fetchText(path) {
  try {
    const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) return null;
    return await r.text();
  } catch {
    return null;
  }
}
async function fetchJSON(path) {
  const t = await fetchText(path);
  if (t == null) return null;
  try { return JSON.parse(t); } catch { return null; }
}
async function fetchJSONL(path) {
  const t = await fetchText(path);
  if (t == null) return [];
  const out = [];
  for (const line of t.split("\n")) {
    const l = line.trim();
    if (!l) continue;
    try { out.push(JSON.parse(l)); } catch {}
  }
  return out;
}

function ago(iso) {
  if (!iso) return Infinity;
  const ms = Date.now() - new Date(iso).getTime();
  return ms / 60000;
}

async function load() {
  const [state, trials, ledger, equity, runs, backtest] = await Promise.all([
    fetchJSON("data/state.json"),
    fetchJSONL("data/trials.jsonl"),
    fetchJSONL("data/ledger.jsonl"),
    fetchJSONL("data/equity.jsonl"),
    fetchJSONL("data/runs.jsonl"),
    fetchJSON("reports/backtest-latest.json"),
  ]);

  renderHeader(state, runs);
  renderCards(state, ledger);
  renderToday(trials, ledger);
  renderEquityChart(equity);
  renderTrialsChart(trials);
  renderTrades(ledger);
  renderRuns(runs);
  renderBacktest(backtest);
  $("updated").textContent = "diperbarui " + new Date().toLocaleTimeString("id-ID", WIB) + " WIB";
}

function renderHeader(state, runs) {
  const lastRun = state ? state.last_run_iso : null;
  const ageMin = ago(lastRun);
  const alive = ageMin < 40;
  const pill = $("statusPill");
  pill.textContent = alive ? "● BOT HIDUP" : "● TERLAMBAT / MATI";
  pill.className = "pill " + (alive ? "ok" : "bad");
  const p = state ? state.params : {};
  $("metaLine").textContent = `${p.symbol || "—"} ${p.interval || ""} • profil ${p.profile || "—"} • TP ${p.tp}% / SL ${p.sl}% • fee ${p.fee}%`;
  $("lastRun").textContent = "run terakhir: " + (lastRun ? `${fmtWIB(lastRun)} WIB (${Math.round(ageMin)} mnt lalu)` : "belum ada");
  const banner = $("banner");
  if (!state) {
    banner.style.display = "block";
    banner.innerHTML = "Record pertama belum ada. Jalankan <span class='mono'>python live_catchup.py</span> (lokal) atau aktifkan workflow GitHub Actions — lihat <a href='setup/SETUP.md'>setup/SETUP.md</a>.";
  } else if (ago(runs.length ? runs[runs.length - 1].run : null) > 40) {
    banner.style.display = "block";
    banner.innerHTML = "Bot <b>terlambat >40 menit</b>. Kemungkinan: cron GitHub Actions belum aktif (lihat <a href='setup/SETUP.md'>setup/SETUP.md</a>), Actions gagal (cek tab Actions di repo), atau sedang di-<i>queue</i>.";
  } else {
    banner.style.display = "none";
  }
}

function renderCards(state, ledger) {
  if (!state) {
    ["cEquity", "cPnl", "cTrades", "cPos", "cVerdict"].forEach((id) => ($(id).textContent = "—"));
    return;
  }
  const eq = state.last_equity ?? state.cash ?? 0;
  const pnl = state.realized_pnl ?? 0;
  $("cEquity").textContent = fmtUSD(eq);
  $("cEquity").className = "value";
  $("cEquitySub").textContent = `kertas ${fmtUSD(state.cash)} • mulai ${fmtUSD(state.params ? state.params.cash : 10000, 0)}`;
  $("cPnl").textContent = fmtUSD(pnl);
  $("cPnl").className = "value " + cls(pnl);
  $("cPnlSub").textContent = `fee terbayar ${fmtUSD(state.fees_paid ?? 0)}`;
  const wr = state.n_exits ? Math.round((100 * state.n_wins) / state.n_exits) : null;
  $("cTrades").textContent = state.n_exits != null ? `${state.n_exits} (${wr == null ? "—" : wr + "%"} WR)` : "—";
  $("cTrades").className = "value";
  $("cTradesSub").textContent = `${state.n_enters ?? 0} entry • ${state.n_trials ?? 0} sidang`;
  const pos = state.position;
  if (pos) {
    $("cPos").innerHTML = `LONG ${fmtUSD(pos.entry_price)}<div class="sub">TP ${fmtUSD(pos.tp_price)} • SL ${fmtUSD(pos.sl_price)} • qty ${pos.qty.toFixed(5)}</div>`;
    $("cPos").className = "value pos";
  } else {
    $("cPos").textContent = "FLAT";
    $("cPos").className = "value";
    $("cPosSub").textContent = "menunggu verdict ENTER";
  }
  $("cVerdict").textContent = (state.last_verdict || "—").toUpperCase().replace("_", " ");
  $("cVerdict").className = "value " + (state.last_verdict === "enter" ? "pos" : "muted");
  $("cVerdictSub").textContent = state.last_verdict_iso ? fmtWIB(state.last_verdict_iso) + " WIB" : "—";
}

function renderToday(trials, ledger) {
  const today = new Date().toLocaleDateString("id-ID", { ...WIB });
  const isToday = (iso) => iso && new Date(iso).toLocaleDateString("id-ID", { ...WIB }) === today;
  const t = trials.filter((x) => isToday(x.iso));
  const enters = t.filter((x) => x.verdict === "enter").length;
  const exits = ledger.filter((e) => e.event === "exit" && isToday(new Date(e.ts * 1000).toISOString()));
  const pnl = exits.reduce((s, e) => s + (e.trade ? e.trade.pnl : 0), 0);
  $("todayLine").innerHTML =
    `Hari ini (WIB): <b>${t.length}</b> sidang • <b class="pos">${enters}</b> ENTER • ` +
    `<b>${exits.length}</b> exit • PnL <b class="${cls(pnl)}">${fmtUSD(pnl)}</b>`;
}

function renderEquityChart(equity) {
  const pts = equity.filter((e) => e.ts).map((e) => [e.ts, e.equity]);
  window.SIDANGCharts.drawChart($("chartEquity"), {
    series: pts.length ? [{ points: pts, color: "#4ade80", width: 1.8 }] : [],
    hlines: [{ y: 10000, color: "#fbbf24", label: "modal awal $10.000" }],
    yFmt: (v) => "$" + Math.round(v).toLocaleString("id-ID"),
    empty: "kurva ekuitas menyusul setelah run pertama",
  });
}

function renderTrialsChart(trials) {
  const tail = trials.slice(-1500);
  const be = tail.length ? tail[tail.length - 1].be_pct : 44.4;
  const pts = tail.filter((x) => x.p_tp_used != null).map((x) => [x.ts * 1, x.p_tp_used]);
  const markers = tail.filter((x) => x.verdict === "enter").map((x) => ({ x: x.ts, y: x.p_tp_used, color: "#4ade80", r: 3.5 }));
  window.SIDANGCharts.drawChart($("chartTrials"), {
    series: pts.length ? [{ points: pts, color: "#60a5fa", width: 1.3 }] : [],
    hlines: [{ y: be, color: "#f87171", label: `breakeven ${be.toFixed(1)}%` }],
    markers,
    yFmt: (v) => v.toFixed(0) + "%",
    empty: "menunggu sidang pertama",
  });
  const last = tail[tail.length - 1];
  if (last) {
    $("trialsNote").textContent =
      `sidang terakhir ${fmtWIB(last.iso)} WIB: P(TP) pesimis ${last.p_tp_used.toFixed(1)}% (mbb ${last.p_tp_mbb.toFixed(1)}% / garch ${last.p_tp_fhs != null ? last.p_tp_fhs.toFixed(1) + "%" : "—"}), EV ${last.ev_used.toFixed(2)}%, divergence ${last.div_pp.toFixed(1)}pp → ${last.verdict.toUpperCase()} • titik hijau = ENTER`;
  }
}

function renderTrades(ledger) {
  const exits = ledger.filter((e) => e.event === "exit" && e.trade).slice(-15).reverse();
  const tb = $("tradesBody");
  tb.innerHTML = "";
  if (!exits.length) {
    tb.innerHTML = "<tr><td colspan='8' class='muted' style='font-family:inherit'>Belum ada trade tertutup. Paper bot ini selektif — tidak ada trade juga berarti sistem bekerja.</td></tr>";
    return;
  }
  for (const e of exits) {
    const t = e.trade;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${fmtWIB(new Date(e.ts * 1000).toISOString())}</td>` +
      `<td>${fmtUSD(t.entry_price)}</td><td>${fmtUSD(t.exit_price)}</td>` +
      `<td>${REASON[t.reason] || t.reason}</td>` +
      `<td class="${cls(t.pnl_gross)}">${fmtUSD(t.pnl_gross)}</td>` +
      `<td class="muted">${fmtUSD(t.fee)}</td>` +
      `<td class="${cls(t.pnl)}"><b>${fmtUSD(t.pnl)}</b></td>` +
      `<td class="${cls(t.pnl_pct)}">${fmtPct(t.pnl_pct, 2)}</td>`;
    tb.appendChild(tr);
  }
}

function renderRuns(runs) {
  const tb = $("runsBody");
  tb.innerHTML = "";
  const tail = runs.slice(-8).reverse();
  if (!tail.length) {
    tb.innerHTML = "<tr><td colspan='7' class='muted' style='font-family:inherit'>belum ada run tercatat</td></tr>";
    return;
  }
  for (const r of tail) {
    const tr = document.createElement("tr");
    const st = r.status === "ok" ? "pos" : r.status === "idle" ? "muted" : "neg";
    tr.innerHTML =
      `<td>${fmtWIB(r.run)}</td>` +
      `<td>${r.candles}</td><td>${r.trials}</td>` +
      `<td>${r.enters} / ${r.exits}</td>` +
      `<td>${fmtUSD(r.equity)}</td>` +
      `<td class="muted">${(r.elapsed_s ?? 0).toFixed(1)}s</td>` +
      `<td class="${st}">${r.status}</td>`;
    tb.appendChild(tr);
  }
}

function renderBacktest(bt) {
  const box = $("backtestSection");
  if (!bt) {
    $("backtestBody").innerHTML = "<span class='muted'>belum ada laporan backtest — jalankan <span class='mono'>python backtest.py</span> atau workflow manual di tab Actions</span>";
    return;
  }
  const s = bt.stats, d = bt.sidang;
  const rows = [
    ["periode", `${bt.period.from.slice(0, 10)} .. ${bt.period.to.slice(0, 10)} (${bt.period.months} bln, ${bt.period.bars.toLocaleString("id-ID")} bar)`],
    ["sidang", `${d.n_trials.toLocaleString("id-ID")} trial • ${d.n_enters} ENTER (${d.enter_rate_pct}%)`],
    ["hasil", `${fmtUSD(s.starting_cash, 0)} → <b class="${cls(s.total_return_pct)}">${fmtUSD(s.final_equity)}</b> (${fmtPct(s.total_return_pct, 2)})`],
    ["trade", `${s.n_trades} • WR ${s.win_rate_pct ?? "—"}% • PF ${s.profit_factor ?? "—"}`],
    ["risiko", `maxDD ${s.max_drawdown_pct}% • Sharpe(harian) ${s.sharpe_daily_ann} • exposure ${s.exposure_pct}%`],
    ["fee", `${fmtUSD(s.fees_paid)} terbayar • ${JSON.stringify(s.exit_reasons)}`],
  ];
  $("backtestBody").innerHTML =
    rows.map(([k, v]) => `<div><span class="k">${k}</span><span>${v}</span></div>`).join("") +
    `<div><span class="k">peringatan</span><span class="muted small">${bt.honesty.disclaimer}</span></div>` +
    `<div><span class="k">unduh</span><span><a href="reports/backtest-latest.json">backtest-latest.json</a></span></div>`;
  window.SIDANGCharts.drawChart($("chartBacktest"), {
    series: [{ points: bt.equity_curve, color: "#60a5fa", width: 1.5 }],
    hlines: [{ y: s.starting_cash, color: "#fbbf24", label: "modal awal" }],
    yFmt: (v) => "$" + Math.round(v).toLocaleString("id-ID"),
    empty: "—",
  });
}

document.addEventListener("DOMContentLoaded", () => {
  load();
  setInterval(load, 60000);
  $("btnRefresh").addEventListener("click", load);
});
