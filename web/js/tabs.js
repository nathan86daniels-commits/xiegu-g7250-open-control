"use strict";
/* The bottom tab panel. DISPLAY, LOG, DX, MUF / SFI, WSPR, REC, CW and AUDIO & TX each show one pane;
   LOG shares its row with the globe, and MUF / SFI and WSPR (and an expanded globe) take the whole window above
   the tabs. The CW, REC and WSPR panels are the old floating panels, docked into their tab and
   still opened and closed through POP.toggle so their polling and settings load as before. */

const TABS = [["display", "DISPLAY"], ["log", "LOG"], ["dx", "DX"], ["muf", "MUF / SFI"], ["wspr", "WSPR"], ["rec", "REC"], ["cw", "CW"], ["tx", "AUDIO & TX"], ["swr", "SWR"], ["dev", "DevTools"]];
const POPS = { cw: [["cwDecPop", "tbCwDec"], ["cwSendPop", "tbCwSend"], ["cwOpsPop", "tbCwOps"]], rec: [["recPop", "tbRec"]] };
const TAB = { cur: null, syncing: false };

// the CW and REC panels move into their tabs
POPS.cw.forEach(([id]) => document.querySelector('[data-pane="cw"]').appendChild($(id)));
document.querySelector('[data-pane="rec"]').appendChild($("recPop"));

TAB.select = (t) => {
  const prev = TAB.cur;
  if (prev === t) return;
  TAB.cur = t;
  G.store.set("tab", t);
  if (!TAB.syncing) {
    if (prev === "muf" && GV.view === "MUF") GV.show("MUF", false);
    if (prev === "wspr") POP.toggle("wsprPop", "tbWspr", false);
    if ((prev === "log" || prev === "dx") && $("globeP").classList.contains("expanded") && t !== "muf" && t !== "wspr") GV.expand(false);
  }
  (POPS[prev] || []).forEach(([id, btn]) => POP.toggle(id, btn, false));
  document.body.dataset.tab = t;
  TAB.fold(false);
  if (!TAB.syncing) {
    if (t === "muf") GV.show("MUF", true);
    if (t === "wspr") POP.toggle("wsprPop", "tbWspr", true);
  }
  (POPS[t] || []).forEach(([id, btn]) => POP.toggle(id, btn, true));
  document.querySelectorAll("#tabBar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  TAB.relayout();
};
TAB.relayout = () => requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));

// the panel folds away to just its tabs (the chevron); MUF / WSPR never fold
TAB.fold = (on) => {
  document.body.classList.toggle("folded", on);
  $("dockFold").textContent = on ? "⌃" : "⌄";
  G.store.set("dockFolded", on);
  TAB.relayout();
};
$("dockFold").onclick = () => TAB.fold(!document.body.classList.contains("folded"));

TABS.forEach(([t, label]) => {
  const b = document.createElement("button");
  b.className = "btn"; b.textContent = label; b.dataset.tab = t;
  b.onclick = () => (TAB.cur === t && !["muf", "wspr"].includes(t) ? TAB.fold(!document.body.classList.contains("folded")) : TAB.select(t));
  $("tabBar").appendChild(b);
});

// the globe decides when it is full-window (MUF, WSPR, or expanded from LOG); Esc closing it brings the tabs back
new MutationObserver(() => {
  const full = $("globeP").classList.contains("expanded");
  document.body.classList.toggle("full", full);
  TAB.relayout();
}).observe($("globeP"), { attributes: true, attributeFilter: ["class"] });
const gvChanged = GV.changed;
GV.changed = () => {
  gvChanged();
  const want = GV.view === "MUF" ? "muf" : GV.view === "WSPR" ? "wspr" : null;
  const target = want || (["muf", "wspr"].includes(TAB.cur) ? "log" : null);
  if (target && target !== TAB.cur) { TAB.syncing = true; TAB.select(target); TAB.syncing = false; }
};

// menu and right-column shortcuts into the tabs
if ($("railLog")) $("railLog").onclick = () => { TAB.select("log"); $("logSearch").focus(); };

// start on the remembered tab, with every docked panel closed first
[...POPS.cw, ...POPS.rec, ["wsprPop", "tbWspr"]].forEach(([id, btn]) => POP.toggle(id, btn, false));
if (GV.view === "MUF" || GV.view === "WSPR") GV.show(GV.view, false);
let startTab = G.store.get("tab", "log");
if (!TABS.some(([t]) => t === startTab)) startTab = "log";
TAB.select(startTab);
if (G.store.get("dockFolded", false) && !["muf", "wspr"].includes(startTab)) TAB.fold(true);
