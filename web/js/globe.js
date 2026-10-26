"use strict";
/* 3D globe (WebGL).
   Layers (back to front): space backdrop (stars + atmosphere glow), the lit Earth sphere
   (ray-cast per pixel: Earth texture, night shell, optional glowing surface layer), and an
   overlay canvas for arcs, markers, labels, home, selection and the hover card.
   Geometry matches the C#: lon 0 lat 0 faces the camera (+Z), east +X, north +Y; the view
   turns by -centerLon about Y, then tilts by centerLat about X; orthographic projection. */

const GLOBE_ACCENT = [0x5b, 0x9b, 0xff];
// the atmosphere round the globe (halo, limb haze, rim) in the app's purple
const GLOBE_GLOW = [0x8b, 0x5c, 0xf6];
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const hexRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const wrap180 = (lon) => ((((lon + 180) % 360) + 360) % 360) - 180;
const d2r = Math.PI / 180;

const geoUnit = (lat, lon) => {
  const f = lat * d2r, l = lon * d2r;
  return [Math.cos(f) * Math.sin(l), Math.sin(f), Math.cos(f) * Math.cos(l)];
};
const rotate = (p, cLat, cLon) => {
  const t = -cLon * d2r;
  const x = p[0] * Math.cos(t) + p[2] * Math.sin(t);
  let z = -p[0] * Math.sin(t) + p[2] * Math.cos(t);
  const a = cLat * d2r;
  const y = p[1] * Math.cos(a) - z * Math.sin(a);
  z = p[1] * Math.sin(a) + z * Math.cos(a);
  return [x, y, z];
};
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/** Sun position (declination, longitude), the usual subsolar-point formula. */
const subsolar = (date = new Date()) => {
  const days = (date.getTime() - Date.UTC(2000, 0, 1, 12)) / 86400000;
  const deg = (v) => (((v % 360) + 360) % 360) * d2r;
  const g = deg(357.529 + 0.98560028 * days), q = 280.459 + 0.98564736 * days;
  const l = deg(q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)), e = deg(23.439 - 0.00000036 * days);
  const dec = Math.asin(Math.sin(e) * Math.sin(l)) / d2r;
  const ra = Math.atan2(Math.cos(e) * Math.sin(l), Math.cos(l)) / d2r;
  const eq = 4 * wrap180(q - ra);
  const hours = (date.getTime() % 86400000) / 3600000;
  return [dec, wrap180(-15 * (hours - 12 + eq / 60))];
};

/* ---------- textures shared by every globe ---------- */
const GLOBE_TEX = {};
const buildEarthTexture = (W = 4096, H = 2048) => {
  if (GLOBE_TEX.earth) return GLOBE_TEX.earth;
  const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const c = cv.getContext("2d"), A = GLOBE_ACCENT;
  let g = c.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, "#040c16"); g.addColorStop(0.5, "#082236"); g.addColorStop(1, "#061a2a");
  c.fillStyle = g; c.fillRect(0, 0, W, H);
  const P = (p) => [((p[0] + 180) / 360) * W, ((90 - p[1]) / 180) * H];
  const land = new Path2D();
  const feature = topojson.feature(LAND, LAND.objects.land);
  const polys = feature.type === "FeatureCollection" ? feature.features.map((f) => f.geometry) : [feature.geometry || feature];
  for (const geom of polys) {
    const sets = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
    for (const rings of sets) for (const ring of rings) {
      ring.forEach((pt, i) => { const [x, y] = P(pt); i ? land.lineTo(x, y) : land.moveTo(x, y); });
      land.closePath();
    }
  }
  c.lineJoin = "round";
  c.strokeStyle = rgba(A, 0x26 / 255); c.lineWidth = 22; c.stroke(land);   // shallow-water shelf glow
  c.strokeStyle = rgba(A, 0x30 / 255); c.lineWidth = 9; c.stroke(land);
  g = c.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, "#264a4c"); g.addColorStop(0.5, "#2a5044"); g.addColorStop(1, "#1c3a32");
  c.fillStyle = g; c.fill(land, "nonzero");
  c.strokeStyle = rgba(A, 0xc8 / 255); c.lineWidth = 2.2; c.stroke(land);
  for (let lon = -180; lon <= 180; lon += 15) {
    c.strokeStyle = rgba(A, (lon % 90 === 0 ? 0x48 : 0x22) / 255); c.lineWidth = lon % 90 === 0 ? 2.4 : 1.6;
    const x = ((lon + 180) / 360) * W; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, H); c.stroke();
  }
  for (let lat = -75; lat <= 75; lat += 15) {
    c.strokeStyle = rgba(A, (lat === 0 ? 0x48 : 0x22) / 255); c.lineWidth = lat === 0 ? 2.4 : 1.6;
    const y = ((90 - lat) / 180) * H; c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke();
  }
  c.setLineDash([6, 6]); c.strokeStyle = "rgba(224,166,64,0.19)"; c.lineWidth = 1.6;
  for (const lat of [23.44, -23.44, 66.56, -66.56]) { const y = ((90 - lat) / 180) * H; c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke(); }
  GLOBE_TEX.earth = cv;
  return cv;
};
/** Night-side darkness: alpha from the sun's altitude, smooth from +2 deg (day) to -12 deg (night). */
const buildNight = (date = new Date()) => {
  const W = 720, H = 360;
  const cv = GLOBE_TEX.night || Object.assign(document.createElement("canvas"), { width: W, height: H });
  const c = cv.getContext("2d"), img = c.createImageData(W, H), d = img.data;
  const [dec, sunLon] = subsolar(date), sd = Math.sin(dec * d2r), cd = Math.cos(dec * d2r);
  for (let y = 0; y < H; y++) {
    const lat = (90 - ((y + 0.5) * 180) / H) * d2r, sl = Math.sin(lat), cl = Math.cos(lat);
    for (let x = 0; x < W; x++) {
      const lon = -180 + ((x + 0.5) * 360) / W;
      const alt = Math.asin(sl * sd + cl * cd * Math.cos((lon - sunLon) * d2r)) / d2r;
      let t = clamp((2 - alt) / 14, 0, 1); t = t * t * (3 - 2 * t);
      d[(y * W + x) * 4 + 3] = t * 165;
    }
  }
  c.putImageData(img, 0, 0);
  GLOBE_TEX.night = cv; GLOBE_TEX.nightAt = Date.now();
  return cv;
};

const GLOBE_VS = `attribute vec2 p; varying vec2 v; void main(){ v = p; gl_Position = vec4(p, 0.0, 1.0); }`;
const GLOBE_FS = `
precision highp float;
varying vec2 v;
uniform vec2 uSize; uniform vec2 uCentre; uniform float uR;
uniform float uLat; uniform float uLon;
uniform sampler2D uEarth; uniform sampler2D uNight; uniform sampler2D uLayer; uniform float uHasLayer;
const float PI = 3.14159265;
void main(){
  vec2 px = vec2((v.x * 0.5 + 0.5) * uSize.x, (0.5 - v.y * 0.5) * uSize.y);
  float x = (px.x - uCentre.x) / uR, y = -(px.y - uCentre.y) / uR;
  float rr = x * x + y * y;
  float edge = 1.5 / uR;
  if (rr > (1.0 + edge) * (1.0 + edge)) discard;
  float z = sqrt(max(0.0, 1.0 - rr));
  // undo the view rotation: tilt back by uLat about X, then turn back by uLon about Y
  float a = uLat, t = -uLon;
  float y0 = y * cos(a) + z * sin(a);
  float z0 = -y * sin(a) + z * cos(a);
  float x1 = x * cos(t) - z0 * sin(t);
  float z1 = x * sin(t) + z0 * cos(t);
  float lat = asin(clamp(y0, -1.0, 1.0)), lon = atan(x1, z1);
  vec2 uv = vec2((lon + PI) / (2.0 * PI), (PI * 0.5 - lat) / PI);
  vec3 tex = texture2D(uEarth, uv).rgb;
  vec3 n = normalize(vec3(x, y, z));
  vec3 L = normalize(vec3(-0.55, 0.45, 1.0));            // WPF DirectionalLight (0.55,-0.45,-1) reversed
  float diff = max(dot(n, L), 0.0);
  vec3 lit = tex * (vec3(120.0, 128.0, 136.0) / 255.0 + vec3(154.0, 154.0, 150.0) / 255.0 * diff);   // ambient #788088 + directional #9A9A96
  vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
  lit += vec3(200.0, 240.0, 255.0) / 255.0 * (56.0 / 255.0) * pow(max(dot(n, H), 0.0), 28.0) * diff;   // specular #38C8F0FF, power 28
  float night = texture2D(uNight, uv).a;
  lit *= 1.0 - night;
  if (uHasLayer > 0.5) { vec4 l = texture2D(uLayer, uv); lit += l.rgb; }   // premultiplied, emissive
  float alpha = 1.0 - smoothstep(1.0 - edge, 1.0 + edge, sqrt(rr));
  gl_FragColor = vec4(lit * alpha, alpha);
}`;

class Globe {
  constructor(host, opts = {}) {
    this.host = host;
    this.compact = !!opts.compact;
    this.hint = opts.hint || "";
    this.centerLat = 30; this.centerLon = -10; this.zoom = 1;
    this.home = null;
    this.outgoing = []; this.incoming = [];
    this.selected = null; this.hover = null;
    this.flyTo = null; this.zoomTo = null; this.holdSpinUntil = 0; this.lastInteraction = 0;
    this.layers = [];          // extra draw functions: (ctx, globe, time) after the arcs, before markers
    this.topLayers = [];       // drawn last (cards etc.)
    this.surface = null; this.surfaceVersion = 0;
    host.classList.add("globeHost");
    this.bg = document.createElement("canvas");
    this.glc = document.createElement("canvas");
    this.ov = document.createElement("canvas");
    for (const c of [this.bg, this.glc, this.ov]) { c.className = "globeLayer"; host.appendChild(c); }
    this._initGl();
    this._bindInput();
    new ResizeObserver(() => this._resize()).observe(host);
    this._resize();
    this.animating = true;
    const frame = (t) => { this._frame(t); requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
  }

  /* ---------- geometry ---------- */
  get W() { return this.host.clientWidth; }
  get H() { return this.host.clientHeight; }
  get radius() { return 0.425 * Math.min(this.W, this.H) * this.zoom; }
  get centre() { return [this.W / 2, this.H / 2]; }
  project(u, r = 1) {
    const q = rotate([u[0] * r, u[1] * r, u[2] * r], this.centerLat, this.centerLon), R = this.radius, [cx, cy] = this.centre;
    return { x: cx + q[0] * R, y: cy - q[1] * R, visible: q[2] >= 0 || q[0] * q[0] + q[1] * q[1] > 1, depth: q[2] };
  }
  geoAt(px, py) {
    const R = this.radius; if (R <= 0) return null;
    const [cx, cy] = this.centre, x = (px - cx) / R, y = -(py - cy) / R, rr = x * x + y * y;
    if (rr > 1) return null;
    const z = Math.sqrt(1 - rr), a = this.centerLat * d2r, t = -this.centerLon * d2r;
    const y0 = y * Math.cos(a) + z * Math.sin(a), z0 = -y * Math.sin(a) + z * Math.cos(a);
    const x1 = x * Math.cos(t) - z0 * Math.sin(t), z1 = x * Math.sin(t) + z0 * Math.cos(t);
    return { lat: Math.asin(clamp(y0, -1, 1)) / d2r, lon: Math.atan2(x1, z1) / d2r };
  }

  /* ---------- API (as WsprGlobe) ---------- */
  setHome(home) {
    const first = !this.home && home;
    this.home = home;
    if (first) { this.centerLon = home.lon; this.centerLat = clamp(home.lat - 22, -60, 60); }
  }
  /** markers: {call, locator, lat, lon, snr, detail, distanceKm, colour:[r,g,b]|null, approximate, addedAt(ms)} */
  setMarkers(outgoing, incoming = []) {
    this.outgoing = outgoing; this.incoming = incoming; this.hover = null;
    if (this.selected) this.selected = outgoing.concat(incoming).find((m) => m.call === this.selected.call) || this.selected;
  }
  select(marker) {
    this.selected = marker;
    if (!marker) return;
    this.flyTo = { lat: clamp(marker.lat - 15, -70, 70), lon: marker.lon };
    this.holdSpinUntil = performance.now() + 60000;
    if (this.compact && this.home) {
      const a = geoUnit(this.home.lat, this.home.lon), b = geoUnit(marker.lat, marker.lon);
      const angle = Math.acos(clamp(dot3(a, b), -1, 1)) / d2r, mid = [a[0] + b[0], a[1] + b[1], a[2] + b[2]], ml = Math.hypot(...mid);
      if (ml > 1e-6) this.flyTo = { lat: clamp(Math.asin(mid[1] / ml) / d2r, -60, 60), lon: Math.atan2(mid[0], mid[2]) / d2r };
      this.zoomTo = angle < 20 ? 1.7 : angle < 45 ? 1.4 : angle < 80 ? 1.15 : 1;
    }
  }
  /** A glowing equirectangular canvas (premultiplied, black adds nothing) over the whole Earth. Null removes it. */
  setSurfaceLayer(canvas) { this.surface = canvas; this.surfaceVersion++; }
  holdStill() { this.lastInteraction = performance.now(); }
  centreOnHome() {
    if (this.home) { this.centerLon = this.home.lon; this.centerLat = clamp(this.home.lat - 22, -80, 80); }
    this.flyTo = null; this.zoomTo = null; this.zoom = 1; this.lastInteraction = performance.now();
  }

  /* ---------- WebGL ---------- */
  _initGl() {
    const gl = this.glc.getContext("webgl", { premultipliedAlpha: true, alpha: true, antialias: false });
    this.gl = gl;
    if (!gl) return;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(s)); return s; };
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, GLOBE_VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, GLOBE_FS)); gl.linkProgram(pr);
    this.prog = pr;
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    this.u = {};
    for (const n of ["uSize", "uCentre", "uR", "uLat", "uLon", "uEarth", "uNight", "uLayer", "uHasLayer"]) this.u[n] = gl.getUniformLocation(pr, n);
    this.attr = gl.getAttribLocation(pr, "p");
    const tex = () => { const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); return t; };
    this.tEarth = tex(); this.tNight = tex(); this.tLayer = tex();
    const upload = (t, src, mip) => {
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, src);
      if (mip) gl.generateMipmap(gl.TEXTURE_2D);
      else {   // WebGL1: a non-power-of-two texture must clamp and skip mipmaps or it samples as black
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      }
    };
    this._upload = upload;
    upload(this.tEarth, buildEarthTexture(), true);
    upload(this.tNight, GLOBE_TEX.night && Date.now() - GLOBE_TEX.nightAt < 60000 ? GLOBE_TEX.night : buildNight(), false);
    this.nightUploaded = GLOBE_TEX.nightAt;
    this.layerUploaded = -1;
  }
  _drawGl() {
    const gl = this.gl; if (!gl) return;
    const dpr = this.dpr;
    if (GLOBE_TEX.nightAt !== this.nightUploaded) { this._upload(this.tNight, GLOBE_TEX.night, false); this.nightUploaded = GLOBE_TEX.nightAt; }
    if (this.layerUploaded !== this.surfaceVersion) {
      if (this.surface) this._upload(this.tLayer, this.surface, false);
      this.layerUploaded = this.surfaceVersion;
    }
    gl.viewport(0, 0, this.glc.width, this.glc.height);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.prog);
    gl.enableVertexAttribArray(this.attr); gl.vertexAttribPointer(this.attr, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(this.u.uSize, this.glc.width, this.glc.height);
    gl.uniform2f(this.u.uCentre, this.glc.width / 2, this.glc.height / 2);
    gl.uniform1f(this.u.uR, this.radius * dpr);
    gl.uniform1f(this.u.uLat, this.centerLat * d2r); gl.uniform1f(this.u.uLon, this.centerLon * d2r);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.tEarth); gl.uniform1i(this.u.uEarth, 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this.tNight); gl.uniform1i(this.u.uNight, 1);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, this.tLayer); gl.uniform1i(this.u.uLayer, 2);
    gl.uniform1f(this.u.uHasLayer, this.surface ? 1 : 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  _resize() {
    this.dpr = window.devicePixelRatio || 1;
    for (const c of [this.bg, this.glc, this.ov]) { c.width = Math.round(this.W * this.dpr); c.height = Math.round(this.H * this.dpr); }
    this.stars = null;
  }

  /* ---------- input ---------- */
  _bindInput() {
    const el = this.ov;
    el.style.touchAction = "none";
    let drag = null;
    el.addEventListener("pointerdown", (e) => { drag = { x: e.offsetX, y: e.offsetY }; el.setPointerCapture(e.pointerId); this.lastInteraction = performance.now(); });
    el.addEventListener("pointermove", (e) => {
      this.mouse = drag ? null : { x: e.offsetX, y: e.offsetY };
      if (drag) {
        this.flyTo = null;
        const per = 180 / Math.PI / Math.max(1, this.radius);
        this.centerLon = wrap180(this.centerLon - (e.offsetX - drag.x) * per);
        this.centerLat = clamp(this.centerLat + (e.offsetY - drag.y) * per, -85, 85);
        drag = { x: e.offsetX, y: e.offsetY };
        this.lastInteraction = performance.now();
      } else {
        this.hover = this._hit(e.offsetX, e.offsetY);
        if (this.hover || (this.holdOnPoint && this.geoAt(e.offsetX, e.offsetY))) this.lastInteraction = performance.now();
      }
    });
    const up = () => { if (drag) { drag = null; this.lastInteraction = performance.now(); } };
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("pointerleave", () => { this.hover = null; this.mouse = null; });
    el.addEventListener("dblclick", () => this.centreOnHome());
    el.addEventListener("wheel", (e) => {
      e.preventDefault(); this.zoomTo = null;
      this.zoom = clamp(this.zoom * Math.pow(1.12, -e.deltaY / 100), 0.6, 5);
      this.lastInteraction = performance.now();
    }, { passive: false });
    el.addEventListener("click", (e) => { const m = this._hit(e.offsetX, e.offsetY); if (m && this.onPick) this.onPick(m); });
  }
  _hit(x, y) {
    let best = null, bestD = 9;
    for (const s of this.outgoing.concat(this.incoming)) {
      const p = this.project(geoUnit(s.lat, s.lon));
      if (!p.visible) continue;
      const d = Math.hypot(p.x - x, p.y - y);
      if (d < bestD) { bestD = d; best = s; }
    }
    return best;
  }

  /* ---------- animation ---------- */
  _frame(now) {
    if (!this.host.isConnected || !this.W) return;
    if (this.host.offsetParent === null) return;          // hidden
    const dt = Math.min(0.1, (now - (this.last || now)) / 1000);
    if (this.compact && now - (this.last || 0) < 32) return;
    this.last = now;
    if (this.zoomTo !== null && this.zoomTo !== undefined) {
      this.zoom += (this.zoomTo - this.zoom) * (1 - Math.exp(-dt * 4));
      if (Math.abs(this.zoomTo - this.zoom) < 0.005) { this.zoom = this.zoomTo; this.zoomTo = null; }
    }
    if (this.flyTo) {
      const k = 1 - Math.exp(-dt * 5), dLon = wrap180(this.flyTo.lon - this.centerLon), dLat = this.flyTo.lat - this.centerLat;
      this.centerLon = wrap180(this.centerLon + dLon * k); this.centerLat += dLat * k;
      if (Math.abs(dLon) < 0.05 && Math.abs(dLat) < 0.05) this.flyTo = null;
    } else if (!this.compact && now - this.lastInteraction > 8000 && now > this.holdSpinUntil) {
      this.centerLon = wrap180(this.centerLon + 4 * dt);   // 4 deg/s, one turn in 90 s
    }
    if (Date.now() - (GLOBE_TEX.nightAt || 0) > 60000) buildNight();
    this._drawBackdrop();
    this._drawGl();
    this._drawOverlay();
  }

  _drawBackdrop() {
    const c = this.bg.getContext("2d"), W = this.W, H = this.H;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const g = c.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, Math.hypot(W, H) * 0.4);
    g.addColorStop(0, "#0a1420"); g.addColorStop(1, "#020408");
    c.fillStyle = g; c.fillRect(0, 0, W, H);
    if (!this.stars) {
      let s = 1234; const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
      this.stars = Array.from({ length: Math.floor((W * H) / 2600) }, () => [rnd() * W, rnd() * H, 0.4 + Math.pow(rnd(), 3) * 1.3, (60 + rnd() * 170) / 255]);
    }
    for (const [x, y, r, a] of this.stars) { c.fillStyle = `rgba(221,232,255,${a})`; c.beginPath(); c.arc(x, y, r, 0, 7); c.fill(); }
  }

  _drawOverlay() {
    const c = this.ov.getContext("2d"), W = this.W, H = this.H, A = GLOBE_ACCENT;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const R = this.radius, [cx, cy] = this.centre, time = (Date.now() % 86400000) / 1000, now = Date.now();
    for (const f of this.layers) f(c, this, time);

    const homeU = this.home ? geoUnit(this.home.lat, this.home.lon) : null;
    const font = (sz, w = 600) => `${w} ${sz}px "Barlow Semi Condensed", "Segoe UI", sans-serif`;
    let index = 0;
    const labels = [];
    // stations we hear: dashed accent arcs marching in, diamond markers
    for (const s of [...this.incoming].sort((a, b) => a.snr - b.snr)) {
      const tx = geoUnit(s.lat, s.lon);
      if (homeU) this._arc(c, tx, homeU, A, time, index++, { dashed: true });
      const p = this.project(tx); if (!p.visible) continue;
      const fade = clamp(0.35 + p.depth * 1.5, 0.35, 1), col = s.colour || snrColour(s.snr);
      c.fillStyle = rgba(A, (60 * fade) / 255); c.beginPath(); c.arc(p.x, p.y, 8, 0, 7); c.fill();
      c.beginPath(); c.moveTo(p.x, p.y - 5); c.lineTo(p.x + 5, p.y); c.lineTo(p.x, p.y + 5); c.lineTo(p.x - 5, p.y); c.closePath();
      c.fillStyle = rgba(col, fade); c.fill(); c.strokeStyle = rgba(A, (230 * fade) / 255); c.lineWidth = 1.2; c.stroke();
      if (p.depth > 0.15) labels.push([s, p]);
    }
    // outgoing: arcs out from home, round markers (logbook / heard me / DX)
    const count = this.compact ? 0 : this.outgoing.length, busy = count > 150;
    for (const s of [...this.outgoing].sort((a, b) => a.snr - b.snr)) {
      const rx = geoUnit(s.lat, s.lon), col = s.colour || snrColour(s.snr);
      if (this.compact && this.selected?.call !== s.call) {
        const p = this.project(rx); if (!p.visible) continue;
        c.fillStyle = rgba(col, ((190 / 255) * clamp(0.25 + p.depth * 1.5, 0.25, 1)) * (s.approximate ? 0.55 : 1));
        c.beginPath(); c.arc(p.x, p.y, 1.7, 0, 7); c.fill(); continue;
      }
      const reveal = s.addedAt ? clamp((now - s.addedAt) / 1600, 0, 1) : 1;
      if (homeU) this._arc(c, homeU, rx, col, time, index++, { reveal, steps: count > 300 ? 28 : 72, pulses: !busy, strength: s.approximate ? 0.55 : busy ? 0.7 : 1 });
      if (reveal < 1) continue;
      const p = this.project(rx); if (!p.visible) continue;
      const fade = clamp(0.35 + p.depth * 1.5, 0.35, 1) * (s.approximate ? 0.6 : 1);
      if (!busy) {
        const ph = (time * 0.6 + (hashCall(s.call) & 0xff) / 255) % 1;
        c.strokeStyle = rgba(col, (160 / 255) * (1 - ph) * fade); c.lineWidth = 1.4; c.beginPath(); c.arc(p.x, p.y, 3 + 9 * ph, 0, 7); c.stroke();
      }
      if (s.addedAt && now - s.addedAt < 4800) {
        const t = (now - s.addedAt - 1600) / 3200;
        c.strokeStyle = `rgba(255,255,255,${1 - t})`; c.lineWidth = 2; c.beginPath(); c.arc(p.x, p.y, 4 + 26 * t, 0, 7); c.stroke();
      }
      c.fillStyle = rgba(col, (70 / 255) * fade); c.beginPath(); c.arc(p.x, p.y, 7, 0, 7); c.fill();
      c.fillStyle = rgba(col, fade); c.beginPath(); c.arc(p.x, p.y, 3.4, 0, 7); c.fill();
      c.strokeStyle = `rgba(255,255,255,${(200 / 255) * fade})`; c.lineWidth = 0.8; c.stroke();
      if (p.depth > 0.15) labels.push([s, p]);
    }
    // a selected station that is not a marker (a call typed into the log) still gets its path
    const sel = this.selected;
    if (sel && homeU && !this.outgoing.some((m) => m.call === sel.call) && !this.incoming.some((m) => m.call === sel.call))
      this._arc(c, homeU, geoUnit(sel.lat, sel.lon), sel.colour || A, time, index++, { strength: sel.approximate ? 0.55 : 1 });
    if (sel) {
      const p = this.project(geoUnit(sel.lat, sel.lon));
      if (p.visible) {
        const ph = (time % 1.2) / 1.2;
        c.strokeStyle = "#fff"; c.lineWidth = 1.4; c.beginPath(); c.arc(p.x, p.y, 4, 0, 7); c.stroke();
        c.strokeStyle = `rgba(255,255,255,${(220 / 255) * (1 - ph)})`; c.lineWidth = 1.2; c.beginPath(); c.arc(p.x, p.y, 4 + 7 * ph, 0, 7); c.stroke();
        c.font = font(12); const tw = c.measureText(sel.call).width;
        c.fillStyle = "rgba(4,10,16,0.82)"; c.strokeStyle = rgba(A, 1); c.lineWidth = 1;
        c.beginPath(); c.roundRect(p.x + 8, p.y - 9, tw + 8, 18, 3); c.fill(); c.stroke();
        c.fillStyle = "#fff"; c.textBaseline = "middle"; c.fillText(sel.call, p.x + 12, p.y);
        for (let i = labels.length - 1; i >= 0; i--) if (labels[i][0].call === sel.call) labels.splice(i, 1);
      }
    }
    if (labels.length <= 40) {
      const placed = [];
      c.font = font(10.5); c.textBaseline = "middle";
      for (const [s, p] of labels.sort((a, b) => (b[0].distanceKm || 0) - (a[0].distanceKm || 0))) {
        const tw = c.measureText(s.call).width, box = [p.x + 7, p.y - 7, tw + 6, 14];
        if (placed.some((r) => box[0] < r[0] + r[2] && box[0] + box[2] > r[0] && box[1] < r[1] + r[3] && box[1] + box[3] > r[1])) continue;
        placed.push(box);
        c.fillStyle = "rgba(4,10,16,0.63)"; c.beginPath(); c.roundRect(...box, 3); c.fill();
        c.fillStyle = "rgba(232,237,240,0.88)"; c.fillText(s.call, box[0] + 3, p.y);
      }
    }
    if (homeU) {
      const p = this.project(homeU);
      if (p.visible) {
        for (let k = 0; k < 2; k++) {
          const ph = (time * 0.5 + k * 0.5) % 1;
          c.strokeStyle = rgba(A, (200 / 255) * (1 - ph)); c.lineWidth = 1.2; c.beginPath(); c.arc(p.x, p.y, 2.5 + 9 * ph, 0, 7); c.stroke();
        }
        c.fillStyle = rgba(A, 0x60 / 255); c.beginPath(); c.arc(p.x, p.y, 4.5, 0, 7); c.fill();
        c.fillStyle = "#fff"; c.strokeStyle = rgba(A, 1); c.lineWidth = 1.4; c.beginPath(); c.arc(p.x, p.y, 2.6, 0, 7); c.fill(); c.stroke();
      }
    }
    for (const f of this.topLayers) f(c, this, time);
    if (this.hover) this._card(c, this.hover);
    if (this.hint) {
      c.font = font(10, 500); c.fillStyle = "rgba(135,148,155,0.56)"; c.textAlign = "right"; c.textBaseline = "alphabetic";
      c.fillText(this.hint, W - 10, H - 8); c.textAlign = "left";
    }
  }

  /** Raised great-circle arc (higher for longer paths) with a glow and a pulse travelling outwards. */
  _arc(c, a, b, col, time, index, { dashed = false, reveal = 1, steps = 72, pulses = true, strength = 1 } = {}) {
    const angle = Math.acos(clamp(dot3(a, b), -1, 1));
    if (angle < 1e-4 || reveal <= 0) return;
    const lift = 0.03 + (0.22 * angle) / Math.PI, shown = Math.max(1, Math.ceil(steps * reveal)), pts = [];
    for (let i = 0; i <= shown; i++) {
      const t = i / steps, sa = Math.sin((1 - t) * angle), sb = Math.sin(t * angle), sn = Math.sin(angle);
      const v = [(sa * a[0] + sb * b[0]) / sn, (sa * a[1] + sb * b[1]) / sn, (sa * a[2] + sb * b[2]) / sn];
      pts.push(this.project(v, 1 + lift * Math.sin(Math.PI * t)));
    }
    const path = new Path2D();
    let open = false;
    for (let i = 1; i <= shown; i++) {
      if (!pts[i - 1].visible || !pts[i].visible) { open = false; continue; }
      if (!open) { path.moveTo(pts[i - 1].x, pts[i - 1].y); open = true; }
      path.lineTo(pts[i].x, pts[i].y);
    }
    c.lineCap = "round"; c.lineJoin = "round";
    c.strokeStyle = rgba(col, (0x30 / 255) * strength); c.lineWidth = 5; c.stroke(path);
    c.strokeStyle = rgba(col, (0xc0 / 255) * strength); c.lineWidth = 1.3;
    if (dashed) { c.setLineDash([4 * 1.3, 3 * 1.3]); c.lineDashOffset = time * 6 * 1.3; }
    c.stroke(path); c.setLineDash([]);
    if (reveal < 1) { const tip = pts[shown]; if (tip.visible) { c.fillStyle = "#fff"; c.beginPath(); c.arc(tip.x, tip.y, 2.5, 0, 7); c.fill(); } return; }
    if (!pulses) return;
    const pulse = (time * 0.35 + index * 0.137) % 1, pp = pts[Math.floor(pulse * steps)];
    if (pp && pp.visible) {
      c.fillStyle = rgba(col, 0x50 / 255); c.beginPath(); c.arc(pp.x, pp.y, 5, 0, 7); c.fill();
      c.fillStyle = "rgba(255,255,255,0.94)"; c.beginPath(); c.arc(pp.x, pp.y, 1.8, 0, 7); c.fill();
    }
  }

  _card(c, s) {
    const p = this.project(geoUnit(s.lat, s.lon)); if (!p.visible) return;
    const col = s.colour || snrColour(s.snr), title = `${s.call}  ${s.locator || ""}`.trim(), lines = (s.detail || "").split("\n").filter(Boolean);
    c.font = `600 13px "Barlow Semi Condensed", sans-serif`; let w = c.measureText(title).width;
    c.font = `600 11px "Barlow Semi Condensed", sans-serif`; for (const l of lines) w = Math.max(w, c.measureText(l).width);
    w += 20; const h = 22 + lines.length * 15 + 8;
    let x = p.x + 14, y = clamp(p.y - h / 2, 4, Math.max(4, this.H - h - 4));
    if (x + w > this.W - 4) x = p.x - 14 - w;
    c.fillStyle = "rgba(6,12,18,0.91)"; c.strokeStyle = rgba(GLOBE_ACCENT, 0xb0 / 255); c.lineWidth = 1;
    c.beginPath(); c.roundRect(x, y, w, h, 5); c.fill(); c.stroke();
    c.fillStyle = rgba(col, 1); c.fillRect(x, y + 5, 3, h - 10);
    c.textBaseline = "top"; c.font = `600 13px "Barlow Semi Condensed", sans-serif`; c.fillStyle = "#e8edf0"; c.fillText(title, x + 10, y + 6);
    c.font = `600 11px "Barlow Semi Condensed", sans-serif`; c.fillStyle = "#a8b4ba"; lines.forEach((l, i) => c.fillText(l, x + 10, y + 26 + i * 15));
    c.textBaseline = "alphabetic";
  }
}

const hashCall = (s) => { let h = 0; for (const ch of s || "") h = (h * 31 + ch.charCodeAt(0)) | 0; return h; };
const snrColour = (snr) => (snr >= -10 ? [0x3d, 0xdc, 0x84] : snr >= -20 ? [0xf5, 0xa5, 0x24] : [0xff, 0x7a, 0x3c]);
const BAND_COLOURS = { "160m": "#E05CC8", "80m": "#A06CFF", "60m": "#7E6CFF", "40m": "#46D446", "30m": "#2BBFA0", "20m": "#3D7EFF",
  "17m": "#E8D040", "15m": "#F09A3E", "12m": "#FF6F91", "10m": "#F0453A", "6m": "#E8EDF0" };
const bandColour = (band) => hexRgb(BAND_COLOURS[band] || "#87949B");
