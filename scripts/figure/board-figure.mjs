#!/usr/bin/env node
// Turn a board dump (scripts/kicad/board_dump.py) into a standalone interactive figure page.
//
//   node scripts/figure/board-figure.mjs board.json --out figure.html [--light] [--palette ink|board]
//        [--title "USB-C ATtiny85 blinker"] [--fig "Fig 1"] [--tour VBUS,+3V3,/MISO] [--width 640 --height 420]
//        [--azimuth 38] [--no-pours] [--no-silk] [--override overrides.json] [--caption "..."]
//
// The page tours through the nets in --tour (default: the busiest named nets), lights each net's copper
// with flowing dashes and dims the parts not on it, follows the pointer to whatever trace or pad it is
// over, and lifts every part off its pads on Space or a click. Reduced motion gets a still page.
// --override takes {"REF": {"height": 2.2, "kind": "block"}} to fix a guessed package.
import { readFileSync, writeFileSync } from "node:fs";
import { basename } from "node:path";
import * as k from "../../kit/anatomy/iso-kit.mjs";
import { boardFigure, PCB_CSS, liveScript } from "../../kit/pcb.mjs";
import { kitScript } from "./inline-kit.mjs";

const args = process.argv.slice(2);
const flag = (name, fallback) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : fallback; };
const has = (name) => args.includes(`--${name}`);
const input = args.find((a) => a.endsWith(".json") && !a.startsWith("--") && args[args.indexOf(a) - 1] !== "--override");
if (!input) { console.error("usage: board-figure.mjs board.json --out figure.html [options]"); process.exit(2); }

const dump = JSON.parse(readFileSync(input, "utf8"));
const theme = has("light") ? "light" : "dark";
const palette = flag("palette", "ink");
const width = +flag("width", 640), height = +flag("height", 420);
const override = flag("override") ? JSON.parse(readFileSync(flag("override"), "utf8")) : {};
const named = Object.entries(dump.nets).filter(([n]) => n && !/GND|unconnected/i.test(n));
const tour = flag("tour") ? flag("tour").split(",") : named.sort((a, b) => b[1].pads - a[1].pads || b[1].length - a[1].length).slice(0, 6).map(([n]) => n);
const missing = tour.filter((n) => !dump.nets[n]);
if (missing.length) console.warn(`warning: not on this board: ${missing.join(", ")} (names are as KiCad gives them, local nets start with /)`);

const fig = boardFigure(dump, { width, height, azimuth: flag("azimuth") === undefined ? null : +flag("azimuth"), elevation: +flag("elevation", 32), pours: !has("no-pours"), silk: !has("no-silk"), override });
const title = flag("title", dump.source.replace(/\.kicad_pcb$/, ""));
const label = `An isometric drawing of the ${title} circuit board, ${dump.size[0].toFixed(0)} by ${dump.size[1].toFixed(0)} mm, ${dump.layers} copper layers, with ${fig.parts.length} parts: ` +
  fig.parts.map((p) => `${p.ref} ${p.value}`).join(", ") + ". Each net's copper lights up in turn; the parts can be lifted off their pads.";
const svg = k.figureSvg({ width, height, label, body: fig.body });
const body = k.plateHtml({
  fig: flag("fig", "Fig 1"), title, hint: "Hover a trace · Space lifts the parts", readout: "",
  keys: [
    { mark: "lit", label: "The net in focus: its copper, pads and vias" },
    { mark: "raised", label: "Parts, at their package heights" },
    { mark: "flat", label: `Copper on the top layer${dump.zones?.length ? ", over the ground pour" : ""}` },
  ],
  caption: flag("caption", `${title}: ${dump.size[0].toFixed(1)} × ${dump.size[1].toFixed(1)} mm, ${dump.thickness} mm thick, ${dump.layers} layers, ${dump.tracks.length} track segments and ${dump.vias.length} vias, drawn from ${dump.source}.`),
  body: `<div class="iso-stage" tabindex="0" role="application" aria-label="${title}: arrow keys step through nets, Space lifts the parts" data-palette="${palette}">${svg}</div>`,
});
const page = k.pageHtml({ title, theme, body, script: kitScript() + liveScript({ P: fig.F.P, K: fig.F.K, tour, nets: dump.nets }), width: width + 10 })
  .replace("</style>", `${PCB_CSS}</style>`);
const out = flag("out", input.replace(/\.json$/, theme === "light" ? "-light.html" : ".html"));
writeFileSync(out, page);
console.log(`wrote ${out} (${(page.length / 1024).toFixed(0)} KB, tour: ${tour.join(", ")})`);
