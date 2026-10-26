"use strict";
/* The one 3D globe, used for everything: LOG, DX, WSPR and MUF views. */

const GV = {
  view: ["LOG", "DX"].includes(G.store.get("globeView", "LOG")) ? G.store.get("globeView", "LOG") : "LOG",
  period: G.store.get("globePeriod", "all"),
  wsprHours: G.store.get("wsprHours", 24),
  band: "",
  data: {}, muf: null, lookup: null, lastLogIds: new Set(),
};
GV.globe = new Globe($("globeBody"), { hint: "Drag to spin · wheel to zoom · double-click for home" });
GV.globe.onPick = (m) => {
  if (GV.view === "DX" && m.spot) G.tuneSpot(m.spot);
  else GV.globe.select(m);
};
$("globeBody").prepend(...[...$("globeBody").querySelectorAll(".globeLayer")]);   // canvases under the HTML chips

const kmText = (km, approx) => (km == null ? "" : `${approx ? "≈ " : ""}${Math.round(km).toLocaleString()} km · ${Math.round(km * 0.621371).toLocaleString()} mi`);
const homeLatLon = () => {
  const g = (G.st?.grid || "").toUpperCase();
  if (g.length < 4) return null;
  let lon = (g.charCodeAt(0) - 65) * 20 - 180 + Number(g[2]) * 2, lat = (g.charCodeAt(1) - 65) * 10 - 90 + Number(g[3]);
  if (g.length >= 6) { lon += ((g.charCodeAt(4) - 65) + 0.5) / 12; lat += ((g.charCodeAt(5) - 65) + 0.5) / 24; } else { lon += 1; lat += 0.5; }
  return Number.isFinite(lon + lat) ? { lat, lon } : null;
};
GV.homeChanged = () => { const h = homeLatLon(); if (h && (!GV.globe.home || h.lat !== GV.globe.home.lat || h.lon !== GV.globe.home.lon)) { GV.globe.home = null; GV.globe.setHome(h); } };

/* ---------- header controls ---------- */
// LOG and DX on the globe's own tabs; MUF and WSPR open from the Tools buttons in the left panel (the
// MUF / SFI and WSPR tabs), on the expanded globe.
const VIEWS = [["LOG", "LOG"], ["DX", "DX"]];
G.seg($("gView"), VIEWS, { get: () => GV.view, pick: (v) => { GV.view = v; G.store.set("globeView", v); GV.apply(); GV.changed(); } });
GV.changed = () => {};                     // the Tools buttons follow the view (cwwspr.js)
/** MUF or WSPR on the expanded globe; closing returns to LOG or DX. */
GV.show = (view, on) => {
  if (on) { GV.back = ["LOG", "DX"].includes(GV.view) ? GV.view : GV.back || "LOG"; GV.view = view; GV.apply(); GV.expand(true); }
  else if (GV.view === view) { GV.view = GV.back || "LOG"; GV.apply(); GV.expand(false); }
  GV.changed();
};
GV.filterWidget = null;
GV.renderFilter = () => {
  const el = $("gFilter");
  if (GV.view === "LOG") {
    GV.filterWidget = G.seg(el, [["all", "ALL"], ["week", "WEEK"], ["today", "TODAY"], ["hour", "HOUR"]],
      { get: () => GV.period, pick: (v) => { GV.period = v; G.store.set("globePeriod", v); GV.loadLog(); } });
  } else if (GV.view === "WSPR") {
    GV.filterWidget = G.seg(el, [[1, "1H"], [6, "6H"], [24, "24H"]],
      { get: () => GV.wsprHours, pick: (v) => { GV.wsprHours = v; G.store.set("wsprHours", v); GV.loadWspr(true); } });
  } else { el.innerHTML = ""; }
  // the time filter (ALL/WEEK/TODAY/HOUR, 1H/6H/24H) only on the expanded globe
  el.hidden = !(GV.view === "LOG" || GV.view === "WSPR") || !$("globeP").classList.contains("expanded");
  $("gBand").hidden = GV.view === "MUF";
};
$("gBand").addEventListener("change", () => { GV.band = $("gBand").value; GV.apply(false); });
const setBands = (bands) => {
  const sel = $("gBand"), cur = GV.band;
  sel.innerHTML = '<option value="">ALL BANDS</option>' + [...new Set(bands)].filter(Boolean).sort((a, b) => parseFloat(b) - parseFloat(a)).map((b) => `<option${b === cur ? " selected" : ""}>${b}</option>`).join("");
};
$("gExpand").onclick = () => GV.expand(!$("globeP").classList.contains("expanded"));
GV.expand = (on) => {
  $("globeP").classList.toggle("expanded", on);
  $("gExpand").textContent = on ? "✕ CLOSE" : "⤢";
  GV.globe.compact = false;
  // MUF and WSPR live on the expanded globe: closing it goes back to LOG / DX
  if (!on && (GV.view === "MUF" || GV.view === "WSPR")) { GV.view = GV.back || "LOG"; GV.apply(); GV.changed(); }
  else GV.renderFilter();
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("globeP").classList.contains("expanded")) GV.expand(false); });

const stats = (items) => { $("gStats").innerHTML = items.map(([v, label, cls]) => `<div><b class="${cls || ""}">${v}</b><span class="cap">${label}</span></div>`).join(""); };
GV.footer = () => {
  const m = GV.muf;
  $("propLine").innerHTML = GV.view === "MUF" && m
    ? `${esc(m.attribution)} · ${esc(m.state)}${m.map_time ? " · MAP " + new Date(m.map_time * 1000).toISOString().slice(11, 16) + "Z" : ""}<br>${LOG.propText || ""}`
    : GV.view === "WSPR" ? `wspr.live reports${GV.data.wspr?.clock_offset_s != null && Math.abs(GV.data.wspr.clock_offset_s) > 2 ? ` · PC CLOCK OFF BY ${GV.data.wspr.clock_offset_s} s` : ""}<br>${LOG.propText || ""}`
    : LOG.propText || "";
};

/* ---------- LOG ---------- */
GV.loadLog = async (fresh = false) => {
  try {
    const rows = await G.api(`/api/log/globe?period=${GV.period}`);
    const now = Date.now();
    GV.data.log = rows.map((r) => ({
      call: r.call, locator: r.grid, lat: r.lat, lon: r.lon, snr: 0, band: r.band, distanceKm: r.dist_km, approximate: !!r.approx,
      colour: bandColour(r.band),
      addedAt: fresh && !GV.lastLogIds.has(r.id) ? now : null, id: r.id,
      detail: [[r.name, r.country].filter(Boolean).join(" · "), kmText(r.dist_km, r.approx),
        [r.band, r.mode, (r.rst_s || r.rst_r) ? `RST ${r.rst_s}/${r.rst_r}` : ""].filter(Boolean).join(" · "),
        `${r.utc.slice(0, 16).replace("T", " ")} UTC${r.count > 1 ? ` · ${r.count} QSOs` : ""}`].filter(Boolean).join("\n"),
    }));
    GV.lastLogIds = new Set(rows.map((r) => r.id));
    if (GV.view === "LOG") GV.apply(false);
    const fresh1 = GV.data.log.find((m) => m.addedAt);
    if (fresh1 && GV.view === "LOG") setTimeout(() => GV.globe.select(fresh1), 1700);   // line draws out, then select
  } catch { /* offline */ }
};
GV.logChanged = () => GV.loadLog(true);

/* ---------- DX ---------- */
GV.spotMarkers = () => (G.spots || []).filter((s) => s.lat != null).map((s) => {
  const band = (G.bandOf(s.freq_hz)?.b || "") + (G.bandOf(s.freq_hz) ? "m" : "");
  return { call: s.dx, locator: s.prefix, lat: s.lat, lon: s.lon, snr: 0, band, colour: bandColour(band), approximate: true, spot: s,
    detail: `${s.country}\n${G.fmtKhz(s.freq_hz)} kHz · ${band || "—"} · ${s.utc}Z\nde ${s.spotter} · ${s.note || ""}\nClick to tune` };
});

/* ---------- WSPR ---------- */
GV.loadWspr = async (force = false) => {
  try {
    GV.data.wspr = await G.api(`/api/wspr?hours=${GV.wsprHours}${force ? "&force=true" : ""}`);
    GV.data.wsprError = null;
  } catch (e) { GV.data.wsprError = e.message; }
  if (GV.view === "WSPR") GV.apply(false);
};
const wsprMarkers = (list, incoming) => {
  const best = new Map();
  for (const s of list) { const k = s.call; if (!best.has(k) || s.snr > best.get(k).snr) best.set(k, s); }
  return [...best.values()].map((s) => ({
    call: s.call, locator: s.loc, lat: s.lat, lon: s.lon, snr: s.snr, band: s.band, distanceKm: s.km, incoming,
    detail: `${incoming ? "I heard" : "Heard me"} · ${s.band} · SNR ${s.snr} dB\n${kmText(s.km)}\n${s.time.slice(5, 16)} UTC · ${incoming ? "they send" : "I sent"} ${s.dbm} dBm`,
  }));
};

/* ---------- MUF ---------- */
const MUF_RAMP = [[5.3, "#5B6CFF"], [7.0, "#168BFF"], [10.1, "#12B5FF"], [14.0, "#19D8D8"], [18.0, "#46D446"], [21.0, "#A8D838"], [24.8, "#E0A640"], [28.0, "#F03E2D"]].map(([m, h]) => [m, hexRgb(h)]);
const mufColour = (mhz) => {
  if (mhz <= MUF_RAMP[0][0]) return MUF_RAMP[0][1];
  for (let i = 1; i < MUF_RAMP.length; i++) if (mhz <= MUF_RAMP[i][0]) {
    const [m0, c0] = MUF_RAMP[i - 1], [m1, c1] = MUF_RAMP[i], t = (mhz - m0) / (m1 - m0);
    return c0.map((v, k) => Math.round(v + (c1[k] - v) * t));
  }
  return MUF_RAMP[MUF_RAMP.length - 1][1];
};
GV.mufAt = (lat, lon) => {
  const m = GV.muf; if (!m || !m.grid) return null;
  const fy = clamp(90 - lat, 0, m.rows - 1), fx = (((lon + 180) % 360) + 360) % 360;
  const y0 = Math.min(Math.floor(fy), m.rows - 2), x0 = Math.floor(fx) % m.columns, x1 = (x0 + 1) % m.columns, ty = fy - y0, tx = fx - Math.floor(fx);
  const g = (y, x) => m.grid[y * m.columns + x] / m.field_scale;
  return (g(y0, x0) * (1 - tx) + g(y0, x1) * tx) * (1 - ty) + (g(y0 + 1, x0) * (1 - tx) + g(y0 + 1, x1) * tx) * ty;
};
const highestOpen = (mhz) => { let b = null; for (const x of GV.muf?.bands || []) if (x.mhz <= mhz + 0.05) b = x.band; return b; };
GV.loadMuf = async () => {
  try {
    const m = await G.api("/api/muf");
    if (m.field) {
      const raw = atob(m.field); m.grid = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) m.grid[i] = raw.charCodeAt(i);
    }
    m.paths = []; m.anchors = [];
    for (const c of m.contours) {
      const col = mufColour(c.mhz);
      for (const line of c.lines) {
        const pts = line.map(([x, y]) => geoUnit(y, x));
        m.paths.push({ col, pts });
        let run = 0, next = 0.35;
        for (let i = 1; i < pts.length; i++) {
          run += Math.acos(clamp(dot3(pts[i - 1], pts[i]), -1, 1));
          if (run >= next) { m.anchors.push({ band: c.band, col, at: pts[i] }); next += 0.9; }
        }
      }
    }
    GV.muf = m;
    GV.mufLayer = m.grid ? GV.buildMufLayer() : null;
    if (GV.view === "MUF") GV.apply(false);
  } catch { /* offline */ }
};
GV.buildMufLayer = () => {
  const W = 2048, H = 1024, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const c = cv.getContext("2d"), img = c.createImageData(W, H), d = img.data;
  const steps = GV.muf.bands.map((b) => b.mhz);
  const stepFrac = (mhz) => { for (let i = 1; i < steps.length; i++) if (mhz < steps[i]) return mhz < steps[i - 1] ? 0 : (mhz - steps[i - 1]) / (steps[i] - steps[i - 1]); return 1; };
  const lut = Array.from({ length: 401 }, (_, i) => mufColour(i / 10));
  for (let y = 0; y < H; y++) {
    const lat = 90 - ((y + 0.5) * 180) / H;
    for (let x = 0; x < W; x++) {
      const lon = -180 + ((x + 0.5) * 360) / W, mhz = GV.mufAt(lat, lon), col = lut[clamp(Math.round(mhz * 10), 0, 400)];
      let s = 0.10 + 0.30 * clamp((mhz - 5) / 25, 0, 1);
      s *= 0.70 + 0.30 * stepFrac(mhz);
      const i = (y * W + x) * 4;
      d[i] = col[0] * s; d[i + 1] = col[1] * s; d[i + 2] = col[2] * s; d[i + 3] = 255 * s;   // premultiplied, added on the Earth
    }
  }
  c.putImageData(img, 0, 0);
  return cv;
};
/** A line on the sphere, front side only; half strength near the limb so it fades as it turns away. */
const drawRuns = (c, g, pts, col, radius, glow, core, coreA, glowA) => {
  if (pts.length < 2) return;
  const front = new Path2D(), limb = new Path2D();
  let prev = g.project(pts[0], radius), open = 0;
  for (let i = 1; i < pts.length; i++) {
    const cur = g.project(pts[i], radius);
    if (prev.depth < 0 || cur.depth < 0) { open = 0; prev = cur; continue; }
    const band = Math.min(prev.depth, cur.depth) >= 0.18 ? 1 : 2, p = band === 1 ? front : limb;
    if (open !== band) { p.moveTo(prev.x, prev.y); open = band; }
    p.lineTo(cur.x, cur.y); prev = cur;
  }
  c.lineCap = "round"; c.lineJoin = "round";
  c.strokeStyle = rgba(col, glowA); c.lineWidth = glow; c.stroke(front);
  c.strokeStyle = rgba(col, coreA); c.lineWidth = core; c.stroke(front);
  c.strokeStyle = rgba(col, glowA / 2); c.lineWidth = glow; c.stroke(limb);
  c.strokeStyle = rgba(col, coreA / 2); c.lineWidth = core; c.stroke(limb);
};
const mufCard = (c, g, x, y, caption, figure, detail, col) => {
  const lines = detail.split("\n");
  c.font = "600 9.5px 'Barlow Semi Condensed', sans-serif"; let w = c.measureText(caption).width;
  c.font = "700 17px 'Barlow Semi Condensed', sans-serif"; w = Math.max(w, c.measureText(figure).width);
  c.font = "600 10.5px 'Barlow Semi Condensed', sans-serif"; for (const l of lines) w = Math.max(w, c.measureText(l).width);
  w += 22; const h = 14 + 13 + 22 + lines.length * 14;
  if (x + w > g.W - 4) x = x - 32 - w;
  x = Math.max(4, x); y = clamp(y, 4, Math.max(4, g.H - h - 4));
  c.fillStyle = "rgba(6,12,18,0.91)"; c.strokeStyle = rgba(GLOBE_ACCENT, 0.69); c.lineWidth = 1;
  c.beginPath(); c.roundRect(x, y, w, h, 5); c.fill(); c.stroke();
  c.fillStyle = rgba(col, 1); c.fillRect(x, y + 5, 3, h - 10);
  c.textBaseline = "top";
  c.font = "600 9.5px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = rgba(GLOBE_ACCENT, 0.82); c.fillText(caption, x + 11, y + 5);
  c.font = "700 17px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = rgba(col, 1); c.fillText(figure, x + 11, y + 18);
  c.font = "600 10.5px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = "#a8b4ba"; lines.forEach((l, i) => c.fillText(l, x + 11, y + 40 + i * 14));
  c.textBaseline = "alphabetic";
};
GV.mufUnder = (c, g, time) => {
  const m = GV.muf; if (!m) return;
  const gold = [0xe0, 0xa6, 0x40], [dec, sunLon] = subsolar();
  // greyline
  const sun = geoUnit(dec, sunLon);
  let e1 = [sun[1] * 0 - sun[2] * 1, sun[2] * 0 - sun[0] * 0, sun[0] * 1 - sun[1] * 0];   // sun x (0,1,0)
  e1 = [-sun[2], 0, sun[0]]; const l1 = Math.hypot(...e1) || 1; e1 = e1.map((v) => v / l1);
  const e2 = [sun[1] * e1[2] - sun[2] * e1[1], sun[2] * e1[0] - sun[0] * e1[2], sun[0] * e1[1] - sun[1] * e1[0]];
  const ring = Array.from({ length: 181 }, (_, k) => { const a = (k * Math.PI) / 90; return e1.map((v, i) => v * Math.cos(a) + e2[i] * Math.sin(a)); });
  drawRuns(c, g, ring, gold, 1.003, 8, 1.3, 0xa8 / 255, 0x28 / 255);
  const best = ring.map((p) => g.project(p, 1.003)).reduce((a, b) => (b.depth > a.depth ? b : a));
  GV.placed = [];
  if (best.depth >= 0.55) { c.font = "600 9.5px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = rgba(gold, 0.85); c.fillText("GREYLINE", best.x + 6, best.y - 4); GV.placed.push([best.x + 6, best.y - 14, 60, 12]); }
  for (const p of m.paths) drawRuns(c, g, p.pts, p.col, 1.006, 5, 1.6, 0xf0 / 255, 0x48 / 255);
  // ionosondes: footprint, beam as tall as the MUF, value on the cap, sounding pulse up and back
  GV.mufHits = [];
  const shown = m.stations.map((s) => ({ s, at: geoUnit(s.lat, s.lon) })).map((o) => ({ ...o, foot: g.project(o.at) })).filter((o) => o.foot.depth > 0.02).sort((a, b) => a.foot.depth - b.foot.depth);
  for (const { s, at, foot } of shown) {
    const fade = clamp(0.35 + foot.depth * 1.4, 0.35, 1), col = mufColour(s.muf), cap = g.project(at, 1 + 0.04 + 0.0065 * s.muf);
    c.fillStyle = rgba(col, 0.28 * fade); c.strokeStyle = rgba(col, 0.75 * fade); c.lineWidth = 1;
    c.beginPath();
    const east = [at[2], 0, -at[0]], le = Math.hypot(...east) || 1; const E = east.map((v) => v / le);
    const N = [at[1] * E[2] - at[2] * E[1], at[2] * E[0] - at[0] * E[2], at[0] * E[1] - at[1] * E[0]], rr = 1.3 * d2r;
    for (let k = 0; k <= 20; k++) { const a = (k * 2 * Math.PI) / 20; const v = at.map((u, i) => u * Math.cos(rr) + (E[i] * Math.cos(a) + N[i] * Math.sin(a)) * Math.sin(rr)); const p = g.project(v); k ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y); }
    c.fill(); c.stroke();
    c.lineCap = "round"; c.strokeStyle = rgba(col, 0.2 * fade); c.lineWidth = 7; c.beginPath(); c.moveTo(foot.x, foot.y); c.lineTo(cap.x, cap.y); c.stroke();
    const bg = c.createLinearGradient(foot.x, foot.y, cap.x, cap.y); bg.addColorStop(0, rgba(col, (60 / 255) * fade)); bg.addColorStop(1, rgba(col, (250 / 255) * fade));
    c.strokeStyle = bg; c.lineWidth = 2.2; c.beginPath(); c.moveTo(foot.x, foot.y); c.lineTo(cap.x, cap.y); c.stroke();
    const phase = (time * 0.45 + (hashCall(s.name) & 0xff) / 255) % 1, up = phase < 0.5 ? phase * 2 : 2 - phase * 2;
    const px = foot.x + (cap.x - foot.x) * up, py = foot.y + (cap.y - foot.y) * up;
    c.fillStyle = rgba(col, 0.45 * fade); c.beginPath(); c.arc(px, py, 4, 0, 7); c.fill();
    c.fillStyle = `rgba(255,255,255,${0.95 * fade})`; c.beginPath(); c.arc(px, py, 1.7, 0, 7); c.fill();
    if (phase > 0.46 && phase < 0.7) { const t = (phase - 0.46) / 0.24; c.strokeStyle = `rgba(255,255,255,${(1 - t) * 0.8 * fade})`; c.lineWidth = 1.4; c.beginPath(); c.arc(cap.x, cap.y, 9 + 12 * t, 0, 7); c.stroke(); }
    c.fillStyle = rgba(col, 0.3 * fade); c.beginPath(); c.arc(cap.x, cap.y, 13, 0, 7); c.fill();
    c.fillStyle = rgba(col, fade); c.strokeStyle = `rgba(8,13,17,${fade})`; c.lineWidth = 1.5; c.beginPath(); c.arc(cap.x, cap.y, 9, 0, 7); c.fill(); c.stroke();
    if (fade > 0.5) { c.font = "700 10px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = "#080d11"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(String(Math.round(s.muf)), cap.x, cap.y + 0.5); c.textAlign = "left"; c.textBaseline = "alphabetic"; }
    GV.placed.push([cap.x - 11, cap.y - 11, 22, 22]);
    GV.mufHits.push({ s, cap, foot });
  }
  // band labels on the lines, nearest the middle first, never overlapping
  c.font = "600 10.5px 'Barlow Semi Condensed', sans-serif"; c.textBaseline = "middle";
  for (const a of m.anchors.map((a) => ({ ...a, p: g.project(a.at, 1.006) })).filter((a) => a.p.depth > 0.4).sort((x, y) => y.p.depth - x.p.depth)) {
    const tw = c.measureText(a.band).width, box = [a.p.x - tw / 2 - 4, a.p.y - 8, tw + 8, 16];
    if (GV.placed.some((r) => box[0] - 10 < r[0] + r[2] && box[0] + box[2] + 10 > r[0] && box[1] - 6 < r[1] + r[3] && box[1] + box[3] + 6 > r[1])) continue;
    GV.placed.push(box);
    c.globalAlpha = clamp((a.p.depth - 0.4) / 0.2, 0, 1);
    c.fillStyle = "rgba(8,13,17,0.85)"; c.strokeStyle = rgba(a.col, 0.38); c.lineWidth = 1; c.beginPath(); c.roundRect(...box, 3); c.fill(); c.stroke();
    c.fillStyle = rgba(a.col, 1); c.fillText(a.band, box[0] + 4, a.p.y + 0.5);
    c.globalAlpha = 1;
  }
  c.textBaseline = "alphabetic";
  // sun
  const sp = g.project(sun, 1.003);
  if (sp.depth >= 0.05) {
    const gl = c.createRadialGradient(sp.x, sp.y, 0, sp.x, sp.y, 42);
    gl.addColorStop(0, `rgba(255,242,192,${(200 / 255) * sp.depth})`); gl.addColorStop(0.35, `rgba(255,200,80,${(70 / 255) * sp.depth})`); gl.addColorStop(1, "rgba(255,160,48,0)");
    c.fillStyle = gl; c.beginPath(); c.arc(sp.x, sp.y, 42, 0, 7); c.fill();
    c.fillStyle = `rgba(255,248,224,${sp.depth})`; c.beginPath(); c.arc(sp.x, sp.y, 3.5, 0, 7); c.fill();
  }
};
GV.mufTop = (c, g) => {
  const m = GV.muf; if (!m) return;
  // colour scale down the right
  const h = clamp(g.H * 0.42, 120, 260), w = 8, x = g.W - 26, y = (g.H - h) / 2;
  if (x >= g.centre[0] + g.radius * 0.6) {
    const ramp = c.createLinearGradient(0, y + h, 0, y);
    for (let mm = 5; mm <= 30.01; mm += 1) ramp.addColorStop((mm - 5) / 25, rgba(mufColour(mm), 1));
    c.font = "600 9px 'Barlow Semi Condensed', sans-serif"; c.fillStyle = "rgb(120,140,152)"; c.textAlign = "right"; c.fillText("MUF MHz", x + w, y - 6);
    c.fillStyle = ramp; c.strokeStyle = "rgba(232,237,240,0.38)"; c.lineWidth = 0.8; c.beginPath(); c.roundRect(x, y, w, h, 2); c.fill(); c.stroke();
    for (const b of m.bands) {
      const ty = y + h - ((b.mhz - 5) / 25) * h;
      c.strokeStyle = "rgba(232,237,240,0.75)"; c.lineWidth = 1; c.beginPath(); c.moveTo(x - 3, ty); c.lineTo(x, ty); c.stroke();
      c.fillStyle = rgba(mufColour(b.mhz), 1); c.textBaseline = "middle"; c.fillText(b.band, x - 6, ty);
    }
    c.textAlign = "left"; c.textBaseline = "alphabetic";
  }
  // YOUR STATION
  const home = g.home, hm = home && GV.mufAt(home.lat, home.lon);
  if (hm != null) { const b = highestOpen(hm); mufCard(c, g, 10, 10, "YOUR STATION", `${hm.toFixed(1)} MHz`, `MUF(3000 km) overhead · ${b ? "open up to " + b : "below 60m"}`, mufColour(hm)); }
  // hover readout
  const p = g.mouse; if (!p) return;
  const near = (GV.mufHits || []).filter((h) => Math.hypot(h.cap.x - p.x, h.cap.y - p.y) < 12 || Math.hypot(h.foot.x - p.x, h.foot.y - p.y) < 9)[0];
  if (near) {
    const s = near.s, age = Math.max(0, (Date.now() / 1000 - s.time) / 60), mv = GV.mufAt(s.lat, s.lon);
    mufCard(c, g, p.x + 16, p.y - 30, s.name.toUpperCase(), `${s.muf.toFixed(1)} MHz`, `Ionosonde MUF(3000 km)${mv != null ? ` · map ${mv.toFixed(1)}` : ""}\nReading ${new Date(s.time * 1000).toISOString().slice(11, 16)} UTC · ${age.toFixed(0)} min ago${s.cs != null && s.cs >= 0 ? ` · confidence ${s.cs.toFixed(0)}` : ""}`, mufColour(s.muf));
    return;
  }
  const geo = g.geoAt(p.x, p.y); if (!geo) return;
  const mhz = GV.mufAt(geo.lat, geo.lon); if (mhz == null) return;
  const [dec, sl] = subsolar(), f = geo.lat * d2r, dd = dec * d2r;
  const alt = Math.asin(Math.sin(f) * Math.sin(dd) + Math.cos(f) * Math.cos(dd) * Math.cos((geo.lon - sl) * d2r)) / d2r;
  const light = alt > 6 ? `Daylight · sun ${alt.toFixed(0)}° up` : alt < -6 ? `Night · sun ${(-alt).toFixed(0)}° down` : "On the greyline";
  const b = highestOpen(mhz), col = mufColour(mhz);
  c.strokeStyle = "#fff"; c.lineWidth = 1.5; c.beginPath(); c.arc(p.x, p.y, 5, 0, 7); c.stroke();
  c.strokeStyle = rgba(col, 0.9); c.lineWidth = 1.2; c.beginPath(); c.arc(p.x, p.y, 8, 0, 7); c.stroke();
  mufCard(c, g, p.x + 16, p.y - 30, "MUF(3000 km)", `${mhz.toFixed(1)} MHz`,
    `${b ? "Open up to " + b : "Below 60m"}\n${Math.abs(geo.lat).toFixed(1)}°${geo.lat >= 0 ? "N" : "S"} ${Math.abs(geo.lon).toFixed(1)}°${geo.lon >= 0 ? "E" : "W"} · ${light}`, col);
};

/* ---------- apply the current view ---------- */
GV.showLookup = (p) => {
  GV.lookup = p;
  if (GV.view === "LOG" && p && p.lat != null) {
    const existing = (GV.data.log || []).find((m) => m.call === p.call);
    GV.globe.select(existing || { call: p.call, locator: p.grid, lat: p.lat, lon: p.lon, snr: 0, approximate: !!p.approx, colour: null,
      detail: [p.country, kmText(p.dist_km, p.approx)].filter(Boolean).join("\n") });
  }
};
GV.apply = (renderFilter = true) => {
  const g = GV.globe;
  GV.homeChanged();
  if (renderFilter) { GV.renderFilter(); GV.band = ""; }
  g.layers = []; g.topLayers = []; g.holdOnPoint = false;
  g.setSurfaceLayer(null);
  $("gPath").hidden = !(GV.view === "LOG" && GV.lookup && GV.lookup.call);
  const byBand = (list) => (GV.band ? list.filter((m) => m.band === GV.band) : list);
  if (GV.view === "LOG") {
    const all = GV.data.log || [];
    setBands(all.map((m) => m.band));
    const shown = byBand(all);
    g.compact = false;
    g.setMarkers(shown, []);
    const countries = new Set(shown.map((m) => (m.detail.split("\n")[0] || "").split(" · ").pop())).size;
    const best = shown.reduce((a, b) => ((b.distanceKm || 0) > (a?.distanceKm || 0) ? b : a), null);
    stats([[shown.length.toLocaleString(), "Stations", "blue"], [countries, "Countries", "amb"], [best ? `${Math.round(best.distanceKm).toLocaleString()}` : "—", best ? `km · ${best.call}` : "Best DX"]]);
    $("gInfo").textContent = "QSOs · great-circle";
  } else if (GV.view === "DX") {
    const all = GV.spotMarkers();
    setBands(all.map((m) => m.band));
    const shown = byBand(all);
    g.setMarkers(shown, []);
    stats([[shown.length, "Spots", "blue"], [new Set(shown.map((m) => m.spot.country)).size, "Countries", "amb"]]);
    $("gInfo").textContent = "click a station to tune";
  } else if (GV.view === "WSPR") {
    const w = GV.data.wspr;
    // our own decoder's spots join the wspr.live "I heard" list
    const band = G.st?.wspr?.dial_hz ? G.bandOf(G.st.wspr.dial_hz) : null;
    const local = (GV.wsprLocal || []).filter((d) => d.lat != null).map((d) => ({ call: d.call, loc: d.grid, lat: d.lat, lon: d.lon, snr: d.snr,
      band: band ? `${band.b}m` : "", km: d.km, time: d.time, dbm: d.dbm }));
    const heard = (w ? w.i_heard : []).concat(local);
    const out = w ? wsprMarkers(w.heard_me, false) : [], inc = wsprMarkers(heard, true);
    setBands((w ? w.heard_me : []).concat(heard).map((s) => s.band));
    const filt = (list) => (GV.band ? list.filter((m) => m.band === GV.band) : list);
    g.setMarkers(filt(out), filt(inc));
    const best = filt(out).reduce((a, b) => ((b.distanceKm || 0) > (a?.distanceKm || 0) ? b : a), null);
    stats([[filt(out).length, "Heard me", "blue"], [filt(inc).length, "I heard", "amb"], [best ? Math.round(best.distanceKm).toLocaleString() : "—", best ? `km · ${best.call}` : "Best DX"]]);
    $("gInfo").textContent = GV.data.wsprError ? GV.data.wsprError : w ? `${w.call} · last ${w.hours} h` : "loading…";
  } else if (GV.view === "MUF") {
    g.setMarkers([], []);
    if (GV.mufLayer) g.setSurfaceLayer(GV.mufLayer);
    g.layers = [GV.mufUnder]; g.topLayers = [GV.mufTop]; g.holdOnPoint = true;
    $("gStats").innerHTML = "";
    $("gInfo").textContent = GV.muf ? `MUF(3000 km) · ${GV.muf.state}` : "loading…";
  }
  GV.footer();
};

/* ---------- refresh ---------- */
const prevSpots = LOG.loadSpots;
LOG.loadSpots = async () => { await prevSpots(); if (GV.view === "DX") GV.apply(false); };
G.on("status", () => GV.homeChanged());
GV.loadLog(); GV.loadMuf(); GV.loadWspr();
setInterval(GV.loadMuf, 5 * 60 * 1000);
setInterval(() => { if (GV.view === "WSPR") GV.loadWspr(); }, 2 * 60 * 1000);
GV.apply();
