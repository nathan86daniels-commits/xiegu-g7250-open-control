"use strict";
/* Logbook entry + table, callsign lookup, Worked-the-World globe, DX cluster. */

const LOG = { rows: [], sel: null, lookup: null, points: [], prop: null, rot: null };

/* ---------- logbook ---------- */
LOG.load = async () => {
  try {
    LOG.rows = await G.api(`/api/log?search=${encodeURIComponent($("logSearch").value.trim())}&limit=300`);
    LOG.render();
  } catch (e) { G.toast(e.message, true); }
};
LOG.stats = async () => {
  try {
    const s = await G.api("/api/log/stats");
    LOG.totals = s; LOG.points = s.points;
    if (window.GV) GV.logChanged();
  } catch { /* offline */ }
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
LOG.render = () => {
  const head = "<thead><tr><th>UTC</th><th>CALL</th><th>FREQ</th><th>MODE</th><th>S / R</th><th>NAME</th><th>COUNTRY</th><th>GRID</th><th>DIST</th><th title='QRZ logbook'>QRZ</th></tr></thead>";
  const body = LOG.rows.map((r) => `<tr data-id="${r.id}"${LOG.sel === r.id ? ' class="sel"' : ""}>
    <td>${esc(r.utc.slice(11, 16))}</td><td class="c">${esc(r.call)}</td><td>${r.freq_hz ? G.fmtMHz(r.freq_hz).slice(0, -2) : ""}</td>
    <td>${esc(r.mode)}</td><td>${esc(r.rst_s)} / ${esc(r.rst_r)}</td><td${r.name ? "" : ' class="dim"'}>${esc(r.name) || "—"}</td>
    <td>${r.prefix ? `<span class="cc">${esc(r.prefix)}</span>` : ""}${esc(r.country)}</td><td>${esc(r.grid)}</td>
    <td>${r.dist_km != null ? r.dist_km.toLocaleString() + " km" + (r.approx ? "*" : "") : ""}</td>
    <td class="q" title="${esc(r.qrz_status || "")}">${r.qrz_status === "sent" || r.qrz_status === "already" ? "✓" : r.qrz_status ? "!" : ""}</td></tr>`).join("");
  $("logTable").innerHTML = head + `<tbody>${body || '<tr><td colspan="10" class="dim">No QSOs yet. Type a callsign above and press Log QSO.</td></tr>'}</tbody>`;
};
$("logTable").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  LOG.sel = Number(tr.dataset.id);
  const r = LOG.rows.find((x) => x.id === LOG.sel);
  LOG.render();
  if (r) LOG.showPath({ call: r.call, country: r.country, lat: r.lat, lon: r.lon, grid: r.grid, dist_km: r.dist_km, bearing: r.bearing, compass: r.compass, lp_km: r.lp_km, approx: r.approx });
});
$("logTable").addEventListener("contextmenu", (e) => {
  const tr = e.target.closest("tr[data-id]");
  const r = tr && LOG.rows.find((x) => x.id === Number(tr.dataset.id));
  if (!r) return;
  e.preventDefault();
  LOG.edit(r);
});
$("logTable").addEventListener("dblclick", (e) => {
  const tr = e.target.closest("tr[data-id]");
  const r = tr && LOG.rows.find((x) => x.id === Number(tr.dataset.id));
  if (r && r.freq_hz) G.tune(r.freq_hz);
});
document.addEventListener("keydown", async (e) => {
  if (e.key !== "Delete" || LOG.sel === null || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  const r = LOG.rows.find((x) => x.id === LOG.sel);
  if (r && confirm(`Delete the QSO with ${r.call} at ${r.utc}?`)) {
    await G.api(`/api/log/${r.id}`, undefined, "DELETE");
    LOG.sel = null; LOG.load(); LOG.stats();
  }
});
let searchT;
$("logSearch").addEventListener("input", () => { clearTimeout(searchT); searchT = setTimeout(LOG.load, 250); });

LOG.defaultRst = () => {
  const m = G.st?.rig.mode || "USB";
  return m === "CW" || m === "CW-R" || m === "RTTY" ? "599" : m === "DIG" ? "-10" : "59";
};
$("logForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const call = $("lgCall").value.trim().toUpperCase();
  if (!call) { $("lgCall").focus(); return; }
  const m = G.st?.rig.mode || "";
  try {
    await G.api("/api/log", {
      call, freq_hz: G.st?.rig.freq || 0, mode: m === "DIG" ? "FT8" : m === "CW-R" ? "CW" : m,
      rst_s: $("lgS").value, rst_r: $("lgR").value, name: $("lgName").value, grid: $("lgGrid").value, notes: $("lgNotes").value,
      qth: LOG.book?.call === call ? LOG.book.qth || "" : "", region: LOG.book?.call === call ? LOG.book.state || LOG.book.region || "" : "",
      ...(LOG.contest ? { contest: $("lgContest").value.trim().toUpperCase(), stx: $("lgStx").value.trim(), srx: $("lgSrx").value.trim() } : {}),
    });
    G.toast(`Logged ${call}`);
    ["lgCall", "lgName", "lgGrid", "lgNotes", "lgSrx"].forEach((id) => ($(id).value = ""));
    LOG.book = null; LOG.nameAuto = LOG.gridAuto = false;
    if (LOG.contest) LOG.nextSerial();
    $("lgS").value = $("lgR").value = LOG.defaultRst();
    LOG.load(); LOG.stats(); $("lgCall").focus();
  } catch (err) { G.toast(err.message, true); }
});
let lookT;
$("lgCall").addEventListener("input", () => {
  clearTimeout(lookT);
  const call = $("lgCall").value.trim();
  if (call.length < 3) return;
  lookT = setTimeout(async () => {
    try { LOG.showPath(await G.api(`/api/log/lookup?call=${encodeURIComponent(call)}`)); } catch { /* ignore */ }
  }, 300);
  clearTimeout(LOG.bookT);
  LOG.bookT = setTimeout(() => LOG.callbook(call), 900);
});
/* QRZ (or HamQTH) lookup: fills name and grid unless typed by hand, adds QTH and the station's local time */
LOG.callbook = async (call) => {
  call = call.toUpperCase();
  let res;
  try { res = await G.api(`/api/callbook?call=${encodeURIComponent(call)}`); } catch { return; }
  if ($("lgCall").value.trim().toUpperCase() !== call) return;          // typed on since
  const rec = res.record;
  LOG.book = rec;
  if (rec) {
    if (rec.name && (!$("lgName").value || LOG.nameAuto)) { $("lgName").value = rec.name; LOG.nameAuto = true; }
    if (rec.grid && (!$("lgGrid").value || LOG.gridAuto)) { $("lgGrid").value = rec.grid.slice(0, 6); LOG.gridAuto = true; }
    if (rec.grid || rec.lat != null) {
      // re-run the path with the real position (distance, bearing, globe marker)
      try {
        const p = await G.api(`/api/log/lookup?call=${encodeURIComponent(call)}${rec.grid ? "&grid=" + encodeURIComponent(rec.grid) : ""}${rec.lat != null ? `&lat=${rec.lat}&lon=${rec.lon}` : ""}`);
        if ($("lgCall").value.trim().toUpperCase() === call) LOG.showPath(p);
      } catch { /* ignore */ }
    }
  }
  LOG.showBook(res);
};
LOG.showBook = (res) => {
  const rec = res.record, t = res.local_time;
  if (t) { $("pcTime").textContent = `LOCAL ${t.text}`; $("pcTime").title = t.zone || ""; }
  $("pcBook").innerHTML = rec
    ? `<b>${esc(rec.name || "")}</b>${rec.qth || rec.region ? `<br>${esc([rec.qth, rec.region].filter(Boolean).join(", "))}` : ""}<br><span class="dim">${esc(res.message)}</span>`
    : res.outcome && res.outcome !== "invalid" ? `<span class="dim">${esc(res.message)}</span>` : "";
};
$("lgName").addEventListener("input", () => (LOG.nameAuto = false));
$("lgGrid").addEventListener("input", () => (LOG.gridAuto = false));
$("adifFile").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    const res = await fetch("/api/log/import", { method: "POST", body: await f.text() });
    const data = await res.json();
    G.toast(`Imported ${data.imported} QSO${data.imported === 1 ? "" : "s"}`);
    LOG.load(); LOG.stats();
  } catch (err) { G.toast(err.message, true); }
  e.target.value = "";
});

/* ---------- path card ---------- */
LOG.showPath = (p) => {
  LOG.lookup = p;
  $("pcCall").textContent = p.call || "—";
  $("pcCountry").textContent = p.country || "";
  if (p.dist_km != null) {
    $("pcDist").innerHTML = `${p.dist_km.toLocaleString()} km · ${p.bearing}° ${p.compass}${p.approx ? " · approx" : ""}`
      + (p.worked !== undefined ? (p.worked ? `<span class="badge wkd">WKD ${p.worked}×</span>` : p.new_country ? '<span class="badge new">NEW DXCC</span>' : '<span class="badge new">NEW CALL</span>') : "");
  } else {
    $("pcDist").textContent = G.st?.grid ? "" : "Set your grid in Settings for distance";
  }
  $("pcGrid").textContent = [G.st?.grid && (G.st.grid + (p.grid ? " → " + p.grid : "")), p.lp_km ? `LP ${p.lp_km.toLocaleString()} km` : ""].filter(Boolean).join(" · ");
  $("pcTime").textContent = p.local_time ? `LOCAL ${p.local_time.text}` : "";
  if (!LOG.book || LOG.book.call !== p.call) $("pcBook").innerHTML = "";
  $("gPath").hidden = !p.call;
  if (window.GV) GV.showLookup(p);
};

/* ---------- globe: the 3D globe lives in globeviews.js ---------- */
LOG.drawGlobe = () => { if (window.GV) GV.homeChanged(); };

LOG.loadProp = async () => {
  try {
    LOG.prop = await G.api("/api/propagation");
    const p = LOG.prop, [sl, sb] = p.subsolar;
    const sun = `SUN ${G.utcNow().slice(0, 5)}Z OVER ${Math.abs(sb).toFixed(0)}°${sb >= 0 ? "N" : "S"} ${Math.abs(sl).toFixed(0)}°${sl >= 0 ? "E" : "W"}`;
    LOG.propText = `${sun}<br>${p.sfi ? `SFI ${esc(p.sfi)} · K ${esc(p.k)} · A ${esc(p.a)}` : p.error ? "SOLAR DATA OFFLINE" : "SOLAR DATA…"}`;
    if (window.GV) GV.footer();
  } catch { /* offline */ }
};

/* ---------- DX cluster ---------- */
G.spots = [];
LOG.loadSpots = async () => {
  try {
    const d = await G.api("/api/cluster");
    G.spots = d.spots;
    LOG.renderSpots(d.status);
    scope.dirty = true;
  } catch { /* offline */ }
};
LOG.renderSpots = (status) => {
  const f = G.st?.rig.freq || 0, band = G.bandOf(f);
  const list = G.spots.filter((s) => !G.prefs.dxBand || (band && s.freq_hz >= band.lo && s.freq_hz <= band.hi));
  $("dxInfo").textContent = `${G.prefs.dxBand && band ? band.b + " m" : "ALL BANDS"} · LAST 30 MIN`;
  $("dxBandBtn").textContent = G.prefs.dxBand ? "THIS BAND" : "ALL BANDS";
  $("dxTable").title = status || "";
  $("dxTable").innerHTML = "<thead><tr><th>UTC</th><th>FREQ</th><th>DX</th><th>SPOTTER</th><th>NOTE</th></tr></thead><tbody>"
    + (list.slice(0, 60).map((s, i) => `<tr data-i="${G.spots.indexOf(s)}"${Math.abs(s.freq_hz - f) < 1000 ? ' class="near"' : ""}>
      <td>${esc(s.utc)}</td><td class="f">${G.fmtKhz(s.freq_hz)}</td><td class="c">${esc(s.dx)}</td><td>${esc(s.spotter)}</td><td class="sub">${esc(s.note)}</td></tr>`).join("")
      || `<tr><td colspan="5" class="dim">${esc(status || "No spots")}${G.st && !G.st.callsign ? " — set your callsign in Settings to connect" : ""}</td></tr>`)
    + "</tbody>";
};
$("dxTable").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-i]");
  if (tr) G.tuneSpot(G.spots[Number(tr.dataset.i)]);
});
$("dxBandBtn").onclick = () => { G.setPref("dxBand", !G.prefs.dxBand); LOG.renderSpots(); };

G.tuneSpot = (s) => {
  if (!s) return;
  const note = (s.note || "").toUpperCase();
  const mode = /FT8|FT4|JS8|PSK|RTTY|DIGI|Q65/.test(note) ? "DIG" : /\bCW\b|WPM/.test(note) ? "CW" : s.freq_hz < 10000000 ? "LSB" : "USB";
  if (G.st && G.st.rig.mode !== mode) G.control("mode", mode);
  G.tune(s.freq_hz);
  $("lgCall").value = s.dx;
  $("lgCall").dispatchEvent(new Event("input"));
  $("lgS").value = $("lgR").value = mode === "CW" ? "599" : mode === "DIG" ? "-10" : "59";
};

/* ---------- contest mode: serial numbers ---------- */
LOG.contest = G.store.get("contestMode", false);
LOG.nextSerial = async () => {
  try {
    const r = await G.api(`/api/log/contests?contest=${encodeURIComponent($("lgContest").value.trim().toUpperCase())}`);
    $("lgStx").value = r.next_serial;
  } catch { /* ignore */ }
};
LOG.setContest = (on) => {
  LOG.contest = on; G.store.set("contestMode", on);
  document.querySelectorAll(".logEntry .cst").forEach((el) => (el.hidden = !on));
  $("contestBtn").classList.toggle("spotsTag", on); $("contestBtn").classList.toggle("on", on);
  if (on) { $("lgContest").value = G.store.get("contestName", ""); LOG.nextSerial(); }
};
$("contestBtn").onclick = () => LOG.setContest(!LOG.contest);
$("lgContest").addEventListener("change", () => { G.store.set("contestName", $("lgContest").value.trim().toUpperCase()); LOG.nextSerial(); });
LOG.setContest(LOG.contest);

/* ---------- edit a QSO (right-click a row) ---------- */
const QF = { qCall: "call", qUtc: "utc", qMode: "mode", qRstS: "rst_s", qRstR: "rst_r", qName: "name", qQth: "qth", qGrid: "grid",
  qRegion: "region", qContest: "contest", qStx: "stx", qSrx: "srx", qNotes: "notes" };
LOG.edit = (r) => {
  LOG.editing = r;
  $("qsoId").textContent = `#${r.id}`;
  for (const [id, k] of Object.entries(QF)) $(id).value = r[k] ?? "";
  $("qFreq").value = r.freq_hz ? (r.freq_hz / 1000).toFixed(3) : "";
  $("qQrz").textContent = r.qrz_status ? `QRZ logbook: ${r.qrz_status}${r.qrz_logid ? " · log id " + r.qrz_logid : ""}` : "";
  $("qsoDlg").showModal();
};
$("qsoForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const changes = {};
  for (const [id, k] of Object.entries(QF)) changes[k] = $(id).value.trim();
  changes.call = changes.call.toUpperCase();
  const khz = parseFloat($("qFreq").value);
  if (khz > 0) changes.freq_hz = Math.round(khz * 1000);
  try {
    await G.api(`/api/log/${LOG.editing.id}`, changes, "PUT");
    $("qsoDlg").close(); G.toast(`Saved ${changes.call}`);
    LOG.load(); LOG.stats();
  } catch (err) { G.toast(err.message, true); }
});
$("qLookup").onclick = async () => {
  $("qQrz").textContent = "Looking up…";
  try {
    const res = await G.api(`/api/callbook?call=${encodeURIComponent($("qCall").value.trim())}&fresh=true`);
    const rec = res.record;
    if (rec) {
      if (rec.name) $("qName").value = rec.name;
      if (rec.qth) $("qQth").value = rec.qth;
      if (rec.grid) $("qGrid").value = rec.grid.slice(0, 6);
      if (rec.state || rec.region) $("qRegion").value = rec.state || rec.region;
    }
    $("qQrz").textContent = res.message + (res.local_time ? ` · local time ${res.local_time.text}` : "");
  } catch (err) { $("qQrz").textContent = err.message; }
};
$("qUpload").onclick = async () => {
  $("qQrz").textContent = "Sending to QRZ…";
  try { const r = await G.api(`/api/log/${LOG.editing.id}/qrz`, {}); $("qQrz").textContent = r.message; LOG.load(); }
  catch (err) { $("qQrz").textContent = err.message; }
};

/* ---------- Cabrillo export ---------- */
$("cabBtn").onclick = async () => {
  let contests = [];
  try { contests = (await G.api("/api/log/contests")).contests; } catch { /* ignore */ }
  $("cabWhich").innerHTML = contests.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("") + '<option value="">Whole logbook</option>';
  $("cabId").value = G.store.get("cabId", "");
  $("cabDlg").showModal();
};
$("cabForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const body = { contest: $("cabWhich").value, contest_id: $("cabId").value.trim().toUpperCase() || "DX", category_operator: $("cabOp").value,
    category_power: $("cabPwr").value, category_band: $("cabBand").value, category_mode: $("cabMode").value,
    name: $("cabName").value.trim(), email: $("cabEmail").value.trim() };
  G.store.set("cabId", body.contest_id);
  try {
    const res = await fetch("/api/log/cabrillo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([await res.text()], { type: "text/plain" }));
    a.download = `${body.contest_id}.cbr`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    $("cabDlg").close();
  } catch (err) { G.toast(err.message, true); }
});
