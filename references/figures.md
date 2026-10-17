# Figures of hardware

Illustrate mode makes Anatomy figures (isometric, solid, shaded, framed, live) of circuit boards. Anatomy's own craft guide applies in full and is vendored in `references/anatomy/`: read `craft.md` before designing a figure and `verify.md` before calling one done. This page covers what is specific to boards.

## The one idea, for hardware

Anatomy's rule is that the object is the explanation. A board is already a real object, so the work is choosing **which board, seen how, doing what**:

| Concept | The board that explains it | What moves | Readout |
|---|---|---|---|
| Why decoupling caps sit at the pins | IC, supply and ground rails, a cap that slides along them | the cap; the loop it closes is lit | loop area, nH, Ω at 100 MHz |
| Ground return current | two-layer board, top trace with the bottom pour lifted away beneath | a pulse along the trace; its return path lights in the pour under it, and spreads when a slot cuts the pour | path length, loop area |
| Stackup | a four-layer board exploded layer by layer | layers lift apart on hover | layer name, copper weight, dielectric thickness |
| Through-hole vs via vs blind via | a board cut in section | a cutting plane sweeps across | drill, plating, aspect ratio |
| A switching regulator's hot loop | input cap, switch, diode and inductor around a lit loop | switch on/off; current alternates between the two loops | di/dt, loop nH, ringing estimate |
| I²C pull-ups | a bus with three devices and two resistors | lines pulled low and released; rise time grows with capacitance | rise time from R·C |
| Thermal relief | a pad in a pour with spokes | solder heat flowing out by the spokes vs a solid connection | spoke width, thermal resistance |
| Differential pair | two traces with a length-matching meander | a bit edge racing down each trace | skew in ps |

Keep the same discipline as Anatomy: truth first (the numbers come from a pure `model.mjs`), many small real parts (screws are vias and holes here, rulers are silkscreen), one lit accent, calm motion, words only as silkscreen.

## Path 1: a real board

```bash
python3 scripts/kicad/board_dump.py board.kicad_pcb --zones                  # -> board.board.json
node scripts/figure/board-figure.mjs board.board.json --out fig.html \
     [--light] [--palette ink|board] [--tour VBUS,+3V3,/SDA] [--title "..."] [--fig "Fig 3"] \
     [--azimuth -52] [--width 640 --height 420] [--no-pours] [--no-silk] [--override fix.json]
```

`board_dump.py` records the outline, every footprint (body outline from its fab layer, pads, a package kind and height), tracks, vias, pours and per-net totals, in KiCad's millimetres. Kinds come from footprint names: `chip`, `led`, `gullwing`, `dip`, `nolead`, `header`, `socket`, `connector` (USB-C gets a real shell; USB, D-sub, RJ45 and SD get metal shells; JST, Molex and terminal blocks get nylon housings), `switch`, `axial`, `disc`, `electrolytic` (5 mm LEDs too), `crystal`, `inductor`, `to92`, `to220`, `hole`, `edge` (gold fingers, no body), and `generic` (a box). When a guess is wrong, pass `--override fix.json` with `{"REF": {"kind": "connector", "height": 6.0}}`.

The page: the camera turns the largest edge connector toward the reader (override with `--azimuth`), the tour steps through the nets in `--tour` (default: the named nets with the most pads), each net's copper lights with current flowing along it while parts off the net tone down, the pointer focuses whatever trace or pad it is over, arrow keys step through the tour, and Space or a click lifts every part off its pads on springs. Reduced motion gets a still figure.

Local nets are named as KiCad names them (`/SDA`); `--tour` warns about names it cannot find.

## Path 2: an invented board

Copy `examples/decoupling-loop/` and change it:

- `model.mjs`: the physics, as pure functions, and `describe(state)` for the readout. Real formulas with their sources in comments, real constants.
- `build.mjs`: the board in millimetres with the part builders below, static art painted back to front, the moving parts as groups moved with `translateAlong`, and the live script (it inlines the model's functions with `fn.toString()`, as the example does).

Keep the board small around the idea (the subject should fill most of the card), and frame it with `frameOf(size, { tops })` so tall parts get headroom and nothing else does.

### Part API (`kit/pcb.mjs`)

All positions in millimetres, KiCad's frame. `F` is the frame from `frameOf`.

| Function | Draws |
|---|---|
| `frameOf([w, h], { span, width, height, azimuth, elevation, tops, pad, thickness })` | the frame: `F.at([x, y], z)` → world, `F.P` projection, `F.K` units per mm |
| `boardSlab(F, [ring], thickness, { layers })` | the board, with copper foil lines in its edge |
| `trackSvg(F, { from, to, width, net }, { flow })` | a track (and a flow line for lighting) |
| `padSvg(F, { at, size, angle, shape, drill, net })`, `viaSvg(F, { at, size, drill, net })`, `holeSvg(F, at, drill, plated)` | copper and holes |
| `flatSvg(F, [rings], z, { cls, tone, net })` | any flat region (pours, loops, silkscreen strips) |
| `chip`, `gullwing`, `nolead`, `header`, `tactSwitch`, `usbC`, `housing`, `block`, `axial`, `discCap`, `electrolytic` | packages from pads and a body outline |
| `rectMm`, `circleMm`, `stadiumMm`, `hull` | outlines in mm |
| `readableOnTop(F, at, z)` | a transform for silkscreen text that reads left to right from any camera |
| `boardFigure(dump, options)`, `liveScript(...)`, `facingAzimuth(dump)` | the real-board path |
| `PCB_CSS` | both palettes; append it to the page's styles |

Anatomy's kit (`kit/anatomy/iso-kit.mjs`, `lathe.mjs`, `tube.mjs`) is available for anything else: wires and cables to off-board parts, knobs, enclosures, test equipment (a probe on a test point makes a fine subject).

## Palettes and themes

`--palette ink` (default) keeps Anatomy's monochrome: right for documentation next to other Anatomy figures. `--palette board` paints soldermask, FR4, copper, gold, tin, epoxy and silkscreen: right when the look of the hardware is the point (a product page, a teaching figure about real boards). Both palettes work in both themes; build `--light` too and look at it.

## Verify

As Anatomy does, and read every PNG:

```bash
node scripts/figure/capture.mjs fig.html --out shots/fig.png                      # desktop
node scripts/figure/capture.mjs fig.html --out shots/phone.png --width 390        # phone
node scripts/figure/capture.mjs fig.html --out shots/lift.png --key Space --times 1500
node scripts/figure/capture.mjs fig.html --out shots/zoom.png --zoom 120,150,260,200   # 4x close-up
node scripts/figure/lines.mjs fig.html --out shots/lines.png                      # must print line ends: 0
```

Check at 4×: leads land on their pads, no part floats or sinks, silkscreen reads left to right, nothing is see-through, the lit net is the one the readout names. Compare a real-board figure against `render.py`'s top view: same parts in the same places, not mirrored.
