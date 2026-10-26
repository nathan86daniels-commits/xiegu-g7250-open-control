"use strict";
/* The layered scope renderer, in WebGL2.
   Passes (as ScopeLayeredShaders.hlsl): ENERGY (persistence field), BLOOM (separable gaussian on a
   quarter-size copy), COMPOSITE (waterfall, energy body, lit fill, trace with polish glow, seam bridge
   and spill, polished clear bandpass glass, carrier line).
   For the G7250 every bin is IQ dBFS,
   mapped onto the same 0..1 raw scale (RAW_DB dB = 1.0) so the published calibration maths
   (calibratedFloor, displayAmplitude, spectrumY, emissiveStrength) runs unchanged. */

const SG = {
  SEAM: 0.42, ROWS: 1024, RAW_DB: 100, RAW_ZERO: -160,
  palette: "TsBlack",                           // one scope look: the TS Black scope (no palette or style choice)
  level: G.store.get("scopeLevel", 0),          // displaySettings.x: -30 (dark) .. +30 (reveal), the LEVEL control
  glass: G.store.get("scopeGlass", 100),
  style: "TS",
};
const toRaw = (db) => (db - SG.RAW_ZERO) / SG.RAW_DB;

/* ---------- palettes (SpectrumPalette.cs) ---------- */
const PAL = {
  TsBlack: ["ramp6", [[10, 15, 107], [13, 46, 217], [30, 110, 255], [0, 205, 238], [43, 240, 125], [250, 240, 70]].map((c) => c.map((v) => v / 255)), 0.6],
  Blue: ["ramp4", [[0, 6 / 255, 1], [0, 0.2133625, 1], [0, 74 / 255, 1], [0.637, 0.607196, 1]]],
  InstrumentBlue: ["ramp6", [[6, 21, 48], [10, 42, 94], [16, 72, 154], [30, 111, 214], [74, 157, 255], [227, 240, 255]].map((c) => c.map((v) => v / 255)), 1.35],
  Cyan: ["ramp4", [[0, 0.035, 0.045], [0, 0.40, 0.46], [0.12, 0.88, 0.92], [0.78, 1, 1]]],
  MatrixGreen: ["ramp4", [[0.01, 0.06, 0.025], [0.04, 0.31, 0.12], [0.16, 0.86, 0.45], [0.62, 1, 0.84]]],
  SdrBlueGreen: ["ramp6", [[0, 0.025, 0.13], [0.02, 0.19, 0.64], [0.06, 0.42, 1], [0.10, 0.78, 0.80], [0.22, 0.92, 0.34], [0.40, 1, 0.46]]],
  DeepRedBlue: ["ramp6", [[0.02, 0.02, 0.14], [0.08, 0.14, 0.62], [0.40, 0.14, 0.80], [0.70, 0.08, 0.45], [0.95, 0.10, 0.18], [1, 0.55, 0.50]]],
  Purple: ["ramp4", [[0.04, 0, 0.09], [0.35, 0, 0.7], [1, 0.05, 0.9], [1, 0.75, 1]]],
  Orange: ["ramp4", [[0.06, 0.02, 0], [0.6, 0.22, 0], [1, 0.62, 0], [1, 0.92, 0.5]]],
};
/* Colour A: A DARK #5300FF, A LIGHT #6B33FF on TS Black.
   SpectrumPalette.ApplyEndpoints: A dark is stop 0, A light stop 2; stop 1 follows by half of their movement and
   stop 3 by half of A light's (B is unchanged), so the field turns purple and the cores keep TS Black's top end. */
const applyColourA = (d, aDark, aLight) => {
  const c01 = (v) => v.map((x) => clamp(x, 0, 1)), add = (u, v, k = 1) => u.map((x, i) => x + v[i] * k), sub = (u, v) => u.map((x, i) => x - v[i]);
  const s = d.map((v) => v.slice());
  s[0] = c01(aDark); s[2] = c01(aLight);
  s[1] = c01(add(d[1], add(sub(s[0], d[0]), sub(s[2], d[2])), 0.5));
  s[3] = c01(add(d[3], sub(s[2], d[2]), 0.5));
  return s;
};
PAL.TsBlack[1] = applyColourA(PAL.TsBlack[1], [0x53 / 255, 0, 1], [0x6b / 255, 0x33 / 255, 1]);
const lerp3 = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const ramp4 = (n, c) => { n = clamp(n, 0, 1); return n < 0.10 ? lerp3([0, 0, 0], c[0], n / 0.10) : n < 0.48 ? lerp3(c[0], c[1], (n - 0.10) / 0.38) : n < 0.90 ? lerp3(c[1], c[2], (n - 0.48) / 0.42) : lerp3(c[2], c[3], Math.pow((n - 0.90) / 0.10, 2)); };
const ramp6 = (n, c) => {
  n = clamp(n, 0, 1);
  if (n < 0.06) return lerp3([0, 0, 0], c[0], n / 0.06);
  if (n < 0.24) return lerp3(c[0], c[1], (n - 0.06) / 0.18);
  if (n < 0.42) return lerp3(c[1], c[2], (n - 0.24) / 0.18);
  if (n < 0.58) return lerp3(c[2], c[3], (n - 0.42) / 0.16);
  if (n < 0.78) return lerp3(c[3], c[4], (n - 0.58) / 0.20);
  return lerp3(c[4], c[5], Math.pow((n - 0.78) / 0.22, 2));
};
const smooth01 = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
SG.lut = () => {
  const [shape, stops, gamma = 1] = PAL[SG.palette] || PAL.Blue;
  const ev = (n) => (shape === "ramp4" ? ramp4(n, stops) : ramp6(n, stops));
  const native = shape === "ramp4" ? 0.10 : 0.06;
  const data = new Uint8Array(1024 * 2 * 4);
  for (let i = 0; i < 1024; i++) {
    const n = i / 1023;
    const s = ev(n);
    // waterfall row: calm compression (floor .05) then gamma, then the near-black guard
    let w = n * (0.05 + 0.95 * smooth01((n - 0.12) / 0.30));
    if (gamma !== 1) w = Math.pow(clamp(w, 0, 1), gamma);
    const wc = ev(w).map((v) => v * smooth01(w / native));
    for (let k = 0; k < 3; k++) { data[i * 4 + k] = clamp(s[k], 0, 1) * 255; data[(1024 + i) * 4 + k] = clamp(wc[k], 0, 1) * 255; }
    data[i * 4 + 3] = data[(1024 + i) * 4 + 3] = 255;
  }
  return data;
};

/* ---------- shaders ---------- */
const SG_VS = `#version 300 es
in vec2 p; out vec2 uv;
void main(){ uv = vec2(p.x*.5+.5, .5-p.y*.5); gl_Position = vec4(p,0.,1.); }`;

const SG_COMMON = `#version 300 es
precision highp float; precision highp int;
in vec2 uv; out vec4 outColor;
uniform vec4 view;        // left Hz (display), span Hz, width px, height px
uniform vec4 source;      // trace: left Hz of bin 0 relative to view.x, bin span Hz, first bin, last bin
uniform vec4 levels;      // floor raw, high raw, newest ring row, visible rows
uniform vec4 marker;      // carrier Hz rel. to view.x, -, -, contrast
uniform vec4 options;     // grid, axis, fill, shadows
uniform vec4 timing;      // elapsed s, peak, dpi, waterfall rows on screen
uniform vec4 displaySettings; // level dB
uniform vec4 effects;     // night sky, RX left px, RX right px, -
uniform vec4 field;       // dt, floor tau, bloom strength, spectrum height px
uniform vec4 tsLayout;      // w: 0 classic scope, 2 Obsidian TS scope
uniform vec4 refHz;       // x = absolute Hz of view left (history rows are stored in absolute Hz)
uniform sampler2D paletteLut;   // 1024x2
uniform sampler2D motionTrace;  // N x 1 RGBA32F: x spring height, y peak, z raw newest, w rise velocity
uniform sampler2D history;      // N x ROWS R32F
uniform sampler2D metadata;     // 4 x ROWS RGBA32F: (0) lo Hz, span Hz, -, - ; (1) time, p20, median, p80 ; (2) white, -, -, valid
uniform sampler2D energyTex; uniform sampler2D bloomTex;
const float SPECTRUM_SEAM = ${SG.SEAM};
const int ROWS = ${SG.ROWS};
float seamFraction(){ return SPECTRUM_SEAM; }
float dpiScale(){ return max(1., timing.z); }
float saturate(float x){ return clamp(x,0.,1.); }
vec3 saturate3(vec3 x){ return clamp(x,0.,1.); }
vec3 paletteRow(float n, float row){ return texture(paletteLut, vec2((saturate(n)*1023.+.5)/1024., row)).rgb; }
vec3 palette(float n){ return paletteRow(n,.25); }
vec3 paletteWf(float n){ return paletteRow(n,.75); }
vec3 calmWaterfall(float s){ return paletteWf(s); }
vec3 palWfOuter(){ return palette(.32); } vec3 palWfInner(){ return palette(.60); }
vec3 deepColor(){ return palette(.38); } vec3 accentColor(){ return palette(.66); }
vec3 palBloom(){ return palette(.90); } vec3 palPeak(){ return palette(.18)*.75; }
vec3 palCore(float e){ return mix(palette(.72),palette(1.),saturate(e)); }
vec3 traceCoreColor(float e){ return palette(mix(.50,1.0,e)); }
float displayAmplitude(float value, float floorv, float range){ return saturate(pow(saturate((value-floorv)/max(.10,range)),1.8)*marker.w); }
float amplitude(float v){ return displayAmplitude(v, levels.x, levels.y-levels.x); }
float calibratedFloor(float p20, float median, float p80){
  float sigma=max(1./255.,(p80-p20)/1.683);
  float quiet=median+1.5*sigma, reveal=p20-.75*sigma;
  return mix(quiet, reveal, (clamp(displaySettings.x,-30.,30.)+30.)/60.);
}
float spectrumY(float value){
  float intensity=pow(max(0.,(value-levels.x)/max(.18,levels.y-levels.x)),1.8)*marker.w;
  float knee=log(1.+max(0.,intensity-.72)/.28);
  float height=intensity<=.72 ? intensity : .72+.28*knee/(1.+knee);
  float seam=seamFraction();
  float top=min(24.*dpiScale(), view.w*seam*.25);
  return seam-3./view.w-height*(seam-(top+3.)/view.w);
}
float emissiveStrength(float v){ return smoothstep(.22,.85,v)*smoothstep(.12,.45,v); }
float traceBin(float hz){ return source.z+(hz-source.x)/max(.001,source.y)*(source.w-source.z); }
float traceFootprint(){ return view.y/view.z/max(.001,source.y)*(source.w-source.z); }
float binScreenX(float bin){ return ((source.x+(bin-source.z)/(source.w-source.z)*source.y)/view.y)*view.z; }
vec4 traceAt(int i){ return texelFetch(motionTrace, ivec2(clamp(i,int(source.z),int(source.w)),0),0); }
vec4 readTrace(float bin, float footprint){
  bin=clamp(bin,source.z,source.w);
  int lo=int(floor(bin));
  vec4 v=mix(traceAt(lo),traceAt(lo+1),fract(bin));
  if(footprint>1.){
    int a=max(int(source.z),int(ceil(bin-footprint*.5))), b=min(int(source.w),int(floor(bin+footprint*.5)));
    for(int k=a;k<=b;k++){ vec4 s=traceAt(k); v=max(v,s); }
  }
  return v;
}
float columnHeight(float value){ float seam=seamFraction(); return saturate((seam-spectrumY(value))/seam); }
// TS scope (Obsidian / TS Black): no spectrum reading line, noise-floor fill gated off, grey grid, one-piece seam line.
float traceShown(){ return 1.-saturate(tsLayout.w); }
float tsSignalGate(float strength){ return tsLayout.w>0. ? smoothstep(.06,.16,strength) : 1.; }
bool themeFrosted(){ return tsLayout.w>0.; }
const vec3 OBSIDIAN_GRID=vec3(.20,.20,.21), OBSIDIAN_GRID_FLOOR=OBSIDIAN_GRID;   // grey; the bottom (seam) line matches the other grid lines
bool rxPassbandContains(float px){ return effects.z>effects.y && px>=effects.y && px<effects.z; }
float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }
`;

const SG_ENERGY = SG_COMMON + `
// PSEnergy: RF energy rises to the spring-smoothed column height, then decays with height-dependent tau.
void main(){
  float hz=view.x+uv.x*view.y;
  float depth=1.-uv.y;                         // uv covers the spectrum area only
  vec4 prev=texture(energyTex, uv);
  float tau=mix(field.y, field.y*.30, depth);
  float k=exp(-field.x/max(.02,tau));
  vec4 e=vec4(prev.x*k, prev.y*exp(-field.x/.16), 0., 1.);
  float bin=traceBin(hz);
  if(bin>=source.z && bin<=source.w){
    vec4 t=readTrace(bin,traceFootprint());
    float h=columnHeight(t.x), strength=amplitude(t.x);
    float px=(h-depth)*field.w;
    float body=px>0. ? strength*(.55+.45*saturate(depth/max(.02,h))) : 0.;
    float crest=emissiveStrength(strength)*exp(-px*px/(2.*9.*dpiScale()*dpiScale()));
    e.x=max(e.x,body); e.y=max(e.y,crest);
    e.xy*=traceShown();
  }
  outColor=e;
}`;

const SG_BLOOM = `#version 300 es
precision highp float;
in vec2 uv; out vec4 outColor;
uniform sampler2D src; uniform vec4 blur;   // xy texel, zw direction
void main(){
  float w[5]=float[5](.227,.194,.121,.054,.016);
  vec3 c=texture(src,uv).xyz*w[0];
  for(int i=1;i<5;i++){ vec2 o=blur.zw*blur.xy*float(i); c+=(texture(src,uv+o).xyz+texture(src,uv-o).xyz)*w[i]; }
  outColor=vec4(c,1.);
}`;

const SG_COMPOSITE = SG_COMMON + `
const float POLISH_REACH_PX=11., POLISH_TRACE_WEIGHT=.45, POLISH_GLOW=.12, POLISH_GLOW_SIGMA=4., POLISH_ABOVE=.55,
  POLISH_PEAK_BLOOM=.12, POLISH_PEAK_SIGMA=5., POLISH_FILL=.045, POLISH_WF_PERSIST=1., POLISH_WF_TAU=.12, POLISH_WF_GRAIN=.024,
  POLISH_WF_GRAIN_HUE=.45, POLISH_SEAM_FADE=.85, POLISH_SEAM_SPILL_PX=8., POLISH_SEAM_PEAK=.16;
const vec3 GRID_BASE=vec3(.025,.035,.04), GRID_SHEEN=vec3(.004,.014,.022);
vec3 polishTint(vec3 c){ return c*min(4.,1./max(.001,max(c.r,max(c.g,c.b)))); }
float polishLuma(vec3 c){ return dot(c,vec3(.2126,.7152,.0722)); }
vec2 motionVertex(int bin){ return vec2(binScreenX(float(bin)), spectrumY(traceAt(bin).x)*view.w); }

// distance to the trace polyline, soft glow light, strong-peak bloom light, emission (motionTraceReach)
vec4 motionTraceReach(vec2 pixel, float bin, float reachPx){
  int first=int(source.z), last=int(source.w);
  float binsPerPixel=(source.w-source.z)*view.y/max(1.,source.y*view.z);
  float search=max(2.,binsPerPixel*max(1.5,reachPx));
  int begin=max(first,int(floor(bin-search))), end=min(last-1,int(ceil(bin+search)));
  float dpi=dpiScale();
  float glowK=1./(2.*POLISH_GLOW_SIGMA*POLISH_GLOW_SIGMA*dpi*dpi), bloomK=1./(2.*POLISH_PEAK_SIGMA*POLISH_PEAK_SIGMA*dpi*dpi);
  float nearest=1e20, glow=0., bloom=0., weighted=0., weight=0.;
  float a=traceAt(begin).x, ea=emissiveStrength(amplitude(a));
  vec2 pa=vec2(binScreenX(float(begin)),spectrumY(a)*view.w);
  for(int i=begin;i<=end;i++){
    float b=traceAt(i+1).x, eb=emissiveStrength(amplitude(b));
    vec2 pb=vec2(binScreenX(float(i+1)),spectrumY(b)*view.w), ab=pb-pa;
    float t=saturate(dot(pixel-pa,ab)/max(.0001,dot(ab,ab)));
    vec2 off=pixel-pa-t*ab; float d2=dot(off,off), e=mix(ea,eb,t), g=exp(-d2*glowK)*e;
    nearest=min(nearest,d2); glow=max(glow,g); bloom=max(bloom,exp(-d2*bloomK)*e*e); weighted+=g*e; weight+=g;
    a=b; ea=eb; pa=pb;
  }
  return vec4(sqrt(nearest),glow,bloom,weight>1e-5 ? weighted/weight : 0.);
}
vec3 traceGlow(vec4 reach, bool above){
  vec3 hue=polishTint(palette(mix(.50,1.,reach.w))), bloomHue=polishTint(palette(.90));
  return (hue*reach.y*POLISH_GLOW+bloomHue*reach.z*POLISH_PEAK_BLOOM)*(above ? POLISH_ABOVE : 1.);
}
float traceCoverage(float d){ float sd=d-.5; float fw=max(fwidth(sd),1e-3); return saturate(.5-sd/fw); }

bool onGridLine(float t, float count, float spanPx){ return floor(t*spanPx)==floor(floor(t*count+.5)*spanPx/count+1e-3); }
vec3 gridLineColor(vec2 g){ float lit=pow(saturate(1.-(g.x+g.y)*.5),1.4)*3.; return GRID_BASE*(1.+lit)+GRID_SHEEN*lit; }
vec3 spectrumGrid(vec3 color, vec2 p, float seam){
  float gy=p.y/seam;
  if(options.x>1.5){
    if(onGridLine(p.x,10.,view.z)) color=max(color,OBSIDIAN_GRID*.75);
    if(onGridLine(gy,10.,view.w*seam)) color=max(color,OBSIDIAN_GRID);
  } else if(options.x>0. && (onGridLine(p.x,8.,view.z) || onGridLine(gy,4.,view.w*seam))) color+=gridLineColor(vec2(p.x,gy));
  return color;
}
// ---- waterfall history ----
vec4 meta(int k, int row){ return texelFetch(metadata, ivec2(k,row), 0); }
int ringRow(int age){ return (int(levels.z)-age+ROWS*4)%ROWS; }
struct HP { float value; float floorv; float range; float sigma; float time; float valid; };
float readHistory(float bin, int row, float footprint){
  bin=clamp(bin,source.z,source.w);
  int lo=int(floor(bin));
  float v=mix(texelFetch(history,ivec2(lo,row),0).x, texelFetch(history,ivec2(min(lo+1,int(source.w)),row),0).x, fract(bin));
  if(footprint>1.){
    int a=max(int(source.z),int(ceil(bin-footprint*.5))), b=min(int(source.w),int(floor(bin+footprint*.5)));
    for(int k=a;k<=b;k++) v=max(v,texelFetch(history,ivec2(k,row),0).x);
  }
  return v;
}
HP historyPoint(int age, float hz){
  HP p=HP(0.,0.,0.,0.,0.,0.);
  if(age<0 || float(age)>=levels.w) return p;
  int row=ringRow(age);
  vec4 map=meta(0,row), stamp=meta(1,row), stats=meta(2,row);
  map.x-=refHz.x;
  if(!(stats.w>.5 && timing.x-stamp.x<90. && hz>=map.x && hz<=map.x+map.y)) return p;
  float bin=source.z+(hz-map.x)/map.y*(source.w-source.z);
  p.floorv=calibratedFloor(stamp.y,stamp.z,stamp.w);
  p.range=max(.10,stats.x-p.floorv);
  p.sigma=max(1./255.,(stamp.w-stamp.y)/1.683);
  p.time=stamp.x;
  p.value=readHistory(bin,row,view.y/view.z/map.y*(source.w-source.z));
  p.valid=1.;
  return p;
}
int presentationAge(float uvy){ float seam=seamFraction(); return max(0,int(floor((uvy-seam)/(1.-seam)*timing.w))); }
vec3 waterfallColor(float hz, float uvy){
  int age=presentationAge(uvy);
  HP h=historyPoint(age,hz);
  if(h.valid<=0.) return vec3(0.);
  vec3 color=calmWaterfall(displayAmplitude(h.value,h.floorv,h.range));
  if(options.w>0.){
    int row=ringRow(age); vec4 map=meta(0,row); map.x-=refHz.x;
    float bin=source.z+(hz-map.x)/map.y*(source.w-source.z), pixelBin=view.y/view.z/map.y*(source.w-source.z);
    float inner=0., outer=0.;
    for(int k=-10;k<=10;k+=2){
      float nb=bin+float(k)*pixelBin;
      if(nb>=source.z && nb<=source.w){
        float energy=emissiveStrength(displayAmplitude(readHistory(nb,row,pixelBin),h.floorv,h.range)), d=abs(float(k))/dpiScale();
        inner=max(inner,energy*exp(-d*d/8.)); outer=max(outer,energy*exp(-d*d/32.));
      }
    }
    color+=palWfInner()*inner*.52+palWfOuter()*outer*.13;
  }
  return color;
}
vec3 waterfallPolish(vec3 color, float hz, float uvy, out vec3 grain){
  grain=vec3(0.);
  int g=presentationAge(uvy);
  HP here=historyPoint(g,hz), older=historyPoint(g+1,hz);
  if(here.valid<=0.) return color;
  float v=displayAmplitude(here.value,here.floorv,here.range);
  float under=smoothstep(here.floorv-.9*here.sigma,here.floorv+.3*here.sigma,here.value);
  vec3 grainHue=mix(vec3(.75),polishTint(paletteWf(.55)),POLISH_WF_GRAIN_HUE);
  grain=grainHue*under*under*(1.-smoothstep(.01,.08,v))*POLISH_WF_GRAIN;
  float keep=g>0 ? exp(-max(0.,here.time-older.time)/POLISH_WF_TAU)*older.valid : 0.;
  float vo=displayAmplitude(older.value,older.floorv,older.range);
  vec3 trail=calmWaterfall(vo*keep*smoothstep(.28,.60,vo));
  return mix(color,trail,smoothstep(0.,.015,polishLuma(trail)-polishLuma(color))*POLISH_WF_PERSIST);
}
vec3 seamSpill(vec3 color, vec2 p, float hz, vec4 reach){
  float dpi=dpiScale(), below=(p.y-seamFraction())*view.w/dpi, bin=traceBin(hz);
  if(below>=POLISH_SEAM_SPILL_PX || !(bin>=source.z && bin<=source.w)) return color;
  float fade=1.-smoothstep(0.,POLISH_SEAM_SPILL_PX,below), footprint=traceFootprint(), d=reach.x;
  float strength=amplitude(readTrace(bin,footprint).x), emit=emissiveStrength(strength);
  float bridged=options.z>0. ? smoothstep(.10,.35,strength) : 0.;
  vec3 light=vec3(0.);
  if(options.w>0.) light+=accentColor()*exp(-d*d/(18.*dpi*dpi))*(.18+.35*emit);
  light+=traceCoreColor(emit)*exp(-d*d/(9.*dpi*dpi))*(.30+.60*emit);
  light+=palette(.90)*exp(-d*d/(26.*dpi*dpi))*(.08+.18*emit);
  light+=traceGlow(reach,false);
  if(options.z>0.) light+=deepColor()*texture(bloomTex,vec2(p.x,1.)).x*1.5*field.z*1.4;
  light*=(1.-bridged)*traceShown();
  if(options.z>0.){
    float spread=0., total=0.;
    for(int k=-3;k<=3;k++){
      float w=exp(-float(k*k)/4.5), b=traceBin(hz+float(k)*2.*dpi*view.y/view.z);
      if(b>=source.z && b<=source.w) spread+=w*emissiveStrength(amplitude(readTrace(b,footprint).x));
      total+=w;
    }
    light+=polishTint(palette(.90))*(spread/total)*exp(-below*below/(2.*3.5*3.5))*POLISH_SEAM_PEAK*(1.-.7*bridged);
  }
  return 1.-(1.-saturate3(color))*(1.-saturate3(light*fade));
}
vec3 containedEnergy(vec3 color, vec2 p, float value, float traceY){
  float floorY=spectrumY(levels.x);
  if(options.z<=0. || !rxPassbandContains(p.x*view.z) || value<=levels.x || p.y<traceY || p.y>floorY || traceY>=floorY) return color;
  float strength=amplitude(value), pressure=saturate((floorY-p.y)/max(1./view.w,floorY-traceY));
  vec3 unlit=color;
  vec3 body=palette(strength*(.85+.18*pressure));
  float density=saturate(sqrt(strength)*(.70+.34*pressure));
  color=mix(color,body,density);
  float belowTrace=(p.y-traceY)*view.w/dpiScale(), emitted=emissiveStrength(strength);
  color+=palCore(emitted)*exp(-belowTrace*belowTrace/3.)*emitted*.44;
  if(options.w>0.) color+=palBloom()*exp(-belowTrace*belowTrace/55.)*emitted*.22;
  color+=palBloom()*exp(-belowTrace*belowTrace/220.)*strength*.10;
  color+=palBloom()*density*strength*(.12+.16*pressure);
  return mix(unlit,color,tsSignalGate(strength));
}
// Ice glass: ONE pane from the top of the spectrum through the seam to the
// bottom of the waterfall, one strength top to bottom, no edge lines. SSB-aware: in USB / LSB the glass starts AT the
// carrier hairline and runs to the far filter wall; in CW / AM / FM it is centred, exactly as the filter is.
// effects.w = the operator's Glass opacity (1 = 100%, as designed; 0..3).
vec3 rxChamber(vec3 color, vec2 p){
  float wallL=effects.y, wallR=effects.z, carrier=marker.x/view.y*view.z, px=p.x*view.z, dpi=dpiScale();
  if(!(wallR>wallL)) return color;
  float width=wallR-wallL;
  bool usb=carrier<=wallL+width*.2, lsb=carrier>=wallR-width*.2, ssb=(usb||lsb) && !(usb&&lsb);
  float left=ssb && usb ? min(wallL,carrier) : wallL, right=ssb && lsb ? max(wallR,carrier) : wallR;
  if(ssb && usb && !themeFrosted()) left=max(left,carrier+1.*dpi);      // keep clear of the carrier hairline
  if(ssb && lsb && !themeFrosted()) right=min(right,carrier-1.*dpi);
  if(px<left || px>=right) return color;
  vec3 hue=accentColor(); hue/=max(.02,max(hue.r,max(hue.g,hue.b)));
  float k=max(0.,effects.w);
  vec3 ice=mix(vec3(.74,.62,1.),hue,.30);           // purple-tinted ice
  color=mix(color,ice,min(.60,.07*k));
  color+=vec3(.92,.86,1.)*.012*k;
  return color;
}

void main(){
  vec2 p=uv; vec2 pixel=vec2(p.x*view.z, p.y*view.w);
  float seam=seamFraction(), dpi=dpiScale(), px=p.x*view.z;
  bool contained=rxPassbandContains(px);
  float hz=view.x+p.x*view.y;
  vec3 color=tsLayout.w>0. ? vec3(1.,2.,1.)/255. : vec3(2.,3.,4.)/255.;
  float traceBinHere=traceBin(hz);
  vec4 reach=vec4(1e20,0.,0.,0.);
  if(traceBinHere>=source.z && traceBinHere<=source.w && (p.y-seam)*view.w/dpi<POLISH_SEAM_SPILL_PX)
    reach=motionTraceReach(pixel,traceBinHere,POLISH_REACH_PX*dpi);
  if(p.y>=seam){
    vec3 grain;
    color=max(waterfallPolish(waterfallColor(hz,p.y),hz,p.y,grain),color)+grain;
    color=seamSpill(color,p,hz,reach);
  } else {
    color=spectrumGrid(color,p,seam);
    vec2 euv=vec2(p.x,p.y/seam);
    vec4 e=texture(energyTex,euv), b=texture(bloomTex,euv);
    float depth=1.-euv.y;
    if(options.z>0.){
      color=mix(color,palette(saturate(e.x*.92)),smoothstep(0.,1.,saturate(e.x*1.35))*traceShown());
      color+=deepColor()*b.x*field.z*2.4*traceShown();
      vec4 haze=texture(bloomTex,vec2(euv.x,mix(euv.y,1.,.55))), hazeLow=texture(bloomTex,vec2(euv.x,mix(euv.y,1.,.85)));
      float floorWeight=(1.-depth)*(1.-depth);
      color+=deepColor()*(haze.x*.9+hazeLow.x*.6)*field.z*floorWeight*1.4*traceShown();
      color+=accentColor()*(haze.y*.35)*field.z*floorWeight*traceShown();
    }
    float bin=traceBinHere;
    if(bin>=source.z && bin<=source.w){
      float footprint=traceFootprint();
      vec4 s=readTrace(bin,footprint);
      float traceY=spectrumY(s.x), dist=reach.x, strength=amplitude(s.x), floorY=spectrumY(levels.x);
      if(options.z>0. && !contained && s.x>levels.x && p.y>traceY && p.y<floorY && traceY<floorY){
        vec3 unfilled=color;
        float pressure=saturate((floorY-p.y)/max(1./view.w,floorY-traceY));
        color=mix(color,palette(strength*(.80+.16*pressure)),saturate(sqrt(strength)*(.58+.28*pressure)));
        float belowTrace=(p.y-traceY)*view.w/dpi, emitted=emissiveStrength(strength);
        color+=palCore(emitted)*exp(-belowTrace*belowTrace/3.)*emitted*.36;
        color+=palBloom()*exp(-belowTrace*belowTrace/220.)*strength*.08;
        color+=polishTint(palette(strength*.80))*pow(pressure,1.6)*smoothstep(.08,.30,strength)*POLISH_FILL;
        color=mix(unfilled,color,tsSignalGate(strength));
      }
      color=containedEnergy(color,p,s.x,traceY);
      float emit=emissiveStrength(strength);
      vec3 core=traceCoreColor(emit);
      if(options.w>0.) color+=accentColor()*exp(-dist*dist/(18.*dpi*dpi))*(.18+.35*emit)*traceShown();
      float halo=dist;
      dist-=POLISH_TRACE_WEIGHT*dpi*(.15+.85*emit);
      color+=core*exp(-halo*halo/(9.*dpi*dpi))*(.30+.60*emit)*traceShown();
      color+=palBloom()*exp(-halo*halo/(26.*dpi*dpi))*(.08+.18*emit)*traceShown();
      color+=traceGlow(reach,p.y<traceY)*traceShown();
      color=mix(color,core,traceCoverage(dist)*(.30+.55*emit)*traceShown());
      if(timing.y>0.){ float peakY=spectrumY(s.y); color+=palPeak()*saturate(1.-abs(p.y-peakY)*view.w/dpi)*tsSignalGate(amplitude(s.y)); }
      // seam bridge: a strong column melts into the newest waterfall row across the seam
      float bridgeZone=(seam-p.y)*view.w/(10.*dpi), bridge=smoothstep(.10,.35,strength);
      float strip=saturate((p.y-floorY)/max(1./view.w,seam-floorY));
      bool bridging=options.z>0. && bridgeZone<1. && bridge>0. && tsLayout.w<=0., rising=options.z>0. && strip>0. && bridge<1. && tsLayout.w<=0.;
      vec3 newestRow=vec3(0.);
      if(bridging || rising) newestRow=waterfallColor(hz,seam);
      if(bridging){ float t=1.-saturate(bridgeZone); color=mix(color,mix(palette(strength),newestRow,t),bridge*t); }
      if(rising) color=1.-(1.-saturate3(color))*(1.-saturate3(newestRow*strip*strip*POLISH_SEAM_FADE*(1.-bridge)));
    }
  }
  color=rxChamber(color,p);
  // TS scope: the grid's bottom line IS the divider: one solid device pixel at the seam, drawn over everything,
  // so no fill, glow or glass crosses it (tsSeamLine).
  if(tsLayout.w>0. && p.y<seam && (seam-p.y)*view.w<=dpi) color=OBSIDIAN_GRID_FLOOR;
  float carrier=marker.x/view.y*view.z;
  float markerLine=1.-smoothstep(.4*dpi,1.*dpi,abs(px-carrier));
  if(!themeFrosted()){                       // the TS scope's glass meets the carrier: no carrier line
    color=mix(color,traceCoreColor(0.),markerLine);
    color=mix(color,mix(traceCoreColor(0.),palette(.85),.60),markerLine);   // glass style: a sharp bright hairline
  }
  outColor=vec4(saturate3(color),1.);
}`;

/* ---------- renderer ---------- */
class ScopeGL {
  constructor(canvas) {
    this.cv = canvas;
    const gl = canvas.getContext("webgl2", { antialias: false, premultipliedAlpha: false });
    this.gl = gl;
    if (!gl) { this.failed = "WebGL2 not available"; return; }
    this.floatTargets = !!gl.getExtension("EXT_color_buffer_float");
    const comp = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
    const prog = (fs) => { const p = gl.createProgram(); gl.attachShader(p, comp(gl.VERTEX_SHADER, SG_VS)); gl.attachShader(p, comp(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)); return p; };
    try {
      this.pEnergy = prog(SG_ENERGY); this.pBloom = prog(SG_BLOOM); this.pComp = prog(SG_COMPOSITE);
    } catch (e) { this.failed = e.message; console.error(e); return; }
    this.quad = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const tex = (w, h, ifmt, fmt, type, filter = gl.NEAREST) => {
      const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, ifmt, w, h, 0, fmt, type, null);
      return t;
    };
    this.tex = tex;
    this.tPal = tex(1024, 2, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, gl.LINEAR);
    this.tMeta = tex(4, SG.ROWS, gl.RGBA32F, gl.RGBA, gl.FLOAT);
    this.metaRow = new Float32Array(16);
    this.paletteDirty = true;
    this.n = 0; this.ring = -1; this.rowsFilled = 0;
    this.t0 = performance.now();
  }
  ensureBins(n) {
    if (n === this.n) return;
    const gl = this.gl;
    this.n = n;
    this.tTrace = this.tex(n, 1, gl.RGBA32F, gl.RGBA, gl.FLOAT);
    this.tHist = this.tex(n, SG.ROWS, gl.R32F, gl.RED, gl.FLOAT);
    this.spring = new Float32Array(n * 4);
    this.vel = new Float32Array(n);
    this.springInit = false;
    this.ring = -1; this.rowsFilled = 0;
  }
  ensureTargets(w, h) {
    if (w === this.tw && h === this.th) return;
    const gl = this.gl;
    this.tw = w; this.th = h;
    const fmt = this.floatTargets ? [gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT] : [gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE];
    const target = (tw, th) => {
      const t = this.tex(tw, th, fmt[0], fmt[1], fmt[2], gl.LINEAR), f = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, f); gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
      gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
      return { t, f, w: tw, h: th };
    };
    const eh = Math.max(1, Math.round(h * SG.SEAM));
    this.energy = [target(w, eh), target(w, eh)];
    this.qw = Math.max(1, w >> 2); this.qh = Math.max(1, eh >> 2);
    this.bloomA = target(this.qw, this.qh); this.bloomB = target(this.qw, this.qh);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  }

  /** One new IQ spectrum frame (dBFS bins, lowest first, centred on loHz). */
  push(binsDb, loHz, spanHz, now) {
    if (this.failed) return;
    const gl = this.gl, n = binsDb.length;
    this.ensureBins(n);
    const raw = new Float32Array(n);
    for (let i = 0; i < n; i++) raw[i] = toRaw(binsDb[i]);
    // per-row calibration stats (p20, median, p80, white = p97)
    const s = Float32Array.from(raw).sort(), q = (f) => s[Math.min(n - 1, Math.floor(f * n))];
    const p20 = q(0.2), med = q(0.5), p80 = q(0.8), p97 = q(0.97);
    this.stats = { p20, med, p80, p97 };
    // history row + its metadata
    this.ring = (this.ring + 1) % SG.ROWS;
    this.rowsFilled = Math.min(SG.ROWS, this.rowsFilled + 1);
    gl.bindTexture(gl.TEXTURE_2D, this.tHist);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.ring, n, 1, gl.RED, gl.FLOAT, raw);
    const t = (now - this.t0) / 1000;
    // white: the row's P97, lifted to keep at least 30 dB of range so a quiet row is not stretched to white
    this.metaRow.set([loHz - spanHz / 2, spanHz, 0, 0, t, p20, med, p80, Math.max(p97, med + 0.30), 0, 0, 1, 0, 0, 0, 0]);
    gl.bindTexture(gl.TEXTURE_2D, this.tMeta);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.ring, 4, 1, gl.RGBA, gl.FLOAT, this.metaRow);
    this.raw = raw; this.loHz = loHz; this.spanHz = spanHz;
  }

  /** Spring motion per bin (x height, y peak, z raw newest, w rise velocity), as the CPU trace. */
  stepSpring(dt) {
    if (!this.raw) return;
    const sp = this.spring, raw = this.raw, n = this.n;
    if (!this.springInit) { for (let i = 0; i < n; i++) { sp[i * 4] = sp[i * 4 + 1] = sp[i * 4 + 2] = raw[i]; } this.springInit = true; }
    const kUp = 1 - Math.exp(-dt / 0.035), kDown = 1 - Math.exp(-dt / 0.16), peakFall = dt * 0.06;
    for (let i = 0; i < n; i++) {
      const o = i * 4, x = sp[o], target = raw[i];
      const nx = x + (target - x) * (target > x ? kUp : kDown);
      const v = (nx - x) / Math.max(1e-3, dt);
      sp[o] = nx;
      sp[o + 1] = Math.max(sp[o + 1] - peakFall, nx);
      sp[o + 2] = target;
      sp[o + 3] = Math.max(0, v);
    }
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.tTrace);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, n, 1, gl.RGBA, gl.FLOAT, sp);
  }

  _uniforms(p, u) {
    const gl = this.gl;
    for (const [k, v] of Object.entries(u)) {
      const loc = gl.getUniformLocation(p, k); if (!loc) continue;
      if (Array.isArray(v)) gl.uniform4f(loc, v[0], v[1], v[2], v[3]); else gl.uniform1i(loc, v);
    }
  }
  _quad(p) {
    const gl = this.gl, a = gl.getAttribLocation(p, "p");
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
  _bind(unit, t) { const gl = this.gl; gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, t); return unit; }

  render(state, dt) {
    if (this.failed || !this.raw) return;
    const gl = this.gl, cv = this.cv, dpr = window.devicePixelRatio || 1;
    const W = Math.round(cv.clientWidth * dpr), H = Math.round(cv.clientHeight * dpr);
    if (!W || !H) return;
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    this.ensureTargets(W, H);
    if (this.paletteDirty) { gl.bindTexture(gl.TEXTURE_2D, this.tPal); gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 1024, 2, gl.RGBA, gl.UNSIGNED_BYTE, SG.lut()); this.paletteDirty = false; }
    this.stepSpring(dt);
    const { p20, med, p80 } = this.stats;
    const sigma = Math.max(1 / 255, (p80 - p20) / 1.683), reveal = (clamp(SG.level, -30, 30) + 30) / 60;
    const floor = (med + 1.5 * sigma) * (1 - reveal) + (p20 - 0.75 * sigma) * reveal;
    const viewLeft = state.viewLeft, span = state.span;
    const t = (performance.now() - this.t0) / 1000;
    const wfRows = Math.min(this.rowsFilled, Math.round(H * (1 - SG.SEAM)));
    const common = {
      view: [0, span, W, H],
      source: [this.loHz - this.spanHz / 2 - viewLeft, this.spanHz, 0, this.n - 1],
      levels: [floor, floor + 0.45, this.ring, wfRows],
      marker: [state.carrier - viewLeft, 0, 0, 1],
      options: SG.style === "TS" ? [2, 0, 1, 0] : [1, 0, 1, 1],
      tsLayout: [0, 0, 0, SG.style === "TS" ? 2 : 0],
      timing: [t, state.peak ? 1 : 0, dpr, wfRows],
      displaySettings: [SG.level, 0, 0, 0],
      effects: [0, ((state.rxLo - viewLeft) / span) * W, ((state.rxHi - viewLeft) / span) * W, SG.glass / 100],
      field: [dt, 1.1, 0.55, Math.round(H * SG.SEAM)],
      refHz: [viewLeft, 0, 0, 0],
    };
    // the shaders work in display-relative Hz: view.x = 0, so every Hz below is relative to viewLeft
    // ---- ENERGY (ping-pong) ----
    const [src, dst] = this.energy;
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.f); gl.viewport(0, 0, dst.w, dst.h);
    gl.useProgram(this.pEnergy);
    this._uniforms(this.pEnergy, { ...common, view: [0, span, W, dst.h], energyTex: this._bind(0, src.t), motionTrace: this._bind(1, this.tTrace), paletteLut: this._bind(2, this.tPal) });
    this._quad(this.pEnergy);
    this.energy = [dst, src];
    // ---- BLOOM (quarter size, separable) ----
    gl.useProgram(this.pBloom);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.bloomA.f); gl.viewport(0, 0, this.qw, this.qh);
    this._uniforms(this.pBloom, { src: this._bind(0, dst.t), blur: [1 / this.qw, 1 / this.qh, 1, 0] }); this._quad(this.pBloom);
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.bloomB.f);
    this._uniforms(this.pBloom, { src: this._bind(0, this.bloomA.t), blur: [1 / this.qw, 1 / this.qh, 0, 1] }); this._quad(this.pBloom);
    // ---- COMPOSITE ----
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, W, H);
    gl.useProgram(this.pComp);
    this._uniforms(this.pComp, {
      ...common,
      paletteLut: this._bind(0, this.tPal), motionTrace: this._bind(1, this.tTrace), history: this._bind(2, this.tHist),
      metadata: this._bind(3, this.tMeta), energyTex: this._bind(4, dst.t), bloomTex: this._bind(5, this.bloomB.t),
    });
    this._quad(this.pComp);
  }
}

