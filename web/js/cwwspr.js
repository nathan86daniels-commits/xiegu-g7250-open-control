"use strict";
/* Floating panels, opened from the Tools buttons in the left panel: CW decoder, CW sender (keyer
   memories and typed text), CW OPS, WSPR receive/transmit. MUF opens the globe. */

const POP = {};

/* ---------- floating panel plumbing: drag by the header, remember place and size ---------- */
POP.init = (id, btnId) => {
  const el = $(id), saved = G.store.get(`pop.${id}`, null);
  if (saved) Object.assign(el.style, saved);
  const keep = () => G.store.set(`pop.${id}`, { left: el.style.left, top: el.style.top, width: el.style.width, height: el.style.height });
  const head = el.querySelector(".drag");
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button") || el.classList.contains("docked")) return;
    const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    const move = (ev) => {
      el.style.left = `${clamp(ev.clientX - dx, 0, innerWidth - 120)}px`;
      el.style.top = `${clamp(ev.clientY - dy, 0, innerHeight - 40)}px`;
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", () => { head.removeEventListener("pointermove", move); keep(); }, { once: true });
  });
  new ResizeObserver(() => { if (!el.hidden && !el.classList.contains("docked")) keep(); }).observe(el);
  el.addEventListener("pointerdown", () => { document.querySelectorAll(".pop").forEach((p) => (p.style.zIndex = 8)); el.style.zIndex = 9; });
  $(btnId).onclick = () => POP.toggle(id, btnId);
  el.querySelector(".popX").onclick = () => POP.toggle(id, btnId, false);
  if (G.store.get(`pop.${id}.open`, false)) POP.toggle(id, btnId, true);
};
POP.toggle = (id, btnId, show) => {
  const el = $(id), open = show ?? el.hidden;
  if (open === !el.hidden) return;
  el.hidden = !open;
  $(btnId).classList.toggle("on", open);
  G.store.set(`pop.${id}.open`, open);
  el.dispatchEvent(new Event(open ? "opened" : "closed"));
};

let cfg = null;                            // server settings (cw_memories, wspr)
const loadCfg = async () => { cfg = await G.api("/api/settings"); return cfg; };

/* ================= CW ================= */
const CW = { next: 0, timer: null, enabled: true };
const fill = (text) => text.replace(/\{DX\}/gi, $("lgCall").value.trim().toUpperCase());

CW.renderMems = () => {
  const box = $("cwMems");
  box.innerHTML = "";
  (cfg?.cw_memories || []).slice(0, 6).forEach((m, i) => {
    const b = document.createElement("button");
    b.className = "btn"; b.innerHTML = `${esc(m.label || "M" + (i + 1))}<small>F${i + 1}</small>`; b.title = m.text;
    b.onclick = () => CW.send(m.text);
    b.oncontextmenu = (e) => { e.preventDefault(); CW.editMem(i); };
    box.appendChild(b);
  });
  G.refresh();
};
CW.editMem = (i) => {
  const m = cfg.cw_memories[i] || { label: "", text: "" };
  $("memSlot").textContent = `M${i + 1} · F${i + 1}`;
  $("memLabel").value = m.label; $("memText").value = m.text;
  $("memDlg").dataset.slot = i;
  $("memDlg").showModal();
};
/* every CW message in one place: the sender's M1-M6 and the CQ 1-3 buttons under PTT; editable in any mode */
$("cwEditMsgs").onclick = async () => {
  try { await loadCfg(); } catch (e) { G.toast(e.message, true); return; }
  const mems = cfg.cw_memories || [], cq = cfg.cq || [];
  const row = (key, name, label, text, editLabel) => `<span class="lbl">${name}</span>`
    + (editLabel ? `<input class="inp msgLabel" data-k="${key}" data-f="label" maxlength="8" value="${esc(label)}" placeholder="Label">` : "<span></span>")
    + `<input class="inp" data-k="${key}" data-f="text" value="${esc(text)}" placeholder="Message">`;
  $("msgRows").innerHTML = '<span></span><span class="lbl">Button</span><span class="lbl">Message</span>'
    + Array.from({ length: 6 }, (_, i) => row(`m${i}`, `M${i + 1}`, mems[i]?.label || "", mems[i]?.text || "", true)).join("")
    + Array.from({ length: 3 }, (_, i) => row(`c${i}`, `CQ ${i + 1}`, "", cq[i] || "", false)).join("");
  $("msgDlg").showModal();
};
$("msgForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const v = (k, f) => document.querySelector(`#msgRows [data-k="${k}"][data-f="${f}"]`).value.trim();
  const cw_memories = Array.from({ length: 6 }, (_, i) => ({ label: v(`m${i}`, "label").toUpperCase(), text: v(`m${i}`, "text") }));
  const cq = Array.from({ length: 3 }, (_, i) => v(`c${i}`, "text"));
  try {
    cfg = await G.api("/api/settings", { cw_memories, cq });
    settings = cfg; renderCq();             // the CQ buttons under PTT (main.js)
    CW.renderMems(); $("msgDlg").close(); G.toast("CW messages saved");
  } catch (err) { G.toast(err.message, true); }
});
$("memForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const mems = cfg.cw_memories.map((m) => ({ ...m }));
  mems[Number($("memDlg").dataset.slot)] = { label: $("memLabel").value.trim().toUpperCase(), text: $("memText").value.trim() };
  try { cfg = await G.api("/api/settings", { cw_memories: mems }); CW.renderMems(); $("memDlg").close(); }
  catch (err) { G.toast(err.message, true); }
});

CW.send = async (text) => {
  text = fill(text || "").trim();
  if (!text) return;
  if (!G.catOn()) { G.toast("CW send needs USB CAT", true); return; }
  if (!/^CW/.test(G.st.rig.mode)) { G.toast("Switch to CW mode to send", true); return; }
  try { await G.api("/api/cw/send", { text }); } catch (e) { G.toast(e.message, true); }
};
CW.stop = () => G.api("/api/cw/stop", {}).catch((e) => G.toast(e.message, true));
$("cwSendBtn").onclick = () => { CW.send($("cwFree").value); $("cwFree").value = ""; };
$("cwStopBtn").onclick = CW.stop;
$("cwFree").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); $("cwSendBtn").click(); }
  else if (e.key === "Escape") { e.preventDefault(); CW.stop(); }
});
$("cwClear").onclick = () => { G.api("/api/cw/clear", {}); $("cwRx").textContent = ""; };
$("cwDecTog").onclick = async () => {
  const r = await G.api("/api/cw/decoder", { enabled: !CW.enabled });
  CW.enabled = r.enabled;
  $("cwDecTog").textContent = CW.enabled ? "DECODER ON" : "DECODER OFF";
  $("cwDecTog").classList.toggle("dim", !CW.enabled);
};
CW.decoder = "neural"; CW.delay = 1;
G.seg($("cwDecSg"), [["neural", "NEURAL"], ["classic", "CLASSIC"]], {
  get: () => CW.decoder,
  pick: async (v) => { const r = await G.api("/api/cw/decoder", { decoder: v }); CW.decoder = r.decoder; G.refresh(); },
});
const DELAYS = [1, 2, 0];
const delayLabel = () => { $("cwDelay").textContent = CW.delay > 0 ? `DELAY ${CW.delay} s` : "DELAY 10 s"; $("cwDelay").hidden = CW.decoder !== "neural"; };
$("cwDelay").onclick = async () => {
  const next = DELAYS[(DELAYS.indexOf(CW.delay) + 1) % DELAYS.length];
  const r = await G.api("/api/cw/decoder", { delay: next });
  CW.delay = r.delay; delayLabel();
};
document.addEventListener("keydown", (e) => {
  if ($("cwSendPop").hidden || document.querySelector("dialog[open]")) return;
  const m = /^F([1-6])$/.exec(e.key);
  if (m) { e.preventDefault(); const mem = cfg?.cw_memories?.[Number(m[1]) - 1]; if (mem) CW.send(mem.text); }
  else if (e.key === "Escape" && CW.sending) CW.stop();
});

/* keyer speed: CI-V level 0-255 = 6-48 wpm (Icom convention, which Xiegu follows) */
const wpmOf = (v) => Math.round(6 + (v * 42) / 255);
G.slider($("cwWpmS"), {
  min: 0, max: 255, get: () => G.st?.rig.keyer_speed,
  change: (v) => G.control("keyer_speed", v),
  show: (v) => ($("cwWpmV").textContent = G.st?.rig.keyer_speed != null || G.catOn() ? `${wpmOf(v)} WPM` : "—"),
  enabled: () => G.catOn() && G.st.rig.keyer_speed != null,
});
G.widgets.push({ update() {
  const ok = G.catOn() && /^CW/.test(G.st?.rig.mode || "");
  document.querySelectorAll("#cwMems .btn, #cwSendBtn").forEach((b) => (b.disabled = !ok));
  $("cwNote").textContent = ok ? "F1–F6 send the memories · EDIT MESSAGES changes them · sent by the radio's keyer (CI-V 17h)"
    : !G.catOn() ? "Sending needs USB CAT · the decoder works from the LAN IQ in CW mode" : "Switch the radio to CW to send · EDIT MESSAGES changes them in any mode";
} });

CW.poll = async () => {
  try {
    const r = await G.api(`/api/cw?since=${CW.next}`);
    if (r.next < CW.next) CW.next = 0;           // server restarted
    else {
      if (r.text) {
        const box = $("cwRx"), atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
        box.textContent = (box.textContent + r.text).slice(-6000);
        if (atEnd) box.scrollTop = box.scrollHeight;
      }
      CW.next = r.next;
    }
    CW.sending = r.sending;
    if (r.decoder && (r.decoder !== CW.decoder || r.delay !== CW.delay)) { CW.decoder = r.decoder; CW.delay = r.delay; G.refresh(); }
    delayLabel();
    $("cwTone").classList.toggle("on", !!r.key_down);
    $("cwSending").hidden = !r.sending;
    $("cwSending").textContent = r.sending ? `SENDING ▸ ${r.sending}` : "";
    const cwMode = /^CW/.test(G.st?.rig.mode || "");
    const neuralNote = r.decoder === "neural" && r.neural_status && r.neural_status !== "READY" ? `NEURAL: ${r.neural_status} · ` : "";
    $("cwInfo").textContent = neuralNote + (!cwMode ? "DECODER · CW MODE ONLY"
      : r.tone ? `${r.wpm ? Math.round(r.wpm) + " WPM · " : ""}SNR ${Math.round(r.snr_db)} dB${r.lock ? ` · LOCK ${r.offset_hz >= 0 ? "+" : ""}${r.offset_hz} Hz` : ""}${r.slow_down > 1 ? ` · SLOWED ×${r.slow_down}` : ""}` : "LISTENING");
  } catch { /* offline */ }
};
$("cwSendPop").addEventListener("opened", () => { loadCfg().then(CW.renderMems).catch(() => {}); });
setInterval(() => { if (!$("cwDecPop").hidden || !$("cwSendPop").hidden) CW.poll(); }, 250);

/* ================= WSPR ================= */
const DBM = [0, 3, 7, 10, 13, 17, 20, 23, 27, 30, 33, 37, 40, 43, 47, 50, 53, 57, 60];
const WATTS = (d) => { const w = 10 ** (d / 10) / 1000; return w < 1 ? `${Math.round(w * 1000)} mW` : `${+w.toFixed(w < 10 ? 1 : 0)} W`; };
const DIAL = { "160m": 1836600, "80m": 3568600, "60m": 5287200, "40m": 7038600, "30m": 10138700, "20m": 14095600, "17m": 18104600,
  "15m": 21094600, "12m": 24924600, "10m": 28124600, "6m": 50293000 };
const W = { live: null, outputs: null };
$("wBand").innerHTML = Object.keys(DIAL).map((b) => `<option>${b}</option>`).join("");
$("wDbm").innerHTML = DBM.map((d) => `<option value="${d}">${d} dBm · ${WATTS(d)}</option>`).join("");

W.fillForm = async () => {
  await loadCfg();
  const w = cfg.wspr;
  $("wBand").value = w.band; $("wDbm").value = String(w.dbm); $("wWatts").value = w.watts; $("wAudioHz").value = w.audio_hz;
  $("wEvery").value = String(w.every); $("wUpload").checked = !!w.upload; $("wSwr").checked = w.stop_on_high_swr !== false;
  $("wDial").textContent = `${(DIAL[w.band] / 1e6).toFixed(4)} USB`;
  try { W.outputs = await G.api("/api/audio/outputs"); } catch { W.outputs = []; }
  $("wOut").innerHTML = '<option value="">Pick the radio\'s USB audio…</option>'
    + W.outputs.map((n) => `<option${n === w.audio_out ? " selected" : ""}>${esc(n)}</option>`).join("");
};
W.save = async () => {
  const every = Number($("wEvery").value);
  try {
    cfg = await G.api("/api/settings", { wspr: {
      band: $("wBand").value, dbm: Number($("wDbm").value), watts: clamp(Number($("wWatts").value) || 5, 1, 100),
      audio_hz: clamp(Number($("wAudioHz").value) || 1500, 1410, 1590), every, tx_percent: Math.round(100 / every),
      audio_out: $("wOut").value, upload: $("wUpload").checked, stop_on_high_swr: $("wSwr").checked,
    } });
    $("wDial").textContent = `${(DIAL[cfg.wspr.band] / 1e6).toFixed(4)} USB`;
  } catch (e) { G.toast(e.message, true); }
};
["wBand", "wDbm", "wWatts", "wAudioHz", "wEvery", "wOut", "wUpload", "wSwr"].forEach((id) => $(id).addEventListener("change", W.save));

W.start = async (transmit) => {
  if (transmit) {
    if (!G.catOn()) { G.toast("WSPR transmit needs USB CAT", true); return; }
    if (!G.st.callsign || !G.st.grid) { G.toast("Set your callsign and grid in Settings first", true); return; }
    if (!$("wOut").value) { G.toast("Pick the radio's USB audio output first", true); return; }
  }
  await W.save();
  try { W.live = { ...W.live, ...(await G.api("/api/wspr/start", { transmit })) }; W.render(); } catch (e) { G.toast(e.message, true); }
};
$("wRx").onclick = () => W.start(false);
$("wTx").onclick = () => W.start(true);
$("wStop").onclick = () => G.api("/api/wspr/stop", {}).then(W.poll).catch((e) => G.toast(e.message, true));
$("wsprGlobe").onclick = () => GV.show("WSPR", true);

W.render = () => {
  const l = W.live;
  if (!l) return;
  const tx = !!l.transmitting, run = !!l.running;
  $("wsprDot").className = "dot" + (tx ? " tx" : run ? " rx" : "");
  $("wsprState").textContent = run ? l.state || "RUNNING" : "STOPPED";
  $("wRx").classList.toggle("on", run && !l.transmit_enabled && !tx);
  $("wTx").classList.toggle("on", tx);
  ["wBand", "wDbm", "wWatts", "wAudioHz", "wOut"].forEach((id) => ($(id).disabled = run));
  const bar = $("wProgBar");
  if (tx && l.progress) {
    bar.className = "tx"; bar.style.width = `${Math.min(100, (l.progress.seconds / 110.6) * 100)}%`;
    $("wProgText").textContent = `TX ${l.progress.seconds.toFixed(0)} / 111 s · ${l.progress.watts != null ? l.progress.watts.toFixed(1) + " W" : "— W"}${l.progress.swr ? ` · SWR ${l.progress.swr.toFixed(1)}` : ""}`;
  } else if (run && l.capturing_s) {
    bar.className = ""; bar.style.width = `${Math.min(100, (l.capturing_s / 114) * 100)}%`;
    $("wProgText").textContent = `RX capturing ${l.capturing_s.toFixed(0)} / 114 s`;
  } else {
    bar.style.width = "0"; $("wProgText").textContent = run ? "Waiting for the next even minute" : l.last_tx || "";
  }
  const rows = (l.decodes || []).slice(0, 150);
  $("wsprTable").innerHTML = "<thead><tr><th>UTC</th><th>CALL</th><th>GRID</th><th>dBm</th><th>SNR</th><th>DT</th><th>FREQ MHz</th><th>DRIFT</th><th>KM</th></tr></thead><tbody>"
    + (rows.map((d, i) => `<tr data-i="${i}"><td>${esc(d.time.slice(11))}</td><td class="c">${esc(d.call)}</td><td>${esc(d.grid || "")}</td><td>${d.dbm}</td>
      <td>${d.snr}</td><td>${d.dt.toFixed(1)}</td><td>${(d.freq_hz / 1e6).toFixed(6)}</td><td>${d.drift}</td><td>${d.km != null ? Math.round(d.km).toLocaleString() : ""}</td></tr>`).join("")
      || `<tr><td colspan="9" class="dim">${run ? "Decodes appear after each 2-minute slot." : "Press RECEIVE to decode WSPR from the LAN IQ (USB mode, WSPR dial)."}</td></tr>`)
    + "</tbody>";
  const txs = (l.tx_log || []).length;
  $("wNote").textContent = `${txs ? `${txs} transmission${txs === 1 ? "" : "s"} this session · ` : ""}Transmit plays the WSPR tones into the radio's USB audio; stops on PTT release, CAT loss, mode change, no power or high SWR.`;
  GV.wsprLocal = l.decodes || [];
};
$("wsprTable").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-i]");
  const d = tr && W.live.decodes[Number(tr.dataset.i)];
  if (!d) return;
  $("lgCall").value = d.call; $("lgCall").dispatchEvent(new Event("input"));
});
W.poll = async () => {
  try {
    const prev = W.live?.decodes?.length || 0;
    W.live = await G.api("/api/wspr/live");
    W.render();
    if ((W.live.decodes?.length || 0) !== prev && GV.view === "WSPR") GV.apply(false);
  } catch { /* offline */ }
};
$("wsprPop").addEventListener("opened", () => { W.fillForm().catch(() => {}); W.poll(); });
setInterval(() => { if (!$("wsprPop").hidden || W.live?.running) W.poll(); }, 1000);

POP.init("cwDecPop", "tbCwDec");
POP.init("cwSendPop", "tbCwSend");
POP.init("cwOpsPop", "tbCwOps");
// WSPR tab: the panel docked down the left of the expanded globe, the WSPR globe filling
// the rest. MUF: the MUF globe.
$("globeP").appendChild($("wsprPop"));
$("wsprPop").classList.add("docked");
$("wsprPop").addEventListener("opened", () => {
  GV.show("WSPR", true);
  $("globeP").style.setProperty("--dockTop", `${$("globeP").querySelector(".ph").offsetHeight}px`);
  $("globeP").classList.add("wsprDock");
});
$("wsprPop").addEventListener("closed", () => { $("globeP").classList.remove("wsprDock"); GV.show("WSPR", false); });
POP.init("wsprPop", "tbWspr");
$("tbMuf").onclick = () => GV.show("MUF", GV.view !== "MUF");
GV.changed = () => {
  $("tbMuf").classList.toggle("on", GV.view === "MUF");
  if (GV.view !== "WSPR" && !$("wsprPop").hidden) POP.toggle("wsprPop", "tbWspr", false);
};
