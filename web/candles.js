/* SIDANG candlestick — canvas vanilla, DPR-aware, tanpa library eksternal.
   Pola visual BoardUI: candle naik = lime, turun = rose; grid neutral-800;
   sumbu harga di kanan dengan tag harga terakhir; crosshair saat hover. */

const KC = {
  grid: "#262626",
  lineSoft: "#1f1f1f",
  label: "#737373",
  lime: "#a3e635",
  rose: "#fb7185",
  teal: "#2dd4bf",
  yellow: "#facc15",
  accent: "#3392ff",
  panel2: "#262626",
  text: "#fafafa",
  cross: "#525252",
};

function kSetup(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth || 600;
  const h = cv.clientHeight || 320;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function kFmtTime(ms) {
  return new Date(ms).toLocaleString("id-ID", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta",
  });
}
function kFmtPrice(v) {
  return v.toLocaleString("id-ID", { maximumFractionDigits: 1 });
}

class CandleChart {
  constructor(canvas, opts = {}) {
    this.cv = canvas;
    this.candles = [];       // [{ts,o,h,l,c,v}]
    this.hlines = [];        // [{y,color,label,dash}]
    this.markers = [];       // [{ts,kind:"entry"|"exit"}]
    this.hoverIdx = null;    // index candle yang di-hover
    this.onHover = opts.onHover || null;
    this.maxCandles = opts.maxCandles || 600;
    this.padR = 66; this.padL = 10; this.padT = 12; this.padB = 24;

    canvas.addEventListener("mousemove", (e) => this._move(e.clientX, e.clientY));
    canvas.addEventListener("mouseleave", () => this._setHover(null));
    canvas.addEventListener("touchstart", (e) => { if (e.touches[0]) this._move(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
    canvas.addEventListener("touchmove", (e) => { if (e.touches[0]) this._move(e.touches[0].clientX, e.touches[0].clientY); }, { passive: true });
    window.addEventListener("resize", () => this.draw());
  }

  setData(candles) {
    this.candles = (candles || []).slice(-this.maxCandles);
    this._setHover(this.candles.length ? this.candles.length - 1 : null, true);
    this.draw();
  }

  setOverlays({ hlines, markers } = {}) {
    if (hlines) this.hlines = hlines;
    if (markers) this.markers = markers;
    this.draw();
  }

  _move(clientX, clientY) {
    const rect = this.cv.getBoundingClientRect();
    const x = clientX - rect.left;
    const n = this.candles.length;
    if (!n) return;
    const { padL, padR } = this._pads();
    const iw = rect.width - padL - padR;
    const slot = iw / n;
    let i = Math.floor((x - padL) / slot);
    if (i < 0 || i >= n) { this._setHover(null); return; }
    this._hoverY = clientY - rect.top;
    this._setHover(i);
  }

  _setHover(i, silent) {
    this.hoverIdx = i;
    if (!silent) this.draw();
    if (this.onHover) this.onHover(i == null ? null : this.candles[i], i);
  }

  _pads() { return { padL: this.padL, padR: this.padR, padT: this.padT, padB: this.padB }; }

  draw() {
    const { ctx, w, h } = kSetup(this.cv);
    const n = this.candles.length;
    if (!n) {
      ctx.fillStyle = KC.label;
      ctx.font = "13px Inter, system-ui";
      ctx.textAlign = "center";
      ctx.fillText("chart harga menyusul setelah run berikutnya", w / 2, h / 2);
      return;
    }
    const { padL, padR, padT, padB } = this._pads();
    const iw = w - padL - padR, ih = h - padT - padB;
    const volH = Math.round(ih * 0.2);
    const priceH = ih - volH - 8;
    const slot = iw / n;

    // skala harga (termasuk overlay agar TP/SL selalu terlihat)
    let ymin = Infinity, ymax = -Infinity, vmax = 0;
    for (const c of this.candles) {
      if (c.l < ymin) ymin = c.l;
      if (c.h > ymax) ymax = c.h;
      if (c.v > vmax) vmax = c.v;
    }
    for (const l of this.hlines) {
      if (l.y < ymin) ymin = l.y;
      if (l.y > ymax) ymax = l.y;
    }
    if (ymin === ymax) { ymin -= 1; ymax += 1; }
    const ypad = (ymax - ymin) * 0.06;
    ymin -= ypad; ymax += ypad;

    const Y = (y) => padT + priceH - ((y - ymin) / (ymax - ymin)) * priceH;
    const XC = (i) => padL + (i + 0.5) * slot;

    // grid horizontal + label harga (kanan)
    ctx.font = "10.5px JetBrains Mono, ui-monospace, Menlo, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    for (let i = 0; i <= 4; i++) {
      const yv = ymin + ((ymax - ymin) * i) / 4;
      const yy = Math.round(Y(yv)) + 0.5;
      ctx.strokeStyle = KC.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, yy);
      ctx.lineTo(w - padR, yy);
      ctx.stroke();
      ctx.fillStyle = KC.label;
      ctx.fillText(kFmtPrice(yv), w - padR + 8, yy);
    }
    // label waktu (4 titik)
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let k = 0; k <= 3; k++) {
      const i = Math.min(n - 1, Math.round((k * (n - 1)) / 3));
      ctx.fillStyle = KC.label;
      ctx.fillText(kFmtTime(this.candles[i].ts), XC(i), h - padB + 6);
    }

    // highlight candle hover (papan belakang halus)
    if (this.hoverIdx != null && this.hoverIdx < n) {
      ctx.fillStyle = "rgba(255,255,255,0.035)";
      ctx.fillRect(Math.round(padL + this.hoverIdx * slot), padT, Math.ceil(slot), ih);
    }

    // volume pane
    const volTop = padT + priceH + 8;
    for (let i = 0; i < n; i++) {
      const c = this.candles[i];
      const up = c.c >= c.o;
      const bh = vmax > 0 ? (c.v / vmax) * volH : 0;
      const bw = Math.max(1, Math.floor(slot * 0.62));
      ctx.fillStyle = up ? "rgba(163,230,53,0.30)" : "rgba(251,113,133,0.30)";
      ctx.fillRect(Math.round(XC(i) - bw / 2), volTop + volH - bh, bw, bh);
    }

    // candlestick
    const bw = Math.max(2, Math.floor(slot * 0.62));
    for (let i = 0; i < n; i++) {
      const c = this.candles[i];
      const up = c.c >= c.o;
      const col = up ? KC.lime : KC.rose;
      const xc = Math.round(XC(i)) + 0.5;
      // wick
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(xc, Y(c.h));
      ctx.lineTo(xc, Y(c.l));
      ctx.stroke();
      // body
      const y1 = Y(Math.max(c.o, c.c));
      const y2 = Y(Math.min(c.o, c.c));
      const bh = Math.max(1, y2 - y1);
      ctx.fillStyle = col;
      ctx.fillRect(Math.round(XC(i) - bw / 2), y1, bw, bh);
    }

    // overlay garis (entry / TP / SL)
    ctx.font = "10px Inter, system-ui";
    ctx.textBaseline = "middle";
    for (const l of this.hlines) {
      const yy = Math.round(Y(l.y)) + 0.5;
      ctx.strokeStyle = l.color || KC.label;
      ctx.setLineDash(l.dash || [6, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, yy);
      ctx.lineTo(w - padR, yy);
      ctx.stroke();
      ctx.setLineDash([]);
      if (l.label) {
        const tw = ctx.measureText(l.label).width + 10;
        ctx.fillStyle = l.color || KC.label;
        ctx.globalAlpha = 0.14;
        ctx.fillRect(padL + 2, yy - 8, tw, 16);
        ctx.globalAlpha = 1;
        ctx.textAlign = "left";
        ctx.fillText(l.label, padL + 7, yy);
      }
    }

    // marker trade (segitiga entry lime / exit rose)
    const tsIdx = new Map(this.candles.map((c, i) => [c.ts, i]));
    for (const m of this.markers) {
      const i = tsIdx.get(m.ts);
      if (i == null) continue;
      const c = this.candles[i];
      const x = XC(i);
      ctx.fillStyle = m.kind === "entry" ? KC.lime : KC.rose;
      ctx.beginPath();
      if (m.kind === "entry") {
        const y = Y(c.l) + 12;
        ctx.moveTo(x, y - 5);
        ctx.lineTo(x - 4.5, y + 3);
        ctx.lineTo(x + 4.5, y + 3);
      } else {
        const y = Y(c.h) - 12;
        ctx.moveTo(x, y + 5);
        ctx.lineTo(x - 4.5, y - 3);
        ctx.lineTo(x + 4.5, y - 3);
      }
      ctx.closePath();
      ctx.fill();
    }

    // garis + tag harga terakhir
    const last = this.candles[n - 1];
    const lastUp = last.c >= last.o;
    const tagCol = lastUp ? KC.lime : KC.rose;
    const ly = Math.round(Y(last.c)) + 0.5;
    ctx.strokeStyle = tagCol;
    ctx.setLineDash([2, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, ly);
    ctx.lineTo(w - padR, ly);
    ctx.stroke();
    ctx.setLineDash([]);
    const tagTxt = kFmtPrice(last.c);
    ctx.font = "10.5px JetBrains Mono, ui-monospace, Menlo, monospace";
    const tw = ctx.measureText(tagTxt).width + 10;
    ctx.fillStyle = tagCol;
    ctx.fillRect(w - padR + 3, ly - 9, tw, 18);
    ctx.fillStyle = "#0a0a0a";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(tagTxt, w - padR + 8, ly);

    // crosshair hover
    if (this.hoverIdx != null && this.hoverIdx < n) {
      const i = this.hoverIdx;
      const x = Math.round(XC(i)) + 0.5;
      ctx.strokeStyle = KC.cross;
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + ih);
      ctx.stroke();
      if (this._hoverY != null && this._hoverY > padT && this._hoverY < padT + priceH) {
        const py = Math.round(this._hoverY) + 0.5;
        ctx.beginPath();
        ctx.moveTo(padL, py);
        ctx.lineTo(w - padR, py);
        ctx.stroke();
        // label harga crosshair di sumbu kanan
        const yv = ymin + (1 - (py - padT) / priceH) * (ymax - ymin);
        const cTxt = kFmtPrice(yv);
        const cw = ctx.measureText(cTxt).width + 10;
        ctx.setLineDash([]);
        ctx.fillStyle = KC.panel2;
        ctx.fillRect(w - padR + 3, py - 9, cw, 18);
        ctx.strokeStyle = KC.cross;
        ctx.strokeRect(w - padR + 3.5, py - 8.5, cw - 1, 17);
        ctx.fillStyle = KC.text;
        ctx.fillText(cTxt, w - padR + 8, py);
      }
      ctx.setLineDash([]);
      // label waktu crosshair di bawah
      const tTxt = kFmtTime(this.candles[i].ts);
      ctx.font = "10px JetBrains Mono, ui-monospace, Menlo, monospace";
      const ttw = ctx.measureText(tTxt).width + 10;
      ctx.fillStyle = KC.panel2;
      ctx.fillRect(Math.min(Math.max(x - ttw / 2, padL), w - padR - ttw), h - padB + 2, ttw, 16);
      ctx.fillStyle = KC.text;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(tTxt, Math.min(Math.max(x, padL + ttw / 2), w - padR - ttw / 2), h - padB + 4);
    }
  }
}

window.CandleChart = CandleChart;
