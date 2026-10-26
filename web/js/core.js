"use strict";
/* Shared state, server connection and helpers. Every other script hangs off G. */

const $ = (id) => document.getElementById(id);
const MONO = '"Barlow Semi Condensed", "Segoe UI", sans-serif';
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

const G = {
  st: null,                 // latest status from the server
  bins: null,               // latest spectrum, Float32Array dBFS, lowest frequency first
  specFrames: 0,
  _handlers: {},
  on(evt, fn) { (this._handlers[evt] ||= []).push(fn); },
  emit(evt, data) { (this._handlers[evt] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
};

G.store = {
  get(k, d) { try { const v = localStorage.getItem("g7250." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("g7250." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

/* UI-only preferences (per browser) */
G.prefs = {
  step: G.store.get("step", 100),
  zoom: G.store.get("zoom", 48000),
  palette: G.store.get("palette", "BLUE"),
  avg: G.store.get("avg", "AVG"),
  spots: G.store.get("spots", true),
  dxBand: G.store.get("dxBand", true),
  af: G.store.get("af", 65),
  sql: G.store.get("sql", 0),
  mute: false,
  floor: G.store.get("floor", null),
  range: G.store.get("range", 70),
};
G.setPref = (k, v) => { G.prefs[k] = v; G.store.set(k, v); G.emit("prefs", k); };

G.toast = (msg, err = false) => {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(G._toastT);
  G._toastT = setTimeout(() => (t.className = "toast"), 2600);
};

G.api = async (path, body, method) => {
  const opts = body === undefined && !method ? {} : {
    method: method || "POST", headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error((data && data.detail) || `${res.status} ${path}`);
  return data;
};

/** Send one control to the radio; failures show as a toast, not an exception. */
G.control = (name, value) => G.api("/api/control", { name, value }).catch((e) => G.toast(e.message, true));

G.tune = (hz) => {
  hz = Math.round(clamp(hz, 100000, 54000000));
  if (G.st) { G.st.rig.freq = hz; G.emit("tuned", hz); }
  clearTimeout(G._tuneT);                  // coalesce wheel spins
  G._tuneT = setTimeout(() => G.api("/api/frequency", { hz }).catch((e) => G.toast(e.message, true)), 30);
};

/* ---------- capability checks ---------- */
G.catOn = () => !!(G.st && G.st.cat.connected);
G.has = (field) => G.catOn() && G.st.rig[field] !== undefined && G.st.rig[field] !== null;

/* ---------- formatting ---------- */
G.fmtMHz = (hz) => {
  if (!hz) return "—";
  const s = String(Math.round(hz)).padStart(7, "0");
  return `${s.slice(0, -6)}.${s.slice(-6, -3)}.${s.slice(-3)}`;
};
G.fmtKhz = (hz) => (hz / 1000).toFixed(1);
G.utcNow = () => new Date().toISOString().slice(11, 19);

/* ---------- bands ---------- */
G.BANDS = [
  { b: "160", lo: 1800000, hi: 2000000, def: 1840000 },
  { b: "80", lo: 3500000, hi: 4000000, def: 3573000 },
  { b: "60", lo: 5250000, hi: 5450000, def: 5357000 },
  { b: "40", lo: 7000000, hi: 7300000, def: 7074000 },
  { b: "30", lo: 10100000, hi: 10150000, def: 10136000 },
  { b: "20", lo: 14000000, hi: 14350000, def: 14074000 },
  { b: "17", lo: 18068000, hi: 18168000, def: 18100000 },
  { b: "15", lo: 21000000, hi: 21450000, def: 21074000 },
  { b: "12", lo: 24890000, hi: 24990000, def: 24915000 },
  { b: "10", lo: 28000000, hi: 29700000, def: 28074000 },
  { b: "6", lo: 50000000, hi: 54000000, def: 50313000 },
];
G.bandOf = (hz) => G.BANDS.find((x) => hz >= x.lo && hz <= x.hi) || null;

/* Simplified IARU Region 1 band plan, for the strip above the spectrum. */
G.PLAN = [
  [1810000, 1838000, "CW"], [1838000, 1843000, "DIGI"], [1843000, 2000000, "PHONE"],
  [3500000, 3570000, "CW"], [3570000, 3600000, "DIGI"], [3600000, 3800000, "PHONE"],
  [7000000, 7040000, "CW"], [7040000, 7060000, "DIGI"], [7060000, 7200000, "PHONE"],
  [10100000, 10130000, "CW"], [10130000, 10150000, "DIGI"],
  [14000000, 14070000, "CW"], [14070000, 14099000, "DIGI"], [14099000, 14101000, "BEACON"], [14101000, 14112000, "DIGI"], [14112000, 14350000, "PHONE"],
  [18068000, 18095000, "CW"], [18095000, 18111000, "DIGI"], [18111000, 18168000, "PHONE"],
  [21000000, 21070000, "CW"], [21070000, 21151000, "DIGI"], [21151000, 21450000, "PHONE"],
  [24890000, 24915000, "CW"], [24915000, 24931000, "DIGI"], [24931000, 24990000, "PHONE"],
  [28000000, 28070000, "CW"], [28070000, 28190000, "DIGI"], [28190000, 28225000, "BEACON"], [28225000, 29700000, "PHONE"],
  [50000000, 50100000, "CW"], [50100000, 50300000, "PHONE"], [50300000, 50500000, "DIGI"],
];

/* ---------- live connection ---------- */
G.connect = () => {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  G.ws = ws;
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      G.st = JSON.parse(ev.data);
      G.emit("status", G.st);
    } else {
      G.bins = new Float32Array(ev.data);
      G.specFrames++;
      G.emit("spectrum", G.bins);
    }
  };
  ws.onclose = () => { G.st && (G.st.lan.receiving = false); G.emit("offline"); setTimeout(G.connect, 1500); };
};
G.send = (obj) => { if (G.ws && G.ws.readyState === 1) G.ws.send(JSON.stringify(obj)); };

/* ---------- canvas helper ---------- */
G.canvas = (el) => {
  const dpr = window.devicePixelRatio || 1;
  const w = el.clientWidth, h = el.clientHeight;
  if (el.width !== Math.round(w * dpr) || el.height !== Math.round(h * dpr)) {
    el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
  }
  const c = el.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { c, w, h, dpr };
};
