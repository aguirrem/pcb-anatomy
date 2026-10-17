// An idea-based figure: why the decoupling capacitor sits next to the IC.
// The supply and ground rails run from an SOIC to a bulk capacitor at the power input.
// C1 taps the rails wherever the reader slides it; the current loop it closes with the
// IC's supply pins is lit, and the readout gives that loop's inductance and impedance
// from model.mjs. Build: node examples/decoupling-loop/build.mjs [--light] [--palette board]
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as k from "../../kit/anatomy/iso-kit.mjs";
import * as pcb from "../../kit/pcb.mjs";
import { kitScript } from "../../scripts/figure/inline-kit.mjs";
import { TRACE, loopOf, describe } from "./model.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const theme = process.argv.includes("--light") ? "light" : "dark";
const palette = process.argv.includes("--palette") ? process.argv[process.argv.indexOf("--palette") + 1] : "ink";
const WIDTH = 640, HEIGHT = 400;

// -- the board, in millimetres, KiCad's frame (x right, y down) --------------------------------
const BOARD = { w: 34, h: 19, r: 2, t: 1.6 };
const IC = { c: [8.2, 9.5], pitch: 1.27, padX: 2.475, pad: [1.95, 0.6], body: [3.9, 4.9] };
const pinY = (i) => IC.c[1] + (i - 1.5) * IC.pitch;              // rows 0..3 top to bottom
const PIN8 = [IC.c[0] + IC.padX, pinY(0)], PIN5 = [IC.c[0] + IC.padX, pinY(3)];
const RAIL = { vdd: PIN8[1], gnd: PIN5[1], from: PIN8[0], to: 26.6, w: TRACE.w };
const START = PIN8[0] + IC.pad[0] / 2;                            // where the loop leaves the pins
const REACH = [START + 1.0, START + 9.5];                        // C1 centre travel, mm: stays in clear view of the bulk cap
const CAP_PADS = (x) => [[x, IC.c[1] - 0.775], [x, IC.c[1] + 0.775]];
const J1 = [31.3, IC.c[1]], BULK = [26.6, IC.c[1]];

const tops = [[IC.c[0], IC.c[1], 1.4], [J1[0], J1[1] - 1.3, 8.5], [J1[0], J1[1] + 1.3, 8.5], [BULK[0], BULK[1], 7.7]];
const F = pcb.frameOf([BOARD.w, BOARD.h], { span: 300, width: WIDTH, height: HEIGHT, azimuth: -130, elevation: 34, tops, pad: 22 });
const outline = [k.roundedPlan({ x: 0, y: 0, w: BOARD.w, d: BOARD.h, r: BOARD.r }, 8)];

// pads of the SOIC: 1-4 down the left, 5-8 up the right
const icPads = [0, 1, 2, 3].map((i) => ({ number: String(i + 1), at: [IC.c[0] - IC.padX, pinY(i)], size: IC.pad, angle: 0, shape: "roundrect", net: i === 3 ? "" : `SIG${i}` }))
  .concat([3, 2, 1, 0].map((i, j) => ({ number: String(j + 5), at: [IC.c[0] + IC.padX, pinY(i)], size: IC.pad, angle: 0, shape: "roundrect", net: j === 0 ? "GND" : j === 3 ? "VDD" : `IO${j}` })));
icPads[3].net = "GND";
const icBody = pcb.rectMm(IC.c, [IC.body[0], IC.body[1]]);
const capPads = (x) => CAP_PADS(x).map((at, i) => ({ number: String(i + 1), at, size: [0.95, 0.9], angle: 0, shape: "roundrect", net: i ? "GND" : "VDD" }));
const jPads = [-1.27, 1.27].map((dy, i) => ({ number: String(i + 1), at: [J1[0], J1[1] + dy], size: [1.7, 1.7], angle: 0, shape: i ? "circle" : "rect", drill: 1.0, net: i ? "GND" : "VDD" }));
const bulkPads = [-1.75, 1.75].map((dy, i) => ({ number: String(i + 1), at: [BULK[0], BULK[1] + dy], size: [1.6, 1.6], angle: 0, shape: i ? "circle" : "rect", drill: 0.8, net: i ? "GND" : "VDD" }));

// copper: the two rails, stubs into J1, signal traces leaving the IC to the left, stitching vias
const tracks = [
  { from: [RAIL.from, RAIL.vdd], to: [RAIL.to, RAIL.vdd], width: RAIL.w, net: "VDD" },
  { from: [RAIL.from, RAIL.gnd], to: [RAIL.to, RAIL.gnd], width: RAIL.w, net: "GND" },
  { from: [RAIL.to, RAIL.vdd], to: bulkPads[0].at, width: RAIL.w, net: "VDD" },
  { from: [RAIL.to, RAIL.gnd], to: bulkPads[1].at, width: RAIL.w, net: "GND" },
  { from: bulkPads[0].at, to: [J1[0] - 2.2, bulkPads[0].at[1]], width: 0.5, net: "VDD" },
  { from: [J1[0] - 2.2, bulkPads[0].at[1]], to: jPads[0].at, width: 0.5, net: "VDD" },
  { from: bulkPads[1].at, to: [J1[0] - 2.2, bulkPads[1].at[1]], width: 0.5, net: "GND" },
  { from: [J1[0] - 2.2, bulkPads[1].at[1]], to: jPads[1].at, width: 0.5, net: "GND" },
  ...[0, 1, 2].map((i) => ({ from: [IC.c[0] - IC.padX, pinY(i)], to: [3.0, pinY(i) + (i - 1) * 1.8], width: 0.2, net: `SIG${i}` })),
  ...[1, 2].map((j) => ({ from: [IC.c[0] + IC.padX, pinY(3 - j)], to: [IC.c[0] + IC.padX + 1.4, pinY(3 - j)], width: 0.2, net: `IO${j}` })),
];
const vias = [[3.0, pinY(0) - 1.8], [3.0, pinY(1)], [3.0, pinY(2) + 1.8], [IC.c[0] + IC.padX + 1.4, pinY(2)], [IC.c[0] + IC.padX + 1.4, pinY(1)]]
  .map((at, i) => ({ at, size: 0.6, drill: 0.3, net: i < 3 ? `SIG${i}` : `IO${i - 2}` }));
vias.push({ at: [IC.c[0] - IC.padX - 1.6, pinY(3)], size: 0.6, drill: 0.3, net: "GND" });
tracks.push({ from: [IC.c[0] - IC.padX, pinY(3)], to: [IC.c[0] - IC.padX - 1.6, pinY(3)], width: 0.3, net: "GND" });

// -- static art ------------------------------------------------------------------------------
const flat = [];
for (const t of tracks) flat.push(pcb.trackSvg(F, t));
for (const p of [...icPads, ...jPads, ...bulkPads]) flat.push(pcb.padSvg(F, p));
for (const v of vias) flat.push(pcb.viaSvg(F, v));
for (const [x, y] of [[2.6, 2.6], [BOARD.w - 2.6, 2.6], [BOARD.w - 2.6, BOARD.h - 2.6], [2.6, BOARD.h - 2.6]]) flat.push(pcb.holeSvg(F, [x, y], 2.2, true));

// silkscreen ruler under the GND rail: 1 mm ticks from the pins, a major every 5
const rulerY = RAIL.gnd + 1.5, rulerTo = REACH[1] + 0.5;
const strip = [[START - 0.4, rulerY], [rulerTo + 0.4, rulerY], [rulerTo + 0.4, rulerY + 0.7], [START - 0.4, rulerY + 0.7]];
flat.push(pcb.flatSvg(F, [strip], 0.04, { cls: "pcb-silk-strip", tone: "faint" }));
const ticks = { minor: [], major: [] };
for (let mm = 0; START + mm <= rulerTo; mm += 1) {
  const x = START + mm;   // every tick spans the printed strip edge to edge; majors are heavier, not longer
  ticks[mm % 5 === 0 ? "major" : "minor"].push(k.lineOnTop(F.at([x, rulerY]).slice(0, 2), F.at([x, rulerY + 0.7]).slice(0, 2), 0.05 * F.K, F.P));
}
flat.push(k.lineSvg(ticks.minor.join(""), { tone: "lo", className: "pcb-ruler" }), k.lineSvg(ticks.major.join(""), { tone: "hi", className: "pcb-ruler pcb-ruler-major" }));
for (const mm of [0, 5, 10]) {
  if (START + mm > rulerTo) continue;
  flat.push(k.faceTextSvg(pcb.readableOnTop(F, [START + mm, rulerY + 2.2]), String(mm), { size: 0.85 * F.K, tone: "mid", anchor: "middle" }).replace('class="iso-face-text"', 'class="iso-face-text pcb-silk"'));
}
const label = (text, at, size = 0.95) => k.faceTextSvg(pcb.readableOnTop(F, at), text, { size: size * F.K, tone: "mid", anchor: "middle" }).replace('class="iso-face-text"', 'class="iso-face-text pcb-silk"');
flat.push(label("VDD", [RAIL.to - 4.2, RAIL.vdd - 1.0]), label("GND", [RAIL.to - 4.2, RAIL.gnd + 1.0]), label("U1", [IC.c[0], IC.c[1] - 3.6]),
  label("J1", [J1[0], J1[1] + 3.6]), label("C2", [BULK[0] - 3.4, BULK[1] + 4.6]), label("mm from the pins", [START + 5, rulerY + 3.3], 0.7));

// -- parts, painted back to front -----------------------------------------------------------
const parts = [
  { at: IC.c, svg: pcb.gullwing(F, { pads: icPads, body: icBody, height: 1.45 }) },
  { at: BULK, svg: pcb.electrolytic(F, { body: pcb.circleMm(BULK, 2.5), height: 7.7, dims: { diameter: 5 } }) },
  { at: J1, svg: pcb.header(F, { pads: jPads, body: pcb.rectMm(J1, [2.54, 5.08]), height: 8.5 }) },
].sort((a, b) => k.depthOf(F.at(a.at), F.P) - k.depthOf(F.at(b.at), F.P));

// C1 and its two stubs are drawn once at x = 0 offset (REACH[0]) and moved as one rigid group
const x0 = REACH[0];
const cap = [
  pcb.trackSvg(F, { from: [x0, RAIL.vdd], to: CAP_PADS(x0)[0], width: RAIL.w, net: "VDD" }),
  pcb.trackSvg(F, { from: [x0, RAIL.gnd], to: CAP_PADS(x0)[1], width: RAIL.w, net: "GND" }),
  ...capPads(x0).map((p) => pcb.padSvg(F, p)),
  pcb.chip(F, { pads: capPads(x0), body: pcb.rectMm(IC.c.map((v, i) => (i ? v : x0)), [0.85, 1.65]), height: 0.85, lit: true }),
  label("C1", [x0 + 1.6, IC.c[1]]),
].join("");

const loopPath = (x) => {
  const pts = [[START, RAIL.vdd], [x, RAIL.vdd], [x, RAIL.gnd], [START, RAIL.gnd]];
  return "M" + pts.map((p) => k.iso(F.at(p, 0.03), F.P).map((v) => Math.round(v * 10) / 10).join(" ")).join("L") + "Z";
};
const flowPath = (x) => {
  const pts = [CAP_PADS(x)[0], [x, RAIL.vdd], [START, RAIL.vdd], [START, RAIL.gnd], [x, RAIL.gnd], CAP_PADS(x)[1]];
  return "M" + pts.map((p) => k.iso(F.at(p, 0.06), F.P).map((v) => Math.round(v * 10) / 10).join(" ")).join("L");
};

const startState = loopOf(REACH[0] - START + 2);
const svg = k.figureSvg({
  width: WIDTH, height: HEIGHT,
  label: "A small two-layer board. An SOIC-8 on the left takes its supply from two parallel rails, VDD and GND, that run right to a bulk electrolytic capacitor and a two-pin power header. A small ceramic capacitor, C1, bridges the rails and slides along them. The lit rectangle is the current loop C1 closes with the chip's supply pins: the farther C1 sits, the larger the loop and its inductance.",
  body: [
    pcb.boardSlab(F, outline, BOARD.t, { layers: 2 }),
    `<g class="pcb-surface"><path class="pcb-loop" data-part="loop" d="${loopPath(x0)}"/>${flat.join("")}</g>`,
    `<path class="pcb-loop-flow" data-part="flow" data-free="" d="${flowPath(x0)}"/>`,
    ...parts.filter((p) => k.depthOf(F.at(p.at), F.P) < k.depthOf(F.at([x0, IC.c[1]]), F.P)).map((p) => p.svg),
    `<g data-part="cap">${cap}</g>`,
    ...parts.filter((p) => k.depthOf(F.at(p.at), F.P) >= k.depthOf(F.at([x0, IC.c[1]]), F.P)).map((p) => p.svg),
  ],
});

const live = `
const P = ${JSON.stringify(F.P)}, K = ${F.K};
const TRACE = ${JSON.stringify(TRACE)}, ESL = 0.5, F_TEST = 100e6;
const START = ${START}, REACH = ${JSON.stringify(REACH)}, X0 = ${x0};
const RAIL = ${JSON.stringify(RAIL)}, CY = ${IC.c[1]};
const loopOf = ${loopOf.toString()};
const describe = ${describe.toString()};
const at = ([x, y], z = 0) => [x * K, y * K, z * K];
const path = (pts, z, close) => "M" + pts.map((p) => iso(at(p, z), P).map((v) => Math.round(v * 10) / 10).join(" ")).join("L") + (close ? "Z" : "");
const stage = document.querySelector(".iso-stage");
const svg = stage.querySelector("svg");
const capEl = svg.querySelector('[data-part="cap"]'), loopEl = svg.querySelector('[data-part="loop"]'), flowEl = svg.querySelector('[data-part="flow"]');
const readout = document.querySelector("[data-readout]");
const still = matchMedia("(prefers-reduced-motion: reduce)");
const STOPS = [REACH[0], START + 4, START + 7, REACH[1]];
let x = REACH[0], target = x, touring = !still.matches, clock = 0, frame = 0, last = 0, visible = false, idle = 0;
function draw() {
  capEl.setAttribute("transform", translateAlong(P, [(x - X0) * K, 0, 0]));
  loopEl.setAttribute("d", path([[START, RAIL.vdd], [x, RAIL.vdd], [x, RAIL.gnd], [START, RAIL.gnd]], 0.03, true));
  flowEl.setAttribute("d", path([[x, CY - 0.775], [x, RAIL.vdd], [START, RAIL.vdd], [START, RAIL.gnd], [x, RAIL.gnd], [x, CY + 0.775]], 0.06, false));
  const s = loopOf(x - START);
  const text = describe(s);
  if (readout.textContent !== text) readout.textContent = text;
  stage.setAttribute("aria-valuenow", s.l.toFixed(1));
  stage.setAttribute("aria-valuetext", text);
}
function tick(now) {
  const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60;
  last = now;
  if (touring) { clock += dt; target = STOPS[Math.floor(clock / 2.4) % STOPS.length]; }
  x = still.matches ? target : x + (target - x) * (1 - Math.exp(-dt / 0.32));
  if (Math.abs(target - x) < 0.005) x = target;
  draw();
  frame = visible && (touring || x !== target) ? requestAnimationFrame(tick) : 0;
}
const run = () => { if (!frame && visible) { last = 0; frame = requestAnimationFrame(tick); } };
const steer = (next) => { touring = false; target = Math.min(REACH[1], Math.max(REACH[0], next)); clearTimeout(idle); idle = setTimeout(() => { touring = !still.matches; clock = 0; run(); }, 4000); run(); };
new IntersectionObserver((e) => { visible = e[e.length - 1].isIntersecting; if (visible) run(); }, { rootMargin: "120px 0px" }).observe(stage);
stage.addEventListener("pointermove", (event) => {
  const box = svg.getBoundingClientRect();
  const share = Math.min(1, Math.max(0, ((event.clientX - box.left) / box.width - 0.3) / 0.45));
  steer(REACH[0] + share * (REACH[1] - REACH[0]));
});
stage.addEventListener("keydown", (event) => {
  const step = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[event.key];
  if (event.key === "Home") steer(REACH[0]); else if (event.key === "End") steer(REACH[1]); else if (step) steer(target + step); else return;
  event.preventDefault();
});
if (still.matches) touring = false;
draw();
`;

const extraCss = `
.pcb-loop{fill:var(--anatomy-lit-top);stroke:var(--anatomy-lit);stroke-width:.5;stroke-dasharray:2 2.5;opacity:.9}
.pcb-loop-flow{fill:none;stroke:var(--anatomy-lit);stroke-width:1.1;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1.6 3.6;animation:pcb-flow 0.9s linear infinite}
[data-palette="board"] .pcb-loop{fill:#ffd36a55;stroke:#ffcf5a}
[data-palette="board"] .pcb-loop-flow{stroke:#fff1c2}
@media (prefers-reduced-motion:reduce){.pcb-loop-flow{animation:none}}
`;
const body = k.plateHtml({
  fig: "Fig 2", title: "Decoupling loop", hint: "Hover to slide C1", readout: describe(startState),
  keys: [
    { mark: "lit", label: "C1 and the loop it closes with the chip's VDD and GND pins" },
    { mark: "raised", label: "SOIC-8, bulk capacitor C2 and the power header J1" },
    { mark: "flat", label: "0.3 mm rails, 3.81 mm apart, on the top layer" },
  ],
  caption: "Two parallel rails have about 0.4 · ln(πd / (w + t)) nH per millimetre, here 1.4 nH/mm, plus about 0.5 nH for the capacitor itself. Above its self-resonance a capacitor is only as good as that loop: every millimetre away from the pins adds about 0.9 Ω at 100 MHz, so at 9 mm C1 already looks like 8 Ω to the chip.",
  body: `<div class="iso-stage" tabindex="0" role="slider" aria-label="Decoupling capacitor position: arrow keys move C1 along the rails" aria-valuemin="${(REACH[0] - START).toFixed(1)}" aria-valuemax="${(REACH[1] - START).toFixed(1)}" data-palette="${palette}">${svg}</div>`,
});
const file = `decoupling-loop${theme === "light" ? "-light" : ""}${palette === "board" ? "-board" : ""}.html`;
writeFileSync(join(HERE, file), k.pageHtml({ title: "Decoupling loop", theme, body, script: kitScript() + live, width: WIDTH + 10 }).replace("</style>", `${pcb.PCB_CSS}${extraCss}</style>`));
console.log("wrote", file);
