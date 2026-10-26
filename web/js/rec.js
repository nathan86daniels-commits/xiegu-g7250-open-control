"use strict";
/* REC tab, from the Tools button in the left panel:
   - CQ 1-3 voice messages: recorded from the microphone, kept in this browser, heard locally, or played on air
     (PTT keyed, the message into the radio's USB audio, the same route as the PC microphone);
   - receive audio: records what you hear (LAN or USB audio) to a file you can play back or save. */

const REC = { cq: [0, 1, 2].map((i) => G.store.get(`recCq${i}`, null)), rec: null, recSlot: null, recT0: 0, tx: null, rx: null, rxItems: [] };
const REC_MAX_S = 30;
G.voiceTx = false;                         // audio.js keeps the live mic gate shut while a message is on air

REC.mime = () => ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((m) => window.MediaRecorder?.isTypeSupported?.(m)) || "";
REC.dataUrl = (blob) => new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(blob); });

REC.render = () => {
  const rows = $("recRows");
  rows.innerHTML = "";
  REC.cq.forEach((m, i) => {
    const row = document.createElement("div"), recOn = REC.recSlot === i, txOn = REC.tx?.slot === i;
    row.className = "recRow";
    row.innerHTML = `<b>CQ ${i + 1}</b>`
      + `<button class="btn rec${recOn ? " on" : ""}" title="Record from the microphone (up to ${REC_MAX_S} s)">${recOn ? "■ STOP" : "● REC"}</button>`
      + `<button class="btn" title="Listen on this PC"${m ? "" : " disabled"}>▶ HEAR</button>`
      + `<button class="btn tx${txOn ? " on" : ""}" title="Key PTT and play it into the radio"${m ? "" : " disabled"}>${txOn ? "■ STOP" : "ON AIR"}</button>`
      + `<span class="len">${recOn ? "recording…" : m ? `${m.secs.toFixed(1)} s` : "empty"}</span>`
      + `<button class="btn" title="Delete"${m && !txOn ? "" : " disabled"}>✕</button>`;
    const [, rec, hear, tx, , del] = row.children;
    rec.onclick = () => REC.record(i);
    hear.onclick = () => new Audio(m.url).play().catch((e) => G.toast(e.message, true));
    tx.onclick = () => (REC.tx ? REC.stopTx() : REC.onAir(i));
    del.onclick = () => { REC.cq[i] = null; G.store.set(`recCq${i}`, null); REC.render(); };
    if (REC.rec && !recOn) rec.disabled = true;
    rows.appendChild(row);
  });
  const rx = !!REC.rx;
  $("recRxBtn").classList.toggle("on", rx);
  $("recRxBtn").textContent = rx ? "■ STOP" : "● REC RX";
  $("recList").innerHTML = "";
  REC.rxItems.forEach((it) => {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML = `<span class="mono">${esc(it.time)} · ${it.secs.toFixed(0)} s</span><audio controls src="${it.url}"></audio><a class="tag mini" href="${it.url}" download="${esc(it.name)}">⤓ SAVE</a>`;
    $("recList").appendChild(d);
  });
  REC.status();
};
REC.status = () => {
  const busy = REC.rec || REC.tx || REC.rx;
  $("recDot").className = "dot" + (REC.tx ? " tx" : busy ? " rx" : "");
  $("recInfo").textContent = REC.rec ? `RECORDING CQ ${REC.recSlot + 1} · ${((performance.now() - REC.recT0) / 1000).toFixed(1)} s`
    : REC.tx ? `ON AIR · CQ ${REC.tx.slot + 1}` : REC.rx ? `RECORDING RX · ${((performance.now() - REC.rx.t0) / 1000).toFixed(0)} s` : "";
};
setInterval(() => { if (REC.rec || REC.rx) REC.status(); }, 200);

/* ---------- voice messages ---------- */
REC.record = async (i) => {
  if (REC.recSlot === i) { REC.rec.stop(); return; }
  if (REC.rec || REC.tx) return;
  if (!window.MediaRecorder) { G.toast("This browser cannot record audio", true); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      deviceId: AU.cfg.mic ? { exact: AU.cfg.mic } : undefined } });
  } catch (e) { G.toast(`Microphone: ${e.message}`, true); return; }
  const mime = REC.mime(), chunks = [], mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mr.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    clearTimeout(REC.limit);
    const secs = (performance.now() - REC.recT0) / 1000;
    REC.rec = null; REC.recSlot = null;
    if (secs > 0.5) {
      REC.cq[i] = { url: await REC.dataUrl(new Blob(chunks, { type: mr.mimeType })), secs };
      try { G.store.set(`recCq${i}`, REC.cq[i]); } catch { G.toast("Kept for this session only: the browser's storage is full", true); }
    }
    REC.render();
  };
  REC.rec = mr; REC.recSlot = i; REC.recT0 = performance.now();
  mr.start(250);
  REC.limit = setTimeout(() => { if (mr.state === "recording") mr.stop(); }, REC_MAX_S * 1000);
  REC.render();
};

/* On air: key PTT, wait a moment for the radio to switch, play the message into the radio's USB audio, unkey at
   the end. Stops on STOP, Esc, PTT released by any other means, or an error. */
REC.onAir = async (i) => {
  const m = REC.cq[i];
  if (!m || REC.rec) return;
  if (!G.catOn()) { G.toast("On air needs USB CAT (for PTT)", true); return; }
  if (!AU.cfg.usbOut) { G.toast("Pick the radio's USB audio out in AUDIO & TX first", true); return; }
  if (/^(CW|DIG)/.test(G.st?.rig.mode || "")) { G.toast("Voice messages are for SSB, AM or FM", true); return; }
  const el = new Audio(m.url);
  try { if (el.setSinkId) await el.setSinkId(AU.cfg.usbOut); } catch (e) { G.toast(`Radio audio out: ${e.message}`, true); return; }
  REC.tx = { slot: i, el, t0: performance.now() };
  G.voiceTx = true;
  PTT.latched = true; PTT.set(true);
  el.onended = REC.stopTx; el.onerror = REC.stopTx;
  setTimeout(() => { if (REC.tx?.el === el) el.play().catch(REC.stopTx); }, 300);
  REC.render();
};
REC.stopTx = () => {
  const t = REC.tx;
  if (!t) return;
  REC.tx = null; G.voiceTx = false;
  t.el.pause();
  PTT.latched = false; PTT.set(false);
  REC.render();
};
G.on("status", (s) => { if (REC.tx && !s.rig.ptt && performance.now() - REC.tx.t0 > 1500) REC.stopTx(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && REC.tx) REC.stopTx(); });

/* ---------- receive audio: what you hear (after the AF level) ---------- */
$("recRxBtn").onclick = () => {
  if (REC.rx) { REC.rx.mr.stop(); return; }
  if (!window.MediaRecorder) { G.toast("This browser cannot record audio", true); return; }
  if (!AU.ctx || !AU.out || !(AU.ws || AU.rxNode)) { G.toast("No receive audio yet: turn on LAN or USB audio in AUDIO & TX", true); return; }
  const dest = AU.ctx.createMediaStreamDestination(), mime = REC.mime(), chunks = [];
  AU.out.connect(dest);
  const mr = new MediaRecorder(dest.stream, mime ? { mimeType: mime } : undefined);
  mr.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mr.onstop = () => {
    try { AU.out.disconnect(dest); } catch { /* already gone */ }
    const secs = (performance.now() - REC.rx.t0) / 1000, d = new Date(), stamp = d.toISOString().replace(/[-:]/g, "").slice(0, 15);
    const ext = (mr.mimeType || "").includes("ogg") ? "ogg" : "webm";
    REC.rxItems.unshift({ url: URL.createObjectURL(new Blob(chunks, { type: mr.mimeType })), secs, time: `${d.toISOString().slice(11, 19)} UTC`, name: `g7250-rx-${stamp}Z.${ext}` });
    REC.rxItems.splice(8).forEach((old) => URL.revokeObjectURL(old.url));
    REC.rx = null;
    REC.render();
  };
  REC.rx = { mr, t0: performance.now() };
  mr.start(500);
  REC.render();
};

POP.init("recPop", "tbRec");
REC.render();
