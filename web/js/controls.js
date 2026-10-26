"use strict";
/* Reusable controls: segmented buttons and drag sliders, each re-rendered from status. */

G.widgets = [];
G.refresh = () => G.widgets.forEach((w) => w.update());
G.on("status", G.refresh);
G.on("prefs", G.refresh);

/**
 * Segmented control.
 * items: [[value, label], ...]; get(): current value (or array of values for toggles);
 * pick(value): action; enabled(): optional gate.
 */
G.seg = (el, items, { get, pick, enabled, multi = false, title }) => {
  el.innerHTML = "";
  const buttons = items.map(([value, label]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.dataset.value = String(value);
    b.onclick = () => pick(value, b);
    el.appendChild(b);
    return [value, b];
  });
  const w = {
    update() {
      const cur = get();
      const ok = enabled ? enabled() : true;
      for (const [value, b] of buttons) {
        const on = multi ? (cur || []).includes(value) : cur === value;
        b.classList.toggle("on", !!on);
        b.disabled = !ok;
        if (!ok && title) b.title = title; else b.removeAttribute("title");
      }
    },
  };
  G.widgets.push(w);
  w.update();
  return w;
};

/**
 * Drag slider on a .trk element. Values are min..max; onChange is called (throttled)
 * while dragging and once on release.
 */
G.slider = (el, { min = 0, max = 100, get, change, show, enabled, off }) => {
  el.innerHTML = "<i></i><b></b>";
  const fill = el.querySelector("i"), knob = el.querySelector("b");
  let dragging = false, last = 0, pending = null;
  const setVisual = (v) => {
    const p = ((v - min) / (max - min)) * 100;
    fill.style.width = `${clamp(p, 0, 100)}%`;
    knob.style.left = `${clamp(p, 0, 100)}%`;
    if (show) show(v);
  };
  const fromEvent = (e) => {
    const r = el.getBoundingClientRect();
    return Math.round(min + clamp((e.clientX - r.left) / r.width, 0, 1) * (max - min));
  };
  const send = (v, force) => {
    const now = performance.now();
    if (force || now - last > 120) { last = now; change(v); pending = null; } else pending = v;
  };
  el.addEventListener("pointerdown", (e) => {
    if (enabled && !enabled()) return;
    dragging = true;
    el.setPointerCapture(e.pointerId);
    const v = fromEvent(e); setVisual(v); send(v);
  });
  el.addEventListener("pointermove", (e) => { if (dragging) { const v = fromEvent(e); setVisual(v); send(v); } });
  el.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    dragging = false;
    const v = pending ?? fromEvent(e);
    send(v, true);
  });
  el.addEventListener("wheel", (e) => {
    if (enabled && !enabled()) return;
    e.preventDefault();
    const cur = get() ?? min;
    const v = clamp(cur + (e.deltaY < 0 ? 1 : -1) * Math.max(1, Math.round((max - min) / 50)), min, max);
    setVisual(v); send(v, true);
  }, { passive: false });
  const w = {
    update() {
      const ok = enabled ? enabled() : true;
      el.classList.toggle("na", !ok);
      el.classList.toggle("off", !!(off && off()));
      if (!dragging) { const v = get(); setVisual(v ?? min); }
    },
  };
  G.widgets.push(w);
  w.update();
  return w;
};

/** Grey out whole sections that need CAT, with a tooltip saying why. */
G.widgets.push({
  update() {
    const on = G.catOn();
    document.querySelectorAll(".cat").forEach((el) => {
      el.classList.toggle("na", !on);
      if (!on) el.title = "Needs USB CAT — connect the radio's CAT port (toolbar → Connect)";
      else el.removeAttribute("title");
    });
  },
});
