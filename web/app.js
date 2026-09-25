/* SIDANG live monitor v2 — paper-only. Data: data/*.jsonl + reports/*.json
   Nol dependensi eksternal; refresh otomatis tiap 60 detik.
   Chart harga: data/klines.jsonl (telemetry — bukan buku keputusan). */

const $ = (id) => document.getElementById(id);
const WIB = { timeZone: "Asia/Jakarta" };
const IV_MS = 300000; // 5m — pembulatan marker exit ke candle

const fmtUSD = (v, d = 2) =>
  (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("id-ID", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const fmtPrice = (v) => v.toLocaleString("id-ID", { maximumFractionDigits: 1 });
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
const PAL = window.SIDANGCharts.C;

let priceChart = null;

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
  return (Date.now() - new Date(iso).getTime()) / 60000;
}

async function load() {
  const [state, trials, ledger, equity, runs, klines, btCon, btBal, btAgg, btLatest] = await Promise.all([
    fetchJSON("data/state.json"),
    fetchJSONL("data/trials.jsonl"),
    fetchJSONL("data/ledger.jsonl"),
    fetchJSONL("data/equity.jsonl"),
    fetchJSONL("data/runs.jsonl"),
    fetchJSONL("data/klines.jsonl"),
    fetchJSON("reports/backtest-conservative-latest.json"),
    fetchJSON("reports/backtest-balanced-latest.json"),
    fetchJSON("reports/backtest-aggressive-latest.json"),
    fetchJSON("reports/backtest-latest.json"),
  ]);

  renderHeader(state, runs);
  renderExplainer(state, trials);
  renderCards(state, ledger);
  renderToday(trials, ledger);
  renderPrice(state, klines, ledger);
  renderEquityChart(equity);
  renderTrialsChart(trials);
  renderTrades(ledger);
  renderRuns(runs);
  renderBacktest({ conservative: btCon, balanced: btBal, aggressive: btAgg, latest: btLatest });
  $("updated").textContent = "diperbarui " + new Date().toLocaleTimeString("id-ID", WIB) + " WIB";
}

function renderHeader(state, runs) {
  const lastRun = state ? state.last_run_iso : null;
  const ageMin = ago(lastRun);
  // Cron gratisan GitHub bisa delay/drop tick di jam sibuk (puncak tiap awal jam) —
  // itu ANTRE, bukan mati; catch-up menjamin data kejar begitu tick berikutnya jalan.
  // Jadi: hijau < 40 mnt, kuning ANTRE 40–75 mnt, merah baru > 75 mnt (5 tick berurutan hilang).
  const pill = $("statusPill");
  if (ageMin < 40) {
    pill.textContent = "● BOT HIDUP";
    pill.className = "pill ok";
  } else if (ageMin < 75) {
    pill.textContent = "● ANTRE (cron gratisan)";
    pill.className = "pill warn";
  } else {
    pill.textContent = "● TERLAMBAT / MATI";
    pill.className = "pill bad";
  }
  const p = state ? state.params : {};
  $("metaLine").textContent = `${p.symbol || "—"} ${p.interval || ""} • profil ${p.profile || "—"} • TP ${p.tp}% / SL ${p.sl}% • fee ${p.fee}%`;
  $("priceTitle").textContent = `${p.symbol || "BTCUSDT"} · ${p.interval || "5m"} — aksi harga`;
  $("lastRun").textContent =
    "run terakhir: " +
    (lastRun ? `${fmtWIB(lastRun)} WIB (${Math.round(ageMin)} mnt lalu)` : "belum ada") +
    " • jadwal: menit 04/19/34/49 (menit sepi, bebas puncak antrean)";
  const banner = $("banner");
  if (!state) {
    banner.style.display = "block";
    banner.innerHTML = "Record pertama belum ada. Jalankan <span class='mono'>python live_catchup.py</span> (lokal) atau aktifkan workflow GitHub Actions — lihat <a href='setup/SETUP.md'>setup/SETUP.md</a>.";
  } else if (ago(runs.length ? runs[runs.length - 1].run : null) > 75) {
    banner.style.display = "block";
    banner.innerHTML =
      "Bot <b>tidak jalan &gt;75 menit</b> — ini bukan sekadar antrean biasa (delay 30–60 mnt itu normal di jam sibuk). " +
      "Begitu bot dipanggil lagi, semua candle terlewat <b>dikejar otomatis</b> (catch-up) — data tidak hilang. " +
      "Mau paksa jalan sekarang? Buka <a href='https://github.com/rfypych/sidang-live/actions/workflows/live.yml' target='_blank' rel='noopener'>tab Actions → Run workflow</a>. " +
      "Kalau tetap mati, cek <a href='setup/SETUP.md'>setup/SETUP.md</a>.";
  } else {
    banner.style.display = "none";
  }
}

function renderExplainer(state, trials) {
  // ingat preferensi buka/tutup panel "Ini apa sih?"
  const ex = $("explainer");
  if (ex) {
    if (localStorage.getItem("sidang-explainer") === "closed") ex.open = false;
    ex.addEventListener("toggle", () =>
      localStorage.setItem("sidang-explainer", ex.open ? "open" : "closed"));
  }

  // angka hidup blok "kenapa 0 trade"
  const last = trials.length ? trials[trials.length - 1] : null;
  const ePtp = $("exPtp"), eBe = $("exBe"), eTr = $("exTrials"), eEn = $("exEnters");
  if (ePtp && last && last.p_tp_used != null) ePtp.textContent = Number(last.p_tp_used).toFixed(1) + "%";
  if (eBe && last && last.be_pct != null) eBe.textContent = Number(last.be_pct).toFixed(1) + "%";
  if (eTr && state) eTr.textContent = `${state.n_trials ?? 0}\u00d7 sidang`;
  if (eEn && state) eEn.textContent = `${state.n_enters ?? 0}\u00d7`;

  // jam OOS 6 bulan (mulai dari started_iso di buku besar, bukan hardcode)
  const fill = $("oosFill");
  if (fill && state && state.started_iso) {
    const start = new Date(state.started_iso).getTime();
    const endD = new Date(start); endD.setMonth(endD.getMonth() + 6);
    const chkD = new Date(start); chkD.setMonth(chkD.getMonth() + 3);
    const end = endD.getTime();
    const now = Date.now();
    const pct = Math.max(0, Math.min(100, ((now - start) / (end - start)) * 100));
    fill.style.width = pct.toFixed(2) + "%";
    const pctEl = $("oosPct");
    if (pctEl) pctEl.textContent = pct.toFixed(1) + "%";
    const daysEl = $("oosDays");
    if (daysEl) {
      const dayN = Math.max(0, Math.floor((now - start) / 864e5));
      const f = (d) => d.toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric", ...WIB });
      daysEl.textContent =
        `hari ke-${dayN + 1} dari \u00b1182 \u2022 cek pertama ${f(chkD)} \u2022 tamat ${f(endD)}`;
    }
  }
}

function chipEl(kind, text) {
  return `<span class="chip ${kind}">${text}</span>`;
}

function renderCards(state, ledger) {
  if (!state) {
    ["cEquity", "cPnl", "cTrades", "cPos", "cVerdict"].forEach((id) => ($(id).textContent = "—"));
    return;
  }
  const eq = state.last_equity ?? state.cash ?? 0;
  const start = state.params ? state.params.cash : 10000;
  const pnl = state.realized_pnl ?? 0;

  $("cEquity").textContent = fmtUSD(eq);
  $("cEquity").className = "value";
  const ret = start ? (eq / start - 1) * 100 : 0;
  $("cEquityChip").outerHTML = chipEl(ret > 0.005 ? "up" : ret < -0.005 ? "down" : "flat", fmtPct(ret, 2)).replace("<span", '<span id="cEquityChip"');
  $("cEquitySub").textContent = `mulai ${fmtUSD(start, 0)}`;

  $("cPnl").textContent = fmtUSD(pnl);
  $("cPnl").className = "value " + cls(pnl);
  $("cPnlChip").outerHTML = chipEl(pnl > 0 ? "up" : pnl < 0 ? "down" : "flat", "fee " + fmtUSD(state.fees_paid ?? 0)).replace("<span", '<span id="cPnlChip"');
  $("cPnlSub").textContent = `${state.n_enters ?? 0} entry`;

  const wr = state.n_exits ? Math.round((100 * state.n_wins) / state.n_exits) : null;
  $("cTrades").textContent = state.n_exits != null ? `${state.n_exits}` : "—";
  $("cTrades").className = "value";
  $("cTradesSub").innerHTML = `${wr == null ? "—" : wr + "%"} WR • ${state.n_trials ?? 0} sidang`;

  const pos = state.position;
  if (pos) {
    $("cPos").innerHTML = `LONG <span class="pos">${fmtUSD(pos.entry_price)}</span>`;
    $("cPos").className = "value pos";
    $("cPosSub").textContent = `TP ${fmtUSD(pos.tp_price)} · SL ${fmtUSD(pos.sl_price)}`;
  } else {
    $("cPos").textContent = "FLAT";
    $("cPos").className = "value";
    $("cPosSub").textContent = "menunggu verdict ENTER";
  }

  const v = state.last_verdict || "—";
  $("cVerdict").textContent = v.toUpperCase().replace("_", " ");
  $("cVerdict").className = "value " + (v === "enter" ? "pos" : "muted");
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

/* ---------- chart harga (candlestick) ---------- */

function renderOHLC(c) {
  const el = $("ohlcReadout");
  if (!c) { el.textContent = "geser kursor di chart untuk membaca candle"; return; }
  const up = c.c >= c.o;
  const d = up ? "pos" : "neg";
  const chg = (c.c / c.o - 1) * 100;
  el.innerHTML =
    `O <b>${fmtPrice(c.o)}</b> &nbsp;H <b class="${d}">${fmtPrice(c.h)}</b> &nbsp;L <b class="${d}">${fmtPrice(c.l)}</b> ` +
    `&nbsp;C <b class="${d}">${fmtPrice(c.c)}</b> &nbsp;<span class="${d}">${chg >= 0 ? "▲" : "▼"}${Math.abs(chg).toFixed(2)}%</span> ` +
    `&nbsp;V <b>${c.v.toFixed(1)}</b> BTC &nbsp;· ${fmtWIB(new Date(c.ts).toISOString(), false)} WIB`;
}

function renderPrice(state, klines, ledger) {
  if (!priceChart) {
    priceChart = new CandleChart($("chartPrice"), { onHover: renderOHLC, maxCandles: 600 });
    window.SIDANGPrice = priceChart; // agar handler resize charts.js ikut menggambar ulang
  }

  const candles = klines
    .filter((k) => k.ts != null && k.o != null && k.h != null && k.l != null && k.c != null)
    .map((k) => ({ ts: k.ts, o: k.o, h: k.h, l: k.l, c: k.c, v: k.v ?? 0 }));
  priceChart.setData(candles);

  const hlines = [];
  const markers = [];
  if (state && state.position) {
    const p = state.position;
    hlines.push({ y: p.entry_price, color: PAL.yellow, label: "entry" });
    hlines.push({ y: p.tp_price, color: PAL.teal, label: "TP" });
    hlines.push({ y: p.sl_price, color: PAL.rose, label: "SL" });
  }
  for (const e of ledger) {
    if (e.event === "enter" && e.position) {
      const ts = (e.position.meta && e.position.meta.entry_open_ms) || Math.floor((e.ts * 1000) / IV_MS) * IV_MS;
      markers.push({ ts, kind: "entry" });
    } else if (e.event === "exit" && e.trade) {
      markers.push({ ts: Math.floor((e.ts * 1000) / IV_MS) * IV_MS, kind: "exit" });
    }
  }
  priceChart.setOverlays({ hlines, markers });

  const last = candles[candles.length - 1];
  const chip = $("priceChip");
  if (last) {
    const prev = candles.length > 1 ? candles[candles.length - 2].c : last.o;
    const chg = (last.c / prev - 1) * 100;
    const up = chg >= 0;
    chip.style.display = "";
    chip.textContent = `$${fmtPrice(last.c)} ${up ? "▲" : "▼"}${Math.abs(chg).toFixed(2)}%`;
    chip.className = "pill " + (up ? "ok" : "bad");
  } else {
    chip.style.display = "none";
  }

  $("priceNote").innerHTML =
    "candle <span class='pos'>lime = naik</span> / <span class='neg'>rose = turun</span> (pola BoardUI) • batang bawah = volume • " +
    "garis putus <span style='color:var(--yellow)'>entry</span> / <span style='color:var(--teal)'>TP</span> / <span style='color:var(--rose)'>SL</span> tampil saat posisi terbuka • " +
    "segitiga = entry/exit paper (data: klines.jsonl, telemetry)";
}

function renderEquityChart(equity) {
  const pts = equity.filter((e) => e.ts).map((e) => [e.ts, e.equity]);
  window.SIDANGCharts.drawChart($("chartEquity"), {
    series: pts.length ? [{ points: pts, color: PAL.lime, width: 1.8, fill: true }] : [],
    hlines: [{ y: 10000, color: PAL.yellow, label: "modal awal $10.000" }],
    yFmt: (v) => "$" + Math.round(v).toLocaleString("id-ID"),
    empty: "kurva ekuitas menyusul setelah run pertama",
  });
}

function renderTrialsChart(trials) {
  const tail = trials.slice(-1500);
  const be = tail.length ? tail[tail.length - 1].be_pct : 44.4;
  const pts = tail.filter((x) => x.p_tp_used != null).map((x) => [x.ts * 1, x.p_tp_used]);
  const markers = tail.filter((x) => x.verdict === "enter").map((x) => ({ x: x.ts, y: x.p_tp_used, color: PAL.lime, r: 3.5 }));
  window.SIDANGCharts.drawChart($("chartTrials"), {
    series: pts.length ? [{ points: pts, color: PAL.sky, width: 1.3 }] : [],
    hlines: [{ y: be, color: PAL.rose, label: `breakeven ${be.toFixed(1)}%` }],
    markers,
    yFmt: (v) => v.toFixed(0) + "%",
    empty: "menunggu sidang pertama",
  });
  const last = tail[tail.length - 1];
  if (last) {
    $("trialsNote").textContent =
      `sidang terakhir ${fmtWIB(last.iso)} WIB: P(TP) pesimis ${last.p_tp_used.toFixed(1)}% (mbb ${last.p_tp_mbb.toFixed(1)}% / garch ${last.p_tp_fhs != null ? last.p_tp_fhs.toFixed(1) + "%" : "—"}), EV ${last.ev_used.toFixed(2)}%, divergence ${last.div_pp.toFixed(1)}pp → ${last.verdict.toUpperCase()} • titik lime = ENTER`;
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

/* ---------- backtest: perbandingan profil (kalibrasi ambang) ---------- */

function renderBacktest(bts) {
  const found = [
    ["conservative", bts.conservative],
    ["balanced", bts.balanced],
    ["aggressive", bts.aggressive],
  ].filter(([, r]) => r);
  let reports = found;
  if (!reports.length && bts.latest) reports = [[bts.latest.params ? bts.latest.params.profile : "latest", bts.latest]];

  const tb = $("btCompareBody");
  tb.innerHTML = "";
  if (!reports.length) {
    tb.innerHTML = "<tr><td colspan='10' class='muted' style='font-family:inherit'>belum ada laporan backtest — trigger <span class='mono'>sidang-backtest</span> di tab Actions repo</td></tr>";
    $("backtestBody").innerHTML = "<span class='muted'>menunggu laporan…</span>";
    window.SIDANGCharts.drawChart($("chartBacktest"), { series: [], empty: "—" });
    return;
  }

  for (const [name, bt] of reports) {
    const s = bt.stats, d = bt.sidang;
    const tr = document.createElement("tr");
    if (name === "conservative") tr.className = "hl";
    tr.innerHTML =
      `<td><b>${name}</b></td>` +
      `<td class="muted">${bt.period.from.slice(0, 10)} .. ${bt.period.to.slice(0, 10)} (${bt.period.months} bln)</td>` +
      `<td>${d.n_trials.toLocaleString("id-ID")}</td>` +
      `<td>${d.n_enters} <span class="muted">(${d.enter_rate_pct}%)</span></td>` +
      `<td>${s.n_trades}</td>` +
      `<td>${s.win_rate_pct ?? "—"}</td>` +
      `<td>${s.profit_factor ?? "—"}</td>` +
      `<td class="${cls(s.total_return_pct)}">${fmtPct(s.total_return_pct, 2)}</td>` +
      `<td class="neg">${s.max_drawdown_pct}%</td>` +
      `<td>${s.sharpe_daily_ann}</td>`;
    tb.appendChild(tr);
  }

  const detail = bts.conservative || bts.latest || reports[0][1];
  const s = detail.stats, d = detail.sidang;
  $("backtestBody").innerHTML =
    [
      ["profil detail", `${detail.params.profile} • ${detail.period.symbol} ${detail.period.interval}`],
      ["periode", `${detail.period.from.slice(0, 10)} .. ${detail.period.to.slice(0, 10)} (${detail.period.bars.toLocaleString("id-ID")} bar)`],
      ["sidang", `${d.n_trials.toLocaleString("id-ID")} trial • ${d.n_enters} ENTER (${d.enter_rate_pct}%)`],
      ["hasil", `${fmtUSD(s.starting_cash, 0)} → <b class="${cls(s.total_return_pct)}">${fmtUSD(s.final_equity)}</b> (${fmtPct(s.total_return_pct, 2)})`],
      ["trade", `${s.n_trades} • WR ${s.win_rate_pct ?? "—"}% • PF ${s.profit_factor ?? "—"}`],
      ["risiko", `maxDD ${s.max_drawdown_pct}% • Sharpe(harian) ${s.sharpe_daily_ann} • exposure ${s.exposure_pct}%`],
      ["fee", `${fmtUSD(s.fees_paid)} terbayar • ${JSON.stringify(s.exit_reasons)}`],
    ].map(([k, v]) => `<div><span class="k">${k}</span><span>${v}</span></div>`).join("") +
    `<div><span class="k">peringatan</span><span class="muted small">${detail.honesty.disclaimer}</span></div>` +
    `<div><span class="k">unduh</span><span><a href="reports/backtest-latest.json">backtest-latest.json</a></span></div>`;
  window.SIDANGCharts.drawChart($("chartBacktest"), {
    series: [{ points: detail.equity_curve, color: PAL.sky, width: 1.5, fill: true }],
    hlines: [{ y: s.starting_cash, color: PAL.yellow, label: "modal awal" }],
    yFmt: (v) => "$" + Math.round(v).toLocaleString("id-ID"),
    empty: "—",
  });
}

document.addEventListener("DOMContentLoaded", () => {
  load();
  setInterval(load, 60000);
  $("btnRefresh").addEventListener("click", load);
});
