"use strict";
/* Audio:
   - LAN: demodulated IQ from /ws/audio (12 kHz PCM), works from any browser on the network.
   - Local USB (only when this browser runs on the PC the radio is plugged into):
       RX: the radio's USB codec input -> speakers (the radio's own DSP, NR, etc.)
       TX: microphone -> the radio's USB codec output, gated open only while PTT is keyed. */

const AU = {
  cfg: Object.assign({ lan: true, usb: false, usbIn: "", tx: false, mic: "", usbOut: "" }, G.store.get("audio", {})),
  ctx: null, out: null, ws: null, next: 0,
  rxStream: null, rxNode: null,
  txStream: null, txGate: null, txEl: null,
};
G.txAudioOn = false;

AU.saveCfg = (c) => { AU.cfg = c; G.store.set("audio", c); AU.apply(); };

AU.ensureCtx = () => {
  if (!AU.ctx) {
    AU.ctx = new (window.AudioContext || window.webkitAudioContext)();
    AU.out = AU.ctx.createGain();
    AU.out.connect(AU.ctx.destination);
    AU.volume();
  }
  if (AU.ctx.state === "suspended") AU.ctx.resume();
  return AU.ctx;
};
AU.volume = () => {
  if (!AU.out) return;
  const v = G.prefs.mute ? 0 : Math.pow(G.prefs.af / 100, 2) * 1.6;
  AU.out.gain.setTargetAtTime(v, AU.ctx.currentTime, 0.03);
};
G.on("prefs", (k) => { if (k === "af" || k === "mute") AU.volume(); });

/* ---------- LAN audio ---------- */
AU.startLan = () => {
  if (AU.ws) return;
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/audio`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    if (!AU.ctx || AU.ctx.state !== "running") return;
    const pcm = new Int16Array(ev.data), n = pcm.length;
    if (!n) return;
    const buf = AU.ctx.createBuffer(1, n, 12000), ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;
    const now = AU.ctx.currentTime;
    if (AU.next < now + 0.04 || AU.next > now + 0.6) AU.next = now + 0.15;   // (re)start or shed lag
    const src = AU.ctx.createBufferSource();
    src.buffer = buf; src.connect(AU.out); src.start(AU.next);
    AU.next += n / 12000;
  };
  ws.onclose = () => { AU.ws = null; if (AU.cfg.lan) setTimeout(AU.startLan, 2000); };
  AU.ws = ws;
};
AU.stopLan = () => { if (AU.ws) { const w = AU.ws; AU.ws = null; w.onclose = null; w.close(); } };

/* ---------- local USB audio ---------- */
const RAW = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
AU.startUsbRx = async () => {
  AU.stopUsbRx();
  const ctx = AU.ensureCtx();
  AU.rxStream = await navigator.mediaDevices.getUserMedia({ audio: { ...RAW, deviceId: AU.cfg.usbIn ? { exact: AU.cfg.usbIn } : undefined } });
  AU.rxNode = ctx.createMediaStreamSource(AU.rxStream);
  AU.rxNode.connect(AU.out);
};
AU.stopUsbRx = () => {
  if (AU.rxNode) AU.rxNode.disconnect();
  if (AU.rxStream) AU.rxStream.getTracks().forEach((t) => t.stop());
  AU.rxNode = AU.rxStream = null;
};
AU.startTx = async () => {
  AU.stopTx();
  const ctx = AU.ensureCtx();
  AU.txStream = await navigator.mediaDevices.getUserMedia({ audio: { ...RAW, deviceId: AU.cfg.mic ? { exact: AU.cfg.mic } : undefined } });
  const src = ctx.createMediaStreamSource(AU.txStream);
  AU.txGate = ctx.createGain(); AU.txGate.gain.value = 0;
  const dest = ctx.createMediaStreamDestination();
  src.connect(AU.txGate); AU.txGate.connect(dest);
  AU.txEl = new Audio(); AU.txEl.srcObject = dest.stream;
  if (AU.cfg.usbOut && AU.txEl.setSinkId) await AU.txEl.setSinkId(AU.cfg.usbOut);
  await AU.txEl.play();
  G.txAudioOn = true;
};
AU.stopTx = () => {
  if (AU.txEl) { AU.txEl.pause(); AU.txEl.srcObject = null; }
  if (AU.txStream) AU.txStream.getTracks().forEach((t) => t.stop());
  AU.txEl = AU.txStream = AU.txGate = null;
  G.txAudioOn = false;
};
/* open the mic gate only while the radio is actually keyed */
G.on("status", (s) => {
  // shut while a recorded voice message is on air (rec.js), so the live mic is not mixed in
  if (AU.txGate) AU.txGate.gain.setTargetAtTime(s.rig.ptt && !G.voiceTx ? 1 : 0, AU.ctx.currentTime, 0.01);
});

AU.apply = async () => {
  try {
    if (AU.cfg.lan) AU.startLan(); else AU.stopLan();
    if (AU.cfg.usb) await AU.startUsbRx(); else AU.stopUsbRx();
    if (AU.cfg.tx) await AU.startTx(); else AU.stopTx();
  } catch (e) {
    G.toast(`Audio: ${e.message}`, true);
  }
};

AU.devices = async (askPermission) => {
  if (!navigator.mediaDevices) return { inputs: [], outputs: [] };
  if (askPermission) {
    try { (await navigator.mediaDevices.getUserMedia({ audio: true })).getTracks().forEach((t) => t.stop()); } catch { /* denied */ }
  }
  const all = await navigator.mediaDevices.enumerateDevices();
  return { inputs: all.filter((d) => d.kind === "audioinput"), outputs: all.filter((d) => d.kind === "audiooutput") };
};

/* Browsers only start audio after a click or key press. */
const unlock = () => { AU.ensureCtx(); AU.apply(); document.removeEventListener("pointerdown", unlock); document.removeEventListener("keydown", unlock); };
document.addEventListener("pointerdown", unlock);
document.addEventListener("keydown", unlock);
