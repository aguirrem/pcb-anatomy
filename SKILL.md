---
name: pcb-anatomy
description: Hardware and PCB work in KiCad, in three modes. DESIGN turns requirements into a working KiCad 9/10 project (schematic with ERC, custom symbols and footprints, placement, autorouting, DRC with schematic parity, Gerbers, BOM and pick-and-place). REVIEW checks any existing KiCad project (ERC, DRC, decoupling, floating inputs, current capacity, diff pairs, fab limits, BOM) and returns a ranked report with findings ringed on the board. ILLUSTRATE draws crafted, interactive isometric figures of circuit boards, either a real .kicad_pcb or an invented board that explains a hardware idea (decoupling, current loops, ground return, stackups). Use it for any request to design, lay out, route, review, audit, check or draw a PCB, schematic, footprint or symbol, for KiCad files, Gerbers or fab outputs, or for an explanatory figure of electronics hardware, even if the user never says "KiCad". Also known as hardware-anatomy or kicad-anatomy.
license: MIT
compatibility: Claude Code and the Claude app. Design and review need KiCad 9 or 10 (kicad-cli and its Python module); routing needs Java 25 and Freerouting; figures need Node.js 20.10+ and headless Chrome for the checks. scripts/kicad/setup.sh installs or checks all of it.
---

# PCB Anatomy

One skill, three modes, one toolchain. Every mode ends the same way: **you look at what you made** (renders, reports, close-ups) before you call it done, because KiCad and the browser will happily produce something that is wrong.

| The user wants… | Mode | Start at |
|---|---|---|
| a board, schematic, footprint or symbol made or changed; "design a PCB for…"; fab files | **Design** | "Design", below, and `references/design.md` |
| an existing design checked, reviewed, audited, "is this ready to order?" | **Review** | "Review", below, and `references/review.md` |
| a picture, figure, diagram or explainer of a board or of a hardware idea | **Illustrate** | "Illustrate", below, and `references/figures.md` |

Requests often span modes ("design it, then draw it for the README"): do them in order, design → review → illustrate. When the mode is unclear, ask which one the user wants rather than guessing.

## Setup (every mode, first)

```bash
bash scripts/kicad/setup.sh --check     # what is installed; changes nothing
bash scripts/kicad/setup.sh             # install what is missing (KiCad 10, libraries, Java 25, Freerouting, headless Chrome)
```

It writes `~/.pcb-anatomy/env.json`, which every script reads. Run scripts from the skill folder with `python3 scripts/kicad/<name>.py` and `node scripts/figure/<name>.mjs`. Install problems (proxies blocking the KiCad PPA key, Maven rate limits, macOS/Windows Python) are covered in `references/setup.md`. Illustrate mode from an invented board needs only Node and Chrome; KiCad is optional there.

## Design

The **circuit spec** (`spec.yaml`) is the single source of truth. The schematic and the board are both generated from it, and a parity check proves KiCad agrees with it at every step. You never hand-edit S-expressions.

1. **Requirements first.** Pin down supply voltages and currents, interfaces, board size and mounting, layer count, fab and assembly house, and what is already decided. Ask when it matters; a wrong rail voltage costs a respin.
2. **Choose real parts.** For each block pick a part that exists in KiCad's libraries: `python3 scripts/kicad/find_part.py sym "LDO 3.3"`, then `find_part.py pins Lib:Name` to read its pins and `find_part.py fp <regex>` for the footprint. Read the datasheet's reference circuit (capacitor values, pull-ups, enable pins). A part not in the libraries gets a generated symbol and footprint: `scripts/kicad/partgen.py` from the datasheet's numbers (`references/parts.md`); compare its preview against the datasheet's recommended land pattern before using it.
3. **Write the spec** (`references/spec.md`): parts with symbol, footprint, value and MPN/LCSC fields; nets as `REF.PIN` lists; deliberate no-connects under `nc`; board size, holes, rules, pours; and `placement` for the anchor parts. Unconnected pins are an error until you list them, which is the point.
4. **Schematic:** `python3 scripts/kicad/sch_build.py spec.yaml`. It must end `netlist parity: ok` and `ERC: 0 errors`. It adds PWR_FLAGs where only power inputs sit on a net and says which: confirm each really is fed from off the board. Render with `scripts/kicad/render.py PROJECT --only sch` and look at it.
5. **Place:** `python3 scripts/kicad/pcb_build.py spec.yaml`. Place **anchors by intent** in `placement` (connectors on edges, ICs by signal flow, regulators by their input, anything thermal or noisy); auto-placement only fills in passives, and puts decoupling caps at IC supply pins (`near: U1.VOUT` in a part's spec entry pins a part to a specific pin). Render `--only top`, look, move parts, rebuild. Iterate until the placement is something you would defend.
6. **Route:** `python3 scripts/kicad/route.py PROJECT/name.kicad_pcb`. Freerouting routes everything, then pours refill and ground gets stitching vias. Route anything critical yourself first in KiCad (USB/Ethernet pairs, crystals, switching-regulator hot loops, RF) and pass `--keep-tracks`. It must end with 0 unrouted and 0 layout errors; findings "inside" one footprint come from that footprint and need a fab check, not a reroute.
7. **Review your own board** (the Review mode below), fix what it finds, then **outputs:** `python3 scripts/kicad/outputs.py PROJECT --fab jlcpcb`. It refuses a board with DRC errors.
8. **Show it:** render top, bottom and 3D and look at every image; report what was built, what you decided and what the user still has to decide or verify (part choices, anything the review could not check).

The honest limits: autorouting is fine for simple and medium boards and not a substitute for a layout engineer on dense, high-speed, RF or power designs. Say so when a design falls in that class, and route the critical parts by hand or hand them to the user.

## Review

`python3 scripts/kicad/review.py PROJECT [--fab jlcpcb] [--currents VBUS=1.5,+3V3=0.4]` on any KiCad 8–10 project. It writes `review/review.md`, `review.json` and `review-top.png` (findings ringed and numbered on the board). Then do the part only you can do:

- **Open review-top.png and the renders** and confirm each located finding is real.
- **Read the datasheets** for the main ICs and check what no script can: reference-circuit values, required pull-ups, enable and reset sequencing, layout notes (thermal pads, keep-outs, sense lines).
- **Report by severity** (blocker, major, minor, info), each with where it is and the concrete fix, then list what was not checked. Never call a board ready to order with a blocker or major open.

`references/review.md` has the full checklist and the severity rules.

## Illustrate

Figures follow Anatomy's craft (this skill vendors its kit, MIT, in `kit/anatomy/`): solid isometric parts with four-tone shading, painted back to front, a framed card with a title, an interaction hint and a live readout, calm motion, and every line ending on something. Two paths:

- **A real board:** `python3 scripts/kicad/board_dump.py board.kicad_pcb --zones` → `node scripts/figure/board-figure.mjs board.board.json --out fig.html [--light] [--palette board] [--tour VBUS,+3V3]`. Packages are recognised from footprint names (chips, gull-wing and leadless ICs, headers, USB-C and other connectors, switches, axial and disc parts, electrolytics, LEDs) with their real heights; fix a wrong guess with `--override`. The page tours the named nets, lights each net's copper with flowing current, follows the pointer to any trace or pad, and lifts every part off its pads on Space. The camera turns the main edge connector toward the reader by default.
- **An idea:** invent a small board whose working *is* the concept, write its physics as a pure `model.mjs`, and build it from `kit/pcb.mjs` part builders in millimetres, like `examples/decoupling-loop/`. Every number in the readout comes from the model. `references/figures.md` has the concept-to-board table and the part API.

Palettes: `ink` (default) is Anatomy's monochrome, for docs that want restraint; `board` paints soldermask, copper, gold and silkscreen, for when the look of the hardware is the point. Verify every figure as Anatomy does: `node scripts/figure/capture.mjs page.html --out shot.png` (and `--width 390`, `--light`, `--key Space --times 1500`, `--zoom x,y,w,h` close-ups), and `node scripts/figure/lines.mjs page.html` must print `line ends: 0`. Read every PNG you make.

## Rules that hold in every mode

- **Look before you report.** Every schematic, board and figure gets rendered and viewed. A clean ERC/DRC on a board you have not looked at is not done.
- **Truth over polish.** Values, currents, part numbers and figure readouts come from datasheets, the spec or a model, never invented. Where you assumed something, say so.
- **The spec is the source.** Change the spec and rebuild; edits made in KiCad by hand are kept only through `--keep-placement` and `--keep-tracks`, so tell the user when a rebuild would discard theirs.
- **Stop at the limits.** Mains voltage, high current, RF, medical or safety-critical hardware need a qualified engineer's review; say so plainly and keep helping with what can be checked.

## Files

| Path | What it is |
|---|---|
| `scripts/kicad/setup.sh` | install or check the toolchain; writes `~/.pcb-anatomy/env.json` |
| `scripts/kicad/find_part.py` | search symbols and footprints; list a symbol's pins or a footprint's pads |
| `scripts/kicad/sch_build.py` | spec → schematic, with netlist parity and ERC |
| `scripts/kicad/partgen.py` | datasheet numbers → project symbol and IPC-7351 footprint, with previews |
| `scripts/kicad/pcb_build.py` | spec + schematic → placed board with rules, net classes, holes and pours, and DRC |
| `scripts/kicad/route.py`, `stitch.py` | Freerouting autorouting, pour refill, ground stitching, DRC |
| `scripts/kicad/review.py` | the review report and ringed board image |
| `scripts/kicad/outputs.py` | Gerbers, drill, BOM, positions, IPC-2581, JLCPCB formats, STEP |
| `scripts/kicad/render.py` | schematic, top, bottom, per-layer and 3D PNGs |
| `scripts/kicad/board_dump.py` | a board as JSON for figures |
| `kit/pcb.mjs` | PCB part builders, `boardFigure`, palettes and the live script |
| `kit/anatomy/` | Anatomy's isometric kit (MIT), unchanged |
| `scripts/figure/` | `board-figure.mjs`, plus Anatomy's `capture.mjs`, `drive.mjs` and `lines.mjs` checks |
| `examples/usb-blinky/` | a complete design: spec, schematic, board, renders, review, fab outputs, figure |
| `examples/decoupling-loop/` | an idea figure with its model |

## References

Read as needed: `references/setup.md` (installing), `references/spec.md` (the spec format), `references/design.md` (the design workflow in depth: parts, schematic, placement, routing, outputs), `references/parts.md` (custom symbols and footprints), `references/review.md` (review checklist and severity), `references/figures.md` (illustration: both paths, the part API, concept-to-board table, verification). Anatomy's own guides for figure craft are in `references/anatomy/` (`craft.md`, `kit.md`, `motion.md`, `verify.md`).
