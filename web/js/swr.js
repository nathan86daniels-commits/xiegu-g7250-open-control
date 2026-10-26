"use strict";
/* SWR tab: the band sweep. The server keys a low-power
   CW carrier step by step (swr_sweep.py); nothing transmits until the operator has confirmed a message that says so.
   Readings arrive raw and are converted here with the DevTools calibration (M.swrV). */

const SWEEP_BANDS = [["160 m", 1810000, 2000000], ["80 m", 3500000, 3800000], ["60 m", 5258500, 5406500], ["40 m", 7000000, 7200000],
  ["30 m", 10100000, 10150000], ["20 m", 14000000, 14350000], ["17 m", 18068000, 18168000], ["15 m", 21000000, 21450000],
  ["12 m", 24890000, 24990000], ["10 m", 28000000, 29700000], ["6 m", 50000000, 52000000]];
// Fine steps are what make a narrow spike visible; the bridge needs real forward power, 5 W often reads nothing.
const SWEEP_STEPS = [[1000, "1 kHz"], [2000, "2 kHz"], [5000, "5 kHz"], [10000, "10 kHz"], [25000, "25 kHz"], [50000, "50 kHz"]];
const SWEEP_POWER = [5, 10, 20, 30];
const SW = { status: null, poll: null };

SW.build = () => {
  const pane = document.querySelector('[data-pane="swr"]');
  pane.innerHTML = `<div class="swrSide">
      <label><span class="lbl">Band</span><select class="inp" id="swBand">${SWEEP_BANDS.map(([n]) => `<option>${n}</option>`).join("")}<option>Custom</option></select></label>
      <div class="row"><label><span class="lbl">Start MHz</span><input class="inp" id="swStart"></label><label><span class="lbl">Stop MHz</span><input class="inp" id="swStop"></label></div>
      <div class="row"><label><span class="lbl">Step</span><select class="inp" id="swStep">${SWEEP_STEPS.map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select></label>
        <label><span class="lbl">Power</span><select class="inp" id="swPower">${SWEEP_POWER.map((w) => `<option value="${w}">${w} W</option>`).join("")}</select></label></div>
      <div class="row"><button class="btn" id="swGo">START SWEEP</button><button class="btn" id="swStopBtn">STOP</button></div>
      <div class="dim small" id="swRange"></div>
      <div class="swWarn" id="swWarn"></div>
      <div class="small" id="swMsg"></div>
    </div>
    <canvas id="swPlot"></canvas>`;
  const saved = G.store.get("sweep", { band: "20 m", step: 5000, watts: 10 });
  $("swBand").value = saved.band; $("swStep").value = saved.step; $("swPower").value = saved.watts;
  SW.fillBand();
  $("swBand").onchange = () => { SW.fillBand(); SW.save(); };
  ["swStart", "swStop"].forEach((id) => $(id).addEventListener("change", () => {
    const b = SWEEP_BANDS.find(([n]) => n === $("swBand").value);
    if (b && (SW.hz("swStart") !== b[1] || SW.hz("swStop") !== b[2])) $("swBand").value = "Custom";
    SW.describe(); SW.save();
  }));
  ["swStep", "swPower"].forEach((id) => $(id).addEventListener("change", () => { SW.describe(); SW.save(); }));
  $("swGo").onclick = SW.start;
  $("swStopBtn").onclick = () => G.api("/api/swr/stop", {}).then((s) => { SW.status = s; SW.render(); }).catch((e) => G.toast(e.message, true));
};
SW.hz = (id) => Math.round(parseFloat(($(id).value || "").replace(",", ".")) * 1e6);
SW.save = () => G.store.set("sweep", { band: $("swBand").value, step: Number($("swStep").value), watts: Number($("swPower").value), start: $("swStart").value, stop: $("swStop").value });
SW.fillBand = () => {
  const b = SWEEP_BANDS.find(([n]) => n === $("swBand").value);
  const saved = G.store.get("sweep", {});
  const [lo, hi] = b ? [b[1], b[2]] : [SW.hz("swStart") || Number(saved.start) * 1e6 || 14000000, SW.hz("swStop") || Number(saved.stop) * 1e6 || 14350000];
  $("swStart").value = (lo / 1e6).toFixed(3); $("swStop").value = (hi / 1e6).toFixed(3);
  SW.describe();
};
SW.describe = () => {
  const lo = SW.hz("swStart"), hi = SW.hz("swStop"), step = Number($("swStep").value);
  if (!(hi > lo)) { $("swRange").textContent = "The stop frequency has to be above the start."; return; }
  const points = Math.floor((hi - lo) / step) + 1, ms = 120 + 5 * 90;
  const breaks = Math.max(0, Math.floor((points - 1) / Math.floor(25000 / ms)));
  $("swRange").textContent = `${(lo / 1e6).toFixed(3)}–${(hi / 1e6).toFixed(3)} MHz · ${points} points · about ${Math.max(1, Math.round((points * ms + breaks * 500) / 1000))} s`;
  const inBand = SWEEP_BANDS.some(([, a, b]) => lo >= a && hi <= b);
  $("swWarn").textContent = inBand ? "" : "This range goes outside the amateur bands. Transmitting there is not permitted under most licences, and the radio will normally refuse to key: those steps produce no power and are skipped. Band plans differ by region, so check yours.";
};
SW.start = async () => {
  const lo = SW.hz("swStart"), hi = SW.hz("swStop"), step = Number($("swStep").value), watts = Number($("swPower").value);
  if (!(hi > lo)) return G.toast("The stop frequency has to be above the start.", true);
  if (!G.catOn()) return G.toast("Connect the radio with USB CAT first.", true);
  const band = $("swBand").value;
  const ok = confirm(`This will TRANSMIT a low-power carrier across ${band} (${(lo / 1e6).toFixed(3)}–${(hi / 1e6).toFixed(3)} MHz).\n\n`
    + ($("swWarn").textContent ? $("swWarn").textContent + "\n\n" : "")
    + `Make sure a suitable antenna or dummy load is connected.\n\nThe radio will switch to CW at ${watts} W with the ATU bypassed, and be restored afterwards.\n\nStart the sweep?`);
  if (!ok) return;
  try { SW.status = await G.api("/api/swr/sweep", { confirm: true, band, start_hz: lo, stop_hz: hi, step_hz: step, watts }); SW.render(); SW.watch(); }
  catch (e) { G.toast(e.message, true); }
};
SW.watch = () => {
  clearInterval(SW.poll);
  SW.poll = setInterval(async () => {
    try { SW.status = await G.api("/api/swr/sweep"); } catch { return; }
    SW.render();
    if (!SW.status.running) clearInterval(SW.poll);
  }, 300);
};
SW.render = () => {
  const s = SW.status;
  if (s) {
    $("swMsg").textContent = s.message || "";
    $("swGo").disabled = !!s.running; $("swStopBtn").disabled = !s.running;
    $("swGo").classList.toggle("on", !!s.running);
  }
  SW.draw();
};

/* the plot (SwrPlot): measured points joined by a shape-preserving cubic, a marker at every sample, the 2:1 line,
   and the best match marked under the axis */
SW.slopes = (p) => {
  const n = p.length; if (n < 2) return new Array(n).fill(0);
  const d = []; for (let i = 0; i < n - 1; i++) d.push((p[i + 1].swr - p[i].swr) / (p[i + 1].hz - p[i].hz));
  const m = new Array(n); m[0] = d[0]; m[n - 1] = d[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (d[i - 1] * d[i] <= 0) { m[i] = 0; continue; }
    const h0 = p[i].hz - p[i - 1].hz, h1 = p[i + 1].hz - p[i].hz, w1 = 2 * h1 + h0, w2 = h1 + 2 * h0;
    m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
  }
  const limit = (s, sec) => (s * sec <= 0 ? 0 : Math.abs(s) > 3 * Math.abs(sec) ? 3 * sec : s);
  m[0] = limit(m[0], d[0]); m[n - 1] = limit(m[n - 1], d[n - 2]);
  return m;
};
SW.interp = (p, m, i, hz) => {
  const a = p[i], b = p[i + 1], h = b.hz - a.hz, t = clamp((hz - a.hz) / h, 0, 1), t2 = t * t, t3 = t2 * t;
  return (2 * t3 - 3 * t2 + 1) * a.swr + (t3 - 2 * t2 + t) * h * m[i] + (-2 * t3 + 3 * t2) * b.swr + (t3 - t2) * h * m[i + 1];
};
SW.draw = () => {
  const el = $("swPlot"); if (!el) return;
  const { c, w, h } = G.canvas(el);
  if (!w || !h) return;
  c.fillStyle = "#000"; c.fillRect(0, 0, w, h);
  const pts = (SW.status?.points || []).map((p) => ({ ...p, swr: M.swrV(p.raw) })).sort((a, b) => a.hz - b.hz);
  const left = 46, right = w - 14, top = 14, bottom = h - 26;
  const maxSwr = Math.max(3, Math.ceil(Math.max(3, ...pts.map((p) => p.swr)) * 2) / 2);
  const Y = (s) => bottom - ((clamp(s, 1, maxSwr) - 1) / (maxSwr - 1)) * (bottom - top);
  c.font = `500 12px ${MONO}`; c.textBaseline = "middle"; c.textAlign = "right";
  for (let s = 1; s <= maxSwr + 0.001; s += 0.5) {
    const y = Math.round(Y(s)) + 0.5, two = Math.abs(s - 2) < 0.001;
    c.strokeStyle = two ? "#b49bff" : "#262626"; c.lineWidth = two ? 1.4 : 1;
    c.beginPath(); c.moveTo(left, y); c.lineTo(right, y); c.stroke();
    c.fillStyle = "#a8a8a8"; c.fillText(s.toFixed(1), left - 6, y);
  }
  c.strokeStyle = "#3a3a3a"; c.lineWidth = 1; c.beginPath(); c.moveTo(left + 0.5, top); c.lineTo(left + 0.5, bottom); c.lineTo(right, bottom + 0.5); c.stroke();
  const lo = SW.status?.start_hz || SW.hz("swStart"), hi = SW.status?.stop_hz || SW.hz("swStop");
  const X = (hz) => left + ((hz - lo) / Math.max(1, hi - lo)) * (right - left);
  // frequency scale: a "nice" tick step giving about one label per 90 px
  const span = hi - lo, want = span / Math.max(2, (right - left) / 90);
  const tick = [1e3, 2e3, 5e3, 1e4, 2e4, 25e3, 5e4, 1e5, 2e5, 25e4, 5e5, 1e6].find((t) => t >= want) || 1e6;
  c.textAlign = "center"; c.textBaseline = "top"; c.fillStyle = "#fff";
  for (let f = Math.ceil(lo / tick) * tick; f <= hi; f += tick) {
    const x = Math.round(X(f)) + 0.5;
    c.strokeStyle = "#3a3a3a"; c.beginPath(); c.moveTo(x, bottom); c.lineTo(x, bottom + 4); c.stroke();
    c.fillText((f / 1e6).toFixed(3), clamp(x, left + 20, right - 20), bottom + 6);
  }
  if (!pts.length) { c.textAlign = "left"; c.fillStyle = "#7c7c7c"; c.fillText(SW.status?.running ? "Sweeping…" : "No sweep yet", left + 10, top + 6); return; }
  if (pts.length > 1) {
    const m = SW.slopes(pts), path = new Path2D();
    path.moveTo(X(pts[0].hz), Y(pts[0].swr));
    for (let i = 0; i < pts.length - 1; i++) {
      const sub = clamp(Math.ceil(Math.abs(X(pts[i + 1].hz) - X(pts[i].hz)) / 4), 2, 64);
      for (let k = 1; k <= sub; k++) { const hz = pts[i].hz + ((pts[i + 1].hz - pts[i].hz) * k) / sub; path.lineTo(X(hz), Y(SW.interp(pts, m, i, hz))); }
    }
    const area = new Path2D(path); area.lineTo(X(pts[pts.length - 1].hz), bottom); area.lineTo(X(pts[0].hz), bottom); area.closePath();
    const g = c.createLinearGradient(0, top, 0, bottom); g.addColorStop(0, "#8b5cf648"); g.addColorStop(1, "#8b5cf605");
    c.fillStyle = g; c.fill(area);
    c.strokeStyle = "#8b5cf6"; c.lineWidth = 2.2; c.stroke(path);
  }
  for (const p of pts) { c.fillStyle = "#b49bff"; c.strokeStyle = "#3a3a3a"; c.beginPath(); c.arc(X(p.hz), Y(p.swr), 3.5, 0, 7); c.fill(); c.stroke(); }
  // the reading at each sample, skipping any that would sit on the previous one
  c.font = `500 11px ${MONO}`; c.textAlign = "left"; c.textBaseline = "top";
  let lastRight = -Infinity;
  for (const p of pts) {
    const t = p.swr.toFixed(2), tw = c.measureText(t).width, px = X(p.hz), py = Y(p.swr);
    const lx = clamp(px - tw / 2, left + 1, right - tw - 1); if (lx < lastRight + 4) continue;
    let ly = py - 14 - 7; if (ly < top + 1) ly = py + 7;
    c.fillStyle = "#000c"; c.fillRect(lx - 3, ly - 1, tw + 6, 15); c.fillStyle = "#f0f0f0"; c.fillText(t, lx, ly);
    lastRight = lx + tw + 3;
  }
  const best = pts.reduce((a, b) => (b.swr < a.swr ? b : a)), bx = X(best.hz);
  c.fillStyle = "#fff"; c.beginPath(); c.moveTo(bx, bottom - 1); c.lineTo(bx - 4, bottom + 5); c.lineTo(bx + 4, bottom + 5); c.fill();
  // best match and the measured 2:1 range around it
  const ord = pts, bi = ord.indexOf(best); let a = bi, b = bi;
  while (a > 0 && ord[a - 1].swr <= 2) a--; while (b < ord.length - 1 && ord[b + 1].swr <= 2) b++;
  const range = best.swr <= 2 ? ` · under 2:1 from ${(ord[a].hz / 1e6).toFixed(3)} to ${(ord[b].hz / 1e6).toFixed(3)} MHz` : " · no measured range under 2:1";
  const cap = `${best.swr.toFixed(2)}:1 @ ${(best.hz / 1e6).toFixed(3)} MHz${range}`;
  c.font = `500 12.5px ${MONO}`; c.textAlign = "left"; c.textBaseline = "top";
  const cw = c.measureText(cap).width;
  c.fillStyle = "#000e"; c.strokeStyle = "#3a3a3a"; c.fillRect(right - cw - 14, top + 2, cw + 10, 20); c.strokeRect(right - cw - 14 + 0.5, top + 2.5, cw + 10, 20);
  c.fillStyle = "#fff"; c.fillText(cap, right - cw - 9, top + 6);
};

SW.build();
G.api("/api/swr/sweep").then((s) => { SW.status = s; SW.render(); if (s.running) SW.watch(); }).catch(() => {});
window.addEventListener("resize", SW.draw);
