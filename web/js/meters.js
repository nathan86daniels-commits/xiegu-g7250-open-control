"use strict";
/* Bar meters (hero), analogue S-meter dial, TX stage columns. Raw values are Icom-scale
   CI-V meter readings (0-255); the conversions match g7250/cat.py and need checking on a
   real G7250. Without CAT the S reading is estimated from the IQ and marked as such. */

const interp = (raw, table) => {
  for (let i = 1; i < table.length; i++) if (raw <= table[i][0]) {
    const [r0, v0] = table[i - 1], [r1, v1] = table[i];
    return v0 + ((v1 - v0) * (raw - r0)) / Math.max(1, r1 - r0);
  }
  return table[table.length - 1][1];
};
/* Calibration: the raw CI-V reading (0-255) at each printed point, and the scope's carrier offset. Set from the
   DevTools tab against a real G7250 and kept in this browser; CAL_DEFAULTS are the uncalibrated starting values. */
const CAL_DEFAULTS = {
  sS9: 120, sP60: 241, iqDb: -40,
  po50: 143, po100: 213,
  swr15: 48, swr2: 80, swr3: 120,
  alcFull: 120,
  comp15: 130, comp30: 241,
  vd10: 13, vd16: 241,
  id10: 97, id15: 146, id25: 241,
  carrierHz: 0,
};
const CAL = { ...CAL_DEFAULTS, ...G.store.get("cal", {}) };
const M = {
  sFrac: 0, sPeak: 0, sPeakT: 0, poFrac: 0, swr: 1, alc: 0, comp: 0,
  poW: (raw) => interp(raw, [[0, 0], [CAL.po50, 50], [CAL.po100, 100], [255, 100 + (50 * (255 - CAL.po100)) / Math.max(1, CAL.po100 - CAL.po50)]]),
  swrV: (raw) => interp(raw, [[0, 1], [CAL.swr15, 1.5], [CAL.swr2, 2], [CAL.swr3, 3], [255, 9.9]]),
  compDb: (raw) => interp(raw, [[0, 0], [CAL.comp15, 15], [CAL.comp30, 30]]),
  vdV: (raw) => interp(raw, [[0, 0], [CAL.vd10, 10], [CAL.vd16, 16]]),
  idA: (raw) => interp(raw, [[0, 0], [CAL.id10, 10], [CAL.id15, 15], [CAL.id25, 25]]),
  alcFrac: (raw) => (raw || 0) / Math.max(1, CAL.alcFull),
};

/** S reading: {frac 0..1 on the S1..S9+60 scale, label, dbm, calibrated} */
M.sReading = () => {
  const r = G.st.rig;
  let dbm, cal = true;
  if (G.has("s_raw")) {
    const raw = r.s_raw;
    const over = raw <= CAL.sS9 ? ((raw / CAL.sS9) * 9 - 9) * 6 : ((raw - CAL.sS9) / Math.max(1, CAL.sP60 - CAL.sS9)) * 60;
    dbm = -73 + over;
  } else {
    dbm = r.iq_level_dbfs + CAL.iqDb; cal = false;    // estimated from the IQ level (DevTools: IQ S offset)
  }
  const over = dbm + 73;
  const s = 9 + over / 6;
  const frac = over <= 0 ? (clamp(s, 1, 9) - 1) / 8 * 0.45 : 0.45 + clamp(over, 0, 60) / 60 * 0.55;
  const label = over <= 0 ? `S${Math.max(0, Math.round(s))}` : over < 5 ? "S9" : `S9+${Math.round(over / 10) * 10}`;
  return { frac, label, dbm, cal };
};

/* ---------- meters: stacked bars and the analogue dial, in purple ---------- */
const MC = {
  print: "#f0f0f0", accent: "#8b5cf6", needle: "#8b5cf6",
  litLow: "#3d2580", lit: "#8b5cf6", hot: "#8b5cf6", hotHigh: "#a07cf8", peak: "#c4b0ff", bloom: [139, 92, 246, 0x80 / 255],
  readout: "#f0f0f0", title: "#aaaaaa", scale: "#7a7a7a", unlit: "#151515", tick: "#3a3a3a", alert: "#d9ccff", inactive: "#5a5a5a",
  face: "#050505", rim: "#202020",
};
/** Ballistics: TX meters reach 90% of a peak in ~46 ms and fall with a 200 ms time constant; S is slower. */
M.ballistic = (tx, cur, target, dt) => { const tau = target > cur ? (tx ? 0.02 : 0.04) : (tx ? 0.2 : 0.32); return cur + (target - cur) * (1 - Math.exp(-dt / tau)); };
const lerpTable = (v, t) => { if (v <= t[0][0]) return t[0][1]; for (let i = 1; i < t.length; i++) if (v <= t[i][0]) return t[i - 1][1] + ((t[i][1] - t[i - 1][1]) * (v - t[i - 1][0])) / (t[i][0] - t[i - 1][0]); return t[t.length - 1][1]; };

/* ---------- Bars (StackedMeterControl): S, PO, SWR, ALC, COMP, thin segments, peak hold 2.5 s ---------- */
const BAR_ROWS = [
  { t: "S", tx: false, hot: 130 / 255, marks: [[12 / 255, "1"], [40 / 255, "3"], [65 / 255, "5"], [95 / 255, "7"], [130 / 255, "9"], [172 / 255, "+20"], [220 / 255, "+40"], [1, "+60"]] },
  { t: "PO", tx: true, hot: 0.8, marks: [[0, "0"], [0.25, "25"], [0.5, "50"], [0.75, "75"], [1, "100"]] },   // the G7250 is a 100 W radio
  { t: "SWR", tx: true, hot: 89 / 255, marks: [[0, "1"], [52 / 255, "1.5"], [89 / 255, "2"], [126 / 255, "3"], [236 / 255, "5"], [1, "∞"]] },
  { t: "ALC", tx: true, hot: 0.75, marks: [[0, "0"], [0.5, "50"], [1, "100"]] },
  { t: "COMP", tx: true, hot: 10 / 30, marks: [[0, "0"], [10 / 30, "10"], [20 / 30, "20"], [1, "30 dB"]] },
].map((r) => ({ ...r, target: 0, anim: 0, held: 0, holdUntil: 0, active: false, text: "--", alert: false }));
M.readBars = () => {
  const r = G.st.rig, tx = !!r.ptt, cat = G.catOn(), S = M.sReading(), over = S.dbm + 73;
  const sFrac = over <= 0
    ? lerpTable(9 + over / 6, [[0, 0], [1, 12], [3, 40], [5, 65], [7, 95], [9, 130]]) / 255
    : lerpTable(over, [[0, 130], [20, 172], [40, 220], [60, 255]]) / 255;
  const set = (row, active, frac, text, alert = false) => Object.assign(row, { active, target: active ? clamp(frac, 0, 1) : 0, text, alert });
  set(BAR_ROWS[0], true, tx ? 0 : sFrac, tx ? "--" : S.label);
  const w = cat && tx ? M.poW(r.po_raw || 0) : 0;
  set(BAR_ROWS[1], cat, w / 100, `${Math.round(w)} W`);
  const swr = cat && tx ? M.swrV(r.swr_raw || 0) : null;
  set(BAR_ROWS[2], cat, swr ? lerpTable(swr, [[1, 0], [1.5, 52], [2, 89], [3, 126], [5, 236], [9.9, 255]]) / 255 : 0, swr ? `${swr.toFixed(1)}:1` : "--", swr >= 2);
  const alc = cat && tx ? clamp(M.alcFrac(r.alc_raw), 0, 1.3) : 0;
  set(BAR_ROWS[3], cat, alc, `${Math.round(alc * 100)} %`, alc >= 0.75);
  const comp = cat && tx ? M.compDb(r.comp_raw || 0) : 0;
  set(BAR_ROWS[4], cat, comp / 30, `${Math.round(comp)} dB`);
};
M.stepBars = (dt) => {
  if (!G.st) return;
  M.readBars();
  const now = performance.now();
  for (const row of BAR_ROWS) {
    row.anim = M.ballistic(row.tx, row.anim, row.target, dt);
    if (row.anim >= row.held) { row.held = row.anim; row.holdUntil = now + 2500; }
    else if (now >= row.holdUntil) row.held = row.anim;          // hold over: back to the live reading at once
  }
};
M.drawBarsOn = (el) => {
  const { c, w, h, dpr } = G.canvas(el);
  c.clearRect(0, 0, w, h);
  if (!G.st || w < 120 || h < 60) return;
  const tx = !!G.st.rig.ptt, pad = 10, readoutW = 62, rowH = (h - pad * 2) / BAR_ROWS.length;
  c.fillStyle = MC.face; c.strokeStyle = MC.rim; c.lineWidth = 1;
  c.beginPath(); c.roundRect(0.5, 0.5, w - 1, h - 1, 4); c.fill(); c.stroke();
  const titleSize = clamp(rowH * 0.2, 8, 10.5) * 0.85, scaleSize = clamp(rowH * 0.2, 9, 11);
  c.font = `400 ${titleSize}px Michroma`;
  const labelW = Math.max(28, c.measureText("COMP").width + 10);
  const barLeft = Math.round(pad + labelW), seg = 2, gap = 2, pitch = seg + gap;
  const segments = Math.max(10, Math.floor((w - pad - readoutW - barLeft + gap) / pitch));
  const barW = pitch * segments - gap, barRight = barLeft + barW;
  const snap = (v) => Math.round(v * dpr) / dpr;
  BAR_ROWS.forEach((row, slot) => {
    const top = pad + slot * rowH, inUse = row.tx === tx;
    c.globalAlpha = inUse ? 1 : 0.38;
    const barTop = snap(top + scaleSize + 10), barH = clamp((rowH - scaleSize - 12) * 0.5, 4, 10);
    c.font = `400 ${titleSize}px Michroma`; c.fillStyle = MC.title; c.textAlign = "left"; c.textBaseline = "middle";
    c.fillText(row.t, pad, barTop + barH / 2);
    c.font = `600 ${scaleSize}px ${MONO}`; c.fillStyle = MC.scale; c.textBaseline = "top";
    for (const [f, t] of row.marks) {
      const tw = c.measureText(t).width;
      c.fillText(t, clamp(barLeft + barW * f - tw / 2, barLeft - 2, barRight - tw + 2), top + 1);
    }
    // ruler: a hairline under the figures with a tick at each mark, the hot end in the hot colour
    const ry = snap(barTop - 4) + 0.5 / dpr, hotX = barLeft + barW * row.hot;
    c.lineWidth = 1 / dpr;
    c.strokeStyle = MC.tick; c.beginPath(); c.moveTo(barLeft, ry); c.lineTo(hotX, ry); c.stroke();
    c.strokeStyle = MC.hot; c.beginPath(); c.moveTo(hotX, ry); c.lineTo(barRight, ry); c.stroke();
    for (const [f] of row.marks) { const x = snap(barLeft + barW * f) + 0.5 / dpr; c.strokeStyle = f > row.hot ? MC.hot : MC.tick; c.beginPath(); c.moveTo(x, ry - 3); c.lineTo(x, ry); c.stroke(); }
    const level = row.active ? Math.min(1, row.anim) : 0;
    const peakSeg = row.active ? Math.min(segments - 1, Math.ceil(Math.min(1, row.held) * segments - 0.5) - 1) : -1;
    const lit = Math.round(clamp(Math.floor(level * segments + 0.5), 0, segments));
    if (lit > 0) {                               // bloom: light from the lit run spilling round it
      const [br, bg, bb, ba] = MC.bloom;
      for (const [spread, a] of [[4.5, 0.16], [2.2, 0.3]]) {
        c.fillStyle = `rgba(${br},${bg},${bb},${ba * a})`;
        c.beginPath(); c.roundRect(barLeft - spread, barTop - spread, lit * pitch - gap + spread * 2, barH + spread * 2, spread); c.fill();
      }
    }
    for (let i = 0; i < segments; i++) {
      const from = i / segments, on = level > from + 0.5 / segments || i === peakSeg;
      let col = MC.unlit;
      if (on) {
        if (i === peakSeg && level <= from + 0.5 / segments) col = MC.peak;
        else if (row.alert) col = MC.alert;
        else col = from >= row.hot ? mixHex(MC.hot, MC.hotHigh, row.hot >= 1 ? 0 : (from - row.hot) / (1 - row.hot)) : mixHex(MC.litLow, MC.lit, row.hot <= 0 ? 1 : from / row.hot);
      }
      c.fillStyle = col; c.fillRect(barLeft + i * pitch, barTop, seg, barH);
    }
    c.font = `400 ${Math.min(13, rowH * 0.32)}px ${MONO}`; c.textAlign = "right"; c.textBaseline = "middle";
    c.fillStyle = !row.active ? MC.inactive : row.alert ? MC.alert : MC.readout;
    c.fillText(row.text, w - 16, barTop + barH / 2 - 1);
    c.globalAlpha = 1;
  });
  c.textBaseline = "alphabetic";
};
const mixHex = (a, b, t) => {
  const pa = hexRgb3(a), pb = hexRgb3(b);
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
};
const hexRgb3 = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

/* ---------- Analog dial (TsAnalogMeterControl, the Black theme's glass dial: S over PO) ----------
   Geometry in the radio photo's pixels: the scale lines are concentric about C, far below the window; the needle
   pivots at P just below it, so each graduation is a ray from P where it crosses its line. */
const AD = { W: 337, H: 88, C: [169.5, 485], P: [170, 194], S: 458, PO: 454, TIP: 471, REST: -45, STOP: 47, needle: -45 };
const PO_SCALE = [[0, -44.7], [5, -38.5], [10, -32.2], [25, -19.9], [50, 0], [100, 29.4], [150, 44.4]];
AD.ray = (angle, radius) => {
  const a = (angle * Math.PI) / 180, dx = Math.sin(a), dy = -Math.cos(a);
  const qx = AD.P[0] - AD.C[0], qy = AD.P[1] - AD.C[1], qd = qx * dx + qy * dy;
  const t = -qd + Math.sqrt(qd * qd - (qx * qx + qy * qy) + radius * radius);
  return [AD.P[0] + t * dx, AD.P[1] + t * dy];
};
AD.sAngle = (db) => (db <= 0 ? Math.max(AD.REST, (db * 5) / 6) : Math.min(44.5, (db * 44.5) / 60));
AD.target = () => {
  const r = G.st.rig;
  if (r.ptt) return G.catOn() ? lerpTable(M.poW(r.po_raw || 0), PO_SCALE) : AD.REST;
  return AD.sAngle(M.sReading().dbm + 73);
};
M.stepDial = (dt) => { if (G.st) AD.needle = M.ballistic(!!G.st.rig.ptt, AD.needle, clamp(AD.target(), AD.REST, AD.STOP), dt); };
M.dialBg = null;
M.drawDial = () => {
  const el = $("dial");
  const { c, w, h, dpr } = G.canvas(el);
  if (!w || !h) return;                    // hidden (MUF / WSPR fill the window)
  const k = Math.min(w / AD.W, h / AD.H), ox = (w - AD.W * k) / 2, oy = (h - AD.H * k) / 2;
  const key = `${w}x${h}@${dpr}`;
  if (!M.dialBg || M.dialBg.key !== key) {
    const off = document.createElement("canvas"); off.width = Math.round(w * dpr); off.height = Math.round(h * dpr);
    const o = off.getContext("2d");
    o.setTransform(dpr, 0, 0, dpr, 0, 0);
    const g = o.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "#000"); g.addColorStop(0.25, "#020202"); g.addColorStop(1, "#040404");
    o.fillStyle = g; o.fillRect(0, 0, w, h);
    o.translate(ox, oy); o.scale(k, k);
    const ang = (p) => Math.atan2(p[1] - AD.C[1], p[0] - AD.C[0]);
    const arc = (from, to, r, col, lw) => { o.beginPath(); o.arc(AD.C[0], AD.C[1], r, ang(AD.ray(from, r)), ang(AD.ray(to, r))); o.strokeStyle = col; o.lineWidth = lw; o.stroke(); };
    const ticks = (r, len, col, lw, ...as) => { o.strokeStyle = col; o.lineWidth = lw; for (const a of as) { const [x0, y0] = AD.ray(a, r), [x1, y1] = AD.ray(a, r + len); o.beginPath(); o.moveTo(x0, y0); o.lineTo(x1, y1); o.stroke(); } };
    const P = MC.print, A = MC.accent;
    arc(-45.5, -0.2, AD.S, P, 1.5); arc(2.1, 44.5, AD.S, A, 2.0); arc(-44.7, 44.4, AD.PO, P, 1.5);
    ticks(AD.S, 7, P, 1.8, -39.8, -29.9, -19.9, -10.1, -0.2);
    ticks(AD.S, 3.5, P, 1.35, -34.8, -24.9, -14.9, -5.1);
    ticks(AD.S, 6, A, 1.7, 14.6, 29.6, 44.5);
    ticks(AD.S, 3.5, A, 1.35, 7.1, 22.0, 36.9);
    ticks(AD.PO, -7, P, 1.8, -44.7, -38.5, -32.2, -19.9, -0.2, 29.4, 44.4);
    ticks(AD.PO, -3.5, P, 1.35, -15.9, -8.0, 7.1, 13.6, 19.4, 25.1, 33.0, 36.2, 39.3, 41.9);
    // figures: Barlow Semi Condensed SemiBold 13.2, centred on their capitals as the radio prints them
    const size = 13.2, cap = 0.716 * size;
    o.font = `600 ${size}px "Barlow Semi Condensed"`; o.textBaseline = "alphabetic";
    const label = (t, x, y, col, align = "center") => { o.fillStyle = col; o.textAlign = align; o.fillText(t, x, y + cap / 2); };
    [["1", 35, 31], ["3", 72, 22], ["5", 106, 16], ["7", 137.5, 13], ["9", 169.5, 12]].forEach(([t, x, y]) => label(t, x, y, P));
    label("+20", 217, 14.5, A); label("+40", 265.5, 23.5, A); label("+60dB", AD.W - 4, 36, A, "right");
    [["0", 41, 63.5], ["5", 61, 58.5], ["10", 81.5, 52.5], ["25", 117, 48], ["50", 169, 44], ["100", 250.5, 50.5]].forEach(([t, x, y]) => label(t, x, y, P));
    label("150W", 295, 65, P, "left");
    label("S", 14.5, 47, P); label("PO", 20, 65, P);
    M.dialBg = { key, off };
  }
  c.drawImage(M.dialBg.off, 0, 0, w, h);
  if (!G.st) return;
  c.save(); c.beginPath(); c.rect(0, 0, w, h); c.clip();
  c.translate(ox, oy); c.scale(k, k);
  const [tx, ty] = AD.ray(AD.needle, AD.TIP);
  c.lineCap = "butt";
  c.strokeStyle = "rgba(139,92,246,0.28)"; c.lineWidth = 4.6; c.beginPath(); c.moveTo(...AD.P); c.lineTo(tx, ty); c.stroke();
  c.strokeStyle = MC.needle; c.lineWidth = 2.4; c.beginPath(); c.moveTo(...AD.P); c.lineTo(tx, ty); c.stroke();
  c.restore();
};

/* ---------- smoothing ---------- */
M.tick = (dt) => {
  if (!G.st) return;
  M.stepBars(dt); M.stepDial(dt);
  const S = M.sReading();
  const k = 1 - Math.exp(-dt / 0.12);
  M.sFrac += (S.frac - M.sFrac) * (S.frac > M.sFrac ? Math.min(1, k * 2.5) : k * 0.6);
  if (M.sFrac > M.sPeak || performance.now() - M.sPeakT > 1500) { M.sPeak = M.sFrac; M.sPeakT = performance.now(); }
  const tx = G.st.rig.ptt, cat = G.catOn();
  M.poFrac += ((tx && cat ? clamp(M.poW(G.st.rig.po_raw || 0) / 100, 0, 1) : 0) - M.poFrac) * k;
};

