/* SIDANG charts — canvas vanilla, DPR-aware, tanpa library eksternal. */

function setupCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth || cv.parentElement.clientWidth || 600;
  const h = cv.clientHeight || 240;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function fmtNum(v) {
  if (Math.abs(v) >= 10000) return v.toLocaleString("id-ID", { maximumFractionDigits: 0 });
  if (Math.abs(v) >= 100) return v.toLocaleString("id-ID", { maximumFractionDigits: 1 });
  return v.toLocaleString("id-ID", { maximumFractionDigits: 2 });
}

function fmtTime(ms, span) {
  const d = new Date(ms);
  if (span > 6 * 864e5) return d.toLocaleDateString("id-ID", { day: "numeric", month: "short", timeZone: "Asia/Jakarta" });
  return d.toLocaleString("id-ID", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta" });
}

/**
 * drawChart(cv, opts)
 * opts.series : [{ points: [[x(ms), y]], color, width, dash }]
 * opts.hlines : [{ y, color, label, dash }]
 * opts.markers: [{ x, y, color }]
 * opts.yFmt   : fungsi format label-y (default fmtNum)
 */
function drawChart(cv, opts) {
  const { ctx, w, h } = setupCanvas(cv);
  const series = (opts.series || []).filter((s) => s.points && s.points.length > 0);
  const hlines = opts.hlines || [];
  if (series.length === 0 && hlines.length === 0) {
    ctx.fillStyle = "#8b95a7";
    ctx.font = "13px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(opts.empty || "belum ada data", w / 2, h / 2);
    return;
  }
  const padL = 56, padR = 14, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;

  let xs = [], ys = [];
  series.forEach((s) => s.points.forEach(([x, y]) => { xs.push(x); ys.push(y); }));
  hlines.forEach((l) => ys.push(l.y));
  if (xs.length === 1) { xs = [xs[0] - 1, xs[0] + 1]; }
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  let ymin = Math.min(...ys), ymax = Math.max(...ys);
  if (ymin === ymax) { ymin -= 1; ymax += 1; }
  const ypad = (ymax - ymin) * 0.08;
  ymin -= ypad; ymax += ypad;
  const span = xmax - xmin;
  const X = (x) => padL + ((x - xmin) / span) * iw;
  const Y = (y) => padT + ih - ((y - ymin) / (ymax - ymin)) * ih;
  const yFmt = opts.yFmt || fmtNum;

  // grid + label y
  ctx.font = "10.5px ui-monospace, Menlo, monospace";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const yv = ymin + ((ymax - ymin) * i) / 4;
    const yy = Y(yv);
    ctx.strokeStyle = "rgba(35,42,56,0.85)";
    ctx.beginPath();
    ctx.moveTo(padL, yy);
    ctx.lineTo(w - padR, yy);
    ctx.stroke();
    ctx.fillStyle = "#8b95a7";
    ctx.fillText(yFmt(yv), padL - 6, yy);
  }
  // label x (3 titik)
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let i = 0; i <= 2; i++) {
    const xv = xmin + (span * i) / 2;
    ctx.fillStyle = "#8b95a7";
    ctx.fillText(fmtTime(xv, span), X(xv), h - padB + 6);
  }

  // garis horizontal referensi
  hlines.forEach((l) => {
    ctx.strokeStyle = l.color || "#8b95a7";
    ctx.setLineDash(l.dash || [5, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, Y(l.y));
    ctx.lineTo(w - padR, Y(l.y));
    ctx.stroke();
    ctx.setLineDash([]);
    if (l.label) {
      ctx.fillStyle = l.color || "#8b95a7";
      ctx.font = "10px system-ui";
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      ctx.fillText(l.label, padL + 4, Y(l.y) + 2);
    }
  });

  // seri
  series.forEach((s) => {
    ctx.strokeStyle = s.color || "#60a5fa";
    ctx.lineWidth = s.width || 1.6;
    ctx.setLineDash(s.dash || []);
    ctx.beginPath();
    s.points.forEach(([x, y], i) => {
      const px = X(x), py = Y(y);
      if (i === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // marker (mis. titik ENTER)
  (opts.markers || []).forEach((m) => {
    ctx.fillStyle = m.color || "#4ade80";
    ctx.beginPath();
    ctx.arc(X(m.x), Y(m.y), m.r || 3, 0, Math.PI * 2);
    ctx.fill();
  });
}

window.SIDANGCharts = { drawChart };
