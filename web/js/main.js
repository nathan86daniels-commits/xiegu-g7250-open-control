"use strict";
/* Wires every control to the radio and runs the render loop. */

const R = () => (G.st ? G.st.rig : {});
const catHas = (f) => () => G.has(f);

/* ================= toolbar ================= */
G.seg($("tbMode"), [["LSB", "LSB"], ["USB", "USB"], ["CW", "CW"], ["AM", "AM"], ["FM", "FM"], ["DIG", "DIG"]], {
  get: () => R().mode,
  pick: (v) => { G.control("mode", v); $("lgS").value = $("lgR").value = v === "CW" ? "599" : v === "DIG" ? "-10" : "59"; },
});

const FILTERS = [[3600, "3.6k"], [2900, "2.9k"], [2400, "2.4k"], [1800, "1.8k"], [500, "500"]];
const curWidth = () => { const r = R(); return r.mode === "AM" ? r.high * 2 : (r.high || 0) - (r.low || 0); };
G.seg($("tbFilter"), FILTERS, {
  get: () => { const w = curWidth(); return FILTERS.reduce((a, b) => (Math.abs(b[0] - w) < Math.abs(a[0] - w) ? b : a))[0]; },
  pick: (w) => {
    const m = R().mode;
    let pb;
    if (m === "CW" || m === "CW-R") pb = [0, w];
    else if (m === "AM") pb = [0, w / 2];
    else if (m === "FM") return;
    else pb = w >= 1800 ? [100, 100 + w] : [1500 - w / 2, 1500 + w / 2];
    G.control("passband", pb);
    if (G.catOn()) G.control("filter", w >= 2900 ? 1 : w >= 2400 ? 2 : 3);
  },
});
G.seg($("tbStep"), [[10, "10"], [100, "100"], [1000, "1k"], [5000, "5k"]], { get: () => G.prefs.step, pick: (v) => G.setPref("step", v) });
const AGC = [["OFF", "OFF"], ["S", "S"], ["M", "M"], ["F", "F"]];
G.seg($("tbAgc"), AGC, { get: () => R().agc, pick: (v) => G.control("agc", v), enabled: catHas("agc") });
G.seg($("tbFront"), [["pre", "PRE"], ["att", "ATT"]], {
  multi: true, enabled: G.catOn,
  get: () => [R().preamp ? "pre" : null, R().att ? "att" : null],
  pick: (v) => (v === "pre" ? G.control("preamp", R().preamp ? 0 : 1) : G.control("att", !R().att)),
});
G.seg($("tbDsp"), [["nr", "NR"], ["nb", "NB"], ["anf", "NF"]], {
  multi: true, enabled: G.catOn,
  get: () => ["nr", "nb", "anf"].filter((k) => R()[k]),
  pick: (v) => G.control(v, !R()[v]),
});
G.slider($("afS"), { min: 0, max: 100, get: () => G.prefs.af, change: (v) => G.setPref("af", v), show: (v) => ($("afV").textContent = v) });
G.slider($("sqlS"), {
  min: 0, max: 100, get: () => G.prefs.sql, show: (v) => ($("sqlV").textContent = v),
  change: (v) => { G.setPref("sql", v); G.control("squelch", v === 0 ? null : -140 + v * 1.1); },
});
const watts = (v) => Math.round(((v ?? 0) / 255) * 100);
G.slider($("pwrS"), { min: 0, max: 255, get: () => R().power, change: (v) => G.control("power", v), show: (v) => ($("pwrV").textContent = `${watts(v)} W`), enabled: catHas("power") });

/* connection tag + button */
const openCat = () => CAT.open();
$("connTag").onclick = openCat; $("railCat").onclick = openCat;
$("connBtn").onclick = () => (G.catOn() ? G.api("/api/cat/disconnect", {}).then(() => G.toast("CAT disconnected")) : openCat());
G.on("status", (s) => {
  const tag = $("connTag");
  tag.classList.toggle("on", s.cat.connected && !s.simulator);
  tag.classList.toggle("sim", s.cat.connected && s.simulator);
  $("connInfo").textContent = s.cat.connected ? `${s.cat.transport} · ${s.cat.address}` : `LAN only · ${s.lan.radio.split(":")[0]}`;
  $("connBtn").textContent = s.cat.connected ? "Disconnect" : "Connect";
});

/* ================= bands ================= */
const bandStack = G.store.get("bandStack", {});
G.BANDS.concat([{ b: "GEN" }]).forEach((band) => {
  const btn = document.createElement("button");
  btn.className = "btn";
  // labelled in MHz (the metre band is in the tooltip)
  btn.textContent = band.b === "GEN" ? "GEN" : String(+(band.lo / 1e6).toFixed(1));
  btn.title = band.b === "GEN" ? "General coverage: type a frequency" : `${band.b} m`;
  btn.onclick = () => {
    if (band.b === "GEN") { promptFreq(); return; }
    G.tune(bandStack[band.b] || band.def);
  };
  btn.dataset.band = band.b;
  $("bands").appendChild(btn);
});
let stackT = 0;
G.on("status", (s) => {
  const band = G.bandOf(s.rig.freq);
  document.querySelectorAll("#bands .btn").forEach((b) => b.classList.toggle("on", !!band && b.dataset.band === band.b));
  if (band && performance.now() - stackT > 2000) { stackT = performance.now(); bandStack[band.b] = s.rig.freq; G.store.set("bandStack", bandStack); }
});
function promptFreq() {
  const v = prompt("Frequency in MHz (0.1 – 54)", G.st ? (G.st.rig.freq / 1e6).toFixed(4) : "");
  const mhz = parseFloat((v || "").replace(",", "."));
  if (Number.isFinite(mhz)) G.tune(mhz * 1e6);
}

/* ================= front end ================= */
G.seg($("feAnt"), [[1, "ANT 1"], [2, "ANT 2"]], { get: () => R().antenna, pick: (v) => G.control("antenna", v), enabled: catHas("antenna") });
G.seg($("fePre"), [[0, "IPO"], [1, "PRE"]], { get: () => (R().preamp ? 1 : 0), pick: (v) => G.control("preamp", v), enabled: catHas("preamp") });
G.seg($("feAtt"), [[false, "Off"], [true, "On"]], { get: () => !!R().att, pick: (v) => G.control("att", v), enabled: G.catOn });
G.slider($("rfS"), { min: 0, max: 255, get: () => R().rf, change: (v) => G.control("rf", v), show: (v) => ($("rfV").textContent = Math.round((v / 255) * 100)), enabled: catHas("rf") });
G.seg($("feAtu"), [[true, "ON"], [false, "OFF"]], { get: () => !!R().atu, pick: (v) => G.control("atu", v), enabled: G.catOn });

/* ================= RX DSP ================= */
const togWidget = (id, field, label) => {
  $(id).onclick = () => G.control(field, !R()[field]);
  G.widgets.push({ update() { const on = !!R()[field]; $(id).classList.toggle("on", on); $(id).textContent = label; } });
};
togWidget("nrTog", "nr", "NR"); togWidget("nbTog", "nb", "NB"); togWidget("nfTog", "anf", "NF"); togWidget("compTog", "comp", "COMP"); togWidget("txCompTog", "comp", "COMP");
G.slider($("nrS"), { min: 0, max: 255, get: () => R().nr_level, change: (v) => G.control("nr_level", v), show: (v) => ($("nrV").textContent = Math.round((v / 255) * 15)), enabled: catHas("nr_level"), off: () => !R().nr });
G.slider($("nbS"), { min: 0, max: 255, get: () => R().nb_level, change: (v) => G.control("nb_level", v), show: (v) => ($("nbV").textContent = Math.round((v / 255) * 10)), enabled: catHas("nb_level"), off: () => !R().nb });
G.seg($("nfSg"), [[true, "AUTO"], [false, "OFF"]], { get: () => !!R().anf, pick: (v) => G.control("anf", v), enabled: catHas("anf") });
G.on("status", (s) => {
  $("nfV").textContent = s.rig.anf ? "AUTO" : "OFF";
  $("dspFil").textContent = `FIL ${s.rig.filter || "—"}`;
});

/* RX BW and SHIFT (the RX DSP sliders). Both set the demodulator passband, as the FILTER buttons do.
   SHIFT moves the passband from where that width normally sits (100 Hz up for wide SSB, centred on 1500 Hz when
   narrow); CW, AM and FM are centred, so SHIFT is for SSB and DIG only. */
const DSP = {
  home: (w) => (w >= 1800 ? 100 + w / 2 : 1500),
  range: () => { const m = R().mode; return m === "CW" || m === "CW-R" ? [50, 2000] : m === "AM" ? [2000, 12000] : [200, 4000]; },
  shiftable: () => !/^(CW|AM|FM)/.test(R().mode || ""),
  timer: null,
  set(width, shift) {
    const r = R(), m = r.mode;
    let low, high;
    if (m === "CW" || m === "CW-R") { low = 0; high = width; }
    else if (m === "AM") { low = 0; high = width / 2; }
    else {
      high = Math.min(6000, Math.round((DSP.home(width) + shift + width / 2) / 10) * 10);
      low = Math.max(0, high - width); high = low + width;
    }
    // show it at once; the radio follows a moment later
    r.low = low; r.high = high;
    r.passband = (m === "LSB" || m === "RTTY-R") ? [-high, -low] : (m === "CW" || m === "CW-R") ? [-(high - low) / 2, (high - low) / 2] : m === "AM" ? [-high, high] : [low, high];
    scope.dirty = true;
    clearTimeout(DSP.timer);
    DSP.timer = setTimeout(() => G.control("passband", [low, high]), 60);
  },
};
G.slider($("shiftS"), {
  min: -1000, max: 1000,
  get: () => (G.st && DSP.shiftable() ? Math.round(scope.filterShape().shift) : 0),
  change: (v) => DSP.set(scope.filterShape().width, Math.round(v / 10) * 10),
  show: (v) => ($("shiftV").textContent = G.st && DSP.shiftable() ? `${v > 0 ? "+" : ""}${Math.round(v / 10) * 10}` : "—"),
  enabled: () => !!G.st && DSP.shiftable(),
});

/* ================= memories ================= */
let memories = [];
const renderMems = () => {
  $("mems").innerHTML = "";
  memories.forEach((m, i) => {
    const b = document.createElement("button");
    b.className = "btn";
    b.title = "Shift-click to store the current frequency";
    b.textContent = `M${i + 1}`;
    b.classList.toggle("stored", !!m);
    if (m) b.title = `${G.fmtMHz(m.freq).slice(0, -2)} ${m.mode} · shift-click to store over it`;
    b.onclick = async (e) => {
      if (e.shiftKey && G.st) memories = await G.api(`/api/memories/${i}`, { freq: G.st.rig.freq, mode: G.st.rig.mode });
      else if (e.altKey) memories = await G.api(`/api/memories/${i}`, null);
      else if (m) { if (m.mode !== R().mode) G.control("mode", m.mode); G.tune(m.freq); return; }
      else { G.toast("Shift-click to store the current frequency here"); return; }
      renderMems();
    };
    $("mems").appendChild(b);
  });
};
G.api("/api/memories").then((m) => { memories = m; renderMems(); }).catch(() => {});

/* ================= VFO ================= */
const renderFreq = () => {
  const r = R(), el = $("freq");
  if (!r.freq) { el.textContent = "—"; return; }
  const s = String(r.freq).padStart(7, "0"), mhz = s.slice(0, -6), khz = s.slice(-6, -3), hz = s.slice(-3);
  const parts = [], step = G.prefs.step;
  const digits = (str, first, cls) => [...str].forEach((d, i) => {
    const place = 10 ** (first - i);
    parts.push(`<span data-place="${place}" class="${cls}${place === step ? " stepDigit" : ""}">${d}</span>`);
  });
  digits(mhz, mhz.length + 5, ""); parts.push('<span class="sep">.</span>'); digits(khz, 5, ""); parts.push('<span class="sep">.</span>'); digits(hz, 2, "lo");
  el.innerHTML = parts.join("");
};
$("freq").addEventListener("wheel", (e) => {
  const place = Number(e.target.dataset?.place);
  if (!place || !G.st) return;
  e.preventDefault();
  G.tune(G.st.rig.freq + (e.deltaY < 0 ? place : -place));
}, { passive: false });
$("freq").addEventListener("click", (e) => {
  const place = Number(e.target.dataset?.place);
  if ([10, 100, 1000, 5000].includes(place)) G.setPref("step", place);
});
$("freq").addEventListener("dblclick", promptFreq);
const nudgeFreq = (direction) => {
  if (!G.st) return;
  G.tune(G.st.rig.freq + direction * G.prefs.step);
};
const roundFreq = () => {
  if (!G.st) return;
  const step = G.prefs.step;
  G.tune(Math.round(G.st.rig.freq / step) * step);
};
$("freqDn").onclick = () => nudgeFreq(-1);
$("freqRound").onclick = roundFreq;
$("freqUp").onclick = () => nudgeFreq(1);
G.on("tuned", renderFreq);
G.on("prefs", renderFreq);
G.on("status", (s) => {
  const r = s.rig, band = G.bandOf(r.freq);
  renderFreq();
  $("vfoTag").textContent = `VFO-${r.vfo || "A"}`;
  $("rxtxTag").textContent = r.ptt ? "TX" : "RX";
  $("rxtxTag").classList.toggle("tx", !!r.ptt);
  $("modeTag").textContent = r.mode;
  $("bandTag").textContent = band ? `${band.b} m` : "GEN";
  $("rxBw").textContent = `${(curWidth() / 1000).toFixed(1)}k`;
  $("agcTag").textContent = r.agc || "—";
  $("stepTag").textContent = G.prefs.step >= 1000 ? `${G.prefs.step / 1000} kHz` : `${G.prefs.step} Hz`;
  $("ritTag").textContent = r.rit ? `${r.rit_hz >= 0 ? "+" : ""}${r.rit_hz}` : "OFF";
  $("xitTag").textContent = r.xit ? `${r.rit_hz >= 0 ? "+" : ""}${r.rit_hz}` : "OFF";
  $("iqTag").textContent = s.lan.receiving ? `48k · ${Math.round(s.lan.packets_per_second)}/s` : "NO IQ";
  // VFO B
  const other = r.vfo === "B" ? "A" : "B";
  $("vfoBTag").textContent = `VFO-${other}`;
  { const t = r.freq_other ? G.fmtMHz(r.freq_other) : "—", k = t.lastIndexOf("."); $("freqB").innerHTML = k < 0 ? t : t.slice(0, k).replace(/\./g, '<span class="sep">.</span>') + '<span class="sep">.</span><span class="lo">' + t.slice(k + 1) + "</span>"; }
  $("splitTag").textContent = r.split ? "SPLIT ON" : "SPLIT OFF";
  $("splitTag").classList.toggle("dim", !r.split);
  $("splitBtn").classList.toggle("on", !!r.split);
  $("vfoBInfo").textContent = `${r.mode} · RX BW ${(curWidth() / 1000).toFixed(1)}k`;
  // RIT / XIT row under the VFOs (RIT, XIT, −, offset, +, CLR)
  $("ritBtn").classList.toggle("on", !!r.rit); $("xitBtn").classList.toggle("on", !!r.xit);
  $("lockBtn").classList.toggle("on", !!r.lock); $("muteBtn").classList.toggle("on", !!G.prefs.mute);
  $("ritVal").textContent = `${(r.rit_hz || 0) >= 0 ? "" : "−"}${(Math.abs(r.rit_hz || 0) / 1000).toFixed(2)} kHz`;
});
$("ritTag").parentElement.onclick = () => G.control("rit", !R().rit);
$("ritTag").parentElement.addEventListener("wheel", (e) => { e.preventDefault(); G.control("rit_hz", (R().rit_hz || 0) + (e.deltaY < 0 ? 10 : -10)); }, { passive: false });
$("xitTag").parentElement.onclick = () => G.control("xit", !R().xit);
$("ritBtn").onclick = () => G.control("rit", !R().rit);
$("xitBtn").onclick = () => G.control("xit", !R().xit);
$("ritDn").onclick = () => G.control("rit_hz", (R().rit_hz || 0) - 10);
$("ritUp").onclick = () => G.control("rit_hz", (R().rit_hz || 0) + 10);
$("ritClr").onclick = () => G.control("rit_hz", 0);
$("ritVal").addEventListener("wheel", (e) => { e.preventDefault(); G.control("rit_hz", (R().rit_hz || 0) + (e.deltaY < 0 ? 10 : -10)); }, { passive: false });
$("lockBtn").onclick = () => G.control("lock", !R().lock);
$("muteBtn").onclick = () => { G.setPref("mute", !G.prefs.mute); $("muteBtn").classList.toggle("on", !!G.prefs.mute); };
$("abBtn").onclick = () => G.control("swap");
$("aeqbBtn").onclick = () => G.control("equal");
$("splitBtn").onclick = () => G.control("split", !R().split);
$("freqB").addEventListener("wheel", (e) => {
  if (!R().freq_other) return;
  e.preventDefault();
  G.control("freq_other", R().freq_other + (e.deltaY < 0 ? G.prefs.step : -G.prefs.step));
}, { passive: false });

/* ================= TX chain ================= */
// MIC and COMP sit in the right column and again in AUDIO & TX; both pairs drive the same radio settings
[["micS", "micV"], ["txMicS", "txMicV"]].forEach(([s, v]) =>
  G.slider($(s), { min: 0, max: 255, get: () => R().mic, change: (x) => G.control("mic", x), show: (x) => ($(v).textContent = Math.round((x / 255) * 100)), enabled: catHas("mic") }));
[["compS", "compV"], ["txCompS", "txCompV"]].forEach(([s, v]) =>
  G.slider($(s), { min: 0, max: 255, get: () => R().comp_level, change: (x) => G.control("comp_level", x), show: (x) => ($(v).textContent = `${Math.round((x / 255) * 10)}`), enabled: catHas("comp_level"), off: () => !R().comp }));
G.seg($("voxSg"), [[true, "ON"], [false, "OFF"]], { get: () => !!R().vox, pick: (v) => G.control("vox", v), enabled: catHas("vox") });
G.on("status", (s) => {
  $("voxSmall").textContent = s.rig.vox ? "ON" : "OFF";
  $("voxBtn").classList.toggle("on", !!s.rig.vox);
  $("pttBtn").classList.toggle("on", !!s.rig.ptt);
  if (s.ptt_release_reason && s.ptt_release_reason !== PTT.lastReason) {
    PTT.lastReason = s.ptt_release_reason;
    if (/lost|maximum|failed|disconnect/.test(s.ptt_release_reason)) G.toast(`PTT released: ${s.ptt_release_reason}`, true);
  }
});
$("tuneBtn").onclick = () => G.control("tune").then(() => { G.toast("ATU tuning"); PTT.tuneUntil = performance.now() + 5000; });
$("voxBtn").onclick = () => G.control("vox", !R().vox);
$("cwText").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !e.target.value.trim()) return;
  G.control("cw", e.target.value.trim()).then(() => (e.target.value = ""));
});

/* PTT: held = keyed, with a heartbeat so a stalled browser can never leave the radio keyed */
const PTT = { held: false, beat: null, latched: false, lastReason: null, tuneUntil: 0 };
PTT.set = (on) => {
  if (on === PTT.held) return;
  if (on && !G.catOn()) { G.toast("PTT needs USB CAT", true); return; }
  PTT.held = on;
  G.send({ type: "ptt", on });
  clearInterval(PTT.beat);
  if (on) PTT.beat = setInterval(() => G.send({ type: "beat" }), 150);
};
const ptt = $("pttBtn");
ptt.addEventListener("pointerdown", (e) => { ptt.setPointerCapture(e.pointerId); PTT.latched = false; PTT.set(true); });
ptt.addEventListener("pointerup", () => PTT.set(false));
ptt.addEventListener("pointercancel", () => PTT.set(false));
window.addEventListener("blur", () => { if (!PTT.latched) PTT.set(false); });
const typing = () => /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.querySelector("dialog[open]");
document.addEventListener("keydown", (e) => {
  if (typing()) return;
  if (e.code === "Space") { e.preventDefault(); if (!e.repeat) { PTT.latched = false; PTT.set(true); } }
  else if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); if (G.st) G.tune(Math.round(G.st.rig.freq / G.prefs.step) * G.prefs.step + (e.key === "ArrowUp" ? G.prefs.step : -G.prefs.step)); }
});
document.addEventListener("keyup", (e) => { if (e.code === "Space" && !typing()) { e.preventDefault(); PTT.set(false); } });

let settings = null;
const renderCq = () => {
  $("cqBtns").innerHTML = "";
  (settings?.cq || []).slice(0, 3).forEach((text, i) => {
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = `CQ${i + 1}`; b.title = text;
    b.onclick = () => G.control("cw", text);
    $("cqBtns").appendChild(b);
  });
  G.refresh();
};
G.widgets.push({ update() { const cw = G.catOn() && /^CW/.test(R().mode || ""); document.querySelectorAll("#cqBtns .btn, #cwText").forEach((b) => { b.disabled = !cw; b.title = cw ? b.title : "CW mode + USB CAT"; }); } });

/* ================= status bar ================= */
G.seg($("sbFlags"), [["mox", "MOX"], ["vox", "VOX"], ["tune", "TUNE"], ["split", "SPLIT"], ["rit", "RIT"], ["xit", "XIT"], ["lock", "LOCK"], ["mute", "MUTE"]], {
  multi: true,
  get: () => {
    const r = R(), on = [];
    if (r.ptt) on.push("mox"); if (r.vox) on.push("vox"); if (performance.now() < PTT.tuneUntil) on.push("tune");
    if (r.split) on.push("split"); if (r.rit) on.push("rit"); if (r.xit) on.push("xit"); if (r.lock) on.push("lock");
    if (G.prefs.mute) on.push("mute");
    return on;
  },
  pick: (v) => {
    const r = R();
    if (v === "mute") return G.setPref("mute", !G.prefs.mute);
    if (v === "mox") { if (PTT.held) { PTT.latched = false; PTT.set(false); } else { PTT.set(true); PTT.latched = true; } return; }
    if (v === "tune") return $("tuneBtn").click();
    if (v === "split") return G.control("split", !r.split);
    G.control(v, !r[v]);
  },
});
$("sbFlags").classList.add("flat");
G.on("status", (s) => {
  $("simNote").hidden = !s.simulator;
  $("sbGrid").textContent = s.grid || "—";
  $("sbVdd").textContent = s.rig.vd_raw != null ? `${M.vdV(s.rig.vd_raw).toFixed(1)} V` : "—";
  $("sbHamlib").textContent = s.rigctl.running ? `:${s.rigctl.port}${s.rigctl.clients ? " · " + s.rigctl.clients : ""}` : "OFF";
  if (s.callsign !== LOG.lastCall || s.grid !== LOG.lastGrid) { LOG.lastCall = s.callsign; LOG.lastGrid = s.grid; LOG.drawGlobe(); }
});
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
setInterval(() => {
  const d = new Date();
  $("sbClock").textContent = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} · ${d.toISOString().slice(11, 19)} UTC`;
}, 500);

/* ================= rail ================= */
if ($("railLog")) $("railLog").onclick = () => $("logSearch").focus();
$("railFull").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen());
// the square shows two squares while full screen, as a "restore" button does
document.addEventListener("fullscreenchange", () => {
  $("railFull").innerHTML = document.fullscreenElement
    ? '<svg viewBox="0 0 16 16"><rect x="2.5" y="5.5" width="8" height="8" rx="1"/><path d="M5.5 5.5V2.5h8v8h-3"/></svg>'
    : '<svg viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="11" rx="1"/></svg>';
  $("railFull").setAttribute("aria-label", document.fullscreenElement ? "Leave full screen" : "Full screen");
});
$("railSettings").onclick = () => SET.open();

/* ================= CAT dialog ================= */
const CAT = {
  async open() {
    $("catDlg").showModal();
    $("catMsg").textContent = G.st?.cat.error || (G.st?.cat.connected ? `Connected: ${G.st.cat.transport} at ${G.st.cat.address}` : "Not connected");
    try {
      const s = await G.api("/api/settings");
      $("catBaud").value = String(s.cat.baud);
      $("catAddr").value = s.cat.address || "auto";
      await CAT.ports(s.cat.port);
    } catch (e) { $("catMsg").textContent = e.message; }
  },
  async ports(selected) {
    const ports = await G.api("/api/cat/ports");
    $("catPort").innerHTML = ports.length
      ? ports.map((p) => `<option value="${esc(p.device)}"${p.device === selected ? " selected" : ""}>${esc(p.device)} — ${esc(p.description)}${p.vid ? ` (${p.vid}:${p.pid})` : ""}</option>`).join("")
      : '<option value="">No serial ports found</option>';
  },
};
$("catRefresh").onclick = () => CAT.ports($("catPort").value);
$("catConnect").onclick = async () => {
  $("catMsg").textContent = "Connecting…";
  try {
    const r = await G.api("/api/cat/connect", { port: $("catPort").value, baud: Number($("catBaud").value), address: $("catAddr").value });
    $("catMsg").textContent = `Connected: ${r.transport} at ${r.address}`;
    G.toast("CAT connected");
  } catch (e) { $("catMsg").textContent = e.message; }
};
$("catDisconnect").onclick = () => G.api("/api/cat/disconnect", {}).then(() => ($("catMsg").textContent = "Disconnected"));

/* ================= settings dialog ================= */
const SET = {
  async open() {
    try { settings = await G.api("/api/settings"); } catch (e) { G.toast(e.message, true); return; }
    $("sCall").value = settings.callsign; $("sGrid").value = settings.grid; $("sMaxTx").value = settings.tx.max_seconds;
    $("sClOn").checked = settings.cluster.enabled; $("sClHost").value = settings.cluster.host; $("sClPort").value = settings.cluster.port;
    $("sClStatus").textContent = G.st?.cluster || "";
    [0, 1, 2].forEach((i) => ($(`sCq${i}`).value = settings.cq[i] || ""));
    $("sQrzUser").value = settings.qrz.username || ""; $("sQrzPass").value = settings.qrz.password || "";
    $("sQrzKey").value = settings.qrz.logbook_key || ""; $("sQrzAuto").checked = !!settings.qrz.auto_upload;
    $("sHqUser").value = settings.hamqth.username || ""; $("sHqPass").value = settings.hamqth.password || "";
    $("hamlibNote").textContent = G.st?.rigctl.running
      ? `Hamlib NET rigctl is on 127.0.0.1:${G.st.rigctl.port}. WSJT-X: Rig "Hamlib NET rigctl", PTT "CAT". LOG4OM: Hamlib, "Connect to active HAMLIB instance".`
      : "Hamlib server is off (port busy?).";
    $("setDlg").showModal();
  },
};
$("setForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  try {
    settings = await G.api("/api/settings", {
      callsign: $("sCall").value.trim().toUpperCase(), grid: $("sGrid").value.trim(),
      tx: { max_seconds: Number($("sMaxTx").value) || 180 },
      cluster: { enabled: $("sClOn").checked, host: $("sClHost").value.trim(), port: Number($("sClPort").value) || 7373 },
      cq: [0, 1, 2].map((i) => $(`sCq${i}`).value),
      qrz: { username: $("sQrzUser").value.trim(), password: $("sQrzPass").value, logbook_key: $("sQrzKey").value.trim(), auto_upload: $("sQrzAuto").checked },
      hamqth: { username: $("sHqUser").value.trim(), password: $("sHqPass").value },
    });
    renderCq(); LOG.load(); LOG.stats();
    $("setDlg").close();
    G.toast("Settings saved");
  } catch (err) { G.toast(err.message, true); }
});

/* ================= AUDIO & TX: sound devices, saved as soon as they change (no Save button) ================= */
const AUD = {
  async devices(ask) {
    const { inputs, outputs } = await AU.devices(ask);
    const opts = (list, sel) => '<option value="">Default</option>' + list.map((d) => `<option value="${esc(d.deviceId)}"${d.deviceId === sel ? " selected" : ""}>${esc(d.label || "Device " + d.deviceId.slice(0, 6))}</option>`).join("");
    $("aUsbIn").innerHTML = opts(inputs, AU.cfg.usbIn); $("aMic").innerHTML = opts(inputs, AU.cfg.mic); $("aUsbOut").innerHTML = opts(outputs, AU.cfg.usbOut);
    $("aNote").textContent = inputs.some((d) => d.label) ? "Pick the radio's USB codec for 'in' and 'out'. Changes apply at once." : "Tick a local-audio box to let the browser list device names.";
  },
  save() { AU.saveCfg({ lan: $("aLan").checked, usb: $("aUsb").checked, usbIn: $("aUsbIn").value, tx: $("aTx").checked, mic: $("aMic").value, usbOut: $("aUsbOut").value }); },
};
$("aLan").checked = AU.cfg.lan; $("aUsb").checked = AU.cfg.usb; $("aTx").checked = AU.cfg.tx;
["aLan", "aUsb", "aTx"].forEach((id) => $(id).addEventListener("change", async (e) => {
  if (e.target.checked && id !== "aLan") await AUD.devices(true);
  AUD.save();
}));
["aUsbIn", "aMic", "aUsbOut"].forEach((id) => $(id).addEventListener("change", AUD.save));
navigator.mediaDevices?.addEventListener("devicechange", () => AUD.devices(false));
AUD.devices(false);

/* ================= meter face (DISPLAY > Meters, Analog dial or Bars) ================= */
const METER = { face: G.store.get("meterFace", "dial") };
G.seg($("meterFace"), [["dial", "Analog dial"], ["bars", "Bars"]], {
  get: () => METER.face,
  pick: (v) => { METER.face = v; G.store.set("meterFace", v); METER.apply(); },
});
METER.apply = () => { $("dial").hidden = METER.face !== "dial"; $("meterBars").hidden = METER.face !== "bars"; M.dialBg = null; };
METER.apply();

/* ================= render loop ================= */
let last = performance.now(), uiFrames = 0;
const loop = (now) => {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now; uiFrames++;
  M.tick(dt);
  if (METER.face === "bars") M.drawBarsOn($("meterBars")); else M.drawDial();
  scope.frame();
  requestAnimationFrame(loop);
};
setInterval(() => { $("sbScope").textContent = G.specFrames; G.specFrames = 0; $("sbUi").textContent = uiFrames; uiFrames = 0; }, 1000);
window.addEventListener("resize", () => { scope.dirty = true; scope.lastLo = null; LOG.drawGlobe(); M.dialBg = null; });

/* ================= start ================= */
G.connect();
G.api("/api/settings").then((s) => { settings = s; renderCq(); }).catch(() => {});
LOG.load(); LOG.stats(); LOG.loadProp(); LOG.loadSpots();
setInterval(LOG.loadSpots, 5000);
setInterval(LOG.loadProp, 300000);
G.on("status", () => LOG.renderSpots(G.st.cluster));
requestAnimationFrame(loop);
