"use strict";
/* DevTools tab: calibrate the meters, bars and the scope's carrier line against a real G7250. Each slider is the raw
   CI-V reading (0-255) the radio sends at a printed point (or a dB / Hz offset); the live raw readings sit beside them,
   so a value can be read off the radio's own meter and matched here. Kept in this browser (localStorage "cal");
   COPY VALUES / LOAD VALUES share a calibration as JSON, e.g. in a GitHub issue. */

const DEV_GROUPS = [
  ["S meter", "s_raw", [["sS9", "Raw at S9", 1, 255], ["sP60", "Raw at S9+60", 1, 255], ["iqDb", "IQ S offset (no CAT), dB", -80, 0]]],
  ["PO", "po_raw", [["po50", "Raw at 50 W", 1, 255], ["po100", "Raw at 100 W", 1, 255]]],
  ["SWR", "swr_raw", [["swr15", "Raw at 1.5:1", 1, 255], ["swr2", "Raw at 2:1", 1, 255], ["swr3", "Raw at 3:1", 1, 255]]],
  ["ALC", "alc_raw", [["alcFull", "Raw at 100 %", 1, 255]]],
  ["COMP", "comp_raw", [["comp15", "Raw at 15 dB", 1, 255], ["comp30", "Raw at 30 dB", 1, 255]]],
  ["VDD", "vd_raw", [["vd10", "Raw at 10 V", 1, 255], ["vd16", "Raw at 16 V", 1, 255]]],
  ["IDD", "id_raw", [["id10", "Raw at 10 A", 1, 255], ["id15", "Raw at 15 A", 1, 255], ["id25", "Raw at 25 A", 1, 255]]],
  ["Scope", null, [["carrierHz", "Carrier line / passband, Hz", -500, 500]]],
];
const DEV = {};
DEV.save = () => { G.store.set("cal", CAL); M.dialBg = null; scope.dirty = true; };
DEV.reading = (field) => {
  const r = G.st?.rig || {}, raw = r[field];
  if (raw == null) return "—";
  const v = { s_raw: () => M.sReading().label, po_raw: () => `${M.poW(raw).toFixed(1)} W`, swr_raw: () => `${M.swrV(raw).toFixed(2)}:1`,
    alc_raw: () => `${Math.round(M.alcFrac(raw) * 100)} %`, comp_raw: () => `${M.compDb(raw).toFixed(1)} dB`,
    vd_raw: () => `${M.vdV(raw).toFixed(1)} V`, id_raw: () => `${M.idA(raw).toFixed(1)} A` }[field];
  return `raw ${raw} → ${v ? v() : ""}`;
};
DEV.build = () => {
  const pane = document.querySelector('[data-pane="dev"]');
  pane.innerHTML = `<div class="devHead row"><span class="lbl">Calibrate against a real G7250: read the radio's own meter, then move the slider until this app agrees. Kept in this browser.</span><span class="sp"></span>
    <button class="btn" id="devCopy">COPY VALUES</button><button class="btn" id="devLoad">LOAD VALUES</button><button class="btn" id="devReset">RESET ALL</button></div>
    <div class="devGrid" id="devGrid"></div>`;
  for (const [title, field, rows] of DEV_GROUPS) {
    const g = document.createElement("section");
    g.className = "devGroup";
    g.innerHTML = `<div class="row devTitle"><span>${title}</span><span class="sp"></span><span class="dim" data-live="${field || ""}"></span></div>`;
    for (const [key, label, min, max] of rows) {
      const row = document.createElement("div");
      row.className = "sl2";
      row.innerHTML = `<div class="row"><span>${label}</span><span class="v" data-v="${key}"></span></div><div class="trk"></div>`;
      g.appendChild(row);
      G.slider(row.querySelector(".trk"), {
        min, max, get: () => CAL[key],
        change: (v) => { CAL[key] = v; DEV.save(); },
        show: (v) => (row.querySelector(`[data-v="${key}"]`).textContent = `${v}${v === CAL_DEFAULTS[key] ? "" : " *"}`),
      });
    }
    $("devGrid").appendChild(g);
  }
  $("devCopy").onclick = async () => {
    const text = JSON.stringify(CAL);
    try { await navigator.clipboard.writeText(text); G.toast("Calibration copied"); } catch { prompt("Copy these values:", text); }
  };
  $("devLoad").onclick = () => {
    const text = prompt("Paste calibration values (JSON):");
    if (!text) return;
    try {
      const v = JSON.parse(text);
      for (const k of Object.keys(CAL_DEFAULTS)) if (Number.isFinite(v[k])) CAL[k] = v[k];
      DEV.save(); G.refresh(); G.toast("Calibration loaded");
    } catch { G.toast("Those values could not be read", true); }
  };
  $("devReset").onclick = () => { if (!confirm("Put every calibration value back to its default?")) return; Object.assign(CAL, CAL_DEFAULTS); DEV.save(); G.refresh(); G.toast("Calibration reset"); };
};
DEV.build();
setInterval(() => {
  if (TAB.cur !== "dev") return;
  document.querySelectorAll("[data-live]").forEach((el) => { if (el.dataset.live) el.textContent = DEV.reading(el.dataset.live); });
}, 250);
