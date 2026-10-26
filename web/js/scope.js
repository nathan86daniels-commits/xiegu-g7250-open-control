"use strict";
/* Panadapter (ruler + spectrum + overlay + waterfall), bandwidth filter, RX DSP shape. */

const IQ_SPAN = 48000;
const PALETTES = {
  BLUE: [[0, 0, 0], [3, 19, 117], [18, 74, 235], [64, 140, 255], [219, 237, 255]],
  NIGHT: [[0, 0, 0], [18, 8, 46], [72, 34, 160], [150, 100, 255], [245, 225, 255]],
  CLASSIC: [[0, 0, 16], [0, 30, 120], [0, 170, 255], [61, 220, 132], [245, 230, 99], [242, 72, 79]],
};
const PAL_ORDER = Object.keys(PALETTES);

const scope = {
  lut: new Uint8ClampedArray(256 * 3),
  avg: null, peak: null,
  lastLo: null,
  floor: G.prefs.floor ?? -95, range: G.prefs.range,
  spotRects: [],
  hoverX: null,
};

scope.buildLut = () => {
  const stops = PALETTES[G.prefs.palette] || PALETTES.BLUE;
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1), k = Math.min(stops.length - 2, Math.floor(t)), f = t - k;
    for (let c = 0; c < 3; c++) scope.lut[i * 3 + c] = stops[k][c] + (stops[k + 1][c] - stops[k][c]) * f;
  }
};
scope.color = (u, a = 1) => {
  const i = Math.round(clamp(u, 0, 1) * 255) * 3, l = scope.lut;
  return `rgba(${l[i]},${l[i + 1]},${l[i + 2]},${a})`;
};

/** Visible window, `zoom` Hz wide. It stays where it is while you tune inside it, so the
    passband moves across the scope; it scrolls (never snaps) when tuning takes the VFO past an edge, and a
    background drag pans it anywhere on (or off) the band without touching the VFO. */
scope.view = () => {
  const lo = G.st?.lan.lo_hz || G.st?.rig.freq || 14074000, vfo = G.st?.rig.freq || lo;
  const span = G.prefs.zoom, room = Math.max(0, (IQ_SPAN - span) / 2), edge = span * 0.12;
  let c;
  if (scope.pan != null) {
    c = scope.pan;                                     // dragging, or waiting for the radio to follow the pan
    if (!scope.panning && (Math.abs(lo - scope.panLo) < 1 || performance.now() - scope.panAt > 2500)) scope.pan = null;
  } else {
    c = scope.centre ?? lo;
    if (scope.centreLo !== lo) { c = clamp(c, lo - room, lo + room); scope.centreLo = lo; }
    if (vfo !== scope.lastVfo || span !== scope.lastSpan) {   // a tune or a zoom change moves the window; a pan is left alone
      scope.lastVfo = vfo; scope.lastSpan = span;
      if (vfo < c - span / 2 + edge) c = vfo + span / 2 - edge;
      if (vfo > c + span / 2 - edge) c = vfo - span / 2 + edge;
    }
    c = clamp(c, lo - room, lo + room);
  }
  scope.centre = c;
  return { lo, span, f0: c - span / 2, f1: c + span / 2 };
};
/** Move the IQ window (the radio's LAN centre) without retuning the VFO; latest wins. */
scope.requestCentre = (hz) => {
  scope.panLo = Math.round(clamp(hz, 100000 + IQ_SPAN / 2, 54000000 - IQ_SPAN / 2));
  scope.panAt = performance.now();
  clearTimeout(scope._panT);
  scope._panT = setTimeout(() => G.api("/api/scope/centre", { hz: scope.panLo }).catch(() => {}), 40);
};
scope.binIndex = (f, lo, n) => ((f - (lo - IQ_SPAN / 2)) / IQ_SPAN) * n - 0.5;

/** One value per pixel column (max-hold over the bins the column covers). */
scope.columns = (bins, width, v) => {
  const n = bins.length, out = new Float32Array(width);
  for (let x = 0; x < width; x++) {
    const fa = v.f0 + (x / width) * v.span, fb = v.f0 + ((x + 1) / width) * v.span;
    let a = Math.floor(scope.binIndex(fa, v.lo, n)), b = Math.ceil(scope.binIndex(fb, v.lo, n));
    a = clamp(a, 0, n - 1); b = clamp(Math.max(b, a + 1), 1, n);
    let m = -200;
    for (let i = a; i < b; i++) if (bins[i] > m) m = bins[i];
    out[x] = m;
  }
  return out;
};

scope.unit = (db) => clamp((db - scope.floor) / scope.range, 0, 1);

/* ---------- auto levels: floor follows the noise ---------- */
scope.track = (bins) => {
  if (G.prefs.floor !== null) { scope.floor = G.prefs.floor; scope.range = G.prefs.range; return; }
  const s = Float32Array.from(bins).sort();
  const target = s[Math.floor(s.length * 0.45)] - 6;
  scope.floor += (target - scope.floor) * (scope.firstLevel ? 0.05 : 1);
  scope.firstLevel = true;
  scope.range = 62;
};

/* ---------- ruler with band plan ---------- */
scope.drawRuler = () => {
  const { c, w, h } = G.canvas($("ruler"));
  const v = scope.view();
  c.fillStyle = "#000"; c.fillRect(0, 0, w, h);
  const x = (f) => ((f - v.f0) / v.span) * w;
  // scale only: no band-plan strip
  // ticks
  const tick = [1000, 2000, 5000, 10000].find((t) => (t / v.span) * w >= 70) || 10000;
  c.font = `500 12px ${MONO}`; c.textAlign = "center";
  for (let f = Math.ceil(v.f0 / tick) * tick; f <= v.f1; f += tick) {
    const px = Math.round(x(f)) + 0.5;
    c.strokeStyle = "#333"; c.beginPath(); c.moveTo(px, 0); c.lineTo(px, 5); c.stroke();
    if (px > 30 && px < w - 30) { c.fillStyle = "#fff"; c.fillText((f / 1e6).toFixed(3), px, 15); }
  }
};

/* ---------- spectrum ---------- */
scope.drawSpectrum = (bins) => {
  const { c, w, h, dpr } = G.canvas($("spectrum"));
  const v = scope.view();
  c.fillStyle = "#000"; c.fillRect(0, 0, w, h);
  // dB grid
  c.font = `500 9px ${MONO}`; c.textAlign = "left";
  for (let db = Math.ceil(scope.floor / 10) * 10; db <= scope.floor + scope.range; db += 10) {
    const y = Math.round(h - scope.unit(db) * (h - 8)) + 0.5;
    c.strokeStyle = "#ffffff08"; c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke();
    c.fillStyle = "#555"; c.fillText(`${db}`, 4, y - 3);
  }
  const cols = scope.columns(bins, Math.round(w), v);
  const yOf = (d) => h - Math.pow(scope.unit(d), 1.25) * (h - 8);
  // filled body in the palette
  const grad = c.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, scope.color(0.95, 0.8)); grad.addColorStop(0.4, scope.color(0.6, 0.55)); grad.addColorStop(1, scope.color(0.2, 0.05));
  c.beginPath(); c.moveTo(0, h);
  for (let x = 0; x < cols.length; x++) c.lineTo(x, yOf(cols[x]));
  c.lineTo(w, h); c.closePath(); c.fillStyle = grad; c.fill();
  // glowing trace
  c.save(); c.shadowColor = scope.color(0.8); c.shadowBlur = 8;
  c.beginPath();
  for (let x = 0; x < cols.length; x++) (x ? c.lineTo(x, yOf(cols[x])) : c.moveTo(0, yOf(cols[0])));
  c.strokeStyle = scope.color(0.97); c.lineWidth = 1.1; c.stroke(); c.restore();
  // peak hold
  if (G.prefs.avg === "PEAK" && scope.peak) {
    const pc = scope.columns(scope.peak, Math.round(w), v);
    c.beginPath();
    for (let x = 0; x < pc.length; x++) (x ? c.lineTo(x, yOf(pc[x])) : c.moveTo(0, yOf(pc[0])));
    c.strokeStyle = "#b49bff88"; c.lineWidth = 1; c.stroke();
  }
};

/* ---------- overlay: passband, VFO marker, spots, cursor ---------- */
scope.drawOverlay = () => {
  const { c, w, h } = G.canvas($("ovl"));
  c.clearRect(0, 0, w, h);
  if (!G.st) return;
  const v = scope.view(), x = (f) => ((f - v.f0) / v.span) * w;
  const vfo = G.st.rig.freq, [plo, phi] = G.st.rig.passband;
  // DX spot labels, stacked to avoid overlap
  scope.spotRects = [];
  if (G.prefs.spots && G.spots) {
    const rows = [[], [], []];
    c.font = `600 11px ${MONO}`;
    for (const s of G.spots) {
      if (s.freq_hz < v.f0 || s.freq_hz > v.f1) continue;
      const sx = x(s.freq_hz), tw = c.measureText(s.dx).width + 10;
      const row = rows.findIndex((r) => r.every(([l, rr]) => sx - tw / 2 > rr + 4 || sx + tw / 2 < l - 4));
      if (row < 0) continue;
      rows[row].push([sx - tw / 2, sx + tw / 2]);
      const y = 6 + row * 21;
      c.strokeStyle = "#8b5cf666"; c.beginPath(); c.moveTo(Math.round(sx) + 0.5, y + 17); c.lineTo(Math.round(sx) + 0.5, h); c.stroke();
      c.fillStyle = "#120c22ee"; c.strokeStyle = "#9d86e0"; c.beginPath(); c.roundRect(sx - tw / 2, y, tw, 17, 3); c.fill(); c.stroke();
      c.fillStyle = "#fff"; c.textAlign = "center"; c.fillText(s.dx, sx, y + 12.5);
      scope.spotRects.push({ x0: sx - tw / 2, x1: sx + tw / 2, y0: y, y1: y + 17, spot: s });
    }
  }
  // no line under the mouse: the crosshair pointer alone marks the spot (the readout gives the frequency)
};

/* ---------- waterfall ---------- */
scope.wfRow = (bins) => {
  const el = $("waterfall"), dpr = window.devicePixelRatio || 1;
  const W = Math.round(el.clientWidth * dpr), H = Math.round(el.clientHeight * dpr);
  if (!W || !H) return;
  const ctx = el.getContext("2d");
  if (el.width !== W || el.height !== H) {
    const old = el.width ? (() => { const t = document.createElement("canvas"); t.width = el.width; t.height = el.height; t.getContext("2d").drawImage(el, 0, 0); return t; })() : null;
    el.width = W; el.height = H; ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    if (old) ctx.drawImage(old, 0, 0, W, H);
  }
  const v = scope.view();
  if (scope.lastLo !== null && scope.lastLo !== v.lo) {          // keep history on frequency after a retune
    const px = Math.round(((v.lo - scope.lastLo) / v.span) * W);
    if (Math.abs(px) >= W) { ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H); }
    else { ctx.drawImage(el, -px, 0); ctx.fillStyle = "#000"; px > 0 ? ctx.fillRect(W - px, 0, px, H) : ctx.fillRect(0, 0, -px, H); }
  }
  scope.lastLo = v.lo;
  ctx.drawImage(el, 0, 0, W, H - 1, 0, 1, W, H - 1);
  const cols = scope.columns(bins, W, v), img = ctx.createImageData(W, 1), d = img.data;
  for (let x = 0; x < W; x++) {
    const i = Math.round(Math.pow(scope.unit(cols[x]), 1.3) * 255) * 3;
    d[x * 4] = scope.lut[i]; d[x * 4 + 1] = scope.lut[i + 1]; d[x * 4 + 2] = scope.lut[i + 2]; d[x * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
};

/* ---------- RX DSP filter (left panel): the filter display, in purple ----------
   Centred on the carrier, ±extent either side; the passband is a flared trapezoid (each shoulder about a fifth of
   the width, as Yaesu draws it) at SHIFT ± WIDTH/2, with the roofing line under it. */
scope.filterShape = () => {
  const r = G.st.rig, m = r.mode || "USB", width = Math.max(50, (r.high || 0) - (r.low || 0));
  if (m === "AM" || m === "FM") return { width: r.high * 2, shift: 0, centred: true };
  if (m === "CW" || m === "CW-R") return { width, shift: 0, centred: true };
  return { width, shift: (r.low + r.high) / 2 - DSP.home(width), centred: false };
};
scope.drawDsp = () => {
  const { c, w, h } = G.canvas($("dspc"));
  c.clearRect(0, 0, w, h);
  c.fillStyle = "#000"; c.beginPath(); c.roundRect(0.5, 0.5, w - 1, h - 1, 3); c.fill(); c.strokeStyle = "#303030"; c.stroke();
  if (!G.st) return;
  const f = scope.filterShape(), lsb = /^LSB|RTTY-R/.test(G.st.rig.mode || "");
  const extent = Math.max(2500, Math.min(7500, Math.max(f.width * 0.8, 2200) + Math.abs(f.shift)));
  const top = 10, base = h - 14, x = (hz) => 8 + ((hz + extent) / (extent * 2)) * (w - 16);
  c.strokeStyle = "#ffffff1c"; c.lineWidth = 0.9; c.beginPath(); c.moveTo(8, base + 0.5); c.lineTo(w - 8, base + 0.5); c.stroke();
  for (let i = -4; i <= 4; i++) {
    const tx = Math.round(x((i * extent) / 4)) + 0.5, len = i === 0 ? 5 : i % 2 === 0 ? 3 : 2;
    c.strokeStyle = i === 0 ? "#fff" : "#ffffff30"; c.beginPath(); c.moveTo(tx, base); c.lineTo(tx, base + len); c.stroke();
  }
  const shift = lsb ? -f.shift : f.shift, start = shift - f.width / 2, finish = shift + f.width / 2, sh = f.width * 0.22;
  const y = (hz) => base - (base - top - 5) * (hz < start + sh ? clamp((hz - start) / sh, 0, 1) : hz > finish - sh ? clamp((finish - hz) / sh, 0, 1) : 1);
  c.beginPath(); c.moveTo(x(start), base);
  for (let i = 0; i <= 56; i++) { const hz = start + (f.width * i) / 56; c.lineTo(x(hz), Math.min(base, y(hz))); }
  const g = c.createLinearGradient(0, top, 0, base); g.addColorStop(0, "#8b5cf633"); g.addColorStop(1, "#8b5cf600");
  c.fillStyle = g; c.fill();
  c.strokeStyle = "#a07cf8"; c.lineWidth = 1.8; c.stroke();
  c.strokeStyle = "#8b5cf6cc"; c.lineWidth = 1.1; c.beginPath(); c.moveTo(x(start) + 2, base + 4.5); c.lineTo(x(finish) - 2, base + 4.5); c.stroke();
  if (G.st.rig.anf) {
    c.fillStyle = "#dedede"; c.font = `500 10px ${MONO}`; c.textAlign = "right"; c.fillText("NF", w - 8, 12);
  }
};

/* ---------- panadapter mouse ---------- */
(() => {
  const body = $("panBody");
  const hzAt = (clientX) => { const r = body.getBoundingClientRect(), v = scope.view(); return v.f0 + ((clientX - r.left) / r.width) * v.span; };
  body.addEventListener("mousemove", (e) => {
    const r = body.getBoundingClientRect();
    scope.hoverX = e.clientX - r.left;
    scope.dirty = true;
    const hz = hzAt(e.clientX);
    const vfo = G.st?.rig.freq || hz;
    $("cursor").textContent = `${(hz / 1e6).toFixed(4)} MHz  (${hz - vfo >= 0 ? "+" : ""}${((hz - vfo) / 1000).toFixed(2)} k)`;
  });
  body.addEventListener("mouseleave", () => { scope.hoverX = null; scope.dirty = true; $("cursor").textContent = ""; });
  // drag the passband to move the VFO (pushing into an edge scrolls the band along);
  // drag anywhere else to pan the scope across the band; a plain click tunes to the spot.
  let drag = null;
  const inPassband = (hz) => {
    const [plo, phi] = G.st.rig.passband, vfo = G.st.rig.freq, pad = scope.view().span * 0.006;
    return hz >= vfo + Math.min(plo, phi) - pad && hz <= vfo + Math.max(plo, phi) + pad;
  };
  // Pushing the passband into an edge: the VFO and the scope move together, so the passband stays pinned just
  // inside the edge (12%, where a tune would leave it) and the band slides underneath it. The
  // window is moved here rather than waiting for the radio's LAN centre to follow the tune, which lagged and let the
  // passband wander towards and past the edge. 12% keeps the VFO inside the server's own follow margin, so the two
  // never pull the window different ways.
  const edgePush = () => {
    if (!drag || !drag.moved || drag.kind !== "passband") return;
    const r = body.getBoundingClientRect(), zone = r.width * 0.08, x = drag.lastX - r.left;
    const s = x < zone ? -(1 - x / zone) : x > r.width - zone ? (x - (r.width - zone)) / zone : 0;
    if (s) {
      const v = scope.view(), step = Math.min(G.prefs.step, 100), pin = v.span / 2 - v.span * 0.12;
      if (!drag.pushing) { drag.pushing = true; drag.hz = G.st.rig.freq; }
      drag.hz += Math.sign(s) * Math.min(1, Math.abs(s)) ** 2 * v.span * 0.012;
      G.tune(Math.round(drag.hz / step) * step);
      // the window follows the frequency the radio reports, which is where the passband is drawn
      const shown = G.st.rig.freq;
      scope.panning = true;
      scope.pan = s < 0 ? shown + pin : shown - pin;
      scope.requestCentre(scope.pan);
      scope.dirty = true;
    }
    drag.raf = requestAnimationFrame(edgePush);
  };
  body.addEventListener("pointerdown", (e) => {
    if (!G.st || e.button !== 0) return;
    const hz = hzAt(e.clientX), v = scope.view();
    drag = { x: e.clientX, lastX: e.clientX, id: e.pointerId, moved: false, kind: inPassband(hz) ? "passband" : "pan",
      offset: hz - G.st.rig.freq, hz: G.st.rig.freq, centre: (v.f0 + v.f1) / 2, span: v.span, width: body.getBoundingClientRect().width };
  });
  body.addEventListener("pointermove", (e) => {
    if (!drag || drag.id !== e.pointerId) return;
    drag.lastX = e.clientX;
    if (!drag.moved && Math.abs(e.clientX - drag.x) < 4) return;
    if (!drag.moved) {
      drag.moved = true; body.setPointerCapture(e.pointerId);
      if (drag.kind === "passband") drag.raf = requestAnimationFrame(edgePush);
    }
    if (drag.kind === "passband") {
      const r = body.getBoundingClientRect(), zone = r.width * 0.08, x = e.clientX - r.left;
      if (x > zone && x < r.width - zone) {
        const step = Math.min(G.prefs.step, 100);
        drag.hz = hzAt(e.clientX) - drag.offset;
        G.tune(Math.round(drag.hz / step) * step);
      }
    } else {
      // grab the band and slide it: the frequency under the mouse stays under the mouse
      scope.panning = true;
      scope.pan = drag.centre - ((e.clientX - drag.x) / drag.width) * drag.span;
      scope.requestCentre(scope.pan);
      scope.dirty = true;
    }
  });
  const endDrag = (e) => {
    if (!drag || drag.id !== e.pointerId) return;
    cancelAnimationFrame(drag.raf);
    if (drag.moved) { scope.justDragged = true; setTimeout(() => (scope.justDragged = false), 0); }
    if (drag.kind === "pan" || drag.pushing) { scope.panning = false; scope.panAt = performance.now(); }
    drag = null;
  };
  body.addEventListener("pointerup", endDrag);
  body.addEventListener("pointercancel", endDrag);
  body.addEventListener("click", (e) => {
    if (!G.st || scope.justDragged) return;
    const r = $("ovl").getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    const hit = scope.spotRects.find((s) => x >= s.x0 && x <= s.x1 && y >= s.y0 && y <= s.y1);
    if (hit) { G.tuneSpot(hit.spot); return; }
    const step = G.prefs.step;
    G.tune(Math.round(hzAt(e.clientX) / step) * step);
  });
  body.addEventListener("wheel", (e) => {
    if (!G.st) return;
    e.preventDefault();
    const step = G.prefs.step, snapped = Math.round(G.st.rig.freq / step) * step;
    G.tune(snapped + (e.deltaY < 0 ? step : -step));
  }, { passive: false });
})();

/* ---------- header controls ---------- */
G.seg($("zoomSg"), [[12000, "12k"], [24000, "24k"], [48000, "48k"]], { get: () => G.prefs.zoom, pick: (v) => { G.setPref("zoom", v); scope.lastLo = null; } });
G.seg($("avgSg"), [["AVG", "LIVE"], ["PEAK", "PEAK"]], { get: () => G.prefs.avg, pick: (v) => G.setPref("avg", v) });
const showLvl = () => { $("lvlTag").textContent = `LEVEL ${SG.level > 0 ? "+" : ""}${SG.level}`; };
const stepLvl = (d) => { SG.level = clamp(SG.level + d, -30, 30); G.store.set("scopeLevel", SG.level); showLvl(); };
$("lvlDown").onclick = () => stepLvl(-3); $("lvlUp").onclick = () => stepLvl(3);
$("lvlTag").addEventListener("wheel", (e) => { e.preventDefault(); stepLvl(e.deltaY < 0 ? 1 : -1); }, { passive: false });
showLvl();
$("glassS").value = SG.glass; $("glassV").textContent = `${SG.glass}%`;
$("glassS").addEventListener("input", (e) => { SG.glass = Number(e.target.value); $("glassV").textContent = `${SG.glass}%`; G.store.set("scopeGlass", SG.glass); });
$("spotsBtn").onclick = () => G.setPref("spots", !G.prefs.spots);
G.on("prefs", () => $("spotsBtn").classList.toggle("on", G.prefs.spots));
$("spotsBtn").classList.toggle("on", G.prefs.spots);
$("recBtn").onclick = () => {
  if (G.st?.lan.recording) G.api("/api/record/stop", {}).catch((e) => G.toast(e.message, true));
  else G.api("/api/record/start", { seconds: 15 }).then(() => G.toast("Recording 15 s of raw IQ")).catch((e) => G.toast(e.message, true));
};
G.on("status", (s) => {
  $("recBtn").classList.toggle("on", s.lan.recording);
  $("recBtn").textContent = s.lan.recording ? "■ STOP" : "● REC IQ";
  $("recDl").hidden = !s.lan.last_recording || s.lan.recording;
  $("noIq").hidden = s.lan.receiving;
  const band = G.bandOf(s.rig.freq);
  $("panInfo").textContent = `${(s.rig.freq / 1e6).toFixed(6)} MHz${band ? " · " + band.b + " m" : ""}`;
});

/* ---------- frame pipeline: the layered renderer (scopegl.js) ---------- */
scope.gl = new ScopeGL($("scopeGl"));
if (scope.gl.failed) G.toast(`Scope: ${scope.gl.failed}`, true);
G.on("spectrum", (bins) => {
  scope.track(bins);
  if (G.st) scope.gl.push(bins, G.st.lan.lo_hz || G.st.rig.freq, IQ_SPAN, performance.now());
  scope.latest = bins;
  scope.dirty = true;
});
G.on("tuned", () => { scope.dirty = true; });
scope.lastFrame = performance.now();
scope.frame = () => {
  const now = performance.now(), dt = Math.min(0.1, (now - scope.lastFrame) / 1000);
  scope.lastFrame = now;
  if (G.st && scope.latest) {
    const v = scope.view(), r = G.st.rig;
    scope.gl.render({ viewLeft: v.f0, span: v.span, carrier: r.freq + CAL.carrierHz, rxLo: r.freq + CAL.carrierHz + r.passband[0], rxHi: r.freq + CAL.carrierHz + r.passband[1], peak: G.prefs.avg === "PEAK" }, dt);
  }
  if (scope.dirty && scope.latest) {
    scope.dirty = false;
    scope.drawRuler(); scope.drawOverlay(); scope.drawDsp();
  }
};
scope.buildLut();
