// PCB parts for Anatomy figures: boards, copper, pads, vias and the packages
// that sit on them, built from the same isometric kit (kit/anatomy/iso-kit.mjs).
//
// Two ways in:
//   boardFigure(dump, options)   a real KiCad board, from scripts/kicad/board_dump.py
//   the part builders below      an invented board for an idea: call chip(), gullwing(),
//                                header() ... with your own positions in millimetres
//
// Coordinates you pass are millimetres in KiCad's frame (x right, y down from the
// board's top-left corner). frameOf() scales them into world units on Anatomy's
// floor, whose handedness matches KiCad's top view, so nothing is mirrored.

import * as k from "./anatomy/iso-kit.mjs";
import * as G from "./anatomy/lathe.mjs";

// ---------------------------------------------------------------------------
// frame and geometry helpers

// tops: optional [[x, y, z], ...] in mm, the highest points of the parts; without them every corner is
// given `tallest` of headroom, which wastes space when only one part is tall
export function frameOf(size, { span = 260, width = 640, height = 420, azimuth = 38, elevation = 32, pad = 34, tallest = 9, thickness = 1.6, headroom = 0, tops = null } = {}) {
  const [W, H] = size;
  const K = span / Math.max(W, H);
  const at = ([x, y], z = 0) => [x * K, y * K, z * K];   // Anatomy's floor is screen-handed like KiCad's: no flip, or the board mirrors
  const floor = [[0, 0], [W, 0], [W, H], [0, H]];
  const points = floor.flatMap((p) => [at(p, -thickness - 0.6), at(p, 0.2)]);
  if (tops) points.push(...tops.map(([x, y, z]) => at([x, y], z + 0.3)));
  else points.push(...floor.map((p) => at(p, tallest)));
  const P = k.fitProjection(points, width, height, { pad, azimuth, elevation, headroom });
  return { K, W, H, at, P, width, height };
}

export function hull(points) {
  const pts = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (pts.length < 3) return pts;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [], upper = [];
  for (const p of pts) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop(); lower.push(p); }
  for (const p of pts.reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop(); upper.push(p); }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

// a rectangle (or rounded rectangle) centred at c, size [w, h], rotated by a degrees as KiCad does (screen ccw, y down)
export function rectMm(c, [w, h], angle = 0, r = 0, steps = 3) {
  const t = (angle * Math.PI) / 180, cos = Math.cos(t), sin = Math.sin(t);
  const local = r > 0 ? k.roundedPlan({ x: -w / 2, y: -h / 2, w, d: h, r: Math.min(r, w / 2, h / 2) }, steps) : [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
  return local.map(([x, y]) => [c[0] + x * cos + y * sin, c[1] - x * sin + y * cos]);
}

export const circleMm = (c, r, steps = 24) => Array.from({ length: steps }, (_, i) => [c[0] + r * Math.cos((i / steps) * Math.PI * 2), c[1] + r * Math.sin((i / steps) * Math.PI * 2)]);

// a track segment as a stadium (both ends round)
export function stadiumMm(a, b, width, steps = 8) {
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1e-6;
  const nx = -dy / len, ny = dx / len, r = width / 2, base = Math.atan2(ny, nx);
  const out = [];
  for (let i = 0; i <= steps; i++) { const t = base + (Math.PI * i) / steps; out.push([b[0] + r * Math.cos(t), b[1] + r * Math.sin(t)]); }
  for (let i = 0; i <= steps; i++) { const t = base + Math.PI + (Math.PI * i) / steps; out.push([a[0] + r * Math.cos(t), a[1] + r * Math.sin(t)]); }
  return out;
}

const centroid = (pts) => [pts.reduce((s, p) => s + p[0], 0) / pts.length, pts.reduce((s, p) => s + p[1], 0) / pts.length];
const tenth = (v) => Math.round(v * 10) / 10;
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

// world ring (floor points) from millimetre points
const ringOf = (F, pts) => pts.map((p) => F.at(p).slice(0, 2));

// a solid prism from millimetre points, z0..z1 in mm
export function prism(F, pts, z0, z1, style = {}, { bevel = 0 } = {}) {
  const ring = ringOf(F, hull(pts));
  return k.solidSvg(k.extrude(ring, z0 * F.K, (z1 - z0) * F.K, F.P, { convex: true, bevel: bevel * F.K }), style);
}

// a flat region on a face at height z (mm), with optional holes, filled evenodd
export function flatSvg(F, rings, z, { cls = "", tone = "faint", net } = {}) {
  const d = rings.map((r) => "M" + r.map((p) => { const [sx, sy] = k.iso(F.at(p, z), F.P); return `${tenth(sx)} ${tenth(sy)}`; }).join("L") + "Z").join("");
  return `<path class="pcb-flat ${cls}" data-tone="${tone}"${net ? ` data-net="${esc(net)}"` : ""} fill-rule="evenodd" d="${d}"/>`;
}

// a matrix that lays text flat on the board top at `at` (mm), running left to right for the reader
// whatever the camera: of the four floor directions it takes the one that points most to the right on
// screen, and the perpendicular that keeps the glyphs unmirrored
export function readableOnTop(F, at, z = 0.04) {
  const o = k.iso(F.at(at, z), F.P);
  const image = ([dx, dy]) => { const p = k.iso(F.at([at[0] + dx, at[1] + dy], z), F.P); return [p[0] - o[0], p[1] - o[1]]; };
  const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  const a = dirs.sort((p, q) => image(q)[0] - image(p)[0])[0];
  let ex = image(a), ey = image([-a[1], a[0]]);
  if (ex[0] * ey[1] - ex[1] * ey[0] < 0) ey = image([a[1], -a[0]]);
  const n = (v) => { const l = Math.hypot(v[0], v[1]) || 1; return [v[0] / l, v[1] / l]; };
  const scale = Math.hypot(...image(a));
  const [x1, y1] = n(ex), [x2, y2] = n(ey);
  const s2 = Math.hypot(...ey) / scale;
  const f = (v) => Math.round(v * 1000) / 1000;
  return `matrix(${f(x1)} ${f(y1)} ${f(x2 * s2)} ${f(y2 * s2)} ${f(o[0])} ${f(o[1])})`;
}

// ---------------------------------------------------------------------------
// the board itself

export function boardSlab(F, outline, thickness = 1.6, { layers = 2, holes = [] } = {}) {
  const outer = outline[0];
  const ring = ringOf(F, outer);
  const z0 = -thickness * F.K;
  const paths = k.extrude(ring, z0, thickness * F.K, F.P, { convex: false });
  const seams = [];
  for (let i = 0; i < layers; i++) {
    const z = z0 + (thickness * F.K * (i === 0 ? 0.08 : i === layers - 1 ? 0.92 : i / (layers - 1)));
    seams.push(k.lineSvg(k.ringSeam(ring, z, F.P), { tone: "faint", className: "pcb-foil" }));
  }
  const halo = `<path class="iso-halo" d="${paths.fill}"/>`;
  return halo + k.solidSvg(paths, { tone: "mid", className: "pcb-board", inner: seams.join("") });
}

export function holeSvg(F, at, drill, plated = true) {
  const rim = circleMm(at, drill / 2, 28);
  const ring = plated ? circleMm(at, drill / 2 + 0.35, 28) : null;
  return (ring ? flatSvg(F, [ring], 0.01, { cls: "pcb-copper pcb-pad-face", tone: "mid" }) : "") + flatSvg(F, [rim], 0.02, { cls: "pcb-hole", tone: "lo" });
}

// ---------------------------------------------------------------------------
// copper on the surface

export function trackSvg(F, t, { flow = false } = {}) {
  const pts = stadiumMm(t.from, t.to, t.width);
  const body = flatSvg(F, [pts], 0.02, { cls: "pcb-copper pcb-track", tone: "faint", net: t.net });
  const a = k.iso(F.at(t.from, 0.03), F.P), b = k.iso(F.at(t.to, 0.03), F.P);
  const centre = flow ? `<path class="pcb-flow" data-net="${esc(t.net)}" data-free="" d="M${tenth(a[0])} ${tenth(a[1])}L${tenth(b[0])} ${tenth(b[1])}"/>` : "";
  return body + centre;
}

export function padSvg(F, p) {
  const r = p.shape === "roundrect" ? Math.min(p.size[0], p.size[1]) * 0.25 : p.shape === "oval" ? Math.min(p.size[0], p.size[1]) / 2 : 0;
  const pts = p.shape === "circle" ? circleMm(p.at, p.size[0] / 2) : rectMm(p.at, p.size, p.angle || 0, r);
  let svg = `<g class="pcb-pad" data-net="${esc(p.net || "")}">` + flatSvg(F, [pts], 0.035, { cls: "pcb-copper pcb-pad-face", tone: "mid" });
  if (p.drill) svg += flatSvg(F, [circleMm(p.at, p.drill / 2, 20)], 0.04, { cls: "pcb-hole", tone: "lo" });
  return svg + "</g>";
}

export function viaSvg(F, v) {
  return `<g class="pcb-via" data-net="${esc(v.net || "")}">` + flatSvg(F, [circleMm(v.at, v.size / 2, 18)], 0.03, { cls: "pcb-copper", tone: "mid" }) +
    flatSvg(F, [circleMm(v.at, v.drill / 2, 14)], 0.04, { cls: "pcb-hole", tone: "lo" }) + "</g>";
}

// ---------------------------------------------------------------------------
// packages. Every builder takes millimetres and returns SVG for one part,
// drawn bottom up, and is meant to be sorted with others by depthOf(centre).

const style = (cls, tone = "hi", extra = {}) => ({ tone, crease: "faint", className: cls, ...extra });

// two-terminal chip: body plus a metal cap at each end
export function chip(F, { pads, body, height = 0.45, kind = "chip", lit = false }) {
  const [a, b] = [pads[0].at, pads[pads.length - 1].at];
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len, vx = -uy, vy = ux;
  const pts = body && body.length >= 3 ? hull(body) : null;
  let L = len + Math.max(pads[0].size[0], pads[0].size[1]) * 0.6, Wd = Math.min(pads[0].size[0], pads[0].size[1]);
  if (pts) {
    const us = pts.map((p) => (p[0] - a[0]) * ux + (p[1] - a[1]) * uy), vs = pts.map((p) => (p[0] - a[0]) * vx + (p[1] - a[1]) * vy);
    L = Math.max(...us) - Math.min(...us); Wd = Math.max(...vs) - Math.min(...vs);
  }
  const c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const box = (u0, u1, w) => [[u0, -w / 2], [u1, -w / 2], [u1, w / 2], [u0, w / 2]].map(([u, v]) => [c[0] + ux * u + vx * v, c[1] + uy * u + vy * v]);
  const cap = L * 0.2;
  const out = [];
  out.push(prism(F, box(-L / 2 + cap, L / 2 - cap, Wd * 0.98), 0.02, height * 0.98, style(kind === "led" ? "pcb-lens" : "pcb-body", "hi", { lit })));
  out.push(prism(F, box(-L / 2, -L / 2 + cap, Wd), 0, height, style("pcb-metal", "hi"), { bevel: 0.03 }));
  out.push(prism(F, box(L / 2 - cap, L / 2, Wd), 0, height, style("pcb-metal", "hi"), { bevel: 0.03 }));
  if (kind === "led") {
    const tip = box(L * 0.05, L * 0.18, Wd * 0.5);
    out.push(flatSvg(F, [tip], height + 0.005, { cls: "pcb-mark", tone: "mid" }));
  }
  return out.join("");
}

// gull-wing leaded package (SOIC, SOT-23, QFP): moulded body plus bent leads onto each pad
export function gullwing(F, { pads, body, height = 1.4, lit = false, pin1 = true }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const c = centroid(pts);
  const out = [];
  const standoff = Math.min(0.1, height * 0.1);
  const leads = [];
  for (const p of pads) {
    const dx = p.at[0] - c[0], dy = p.at[1] - c[1];
    // the lead runs along whichever body axis the pad sits off
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const halfX = (Math.max(...xs) - Math.min(...xs)) / 2, halfY = (Math.max(...ys) - Math.min(...ys)) / 2;
    const alongX = Math.abs(dx) / (halfX || 1) > Math.abs(dy) / (halfY || 1);
    const sgn = alongX ? Math.sign(dx) : Math.sign(dy);
    const edge = alongX ? c[0] + sgn * halfX : c[1] + sgn * halfY;
    const padLen = alongX ? Math.max(p.size[0], p.size[1]) : Math.max(p.size[0], p.size[1]);
    const padW = Math.min(p.size[0], p.size[1]);
    const lw = padW * 0.7, t = 0.15;
    const tip = (alongX ? p.at[0] : p.at[1]) + sgn * padLen * 0.3;
    const knee = (alongX ? p.at[0] : p.at[1]) - sgn * padLen * 0.2;
    const across = alongX ? p.at[1] : p.at[0];
    const box = (s0, s1) => {
      const lo = Math.min(s0, s1), hi = Math.max(s0, s1);
      return alongX ? [[lo, across - lw / 2], [hi, across - lw / 2], [hi, across + lw / 2], [lo, across + lw / 2]]
        : [[across - lw / 2, lo], [across + lw / 2, lo], [across + lw / 2, hi], [across - lw / 2, hi]];
    };
    const zTop = height * 0.45;
    leads.push({ depth: k.depthOf(F.at(p.at), F.P), svg:
      prism(F, box(knee, tip), 0, t, style("pcb-metal", "mid")) +
      prism(F, box(knee - sgn * t, knee), 0, zTop, style("pcb-metal", "mid")) +
      prism(F, box(edge, knee), zTop - t, zTop, style("pcb-metal", "mid")) });
  }
  const bodyDepth = k.depthOf(F.at(c), F.P);
  const behind = leads.filter((l) => l.depth < bodyDepth).sort((a, b) => a.depth - b.depth);
  const front = leads.filter((l) => l.depth >= bodyDepth).sort((a, b) => a.depth - b.depth);
  out.push(...behind.map((l) => l.svg));
  out.push(prism(F, pts, standoff, height, style("pcb-body", "hi", { lit }), { bevel: Math.min(0.15, height * 0.12) }));
  if (pin1 && pads.length) {
    const p1 = pads[0].at;
    const towards = [c[0] + (p1[0] - c[0]) * 0.62, c[1] + (p1[1] - c[1]) * 0.62];
    const r = Math.min(0.35, Math.max(0.15, Math.hypot(p1[0] - c[0], p1[1] - c[1]) * 0.08));
    out.push(k.lineSvg(k.ring(...F.at(towards).slice(0, 2), r * F.K, height * F.K + 0.05, F.P, 18), { tone: "mid", className: "pcb-dimple" }));
  }
  out.push(...front.map((l) => l.svg));
  return out.join("");
}

// leadless package (QFN, DFN): low body, the pads around it show the terminals
export function nolead(F, { pads, body, height = 0.85, lit = false }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const c = centroid(pts);
  const p1 = pads[0]?.at || c;
  const towards = [c[0] + (p1[0] - c[0]) * 0.6, c[1] + (p1[1] - c[1]) * 0.6];
  return prism(F, pts, 0.05, height, style("pcb-body", "hi", { lit }), { bevel: 0.08 }) +
    k.lineSvg(k.ring(...F.at(towards).slice(0, 2), 0.2 * F.K, height * F.K + 0.05, F.P, 16), { tone: "mid" });
}

// pin header: plastic carrier and gold square posts with pointed tips
export function header(F, { pads, body, height = 8.5, base = 2.5 }) {
  const pts = hull(body && body.length >= 3 ? body : pads.flatMap((p) => rectMm(p.at, [2.54, 2.54])));
  const out = [prism(F, pts, 0, base, style("pcb-plastic", "hi"), { bevel: 0.1 })];
  const posts = [...pads].sort((a, b) => k.depthOf(F.at(a.at), F.P) - k.depthOf(F.at(b.at), F.P));
  for (const p of posts) {
    out.push(prism(F, rectMm(p.at, [0.64, 0.64]), base, height - 0.5, style("pcb-gold", "hi")));
    out.push(prism(F, rectMm(p.at, [0.36, 0.36]), height - 0.5, height, style("pcb-gold", "hi")));
  }
  return out.join("");
}

// tactile switch: square base, metal cover with a seam, round actuator
export function tactSwitch(F, { pads, body, height = 2.0, pressed = 0, lit = false }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const c = centroid(pts);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const size = Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const square = rectMm(c, [size * 0.92, size * 0.92], 0, size * 0.08);
  const out = [];
  for (const p of pads) out.push(prism(F, rectMm(p.at, [Math.min(...p.size) * 0.7, Math.min(...p.size) * 0.7]), 0, 0.25, style("pcb-metal", "mid")));
  out.push(prism(F, square, 0, height * 0.55, style("pcb-plastic", "hi"), { bevel: 0.1 }));
  out.push(prism(F, rectMm(c, [size * 0.86, size * 0.86], 0, size * 0.06), height * 0.55, height * 0.62, style("pcb-metal", "hi")));
  const r = size * 0.22;
  const top = height - pressed * 0.25;
  out.push(k.solidSvg(k.cylinder(...F.at(c).slice(0, 2), r * F.K, height * 0.62 * F.K, (top - height * 0.62) * F.K, F.P, 36, 0.06 * F.K), style("pcb-cap", "hi", { lit })));
  return out.join("");
}

// box-shaped connector or module (USB receptacle, JST, crystal can, inductor)
export function block(F, { pads, body, height = 3, cls = "pcb-metal", seams = 1, lit = false, ring = false }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const world = ringOf(F, pts);
  const inner = [];
  for (let i = 1; i <= seams; i++) inner.push(k.lineSvg(k.ringSeam(world, (height * i) / (seams + 1) * F.K, F.P), { tone: "faint" }));
  let svg = k.solidSvg(k.extrude(world, 0, height * F.K, F.P, { convex: true, bevel: 0.12 * F.K }), style(cls, "hi", { lit, inner: inner.join("") }));
  if (ring) {
    const c = centroid(pts), xs = pts.map((p) => p[0]);
    svg += k.lineSvg(k.ring(...F.at(c).slice(0, 2), (Math.max(...xs) - Math.min(...xs)) * 0.3 * F.K, height * F.K + 0.05, F.P, 32), { tone: "mid" });
  }
  return svg;
}

// USB-C receptacle: a stadium-section shell lying along the plug axis, opening toward the nearest board edge
export function usbC(F, { pads, body, height = 3.2, lit = false }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const c = centroid(pts);
  const edges = [[c[0], [-1, 0]], [F.W - c[0], [1, 0]], [c[1], [0, -1]], [F.H - c[1], [0, 1]]];
  const dir = edges.sort((a, b) => a[0] - b[0])[0][1];
  const along = pts.map((p) => (p[0] - c[0]) * dir[0] + (p[1] - c[1]) * dir[1]);
  const across = pts.map((p) => (p[0] - c[0]) * -dir[1] + (p[1] - c[1]) * dir[0]);
  const len = Math.max(...along) - Math.min(...along), wid = Math.max(...across) - Math.min(...across);
  const back = [c[0] + dir[0] * Math.min(...along), c[1] + dir[1] * Math.min(...along)];
  const o = F.at(back, height / 2);
  const tip = F.at([back[0] + dir[0], back[1] + dir[1]], height / 2);
  const a = G.unit3(G.sub3(tip, o));
  const u = G.unit3(G.cross3([0, 0, 1], a));
  const frame = G.frameOf(o, a, u, [0, 0, 1]);
  const r = (height / 2) * F.K, off = (wid - height) * F.K;
  const shell = G.stadium(r, r, off, 40).map(([x, y]) => [x - off / 2, y]);
  const paths = G.prismOf(frame, shell, 0, len * F.K, F.P);
  let svg = k.solidSvg(paths, style("pcb-metal", "hi", { lit }));
  if (G.dot3(a, G.viewOf(F.P)) > 0.05) {
    const at = (s, [x, y]) => k.iso([o[0] + a[0] * s + u[0] * x, o[1] + a[1] * s + u[1] * x, o[2] + y], F.P);
    const ring = (inset) => G.stadium(r - inset, r - inset, off, 40).map(([x, y]) => [x - off / 2, y]);
    const mouth = ring(0.35 * F.K).map((p) => at(len * F.K, p));
    const tongue = [[-off / 2 - r * 0.45, -r * 0.18], [off / 2 + r * 0.45, -r * 0.18], [off / 2 + r * 0.45, r * 0.18], [-off / 2 - r * 0.45, r * 0.18]].map((p) => at(len * F.K - 0.5 * F.K, p));
    const path = (list) => "M" + list.map(([x, y]) => `${tenth(x)} ${tenth(y)}`).join("L") + "Z";
    svg += `<path class="pcb-mouth" d="${path(mouth)}"/>` + `<path class="pcb-tongue" d="${path(tongue)}"/>`;
  }
  return svg;
}

// a frame along the line between two pads, raised to height z (mm), for parts that lie on their side
function sideFrame(F, a, b, z) {
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const o = F.at(mid, z);
  const axis = G.unit3(G.sub3(F.at(b, z), F.at(a, z)));
  const u = G.unit3(G.cross3([0, 0, 1], axis));
  return { frame: G.frameOf(o, axis, u, [0, 0, 1]), mid, half: (Math.hypot(b[0] - a[0], b[1] - a[1]) / 2) * F.K };
}

// a wire lead: up from a pad to height z, then along to the body end at s (world units along the frame)
function leadSvg(F, frame, pad, z, s, r) {
  const up = G.frameAlong(F.at(pad, 0), [0, 0, 1]);
  const bend = G.disc(0, z * F.K, r, up, F.P);
  const sPad = G.dot3(G.sub3(F.at(pad, z), frame.o), frame.a);
  const [s0, s1] = sPad < s ? [sPad, s] : [s, sPad];
  const run = G.disc(s0, s1, r, frame, F.P);
  return k.solidSvg(bend, style("pcb-metal", "mid")) + k.solidSvg(run, style("pcb-metal", "mid"));
}

// axial resistor, diode or capacitor lying on its side, raised on bent leads
export function axial(F, { pads, dims = {}, kind = "resistor", lit = false }) {
  const a = pads[0].at, b = pads[pads.length - 1].at;
  const L = dims.length || 6.3, D = dims.diameter || 2.5;
  const z = D / 2 + 0.6;
  const { frame, half } = sideFrame(F, a, b, z);
  const r = (D / 2) * F.K, s0 = (-L / 2) * F.K, s1 = (L / 2) * F.K, cap = Math.min(L * 0.18, 1.2) * F.K;
  const profile = [[s0, r * 0.82], [s0 + cap * 0.5, r, 1], [s0 + cap, r * 0.94], [s1 - cap, r * 0.94], [s1 - cap * 0.5, r, 1], [s1, r * 0.82]];
  const body = G.solidOf(profile, frame, F.P);
  const bands = kind === "diode" ? [s0 + cap * 1.2] : [s0 + cap * 1.4, s0 + cap * 2.1, s0 + cap * 2.8, s1 - cap * 1.5];
  const inner = bands.map((s) => k.lineSvg(G.arcOf(frame, s, r * 0.95, F.P), { tone: "mid" })).join("");
  const wire = 0.3 * F.K;
  const view = G.viewOf(F.P);
  const leads = [[a, s0], [b, s1]].map(([pad, s]) => ({ depth: G.dot3(F.at(pad, 0), view), svg: leadSvg(F, frame, pad, z, s, wire) }));
  leads.sort((x, y) => x.depth - y.depth);
  return leads[0].svg + k.solidSvg(body, style(kind === "diode" ? "pcb-body" : "pcb-axial", "hi", { lit, inner })) + leads[1].svg;
}

// ceramic disc capacitor standing on edge between its two leads
export function discCap(F, { pads, dims = {}, lit = false }) {
  const a = pads[0].at, b = pads[pads.length - 1].at;
  const D = dims.diameter || 5, T = dims.thickness || 2.5;
  const lift = 1.2, z = lift + D / 2;
  const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  const n = [-dy / len, dx / len];
  const frame = G.frameAlong(F.at(mid, z), G.unit3(G.sub3(F.at([mid[0] + n[0], mid[1] + n[1]], z), F.at(mid, z))));
  const face = G.disc((-T / 2) * F.K, (T / 2) * F.K, (D / 2) * F.K, frame, F.P, { bevel: 0.2 * F.K });
  const legs = [a, b].map((p) => k.solidSvg(G.disc(0, (lift + 0.6) * F.K, 0.28 * F.K, G.frameAlong(F.at(p, 0), [0, 0, 1]), F.P), style("pcb-metal", "mid")));
  return legs.join("") + k.solidSvg(face, style("pcb-disc", "hi", { lit }));
}

// moulded plastic housing (JST, Molex, terminal blocks): solid body with the mating cavity drawn on top
export function housing(F, { pads, body, height = 5, lit = false }) {
  const pts = hull(body && body.length >= 3 ? body : pads.map((p) => p.at));
  const c = centroid(pts);
  const inset = pts.map(([x, y]) => [c[0] + (x - c[0]) * 0.78, c[1] + (y - c[1]) * 0.7]);
  const top = height * F.K + 0.05;
  return k.solidSvg(k.extrude(ringOf(F, pts), 0, height * F.K, F.P, { convex: true, bevel: 0.12 * F.K }), style("pcb-housing", "hi", { lit,
    inner: k.lineSvg(k.onTop(ringOf(F, inset), top, F.P, true), { tone: "lo" }) }));
}

export function electrolytic(F, { body, height = 6, dims = {}, lens = false, lit = false }) {
  if (lens) {
    const pts = hull(body);
    const c = centroid(pts);
    const r = ((dims.diameter || 5) / 2) * F.K;
    const [x, y] = F.at(c);
    const can = k.solidSvg(k.cylinder(x, y, r * 1.08, 0, 1.0 * F.K, F.P, 40), style("pcb-lens", "hi", { lit }));
    const shaft = k.solidSvg(G.solidOf([[1.0 * F.K, r], [(height - (dims.diameter || 5) / 2) * F.K, r, 1], [height * F.K - r * 0.35, r * 0.75, 1], [height * F.K, 0, 1]],
      G.frameAlong([x, y, 0], [0, 0, 1]), F.P), style("pcb-lens", "hi", { lit }));
    return can + shaft;
  }
  return electrolyticCan(F, { body, height, dims });
}

function electrolyticCan(F, { body, height = 6, dims = {} }) {
  const pts = hull(body);
  const c = centroid(pts);
  const r = dims.diameter ? dims.diameter / 2 : Math.max(...pts.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) * 0.92;
  const [x, y] = F.at(c);
  const base = prism(F, rectMm(c, [r * 2.1, r * 2.1], 0, r * 0.15), 0, 0.6, style("pcb-plastic", "mid"));
  const can = k.solidSvg(k.cylinder(x, y, r * F.K, 0.6 * F.K, (height - 0.6) * F.K, F.P, 56, 0.12 * F.K), style("pcb-metal", "hi", {
    inner: k.lineSvg(k.sideArc(x, y, r * F.K, (height - 0.9) * F.K, F.P, 40), { tone: "faint" }),
  }));
  const vent = k.lineSvg(k.lineOnTop([x - r * F.K, y], [x + r * F.K, y], height * F.K, F.P) + k.lineOnTop([x, y - r * F.K], [x, y + r * F.K], height * F.K, F.P), { tone: "lo" });   // a scored vent runs rim to rim
  return base + can + vent;
}

// one footprint from a board dump, whatever its kind
export function partSvg(F, fp, options = {}) {
  const o = { ...fp, ...(options.override?.[fp.ref] || {}) };
  const lit = options.lit?.includes?.(fp.ref) || false;
  switch (o.kind) {
    case "chip": return chip(F, { pads: o.pads, body: o.body, height: o.height, lit });
    case "led": return chip(F, { pads: o.pads, body: o.body, height: o.height, kind: "led", lit });
    case "gullwing": case "dip": return gullwing(F, { pads: o.pads, body: o.body, height: o.height, lit });
    case "nolead": return nolead(F, { pads: o.pads, body: o.body, height: o.height, lit });
    case "header": case "socket": return header(F, { pads: o.pads, body: o.body, height: o.height });
    case "switch": return tactSwitch(F, { pads: o.pads, body: o.body, height: o.height, lit });
    case "connector": {
      const name = o.footprint || "";
      if (/USB_C/.test(name)) return usbC(F, { pads: o.pads, body: o.body, height: o.height, lit });
      const metal = /USB|DSUB|D-Sub|DB9|RJ45|SD|HDMI|DisplayPort|SMA|U\.FL/i.test(name);
      return metal ? block(F, { pads: o.pads, body: o.body, height: o.height, cls: "pcb-metal", seams: 1, lit })
        : housing(F, { pads: o.pads, body: o.body, height: o.height, lit });
    }
    case "crystal": return block(F, { pads: o.pads, body: o.body, height: o.height, cls: "pcb-metal", seams: 0, lit });
    case "inductor": return block(F, { pads: o.pads, body: o.body, height: o.height, cls: "pcb-body", seams: 0, ring: true, lit });
    case "electrolytic": return electrolytic(F, { body: o.body, height: o.height, dims: o.dims || {}, lens: !!o.dims?.lens, lit });
    case "axial": return axial(F, { pads: o.pads, dims: o.dims || {}, kind: /^D|Diode|DO-|SOD/.test(o.footprint?.split(":")[1] || "") ? "diode" : "resistor", lit });
    case "disc": return discCap(F, { pads: o.pads, dims: o.dims || {}, lit });
    case "to92": case "to220": return block(F, { pads: o.pads, body: o.body, height: o.height, cls: "pcb-body", seams: 0, lit });
    case "hole": case "fiducial": case "testpoint": case "edge": return "";
    default: return block(F, { pads: o.pads, body: o.body, height: o.height || 1, cls: "pcb-body", seams: 0, lit });
  }
}

// ---------------------------------------------------------------------------
// a whole board

// the azimuth that turns the largest edge connector's opening toward the reader (38 degrees off square on)
export function facingAzimuth(dump, fallback = 38) {
  const [W, H] = dump.size;
  const nearEdge = (f) => f.body.some(([x, y]) => x < 3 || y < 3 || x > W - 3 || y > H - 3);
  const edgeParts = dump.footprints.filter((f) => ["connector", "header", "socket"].includes(f.kind) && f.body?.length >= 3 && nearEdge(f));
  if (!edgeParts.length) return fallback;
  const area = (f) => { const xs = f.body.map((p) => p[0]), ys = f.body.map((p) => p[1]); return (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys)); };
  const f = edgeParts.sort((a, b) => area(b) - area(a))[0];
  const c = centroid(hull(f.body));
  const [, d] = [[c[0], [-1, 0]], [W - c[0], [1, 0]], [c[1], [0, -1]], [H - c[1], [0, 1]]].sort((a, b) => a[0] - b[0])[0];
  const a = (Math.atan2(d[1], d[0]) * 180) / Math.PI + 38;
  return ((a + 540) % 360) - 180;
}

export function boardFigure(dump, { width = 640, height = 420, span, azimuth = null, elevation = 32, pours = true, flow = true, silk = true, lit = [], override = {}, focus = null } = {}) {
  if (azimuth === null || azimuth === undefined || Number.isNaN(azimuth)) azimuth = facingAzimuth(dump);
  const tops = dump.footprints.flatMap((f) => {
    const h = (override[f.ref]?.height ?? f.height) || 0;
    const pts = f.body?.length >= 3 ? hull(f.body) : f.pads.map((p) => p.at);
    return pts.map(([x, y]) => [x, y, h + 3 + h * 0.2]);   // room for the lifted pose, so no part leaves the card
  });
  const F = frameOf(dump.size, { span: span || 300, width, height, azimuth, elevation, thickness: dump.thickness, tops, pad: 30 });
  const flat = [];
  if (pours) for (const z of dump.zones || []) flat.push(flatSvg(F, [z.outline, ...(z.holes || [])], 0.015, { cls: "pcb-pour", tone: "faint", net: z.net }));
  for (const t of dump.tracks) flat.push(trackSvg(F, t, { flow }));
  for (const fp of dump.footprints) for (const p of fp.pads) flat.push(padSvg(F, p));
  for (const v of dump.vias) flat.push(viaSvg(F, v));
  for (const fp of dump.footprints) {
    if (fp.kind === "hole") for (const p of fp.pads) flat.push(holeSvg(F, p.at, p.drill || p.size[0], p.plated));
  }
  if (silk) {
    for (const fp of dump.footprints) {
      if (["hole", "fiducial"].includes(fp.kind) || !fp.body?.length) continue;
      const pts = hull(fp.body);
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const at = [(Math.min(...xs) + Math.max(...xs)) / 2, Math.max(...ys) + 0.9];
      if (at[1] > dump.size[1] - 0.6) continue;
      flat.push(k.faceTextSvg(readableOnTop(F, at, 0.04), fp.ref, { size: 0.9 * F.K, tone: "faint", anchor: "middle" }).replace('class="iso-face-text"', `class="iso-face-text pcb-silk" data-ref="${esc(fp.ref)}"`));
    }
  }
  const parts = dump.footprints
    .filter((fp) => !["hole", "fiducial", "testpoint", "edge"].includes(fp.kind))
    .map((fp) => {
      const c = centroid(hull(fp.body?.length >= 3 ? fp.body : fp.pads.map((p) => p.at)));
      const nets = [...new Set(fp.pads.map((p) => p.net).filter(Boolean))];
      return { fp, depth: k.depthOf(F.at(c), F.P), svg: partSvg(F, fp, { lit, override }), nets, h: (override[fp.ref]?.height ?? fp.height) || 1 };
    })
    .sort((a, b) => a.depth - b.depth);
  const body = [
    boardSlab(F, dump.outline, dump.thickness, { layers: dump.layers }),
    `<g class="pcb-surface">${flat.join("")}</g>`,
    ...parts.map((p) => `<g class="pcb-part" data-part="${esc(p.fp.ref)}" data-h="${p.h}" data-nets="${esc(p.nets.join("|"))}">${p.svg}</g>`),
  ];
  return { F, body, parts: parts.map((p) => ({ ref: p.fp.ref, value: p.fp.value, kind: p.fp.kind, nets: p.nets, height: p.h })) };
}

// ---------------------------------------------------------------------------
// styles: "ink" keeps Anatomy's monochrome look; data-palette="board" on the
// figure (or the page) paints soldermask, copper, gold and silkscreen.

export const PCB_CSS = `
.pcb-flat{stroke-width:var(--anatomy-weight);stroke-linejoin:round}
.pcb-flat[data-tone="faint"]{fill:var(--anatomy-paper);stroke:var(--anatomy-faint)}
.pcb-flat[data-tone="mid"]{fill:var(--anatomy-top);stroke:var(--anatomy-mid)}
.pcb-flat[data-tone="lo"]{fill:var(--anatomy-shade-0);stroke:var(--anatomy-lo)}
.pcb-pour{fill:var(--anatomy-shade-3)!important;stroke:var(--anatomy-faint)!important;opacity:.85}
.pcb-hole{fill:var(--anatomy-halo)!important;stroke:var(--anatomy-lo)!important}
.pcb-mouth{fill:var(--anatomy-halo);stroke:var(--anatomy-hi);stroke-width:var(--anatomy-weight)}
.pcb-tongue{fill:var(--anatomy-shade-1);stroke:var(--anatomy-mid);stroke-width:var(--anatomy-weight)}
.pcb-flow{fill:none;stroke:var(--anatomy-lit);stroke-width:.9;stroke-dasharray:1.5 4;stroke-linecap:round;opacity:0;transition:opacity .4s var(--anatomy-ease)}
[data-net][data-on]>.pcb-flat,.pcb-flat[data-net][data-on]{fill:var(--anatomy-lit-top);stroke:var(--anatomy-lit)}
.pcb-flow[data-on]{opacity:.9;animation:pcb-flow 1.1s linear infinite}
@keyframes pcb-flow{to{stroke-dashoffset:-11}}
.pcb-part{transition:opacity .3s var(--anatomy-ease)}
.pcb-part[data-dim] .iso-edge{stroke:var(--anatomy-lo)}
.pcb-part:not([data-dim]) .iso-edge{transition:stroke .3s var(--anatomy-ease)}
.pcb-silk{font-weight:600}
.iso-stage:focus{outline:none}
.iso-stage:focus-visible{outline:1px solid var(--anatomy-mid);outline-offset:6px;border-radius:10px}
.pcb-silk-strip{fill:none!important;stroke:var(--anatomy-mid)!important}
[data-palette="board"] .pcb-silk-strip{stroke:var(--pcb-silk)!important}
[data-palette="board"] .pcb-ruler{stroke:var(--pcb-silk);opacity:.7}
[data-palette="board"] .pcb-ruler-major{opacity:1;stroke-width:.9}
.pcb-ruler-major{stroke-width:.9}
@media (prefers-reduced-motion:reduce){.pcb-flow[data-on]{animation:none;stroke-dasharray:none;opacity:.6}}

[data-palette="board"]{--pcb-mask:#1e5a3a;--pcb-mask-hi:#2a7a50;--pcb-mask-edge:#123a25;--pcb-fr4:#b9a77a;--pcb-copper:#3f8a5c;--pcb-copper-hi:#4aa06b;
  --pcb-gold:#d9b25a;--pcb-gold-shade:#a9842f;--pcb-tin:#c7c9cc;--pcb-tin-shade:#8f9296;--pcb-epoxy:#2a2a2c;--pcb-epoxy-top:#3a3a3d;--pcb-plastic:#2b2b2e;--pcb-plastic-top:#3c3c40;--pcb-silk:#f2f2ea;--pcb-glow:#ffd36a}
[data-palette="board"] .pcb-board>.iso-top{fill:var(--pcb-mask)}
[data-palette="board"] .pcb-board>.iso-fill,[data-palette="board"] .pcb-board>.iso-shade{fill:var(--pcb-fr4)}
[data-palette="board"] .pcb-board>.iso-edge{stroke:var(--pcb-mask-edge)}
[data-palette="board"] .pcb-foil{stroke:var(--pcb-gold)}
[data-palette="board"] .pcb-pour{fill:var(--pcb-mask-hi)!important;stroke:var(--pcb-mask-hi)!important;opacity:.55}
[data-palette="board"] .pcb-track{fill:var(--pcb-copper);stroke:var(--pcb-copper-hi)}
[data-palette="board"] .pcb-pad-face,[data-palette="board"] .pcb-via>.pcb-flat:first-child{fill:var(--pcb-gold);stroke:var(--pcb-gold-shade)}
[data-palette="board"] [data-net][data-on]>.pcb-flat,[data-palette="board"] .pcb-flat[data-net][data-on]{fill:var(--pcb-glow);stroke:#fff3c4}
[data-palette="board"] .pcb-flow{stroke:#fff3c4}
[data-palette="board"] .pcb-metal>.iso-top,[data-palette="board"] .pcb-metal>.iso-fill{fill:var(--pcb-tin)}
[data-palette="board"] .pcb-metal>.iso-shade{fill:var(--pcb-tin-shade)}
[data-palette="board"] .pcb-gold>.iso-top,[data-palette="board"] .pcb-gold>.iso-fill{fill:var(--pcb-gold)}
[data-palette="board"] .pcb-gold>.iso-shade{fill:var(--pcb-gold-shade)}
[data-palette="board"] .pcb-body>.iso-top{fill:var(--pcb-epoxy-top)}
[data-palette="board"] .pcb-body>.iso-fill,[data-palette="board"] .pcb-body>.iso-shade{fill:var(--pcb-epoxy)}
[data-palette="board"] .pcb-plastic>.iso-top,[data-palette="board"] .pcb-cap>.iso-top{fill:var(--pcb-plastic-top)}
[data-palette="board"] .pcb-plastic>.iso-fill,[data-palette="board"] .pcb-plastic>.iso-shade,[data-palette="board"] .pcb-cap>.iso-fill,[data-palette="board"] .pcb-cap>.iso-shade{fill:var(--pcb-plastic)}
[data-palette="board"] .pcb-housing>.iso-top{fill:#efe8d6}
[data-palette="board"] .pcb-housing>.iso-fill,[data-palette="board"] .pcb-housing>.iso-shade{fill:#d6cdb6}
[data-palette="board"] .pcb-axial>.iso-top,[data-palette="board"] .pcb-axial>.iso-fill,[data-palette="board"] .pcb-axial>.iso-shade{fill:#d9c6a0}
[data-palette="board"] .pcb-disc>.iso-top,[data-palette="board"] .pcb-disc>.iso-fill,[data-palette="board"] .pcb-disc>.iso-shade{fill:#d7893a}
[data-palette="board"] .pcb-lens>.iso-top{fill:#7be3a0}
[data-palette="board"] .pcb-lens>.iso-fill,[data-palette="board"] .pcb-lens>.iso-shade{fill:#4caf74}
[data-palette="board"] .pcb-lens[data-lit]>.iso-top{fill:#c9ffd9}
[data-palette="board"] .pcb-silk{fill:var(--pcb-silk)}
[data-palette="board"] .pcb-hole{fill:#1c1c1e!important;stroke:#5a5a5e!important}
[data-palette="board"] .pcb-mouth{fill:#1c1c1e;stroke:#e9eaec}
[data-palette="board"] .pcb-tongue{fill:#2b2b2e;stroke:#55555a}
[data-palette="board"] .iso-solid>.iso-edge{stroke:#0000004d}
`;

// ---------------------------------------------------------------------------
// live behaviour for boardFigure pages: a tour through nets, hover to follow a
// net, Space (or a click) lifts every part off its pads. Inline after kitScript().

export function liveScript({ P, K, tour = [], nets = {} }) {
  return `
const P = ${JSON.stringify(P)}, K = ${K};
const TOUR = ${JSON.stringify(tour)};
const NETS = ${JSON.stringify(nets)};
const stage = document.querySelector(".iso-stage");
const svg = stage.querySelector("svg");
const readout = document.querySelector("[data-readout]");
const still = matchMedia("(prefers-reduced-motion: reduce)");
const parts = [...svg.querySelectorAll(".pcb-part")].map((el) => ({ el, h: +el.dataset.h, nets: el.dataset.nets.split("|"), y: 0, v: 0 }));
const marked = [...svg.querySelectorAll("[data-net]")];
let current = null, touring = !still.matches, clock = 0, index = 0, lift = 0, liftV = 0, liftTarget = 0, frame = 0, last = 0, visible = false, idle = 0;
const say = (text) => { if (readout.textContent !== text) readout.textContent = text; stage.setAttribute("aria-valuetext", text); };
function focus(net) {
  if (net === current) return;
  current = net;
  for (const el of marked) el.toggleAttribute("data-on", !!net && el.dataset.net === net);
  for (const p of parts) p.el.toggleAttribute("data-dim", !!net && !p.nets.includes(net));
  if (!net) { say("hover a trace or a pad"); return; }
  const n = NETS[net] || { pads: 0, length: 0 };
  say(net.replace(/^\\//, "") + " · " + n.pads + " pads · " + n.length.toFixed(1) + " mm of copper");
}
function tick(now) {
  const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
  last = now;
  if (touring && TOUR.length) { clock += dt; const i = Math.floor(clock / 2.6) % TOUR.length; if (i !== index || current === null) { index = i; focus(TOUR[i]); } }
  const calm = still.matches;
  const stiff = 170, drag = calm ? 2 * Math.sqrt(170) : 19;
  parts.forEach((p, i) => {
    const target = liftTarget * (3 + p.h * 0.2) * K;    // must match LIFT in boardFigure, which reserves the headroom
    if (calm) { p.y = target; p.v = 0; }
    else for (let s = 0; s < 4; s++) { p.v += ((target - p.y) * stiff - p.v * drag) * dt / 4; p.y += p.v * dt / 4; }
    p.el.setAttribute("transform", translateAlong(P, [0, 0, p.y]));
  });
  const moving = parts.some((p) => Math.abs(p.v) > 0.01 || Math.abs(p.y - liftTarget * (3 + p.h * 0.2) * K) > 0.01);
  frame = visible && (touring || moving) ? requestAnimationFrame(tick) : 0;
}
const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
const pause = () => { touring = false; clearTimeout(idle); idle = setTimeout(() => { touring = !still.matches; clock = 0; run(); }, 4000); };
new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
stage.addEventListener("pointermove", (event) => {
  const hit = event.target.closest && event.target.closest("[data-net]");
  if (hit && hit.dataset.net) { pause(); focus(hit.dataset.net); }
});
stage.addEventListener("pointerleave", () => pause());
const toggle = () => { liftTarget = liftTarget ? 0 : 1; pause(); run(); };
stage.addEventListener("click", toggle);
stage.addEventListener("keydown", (event) => {
  if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); return; }
  if (!TOUR.length) return;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") index = (index + 1) % TOUR.length;
  else if (event.key === "ArrowLeft" || event.key === "ArrowUp") index = (index + TOUR.length - 1) % TOUR.length;
  else return;
  event.preventDefault(); pause(); focus(TOUR[index]);
});
focus(TOUR[0] || null);
run();
`;
}
